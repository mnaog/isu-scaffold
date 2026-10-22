"""lane state-logがアプリへ写す書き手（config/state-log/state_log.go）を、単独のGoのmoduleで確かめる。

isuscopeとの約束（1行の形）を守ること、送り手を待たせないこと、環境変数で止められることを見る。
goが無い端末では飛ばす。
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
WRITER = ROOT / 'config/state-log/state_log.go'

MAIN = r'''
package main

import (
	"fmt"
	"sync"
	"time"
)

func main() {
	var group sync.WaitGroup
	for thread := 0; thread < 4; thread++ {
		group.Add(1)
		go func(thread int) {
			defer group.Done()
			for index := 0; index < 250; index++ {
				id := fmt.Sprintf("o%d-%d", thread, index)
				stateLogCreated("order", id, "PENDING", stateLogAttr{"user", fmt.Sprintf("u%d", index)})
				stateLogChanged("order", id, "PENDING", "PAID", stateLogAttr{"amount", index * 10})
				var log stateLogPending
				log.entered("order", id, "SHIPPED", stateLogAttr{"note", "say \"hi\"\n"})
				log.emit()
				// commitしなかったtransactionの変化は書かない。
				var dropped stateLogPending
				dropped.changed("order", id, "SHIPPED", "CANCELLED")
				_ = dropped
			}
		}(thread)
	}
	group.Wait()
	time.Sleep(400 * time.Millisecond)
}
'''

UNIT_TESTS = r'''
package main

import (
	"math"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"
)

type namedState string

func TestLinesFollowTheIsuscopeFormat(t *testing.T) {
	event := stateLogNewEvent("ride", 42, stateLogPreviousKnown, "MATCHING", "EN\"ROUTE", []stateLogAttr{
		{"chair", "c\\1\n"},
		{"distance", 512},
		{"rate", 0.5},
		{"bad", math.NaN()},
		{"paid", true},
		{"big", uint64(math.MaxUint64)},
		{"state", namedState("A\x01")},
		{"whole", 2.0},
	})
	event.t = 1_700_000_000_000_001
	event.seq = 7
	line := string(event.write(nil))
	want := "{\"t\":1700000000000001,\"seq\":7,\"kind\":\"ride\",\"id\":\"42\",\"from\":\"MATCHING\",\"to\":\"EN\\\"ROUTE\",\"attrs\":{\"chair\":\"c\\\\1\\n\",\"distance\":512,\"rate\":0.5,\"bad\":null,\"paid\":true,\"big\":9223372036854775807,\"state\":\"A\\u0001\",\"whole\":2}}\n"
	if line != want {
		t.Fatalf("got  %q\nwant %q", line, want)
	}
	created := string((&stateLogEvent{kind: "ride", id: "r1", previous: stateLogPreviousCreated, to: "A"}).write(nil))
	if !strings.Contains(created, ",\"from\":null,\"to\":\"A\"}") {
		t.Fatal(created)
	}
	entered := string((&stateLogEvent{kind: "ride", id: "r1", previous: stateLogPreviousUnknown, to: "B"}).write(nil))
	if strings.Contains(entered, "from") {
		t.Fatal(entered)
	}
}

func TestManyGoroutinesWriteEveryEventOnce(t *testing.T) {
	path := filepath.Join(t.TempDir(), "state.jsonl")
	writer := stateLogStart(path, stateLogQueue)
	var group sync.WaitGroup
	for thread := 0; thread < 8; thread++ {
		group.Add(1)
		go func(thread int) {
			defer group.Done()
			for index := 0; index < 1000; index++ {
				writer.send(stateLogNewEvent("item", strconv.Itoa(thread)+"-"+strconv.Itoa(index), stateLogPreviousCreated, "", "NEW", nil))
			}
		}(thread)
	}
	group.Wait()
	writer.close()
	written, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	lines := strings.Split(strings.TrimSuffix(string(written), "\n"), "\n")
	if len(lines) != 8000 {
		t.Fatalf("%d lines", len(lines))
	}
	seqs := map[int64]bool{}
	for _, line := range lines {
		rest := line[strings.Index(line, "\"seq\":")+6:]
		seq, err := strconv.ParseInt(rest[:strings.Index(rest, ",")], 10, 64)
		if err != nil {
			t.Fatal(err)
		}
		seqs[seq] = true
	}
	if len(seqs) != 8000 {
		t.Fatalf("%d distinct seqs", len(seqs))
	}
}

func TestAFullQueueDropsAndCountsInsteadOfWaiting(t *testing.T) {
	path := filepath.Join(t.TempDir(), "state.jsonl")
	writer := stateLogStart(path, 1)
	started := time.Now()
	for index := 0; index < 10_000; index++ {
		writer.send(stateLogNewEvent("item", index, stateLogPreviousCreated, "", "NEW", nil))
	}
	if time.Since(started) >= time.Second {
		t.Fatal("send must not block")
	}
	writer.close()
	written, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var events, dropped uint64
	for _, line := range strings.Split(strings.TrimSuffix(string(written), "\n"), "\n") {
		if strings.Contains(line, "\"kind\"") {
			events++
		}
		if parts := strings.SplitN(line, "\"dropped\":", 2); len(parts) == 2 {
			count, err := strconv.ParseUint(strings.TrimSuffix(parts[1], "}"), 10, 64)
			if err != nil {
				t.Fatal(err)
			}
			dropped += count
		}
	}
	if dropped == 0 {
		t.Fatal(string(written))
	}
	if events+dropped != 10_000 {
		t.Fatalf("events %d + dropped %d", events, dropped)
	}
}

func TestAnEmptyVariableTurnsTheLogOff(t *testing.T) {
	if got := stateLogPathFrom("", true); got != "" {
		t.Fatal(got)
	}
	if got := stateLogPathFrom("", false); got != stateLogDefaultPath {
		t.Fatal(got)
	}
	if got := stateLogPathFrom("/tmp/x.jsonl", true); got != "/tmp/x.jsonl" {
		t.Fatal(got)
	}
}

func TestAnUnwritablePathDisablesTheWriter(t *testing.T) {
	writer := stateLogStart("/nonexistent-directory/state.jsonl", stateLogQueue)
	deadline := time.Now().Add(2 * time.Second)
	for !writer.closed.Load() && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	if !writer.closed.Load() {
		t.Fatal("the writer stays open")
	}
	writer.send(stateLogNewEvent("item", 1, stateLogPreviousCreated, "", "NEW", nil))
	writer.close()
}

func TestADisabledLogCollectsNothing(t *testing.T) {
	// testはISUSCOPE_STATE_LOGを空にして動かす。止めた書き手はtransactionの変化もためない。
	var pending stateLogPending
	pending.changed("order", "o1", "A", "B")
	if len(pending.events) != 0 {
		t.Fatal("a disabled log collects events")
	}
	pending.emit()
	stateLogCreated("order", "o1", "A")
}
'''


def go_env():
    # 標準libraryだけなので、moduleの取得もtoolchainの切り替えも要らない。
    return {**os.environ, 'GOTOOLCHAIN': 'local', 'GOFLAGS': '', 'GO111MODULE': 'on'}


def race_flags(env):
    # -raceはcgoが要る。使えない端末では付けない。
    cgo = subprocess.run(['go', 'env', 'CGO_ENABLED'], env=env, capture_output=True, text=True).stdout.strip()
    return ['-race'] if cgo == '1' else []


@unittest.skipUnless(shutil.which('go'), 'go is not installed')
class StateLogWriterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.module = Path(cls.tmp.name) / 'module'
        cls.module.mkdir()
        (cls.module / 'go.mod').write_text('module state_log_check\n\ngo 1.22\n')
        shutil.copyfile(WRITER, cls.module / 'state_log.go')
        (cls.module / 'main.go').write_text(MAIN)
        (cls.module / 'state_log_test.go').write_text(UNIT_TESTS)
        cls.env = go_env()
        cls.binary = Path(cls.tmp.name) / 'state_log_check'
        subprocess.run(['go', 'build', '-o', str(cls.binary), '.'], cwd=cls.module, env=cls.env, check=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_writer(self, path):
        env = {**self.env, 'ISUSCOPE_STATE_LOG': '' if path is None else str(path)}
        subprocess.run([str(self.binary)], env=env, check=True, timeout=60)

    def test_unit_tests_of_the_writer_pass(self):
        # 既定のpathへ書かないよう、書き手を止めて動かす（各testは自分の書き手を作る）。
        env = {**self.env, 'ISUSCOPE_STATE_LOG': ''}
        result = subprocess.run(['go', 'test', *race_flags(env), '-count=1', '-v', '.'],
                                cwd=self.module, env=env, capture_output=True, text=True, timeout=300)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.count('--- PASS'), 6, result.stdout)

    def test_the_writer_passes_go_vet(self):
        result = subprocess.run(['go', 'vet', '.'], cwd=self.module, env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_lines_follow_the_isuscope_format(self):
        path = Path(self.tmp.name) / 'state.jsonl'
        self.run_writer(path)
        lines = [json.loads(line) for line in path.read_text().splitlines()]
        events = [line for line in lines if 'kind' in line]
        self.assertEqual(sum(line.get('dropped', 0) for line in lines), 0)
        self.assertEqual(len(events), 4 * 250 * 3)
        for event in events:
            self.assertIsInstance(event['t'], int)
            self.assertGreater(event['t'], 1_600_000_000_000_000, 'microseconds since the epoch')
            self.assertIsInstance(event['seq'], int)
            self.assertEqual(event['kind'], 'order')
            self.assertIsInstance(event['id'], str)
            self.assertIn(event['to'], ('PENDING', 'PAID', 'SHIPPED'))
        by_state = {event['to']: event for event in events if event['id'] == 'o1-7'}
        self.assertIsNone(by_state['PENDING']['from'], 'created writes "from": null')
        self.assertEqual(by_state['PAID']['from'], 'PENDING')
        self.assertNotIn('from', by_state['SHIPPED'], 'entered leaves "from" out')
        self.assertEqual(by_state['PAID']['attrs'], {'amount': 70})
        self.assertEqual(by_state['SHIPPED']['attrs'], {'note': 'say "hi"\n'})
        self.assertNotIn('CANCELLED', {event['to'] for event in events})
        self.assertEqual(len({event['seq'] for event in events}), len(events))

    def test_an_empty_variable_writes_nothing(self):
        # 空なら既定のpathへも書かない（最終計測の前に止めるための約束）。
        default = Path('/var/tmp/isuscope-state.jsonl')
        before = default.stat().st_mtime_ns if default.exists() else None
        self.run_writer(None)
        after = default.stat().st_mtime_ns if default.exists() else None
        self.assertEqual(before, after)


class StateLogLaneTest(unittest.TestCase):
    def test_kickoff_starts_the_state_log_lane_with_a_prompt_that_copies_the_writer(self):
        lanes = json.loads((ROOT / 'config/lanes.json').read_text())
        self.assertIn('state-log', lanes['kickoff'])
        prompt = (ROOT / lanes['lanes']['state-log']['prompt']).read_text()
        self.assertIn('config/state-log/state_log.go', prompt)
        export = json.loads((ROOT / 'config/scaffold-export.json').read_text())['files']
        for path in ('config/lanes/state-log.md', 'config/state-log/state_log.go'):
            self.assertIn(path, export)


if __name__ == '__main__':
    unittest.main()
