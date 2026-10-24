//! Claude Code and Codex hooks for this repository. scripts/hook.sh runs this; `make hook` builds it.
//!
//!   isu-hook guard   PreToolUse: refuse what the rules forbid (Codex runs only this)
//!   isu-hook pre     PreToolUse: the guard, then a benchmark started in the foreground
//!   isu-hook post    PostToolUse: after a benchmark started in the background, or `isuscope analyze`
//!   isu-hook find    for tests: what the benchmark hook sees in a Bash call, as JSON
//!   isu-hook lex     for tests: stdin split into shell tokens, as JSON
//!
//! The tool call's JSON comes on stdin. A refused call prints a PreToolUse decision with the reason;
//! anything else prints nothing and lets the call through, and so does an internal error, so a broken
//! hook never stops all work. TOOL_GUARD_ROOT names the repository (scripts/hook.sh sets it).

mod bench;
mod guard;
mod shlex;

use serde_json::{json, Value};
use std::io::Read;
use std::path::PathBuf;

fn deny(reason: String) -> Value {
    json!({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }})
}

fn main() {
    let mode = std::env::args().nth(1).unwrap_or_default();
    let mut input = String::new();
    let _ = std::io::stdin().read_to_string(&mut input);
    if mode == "lex" {
        let tokens = shlex::split(&input, " \t\r", ";&|()\n").map_err(|error| format!("{error:?}"));
        println!("{}", json!(tokens.map_or_else(|error| json!({"error": error}), |tokens| json!(tokens))));
        return;
    }
    let payload = serde_json::from_str::<Value>(&input).ok().filter(Value::is_object).unwrap_or_else(|| json!({}));
    let root = std::env::var_os("TOOL_GUARD_ROOT").filter(|r| !r.is_empty()).map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."));
    let output = match mode.as_str() {
        "guard" => guard::decide(&root, &payload).map(deny),
        "pre" => guard::decide(&root, &payload).or_else(|| bench::pre(&root, &payload)).map(deny),
        "post" => bench::post(&root, &payload).map(|context| json!({"hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": context,
        }})),
        "find" => Some(guard::find(&root, &payload).map_or(Value::Null, |found| json!({
            "benchmarks": found.benchmarks, "analyses": found.analyses, "based": found.based,
        }))),
        _ => {
            eprintln!("usage: isu-hook guard|pre|post  (the tool call's JSON on stdin)");
            std::process::exit(2);
        }
    };
    if let Some(output) = output {
        println!("{output}");
    }
}
