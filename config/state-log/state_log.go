// isuscopeの状態ログの書き手。
//
// アプリの中の対象（注文、予約、配車など）が状態を変えるたびに、1行のJSONを書く。isuscopeの
// `state-log` collectorがベンチの前後の差分を運び、`isuscope brief`の`states`欄と
// `isuscope states sql`・`isuscope states timeline`で読む。
//
//   - リクエストを待たせない。行はqueue（channel）へ入れるだけで、別のgoroutineが100msごとにまとめて
//     取り出して書く。queueがあふれたら捨てて、捨てた数を`{"t":..,"dropped":n}`の行で残す。
//   - 書き先は環境変数`ISUSCOPE_STATE_LOG`（無ければ`/var/tmp/isuscope-state.jsonl`）。
//     空にすると何も書かない（最終計測の前に止めるとき）。
//   - 標準libraryだけで動く。このfileをアプリの`main` packageのdirectoryへそのまま写して使う。中身は変えない。
//     名前はどれも`stateLog`で始まるので、アプリの名前とぶつかりにくい。
//
// 使い方:
//
//	stateLogCreated("order", order.ID, "PENDING", stateLogAttr{"user", userID})
//	stateLogChanged("order", order.ID, "PENDING", "PAID", stateLogAttr{"amount", amount})
//	// 前の状態を知らないとき（状態の列を読まずにUPDATEするとき）。前の状態を読み足さない。
//	stateLogEntered("order", order.ID, "SHIPPED")
//
//	// transactionの中で変えるなら、commitが成功した後に書く。
//	var log stateLogPending
//	log.changed("order", order.ID, "PENDING", "PAID")
//	if err := tx.Commit(); err != nil {
//		return err // emitしなければ何も書かない
//	}
//	log.emit()
//
// 1行の形（isuscopeとの約束）: `{"t":<Unix時刻のマイクロ秒>,"seq":<process内の通し番号>,
// "kind":"..","id":"..","from":".."|null,"to":"..","attrs":{..}}`。作られたときは`"from":null`、
// 前の状態を知らないときは`from`を書かない。`attrs`は入れ子にしない。
package main

import (
	"fmt"
	"math"
	"os"
	"reflect"
	"strconv"
	"sync"
	"sync/atomic"
	"time"
)

// isuscopeのcollectorが既定で読むpath。
const stateLogDefaultPath = "/var/tmp/isuscope-state.jsonl"

// 書き先を変える、または空にして止める環境変数。
const stateLogPathVariable = "ISUSCOPE_STATE_LOG"

const (
	stateLogQueue      = 1 << 16
	stateLogFlushEvery = 100 * time.Millisecond
	stateLogFlushBytes = 1 << 20
)

// `attrs`の1つ。値は文字列、整数、小数、真偽値だけ（入れ子にしない）。
// 整数・小数・真偽値・文字列を元にした型（`type Status string`など）はその種類のまま書き、
// それ以外の値は`fmt.Sprint`した文字列で書く。
type stateLogAttr struct {
	Key   string
	Value any
}

// 対象が作られたときに書く（`"from":null`）。
func stateLogCreated(kind string, id any, to string, attrs ...stateLogAttr) {
	if writer := stateLogGlobal(); writer != nil {
		writer.send(stateLogNewEvent(kind, id, stateLogPreviousCreated, "", to, attrs))
	}
}

// 対象の状態が`from`から`to`へ変わったときに書く。
func stateLogChanged(kind string, id any, from, to string, attrs ...stateLogAttr) {
	if writer := stateLogGlobal(); writer != nil {
		writer.send(stateLogNewEvent(kind, id, stateLogPreviousKnown, from, to, attrs))
	}
}

// 前の状態を知らずに`to`へ変えたときに書く（`from`を書かない）。前の状態を読み足さないこと。
func stateLogEntered(kind string, id any, to string, attrs ...stateLogAttr) {
	if writer := stateLogGlobal(); writer != nil {
		writer.send(stateLogNewEvent(kind, id, stateLogPreviousUnknown, "", to, attrs))
	}
}

// transactionの中の変化をためておき、commitが成功した後に`emit`で書く。zero値のまま使う。
// commitせずに捨てれば（途中で`return`するなど）何も書かない。時刻は`emit`したとき。
// 1つの値を複数のgoroutineから同時に使わない。
type stateLogPending struct {
	events []stateLogEvent
}

func (p *stateLogPending) created(kind string, id any, to string, attrs ...stateLogAttr) {
	if stateLogGlobal() != nil {
		p.events = append(p.events, stateLogNewEvent(kind, id, stateLogPreviousCreated, "", to, attrs))
	}
}

func (p *stateLogPending) changed(kind string, id any, from, to string, attrs ...stateLogAttr) {
	if stateLogGlobal() != nil {
		p.events = append(p.events, stateLogNewEvent(kind, id, stateLogPreviousKnown, from, to, attrs))
	}
}

func (p *stateLogPending) entered(kind string, id any, to string, attrs ...stateLogAttr) {
	if stateLogGlobal() != nil {
		p.events = append(p.events, stateLogNewEvent(kind, id, stateLogPreviousUnknown, "", to, attrs))
	}
}

// ためた変化を書き、空にする。
func (p *stateLogPending) emit() {
	events := p.events
	p.events = nil
	if writer := stateLogGlobal(); writer != nil {
		now := stateLogNowMicros()
		for _, event := range events {
			event.t = now
			writer.send(event)
		}
	}
}

type stateLogPrevious int

const (
	stateLogPreviousCreated stateLogPrevious = iota
	stateLogPreviousKnown
	stateLogPreviousUnknown
)

type stateLogEvent struct {
	t        int64
	seq      int64
	kind     string
	id       string
	previous stateLogPrevious
	from     string
	to       string
	attrs    []stateLogAttr
}

func stateLogNewEvent(kind string, id any, previous stateLogPrevious, from, to string, attrs []stateLogAttr) stateLogEvent {
	var copied []stateLogAttr
	if len(attrs) > 0 {
		copied = append(copied, attrs...)
	}
	return stateLogEvent{
		t:        stateLogNowMicros(),
		kind:     kind,
		id:       fmt.Sprint(id),
		previous: previous,
		from:     from,
		to:       to,
		attrs:    copied,
	}
}

func (e *stateLogEvent) write(line []byte) []byte {
	line = append(line, `{"t":`...)
	line = strconv.AppendInt(line, e.t, 10)
	line = append(line, `,"seq":`...)
	line = strconv.AppendInt(line, e.seq, 10)
	line = append(line, `,"kind":`...)
	line = stateLogJSONString(line, e.kind)
	line = append(line, `,"id":`...)
	line = stateLogJSONString(line, e.id)
	switch e.previous {
	case stateLogPreviousCreated:
		line = append(line, `,"from":null`...)
	case stateLogPreviousKnown:
		line = append(line, `,"from":`...)
		line = stateLogJSONString(line, e.from)
	}
	line = append(line, `,"to":`...)
	line = stateLogJSONString(line, e.to)
	if len(e.attrs) > 0 {
		line = append(line, `,"attrs":{`...)
		for index, attr := range e.attrs {
			if index > 0 {
				line = append(line, ',')
			}
			line = stateLogJSONString(line, attr.Key)
			line = append(line, ':')
			line = stateLogAppendValue(line, attr.Value)
		}
		line = append(line, '}')
	}
	return append(line, "}\n"...)
}

func stateLogAppendValue(line []byte, value any) []byte {
	if value == nil {
		return append(line, "null"...)
	}
	if stringer, ok := value.(fmt.Stringer); ok {
		if _, plain := value.(string); !plain {
			return stateLogJSONString(line, stringer.String())
		}
	}
	reflected := reflect.ValueOf(value)
	switch reflected.Kind() {
	case reflect.String:
		return stateLogJSONString(line, reflected.String())
	case reflect.Int, reflect.Int8, reflect.Int16, reflect.Int32, reflect.Int64:
		return strconv.AppendInt(line, reflected.Int(), 10)
	case reflect.Uint, reflect.Uint8, reflect.Uint16, reflect.Uint32, reflect.Uint64, reflect.Uintptr:
		unsigned := reflected.Uint()
		if unsigned > math.MaxInt64 {
			unsigned = math.MaxInt64
		}
		return strconv.AppendInt(line, int64(unsigned), 10)
	case reflect.Float32, reflect.Float64:
		float := reflected.Float()
		if math.IsNaN(float) || math.IsInf(float, 0) {
			return append(line, "null"...)
		}
		return strconv.AppendFloat(line, float, 'f', -1, 64)
	case reflect.Bool:
		return strconv.AppendBool(line, reflected.Bool())
	default:
		return stateLogJSONString(line, fmt.Sprint(value))
	}
}

func stateLogJSONString(line []byte, value string) []byte {
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

func stateLogNowMicros() int64 {
	return time.Now().UnixMicro()
}

// 環境変数の値から書き先を決める。空なら書かない（""を返す）。
func stateLogPathFrom(value string, present bool) string {
	if !present {
		return stateLogDefaultPath
	}
	return value
}

var (
	stateLogOnce   sync.Once
	stateLogWriter *stateLogWriterState
)

func stateLogGlobal() *stateLogWriterState {
	stateLogOnce.Do(func() {
		if path := stateLogPathFrom(os.LookupEnv(stateLogPathVariable)); path != "" {
			stateLogWriter = stateLogStart(path, stateLogQueue)
		}
	})
	if stateLogWriter == nil || stateLogWriter.closed.Load() {
		return nil
	}
	return stateLogWriter
}

type stateLogWriterState struct {
	queue   chan stateLogEvent
	seq     atomic.Int64
	dropped atomic.Uint64
	closed  atomic.Bool
	stop    chan struct{}
	done    chan struct{}
}

func stateLogStart(path string, queue int) *stateLogWriterState {
	writer := &stateLogWriterState{
		queue: make(chan stateLogEvent, queue),
		stop:  make(chan struct{}),
		done:  make(chan struct{}),
	}
	go writer.run(path)
	return writer
}

func (w *stateLogWriterState) send(event stateLogEvent) {
	if w.closed.Load() {
		return
	}
	event.seq = w.seq.Add(1)
	select {
	case w.queue <- event:
	default:
		w.dropped.Add(1)
	}
}

// 残りを書いてgoroutineを止める（testで使う）。送り手が止まってから呼ぶ。
func (w *stateLogWriterState) close() {
	select {
	case <-w.stop:
	default:
		close(w.stop)
	}
	<-w.done
}

func (w *stateLogWriterState) run(path string) {
	defer close(w.done)
	file, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_APPEND, 0o666)
	if err != nil {
		fmt.Fprintf(os.Stderr, "state_log: cannot open %s: %v; state events are not written\n", path, err)
		w.closed.Store(true)
		return
	}
	defer file.Close()
	buffer := make([]byte, 0, stateLogFlushBytes)
	reported := false
	writeOut := func() {
		if len(buffer) == 0 {
			return
		}
		if _, err := file.Write(buffer); err != nil && !reported {
			fmt.Fprintf(os.Stderr, "state_log: cannot write %s: %v\n", path, err)
			reported = true
		}
		buffer = buffer[:0]
	}
	for {
		// 受け手を待たせておくと、送り手が1件ごとに受け手を起こす。間隔を空けてまとめて取り出せば、
		// 送り手はqueueへ入れるだけで済む。止める指示が来たら、残りを書いて終わる。
		stopping := false
		select {
		case <-time.After(stateLogFlushEvery):
		case <-w.stop:
			stopping = true
		}
	drain:
		for {
			select {
			case event := <-w.queue:
				buffer = event.write(buffer)
				if len(buffer) >= stateLogFlushBytes {
					writeOut()
				}
			default:
				break drain
			}
		}
		if lost := w.dropped.Swap(0); lost > 0 {
			buffer = append(buffer, `{"t":`...)
			buffer = strconv.AppendInt(buffer, stateLogNowMicros(), 10)
			buffer = append(buffer, `,"dropped":`...)
			buffer = strconv.AppendUint(buffer, lost, 10)
			buffer = append(buffer, "}\n"...)
		}
		writeOut()
		if stopping {
			return
		}
	}
}
