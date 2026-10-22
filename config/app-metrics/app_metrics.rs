//! isuscopeのアプリ計測の書き手。
//!
//! HTTPとSQLの記録からは見えない、アプリの中の待ちを1秒ごとに集計して書く。isuscopeの
//! `app-metrics` collectorがベンチの前後の差分を運び、`isuscope brief`の`app_calls`・`app_locks`欄と
//! `isuscope query --metric-prefix app. --scope series --window load`で読む。
//!
//! - **外への呼び出し**（決済などの外部API、別nodeのサービス）: 試行ごとに`call`で記録する。
//!   失敗して再試行した分も1回として数えるので、失敗率と再試行の回数がそのまま出る。
//! - **lock**: `Lock`を`static`に置き、lockを取る式を`acquire`（async版は`acquire_async`）で包む。
//!   取れるまで待った時間と、持っていた時間（guardを落とすまで）を数える。
//!
//! - リクエストを待たせない。lockの数はatomicに足すだけ、呼び出しは小さなmapへ足すだけで、
//!   別のthreadが1秒ごとに取り出して書く。
//! - 書き先は環境変数`ISUSCOPE_APP_METRICS`（無ければ`/var/tmp/isuscope-app-metrics.jsonl`）。
//!   空にすると何も数えず書かない（最終計測の前に止めるとき）。
//! - 追加のcrateは要らない（tokioにも依存しない）。このfileをcrateへそのまま置き、
//!   `mod app_metrics;`で使う。中身は変えない。
//!
//! ```ignore
//! // 外への呼び出し。outcomeはstatus code、または"error"・"timeout"など。
//! let started = std::time::Instant::now();
//! let response = client.post(url).json(&body).send().await;
//! let outcome = match &response {
//!     Ok(response) => response.status().as_u16().to_string(),
//!     Err(error) if error.is_timeout() => "timeout".into(),
//!     Err(_) => "error".into(),
//! };
//! app_metrics::call("payment", outcome, started.elapsed());
//!
//! // lock。std::sync::Mutex・RwLockでも、tokioのMutex・RwLockでも同じ形で包む。
//! static STORE_LOCK: app_metrics::Lock = app_metrics::Lock::new("store");
//! let mut store = STORE_LOCK.acquire(|| self.store.lock().unwrap());
//! let mut store = STORE_LOCK.acquire_async(self.store.lock()).await;
//! store.rides.insert(id, ride); // guardの中身へはそのまま触れる（Deref）
//! drop(store);                  // 持っていた時間はここまで
//! ```
//!
//! 1行の形（isuscopeとの約束、collectorの標準の形）: `{"type":"metric","name":"app.call.count",
//! "value":12,"unit":"calls","labels":{"target":"payment","outcome":"502"},"timestamp":<Unix秒>}`。
//! 名前は`app.call.{count,duration_sum,duration_max}`（label `target`・`outcome`）と
//! `app.lock.{count,wait_sum,wait_max,hold_sum,hold_max}`（label `lock`）。時間はms。
//! 1秒の間に1回も無かった対象は書かない。
#![allow(dead_code)]

use std::collections::HashMap;
use std::fmt::{Display, Write as _};
use std::fs::OpenOptions;
use std::future::Future;
use std::io::Write as _;
use std::ops::{Deref, DerefMut};
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::thread::JoinHandle;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

/// isuscopeのcollectorが既定で読むpath。
pub const DEFAULT_PATH: &str = "/var/tmp/isuscope-app-metrics.jsonl";
/// 書き先を変える、または空にして止める環境変数。
pub const PATH_VARIABLE: &str = "ISUSCOPE_APP_METRICS";

const FLUSH_EVERY: Duration = Duration::from_secs(1);

/// 外への呼び出しを1回（1試行）記録する。`target`は呼び先の名前（"payment"など、決まった値）、
/// `outcome`はstatus codeか"error"・"timeout"など。どちらも種類を少なく保つ（IDを入れない）。
pub fn call(target: &str, outcome: impl Display, elapsed: Duration) {
    if let Some(sink) = global() {
        sink.call(target, &outcome.to_string(), elapsed);
    }
}

/// 待ちと保持の時間を数えるlockの名前。`static`に置いて使う。
pub struct Lock {
    name: &'static str,
    registered: AtomicBool,
    count: AtomicU64,
    wait_us: AtomicU64,
    wait_max_us: AtomicU64,
    hold_us: AtomicU64,
    hold_max_us: AtomicU64,
}

impl Lock {
    pub const fn new(name: &'static str) -> Self {
        Self {
            name,
            registered: AtomicBool::new(false),
            count: AtomicU64::new(0),
            wait_us: AtomicU64::new(0),
            wait_max_us: AtomicU64::new(0),
            hold_us: AtomicU64::new(0),
            hold_max_us: AtomicU64::new(0),
        }
    }

    /// `lock`（lockを取る式）を呼び、取れるまでの時間を数える。返したguardを落とすまでが保持。
    pub fn acquire<G>(&'static self, lock: impl FnOnce() -> G) -> Held<G> {
        let Some(sink) = global() else {
            return Held::untimed(lock());
        };
        let started = Instant::now();
        let guard = lock();
        let acquired = Instant::now();
        self.waited(sink, acquired - started);
        Held::timed(guard, self, acquired)
    }

    /// async版。`lock`はlockを取るfuture（`mutex.lock()`など）。
    pub async fn acquire_async<G>(&'static self, lock: impl Future<Output = G>) -> Held<G> {
        let Some(sink) = global() else {
            return Held::untimed(lock.await);
        };
        let started = Instant::now();
        let guard = lock.await;
        let acquired = Instant::now();
        self.waited(sink, acquired - started);
        Held::timed(guard, self, acquired)
    }

    fn waited(&'static self, sink: &Sink, wait: Duration) {
        if !self.registered.swap(true, Ordering::Relaxed) {
            sink.register(self);
        }
        let wait = micros(wait);
        self.count.fetch_add(1, Ordering::Relaxed);
        self.wait_us.fetch_add(wait, Ordering::Relaxed);
        self.wait_max_us.fetch_max(wait, Ordering::Relaxed);
    }

    fn held(&self, hold: Duration) {
        let hold = micros(hold);
        self.hold_us.fetch_add(hold, Ordering::Relaxed);
        self.hold_max_us.fetch_max(hold, Ordering::Relaxed);
    }

    fn take(&self) -> Option<[u64; 5]> {
        let count = self.count.swap(0, Ordering::Relaxed);
        let values = [
            count,
            self.wait_us.swap(0, Ordering::Relaxed),
            self.wait_max_us.swap(0, Ordering::Relaxed),
            self.hold_us.swap(0, Ordering::Relaxed),
            self.hold_max_us.swap(0, Ordering::Relaxed),
        ];
        (values.iter().any(|value| *value > 0)).then_some(values)
    }
}

/// lockのguard。中身へは`Deref`・`DerefMut`で触れる。guard自体が要る（`Condvar::wait`へ渡すなど）
/// ときは`guard_mut`。落とすと、guardを先に落としてから保持の時間を数える。
pub struct Held<G> {
    guard: Option<G>,
    timing: Option<(&'static Lock, Instant)>,
}

impl<G> Held<G> {
    fn timed(guard: G, lock: &'static Lock, since: Instant) -> Self {
        Self {
            guard: Some(guard),
            timing: Some((lock, since)),
        }
    }

    fn untimed(guard: G) -> Self {
        Self {
            guard: Some(guard),
            timing: None,
        }
    }

    pub fn guard(&self) -> &G {
        self.guard.as_ref().expect("guard is present until drop")
    }

    pub fn guard_mut(&mut self) -> &mut G {
        self.guard.as_mut().expect("guard is present until drop")
    }
}

impl<G: Deref> Deref for Held<G> {
    type Target = G::Target;
    fn deref(&self) -> &Self::Target {
        self.guard()
    }
}

impl<G: DerefMut> DerefMut for Held<G> {
    fn deref_mut(&mut self) -> &mut Self::Target {
        self.guard_mut()
    }
}

impl<G> Drop for Held<G> {
    fn drop(&mut self) {
        drop(self.guard.take());
        if let Some((lock, since)) = self.timing.take() {
            lock.held(since.elapsed());
        }
    }
}

#[derive(Debug, Default, Clone, Copy, PartialEq)]
struct CallStats {
    count: u64,
    total_us: u64,
    max_us: u64,
}

fn micros(duration: Duration) -> u64 {
    u64::try_from(duration.as_micros()).unwrap_or(u64::MAX)
}

fn now_seconds() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|elapsed| elapsed.as_secs_f64())
        .unwrap_or(0.0)
}

/// 環境変数の値から書き先を決める。空なら書かない。
fn path_from(value: Option<std::ffi::OsString>) -> Option<PathBuf> {
    match value {
        Some(value) if value.is_empty() => None,
        Some(value) => Some(PathBuf::from(value)),
        None => Some(PathBuf::from(DEFAULT_PATH)),
    }
}

fn global() -> Option<&'static Sink> {
    static SINK: OnceLock<Option<Arc<Sink>>> = OnceLock::new();
    SINK.get_or_init(|| path_from(std::env::var_os(PATH_VARIABLE)).map(Sink::start))
        .as_deref()
        .filter(|sink| !sink.closed.load(Ordering::Relaxed))
}

struct Sink {
    locks: Mutex<Vec<&'static Lock>>,
    calls: Mutex<HashMap<(String, String), CallStats>>,
    closed: AtomicBool,
    stop: AtomicBool,
    thread: Mutex<Option<JoinHandle<()>>>,
}

impl Sink {
    fn new() -> Self {
        Self {
            locks: Mutex::new(Vec::new()),
            calls: Mutex::new(HashMap::new()),
            closed: AtomicBool::new(false),
            stop: AtomicBool::new(false),
            thread: Mutex::new(None),
        }
    }

    fn start(path: PathBuf) -> Arc<Self> {
        let sink = Arc::new(Self::new());
        let thread = {
            let sink = sink.clone();
            std::thread::Builder::new()
                .name("app-metrics".into())
                .spawn(move || sink.run(path))
                .ok()
        };
        match thread {
            Some(thread) => *sink.thread.lock().unwrap_or_else(|e| e.into_inner()) = Some(thread),
            None => sink.closed.store(true, Ordering::Relaxed),
        }
        sink
    }

    fn register(&self, lock: &'static Lock) {
        self.locks
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .push(lock);
    }

    fn call(&self, target: &str, outcome: &str, elapsed: Duration) {
        let elapsed = micros(elapsed);
        let mut calls = self.calls.lock().unwrap_or_else(|e| e.into_inner());
        let stats = match calls.get_mut(&(target.to_owned(), outcome.to_owned())) {
            Some(stats) => stats,
            None => calls
                .entry((target.to_owned(), outcome.to_owned()))
                .or_default(),
        };
        stats.count += 1;
        stats.total_us = stats.total_us.saturating_add(elapsed);
        stats.max_us = stats.max_us.max(elapsed);
    }

    /// この1秒の値を行にして返し、数を0へ戻す。
    fn drain(&self, timestamp: f64, out: &mut String) {
        let calls = std::mem::take(&mut *self.calls.lock().unwrap_or_else(|e| e.into_inner()));
        let mut calls = calls.into_iter().collect::<Vec<_>>();
        calls.sort_by(|a, b| a.0.cmp(&b.0));
        for ((target, outcome), stats) in calls {
            let labels = [("target", target.as_str()), ("outcome", outcome.as_str())];
            metric(out, "app.call.count", stats.count as f64, "calls", &labels, timestamp);
            metric(out, "app.call.duration_sum", ms(stats.total_us), "ms", &labels, timestamp);
            metric(out, "app.call.duration_max", ms(stats.max_us), "ms", &labels, timestamp);
        }
        let locks = self.locks.lock().unwrap_or_else(|e| e.into_inner()).clone();
        for lock in locks {
            let Some([count, wait, wait_max, hold, hold_max]) = lock.take() else {
                continue;
            };
            let labels = [("lock", lock.name)];
            metric(out, "app.lock.count", count as f64, "acquisitions", &labels, timestamp);
            metric(out, "app.lock.wait_sum", ms(wait), "ms", &labels, timestamp);
            metric(out, "app.lock.wait_max", ms(wait_max), "ms", &labels, timestamp);
            metric(out, "app.lock.hold_sum", ms(hold), "ms", &labels, timestamp);
            metric(out, "app.lock.hold_max", ms(hold_max), "ms", &labels, timestamp);
        }
    }

    fn run(&self, path: PathBuf) {
        let mut file = match OpenOptions::new().create(true).append(true).open(&path) {
            Ok(file) => file,
            Err(error) => {
                eprintln!(
                    "app_metrics: cannot open {}: {error}; app metrics are not written",
                    path.display()
                );
                self.closed.store(true, Ordering::Relaxed);
                return;
            }
        };
        let mut buffer = String::new();
        let mut reported = false;
        loop {
            // 止める指示を待つ間も1秒ごとに書く。止めるときは最後の分を書いてから終わる。
            let deadline = Instant::now() + FLUSH_EVERY;
            while Instant::now() < deadline && !self.stop.load(Ordering::Relaxed) {
                std::thread::sleep(Duration::from_millis(20).min(deadline - Instant::now()));
            }
            self.drain(now_seconds(), &mut buffer);
            if !buffer.is_empty() {
                if let Err(error) = file.write_all(buffer.as_bytes()) {
                    if !reported {
                        eprintln!("app_metrics: cannot write {}: {error}", path.display());
                        reported = true;
                    }
                }
                buffer.clear();
            }
            if self.stop.load(Ordering::Relaxed) {
                return;
            }
        }
    }

    /// 残りを書いてthreadを止める（testで使う）。
    fn close(&self) {
        self.stop.store(true, Ordering::Relaxed);
        if let Some(thread) = self
            .thread
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .take()
        {
            let _ = thread.join();
        }
    }
}

fn ms(micros: u64) -> f64 {
    micros as f64 / 1000.0
}

fn metric(
    out: &mut String,
    name: &str,
    value: f64,
    unit: &str,
    labels: &[(&str, &str)],
    timestamp: f64,
) {
    let _ = write!(out, "{{\"type\":\"metric\",\"name\":\"{name}\",\"value\":{value},\"unit\":\"{unit}\",\"labels\":{{");
    for (index, (key, value)) in labels.iter().enumerate() {
        if index > 0 {
            out.push(',');
        }
        json_string(out, key);
        out.push(':');
        json_string(out, value);
    }
    let _ = writeln!(out, "}},\"timestamp\":{timestamp:.3}}}");
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn calls_are_summed_per_target_and_outcome_and_reset_each_second() {
        let sink = Sink::new();
        sink.call("payment", "502", Duration::from_micros(1500));
        sink.call("payment", "502", Duration::from_micros(500));
        sink.call("payment", "204", Duration::from_micros(300));
        let mut out = String::new();
        sink.drain(1_700_000_000.25, &mut out);
        assert!(out.contains("{\"type\":\"metric\",\"name\":\"app.call.count\",\"value\":2,\"unit\":\"calls\",\"labels\":{\"target\":\"payment\",\"outcome\":\"502\"},\"timestamp\":1700000000.250}\n"), "{out}");
        assert!(out.contains("\"name\":\"app.call.duration_sum\",\"value\":2,\"unit\":\"ms\",\"labels\":{\"target\":\"payment\",\"outcome\":\"502\"}"), "{out}");
        assert!(out.contains("\"name\":\"app.call.duration_max\",\"value\":1.5,"), "{out}");
        assert!(out.contains("\"outcome\":\"204\""), "{out}");
        let mut next = String::new();
        sink.drain(1_700_000_001.0, &mut next);
        assert!(next.is_empty(), "{next}");
    }

    static TEST_LOCK: Lock = Lock::new("te\"st");

    #[test]
    fn a_lock_counts_waiting_and_holding_and_only_seconds_with_use_are_written() {
        let sink = Sink::new();
        let mutex = Mutex::new(0_u32);
        for _ in 0..3 {
            let started = Instant::now();
            let guard = mutex.lock().unwrap();
            let acquired = Instant::now();
            TEST_LOCK.waited(&sink, acquired - started);
            let mut held = Held::timed(guard, &TEST_LOCK, acquired);
            *held += 1;
            std::thread::sleep(Duration::from_millis(2));
        }
        assert_eq!(*mutex.lock().unwrap(), 3);
        let mut out = String::new();
        sink.drain(1.0, &mut out);
        assert!(out.contains("\"name\":\"app.lock.count\",\"value\":3,\"unit\":\"acquisitions\",\"labels\":{\"lock\":\"te\\\"st\"}"), "{out}");
        let hold = out
            .lines()
            .find(|line| line.contains("app.lock.hold_sum"))
            .and_then(|line| line.split("\"value\":").nth(1))
            .and_then(|rest| rest.split(',').next())
            .and_then(|value| value.parse::<f64>().ok())
            .unwrap();
        assert!(hold >= 6.0, "{out}");
        let mut idle = String::new();
        sink.drain(2.0, &mut idle);
        assert!(idle.is_empty(), "{idle}");
    }

    #[test]
    fn async_acquire_waits_on_the_future_without_a_runtime_crate() {
        // 何もしないwakerで、すぐ準備できるfutureを1回だけpollする。
        use std::task::{Context, Poll, RawWaker, RawWakerVTable, Waker};
        fn noop(_: *const ()) {}
        fn clone(data: *const ()) -> RawWaker {
            RawWaker::new(data, &VTABLE)
        }
        static VTABLE: RawWakerVTable = RawWakerVTable::new(clone, noop, noop, noop);
        static ASYNC_LOCK: Lock = Lock::new("async");
        let waker = unsafe { Waker::from_raw(RawWaker::new(std::ptr::null(), &VTABLE)) };
        let mut future = std::pin::pin!(ASYNC_LOCK.acquire_async(async { 7_u8 }));
        let Poll::Ready(held) = future.as_mut().poll(&mut Context::from_waker(&waker)) else {
            panic!("the future was ready");
        };
        assert_eq!(*held.guard(), 7);
    }

    #[test]
    fn the_writer_thread_appends_lines_and_stops_after_the_last_second() {
        let directory = std::env::temp_dir().join(format!(
            "app-metrics-{}-{}",
            std::process::id(),
            now_seconds()
        ));
        std::fs::create_dir_all(&directory).unwrap();
        let path = directory.join("app.jsonl");
        let sink = Sink::start(path.clone());
        sink.call("payment", "timeout", Duration::from_millis(3));
        sink.close();
        let written = std::fs::read_to_string(&path).unwrap();
        assert_eq!(written.lines().count(), 3, "{written}");
        assert!(written.lines().all(|line| line.starts_with("{\"type\":\"metric\"")));
    }

    #[test]
    fn an_empty_variable_turns_the_metrics_off() {
        assert_eq!(path_from(Some("".into())), None);
        assert_eq!(path_from(None), Some(PathBuf::from(DEFAULT_PATH)));
    }
}
