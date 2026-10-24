//! The tool guard: stops operations the rules forbid (practice rules included).
//!
//! Stopped calls:
//! - a direct SSH, scp, rsync, sftp or ssh-node.sh session to the benchmark node
//!   (benchmarks run through isuscope and .isuscope/benchmark.sh, which the hook does not see)
//! - setting ISUCON_ALLOW_BENCHMARK_NODE, which only the adapter and bootstrap may set
//! - reading or copying the benchmarker: `benchmarker_paths`, and `<keyword>.../bench`
//!   for each contest keyword (for example a clone of the contest repository)
//! - fetching or cloning the current contest's code from GitHub, or fetching a page about it
//!   outside `allowed_urls`, and web searches about the current contest
//!
//! While a main session is declared (/main, scripts/main-session.py), every other session is also
//! refused what only main does: deploy, rollback, import, kickoff, the benchmark (not the adapter's
//! --check and --probe), isuscope records, deciding worktree proposals, starting or stopping lanes,
//! and the operation lock. Calls without a session id, and every call while no main is declared,
//! are not checked for this. An AI lane (ISUCON_LANE) is refused remote nodes, deploy, the benchmark
//! and the lock.
//!
//! Contest keywords and paths come from config/tool-guard.json. Empty lists turn those rules off; the
//! benchmark node rule always applies. The hook only sees the command it is given, so
//! scripts/ssh-node.sh refuses the benchmark node by itself as well.

use crate::shlex;
use fancy_regex::Regex;
use serde_json::{json, Map, Value};
use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::OnceLock;

pub struct Denied(pub String);
type Checked = Result<(), Denied>;

const ALLOW_VARIABLE: &str = "ISUCON_ALLOW_BENCHMARK_NODE";
const SSH_PROGRAMS: &[&str] = &["ssh", "mosh", "autossh"];
const COPY_PROGRAMS: &[&str] = &["scp", "rsync", "sftp"];
// ssh options that take a value; the host is the first argument after the options.
const SSH_VALUE_OPTIONS: &str = "BbcDEeFIiJLlmOoPpQRSWw";
const CODE_HOSTS: &[&str] = &["github.com", "www.github.com", "raw.githubusercontent.com", "gist.github.com",
    "gist.githubusercontent.com", "codeload.github.com", "api.github.com"];
const WRAPPERS: &[&str] = &["sudo", "env", "nice", "nohup", "time", "exec", "command", "timeout", "gtimeout",
    "xargs", "stdbuf", "ionice"];
pub const SHELLS: &[&str] = &["bash", "sh", "zsh", "dash"];
// Fields that name something to read or fetch. Text being written (content, new_string) is not checked.
const PATH_KEYS: &[&str] = &["file_path", "path", "paths", "notebook_path", "filePath", "pattern", "glob",
    "directory", "dir", "url", "uri"];
const QUERY_KEYS: &[&str] = &["query", "q", "search_query"];
const OPERATOR_CHARS: &str = ";&|()\n";
// Programs whose heredoc or arguments are text being written, not something read or run.
const DATA_WRITERS: &[&str] = &["cat", "tee", "echo", "printf"];
const WRITE_HINT: &str = "説明や設定に書くだけなら、Write・Editツールで書く。";
const LANE_HINT: &str = "laneはremote node・deploy・ベンチ・操作lockに触れない。必要な操作は最後の回答でmainのセッションへ頼む。";
// Scripts and make targets that reach remote nodes, the benchmark or the operation lock.
const LANE_SCRIPTS: &[&str] = &["deploy.sh", "rollback.sh", "import.sh", "kickoff.sh", "kickoff-apply.sh",
    "bootstrap.sh", "bootstrap-ssh.sh", "discover.sh", "configure-draft.sh", "configure-apply.sh",
    "inspect-environment.sh", "bench-check.sh", "quick-import-code.sh", "run-ansible-playbook.sh", "status.sh",
    "switch-language-service.sh", "sync-check.sh", "benchmark.sh", "benchmark-prepare.sh", "ansible-install.sh",
    "tare.py", "durability.py", "kickoff-build.py", "lane.py", "review-draft.py", "check-deployed-commit.sh",
    "first-bench.sh", "bench.sh", "benchmark-markers.py", "deploy-plan.py", "final-verification.py"];
const LANE_MAKE_TARGETS: &[&str] = &["deploy", "rollback", "status", "final-check", "final-reboot", "kickoff",
    "kickoff-apply", "discover", "bootstrap", "bench-check", "first-bench", "bench", "tare", "durability"];
// Read-only isuscope commands; everything else records, benchmarks or reaches nodes.
const LANE_ISUSCOPE: &[&str] = &["list", "brief", "query", "sql", "series", "help", "--help", "-h", "--version",
    "version"];
// `isuscope change` subcommands that only read; `decide` records.
const READ_CHANGE: &[&str] = &["list", "show", "help", "--help", "-h"];
const LANE_PROGRAMS: &[&str] = &["ansible", "ansible-playbook", "aws", "gh", "claude", "codex", "opencode"];
const MAIN_HINT: &str = "この操作はmainのセッション（/mainで宣言したセッション）だけが行う。worktreeの変更はcommitして`python3 scripts/proposal.py submit --hypothesis ... --expect ...`で投稿すると、mainがベンチを起動したときに渡る。mainを引き継ぐならrepoのrootで/mainを打つ。";
// What only main does while a main is declared: changes to remote nodes, the benchmark and its records.
const MAIN_SCRIPTS: &[&str] = &["deploy.sh", "rollback.sh", "import.sh", "kickoff.sh", "kickoff-apply.sh",
    "bootstrap.sh", "bootstrap-ssh.sh", "configure-apply.sh", "bench-check.sh", "quick-import-code.sh",
    "run-ansible-playbook.sh", "switch-language-service.sh", "benchmark.sh", "benchmark-prepare.sh",
    "benchmark-stop.sh", "ansible-install.sh", "tare.py", "durability.py", "first-bench.sh", "bench.sh",
    "final-verification.py"];
const MAIN_MAKE_TARGETS: &[&str] = &["deploy", "rollback", "final-reboot", "kickoff", "kickoff-apply", "discover",
    "bootstrap", "bench-check", "first-bench", "bench", "tare", "durability"];
const PROPOSAL_MAIN: &[&str] = &["decide", "pending", "sync", "hold", "unhold", "requeue", "freeze", "adopt"];
const BENCH_NODE: &str = "ベンチnodeへ直接入らない。ベンチは isuscope run（.isuscope/benchmark.sh）からだけ動かし、ベンチnodeの観測もしない。";

macro_rules! regex {
    ($pattern:expr) => {{
        static CELL: OnceLock<Regex> = OnceLock::new();
        CELL.get_or_init(|| Regex::new($pattern).expect("a valid pattern"))
    }};
}

fn url_pattern() -> &'static Regex {
    regex!(r#"(?:https?://|git@)[^\s'"<>()`]+"#)
}

fn assignment() -> &'static Regex {
    regex!(r"^[A-Za-z_][A-Za-z0-9_]*=")
}

fn shell_command_option() -> &'static Regex {
    regex!(r"^-[a-z]*c[a-z]*$")
}

fn is_match(pattern: &Regex, text: &str) -> bool {
    pattern.is_match(text).unwrap_or(false)
}

fn find_all<'t>(pattern: &Regex, text: &'t str) -> Vec<&'t str> {
    pattern.find_iter(text).filter_map(Result::ok).map(|m| m.as_str()).collect()
}

fn among(set: &[&str], word: &str) -> bool {
    set.contains(&word)
}

pub fn basename(path: &str) -> &str {
    path.rsplit('/').next().unwrap_or(path)
}

/// The words of `regex::escape`: the pattern matches the text as it is.
fn escape(text: &str) -> String {
    let mut escaped = String::with_capacity(text.len());
    for c in text.chars() {
        if "\\.+*?()|[]{}^$#&-~".contains(c) {
            escaped.push('\\');
        }
        escaped.push(c);
    }
    escaped
}

/// Python's truth of a JSON value.
pub fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// Python's `str()` of a JSON value.
pub fn text_of(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        Value::Null => "None".to_string(),
        Value::Bool(true) => "True".to_string(),
        Value::Bool(false) => "False".to_string(),
        other => other.to_string(),
    }
}

fn load_json(path: &Path) -> Option<Value> {
    serde_json::from_str(&std::fs::read_to_string(path).ok()?).ok()
}

fn env_file(path: &Path) -> HashMap<String, String> {
    let mut values = HashMap::new();
    let Ok(text) = std::fs::read_to_string(path) else { return values };
    for line in text.lines() {
        let line = line.trim();
        let Some((key, value)) = line.split_once('=') else { continue };
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let parts = shlex::split(value, " \t\r\n", "").unwrap_or_else(|_| vec![value.to_string()]);
        values.insert(key.trim().to_string(), parts.into_iter().next().unwrap_or_default());
    }
    values
}

/// Names and addresses of the benchmark node from the inventory, discovery and benchmark.env.
fn benchmark_ids(root: &Path) -> HashSet<String> {
    let mut ids = HashSet::new();
    let inventory = load_json(&root.join(".local/ansible-inventory.json")).unwrap_or(Value::Null);
    let hosts = inventory.pointer("/all/children/benchmark/hosts").and_then(Value::as_object);
    for (name, entry) in hosts.into_iter().flatten() {
        ids.insert(name.clone());
        if let Some(host) = entry.get("ansible_host").filter(|h| truthy(h)) {
            ids.insert(text_of(host));
        }
    }
    let nodes = load_json(&root.join(".local/discovered-nodes.json")).unwrap_or(Value::Null);
    for node in nodes.as_array().into_iter().flatten() {
        if node.get("group").and_then(Value::as_str) == Some("benchmark") {
            for key in ["name", "display_name", "host", "public_ip", "private_ip"] {
                if let Some(value) = node.get(key).filter(|v| truthy(v)) {
                    ids.insert(text_of(value));
                }
            }
        }
    }
    let benchmark = env_file(&root.join("config/benchmark.env"));
    if benchmark.get("BENCHMARK_TRANSPORT").map(String::as_str) == Some("ssh") {
        if let Some(node) = benchmark.get("BENCHMARK_NODE").filter(|n| !n.is_empty()) {
            ids.insert(node.clone());
        }
    }
    ids.remove("");
    ids
}

/// Python's `str.splitlines()` for the line breaks a command holds.
fn split_lines(text: &str) -> Vec<&str> {
    let mut lines = Vec::new();
    let mut rest = text;
    while !rest.is_empty() {
        match rest.find(['\n', '\r']) {
            Some(at) => {
                lines.push(&rest[..at]);
                let skip = if rest[at..].starts_with("\r\n") { 2 } else { 1 };
                rest = &rest[at + skip..];
            }
            None => {
                lines.push(rest);
                break;
            }
        }
    }
    lines
}

/// Drops heredoc bodies fed to cat, tee or git (documents, commit messages), or every body.
fn strip_data_heredocs(text: &str, everything: bool) -> String {
    let heredoc = regex!(r#"<<-?\s*(['"]?)([A-Za-z_][A-Za-z0-9_]*)\1"#);
    let separator = regex!(r"&&|\|\||[;|&]");
    let lines: Vec<&str> = text.split('\n').collect();
    let mut kept: Vec<&str> = Vec::new();
    let mut index = 0;
    while index < lines.len() {
        let line = lines[index];
        kept.push(line);
        if let Ok(Some(captures)) = heredoc.captures(line) {
            let before = &line[..captures.get(0).map_or(0, |m| m.start())];
            let segment_start = separator.find_iter(before).filter_map(Result::ok).last().map_or(0, |m| m.end());
            let consumer = before[segment_start..].split_whitespace().next().map(basename).unwrap_or("");
            let delimiter = captures.get(2).map_or("", |m| m.as_str());
            let mut end = index + 1;
            while end < lines.len() && lines[end].trim() != delimiter {
                end += 1;
            }
            if !everything && !(among(DATA_WRITERS, consumer) || consumer == "git") {
                kept.extend(&lines[index + 1..end]);
            }
            if end < lines.len() {
                kept.push(lines[end]);
            }
            index = end;
        }
        index += 1;
    }
    kept.join("\n")
}

fn host_of(target: &str) -> &str {
    target.rsplit('@').next().unwrap_or(target).trim_matches(|c| c == '[' || c == ']')
}

/// What a Bash call runs, for the benchmark hook: benchmarks started and `isuscope analyze` calls.
#[derive(Default)]
pub struct Found {
    pub benchmarks: usize,
    pub analyses: usize,
    pub based: bool,
}

/// isuscope run, make first-bench / bench, or scripts/first-bench.sh / bench.sh, but not their help.
fn is_benchmark(program: &str, args: &[String]) -> bool {
    let options: Vec<&str> = args.iter().map(String::as_str).filter(|a| a.starts_with('-')).collect();
    if options.contains(&"-h") || options.contains(&"--help") {
        return false;
    }
    if program == "isuscope" {
        return args.first().is_some_and(|a| a == "run");
    }
    if program == "make" {
        return args.iter().any(|a| !a.starts_with('-') && (a == "first-bench" || a == "bench"));
    }
    let mut program = program;
    if among(SHELLS, program) {
        if options.contains(&"-n") {
            return false; // a syntax check reads the script without running it
        }
        program = basename(args.iter().find(|a| !a.starts_with('-')).map_or("", String::as_str));
    }
    program == "first-bench.sh" || program == "bench.sh"
}

pub struct Guard {
    keywords: Vec<String>,
    allowed_urls: Vec<String>,
    bench_ids: HashSet<String>,
    lane: Option<String>,
    root: PathBuf,
    not_main: bool, // set per call: a main is declared and this session is not it
    drop_heredocs: bool,
    bench_paths: Option<Regex>,
    found: Option<Found>,
}

impl Guard {
    pub fn new(root: &Path) -> Guard {
        let config = load_json(&root.join("config/tool-guard.json")).unwrap_or(json!({}));
        let strings = |key: &str| -> Vec<String> {
            config.get(key).and_then(Value::as_array).into_iter().flatten().filter(|v| truthy(v)).map(text_of).collect()
        };
        let keywords: Vec<String> = strings("contest_keywords").iter().map(|k| k.to_lowercase()).collect();
        let mut patterns: Vec<String> = strings("benchmarker_paths").iter()
            .map(|p| format!(r"{}(?![\w.-])", escape(p.trim_end_matches('/')))).collect();
        patterns.extend(keywords.iter().map(|k| format!(r"(?<![\w.-]){}[\w.-]*/bench(?![\w.-])", escape(k))));
        let bench_paths = if patterns.is_empty() { None } else { Regex::new(&format!("(?i){}", patterns.join("|"))).ok() };
        Guard {
            keywords,
            allowed_urls: strings("allowed_urls"),
            bench_ids: benchmark_ids(root),
            lane: std::env::var("ISUCON_LANE").ok().filter(|l| !l.is_empty()),
            root: root.to_path_buf(),
            not_main: false,
            drop_heredocs: false,
            bench_paths,
            found: None,
        }
    }

    // --- rules ---------------------------------------------------------------------------
    fn check_text_paths(&self, text: &str) -> Checked {
        if let Some(pattern) = &self.bench_paths {
            if let Ok(Some(found)) = pattern.find(text) {
                return Err(Denied(format!("ベンチマーカーの中身（{}）は読まない・写さない（練習の規則）。{WRITE_HINT}",
                    found.as_str())));
            }
        }
        Ok(())
    }

    fn check_url(&self, url: &str, fetching_page: bool) -> Checked {
        let lowered = url.to_lowercase();
        if self.keywords.is_empty() || !self.keywords.iter().any(|k| lowered.contains(k.as_str())) {
            return Ok(());
        }
        if self.allowed_urls.iter().any(|prefix| url.starts_with(prefix.as_str())) {
            return Ok(());
        }
        let bare = ["https://", "http://", "git@"].iter().find_map(|p| lowered.strip_prefix(p)).unwrap_or(&lowered);
        let host = bare.split('/').next().unwrap_or("").split(':').next().unwrap_or("");
        if fetching_page || among(CODE_HOSTS, host) {
            return Err(Denied(format!("現在練習中の開催回のコードや攻略情報は取得しない（{url}）。\
                公式の配布資料なら config/tool-guard.json の allowed_urls に加える。{WRITE_HINT}")));
        }
        Ok(())
    }

    fn check_bench_host(&self, target: &str) -> Checked {
        let host = host_of(target);
        if self.bench_ids.contains(host) {
            return Err(Denied(format!("ベンチnode（{host}）へ直接入らない。ベンチは isuscope run\
                （.isuscope/benchmark.sh）からだけ動かし、ベンチnodeの観測もしない。")));
        }
        Ok(())
    }

    // --- shell commands ------------------------------------------------------------------
    fn check_shell(&mut self, text: &str, depth: usize) -> Checked {
        if depth > 4 || text.is_empty() {
            return Ok(());
        }
        let text = strip_data_heredocs(text, self.drop_heredocs);
        if is_match(regex!(r"(?<![\w])ISUCON_ALLOW_BENCHMARK_NODE\s*="), &text) {
            return Err(Denied(format!("{ALLOW_VARIABLE} はベンチadapterとkickoffの検査だけが設定する。")));
        }
        // A newline outside quotes ends a command; inside quotes it stays in the word.
        let Ok(tokens) = shlex::split(&text, " \t\r", OPERATOR_CHARS) else {
            self.check_text_paths(&text)?;
            for url in find_all(url_pattern(), &text) {
                self.check_url(url, false)?;
            }
            return self.check_shell_lines(&text);
        };
        let mut command = Vec::new();
        for token in tokens.into_iter().chain(std::iter::once(";".to_string())) {
            if token.chars().all(|c| OPERATOR_CHARS.contains(c)) {
                if !command.is_empty() {
                    self.check_simple(std::mem::take(&mut command), depth)?;
                }
            } else {
                command.push(token);
            }
        }
        Ok(())
    }

    /// Fallback for commands shlex cannot split (heredocs with unbalanced quotes).
    fn check_shell_lines(&self, text: &str) -> Checked {
        let mut ids: Vec<&String> = self.bench_ids.iter().collect();
        if ids.is_empty() {
            return Ok(());
        }
        ids.sort_by_key(|id| std::cmp::Reverse(id.chars().count()));
        let ids = ids.iter().map(|id| escape(id)).collect::<Vec<_>>().join("|");
        let patterns = [
            format!(r#"ssh-node\.sh["']?\s+["']?(?:{ids})(?![\w.-])"#),
            format!(r"(?<![\w.-])(?:ssh|mosh|autossh)\s+(?:-\S+\s+(?:\S+\s+)?)*(?:[\w.-]+@)?(?:{ids})(?![\w.-])"),
            format!(r"(?<![\w.-])(?:scp|rsync|sftp)\s[^\n;|&]*?(?:[\w.-]+@)?(?:{ids}):"),
        ];
        let patterns: Vec<Regex> = patterns.iter().filter_map(|p| Regex::new(p).ok()).collect();
        for line in split_lines(text) {
            if patterns.iter().any(|pattern| is_match(pattern, line)) {
                return Err(Denied(BENCH_NODE.to_string()));
            }
        }
        Ok(())
    }

    fn check_arguments(&self, words: &[String]) -> Checked {
        for word in words {
            self.check_text_paths(word)?;
            for url in find_all(url_pattern(), word) {
                self.check_url(url, false)?;
            }
        }
        Ok(())
    }

    fn check_simple(&mut self, mut words: Vec<String>, depth: usize) -> Checked {
        while !words.is_empty() {
            let head = basename(&words[0]).to_string();
            if is_match(assignment(), &words[0]) {
                let value = words[0].split_once('=').map_or("", |(_, v)| v).to_string();
                self.check_arguments(&[value])?;
                words.remove(0);
            } else if among(WRAPPERS, &head) {
                words.remove(0);
                while !words.is_empty()
                    && (words[0].starts_with('-')
                        || is_match(assignment(), &words[0])
                        || ((head == "timeout" || head == "gtimeout") && words[0].starts_with(|c: char| c.is_numeric()))
                        || (among(&["sudo", "nice", "ionice"], &head)
                            && words[0].chars().all(|c| c.is_numeric()) && !words[0].is_empty()))
                {
                    let option = words.remove(0);
                    if head == "sudo" && among(&["-u", "-g", "-C", "-h", "-p"], &option) && !words.is_empty() {
                        words.remove(0);
                    }
                    if head == "nice" && option == "-n" && !words.is_empty() {
                        words.remove(0);
                    }
                }
            } else if head == "isuscope" && words.len() > 1 && words[1] == "lock" {
                if self.lane.is_some() {
                    return Err(Denied(LANE_HINT.to_string()));
                }
                if self.not_main {
                    return Err(Denied(MAIN_HINT.to_string()));
                }
                words = match words.iter().position(|w| w == "--") {
                    Some(at) => words.split_off(at + 1),
                    None => Vec::new(),
                };
            } else if among(SHELLS, &head) && words.len() > 2 && is_match(shell_command_option(), &words[1]) {
                let script = words[2].clone();
                return self.check_shell(&script, depth + 1);
            } else {
                break;
            }
        }
        if words.is_empty() {
            return Ok(());
        }
        let program = basename(&words[0]).to_string();
        let args = &words[1..];
        let writing_text = among(DATA_WRITERS, &program)
            || (program == "git" && args.first().is_some_and(|a| a == "commit" || a == "tag" || a == "notes"));
        if !writing_text || program == "cat" {
            // cat with arguments reads files; only its heredoc body was dropped as text.
            self.check_arguments(&words)?;
        }
        if self.lane.is_some() {
            self.check_lane(&program, args)?;
        }
        if self.not_main {
            self.check_main_only(&program, args)?;
        }
        self.seen(&program, args);
        if program == "ssh-node.sh" {
            if let Some(target) = args.first() {
                self.check_bench_host(target)?;
                self.check_shell(&args[1..].join(" "), depth + 1)?;
            }
        } else if among(SSH_PROGRAMS, &program) {
            let mut index = 0;
            while index < args.len() && args[index].starts_with('-') {
                let option: Vec<char> = args[index].chars().collect();
                index += 1;
                if option.len() == 2 && SSH_VALUE_OPTIONS.contains(option[1]) {
                    index += 1;
                }
            }
            if index < args.len() {
                self.check_bench_host(&args[index])?;
                self.check_shell(&args[index + 1..].join(" "), depth + 1)?;
            }
        } else if among(COPY_PROGRAMS, &program) {
            let copy_host = regex!(r"^(?:[\w.-]+@)?([\w.-]+):");
            for arg in args {
                if let Ok(Some(captures)) = copy_host.captures(arg) {
                    self.check_bench_host(captures.get(1).map_or("", |m| m.as_str()))?;
                }
            }
            if program == "sftp" {
                if let Some(host) = args.iter().find(|a| !a.starts_with('-')) {
                    self.check_bench_host(host)?;
                }
            }
        } else if program == "ansible" {
            for arg in args {
                if arg == "benchmark" || self.bench_ids.contains(host_of(arg)) {
                    return Err(Denied(BENCH_NODE.to_string()));
                }
            }
        } else if program == "gh" && !self.keywords.is_empty() {
            let lowered = args.join(" ").to_lowercase();
            if self.keywords.iter().any(|k| lowered.contains(k.as_str())) {
                return Err(Denied("現在練習中の開催回のコードや攻略情報をGitHubから取得・検索しない。".to_string()));
            }
        }
        Ok(())
    }

    /// An AI lane works on its own branch; remote, benchmark and lock work stays with main.
    fn check_lane(&self, program: &str, args: &[String]) -> Checked {
        let mut script = program;
        if among(SHELLS, program) || program == "python" || program == "python3" {
            script = basename(args.iter().find(|a| !a.starts_with('-')).map_or("", String::as_str));
        }
        let denied = || Err(Denied(LANE_HINT.to_string()));
        if among(SSH_PROGRAMS, program) || among(COPY_PROGRAMS, program) || among(LANE_PROGRAMS, program)
            || program == "ssh-node.sh" || among(LANE_SCRIPTS, script) || among(LANE_SCRIPTS, program)
            || args.iter().any(|a| a.contains("operation.lock"))
        {
            return denied();
        }
        if program == "make" && args.iter().any(|a| !a.starts_with('-') && among(LANE_MAKE_TARGETS, a)) {
            return denied();
        }
        if program == "isuscope" && args.first().is_none_or(|a| !among(LANE_ISUSCOPE, a)) {
            return denied();
        }
        if program == "git" && args.iter().any(|a| a == "push") {
            return denied();
        }
        let local = regex!(r"^(?:https?://)?(?:localhost|127\.0\.0\.1|\[::1\])(?::\d+)?(?:/|$)");
        if (program == "curl" || program == "wget")
            && !args.iter()
                .filter(|a| !a.starts_with('-') && (a.contains('.') || a.contains(':') || a.contains('/')))
                .all(|a| is_match(local, a))
        {
            return denied();
        }
        Ok(())
    }

    /// While a main is declared, other sessions leave remote changes and the benchmark to it.
    fn check_main_only(&self, program: &str, args: &[String]) -> Checked {
        let mut script = program;
        let mut script_args: &[String] = args;
        let mut operands: Vec<&String> = args.iter().filter(|a| !a.starts_with('-')).collect();
        if among(SHELLS, program) || program == "python" || program == "python3" {
            match operands.first() {
                Some(first) => {
                    script = basename(first);
                    let at = args.iter().position(|a| a == *first).unwrap_or(0);
                    script_args = &args[at + 1..];
                }
                None => {
                    script = "";
                    script_args = &[];
                }
            }
            operands = operands.into_iter().skip(1).collect();
        }
        // The adapter's --check and --probe never start a benchmark: the portal session (/portal)
        // builds the connection with them while main runs kickoff.
        let first = script_args.first().map(String::as_str);
        let checking = (script == "benchmark.sh" && matches!(first, Some("--check") | Some("--probe")))
            || (script == "final-verification.py" && first == Some("check"));
        let denied = || Err(Denied(MAIN_HINT.to_string()));
        if (among(MAIN_SCRIPTS, script) && !checking) || program == "ansible" || program == "ansible-playbook"
            || args.iter().any(|a| a.contains("operation.lock"))
        {
            return denied();
        }
        let operand = operands.first().map(|o| o.as_str());
        if script == "lane.py" && matches!(operand, Some("start") | Some("kickoff") | Some("stop")) {
            return denied();
        }
        // Measuring and deciding a proposal is main's; improve and people submit and read.
        if script == "proposal.py" && operand.is_some_and(|o| among(PROPOSAL_MAIN, o)) {
            return denied();
        }
        if program == "make" && operands.iter().any(|o| among(MAIN_MAKE_TARGETS, o)) {
            return denied();
        }
        // Reading runs (states, route suggestions included) helps a worktree; recording them is main's.
        let reading_changes = args.first().is_some_and(|a| a == "change") && args.get(1).is_some_and(|a| among(READ_CHANGE, a));
        if program == "isuscope" && args.first().is_some_and(|a| !among(LANE_ISUSCOPE, a) && a != "states" && a != "routes")
            && !reading_changes
        {
            return denied();
        }
        Ok(())
    }

    /// Every simple command, after wrappers are removed: the benchmark hook watches for benchmarks here.
    fn seen(&mut self, program: &str, args: &[String]) {
        let Some(found) = self.found.as_mut() else { return };
        if is_benchmark(program, args) {
            found.benchmarks += 1;
            found.based |= args.iter().any(|a| a == "--base" || a.starts_with("--base=") || a.starts_with("BASE="));
        } else if program == "isuscope" && args.first().is_some_and(|a| a == "analyze")
            && !args.iter().any(|a| a == "-h" || a == "--help")
        {
            found.analyses += 1;
        }
    }

    // --- tool calls ----------------------------------------------------------------------
    fn check(&mut self, payload: &Value) -> Checked {
        let tool = payload.get("tool_name").filter(|v| truthy(v)).map(text_of).unwrap_or_default();
        let tool_input = match payload.get("tool_input") {
            Some(Value::Object(input)) => input.clone(),
            other => Map::from_iter([("value".to_string(), other.cloned().unwrap_or(Value::Null))]),
        };
        let command = command_of(&tool_input);
        self.not_main = not_main(&self.root, payload.get("session_id"));
        if let Some(command) = &command {
            if self.lane.is_some() && command.contains("operation.lock") {
                return Err(Denied(LANE_HINT.to_string()));
            }
            if self.not_main && command.contains("operation.lock") {
                return Err(Denied(MAIN_HINT.to_string()));
            }
            self.check_shell(command, 0)?;
        }
        let searching = tool.to_lowercase().contains("search");
        for (key, value) in &tool_input {
            let values: Vec<&Value> = match value {
                Value::Array(items) => items.iter().collect(),
                single => vec![single],
            };
            for item in values.into_iter().filter_map(Value::as_str) {
                if among(PATH_KEYS, key) {
                    self.check_text_paths(item)?;
                    if item.starts_with("http://") || item.starts_with("https://") || item.starts_with("git@") {
                        self.check_url(item, true)?;
                    }
                }
                if searching && among(QUERY_KEYS, key) && !self.keywords.is_empty() {
                    let lowered = item.to_lowercase();
                    if self.keywords.iter().any(|k| lowered.contains(k.as_str())) {
                        return Err(Denied(format!("現在練習中の開催回の攻略法は検索しない（{item}）。")));
                    }
                }
            }
        }
        Ok(())
    }
}

/// True when a main session is declared (in the root repository) and this call's session is another.
///
/// The session is the hook payload's id, or the agent's own variables (Codex's thread id, which
/// `main-session.py claim --agent codex` records, need not be the id its hooks are given).
fn not_main(root: &Path, session: Option<&Value>) -> bool {
    let mut ids: HashSet<String> = HashSet::new();
    ids.insert(session.filter(|s| truthy(s)).map(text_of).unwrap_or_default());
    for name in ["CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID", "CODEX_THREAD_ID"] {
        if let Ok(value) = std::env::var(name) {
            ids.insert(value);
        }
    }
    ids.remove("");
    if ids.is_empty() {
        return false;
    }
    let common = match common_dir(root) {
        Some(common) => Some(common),
        None => match git_common_dir(root) {
            Ok(common) => common,
            Err(()) => return false,
        },
    };
    let declared_path = match common {
        Some(common) => common.parent().unwrap_or(Path::new("")).join(".local/main-session.json"),
        None => root.join(".local/main-session.json"),
    };
    let declared = load_json(&declared_path).unwrap_or(Value::Null);
    match declared.get("session") {
        Some(Value::String(session)) if !session.is_empty() => !ids.contains(session),
        Some(other) => truthy(other),
        None => false,
    }
}

/// The repository's shared git directory, read from `.git` the way git finds it, without starting
/// git (every tool call pays for this; a process costs about 25 ms on macOS). None when it cannot
/// tell: GIT_DIR is set, or no `.git` holds a plain answer.
fn common_dir(root: &Path) -> Option<PathBuf> {
    if std::env::var_os("GIT_DIR").is_some() || std::env::var_os("GIT_COMMON_DIR").is_some() {
        return None;
    }
    let mut dir = std::fs::canonicalize(root).ok()?;
    loop {
        let dot_git = dir.join(".git");
        if dot_git.is_dir() {
            // The repository's own .git; a commondir inside it means git knows better.
            return if dot_git.join("commondir").exists() { None } else { Some(dot_git) };
        }
        if dot_git.is_file() {
            // A worktree: `gitdir: <its git directory>`, whose commondir names the shared one.
            let text = std::fs::read_to_string(&dot_git).ok()?;
            let gitdir = dir.join(text.trim().strip_prefix("gitdir:")?.trim());
            let common = std::fs::read_to_string(gitdir.join("commondir")).ok()?;
            return std::fs::canonicalize(gitdir.join(common.trim())).ok();
        }
        if !dir.pop() {
            return None;
        }
    }
}

/// `git rev-parse --git-common-dir`: Ok(None) outside a repository, Err when git does not run.
fn git_common_dir(root: &Path) -> Result<Option<PathBuf>, ()> {
    let output = Command::new("git").arg("-C").arg(root)
        .args(["rev-parse", "--path-format=absolute", "--git-common-dir"])
        .stdin(Stdio::null()).stderr(Stdio::null()).output().map_err(|_| ())?;
    let common = String::from_utf8_lossy(&output.stdout).trim().to_string();
    Ok((!common.is_empty()).then(|| PathBuf::from(common)))
}

/// The shell command of a Bash (Claude Code) or shell (Codex) call, as one string.
pub fn command_of(tool_input: &Map<String, Value>) -> Option<String> {
    let command = if tool_input.contains_key("command") { tool_input.get("command") } else { tool_input.get("cmd") };
    match command? {
        Value::String(command) => Some(command.clone()),
        Value::Array(words) => {
            let words: Vec<String> = words.iter().map(text_of).collect();
            if words.len() > 2 && among(SHELLS, basename(&words[0])) && is_match(shell_command_option(), &words[1]) {
                return Some(words[2].clone());
            }
            Some(words.iter().map(|w| shlex::quote(w)).collect::<Vec<_>>().join(" "))
        }
        _ => None,
    }
}

/// The reason the guard refuses this tool call, or None to let it through.
pub fn decide(root: &Path, payload: &Value) -> Option<String> {
    Guard::new(root).check(payload).err().map(|Denied(reason)| reason)
}

/// The benchmarks and analyses a Bash call runs, or None for any other call. The guard's own
/// rules do not apply here; only what runs matters (parsing stops where the guard would refuse).
pub fn find(root: &Path, payload: &Value) -> Option<Found> {
    if payload.get("tool_name").and_then(Value::as_str) != Some("Bash") {
        return None;
    }
    let command = command_of(payload.get("tool_input")?.as_object()?)?;
    let mut finder = Guard::new(root);
    finder.lane = None;
    finder.drop_heredocs = true;
    finder.found = Some(Found::default());
    let _ = finder.check_shell(&command, 0);
    finder.found
}
