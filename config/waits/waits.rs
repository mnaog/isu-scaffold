//! APIごとの小さな待ち（wait）の表。mainが空き時間に値を変えて、得点や負荷が良くなる箇所を探す。
//!
//! 応答の中身は変えず、時機だけを遅らせる。ベンチの利用者が応答を待ってから次の要求を出すなら、
//! 点にならない要求（空振りのpolling、繰り返しの一覧取得など）の回数が減り、ベンチとサーバーの手が
//! 点になる処理へ回ることがある。効くかは構成ごとに違うので、値はbuildし直さずに変えられるようにする。
//!
//! - 表は環境変数`ISUCON_WAITS`（アプリのunitが読むEnvironmentFileに置く。repoでは
//!   `scripts/waits.py set`が書き、`make deploy`で配る）。プロセスの起動時に1度だけ読むので、
//!   再起動・OSの再起動・主催者の追試でも同じ表で動く。空なら何もしない。
//! - 形: `;`で区切った規則。1つの規則は`METHOD PATH before|after MS [max_body=N] [status=CODE]`。
//!   `PATH`の`:name`・`{name}`・`*`の区切りは、任意の1区切りに合う（isuscopeのroute名をそのまま書ける）。
//!   - `before`: handlerの前に待つ（要求を受けてから処理を始めるまで）。
//!   - `after`: handlerの後、応答を返す前に待つ（処理は済み、相手が受け取るのが遅れる）。
//!   - `max_body=N`: 長さが分かる本文がNバイト以下のときだけ待つ（空の一覧など）。`after`だけ。
//!   - `status=CODE`: そのstatusのときだけ待つ。`after`だけ。
//!   - 1つの規則は`MAX_MS`までに切り詰める。読めない規則は標準エラーへ書いて飛ばす（アプリは止めない）。
//! - 追加のcrateは要らない。このfileをcrateへそのまま置き、`mod waits;`で使う。中身は変えない。
//!   待つ処理（`tokio::time::sleep`など）は、アプリのframeworkに合わせてmiddlewareに書く。
//!
//! ```ignore
//! // axum 0.7の例。routerの最後に`.layer(axum::middleware::from_fn(wait_layer))`を足す。
//! async fn wait_layer(request: axum::extract::Request, next: axum::middleware::Next) -> axum::response::Response {
//!     let waits = waits::global();
//!     if waits.is_empty() {
//!         return next.run(request).await;
//!     }
//!     let method = request.method().as_str().to_owned();
//!     let path = request.uri().path().to_owned();
//!     if let Some(wait) = waits.before(&method, &path) {
//!         tokio::time::sleep(wait).await;
//!     }
//!     let response = next.run(request).await;
//!     let body = http_body::Body::size_hint(response.body()).exact();
//!     if let Some(wait) = waits.after(&method, &path, response.status().as_u16(), body) {
//!         tokio::time::sleep(wait).await;
//!     }
//!     response
//! }
//! ```
#![allow(dead_code)]

use std::sync::OnceLock;
use std::time::Duration;

/// 表を読む環境変数。
pub const VARIABLE: &str = "ISUCON_WAITS";
/// 1つの規則で待つ上限（ms）。書き間違いでベンチの時間切れを起こさないため。
pub const MAX_MS: u64 = 2000;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Phase {
    Before,
    After,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Rule {
    pub method: String,
    pub path: Vec<String>,
    pub phase: Phase,
    pub ms: u64,
    pub max_body: Option<u64>,
    pub status: Option<u16>,
}

impl Rule {
    fn matches(&self, method: &str, path: &str) -> bool {
        if !self.method.eq_ignore_ascii_case(method) {
            return false;
        }
        let mut segments = path.split('/');
        for pattern in &self.path {
            match segments.next() {
                Some(segment) if is_placeholder(pattern) || pattern == segment => {}
                _ => return false,
            }
        }
        segments.next().is_none()
    }
}

fn is_placeholder(segment: &str) -> bool {
    segment == "*" || segment.starts_with(':') || (segment.starts_with('{') && segment.ends_with('}'))
}

#[derive(Debug, Default)]
pub struct Waits {
    rules: Vec<Rule>,
}

impl Waits {
    /// 表を読む。読めなかった規則は理由と一緒に返し、読めた規則だけで表を作る。
    pub fn parse(spec: &str) -> (Waits, Vec<String>) {
        let mut rules = Vec::new();
        let mut errors = Vec::new();
        for text in spec.split(';').map(str::trim).filter(|text| !text.is_empty()) {
            match parse_rule(text) {
                Ok(rule) => rules.push(rule),
                Err(error) => errors.push(format!("{text}: {error}")),
            }
        }
        (Waits { rules }, errors)
    }

    /// `ISUCON_WAITS`から読み、読んだ表と読めなかった規則を標準エラーへ書く。
    pub fn from_env() -> Waits {
        let spec = std::env::var(VARIABLE).unwrap_or_default();
        let (waits, errors) = Waits::parse(&spec);
        for error in &errors {
            eprintln!("waits: ignored {error}");
        }
        if !waits.rules.is_empty() {
            eprintln!("waits: {}", waits.describe());
        }
        waits
    }

    pub fn is_empty(&self) -> bool {
        self.rules.is_empty()
    }

    /// handlerの前に待つ時間。
    pub fn before(&self, method: &str, path: &str) -> Option<Duration> {
        self.rules
            .iter()
            .find(|rule| rule.phase == Phase::Before && rule.matches(method, path))
            .map(|rule| Duration::from_millis(rule.ms))
    }

    /// handlerの後、応答を返す前に待つ時間。`body`は長さの分かる本文のバイト数（分からなければNone）。
    pub fn after(&self, method: &str, path: &str, status: u16, body: Option<u64>) -> Option<Duration> {
        self.rules
            .iter()
            .find(|rule| {
                rule.phase == Phase::After
                    && rule.matches(method, path)
                    && rule.status.map_or(true, |code| code == status)
                    && rule.max_body.map_or(true, |limit| body.is_some_and(|length| length <= limit))
            })
            .map(|rule| Duration::from_millis(rule.ms))
    }

    /// 読んだ表を、`ISUCON_WAITS`と同じ形で並べる。
    pub fn describe(&self) -> String {
        self.rules
            .iter()
            .map(|rule| {
                let mut text = format!(
                    "{} /{} {} {}",
                    rule.method,
                    rule.path[1..].join("/"),
                    if rule.phase == Phase::Before { "before" } else { "after" },
                    rule.ms
                );
                if let Some(limit) = rule.max_body {
                    text.push_str(&format!(" max_body={limit}"));
                }
                if let Some(code) = rule.status {
                    text.push_str(&format!(" status={code}"));
                }
                text
            })
            .collect::<Vec<_>>()
            .join(";")
    }
}

fn parse_rule(text: &str) -> Result<Rule, String> {
    let words: Vec<&str> = text.split_whitespace().collect();
    if words.len() < 4 {
        return Err("METHOD PATH before|after MS が要る".into());
    }
    let method = words[0].to_ascii_uppercase();
    if !method.bytes().all(|c| c.is_ascii_uppercase()) {
        return Err(format!("METHODが読めない: {}", words[0]));
    }
    if !words[1].starts_with('/') {
        return Err(format!("PATHは/で始める: {}", words[1]));
    }
    let path = words[1].split('/').map(str::to_owned).collect();
    let phase = match words[2] {
        "before" => Phase::Before,
        "after" => Phase::After,
        other => return Err(format!("before・afterのどちらか: {other}")),
    };
    let ms: u64 = words[3].parse().map_err(|_| format!("MSが整数でない: {}", words[3]))?;
    let mut rule = Rule { method, path, phase, ms: ms.min(MAX_MS), max_body: None, status: None };
    for option in &words[4..] {
        match option.split_once('=') {
            Some(("max_body", value)) => {
                rule.max_body = Some(value.parse().map_err(|_| format!("max_bodyが整数でない: {value}"))?)
            }
            Some(("status", value)) => {
                rule.status = Some(value.parse().map_err(|_| format!("statusが整数でない: {value}"))?)
            }
            _ => return Err(format!("読めない指定: {option}")),
        }
    }
    if rule.phase == Phase::Before && (rule.max_body.is_some() || rule.status.is_some()) {
        return Err("max_body・statusはafterだけ".into());
    }
    Ok(rule)
}

static GLOBAL: OnceLock<Waits> = OnceLock::new();

/// 起動時に`ISUCON_WAITS`から1度だけ読んだ表。
pub fn global() -> &'static Waits {
    GLOBAL.get_or_init(Waits::from_env)
}
