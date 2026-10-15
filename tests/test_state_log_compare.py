"""scripts/state-log-compare.py: two state logs from the same flow agree when every kind has
the same number of objects through each sequence of states, whatever their IDs."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent


def line(t, kind, id, to, **extra):
    return json.dumps({"t": t, "seq": t, "kind": kind, "id": id, "to": to, "attrs": {}, **extra})


class StateLogCompareTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def compare(self, before, after, *extra):
        for name, lines in (("before", before), ("after", after)):
            (self.dir / f"{name}.jsonl").write_text("\n".join(lines) + "\n")
        return subprocess.run(["python3", str(ROOT / "scripts/state-log-compare.py"), str(self.dir / "before.jsonl"),
                               str(self.dir / "after.jsonl"), *extra], capture_output=True, text=True, timeout=30)

    def test_logs_agree_whatever_the_ids_and_the_from_field(self):
        # The old version did not know the previous state (no "from"); the rewrite does.
        before = [line(1, "order", "a", "NEW", **{"from": None}), line(3, "order", "a", "PAID"),
                  line(2, "order", "b", "NEW", **{"from": None}), line(4, "order", "b", "PAID"),
                  json.dumps({"type": "metric", "name": "x", "value": 1})]
        after = [line(10, "order", "x", "NEW", **{"from": None}), line(11, "order", "y", "NEW", **{"from": None}),
                 line(12, "order", "y", "PAID", **{"from": "NEW"}), line(13, "order", "x", "PAID", **{"from": "NEW"})]
        result = self.compare(before, after)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("order: same (2 objects, 1 sequences)", result.stdout)

    def test_a_missing_change_or_kind_is_reported(self):
        before = [line(1, "order", "a", "NEW"), line(2, "order", "a", "PAID"), line(3, "order", "a", "SHIPPED"),
                  line(4, "chair", 7, "ACTIVE")]
        after = [line(1, "order", "z", "NEW"), line(2, "order", "z", "PAID")]
        result = self.compare(before, after)
        self.assertEqual(result.returncode, 1)
        self.assertIn("order: DIFFERS", result.stdout)
        self.assertIn("NEW > PAID > SHIPPED", result.stdout)
        self.assertIn("chair: DIFFERS (1 objects before, 0 after)", result.stdout)
        ignored = self.compare(before, after, "--ignore-kind", "chair", "--ignore-kind", "order")
        self.assertEqual(ignored.returncode, 0, ignored.stdout)

    def test_order_follows_time_not_file_position(self):
        before = [line(2, "order", "a", "PAID"), line(1, "order", "a", "NEW")]
        after = [line(1, "order", "b", "NEW"), line(2, "order", "b", "PAID")]
        self.assertEqual(self.compare(before, after).returncode, 0)

    def test_a_log_that_dropped_lines_cannot_be_compared(self):
        before = [line(1, "order", "a", "NEW"), json.dumps({"t": 2, "dropped": 3})]
        result = self.compare(before, [line(1, "order", "b", "NEW")])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("lost 3 lines", result.stderr)


if __name__ == "__main__":
    unittest.main()
