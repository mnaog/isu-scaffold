# lane: runtime

あなたは`make kickoff`が先行回収したGoアプリに、問題に依存しない実行時の設定（log、DB接続、GC）の修正を先行して実装する。SQLと処理の中身はlane `indexes`・`n-plus-one`が、全メモリ化はlane `full-memory`が同時に行う。mainのセッションは同時に環境構築と初回ベンチ（baseline）を進め、baselineの観測と照合してから、このbranchのcommitを選んで取り込み、deployして測る。どれも数行の変更なので、速さを優先して短く終える（時間の上限は20分）。

## 前提

- 作業場所はこのworktreeとbranchだけ。`AGENTS.md`の規則に従う（練習中で`practice.md`があればそれにも従う）。remote node・deploy・ベンチ・isuscopeの記録操作・`.local/operation.lock`には触れない（hookが止める）。baseline前の変更をremoteへ反映しない規則は、mainが取り込み時に守る。
- 変更してよいのは`webapp/`のアプリのコード（起動処理、`go.mod`と`go.sum`）だけ。schema、`config/`、`scripts/`、他の文書は変えない。
- 他のlaneも起動処理（`main.go`など）に行を足す。lane `waits`はrouterに待ちのmiddlewareを足すので、それには触らず、外すのはrequestごとのlogのmiddlewareだけにする。mergeで衝突しにくいよう、直すのに要る行だけを変え、周りを書き換えない（整形し直さない、`gofmt`をpackage全体へかけない）。

## 対象（1件1commit、上から順に）

1. **requestごとのlog。** requestごとに1行以上書いている（echoの`middleware.Logger()`、chiの`middleware.Logger`、handler内の`log.Printf`・`fmt.Println`、debug levelの`slog`など）なら、requestごとのmiddlewareを外し、handler内の毎回のlogを止める。errorのlogと、起動時に1回だけ出るlogは残す。isuscopeの状態ログ（lane `state-log`が足す`stateLog…`の呼び出し）は別の書き手なので触らない。echoの`e.Debug = true`も止める。
2. **DB接続。** `database/sql`の接続poolの上限（`SetMaxOpenConns`）が無いか、同時に来るrequestの数より明らかに小さいなら設定し、`SetMaxIdleConns`を同じ値にして、使い終えた接続を閉じて開き直さないようにする。上限はMySQLの`max_connections`（`make tare`で1000）とアプリのprocess数の積が超えない値にし、値と理由をcommit本文に書く。go-sql-driver/mysqlのDSNに`interpolateParams=true`が無ければ足す（placeholderつきのqueryごとのprepareとcloseの往復が無くなる）。DSNの`tls`が有効なら、同じVPCの中なので外す。
3. **GC。** 起動時に`debug.SetGCPercent`を既定（100）より大きくする（400程度）。環境変数`GOGC`で戻せる形にし（`GOGC`が設定されていれば何もしない）、値をcommit本文に書く。`GOMEMLIMIT`は入れない（nodeのメモリはDBと分け合う）。

上の3つ以外（goroutineの数、HTTP clientの使い回し、JSONのencoderの変更、mapの事前確保など）は実装しない。このlaneはまだ計測の無い時点で動くため。計測を見て選ぶ手（割り当て、lockの範囲、unix socket、JSONの組み立て、PGO）は、improveがbriefの`go`の行とGoのprofileのmetric（`go.*`・`app.go.*`）を根拠に作る（`.claude/commands/improve.md`の「Goで足しやすい手」）。HTTP serverのTCP_NODELAYはGoの既定で付いているので何もしない。buildをCPUに合わせる設定（`GOAMD64=v3`など）は入れない。nodeの型が混ざる構成（c7iとc6in、同じ型でも世代の違うCPU）では、builderと違うCPUのnodeでSIGILLで落ちる（`scripts/local-build.py`は`GOAMD64`を外してbuildする）。

## 守ること

- **応答を変えない。** 返す値・件数・並び順・エラーとstatus codeを元のコードと一致させる。
- **1つの修正を1つのcommitにする。** mainが選んで取り込めるように、commit同士の依存を避ける。該当しない項目はcommitしない。commit messageは英語で、1行目に変更、本文に直した箇所（`file:line`）と期待する観測値（どのnodeのどのCPU・待ちが減るか）を書く。
- commitの前に、`webapp/go`で`go build ./...`と`go vet ./...`、既存のtest（`go test ./...`）を通す。`config/local/compose.yaml`があれば`make local-up`と`make local-check`で動作も確かめ、終わったら`make local-down`する。確かめられなかったことはcommit本文に書く。

## 終わり方

最後の回答は日本語で、次を短く並べる。

1. commitごとに: hash、変更、期待効果、リスク、確認したこと
2. 該当しなかった項目（例: 既にwarn、frameworkが既にnodelay）と、実装しなかった候補とその理由・根拠の`file:line`

mainのセッションは`git log lane/runtime`とこの回答を読み、baselineの観測と照合して取り込む。
