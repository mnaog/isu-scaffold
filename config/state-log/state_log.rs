//! isuscopeの状態ログの書き手。
//!
//! アプリの中の対象（注文、予約、配車など）が状態を変えるたびに、1行のJSONを書く。isuscopeの
//! `state-log` collectorがベンチの前後の差分を運び、`isuscope brief`の`states`欄と
//! `isuscope states sql`・`isuscope states timeline`で読む。
//!
//! - リクエストを待たせない。行はqueueへ入れるだけで、別のthreadが100msごとにまとめて取り出して書く。
//!   queueがあふれたら捨てて、捨てた数を`{"t":..,"dropped":n}`の行で残す。
//! - 書き先は環境変数`ISUSCOPE_STATE_LOG`（無ければ`/var/tmp/isuscope-state.jsonl`）。
//!   空にすると何も書かない（最終計測の前に止めるとき）。
//! - 追加のcrateは要らない。このfileをcrateへそのまま置き、`mod state_log;`で使う。中身は変えない。
//!
//! ```ignore
//! state_log::created("order", &order.id, "PENDING", &[("user", user_id.into())]);
//! state_log::changed("order", &order.id, "PENDING", "PAID", &[("amount", amount.into())]);
//! // 前の状態を知らないとき（状態の列を読まずにUPDATEするとき）。前の状態を読み足さない。
//! state_log::entered("order", &order.id, "SHIPPED", &[]);
//!
//! // transactionの中で変えるなら、commitが成功した後に書く。
//! let mut log = state_log::Pending::new();
//! log.changed("order", &order.id, "PENDING", "PAID", &[]);
//! tx.commit().await?;
//! log.emit();
//! ```
//!
//! 1行の形（isuscopeとの約束）: `{"t":<Unix時刻のマイクロ秒>,"seq":<process内の通し番号>,
//! "kind":"..","id":"..","from":".."|null,"to":"..","attrs":{..}}`。作られたときは`"from":null`、
//! 前の状態を知らないときは`from`を書かない。`attrs`は入れ子にしない。
#![allow(dead_code)]

use std::fmt::{Display, Write as _};
use std::fs::OpenOptions;
use std::io::Write as _;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicI64, AtomicU64, Ordering};
use std::sync::mpsc::{sync_channel, Receiver, SyncSender, TryRecvError, TrySendError};
use std::sync::{Arc, Mutex, OnceLock};
use std::thread::JoinHandle;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

/// isuscopeのcollectorが既定で読むpath。
pub const DEFAULT_PATH: &str = "/var/tmp/isuscope-state.jsonl";
/// 書き先を変える、または空にして止める環境変数。
pub const PATH_VARIABLE: &str = "ISUSCOPE_STATE_LOG";

const QUEUE: usize = 1 << 16;
const FLUSH_EVERY: Duration = Duration::from_millis(100);
const FLUSH_BYTES: usize = 1 << 20;

/// `attrs`の値。文字列、整数、小数、真偽値だけ（入れ子にしない）。
#[derive(Debug, Clone, PartialEq)]
pub enum Attr {
    Str(String),
    Int(i64),
    Float(f64),
    Bool(bool),
}

impl From<&str> for Attr {
    fn from(value: &str) -> Self {
        Attr::Str(value.to_owned())
    }
}
impl From<String> for Attr {
    fn from(value: String) -> Self {
        Attr::Str(value)
    }
}
impl From<&String> for Attr {
    fn from(value: &String) -> Self {
        Attr::Str(value.clone())
    }
}
impl From<bool> for Attr {
    fn from(value: bool) -> Self {
        Attr::Bool(value)
    }
}
impl From<f64> for Attr {
    fn from(value: f64) -> Self {
        Attr::Float(value)
    }
}
impl From<f32> for Attr {
    fn from(value: f32) -> Self {
        Attr::Float(value.into())
    }
}
macro_rules! integer_attr {
    ($($type:ty),*) => {$(
        impl From<$type> for Attr {
            fn from(value: $type) -> Self {
                Attr::Int(i64::try_from(value).unwrap_or(i64::MAX))
            }
        }
    )*};
}
integer_attr!(i8, i16, i32, i64, u8, u16, u32, u64, isize, usize);

/// 対象が作られたときに書く（`"from":null`）。
pub fn created(kind: &str, id: impl Display, to: &str, attrs: &[(&'static str, Attr)]) {
    if let Some(writer) = global() {
        writer.send(Event::new(kind, id, Previous::Created, to, attrs));
    }
}

/// 対象の状態が`from`から`to`へ変わったときに書く。
pub fn changed(kind: &str, id: impl Display, from: &str, to: &str, attrs: &[(&'static str, Attr)]) {
    if let Some(writer) = global() {
        writer.send(Event::new(
            kind,
            id,
            Previous::Known(from.to_owned()),
            to,
            attrs,
        ));
    }
}

/// 前の状態を知らずに`to`へ変えたときに書く（`from`を書かない）。前の状態を読み足さないこと。
pub fn entered(kind: &str, id: impl Display, to: &str, attrs: &[(&'static str, Attr)]) {
    if let Some(writer) = global() {
        writer.send(Event::new(kind, id, Previous::Unknown, to, attrs));
    }
}

/// transactionの中の変化をためておき、commitが成功した後に`emit`で書く。commitせずに
/// 捨てれば（`?`で途中で戻るなど）何も書かない。時刻は`emit`したとき。
#[derive(Debug, Default)]
#[must_use = "call emit() after the transaction commits"]
pub struct Pending {
    events: Vec<Event>,
}

impl Pending {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn created(
        &mut self,
        kind: &str,
        id: impl Display,
        to: &str,
        attrs: &[(&'static str, Attr)],
    ) {
        if global().is_some() {
            self.events
                .push(Event::new(kind, id, Previous::Created, to, attrs));
        }
    }

    pub fn changed(
        &mut self,
        kind: &str,
        id: impl Display,
        from: &str,
        to: &str,
        attrs: &[(&'static str, Attr)],
    ) {
        if global().is_some() {
            self.events.push(Event::new(
                kind,
                id,
                Previous::Known(from.to_owned()),
                to,
                attrs,
            ));
        }
    }

    pub fn entered(
        &mut self,
        kind: &str,
        id: impl Display,
        to: &str,
        attrs: &[(&'static str, Attr)],
    ) {
        if global().is_some() {
            self.events
                .push(Event::new(kind, id, Previous::Unknown, to, attrs));
        }
    }

    pub fn emit(self) {
        if let Some(writer) = global() {
            let now = now_micros();
            for mut event in self.events {
                event.t = now;
                writer.send(event);
            }
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
enum Previous {
    Created,
    Known(String),
    Unknown,
}

#[derive(Debug, Clone, PartialEq)]
struct Event {
    t: i64,
    seq: i64,
    kind: String,
    id: String,
    from: Previous,
    to: String,
    attrs: Vec<(&'static str, Attr)>,
}

impl Event {
    fn new(
        kind: &str,
        id: impl Display,
        from: Previous,
        to: &str,
        attrs: &[(&'static str, Attr)],
    ) -> Self {
        Self {
            t: now_micros(),
            seq: 0,
            kind: kind.to_owned(),
            id: id.to_string(),
            from,
            to: to.to_owned(),
            attrs: attrs.to_vec(),
        }
    }

    fn write(&self, line: &mut String) {
        let _ = write!(line, "{{\"t\":{},\"seq\":{},\"kind\":", self.t, self.seq);
        json_string(line, &self.kind);
        line.push_str(",\"id\":");
        json_string(line, &self.id);
        match &self.from {
            Previous::Created => line.push_str(",\"from\":null"),
            Previous::Known(from) => {
                line.push_str(",\"from\":");
                json_string(line, from);
            }
            Previous::Unknown => {}
        }
        line.push_str(",\"to\":");
        json_string(line, &self.to);
        if !self.attrs.is_empty() {
            line.push_str(",\"attrs\":{");
            for (index, (key, value)) in self.attrs.iter().enumerate() {
                if index > 0 {
                    line.push(',');
                }
                json_string(line, key);
                line.push(':');
                match value {
                    Attr::Str(value) => json_string(line, value),
                    Attr::Int(value) => {
                        let _ = write!(line, "{value}");
                    }
                    Attr::Float(value) if value.is_finite() => {
                        let _ = write!(line, "{value}");
                    }
                    Attr::Float(_) => line.push_str("null"),
                    Attr::Bool(value) => {
                        let _ = write!(line, "{value}");
                    }
                }
            }
            line.push('}');
        }
        line.push_str("}\n");
    }
}

fn json_string(line: &mut String, value: &str) {
    line.push('"');
    for character in value.chars() {
        match character {
            '"' => line.push_str("\\\""),
            '\\' => line.push_str("\\\\"),
            '\n' => line.push_str("\\n"),
            '\r' => line.push_str("\\r"),
            '\t' => line.push_str("\\t"),
            character if (character as u32) < 0x20 => {
                let _ = write!(line, "\\u{:04x}", character as u32);
            }
            character => line.push(character),
        }
    }
    line.push('"');
}

fn now_micros() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|elapsed| elapsed.as_micros() as i64)
        .unwrap_or(0)
}

/// 環境変数の値から書き先を決める。空なら書かない。
fn path_from(value: Option<std::ffi::OsString>) -> Option<PathBuf> {
    match value {
        Some(value) if value.is_empty() => None,
        Some(value) => Some(PathBuf::from(value)),
        None => Some(PathBuf::from(DEFAULT_PATH)),
    }
}

fn global() -> Option<&'static Writer> {
    static WRITER: OnceLock<Option<Writer>> = OnceLock::new();
    WRITER
        .get_or_init(|| {
            path_from(std::env::var_os(PATH_VARIABLE)).map(|path| Writer::start(path, QUEUE))
        })
        .as_ref()
        .filter(|writer| !writer.closed.load(Ordering::Relaxed))
}

struct Writer {
    sender: SyncSender<Event>,
    seq: AtomicI64,
    dropped: Arc<AtomicU64>,
    closed: Arc<AtomicBool>,
    thread: Mutex<Option<JoinHandle<()>>>,
}

impl Writer {
    fn start(path: PathBuf, queue: usize) -> Self {
        let (sender, receiver) = sync_channel(queue);
        let dropped = Arc::new(AtomicU64::new(0));
        let closed = Arc::new(AtomicBool::new(false));
        let thread = {
            let (dropped, closed) = (dropped.clone(), closed.clone());
            std::thread::Builder::new()
                .name("state-log".into())
                .spawn(move || run(path, receiver, dropped, closed))
                .ok()
        };
        if thread.is_none() {
            closed.store(true, Ordering::Relaxed);
        }
        Self {
            sender,
            seq: AtomicI64::new(0),
            dropped,
            closed,
            thread: Mutex::new(thread),
        }
    }

    fn send(&self, mut event: Event) {
        if self.closed.load(Ordering::Relaxed) {
            return;
        }
        event.seq = self.seq.fetch_add(1, Ordering::Relaxed) + 1;
        match self.sender.try_send(event) {
            Ok(()) => {}
            Err(TrySendError::Full(_)) => {
                self.dropped.fetch_add(1, Ordering::Relaxed);
            }
            Err(TrySendError::Disconnected(_)) => self.closed.store(true, Ordering::Relaxed),
        }
    }

    /// 残りを書いてthreadを止める（testで使う）。
    fn close(self) {
        let Self { sender, thread, .. } = self;
        drop(sender);
        if let Some(thread) = thread.into_inner().ok().flatten() {
            let _ = thread.join();
        }
    }
}

fn run(path: PathBuf, receiver: Receiver<Event>, dropped: Arc<AtomicU64>, closed: Arc<AtomicBool>) {
    let mut file = match OpenOptions::new().create(true).append(true).open(&path) {
        Ok(file) => file,
        Err(error) => {
            eprintln!(
                "state_log: cannot open {}: {error}; state events are not written",
                path.display()
            );
            closed.store(true, Ordering::Relaxed);
            return;
        }
    };
    let mut buffer = String::with_capacity(FLUSH_BYTES);
    let mut reported = false;
    let mut write_out = |buffer: &mut String| {
        if buffer.is_empty() {
            return;
        }
        if let Err(error) = file.write_all(buffer.as_bytes()) {
            if !reported {
                eprintln!("state_log: cannot write {}: {error}", path.display());
                reported = true;
            }
        }
        buffer.clear();
    };
    loop {
        // 受け手を待たせておくと、送り手が1件ごとに起こすsystem callを払う。間隔を空けて
        // まとめて取り出せば、送り手はqueueへ入れるだけで済む。
        std::thread::sleep(FLUSH_EVERY);
        let disconnected = loop {
            match receiver.try_recv() {
                Ok(event) => {
                    event.write(&mut buffer);
                    if buffer.len() >= FLUSH_BYTES {
                        write_out(&mut buffer);
                    }
                }
                Err(TryRecvError::Empty) => break false,
                Err(TryRecvError::Disconnected) => break true,
            }
        };
        let lost = dropped.swap(0, Ordering::Relaxed);
        if lost > 0 {
            let _ = writeln!(buffer, "{{\"t\":{},\"dropped\":{lost}}}", now_micros());
        }
        write_out(&mut buffer);
        if disconnected {
            return;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Instant;

    fn temporary(name: &str) -> PathBuf {
        let directory = std::env::temp_dir().join(format!(
            "state-log-{}-{}-{name}",
            std::process::id(),
            now_micros()
        ));
        std::fs::create_dir_all(&directory).unwrap();
        directory.join("state.jsonl")
    }

    #[test]
    fn lines_follow_the_isuscope_format() {
        let mut line = String::new();
        let mut event = Event::new(
            "ride",
            42,
            Previous::Known("MATCHING".into()),
            "EN\"ROUTE",
            &[
                ("chair", "c\\1\n".into()),
                ("distance", 512.into()),
                ("rate", 0.5.into()),
                ("bad", f64::NAN.into()),
                ("paid", true.into()),
            ],
        );
        event.t = 1_700_000_000_000_001;
        event.seq = 7;
        event.write(&mut line);
        assert_eq!(
            line,
            "{\"t\":1700000000000001,\"seq\":7,\"kind\":\"ride\",\"id\":\"42\",\"from\":\"MATCHING\",\"to\":\"EN\\\"ROUTE\",\"attrs\":{\"chair\":\"c\\\\1\\n\",\"distance\":512,\"rate\":0.5,\"bad\":null,\"paid\":true}}\n"
        );
        let mut created = String::new();
        Event::new("ride", "r1", Previous::Created, "A", &[]).write(&mut created);
        assert!(
            created.contains(",\"from\":null,\"to\":\"A\"}"),
            "{created}"
        );
        let mut entered = String::new();
        Event::new("ride", "r1", Previous::Unknown, "B", &[]).write(&mut entered);
        assert!(!entered.contains("from"), "{entered}");
    }

    #[test]
    fn many_threads_write_every_event_once() {
        let path = temporary("threads");
        let writer = Arc::new(Writer::start(path.clone(), QUEUE));
        let threads = (0..8)
            .map(|thread| {
                let writer = writer.clone();
                std::thread::spawn(move || {
                    for index in 0..1000 {
                        writer.send(Event::new(
                            "item",
                            format!("{thread}-{index}"),
                            Previous::Created,
                            "NEW",
                            &[],
                        ));
                    }
                })
            })
            .collect::<Vec<_>>();
        for thread in threads {
            thread.join().unwrap();
        }
        Arc::try_unwrap(writer).ok().unwrap().close();
        let written = std::fs::read_to_string(&path).unwrap();
        assert_eq!(written.lines().count(), 8000);
        let mut seqs = written
            .lines()
            .map(|line| {
                let rest = &line[line.find("\"seq\":").unwrap() + 6..];
                rest[..rest.find(',').unwrap()].parse::<i64>().unwrap()
            })
            .collect::<Vec<_>>();
        seqs.sort_unstable();
        seqs.dedup();
        assert_eq!(seqs.len(), 8000);
    }

    #[test]
    fn a_full_queue_drops_and_counts_instead_of_waiting() {
        let path = temporary("full");
        let writer = Writer::start(path.clone(), 1);
        let started = Instant::now();
        for index in 0..10_000 {
            writer.send(Event::new("item", index, Previous::Created, "NEW", &[]));
        }
        assert!(
            started.elapsed() < Duration::from_secs(1),
            "send must not block"
        );
        writer.close();
        let written = std::fs::read_to_string(&path).unwrap();
        let events = written
            .lines()
            .filter(|line| line.contains("\"kind\""))
            .count() as u64;
        let dropped = written
            .lines()
            .filter_map(|line| line.split("\"dropped\":").nth(1))
            .map(|count| count.trim_end_matches('}').parse::<u64>().unwrap())
            .sum::<u64>();
        assert!(dropped > 0, "{written}");
        assert_eq!(events + dropped, 10_000);
    }

    #[test]
    fn an_empty_variable_turns_the_log_off() {
        assert_eq!(path_from(Some("".into())), None);
        assert_eq!(path_from(None), Some(PathBuf::from(DEFAULT_PATH)));
        assert_eq!(
            path_from(Some("/tmp/x.jsonl".into())),
            Some(PathBuf::from("/tmp/x.jsonl"))
        );
    }

    #[test]
    fn an_unwritable_path_disables_the_writer() {
        let writer = Writer::start(PathBuf::from("/nonexistent-directory/state.jsonl"), QUEUE);
        let deadline = Instant::now() + Duration::from_secs(2);
        while !writer.closed.load(Ordering::Relaxed) && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(10));
        }
        assert!(writer.closed.load(Ordering::Relaxed));
        writer.send(Event::new("item", 1, Previous::Created, "NEW", &[]));
        writer.close();
    }
}
