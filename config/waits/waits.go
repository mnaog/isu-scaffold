// APIごとの小さな待ち（wait）の表。mainが空き時間に値を変えて、得点や負荷が良くなる箇所を探す。
//
// 応答の中身は変えず、時機だけを遅らせる。ベンチの利用者が応答を待ってから次の要求を出すなら、
// 点にならない要求（空振りのpolling、繰り返しの一覧取得など）の回数が減り、ベンチとサーバーの手が
// 点になる処理へ回ることがある。効くかは構成ごとに違うので、値はbuildし直さずに変えられるようにする。
//
//   - 表は環境変数`ISUCON_WAITS`（アプリのunitが読むEnvironmentFileに置く。repoでは
//     `scripts/waits.py set`が書き、`make deploy`で配る）。プロセスの起動時に1度だけ読むので、
//     再起動・OSの再起動・主催者の追試でも同じ表で動く。空なら何もしない。
//   - 形: `;`で区切った規則。1つの規則は`METHOD PATH before|after MS`。
//     `PATH`の`:name`・`{name}`・`*`の区切りは、任意の1区切りに合う（isuscopeのroute名をそのまま書ける）。
//   - `before`: handlerの前に待つ（要求を受けてから処理を始めるまで）。
//   - `after`: handlerが応答を書き始める前に待つ（handlerが最初にheaderか本文を書くとき、何も書かずに
//     戻ったときはその後）。応答を書き終えてから書くhandlerなら、処理は済み、相手が受け取るのが遅れる。
//     本文はbufferし直さないので、streamingの応答もそのまま流れる。
//   - 読めない規則は標準エラーへ書いて飛ばす（アプリは止めない）。
//   - 標準libraryだけで動く。このfileをアプリの`main` packageのdirectoryへそのまま写して使う。中身は変えない。
//     名前はどれも`waits`で始まるので、アプリの名前とぶつかりにくい。
//
// net/httpのmiddleware`waitsMiddleware`を、すべてのrouteを通るように足す。表が空なら何もせずに次へ渡す。
// 待つかは要求のmethodとpath（`r.URL.EscapedPath()`。query stringを含めない）で決める。
//
//	// chi: routeを足す前に。
//	r := chi.NewRouter()
//	r.Use(waitsMiddleware)
//	// echo: routerの後に通るmiddlewareとして。
//	e.Use(echo.WrapMiddleware(waitsMiddleware))
//	// net/httpだけなら、serverへ渡すhandlerを包む。
//	http.ListenAndServe(":8080", waitsMiddleware(mux))
package main

import (
	"bufio"
	"fmt"
	"io"
	"math"
	"net"
	"net/http"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"
)

// 表を読む環境変数。
const waitsVariable = "ISUCON_WAITS"

type waitsPhase int

const (
	waitsBefore waitsPhase = iota
	waitsAfter
)

type waitsRule struct {
	method string
	path   []string
	phase  waitsPhase
	ms     uint64
}

func (rule *waitsRule) matches(method, path string) bool {
	if !waitsEqualFoldASCII(rule.method, method) {
		return false
	}
	segments := strings.Split(path, "/")
	if len(segments) != len(rule.path) {
		return false
	}
	for index, pattern := range rule.path {
		if !waitsIsPlaceholder(pattern) && pattern != segments[index] {
			return false
		}
	}
	return true
}

func waitsEqualFoldASCII(a, b string) bool {
	if len(a) != len(b) {
		return false
	}
	for index := 0; index < len(a); index++ {
		x, y := a[index], b[index]
		if 'a' <= x && x <= 'z' {
			x -= 'a' - 'A'
		}
		if 'a' <= y && y <= 'z' {
			y -= 'a' - 'A'
		}
		if x != y {
			return false
		}
	}
	return true
}

func waitsIsPlaceholder(segment string) bool {
	return segment == "*" || strings.HasPrefix(segment, ":") ||
		(strings.HasPrefix(segment, "{") && strings.HasSuffix(segment, "}"))
}

// 読んだ表。
type waitsTable struct {
	rules []waitsRule
}

// 表を読む。読めなかった規則は理由と一緒に返し、読めた規則だけで表を作る。
func waitsParse(spec string) (*waitsTable, []string) {
	table := &waitsTable{}
	var errors []string
	for _, text := range strings.Split(spec, ";") {
		text = strings.TrimSpace(text)
		if text == "" {
			continue
		}
		rule, err := waitsParseRule(text)
		if err != "" {
			errors = append(errors, text+": "+err)
			continue
		}
		table.rules = append(table.rules, rule)
	}
	return table, errors
}

// `ISUCON_WAITS`から読み、読んだ表と読めなかった規則を標準エラーへ書く。
func waitsFromEnv() *waitsTable {
	table, errors := waitsParse(os.Getenv(waitsVariable))
	for _, err := range errors {
		fmt.Fprintf(os.Stderr, "waits: ignored %s\n", err)
	}
	if !table.isEmpty() {
		fmt.Fprintf(os.Stderr, "waits: %s\n", table.describe())
	}
	return table
}

func (t *waitsTable) isEmpty() bool {
	return len(t.rules) == 0
}

// handlerの前に待つ時間。合う規則が無ければfalse。
func (t *waitsTable) before(method, path string) (time.Duration, bool) {
	return t.find(waitsBefore, method, path)
}

// handlerの後、応答を返す前に待つ時間。合う規則が無ければfalse。
func (t *waitsTable) after(method, path string) (time.Duration, bool) {
	return t.find(waitsAfter, method, path)
}

func (t *waitsTable) find(phase waitsPhase, method, path string) (time.Duration, bool) {
	for index := range t.rules {
		rule := &t.rules[index]
		if rule.phase == phase && rule.matches(method, path) {
			if rule.ms > uint64(math.MaxInt64/int64(time.Millisecond)) {
				return time.Duration(math.MaxInt64), true
			}
			return time.Duration(rule.ms) * time.Millisecond, true
		}
	}
	return 0, false
}

// 読んだ表を、`ISUCON_WAITS`と同じ形で並べる。
func (t *waitsTable) describe() string {
	described := make([]string, 0, len(t.rules))
	for _, rule := range t.rules {
		phase := "after"
		if rule.phase == waitsBefore {
			phase = "before"
		}
		described = append(described, fmt.Sprintf("%s /%s %s %d",
			rule.method, strings.Join(rule.path[1:], "/"), phase, rule.ms))
	}
	return strings.Join(described, ";")
}

func waitsParseRule(text string) (waitsRule, string) {
	words := strings.Fields(text)
	if len(words) != 4 {
		return waitsRule{}, "METHOD PATH before|after MS の4語で書く"
	}
	method := strings.Map(func(r rune) rune {
		if 'a' <= r && r <= 'z' {
			return r - ('a' - 'A')
		}
		return r
	}, words[0])
	for index := 0; index < len(method); index++ {
		if method[index] < 'A' || method[index] > 'Z' {
			return waitsRule{}, "METHODが読めない: " + words[0]
		}
	}
	if !strings.HasPrefix(words[1], "/") {
		return waitsRule{}, "PATHは/で始める: " + words[1]
	}
	var phase waitsPhase
	switch words[2] {
	case "before":
		phase = waitsBefore
	case "after":
		phase = waitsAfter
	default:
		return waitsRule{}, "before・afterのどちらか: " + words[2]
	}
	// Rustのu64と同じく、先頭の`+`を1つだけ許す。
	ms, err := strconv.ParseUint(strings.TrimPrefix(words[3], "+"), 10, 64)
	if err != nil {
		return waitsRule{}, "MSが整数でない: " + words[3]
	}
	return waitsRule{method: method, path: strings.Split(words[1], "/"), phase: phase, ms: ms}, ""
}

var (
	waitsOnce  sync.Once
	waitsState *waitsTable
)

// 起動時に`ISUCON_WAITS`から1度だけ読んだ表。
func waitsGlobal() *waitsTable {
	waitsOnce.Do(func() {
		waitsState = waitsFromEnv()
	})
	return waitsState
}

// すべてのrouteを通るように足すmiddleware。表は足したときに読む（起動時に1度だけ）。
// 表が空なら、応答・status code・速さを変えない。
func waitsMiddleware(next http.Handler) http.Handler {
	waitsGlobal()
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		table := waitsGlobal()
		if table.isEmpty() {
			next.ServeHTTP(w, r)
			return
		}
		path := r.URL.EscapedPath()
		if wait, ok := table.before(r.Method, path); ok {
			if !waitsSleep(r, wait) {
				return
			}
		}
		wait, ok := table.after(r.Method, path)
		if !ok {
			next.ServeHTTP(w, r)
			return
		}
		delayed := &waitsResponseWriter{ResponseWriter: w, request: r, wait: wait}
		next.ServeHTTP(delayed, r)
		delayed.release()
	})
}

// 待つ。待つ間に相手が切ったらfalse。goroutineを止めるだけで、OSのthreadは止めない。
func waitsSleep(r *http.Request, wait time.Duration) bool {
	timer := time.NewTimer(wait)
	defer timer.Stop()
	select {
	case <-timer.C:
		return true
	case <-r.Context().Done():
		return false
	}
}

// 応答を書き始める（header、本文、flush、hijack）前に1度だけ`after`の時間を待つResponseWriter。
type waitsResponseWriter struct {
	http.ResponseWriter
	request  *http.Request
	wait     time.Duration
	released bool
}

func (w *waitsResponseWriter) release() {
	if !w.released {
		w.released = true
		waitsSleep(w.request, w.wait)
	}
}

func (w *waitsResponseWriter) WriteHeader(status int) {
	// 1xxの中間応答は応答の始まりではないので、待たずに送る。
	if status < 200 && status != http.StatusSwitchingProtocols {
		w.ResponseWriter.WriteHeader(status)
		return
	}
	w.release()
	w.ResponseWriter.WriteHeader(status)
}

func (w *waitsResponseWriter) Write(body []byte) (int, error) {
	w.release()
	return w.ResponseWriter.Write(body)
}

func (w *waitsResponseWriter) ReadFrom(reader io.Reader) (int64, error) {
	w.release()
	if from, ok := w.ResponseWriter.(io.ReaderFrom); ok {
		return from.ReadFrom(reader)
	}
	return io.Copy(w.ResponseWriter, reader)
}

func (w *waitsResponseWriter) Flush() {
	w.release()
	_ = http.NewResponseController(w.ResponseWriter).Flush()
}

func (w *waitsResponseWriter) Hijack() (net.Conn, *bufio.ReadWriter, error) {
	w.release()
	return http.NewResponseController(w.ResponseWriter).Hijack()
}

// http.NewResponseControllerが元のResponseWriterへ届くように。
func (w *waitsResponseWriter) Unwrap() http.ResponseWriter {
	return w.ResponseWriter
}
