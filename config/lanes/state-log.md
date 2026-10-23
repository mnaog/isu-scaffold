# lane: state-log

あなたは`make kickoff`が先行回収したアプリに、isuscopeの状態ログを入れる。状態ログは、アプリの中の対象（注文、予約、配送など）が状態を変えるたびに書く1行のJSONで、isuscopeが毎回のrunで運ぶ。mainのセッションはbaselineの後の最初のdeployでこのbranchを取り込み、以後のrunごとに`isuscope brief`の`counts`の行（手順8で決める件数）、`isuscope states sql`・`states timeline`で、状態にいた時間、待っている対象と空いている対象の数、ある対象の前後の変化を、計測を足すrunなしで読む。価値は、挙動と速さを変えずに、状態の変化を漏れなく書くこと。

## 前提

- 作業場所はこのworktreeとbranchだけ。`AGENTS.md`の規則に従う（練習中で`practice.md`があればそれにも従う）。remote node・deploy・ベンチ・isuscopeの記録操作・`.local/operation.lock`には触れない（hookが止める）。baseline前の変更をremoteへ反映しない規則は、mainが取り込み時に守る。
- 変更してよいのは`webapp/`のアプリのコード、`docs/state-log.md`、`.isuscope/policy.toml`の`[brief]`だけ。schema（`webapp/sql`）、`config/`、`scripts/`、他の文書、`.isuscope/`の他の設定は変えない。
- lane `indexes`・`n-plus-one`・`runtime`が同時に同じコードを変える。mergeで衝突しにくいよう、変更は書き手のmoduleの追加と呼び出しの追加だけにし、周りの行を書き換えない（整形し直さない、`gofmt`をpackage全体へかけない）。

## 書き手

- `config/state-log/state_log.go`をアプリの`main` packageのdirectoryへそのまま写す（`cp config/state-log/state_log.go webapp/go/state_log.go`。`main.go`の場所が違えばそこへ）。中身は変えない。標準libraryだけで書いてあり、追加のmoduleは要らない。
- 使い方はfile冒頭のコメントのとおり。`stateLogCreated`（作られた）、`stateLogChanged`（前の状態から変わった）、`stateLogEntered`（前の状態を知らずに変えた）、transactionの中なら`var log stateLogPending`へ`log.created`などでためてcommitの後に`log.emit()`。
- 書き手はリクエストを待たせない（queueへ入れるだけで、あふれたら捨てて数える）。書き先は`ISUSCOPE_STATE_LOG`（無ければ`/var/tmp/isuscope-state.jsonl`、空なら書かない）。
- アプリ計測の書き手`config/app-metrics/app_metrics.go`も同じように写す（`cp config/app-metrics/app_metrics.go webapp/go/app_metrics.go`）。中身は変えない。外への呼び出し（決済などの外部API、別のサービス）の1試行ごとの結果と時間と、lockの待ちと保持の時間を1秒ごとに集計して書く（書き先は`ISUSCOPE_APP_METRICS`、無ければ`/var/tmp/isuscope-app-metrics.jsonl`、空なら書かない）。HTTPとSQLの記録には、外への呼び出しの失敗・再試行も、アプリの中のlock待ちも出ないので入れる（practice-14では、決済の約70%の失敗と再試行、グローバルlockの待ちが、毎回journalctlと手で足したlogでしか見えなかった）。この書き手は起動時に始まり、Goのruntimeの値（GCのCPU、全mutexの待ち、goroutineが実行を待った時間、割り当て、goroutineの数）も1秒ごとに書く。そのための呼び出しは要らない。
- Goのprofileの窓口`config/profile/`の`profile*.go`（3つ）も同じように写す（`cp config/profile/profile*.go webapp/go/`）。中身は変えない。`ISUCON_PROFILE_ADDR`（無ければ`127.0.0.1:6060`、空なら何もしない）だけで聞き、isuscopeがベンチの前後に呼んで、負荷の区間のCPU（関数ごと、アプリの関数ごと、APIごと）、mutexの取り合い、割り当てを運ぶ。APIごとのCPUを出すため、routerの前に`profileMiddleware`を通す。

## 手順

1. **状態を持つ対象を探す。** 状態を表す列（status、state、phaseなど）や状態ごとの表を持つtable、それを変えるSQL（`UPDATE ... SET status`、状態の履歴表へのINSERT、行の作成）と、メモリに持つ状態を変える処理。対象の種類（kind。tableの単数形など）と状態の名前を、アプリが使う値のまま書き出す。決まった値をとる状態だけを対象にし、座標・残高・件数のように値が連続する、または回数の多い更新は対象にしない。
2. **状態を変える箇所すべてに呼び出しを入れる。** 作る箇所、変える箇所、消す箇所（消えた状態として`to`に書く）。同じ変化を2か所で書かない。
   - 前の状態がコードの中で分かっていれば（条件で確かめた、直前に読んだ）`changed`。分からなければ`entered`にする。**前の状態を知るためにSQLや処理を足さない。** isuscopeが前の行から補う。
   - DBのtransactionの中で変えるなら、`Pending`へためて、commitが成功した後に`emit()`する。commitに失敗した変化を書かないため。
   - `attrs`には、関係する対象のID（別のkindの対象。`stateLogAttr{"order", orderID}`のように、そのkindの名前をkeyにする）と、集計に使いそうな数値（距離、金額、件数など）を少しだけ入れる。入れ子にしない。新しく計算しない（手元にある値だけ）。
   - initializeの処理（データの入れ直し）では書かない。初期データの対象は、最初に変わった行の`from`で状態が分かる。
3. **応答と速さを変えない。** 返す値・並び順・status code・SQLの回数を変えない。呼び出しはlockを持つ範囲の外へ置ける場合は外へ置く。
4. **外への呼び出しとlockを測る（`app_metrics`）。**
   - 外への呼び出し: アプリが外へ出すHTTP（`net/http`のclientなど）やgRPCの呼び出し箇所をすべて探し、1試行ごとに`appMetricsCall("<呼び先の名前>", outcome, time.Since(started))`を足す。outcomeはstatus code（`res.StatusCode`）、通信の失敗なら`"timeout"`か`"error"`。再試行の輪の中なら試行ごとに書く。呼び先の名前は決まった短い語（`payment`など）にし、IDやURLを入れない。DB（`database/sql`・sqlx）への呼び出しは対象にしない（SQLはisuscopeが測る）。
   - lock: アプリのコードにある`sync.Mutex`・`sync.RWMutex`で、requestの経路で取るものを、package変数`var <名前>Lock = appMetricsNewLock("<名前>")`と`release := <名前>Lock.acquire(mu.Lock, mu.Unlock)`・`release()`で包む（`RLock`・`RUnlock`も同じ）。`defer mu.Unlock()`なら`defer release()`に替える。`sync.Cond`に渡すlockのように、Lock・Unlockを別の場所が呼ぶものは包まずに残して理由を書く。採番だけのような、requestの経路に無いlockは包まない。
   - 応答・SQLの回数・lockの範囲を変えない。包むためにlockを取る順番やguardを持つ範囲を変えない。
5. **profileの窓口をつなぐ。** routerを作った直後（他の`Use`と並べて、routeを足す前）に`r.Use(profileMiddleware)`を足す（chiの場合。echoなら`e.Use(echo.WrapMiddleware(profileMiddleware))`、net/httpだけならserverへ渡すhandlerを`profileMiddleware(mux)`で包む）。窓口のserverは`profile.go`の`init`が自分で立てるので、`main`には何も足さない。
6. **確かめる。** `webapp/go`で`go build ./...`と`go vet ./...`、既存のtest（`go test ./...`）を通す。`config/local/compose.yaml`があれば`make local-up`で動かし、`ISUSCOPE_STATE_LOG`と`ISUSCOPE_APP_METRICS`を確認できる場所へ向けて、代表的な操作で状態ログが1行以上出ることと形、外への呼び出しのある操作で`app.call.*`、lockを取る操作で`app.lock.*`の行が出ることを確かめ（どちらも1秒ごとに書かれる）、`make local-down`する。`app.go.goroutines`などruntimeの行も出ること、container内で`curl -s -X POST '127.0.0.1:6060/debug/isuscope/start?run=check'`の後に要求を送り、`.../stop?run=check`が`isuscope-go-profile 1`で始まる行を返すことも確かめる。確かめられなかったことは`docs/state-log.md`とcommit本文に書く。
7. **`docs/state-log.md`を書く。** 対象の種類ごとに、状態の一覧、遷移と書いている箇所（関数と`file:line`、`changed`・`entered`・`created`のどれか）、`attrs`のkey。書かなかった状態の変化と理由（前の状態が分からず`entered`にした箇所も）。mainと、後で状態の処理を書き換える人が、呼び出しを残すための表にする。
8. **briefに出す件数とpollingのAPIを書く。** `.isuscope/policy.toml`の末尾に`[brief]`を足す。isuscopeは状態ログに何が書かれているかは知っていても、どれが得点につながるかと、どのAPIが応答を長く待たせるかは知らないので、このlaneが決める。`[brief]`はrunの構成（`config_key`）に入らないので、後から直しても同じ構成どうしの比較は崩れない。
   - `[[brief.counts]]`: mainは毎回のrunで、得点につながる行動（作成・完了・購入・予約など）の件数を比較元のrunと比べて、採否と制約の場所（速くしたのに件数が減った、など）を決める。briefの`counts`の行は、ここに書いた件数を状態ログから数え、比較元との差を付けて出す。書かなければこの行は出ず、mainが`isuscope states sql`で毎回数えることになる。手順1〜2で入れた対象から、公式資料（`docs/official/`）とアプリのコードで得点や成否につながると読める作成と状態を選び、briefの1行に収まる3〜4個にする。`label`は短い名前、`kind`と`state`は状態ログに書く値のまま。`state`を省くと作られた数、書くとその状態に入った回数になる。別の対象の状態をなぞるだけの対象（通知など）は、同じ数が並ぶだけなので入れない。
   - `polling_routes`: 応答を返すまで要求を開いたままにするAPI（long polling、SSEなど）がある場合だけ書く。briefのHTTPは合計時間の上位3行なので、開いている時間がそのまま積まれるこうしたAPIが上位を占め、ほかのAPIが見えなくなる。briefはここに書いたrouteを順位から外し、件数と平均だけを別の行に出す。待たせる処理（sleepや待ちのloop、`text/event-stream`、返すまでのchannel待ち）を持つhandlerから探し、methodと、`.isuscope/routes.toml`で置き換えた後のroute（`"GET /api/notifications"`の形）で書く。すぐ返すpollingは合計時間の上位を占めないので書かない。無ければ`polling_routes`を書かない。
   - `python3 -c 'import tomllib; tomllib.load(open(".isuscope/policy.toml", "rb"))'`で読めることを確かめる。

   ```toml
   [brief]
   polling_routes = ["GET /api/notifications"]
   [[brief.counts]]
   label = "order created"
   kind = "order"
   [[brief.counts]]
   label = "order DELIVERED"
   kind = "order"
   state = "DELIVERED"
   ```

## commit

1. 書き手のmoduleの追加（`state_log.go`・`app_metrics.go`・`profile*.go`）と、`profileMiddleware`の差し込み。
2. 状態ログの呼び出しの追加と`docs/state-log.md`、`.isuscope/policy.toml`の`[brief]`。
3. 外への呼び出しとlockの計測（`app_metrics`）と、`docs/state-log.md`の「アプリ計測」の節（呼び先の名前と箇所、包んだlockの名前と箇所、包まなかったlockと理由）。

commit messageは英語で、1行目に変更、本文に対象の種類と状態、確かめたこと。

## 終わり方

最後の回答は日本語で、次を短く並べる。

1. commitごとに: hash、変えたfile、確かめたこと
2. 対象の種類ごとの状態と遷移の一覧（`entered`にした箇所はそう書く）
3. 外への呼び出しの呼び先と箇所、包んだlockと包まなかったlock（理由）、`profileMiddleware`を差し込んだ箇所
4. 書かなかった状態の変化と理由、mainが取り込み時に気をつけること（lockの中に残した呼び出し、他のlaneと触る関数が重なりそうな箇所）
5. `[brief]`に書いた件数（得点につながると読んだ根拠）とpollingのAPI（無ければ無いと書く）

