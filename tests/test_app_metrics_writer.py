"""lane state-logがアプリへ写すアプリ計測の書き手（config/app-metrics/app_metrics.go）を、単独のGoのmoduleで確かめる。

isuscopeとの約束（collectorの標準のmetric行）を守ること、lockの待ちが数えられること、
環境変数で止められることを見る。goが無い端末では飛ばす。
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
WRITER = ROOT / 'config/app-metrics/app_metrics.go'

MAIN = r'''
package main

import (
	"strconv"
	"sync"
	"time"
)

var (
	store     sync.Mutex
	stored    uint64
	storeLock = appMetricsNewLock("store")
)

func main() {
	var group sync.WaitGroup
	for thread := 0; thread < 4; thread++ {
		group.Add(1)
		go func(thread int) {
			defer group.Done()
			for index := 0; index < 50; index++ {
				// 4本が同じlockを1msずつ持つので、待ちが出る。
				release := storeLock.acquire(store.Lock, store.Unlock)
				stored++
				time.Sleep(time.Millisecond)
				release()
				outcome := "204"
				if index%3 == 0 {
					outcome = strconv.Itoa(502)
				}
				appMetricsCall("payment", outcome, time.Duration(thread+1)*time.Millisecond)
			}
		}(thread)
	}
	group.Wait()
	if stored != 200 {
		panic(stored)
	}
	// 書き手は1秒ごとに書くので、最後の分が届くまで待つ。
	time.Sleep(1300 * time.Millisecond)
}
'''

UNIT_TESTS = r'''
package main

import (
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"
)

func TestCallsAreSummedPerTargetAndOutcomeAndResetEachSecond(t *testing.T) {
	sink := appMetricsNewSink()
	sink.call("payment", "502", 1500*time.Microsecond)
	sink.call("payment", "502", 500*time.Microsecond)
	sink.call("payment", "204", 300*time.Microsecond)
	out := string(sink.drain(1_700_000_000.25, nil))
	for _, want := range []string{
		"{\"type\":\"metric\",\"name\":\"app.call.count\",\"value\":2,\"unit\":\"calls\",\"labels\":{\"target\":\"payment\",\"outcome\":\"502\"},\"timestamp\":1700000000.250}\n",
		"\"name\":\"app.call.duration_sum\",\"value\":2,\"unit\":\"ms\",\"labels\":{\"target\":\"payment\",\"outcome\":\"502\"}",
		"\"name\":\"app.call.duration_max\",\"value\":1.5,",
		"\"outcome\":\"204\"",
	} {
		if !strings.Contains(out, want) {
			t.Fatalf("%q not in\n%s", want, out)
		}
	}
	// 呼び先と結果の順に並ぶ（204が502より先）。
	if strings.Index(out, "\"outcome\":\"204\"") > strings.Index(out, "\"outcome\":\"502\"") {
		t.Fatal(out)
	}
	if next := sink.drain(1_700_000_001.0, nil); len(next) != 0 {
		t.Fatal(string(next))
	}
}

func TestALockCountsWaitingAndHoldingAndOnlySecondsWithUseAreWritten(t *testing.T) {
	sink := appMetricsNewSink()
	lock := appMetricsNewLock("te\"st")
	var mutex sync.Mutex
	value := 0
	for index := 0; index < 3; index++ {
		started := time.Now()
		mutex.Lock()
		acquired := time.Now()
		lock.waited(sink, acquired.Sub(started))
		value++
		time.Sleep(2 * time.Millisecond)
		mutex.Unlock()
		lock.held(time.Since(acquired))
	}
	if value != 3 {
		t.Fatal(value)
	}
	out := string(sink.drain(1.0, nil))
	if !strings.Contains(out, "\"name\":\"app.lock.count\",\"value\":3,\"unit\":\"acquisitions\",\"labels\":{\"lock\":\"te\\\"st\"}") {
		t.Fatal(out)
	}
	var hold float64 = -1
	for _, line := range strings.Split(out, "\n") {
		if strings.Contains(line, "app.lock.hold_sum") {
			rest := strings.SplitN(line, "\"value\":", 2)[1]
			hold, _ = strconv.ParseFloat(strings.SplitN(rest, ",", 2)[0], 64)
		}
	}
	if hold < 6.0 {
		t.Fatal(out)
	}
	if idle := sink.drain(2.0, nil); len(idle) != 0 {
		t.Fatal(string(idle))
	}
}

func TestADisabledSinkStillTakesAndReleasesTheLock(t *testing.T) {
	// testはISUSCOPE_APP_METRICSを空にして動かす。数えずに、取る関数と放す関数だけを呼ぶ。
	lock := appMetricsNewLock("off")
	var mutex sync.RWMutex
	release := lock.acquire(mutex.RLock, mutex.RUnlock)
	if mutex.TryLock() {
		t.Fatal("the read lock is not held")
	}
	release()
	if !mutex.TryLock() {
		t.Fatal("the read lock is not released")
	}
	mutex.Unlock()
	if values, used := lock.take(); used {
		t.Fatal(values)
	}
	appMetricsCall("payment", 200, time.Millisecond)
}

func TestTheWriterGoroutineAppendsLinesAndStopsAfterTheLastSecond(t *testing.T) {
	path := filepath.Join(t.TempDir(), "app.jsonl")
	sink := appMetricsStart(path)
	sink.call("payment", "timeout", 3*time.Millisecond)
	sink.close()
	written, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	lines := strings.Split(strings.TrimSuffix(string(written), "\n"), "\n")
	if len(lines) != 3 {
		t.Fatal(string(written))
	}
	for _, line := range lines {
		if !strings.HasPrefix(line, "{\"type\":\"metric\"") {
			t.Fatal(line)
		}
	}
}

func TestAnEmptyVariableTurnsTheMetricsOff(t *testing.T) {
	if got := appMetricsPathFrom("", true); got != "" {
		t.Fatal(got)
	}
	if got := appMetricsPathFrom("", false); got != appMetricsDefaultPath {
		t.Fatal(got)
	}
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
class AppMetricsWriterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.module = Path(cls.tmp.name) / 'module'
        cls.module.mkdir()
        (cls.module / 'go.mod').write_text('module app_metrics_check\n\ngo 1.22\n')
        shutil.copyfile(WRITER, cls.module / 'app_metrics.go')
        (cls.module / 'main.go').write_text(MAIN)
        (cls.module / 'app_metrics_test.go').write_text(UNIT_TESTS)
        cls.env = go_env()
        cls.binary = Path(cls.tmp.name) / 'app_metrics_check'
        subprocess.run(['go', 'build', '-o', str(cls.binary), '.'], cwd=cls.module, env=cls.env, check=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_writer(self, path):
        env = {**self.env, 'ISUSCOPE_APP_METRICS': '' if path is None else str(path)}
        subprocess.run([str(self.binary)], env=env, check=True, timeout=60)

    def test_unit_tests_of_the_writer_pass(self):
        # 既定のpathへ書かないよう、書き手を止めて動かす（書き先の要るtestは自分の書き手を作る）。
        env = {**self.env, 'ISUSCOPE_APP_METRICS': ''}
        result = subprocess.run(['go', 'test', *race_flags(env), '-count=1', '-v', '.'],
                                cwd=self.module, env=env, capture_output=True, text=True, timeout=300)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.count('--- PASS'), 5, result.stdout)

    def test_the_writer_passes_go_vet(self):
        result = subprocess.run(['go', 'vet', '.'], cwd=self.module, env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_lines_are_isuscope_metric_records_with_calls_and_lock_waits(self):
        path = Path(self.tmp.name) / 'app.jsonl'
        self.run_writer(path)
        lines = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertTrue(lines)
        for line in lines:
            self.assertEqual(line['type'], 'metric')
            self.assertTrue(line['name'].startswith('app.'), line)
            self.assertIsInstance(line['timestamp'], (int, float))
            self.assertGreater(line['timestamp'], 1_600_000_000, 'Unix seconds')
            self.assertTrue(all(isinstance(value, str) for value in line['labels'].values()))

        def total(name, **labels):
            return sum(line['value'] for line in lines
                       if line['name'] == name and all(line['labels'].get(k) == v for k, v in labels.items()))

        self.assertEqual(total('app.call.count', target='payment', outcome='502'), 4 * 17)
        self.assertEqual(total('app.call.count', target='payment', outcome='204'), 4 * 33)
        self.assertEqual(total('app.call.duration_sum', target='payment'), 50 * (1 + 2 + 3 + 4))
        self.assertEqual(max(line['value'] for line in lines if line['name'] == 'app.call.duration_max'), 4)
        self.assertEqual(total('app.lock.count', lock='store'), 200)
        self.assertGreaterEqual(total('app.lock.hold_sum', lock='store'), 200)
        self.assertGreater(total('app.lock.wait_sum', lock='store'), 0, 'four threads contend for one lock')

    def test_an_empty_variable_writes_nothing(self):
        default = Path('/var/tmp/isuscope-app-metrics.jsonl')
        before = default.stat().st_mtime_ns if default.exists() else None
        self.run_writer(None)
        after = default.stat().st_mtime_ns if default.exists() else None
        self.assertEqual(before, after)


class AppMetricsLaneTest(unittest.TestCase):
    def test_the_state_log_lane_copies_the_writer_and_the_export_carries_it(self):
        lanes = json.loads((ROOT / 'config/lanes.json').read_text())
        prompt = (ROOT / lanes['lanes']['state-log']['prompt']).read_text()
        self.assertIn('config/app-metrics/app_metrics.go', prompt)
        export = json.loads((ROOT / 'config/scaffold-export.json').read_text())['files']
        self.assertIn('config/app-metrics/app_metrics.go', export)


if __name__ == '__main__':
    unittest.main()
