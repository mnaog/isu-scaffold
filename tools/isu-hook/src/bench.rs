//! The benchmark hook: a benchmark starts only in the background, and what follows it and
//! `isuscope analyze` (the waiting context, the proposals' decisions) comes from scripts/bench-hook.py.
//!
//! Heredoc bodies and `bash -n` syntax checks do not count as starting a benchmark (isuscope run,
//! make first-bench / bench, scripts/first-bench.sh / bench.sh).

use crate::guard::{self, text_of, truthy};
use serde_json::Value;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

const BACKGROUND_HINT: &str = "ベンチ（isuscope run / make first-bench）はBashのrun_in_background: trueで起動する。\
    完了の通知を待つ間に、次の候補を`make build`しておく。sleepやpollingで待たない。";

fn in_background(payload: &Value) -> bool {
    payload.pointer("/tool_input/run_in_background") == Some(&Value::Bool(true))
}

/// PreToolUse: the reason to refuse a benchmark started in the foreground.
pub fn pre(root: &Path, payload: &Value) -> Option<String> {
    let found = guard::find(root, payload)?;
    (found.benchmarks > 0 && !in_background(payload)).then(|| BACKGROUND_HINT.to_string())
}

/// PostToolUse: the context after a benchmark started in the background, or after `isuscope analyze`.
pub fn post(root: &Path, payload: &Value) -> Option<String> {
    let found = guard::find(root, payload)?;
    let session = payload.get("session_id").filter(|s| truthy(s)).map(text_of).unwrap_or_default();
    if found.benchmarks == 0 {
        return (found.analyses > 0).then(|| actions(root, &["analyzed", "--session", &session])).flatten();
    }
    if !in_background(payload) {
        return None;
    }
    let mut args = vec!["started", "--session", session.as_str()];
    if !found.based {
        args.push("--unbased");
    }
    actions(root, &args)
}

/// What scripts/bench-hook.py prints for the session, or None when it prints nothing.
fn actions(root: &Path, args: &[&str]) -> Option<String> {
    let scripts = std::env::var_os("ISU_HOOK_SCRIPTS").map(PathBuf::from).unwrap_or_else(|| root.join("scripts"));
    let output = Command::new("python3").arg(scripts.join("bench-hook.py")).args(args)
        .env("TOOL_GUARD_ROOT", root).stdin(Stdio::null()).stderr(Stdio::inherit()).output().ok()?;
    let context = String::from_utf8_lossy(&output.stdout).trim_end_matches('\n').to_string();
    (!context.is_empty()).then_some(context)
}
