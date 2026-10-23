// isuscopeがGoのprofile（pprof）を負荷の区間で取るための窓口。
//
// perfはCPUの葉の関数しか分からず、Goのアプリでは「どのAPIの、どの呼び出しの連なりが」CPUを使ったか、
// どのmutexの取り合いで待ったかが見えない。Goのruntimeはそれを自分で数えられるので、ベンチの間だけ
// 取り、isuscopeの`go-profile` collectorが運ぶ。`isuscope brief`の`go_cpu`・`go_routes`・`go_mutex`・
// `go_allocs`欄に出る。取ったCPUのprofileはPGO（`make pgo RUN=...`）にもそのまま使える。
//
//   - 窓口は環境変数`ISUCON_PROFILE_ADDR`（無ければ`127.0.0.1:6060`）でだけ聞く。空にすると何もしない
//     （最終計測の前に止めるとき）。外へ開かない。同じnodeで2つ目のprocessが聞けなければ、標準エラーへ
//     書いて続ける（アプリは止めない）。
//   - `POST /debug/isuscope/start?run=<run ID>`: CPUのprofileを始め、mutex・block・割り当ての今の値を
//     起点として覚える。isuscopeがベンチの前に呼ぶ。
//   - `POST /debug/isuscope/stop?run=<run ID>`: CPUのprofileを止め、起点と今の値を返す。形は1行目が
//     `isuscope-go-profile 1 run=<ID> started=<Unix秒> stopped=<Unix秒>`、続く行が
//     `profile <種類> <gzipしたpprofのbase64>`（種類は`cpu`、`mutex-base`・`mutex`、`block-base`・`block`、
//     `allocs-base`・`allocs`、`goroutine`）。isuscopeが差を取る。startの無いstopや別のrunのstopは409。
//     startから15分たっても止められなければ、CPUのprofileは自分で止める。
//   - 標準の`/debug/pprof/`も同じ窓口に置く（手で`go tool pprof http://127.0.0.1:6060/...`をSSH越しに使える）。
//   - `GET /debug/isuscope/flight`: flight recorder（直前の実行の記録）を`go tool trace`の形で返す。
//     環境変数`ISUCON_FLIGHT_RECORDER`に秒数（例: 20）を入れたときだけ動く。Go 1.25より前では無い。
//
// mutexの取り合いは既定で20回に1回を数える（`ISUCON_PROFILE_MUTEX_FRACTION`、0で止める）。goroutineが
// 待った場所（block）は重いので既定では数えない（`ISUCON_PROFILE_BLOCK_RATE`にns、例: 1000000）。
//
// APIごとのCPU: routerの前に`profileMiddleware`を通すと、要求を処理するgoroutineのCPUのsampleに
// `method`と`route`のlabelが付く。routeはisuscopeの`routes suggest`と同じ規則で丸める（数字は`:id`、
// UUIDは`:uuid`、長いkeyは`:key`）。丸め方を決めたいときは`ISUCON_PROFILE_ROUTES`に`;`区切りで
// `/api/app/rides/:key/evaluation`のように書く（`:name`・`{name}`・`*`の区切りは任意の1区切りに合う）。
// 書いたものが先に合う。
//
//	r := chi.NewRouter()
//	r.Use(profileMiddleware) // waitsMiddlewareと並べてよい
//
// 標準libraryだけで動く。このdirectoryの`profile*.go`をアプリの`main` packageのdirectoryへそのまま写して
// 使う。中身は変えない。名前はどれも`profile`で始まる。
package main

import (
	"bytes"
	"context"
	"encoding/base64"
	"fmt"
	"io"
	"net"
	"net/http"
	httppprof "net/http/pprof"
	"os"
	"regexp"
	"runtime"
	"runtime/pprof"
	"strconv"
	"strings"
	"sync"
	"time"
)

const (
	profileDefaultAddress   = "127.0.0.1:6060"
	profileAddressVariable  = "ISUCON_PROFILE_ADDR"
	profileMutexVariable    = "ISUCON_PROFILE_MUTEX_FRACTION"
	profileBlockVariable    = "ISUCON_PROFILE_BLOCK_RATE"
	profileRoutesVariable   = "ISUCON_PROFILE_ROUTES"
	profileFlightVariable   = "ISUCON_FLIGHT_RECORDER"
	profileFlightMBVariable = "ISUCON_FLIGHT_RECORDER_MB"
	profileDefaultMutex     = 20
	profileLongestCapture   = 15 * time.Minute
)

// 差を取るprofile。CPU以外は起動からの累計なので、startの値を起点にする。
var profileCumulative = []string{"mutex", "block", "allocs"}

var (
	profileEnabled bool
	profileRoutes  []string
	profileState   profileCapture
)

type profileCapture struct {
	mu      sync.Mutex
	run     string
	started time.Time
	cpu     bytes.Buffer
	base    map[string][]byte
	timer   *time.Timer
	cpuOn   bool
}

func init() {
	address, present := os.LookupEnv(profileAddressVariable)
	if !present {
		address = profileDefaultAddress
	}
	if address == "" {
		return
	}
	runtime.SetMutexProfileFraction(profileInt(profileMutexVariable, profileDefaultMutex))
	runtime.SetBlockProfileRate(profileInt(profileBlockVariable, 0))
	profileRoutes = profileParseRoutes(os.Getenv(profileRoutesVariable))
	if seconds := profileInt(profileFlightVariable, 0); seconds > 0 {
		if err := profileFlightStart(time.Duration(seconds)*time.Second, uint64(profileInt(profileFlightMBVariable, 64))<<20); err != nil {
			fmt.Fprintf(os.Stderr, "profile: flight recorder: %v\n", err)
		}
	}
	listener, err := net.Listen("tcp", address)
	if err != nil {
		fmt.Fprintf(os.Stderr, "profile: cannot listen on %s: %v; profiles are not served\n", address, err)
		return
	}
	profileEnabled = true
	go func() {
		server := &http.Server{Handler: profileHandler(), ReadHeaderTimeout: 10 * time.Second}
		if err := server.Serve(listener); err != nil {
			fmt.Fprintf(os.Stderr, "profile: %v\n", err)
		}
	}()
}

func profileInt(name string, fallback int) int {
	value, err := strconv.Atoi(strings.TrimSpace(os.Getenv(name)))
	if err != nil {
		return fallback
	}
	return value
}

func profileHandler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/debug/pprof/", httppprof.Index)
	mux.HandleFunc("/debug/pprof/cmdline", httppprof.Cmdline)
	mux.HandleFunc("/debug/pprof/profile", httppprof.Profile)
	mux.HandleFunc("/debug/pprof/symbol", httppprof.Symbol)
	mux.HandleFunc("/debug/pprof/trace", httppprof.Trace)
	mux.HandleFunc("/debug/isuscope/start", profileStartHandler)
	mux.HandleFunc("/debug/isuscope/stop", profileStopHandler)
	mux.HandleFunc("/debug/isuscope/flight", profileFlightHandler)
	return mux
}

func profileSnapshot(name string) []byte {
	var buffer bytes.Buffer
	if profile := pprof.Lookup(name); profile != nil {
		_ = profile.WriteTo(&buffer, 0)
	}
	return buffer.Bytes()
}

func profileStartHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "POST only", http.StatusMethodNotAllowed)
		return
	}
	run := r.URL.Query().Get("run")
	if run == "" {
		http.Error(w, "run is required", http.StatusBadRequest)
		return
	}
	state := &profileState
	state.mu.Lock()
	defer state.mu.Unlock()
	// 前のrunが止めずに終わっていたら、その分は捨てる。
	state.stopCPU()
	state.base = map[string][]byte{}
	for _, name := range profileCumulative {
		state.base[name] = profileSnapshot(name)
	}
	state.cpu.Reset()
	if err := pprof.StartCPUProfile(&state.cpu); err != nil {
		state.run = ""
		http.Error(w, "cannot start the CPU profile: "+err.Error(), http.StatusConflict)
		return
	}
	state.cpuOn = true
	state.run = run
	state.started = time.Now()
	state.timer = time.AfterFunc(profileLongestCapture, func() {
		state.mu.Lock()
		defer state.mu.Unlock()
		if state.run == run {
			state.stopCPU()
		}
	})
	fmt.Fprintf(w, "started %s\n", run)
}

// muを持って呼ぶ。
func (s *profileCapture) stopCPU() {
	if s.timer != nil {
		s.timer.Stop()
		s.timer = nil
	}
	if s.cpuOn {
		pprof.StopCPUProfile()
		s.cpuOn = false
	}
}

func profileStopHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "POST only", http.StatusMethodNotAllowed)
		return
	}
	run := r.URL.Query().Get("run")
	state := &profileState
	state.mu.Lock()
	defer state.mu.Unlock()
	if state.run == "" || state.run != run {
		http.Error(w, fmt.Sprintf("no profile was started for run %q (current %q)", run, state.run), http.StatusConflict)
		return
	}
	state.stopCPU()
	stopped := time.Now()
	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	fmt.Fprintf(w, "isuscope-go-profile 1 run=%s started=%.3f stopped=%.3f\n", run, profileUnix(state.started), profileUnix(stopped))
	profileWriteLine(w, "cpu", state.cpu.Bytes())
	for _, name := range profileCumulative {
		profileWriteLine(w, name+"-base", state.base[name])
		profileWriteLine(w, name, profileSnapshot(name))
	}
	profileWriteLine(w, "goroutine", profileSnapshot("goroutine"))
	state.run = ""
	state.base = nil
	state.cpu.Reset()
}

func profileUnix(at time.Time) float64 {
	return float64(at.UnixNano()) / 1e9
}

func profileWriteLine(w io.Writer, kind string, data []byte) {
	if len(data) == 0 {
		return
	}
	fmt.Fprintf(w, "profile %s %s\n", kind, base64.StdEncoding.EncodeToString(data))
}

func profileFlightHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/octet-stream")
	if err := profileFlightWrite(w); err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
	}
}

// 要求を処理するgoroutine（とそこから起こしたgoroutine）のCPUに`method`・`route`のlabelを付ける。
// 窓口が動いていなければ何もしない。
func profileMiddleware(next http.Handler) http.Handler {
	if !profileEnabled {
		return next
	}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		labels := pprof.Labels("method", r.Method, "route", profileRoute(r.URL.Path))
		pprof.Do(r.Context(), labels, func(ctx context.Context) {
			next.ServeHTTP(w, r.WithContext(ctx))
		})
	})
}

func profileParseRoutes(spec string) []string {
	var routes []string
	for _, route := range strings.Split(spec, ";") {
		if route = strings.TrimSpace(route); strings.HasPrefix(route, "/") {
			routes = append(routes, route)
		}
	}
	return routes
}

// isuscopeの`routes suggest`と同じ並び。前から順に、区切り1つ全体が合うものを使う。
var profileSegments = []struct {
	pattern *regexp.Regexp
	label   string
}{
	{regexp.MustCompile(`^[0-9]+$`), ":id"},
	{regexp.MustCompile(`^[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,}$`), ":uuid"},
	{regexp.MustCompile(`^[0-9a-fA-F]{16,}$`), ":key"},
	{regexp.MustCompile(`^[A-Za-z0-9_-]{24,}$`), ":key"},
}

func profileRoute(path string) string {
	segments := strings.Split(path, "/")
	for _, route := range profileRoutes {
		if profileRouteMatches(route, segments) {
			return route
		}
	}
	for index, segment := range segments {
		for _, candidate := range profileSegments {
			if candidate.pattern.MatchString(segment) {
				segments[index] = candidate.label
				break
			}
		}
	}
	return strings.Join(segments, "/")
}

func profileRouteMatches(route string, segments []string) bool {
	parts := strings.Split(route, "/")
	if len(parts) != len(segments) {
		return false
	}
	for index, part := range parts {
		placeholder := part == "*" || strings.HasPrefix(part, ":") || (strings.HasPrefix(part, "{") && strings.HasSuffix(part, "}"))
		if placeholder {
			if segments[index] == "" {
				return false
			}
			continue
		}
		if part != segments[index] {
			return false
		}
	}
	return true
}
