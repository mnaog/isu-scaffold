"""scripts/local-flow.py: a fixed flow against a local HTTP app, with cookies, saved values,
waiting on a streaming response, and continuing after a restart."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parent.parent


class App(BaseHTTPRequestHandler):
    orders = {}
    polls = 0

    def log_message(self, *args):
        pass

    def reply(self, status, document, headers=()):
        body = json.dumps(document).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def user(self):
        cookie = self.headers.get("Cookie", "")
        return cookie.split("session=")[1].split(";")[0] if "session=" in cookie else None

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/users":
            self.reply(201, {"user": {"id": "u-" + payload["name"]}}, [("Set-Cookie", "session=s1; Path=/"),
                                                                          ("X-Token", "t-" + payload["name"])])
        elif self.path == "/orders":
            if self.user() != "s1":
                return self.reply(401, {"message": "no session"})
            order = f"o{len(App.orders) + 1}"
            App.orders[order] = time.monotonic()
            self.reply(200, {"order": {"id": order, "items": [payload.get("item")]}})
        else:
            self.reply(404, {})

    def do_GET(self):
        if self.path == "/count":
            App.polls += 1
            self.reply(200, {"count": App.polls})
        elif self.path.startswith("/events"):
            # Server-sent events: the order appears 0.3s after it was made, and the stream stays open.
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b"data: waiting\n\n")
            self.wfile.flush()
            time.sleep(0.3)
            for order in App.orders:
                self.wfile.write(f"data: {order}\n\n".encode())
            self.wfile.flush()
            time.sleep(2)
        elif self.path.startswith("/orders/"):
            order = self.path.rsplit("/", 1)[1]
            self.reply(200 if order in App.orders and self.user() == "s1" else 404, {"id": order})
        else:
            self.reply(404, {})


class LocalFlowTest(unittest.TestCase):
    def setUp(self):
        App.orders, App.polls = {}, 0
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), App)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.tmp = Path(tempfile.mkdtemp())
        # A copy of the script keeps the saved state in the temporary repo, not in this checkout.
        (self.tmp / "scripts").mkdir()
        shutil.copy2(ROOT / "scripts/local-flow.py", self.tmp / "scripts/local-flow.py")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.tmp)

    def run_flow(self, name, flow, *extra):
        path = self.tmp / f"{name}.json"
        path.write_text(json.dumps(flow))
        return subprocess.run(["python3", str(self.tmp / "scripts/local-flow.py"), str(path), "--url", self.url, *extra],
                              capture_output=True, text=True, timeout=60)

    def test_a_flow_keeps_cookies_saves_values_and_waits_for_a_stream(self):
        result = self.run_flow("orders", {"vars": {"name": "ann"}, "steps": [
            {"method": "POST", "path": "/users", "session": "user", "json": {"name": "{name}"}, "expect": 201,
             "save": {"user": "user.id", "token": "header:X-Token"}},
            {"repeat": 2, "steps": [
                {"method": "POST", "path": "/orders", "session": "user", "json": {"item": "{i}"},
                 "save": {"order": "order.id", "item": "order.items.0"}},
                {"method": "GET", "path": "/events", "until": {"contains": "data: {order}"}, "timeout": 5}]},
            {"method": "GET", "path": "/count", "until": {"path": "count", "equals": 3}, "interval": 0.01},
            {"sleep": 0.01}]})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("flow", result.stdout)
        saved = json.loads((self.tmp / ".local/local-runtime/flows/orders.json").read_text())
        self.assertEqual(saved["variables"]["user"], "u-ann")
        self.assertEqual(saved["variables"]["token"], "t-ann")
        self.assertEqual(saved["variables"]["order"], "o2")
        self.assertEqual(saved["variables"]["item"], 2, "a string that is only {i} keeps the number")
        self.assertEqual(saved["sessions"], {"user": {"session": "s1"}})
        self.assertIn("(3 tries)", result.stdout)

    def test_continue_starts_from_the_saved_variables_and_cookies(self):
        first = self.run_flow("before", {"steps": [
            {"method": "POST", "path": "/users", "session": "user", "json": {"name": "bob"}, "expect": 201},
            {"method": "POST", "path": "/orders", "session": "user", "save": {"order": "order.id"}}]})
        self.assertEqual(first.returncode, 0, first.stderr)
        after = {"steps": [{"method": "GET", "path": "/orders/{order}", "session": "user"}]}
        again = self.run_flow("after", after, "--continue", "before")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        fresh = self.run_flow("fresh", after)
        self.assertNotEqual(fresh.returncode, 0)
        self.assertIn("unknown variable {order}", fresh.stderr)

    def test_an_unexpected_status_stops_the_flow_with_the_body(self):
        result = self.run_flow("denied", {"steps": [
            {"method": "POST", "path": "/orders", "json": {}},
            {"method": "GET", "path": "/count"}]})
        self.assertEqual(result.returncode, 1)
        self.assertIn("returned 401", result.stderr)
        self.assertIn("no session", result.stderr)
        self.assertEqual(App.polls, 0, "the flow stops at the failed step")

    def test_waiting_gives_up_after_the_timeout(self):
        result = self.run_flow("stuck", {"steps": [
            {"method": "GET", "path": "/count", "until": {"path": "count", "equals": -1}, "timeout": 0.3}]})
        self.assertEqual(result.returncode, 1)
        self.assertIn("never met", result.stderr)

    def test_only_loopback_urls_are_used(self):
        path = self.tmp / "remote.json"
        path.write_text(json.dumps({"steps": [{"method": "GET", "path": "/"}]}))
        result = subprocess.run(["python3", str(self.tmp / "scripts/local-flow.py"), str(path),
                                 "--url", "http://203.0.113.10:8080"], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 1)
        self.assertIn("loopback", result.stderr)

    def test_the_url_comes_from_local_up(self):
        status = self.tmp / ".local/local-runtime/status.json"
        status.parent.mkdir(parents=True)
        status.write_text(json.dumps({"url": self.url + "/health"}))
        path = self.tmp / "count.json"
        path.write_text(json.dumps({"steps": [{"method": "GET", "path": "/count"}]}))
        result = subprocess.run(["python3", str(self.tmp / "scripts/local-flow.py"), str(path)],
                                capture_output=True, text=True, timeout=30, env=dict(os.environ))
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
