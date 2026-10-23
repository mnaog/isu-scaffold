"""APIごとの待ち: mainが値を変えるscripts/waits.py、runに残すidentity、アプリへ写すconfig/waits/waits.go。"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tests'))
import isuscope_stub  # noqa: E402

UNIT = '[Service]\nEnvironmentFile=/home/isucon/env.sh\nExecStart=/home/isucon/webapp/go/app\n'
SYNC = {'items': [
    {'name': 'go-service', 'local': 'config/systemd/app.service', 'remote': '/etc/systemd/system/app.service', 'type': 'file'},
    {'name': 'app-env', 'local': 'config/app/env.sh', 'remote': '/home/isucon/env.sh', 'type': 'file'},
]}


class WaitsScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        (self.repo / 'scripts').mkdir()
        for name in ('waits.py', 'isuscope_sql.py', 'benchmark-identity.py'):
            shutil.copy(ROOT / 'scripts' / name, self.repo / 'scripts' / name)
        (self.repo / 'config/systemd').mkdir(parents=True)
        (self.repo / 'config/app').mkdir(parents=True)
        (self.repo / 'config/systemd/app.service').write_text(UNIT)
        (self.repo / 'config/app/env.sh').write_text('ISUCON_DB_HOST=127.0.0.1\n')
        (self.repo / 'config/sync.json').write_text(json.dumps(SYNC))
        self.env = dict(os.environ, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@example.com',
                        GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@example.com')
        for args in (['init', '-q', '-b', 'main'], ['add', '.'], ['commit', '-qm', 'fixture']):
            subprocess.run(['git', *args], cwd=self.repo, check=True, env=self.env)

    def tearDown(self):
        self.tmp.cleanup()

    def waits(self, *args, ok=True):
        result = subprocess.run(['python3', 'scripts/waits.py', *args], cwd=self.repo, capture_output=True, text=True,
                                env=self.env)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def identity(self):
        out = subprocess.run(['python3', 'scripts/benchmark-identity.py'], cwd=self.repo, capture_output=True, text=True,
                             check=True).stdout
        return json.loads(out)

    def commit(self, message):
        subprocess.run(['git', 'commit', '-qam', message], cwd=self.repo, check=True, env=self.env)

    def test_set_finds_the_unit_s_environment_file_and_edits_one_line(self):
        self.assertNotIn('waits', self.identity())  # no table, no field
        (self.repo / 'other.txt').write_text('a\n')
        subprocess.run(['git', 'add', 'other.txt'], cwd=self.repo, check=True)
        self.commit('Other')
        (self.repo / 'other.txt').write_text('changed\n')  # someone's unrelated edit
        out = self.waits('set', 'GET', '/api/app/nearby-chairs', '--after', '30').stdout
        self.assertIn('--change wait-get-api-app-nearby-chairs-after-30', out)
        self.assertIn('--hypothesis "wait: GET /api/app/nearby-chairs after 0→30ms。基準 <短縮ID>。期待: "', out)
        env = (self.repo / 'config/app/env.sh').read_text()
        self.assertIn('ISUCON_DB_HOST=127.0.0.1\n', env)
        # set commits the table's file alone; the unrelated edit stays out of it.
        self.assertIn('commit: Wait GET /api/app/nearby-chairs', out)
        shown = subprocess.run(['git', 'show', '--stat', '--format=%s', 'HEAD'], cwd=self.repo, capture_output=True,
                               text=True).stdout
        self.assertIn('config/app/env.sh', shown)
        self.assertNotIn('other.txt', shown)
        self.assertEqual((self.repo / 'other.txt').read_text(), 'changed\n')
        self.assertIn('ISUCON_WAITS="GET /api/app/nearby-chairs after 30"', env)
        # The same route and phase is replaced, a placeholder spelled differently is the same route.
        self.waits('set', 'POST', '/api/app/rides/:ride_id/evaluation', '--before', '5')
        self.waits('set', 'post', '/api/app/rides/{id}/evaluation', '--before', '10')
        self.assertIn('after 30→100ms', self.waits('set', 'GET', '/api/app/nearby-chairs', '--after', '100').stdout)
        self.assertEqual((self.repo / 'config/app/env.sh').read_text().count('ISUCON_WAITS='), 1)
        line = [l for l in (self.repo / 'config/app/env.sh').read_text().splitlines() if l.startswith('ISUCON_WAITS=')]
        self.assertEqual(line, ['ISUCON_WAITS="POST /api/app/rides/{id}/evaluation before 10;GET /api/app/nearby-chairs after 100"'])
        self.assertEqual(self.identity()['waits'],
                         'POST /api/app/rides/{id}/evaluation before 10;GET /api/app/nearby-chairs after 100')
        # 0 drops one phase, --off drops the route.
        self.waits('set', 'POST', '/api/app/rides/:ride_id/evaluation', '--before', '0')
        self.waits('set', 'GET', '/api/app/nearby-chairs', '--off')
        self.assertIn('ISUCON_WAITS=""', (self.repo / 'config/app/env.sh').read_text())
        self.assertNotIn('waits', self.identity())

    def test_a_wrong_rule_is_refused_before_it_reaches_the_app(self):
        refused = self.waits('set', 'GET', 'x', '--after', '10', ok=False)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn('PATHは/で始める', refused.stderr)
        refused = self.waits('set', 'GET', '/x', '--after', '-5', ok=False)
        self.assertIn('0以上の整数', refused.stderr)
        self.assertNotIn('ISUCON_WAITS', (self.repo / 'config/app/env.sh').read_text())

    def test_history_reads_the_runs_and_the_wait_changes(self):
        db, bin_dir = isuscope_stub.install(self.tmp.name)
        for index, (score, waits, tags) in enumerate([(700000, None, []), (760000, 'GET /a after 30', ['wait', 'wait:GET /a']),
                                                      (755000, 'GET /a after 30', [])]):
            run_id = f'01a15900-0000-7000-8000-00000000000{index}'
            db.execute("INSERT INTO runs (id, started_at, score, passed) VALUES (?, ?, ?, 1)",
                       (run_id, f'2026-10-20T12:0{index}:00Z', score))
            db.executemany('INSERT INTO run_tags VALUES (?, ?)', [(run_id, tag) for tag in tags])
            if waits:
                db.execute("INSERT INTO run_identity VALUES (?, 'waits', ?)", (run_id, waits))
        db.execute("INSERT INTO changes (id, created_at) VALUES ('wait-get-a-after-30', '2026-10-20T12:02:00Z')")
        db.execute("INSERT INTO change_decisions VALUES ('d1', 'wait-get-a-after-30', '2026-10-20T12:02:00Z', 'accepted', "
                   "'空振りのpollingが減り、点になる処理が増えた', NULL)")
        db.execute("INSERT INTO change_decision_runs VALUES ('d1', '01a15900-0000-7000-8000-000000000001')")
        db.commit()
        self.env['PATH'] = f'{bin_dir}:{self.env["PATH"]}'
        out = self.waits('history').stdout
        self.assertIn('00000001 PASS    760000  waits="GET /a after 30"', out)
        self.assertNotIn('00000002', out)  # same table, no tag
        self.assertIn('wait-get-a-after-30: accepted（run 00000001）', out)
        self.assertIn('空振りのpollingが減り', out)

MAIN = r'''
package main

import (
	"fmt"
	"os"
	"time"
)

func main() {
	table, errors := waitsParse(
		"GET /api/app/nearby-chairs after 100; POST /api/app/rides/:ride_id/evaluation before 9999;" +
			" PUT /x sideways 1; GET /api/chair/notification after 20 status=200")
	fmt.Println("errors", len(errors))
	fmt.Println("describe", table.describe())
	ms := func(wait time.Duration, ok bool) int64 {
		if !ok {
			return -1
		}
		return wait.Milliseconds()
	}
	fmt.Println("after", ms(table.after("GET", "/api/app/nearby-chairs")))
	fmt.Println("other-phase", ms(table.before("GET", "/api/app/nearby-chairs")))
	fmt.Println("placeholder", ms(table.before("post", "/api/app/rides/01ABC/evaluation")))
	fmt.Println("deeper", ms(table.before("POST", "/api/app/rides/01ABC/evaluation/x")))
	fmt.Println("unknown-word", ms(table.after("GET", "/api/chair/notification")))
	os.Setenv(waitsVariable, "")
	fmt.Println("global-empty", waitsGlobal().isEmpty())
}
'''

# 表はprocessで1度だけ読むので、middlewareのtestは表を環境変数で渡して別のprocessで動かす。
MIDDLEWARE_TABLE = 'GET /slow/{id} before 200;GET /late after 200;GET /stream after 200'

MIDDLEWARE_TESTS = r'''
package main

import (
	"bufio"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func serve(t *testing.T, handler http.HandlerFunc) *httptest.Server {
	server := httptest.NewServer(waitsMiddleware(handler))
	t.Cleanup(server.Close)
	return server
}

func get(t *testing.T, url string) (string, time.Duration) {
	started := time.Now()
	response, err := http.Get(url)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	body, err := io.ReadAll(response.Body)
	if err != nil {
		t.Fatal(err)
	}
	return string(body), time.Since(started)
}

func TestBeforeWaitsAheadOfTheHandler(t *testing.T) {
	requested := make(chan time.Time, 1)
	var started time.Time
	server := serve(t, func(w http.ResponseWriter, r *http.Request) {
		started = time.Now()
		w.Write([]byte("ok"))
	})
	requested <- time.Now()
	body, took := get(t, server.URL+"/slow/42?x=1")
	if body != "ok" || took < 200*time.Millisecond || started.Sub(<-requested) < 200*time.Millisecond {
		t.Fatalf("body %q took %v", body, took)
	}
}

func TestAfterRunsTheHandlerFirstAndDelaysTheResponse(t *testing.T) {
	var finished time.Time
	server := serve(t, func(w http.ResponseWriter, r *http.Request) {
		finished = time.Now()
		w.Header().Set("X-Check", "kept")
		w.WriteHeader(http.StatusAccepted)
		w.Write([]byte("done"))
	})
	started := time.Now()
	response, err := http.Get(server.URL + "/late")
	if err != nil {
		t.Fatal(err)
	}
	body, _ := io.ReadAll(response.Body)
	response.Body.Close()
	took := time.Since(started)
	if string(body) != "done" || response.StatusCode != http.StatusAccepted || response.Header.Get("X-Check") != "kept" {
		t.Fatalf("%d %q %v", response.StatusCode, body, response.Header)
	}
	if finished.Sub(started) > 150*time.Millisecond || took < 200*time.Millisecond {
		t.Fatalf("handler at %v, response at %v", finished.Sub(started), took)
	}
}

func TestAHandlerThatWritesNothingIsStillDelayed(t *testing.T) {
	server := serve(t, func(w http.ResponseWriter, r *http.Request) {})
	if _, took := get(t, server.URL+"/late"); took < 200*time.Millisecond {
		t.Fatal(took)
	}
}

func TestOtherRoutesAndMethodsPassStraightThrough(t *testing.T) {
	server := serve(t, func(w http.ResponseWriter, r *http.Request) { w.Write([]byte("ok")) })
	for _, path := range []string{"/fast", "/slow/1/x", "/slow"} {
		if body, took := get(t, server.URL+path); body != "ok" || took > 150*time.Millisecond {
			t.Fatalf("%s: %q %v", path, body, took)
		}
	}
	response, err := http.Post(server.URL+"/late", "text/plain", nil)
	if err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
}

func TestAStreamingResponseIsNotBuffered(t *testing.T) {
	server := serve(t, func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte("first\n"))
		w.(http.Flusher).Flush()
		time.Sleep(500 * time.Millisecond)
		w.Write([]byte("second\n"))
	})
	started := time.Now()
	response, err := http.Get(server.URL + "/stream")
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	line, err := bufio.NewReader(response.Body).ReadString('\n')
	took := time.Since(started)
	if err != nil || line != "first\n" || took < 200*time.Millisecond || took > 600*time.Millisecond {
		t.Fatalf("%q %v %v", line, err, took)
	}
}
'''


@unittest.skipUnless(shutil.which('go'), 'go is not installed')
class WaitsModuleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.module = Path(self.tmp.name) / 'module'
        self.module.mkdir()
        (self.module / 'go.mod').write_text('module waits_check\n\ngo 1.22\n')
        shutil.copyfile(ROOT / 'config/waits/waits.go', self.module / 'waits.go')
        (self.module / 'main.go').write_text(MAIN)
        # 標準libraryだけなので、moduleの取得もtoolchainの切り替えも要らない。
        self.env = {**os.environ, 'GOTOOLCHAIN': 'local', 'GOFLAGS': '', 'GO111MODULE': 'on'}

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_module_parses_and_matches(self):
        out = subprocess.run(['go', 'run', '.'], cwd=self.module, env=self.env, capture_output=True,
                             text=True, check=True).stdout
        lines = dict(line.split(' ', 1) for line in out.splitlines())
        self.assertEqual(lines['errors'], '2')  # an unknown phase and a fifth word are skipped, not fatal
        self.assertEqual(lines['describe'], 'GET /api/app/nearby-chairs after 100;POST /api/app/rides/:ride_id/evaluation before 9999')
        self.assertEqual(lines['after'], '100')
        self.assertEqual(lines['other-phase'], '-1')
        self.assertEqual(lines['placeholder'], '9999')
        self.assertEqual(lines['deeper'], '-1')
        self.assertEqual(lines['unknown-word'], '-1')
        self.assertEqual(lines['global-empty'], 'true')

    def test_the_middleware_waits_before_and_after_without_buffering(self):
        (self.module / 'waits_test.go').write_text(MIDDLEWARE_TESTS)
        env = {**self.env, 'ISUCON_WAITS': MIDDLEWARE_TABLE}
        result = subprocess.run(['go', 'test', '-count=1', '-v', '.'], cwd=self.module, env=env,
                                capture_output=True, text=True, timeout=300)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.count('--- PASS'), 5, result.stdout)
        # 起動時に読んだ表は標準エラーへ書く（go testは子processの標準エラーを標準出力へまとめる）。
        self.assertIn('waits: ' + MIDDLEWARE_TABLE, result.stdout + result.stderr)

    def test_the_module_passes_go_vet(self):
        result = subprocess.run(['go', 'vet', '.'], cwd=self.module, env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
