#!/usr/bin/env python3
"""Run a fixed sequence of HTTP requests against this worktree's local app (make local-up).

The same flow can run on two versions of the app (before and after a rewrite) so that their
state logs (make local-state-log) can be compared with scripts/state-log-compare.py. It talks
only to a loopback URL, by default the one local-up recorded in .local/local-runtime/status.json.

A flow is a JSON file:

    {"vars": {"name": "flow-user"},
     "steps": [
       {"method": "POST", "path": "/api/initialize", "json": {"payment_server": "http://app:12345"}},
       {"method": "POST", "path": "/api/users", "session": "user", "json": {"name": "{name}"},
        "expect": 201, "save": {"user_id": "id", "token": "header:X-Token"}},
       {"repeat": 3, "steps": [
         {"method": "POST", "path": "/api/orders", "session": "user", "save": {"order": "order.id"}},
         {"method": "GET", "path": "/api/notifications", "session": "user",
          "until": {"contains": "{order}"}, "timeout": 10},
         {"sleep": 0.2}]}]}

- `{var}` in a path, header, body or JSON string is replaced; a string that is only `{var}`
  keeps the saved value's type. `{i}` is the 1-based count inside `repeat`.
- `session` names a cookie jar shared by the steps that use it (Set-Cookie is kept, Cookie sent).
- `expect` is a status or a list of them (default: any 2xx). `save` reads a dotted JSON path
  (list items by index) or `header:Name`.
- `until` repeats the request (or keeps reading a streaming response such as server-sent events)
  until the body contains the text, or the JSON path `path` `equals` the value, within `timeout`
  seconds (default 10), waiting `interval` (default 0.25) between tries.
- `stream: true` accepts a response that is still open when `timeout` ends.

The variables and cookies are saved to .local/local-runtime/flows/<flow name>.json, and
`--continue <flow name>` starts from them, e.g. to finish a flow after restarting the app.

`--parallel N` runs N copies of the flow at once, each with its own variables and cookies, to put
concurrent requests on shared state (with LOCAL_RACE=1 make local-up, then make local-race). `{copy}`
is the copy's number (1..N); use it where copies must not collide (user names, for example).
Top-level steps with `"once": true` (initialize) run once before the copies start. Nothing is saved.
"""
import concurrent.futures
import argparse
import http.cookies
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

REPO = Path(__file__).resolve().parent.parent
STATE = REPO / ".local/local-runtime"
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
VARIABLE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


class FlowError(Exception):
    pass


def base_url(given):
    if given is None:
        status = STATE / "status.json"
        if not status.exists():
            raise FlowError("no local app: run make local-up first (or pass --url)")
        given = json.loads(status.read_text())["url"]
    parts = urllib.parse.urlsplit(given)
    if parts.scheme not in ("http", "https") or parts.hostname not in LOOPBACK:
        raise FlowError(f"local-flow only talks to a loopback URL: {given}")
    return f"{parts.scheme}://{parts.netloc}"


def substitute(value, variables):
    if isinstance(value, str):
        whole = VARIABLE.fullmatch(value)
        if whole:
            return lookup(variables, whole.group(1))
        return VARIABLE.sub(lambda match: str(lookup(variables, match.group(1))), value)
    if isinstance(value, list):
        return [substitute(item, variables) for item in value]
    if isinstance(value, dict):
        return {key: substitute(item, variables) for key, item in value.items()}
    return value


def lookup(variables, name):
    if name not in variables:
        raise FlowError(f"unknown variable {{{name}}}; saved so far: {', '.join(sorted(variables)) or 'none'}")
    return variables[name]


def json_path(document, path):
    value = document
    for part in path.split(".") if path else []:
        if isinstance(value, list) and part.lstrip("-").isdigit():
            value = value[int(part)]
        elif isinstance(value, dict) and part in value:
            value = value[part]
        else:
            raise FlowError(f"no {path!r} in the response")
    return value


def read_body(response, deadline, done):
    """Read until EOF, until done(body) holds, or until the deadline. Returns (body, finished)."""
    body = b""
    while True:
        if done is not None and done(body):
            return body, True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return body, False
        try:
            # The socket timeout is the time left (urlopen), so a quiet stream ends at the deadline.
            chunk = response.read1(65536) if hasattr(response, "read1") else response.read()
        except TimeoutError:
            continue
        if not chunk:
            return body, True
        body += chunk


def request(base, step, variables, sessions, deadline, done):
    method = step.get("method", "GET").upper()
    path = substitute(step["path"], variables)
    headers = {key: str(value) for key, value in substitute(step.get("headers", {}), variables).items()}
    data = None
    if "json" in step:
        data = json.dumps(substitute(step["json"], variables)).encode()
        headers.setdefault("Content-Type", "application/json")
    elif "body" in step:
        data = str(substitute(step["body"], variables)).encode()
    jar = sessions.setdefault(step["session"], {}) if "session" in step else None
    if jar:
        headers["Cookie"] = "; ".join(f"{name}={value}" for name, value in jar.items())
    prepared = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        response = urllib.request.urlopen(prepared, timeout=max(0.1, deadline - time.monotonic()))
    except urllib.error.HTTPError as error:
        response = error
    except (TimeoutError, urllib.error.URLError) as error:
        raise FlowError(f"{method} {path}: no response ({getattr(error, 'reason', error)})")
    if jar is not None:
        for header in response.headers.get_all("Set-Cookie") or []:
            cookie = http.cookies.SimpleCookie()
            cookie.load(header)
            for name, morsel in cookie.items():
                if morsel["max-age"] == "0" or (morsel.value == "" and morsel["expires"]):
                    jar.pop(name, None)
                else:
                    jar[name] = morsel.value
    body, finished = read_body(response, deadline, done)
    response.close()
    return method, path, response.status, response.headers, body, finished


def condition(step, variables):
    until = step.get("until")
    if until is None:
        return None
    if "contains" in until:
        needle = str(substitute(until["contains"], variables)).encode()
        return lambda body: needle in body
    expected = substitute(until["equals"], variables)

    def matches(body):
        try:
            return json_path(json.loads(body), until.get("path", "")) == expected
        except (ValueError, FlowError):
            return False
    return matches


def run_request(base, step, variables, sessions, label):
    timeout = float(step.get("timeout", 10))
    interval = float(step.get("interval", 0.25))
    expect = step.get("expect")
    expected = expect if isinstance(expect, list) else [expect] if expect is not None else None
    done = condition(step, variables)
    started = time.monotonic()
    deadline = started + timeout
    tries = 0
    while True:
        tries += 1
        method, path, status, headers, body, finished = request(base, step, variables, sessions, deadline, done)
        status_ok = status in expected if expected else 200 <= status < 300
        if not finished and not step.get("stream"):
            raise FlowError(f"{label}: {method} {path} did not finish within {timeout}s")
        if done is None or (status_ok and done(body)):
            break
        if time.monotonic() + interval >= deadline:
            raise FlowError(f"{label}: {method} {path} never met {step['until']} in {timeout}s ({tries} tries); "
                            f"last {status}: {body[:300].decode(errors='replace')}")
        time.sleep(interval)
    if not status_ok:
        raise FlowError(f"{label}: {method} {path} returned {status}, expected {expected or '2xx'}: "
                        f"{body[:500].decode(errors='replace')}")
    for name, source in step.get("save", {}).items():
        if source.startswith("header:"):
            value = headers.get(source[len("header:"):])
            if value is None:
                raise FlowError(f"{label}: no {source} in the response")
        else:
            try:
                document = json.loads(body)
            except ValueError:
                raise FlowError(f"{label}: cannot save {name}: the response is not JSON")
            value = json_path(document, source)
        variables[name] = value
    elapsed = (time.monotonic() - started) * 1000
    print(f"ok  {label}  {method} {path}  {status}  {elapsed:.0f}ms" + (f"  ({tries} tries)" if tries > 1 else ""))


def run_steps(base, steps, variables, sessions, prefix):
    for index, step in enumerate(steps, 1):
        label = step.get("name", f"{prefix}{index}")
        if "repeat" in step:
            outer = variables.get("i")
            for count in range(1, int(step["repeat"]) + 1):
                variables["i"] = count
                run_steps(base, step["steps"], variables, sessions, f"{label}.{count}.")
            if outer is None:
                variables.pop("i", None)
            else:
                variables["i"] = outer
        elif "sleep" in step:
            time.sleep(float(step["sleep"]))
        elif "path" in step:
            run_request(base, step, variables, sessions, label)
        else:
            raise FlowError(f"step {label} has none of path, sleep or repeat: {step}")


def run_parallel(base, flow, copies):
    steps = flow["steps"]
    once = [step for step in steps if step.get("once")]
    rest = [step for step in steps if not step.get("once")]
    variables = dict(flow.get("vars", {}))
    if once:
        run_steps(base, once, dict(variables, copy=0), {}, "once.")

    def one(copy):
        run_steps(base, rest, dict(variables, copy=copy), {}, f"#{copy}.")

    failures = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=copies) as pool:
        futures = {pool.submit(one, copy): copy for copy in range(1, copies + 1)}
        for future in concurrent.futures.as_completed(futures):
            try:
                future.result()
            except (FlowError, KeyError, ValueError, OSError) as exc:
                failures.append(f"copy {futures[future]}: {exc}")
    if failures:
        raise FlowError(f"{len(failures)} of {copies} copies failed: " + "; ".join(sorted(failures)))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("flow", type=Path)
    parser.add_argument("--parallel", type=int, metavar="N", help="run N copies at once (see above)")
    parser.add_argument("--continue", dest="previous", metavar="FLOW_NAME",
                        help="start from the variables and cookies a previous flow saved")
    parser.add_argument("--url", help="loopback base URL (default: the one make local-up recorded)")
    args = parser.parse_args()
    if not args.flow.is_file():
        raise FlowError(f"no flow file: {args.flow} (make local-flow FLOW=<path>)")
    flow = json.loads(args.flow.read_text())
    if args.parallel:
        if args.parallel < 1 or args.previous:
            raise FlowError("--parallel needs N >= 1 and cannot continue a saved flow")
        run_parallel(base_url(args.url), flow, args.parallel)
        print(f"flow {args.flow} passed in {args.parallel} parallel copies")
        return
    saved = STATE / "flows"
    variables, sessions = {}, {}
    if args.previous:
        previous = saved / f"{Path(args.previous).stem}.json"
        if not previous.exists():
            raise FlowError(f"nothing saved for {args.previous}: run that flow first")
        record = json.loads(previous.read_text())
        variables, sessions = record["variables"], record["sessions"]
    variables.update({key: value for key, value in flow.get("vars", {}).items() if key not in variables})
    base = base_url(args.url)
    try:
        run_steps(base, flow["steps"], variables, sessions, "")
    finally:
        saved.mkdir(parents=True, exist_ok=True)
        (saved / f"{args.flow.stem}.json").write_text(
            json.dumps({"variables": variables, "sessions": sessions}, ensure_ascii=False, indent=2) + "\n")
    print(f"flow {args.flow} passed")


if __name__ == "__main__":
    try:
        main()
    except (FlowError, KeyError, ValueError, OSError) as exc:
        sys.exit(f"local-flow: {exc}")
