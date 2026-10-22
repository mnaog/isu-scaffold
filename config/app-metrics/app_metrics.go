// isuscopeのアプリ計測の書き手。
//
// HTTPとSQLの記録からは見えない、アプリの中の待ちを1秒ごとに集計して書く。isuscopeの
// `app-metrics` collectorがベンチの前後の差分を運び、`isuscope brief`の`app_calls`・`app_locks`欄と
// `isuscope query --metric-prefix app. --scope series --window load`で読む。
//
//   - 外への呼び出し（決済などの外部API、別nodeのサービス）: 試行ごとに`appMetricsCall`で記録する。
//     失敗して再試行した分も1回として数えるので、失敗率と再試行の回数がそのまま出る。
//   - lock: `appMetricsNewLock`で作った値をpackageの変数に置き、lockを取る処理を`acquire`で包む。
//     取れるまで待った時間と、持っていた時間（返したreleaseを呼ぶまで）を数える。
//
// 書き方の約束:
//
//   - リクエストを待たせない。lockの数はatomicに足すだけ、呼び出しは小さなmapへ足すだけで、
//     別のgoroutineが1秒ごとに取り出して書く。
//   - 書き先は環境変数`ISUSCOPE_APP_METRICS`（無ければ`/var/tmp/isuscope-app-metrics.jsonl`）。
//     空にすると何も数えず書かない（最終計測の前に止めるとき）。
//   - 標準libraryだけで動く。このfileをアプリの`main` packageのdirectoryへそのまま写して使う。中身は変えない。
//     名前はどれも`appMetrics`で始まるので、アプリの名前とぶつかりにくい。
//
// 使い方:
//
//	// 外への呼び出し。outcomeはstatus code、または"error"・"timeout"など。
//	started := time.Now()
//	response, err := client.Do(request)
//	outcome := "error"
//	switch {
//	case err == nil:
//		outcome = strconv.Itoa(response.StatusCode)
//	case errors.Is(err, context.DeadlineExceeded) || os.IsTimeout(err):
//		outcome = "timeout"
//	}
//	appMetricsCall("payment", outcome, time.Since(started))
//
//	// lock。sync.Mutexでも、sync.RWMutexの読み書きどちらでも、取る関数と放す関数を渡して包む。
//	var storeLock = appMetricsNewLock("store") // packageの変数として
//	release := storeLock.acquire(store.mu.Lock, store.mu.Unlock)
//	store.rides[id] = ride
//	release() // 持っていた時間はここまで（defer release()でもよい）
//	release = storeLock.acquire(store.mu.RLock, store.mu.RUnlock)
//	defer release()
//
// 1行の形（isuscopeとの約束、collectorの標準の形）: `{"type":"metric","name":"app.call.count",
// "value":12,"unit":"calls","labels":{"target":"payment","outcome":"502"},"timestamp":<Unix秒>}`。
// 名前は`app.call.{count,duration_sum,duration_max}`（label `target`・`outcome`）と
// `app.lock.{count,wait_sum,wait_max,hold_sum,hold_max}`（label `lock`）。時間はms。
// 1秒の間に1回も無かった対象は書かない。
package main

import (
	"fmt"
	"math"
	"os"
	"sort"
	"strconv"
	"sync"
	"sync/atomic"
	"time"
)

// isuscopeのcollectorが既定で読むpath。
const appMetricsDefaultPath = "/var/tmp/isuscope-app-metrics.jsonl"

// 書き先を変える、または空にして止める環境変数。
const appMetricsPathVariable = "ISUSCOPE_APP_METRICS"

const appMetricsFlushEvery = time.Second

// 外への呼び出しを1回（1試行）記録する。`target`は呼び先の名前（"payment"など、決まった値）、
// `outcome`はstatus codeか"error"・"timeout"など（`fmt.Sprint`で文字列にする。intのstatus codeも渡せる）。
// どちらも種類を少なく保つ（IDを入れない）。
func appMetricsCall(target string, outcome any, elapsed time.Duration) {
	if sink := appMetricsGlobal(); sink != nil {
		sink.call(target, fmt.Sprint(outcome), elapsed)
	}
}

// 待ちと保持の時間を数えるlock。`appMetricsNewLock`で作り、packageの変数に置いて使う。
type appMetricsLock struct {
	name       string
	registered atomic.Bool
	count      atomic.Uint64
	waitUs     atomic.Uint64
	waitMaxUs  atomic.Uint64
	holdUs     atomic.Uint64
	holdMaxUs  atomic.Uint64
}

func appMetricsNewLock(name string) *appMetricsLock {
	return &appMetricsLock{name: name}
}

// `lock`（lockを取る関数）を呼び、取れるまでの時間を数える。返した関数を呼ぶと`unlock`を呼んで
// 放し、その後で保持の時間を数える。返した関数は1度だけ呼ぶ。
func (l *appMetricsLock) acquire(lock, unlock func()) (release func()) {
	sink := appMetricsGlobal()
	if sink == nil {
		lock()
		return unlock
	}
	started := time.Now()
	lock()
	acquired := time.Now()
	l.waited(sink, acquired.Sub(started))
	return func() {
		unlock()
		l.held(time.Since(acquired))
	}
}

func (l *appMetricsLock) waited(sink *appMetricsSink, wait time.Duration) {
	if !l.registered.Swap(true) {
		sink.register(l)
	}
	micros := appMetricsMicros(wait)
	l.count.Add(1)
	l.waitUs.Add(micros)
	appMetricsStoreMax(&l.waitMaxUs, micros)
}

func (l *appMetricsLock) held(hold time.Duration) {
	micros := appMetricsMicros(hold)
	l.holdUs.Add(micros)
	appMetricsStoreMax(&l.holdMaxUs, micros)
}

func (l *appMetricsLock) take() ([5]uint64, bool) {
	values := [5]uint64{
		l.count.Swap(0),
		l.waitUs.Swap(0),
		l.waitMaxUs.Swap(0),
		l.holdUs.Swap(0),
		l.holdMaxUs.Swap(0),
	}
	for _, value := range values {
		if value > 0 {
			return values, true
		}
	}
	return values, false
}

func appMetricsStoreMax(target *atomic.Uint64, value uint64) {
	for {
		current := target.Load()
		if value <= current || target.CompareAndSwap(current, value) {
			return
		}
	}
}

type appMetricsCallKey struct {
	target  string
	outcome string
}

type appMetricsCallStats struct {
	count   uint64
	totalUs uint64
	maxUs   uint64
}

func appMetricsMicros(duration time.Duration) uint64 {
	if duration < 0 {
		return 0
	}
	return uint64(duration.Microseconds())
}

func appMetricsNowSeconds() float64 {
	now := time.Now()
	return float64(now.Unix()) + float64(now.Nanosecond())/1e9
}

// 環境変数の値から書き先を決める。空なら書かない（""を返す）。
func appMetricsPathFrom(value string, present bool) string {
	if !present {
		return appMetricsDefaultPath
	}
	return value
}

var (
	appMetricsOnce      sync.Once
	appMetricsSinkState *appMetricsSink
)

func appMetricsGlobal() *appMetricsSink {
	appMetricsOnce.Do(func() {
		if path := appMetricsPathFrom(os.LookupEnv(appMetricsPathVariable)); path != "" {
			appMetricsSinkState = appMetricsStart(path)
		}
	})
	if appMetricsSinkState == nil || appMetricsSinkState.closed.Load() {
		return nil
	}
	return appMetricsSinkState
}

type appMetricsSink struct {
	locksMu sync.Mutex
	locks   []*appMetricsLock
	callsMu sync.Mutex
	calls   map[appMetricsCallKey]*appMetricsCallStats
	closed  atomic.Bool
	stop    chan struct{}
	done    chan struct{}
}

func appMetricsNewSink() *appMetricsSink {
	return &appMetricsSink{
		calls: make(map[appMetricsCallKey]*appMetricsCallStats),
		stop:  make(chan struct{}),
		done:  make(chan struct{}),
	}
}

func appMetricsStart(path string) *appMetricsSink {
	sink := appMetricsNewSink()
	go sink.run(path)
	return sink
}

func (s *appMetricsSink) register(lock *appMetricsLock) {
	s.locksMu.Lock()
	s.locks = append(s.locks, lock)
	s.locksMu.Unlock()
}

func (s *appMetricsSink) call(target, outcome string, elapsed time.Duration) {
	micros := appMetricsMicros(elapsed)
	key := appMetricsCallKey{target, outcome}
	s.callsMu.Lock()
	stats := s.calls[key]
	if stats == nil {
		stats = &appMetricsCallStats{}
		s.calls[key] = stats
	}
	stats.count++
	if stats.totalUs > math.MaxUint64-micros {
		stats.totalUs = math.MaxUint64
	} else {
		stats.totalUs += micros
	}
	if micros > stats.maxUs {
		stats.maxUs = micros
	}
	s.callsMu.Unlock()
}

// この1秒の値を行にしてoutへ足し、数を0へ戻す。
func (s *appMetricsSink) drain(timestamp float64, out []byte) []byte {
	s.callsMu.Lock()
	calls := s.calls
	s.calls = make(map[appMetricsCallKey]*appMetricsCallStats)
	s.callsMu.Unlock()
	keys := make([]appMetricsCallKey, 0, len(calls))
	for key := range calls {
		keys = append(keys, key)
	}
	sort.Slice(keys, func(i, j int) bool {
		if keys[i].target != keys[j].target {
			return keys[i].target < keys[j].target
		}
		return keys[i].outcome < keys[j].outcome
	})
	for _, key := range keys {
		stats := calls[key]
		labels := [][2]string{{"target", key.target}, {"outcome", key.outcome}}
		out = appMetricsMetric(out, "app.call.count", float64(stats.count), "calls", labels, timestamp)
		out = appMetricsMetric(out, "app.call.duration_sum", appMetricsMs(stats.totalUs), "ms", labels, timestamp)
		out = appMetricsMetric(out, "app.call.duration_max", appMetricsMs(stats.maxUs), "ms", labels, timestamp)
	}
	s.locksMu.Lock()
	locks := append([]*appMetricsLock(nil), s.locks...)
	s.locksMu.Unlock()
	for _, lock := range locks {
		values, used := lock.take()
		if !used {
			continue
		}
		labels := [][2]string{{"lock", lock.name}}
		out = appMetricsMetric(out, "app.lock.count", float64(values[0]), "acquisitions", labels, timestamp)
		out = appMetricsMetric(out, "app.lock.wait_sum", appMetricsMs(values[1]), "ms", labels, timestamp)
		out = appMetricsMetric(out, "app.lock.wait_max", appMetricsMs(values[2]), "ms", labels, timestamp)
		out = appMetricsMetric(out, "app.lock.hold_sum", appMetricsMs(values[3]), "ms", labels, timestamp)
		out = appMetricsMetric(out, "app.lock.hold_max", appMetricsMs(values[4]), "ms", labels, timestamp)
	}
	return out
}

func (s *appMetricsSink) run(path string) {
	defer close(s.done)
	file, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_APPEND, 0o666)
	if err != nil {
		fmt.Fprintf(os.Stderr, "app_metrics: cannot open %s: %v; app metrics are not written\n", path, err)
		s.closed.Store(true)
		return
	}
	defer file.Close()
	var buffer []byte
	reported := false
	timer := time.NewTimer(appMetricsFlushEvery)
	defer timer.Stop()
	for {
		// 止める指示を待つ間も1秒ごとに書く。止めるときは最後の分を書いてから終わる。
		stopping := false
		select {
		case <-timer.C:
			timer.Reset(appMetricsFlushEvery)
		case <-s.stop:
			stopping = true
		}
		buffer = s.drain(appMetricsNowSeconds(), buffer[:0])
		if len(buffer) > 0 {
			if _, err := file.Write(buffer); err != nil && !reported {
				fmt.Fprintf(os.Stderr, "app_metrics: cannot write %s: %v\n", path, err)
				reported = true
			}
		}
		if stopping {
			return
		}
	}
}

// 残りを書いてgoroutineを止める（testで使う）。
func (s *appMetricsSink) close() {
	select {
	case <-s.stop:
	default:
		close(s.stop)
	}
	<-s.done
}

func appMetricsMs(micros uint64) float64 {
	return float64(micros) / 1000.0
}

func appMetricsMetric(out []byte, name string, value float64, unit string, labels [][2]string, timestamp float64) []byte {
	out = append(out, `{"type":"metric","name":"`...)
	out = append(out, name...)
	out = append(out, `","value":`...)
	out = strconv.AppendFloat(out, value, 'f', -1, 64)
	out = append(out, `,"unit":"`...)
	out = append(out, unit...)
	out = append(out, `","labels":{`...)
	for index, label := range labels {
		if index > 0 {
			out = append(out, ',')
		}
		out = appMetricsJSONString(out, label[0])
		out = append(out, ':')
		out = appMetricsJSONString(out, label[1])
	}
	out = append(out, `},"timestamp":`...)
	out = strconv.AppendFloat(out, timestamp, 'f', 3, 64)
	return append(out, "}\n"...)
}

func appMetricsJSONString(line []byte, value string) []byte {
	const hex = "0123456789abcdef"
	line = append(line, '"')
	for _, character := range value {
		switch {
		case character == '"':
			line = append(line, `\"`...)
		case character == '\\':
			line = append(line, `\\`...)
		case character == '\n':
			line = append(line, `\n`...)
		case character == '\r':
			line = append(line, `\r`...)
		case character == '\t':
			line = append(line, `\t`...)
		case character < 0x20:
			line = append(line, '\\', 'u', '0', '0', hex[character>>4], hex[character&0xf])
		default:
			line = append(line, string(character)...)
		}
	}
	return append(line, '"')
}
