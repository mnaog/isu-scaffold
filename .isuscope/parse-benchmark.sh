#!/usr/bin/env bash
set -euo pipefail

# 問題固有のbenchmark出力を、isuscopeの汎用metricとmessageのJSONLへ変換します。
# このscriptはベンチ終了後にだけ実行されるため、採点中の負荷にはなりません。
# 第一引数はbenchmark stdoutのzstd圧縮logです。
#
# 出力例:
#   printf '%s\n' \
#     '{"type":"metric","name":"benchmark.scenario.success","value":123,"unit":"runs","labels":{"scenario":"viewer"}}'
#
# FAILの理由とエラー・警告の実例は、行の本文をmessageとして残します（件数はmetricで出します）。
# FAIL runは分析不要なので、ここで残した理由が`list`と`brief`での唯一の記録になります。
# benchmarkerが運営向けに出す行（ISUCON12の`[ADMIN]`など）はルール側の情報なので使いません。
#   printf '%s\n' \
#     '{"type":"message","kind":"failure","text":"整合性チェックに失敗: GET /home expected(403) != actual(401)"}' \
#     '{"type":"message","kind":"error","category":"timeout","text":"Get \"http://10.0.0.1/home\": dial tcp 10.0.0.1:80: i/o timeout"}' \
#     '{"type":"message","kind":"warning","category":"CODE=30","text":"... (CODE=30)"}'
#
# 既定の実装（当日に何も書かなくても動く部分）:
# - WARN・ERRORの行を、levelとcategoryごとに`benchmark.warnings`・`benchmark.errors`（label
#   `category`）と合計の`benchmark.warnings_total`・`benchmark.errors_total`で数える。警告が上限の
#   件数に達するとFAILにするベンチがあるので、警告もcategoryごとに数え、実例をmessageに残す。
# - categoryは`CODE=<n>`のような番号があればそれ、無ければtimeout・connection・statusの語、
#   どれでも無ければ`other`。当日の出力に合わせて`category()`を直す。
# - クリティカル・FAILの行をfailureとして残す。
# 問題固有の内訳（シナリオごとの成功数、利用者の登録・離脱、不満の割合など）は、下の
# `--- 当日に実装する ---`の範囲で`metric()`を足す。briefの`benchmark`欄とboardの
# 「ベンチの内訳」に、比較元との差分つきで並ぶ。
#
# 当日は、まず何も解析せずベンチを保存し、このscriptを編集してから次を実行できます。
#   isuscope enrich RUN_ID

input=${1:?benchmark stdout path is required}

zstd -dc -- "$input" | python3 -I -c '
import json, re, sys
from collections import Counter

def metric(name, value, unit, **labels):
    print(json.dumps({"type": "metric", "name": name, "value": value, "unit": unit, "labels": labels}, ensure_ascii=False))

def message(kind, text, category=None):
    row = {"type": "message", "kind": kind, "text": text[:1000]}
    if category:
        row["category"] = category
    print(json.dumps(row, ensure_ascii=False))

LEVEL = re.compile(r"\blevel=\"?(\w+)|\[(ERROR|ERR|WARN|WARNING|CRITICAL|FATAL)\]|^(ERROR|WARN|WARNING|CRITICAL|FATAL)\b", re.I)
CRITICAL = re.compile(r"(?i)\bcritical\b|クリティカル|整合性|\bfatal\b|\bpass(ed)?=false\b")
WARNINGS = {"WARN", "WARNING"}
ERRORS = {"ERROR", "ERR", "CRITICAL", "FATAL"}

def level(line):
    m = LEVEL.search(line)
    if not m:
        return None
    return next(group for group in m.groups() if group).upper()

def category(line):
    m = re.search(r"\b(CODE|code|ERR|E)[=:]\s*([A-Za-z0-9_-]+)", line)
    if m:
        return f"{m[1].upper()}={m[2]}"
    lowered = line.lower()
    if "timeout" in lowered or "deadline exceeded" in lowered or "timed out" in lowered:
        return "timeout"
    if "connection refused" in lowered or "connection reset" in lowered or "eof" in lowered:
        return "connection"
    if re.search(r"\bstatus\b|expected", lowered):
        return "status"
    return "other"

warnings = Counter()
errors = Counter()
failures = 0
for raw in sys.stdin:
    line = raw.rstrip("\n")
    if "[ADMIN]" in line:
        continue
    lv = level(line)
    if lv in WARNINGS:
        c = category(line)
        warnings[c] += 1
        message("warning", line, c)
    elif lv in ERRORS:
        c = category(line)
        errors[c] += 1
        message("error", line, c)
    if CRITICAL.search(line) and failures < 10:
        failures += 1
        message("failure", line)

    # --- 当日に実装する（問題固有の内訳）---
    # m = re.search(r"...(\d+)人が新規登録", line)
    # if m:
    #     metric("benchmark.users.registered", int(m[1]), "users")
    # --- ここまで ---

metric("benchmark.warnings_total", sum(warnings.values()), "lines")
metric("benchmark.errors_total", sum(errors.values()), "lines")
for c, n in warnings.items():
    metric("benchmark.warnings", n, "lines", category=c)
for c, n in errors.items():
    metric("benchmark.errors", n, "lines", category=c)
'
