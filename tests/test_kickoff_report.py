from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent


class KickoffReportTest(unittest.TestCase):
    """The report reads isuscope and the conversation events; isuscope is a stand-in here."""

    def test_reports_baseline_limits_post_processing_and_questions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runs = [
                ['run-survey-failed', '2026-10-10T06:27:52+00:00', '2026-10-10T06:28:43+00:00', 'survey-run', 'failed', None, 0],
                ['run-baseline', '2026-10-10T06:33:04+00:00', '2026-10-10T06:34:41+00:00', 'survey-run', 'complete', 880, 1],
                ['run-index', '2026-10-10T06:40:17+00:00', '2026-10-10T06:42:04+00:00', 'run', 'complete', 4701, 1],
                ['run-backlog1', '2026-10-10T07:19:51+00:00', '2026-10-10T07:21:13+00:00', 'run', 'failed', 287456, 0],
            ]
            for run_id, _, finished, *_ in runs:
                path = root / 'isuscope-data/runs' / run_id / 'run.json'
                path.parent.mkdir(parents=True)
                # isuscope finished 30 seconds after the benchmark in every run.
                benchmark_end = (datetime.fromisoformat(finished) - timedelta(seconds=30)).isoformat()
                path.write_text(json.dumps({'benchmark': {'finished_at': benchmark_end}}))
            stub = root / 'bin/isuscope'
            stub.parent.mkdir()
            sql = json.dumps({'columns': ['id', 'started_at', 'finished_at', 'mode', 'state', 'score', 'passed'], 'rows': runs})
            brief = json.dumps({'limits': {'columns': ['node', 'setting', 'evidence'],
                                           'rows': [['app1', 'listen backlog', '1198 TCP listen overflows']]}})
            empty = json.dumps({'limits': {'columns': [], 'rows': []}})
            stub.write_text(f"""#!/bin/sh
case "$1" in
  sql) printf '%s\\n' '{sql}' ;;
  brief) if [ "$2" = backlog1 ]; then printf '%s\\n' '{brief}'; else printf '%s\\n' '{empty}'; fi ;;
esac
""")
            stub.chmod(0o755)
            history = root / 'docs/agent-history/session.events.jsonl'
            history.parent.mkdir(parents=True)
            events = [
                {'kind': 'assistant', 'received_at': '2026-10-10T15:17:14+09:00', 'text': '準備ができました。どちらにしますか？'},
                {'kind': 'user', 'received_at': '2026-10-10T15:20:49+09:00', 'text': '通常のベンチをやって'},
                {'kind': 'assistant', 'received_at': '2026-10-10T15:35:30+09:00', 'text': 'baselineは880点でした。'},
                {'kind': 'user', 'received_at': '2026-10-10T15:36:17+09:00', 'text': '次へ'},
            ]
            history.write_text(''.join(json.dumps(event, ensure_ascii=False) + '\n' for event in events))
            result = subprocess.run(
                ['python3', str(ROOT / 'scripts/kickoff-report.py'), '--start', '2026-10-10T15:01:05+09:00',
                 '--root', str(root), '--json'],
                capture_output=True, text=True, env={**os.environ, 'PATH': f"{stub.parent}:{os.environ['PATH']}"})
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report['baseline_run'], 'baseline')
            self.assertEqual(report['baseline_minutes'], 33.6)
            self.assertEqual(report['survey_runs_before_baseline'], 1)
            self.assertEqual(report['limit_failures'], [{'run': 'backlog1', 'settings': ['listen backlog']}])
            self.assertEqual(report['best_score_60_minutes_after_baseline'], 4701)
            self.assertEqual(report['questions'], 1)
            self.assertEqual(report['question_wait_minutes'], 3.6)
            self.assertEqual(report['after_seconds_median'], 30.0)


if __name__ == '__main__':
    unittest.main()
