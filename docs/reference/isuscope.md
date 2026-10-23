# isuscopeの使い方

isuscopeは、ベンチ1回ごとにscore、仮説、Gitの状態、HTTP・SQL・CPUの計測、状態ログ、会話の位置を1つのrunとして記録する自作CLIである。仕様とオプションは`isuscope --help`とrepo（github.com/mnaog/isuscope）のREADMEを正とする。設定は[自動化の仕様](automation.md)の手順で行い、`isuscope doctor`を通してから使う。

## 初回

初回ベンチも、以後と同じ`isuscope run`で取る（`make first-bench`が実行する）。初回だけの特別な観測は無い。

```bash
isuscope run --hypothesis "初期状態の負荷構造を記録する"
isuscope brief latest
isuscope query latest --metric-prefix benchmark. --limit 100
isuscope analyze RUN_ID supported --analysis "観測結果と判断"
```

初回runのHTTP routeに動的IDが残っている場合は、`isuscope routes suggest <run-id> --output .local/route-suggestions.toml`で候補を作る。確認したものだけ`.isuscope/routes.toml`へ移し、再計測する。

## 毎回

仮説付きのベンチと分析を1単位にする。比較元のrun（ふつうは最後に採用した構成のrun）は、ベンチの始めに`--base`で渡す（mainは`make bench BASE=<run>`）。不採用のrunを比較元にすると、戻した構成と比べることになる。渡さなかったときは、ベンチの間に`isuscope base running <run>`で決める。比較元の無いbriefは差を出さない。

```bash
isuscope run --base BASE_RUN --hypothesis "変更理由と改善を期待する観測値"
isuscope brief latest
isuscope query latest --base BASE_RUN --metric-prefix benchmark. --limit 100
isuscope analyze RUN_ID supported --analysis "観測結果と判断"
```

briefは、採否と次の制約を決めるための短い文で、行ごとに1つの問いに答える。上位だけを出すので、続きは`query`で読む。

| 行 | 答えること | 続き |
| --- | --- | --- |
| 1行目・`spread` | score・PASS/FAIL、比較元とのscoreの差、同じ構成で測ったscoreの幅 | `isuscope list` |
| `analysis` | 記録した最新の分析（記録した後に読むときだけ出る） | 行に添えた`full text`のcommand |
| `bench`・`fail` | ベンチのエラー・警告のcategoryごとの件数（比較元の件数）とinitializeの時間。比較元に無いcategoryの実例とFAILの理由 | `query <run> --metric-prefix benchmark.` |
| `counts` | 得点につながる行動の件数と比較元との差（`.isuscope/policy.toml`の`[brief]`で決めたもの） | `isuscope states sql` |
| `nodes` | nodeのCPU（平均/最大、最も忙しいcore）、上位serviceのcore数（比較元）、`rd`（serviceのthreadがCPUを待った平均の数） | `query <run> --metric-prefix host.`・`--metric-prefix service.` |
| `http` | 合計時間の上位3 route（回数、平均と比較元、p95）、`polling`（長く開くroute）、`static`（静的ファイルの合計） | `query <run> --view http` |
| `sql` | 負荷区間の合計時間の上位3つのSQL | `query <run> --view database --window load` |
| `app` | lockの待ち、外への呼び出しの試行数と結果の割合 | `query <run> --metric-prefix app. --scope series --window load` |
| `edge` | ベンチ側の接続数、upstreamの接続とheaderのp95 | `query <run> --metric-prefix client.`・`--metric-prefix http.upstream_` |
| `go` | Goのprofileから、CPUを使ったアプリの関数と種類の上位（profileがあるとき） | 下の「Goのアプリでは」 |
| `coverage`・`window`・`logs`・`limit` | 計測の欠け、負荷区間が分からずベンチ全体で集計したこと、logのエラー、当たった上限（あるときだけ） | `query <run> --metric log.error_lines`・`--metric-prefix limit.` |

- 仮説の対象は、`query --base`へ同じselectorを指定して比べる。HTTPは`--view http`と`--label route=...`、DBは`--view database --window load`（initializeを除いた負荷区間）、必要な`--label-contains digest=...`、`--group-by sql-shape`を使い、対象を絞らない巨大JSONを避ける。
- HTTPの合計時間には、応答まで要求を開いたままにするAPI（long polling、SSE）の開いている時間も積まれる。そうしたrouteを`.isuscope/policy.toml`の`[brief]`の`polling_routes`に書くと、briefの`http`は順位から外して`polling`の行に出す（書き方は`config/lanes/state-log.md`の手順8）。静的ファイルは1 fileずつ別のrouteなので、briefは合計を`static`の行に出す。速くて回数の多いAPIは合計時間の順に埋もれるので、回数の順と続きは`query --view http --sort count`で見る。
- 外への呼び出し（決済などの外部API）の失敗・再試行と、アプリの中のlockの待ちは、HTTPとSQLの記録に出ない。lane `state-log`が入れるアプリ計測（`config/app-metrics/app_metrics.go`）が1秒ごとに書き、briefの`app`の行（呼び先ごとの試行数と結果の割合、lockごとの待ち）に出る。journalctlやlogを足して数える前に、まずここを見る。比較は`query <run> --base <base> --metric-prefix app. --scope series --window load`。
- Goのアプリでは、CPUと待ちを関数とAPIまで絞れる。lane `state-log`が入れる`config/profile/profile*.go`の窓口から、isuscopeの`go-profile` collectorが負荷の区間のpprofを毎回運ぶ（CPUはベンチの前から取るのでinitializeの数秒も入る）。briefの`go`の行に、CPUを使ったアプリの関数（`cpu`）と種類（`classes`）の上位が出る。続きは次のmetricを`query <run> --metric <名前>`で読む。
  - `go.cpu.app_seconds`: CPUを、最も内側のアプリの関数（`main.`）へ寄せた秒数。標準libraryやruntimeの中の時間も呼んだ関数に入るので、直す関数がそのまま分かる。`go.cpu.flat_seconds`は葉の関数そのもの、`go.cpu.cum_seconds`は呼んだ先を含めた秒数。
  - `go.cpu.route_seconds`: APIごとのCPU（`profileMiddleware`のlabel）。`(no route)`は要求の外のgoroutine（GC、matcherのtickerなど）。
  - `go.cpu.class_seconds`: CPUの種類（`gc`・`alloc`・`lock`・`channel`・`scheduler`・`syscall`・`http`・`net`・`crypto`・`json`・`sql`・`log`・`app`・`stdlib`・`library`・`runtime`・`profiling`）ごとの秒数。
  - `go.mutex.delay_ms`・`go.mutex.contentions`: 取り合いになったlockを放したアプリの関数（持っていた側）と待たせた時間。`go.block.delay_ms`はgoroutineが待ったアプリの関数（既定では数えない。`ISUCON_PROFILE_BLOCK_RATE`）。briefの`app`の行のlockは包んだlockだけ、ここは包んでいないmutexも含む。
  - `go.alloc.bytes`: 割り当てたアプリの関数と量。`classes`の`gc`・`alloc`が大きいときに、減らす場所を探す。
  - `app.go.*`: アプリ計測の書き手が1秒ごとに書くGoのruntimeの値。GCのCPU（`app.go.gc_cpu_ms`）、アプリのCPU（`app.go.user_cpu_ms`）、全mutexの待ち（`app.go.mutex_wait_ms`）、goroutineがCPUを待った時間（`app.go.sched_wait`）、割り当て（`app.go.alloc_bytes`）、GCの回数と停止（`app.go.gc_cycles`・`app.go.gc_pause_max_ms`）、heap、goroutineの数。`query <run> --metric-prefix app.go. --scope series --window load`で読む。
  - 手で見るときは`scripts/ssh-node.sh <node> 'curl -s 127.0.0.1:6060/debug/pprof/goroutine?debug=1 | head'`のように標準のpprofを読める。負荷の途中の急な落ち込みは、アプリを`ISUCON_FLIGHT_RECORDER=<秒>`で起動し、isuscopeのcollector `go-flight`を有効にすると、負荷の最後の<秒>の実行の記録がrunのlogに残る（`go tool trace`で開く）。
  - APIからhandler・SQL・tableへの対応は`make codemap`（`CHECK=read-sql`で、全メモリ化の後に残った読み取りのSQL）。
- CPUの使用率に余裕があるのに遅いときは、briefの`nodes`の`rd`（serviceのthreadがCPUを待った平均の数）と、nodeの`host.procs_running`（`query <run> --metric host.procs_running --window load`）を見る。threadの数がコアより多いと、lockを持ったままpreemptされて待ちが延びる。
- DBの比較では、`query --base`の`total_ms_delta_by_calls`（回数の増減による分）と`total_ms_delta_by_avg`（1回あたりの時間による分）で、合計時間の変化の理由を分ける。
- 判定には`supported`、`rejected`、`inconclusive`、`skipped`を使う。`analyze`には、実行結果か`isuscope list`で得たrun IDを明示する。本文でrunに触れるときは、isuscopeの出力が示す短縮ID（run IDの末尾8文字）にそろえる。
- PASSしたrunは、分析を記録するまで次のベンチを開始できない。FAILまたは中断したrunに分析は不要。
- 変更を残すか戻すかを決めたら、分析と同時に`--change <変更ID> --decision <accepted|provisional|rejected|deferred>`で記録する（`provisional`は`--revisit`必須）。仮説の判定と変更の採否は別に扱い、本文に「採用」と書くだけで済ませない。
- 同じ配布物・計測設定のrunは、isuscopeが始めない。意図して測り直すときは`isuscope run --repeat "理由"`を付ける（いつ測り直すかは`/main`と`docs/guides/finish.md`）。

## 原因を探す

- 初期化の失敗やエラー・遅延の原因は、SSHで手調べする前に、次を`query <run> --metric <名前>`で見る（briefの`logs`の行は最も多いエラーだけ）。どれもinitializeを含む。
  - `log.error_lines`: ベンチの間にアプリ・nginxのerror log・kernelが出したエラーを、型ごとに数えたもの
  - `db.file.read_wait`・`db.file.write_wait`・`db.file.misc_wait`（`--group-by file`）: MySQLがfileの読み書きで待った時間
  - `db.memory.buffer_pool`・`db.memory.tables`: buffer poolとtableの大きさ
- FAILしたrunの理由と、エラー・警告の実例は、`.isuscope/parse-benchmark.sh`がベンチの出力から`message`として残す。briefの`fail`の行と`isuscope list`の`failure`で読む。briefの`bench`の行は、categoryごとの件数を比較元の件数と並べ、比較元に無いcategoryは実例も1行出す。警告とエラーはcategory（`CODE=30`など）ごとの件数も`benchmark.warnings`・`benchmark.errors`に出るので、警告が上限に近づいていないかは生出力をgrepせず`isuscope query <run> --base <base> --metric-prefix benchmark.`で比べる。boardでは「ベンチ特有の出力」に並ぶ。判断材料とmessageには、適用されるルールで参加者の利用が認められた出力だけを使う。
- ベンチ側の動き（誰が、何の後に、どれくらいの間隔で次の要求を出すか）は、毎回のrunの行動遷移（`isuscope query RUN --view transitions`。遷移元→遷移先ごとの回数と、要求の開始から次の要求の開始までの間隔のp50/p95）で読む。応答の速さや内容を変えた回は、`isuscope query RUN --base BASE --view transitions`で前のrunと比べ、ベンチの反応が変わっていないか確かめる（割合の変化が大きい順に並び、消えた・現れた遷移も出る。`--label from=<route>`・`--label to=<route>`で絞る）。遷移に出ない見方（応答から次の要求までの時間、statusやsizeでの分岐、時刻ごとの主体の数）は、run directoryの`logs/nginx-log-raw-<node>-after-stdout.zst`（生のaccess log。`session`・`msec`・`reqtime`付き）を集計する。
- アプリの中の状態（対象がどの状態にいて、いつ変わったか）は、lane `state-log`が入れた状態ログ（`docs/state-log.md`）が毎回のrunに残している。briefの`counts`の行（得点につながる行動の件数）、`isuscope states sql`、`isuscope states timeline`で読む。状態を変える処理を書き換えるときは、呼び出しも一緒に移す。
- 状態は`isuscope states sql latest "SELECT ..."`（表は`--schema`）で集計し、1つの対象の前後は`isuscope states timeline latest --kind <種類> --id <ID> --related`で時刻順に並べる（`--around <時刻>`で前後に絞る）。1つのSQLは30秒で止まる。止まったら、行ごとに数え直す相関サブクエリをやめ、示された書き方（attrsで結ぶなら`state_attrs`の索引、ある状態にいる数の推移なら累積和）に直す。
- より自由な分析や比較には`isuscope sql`（table定義は`--schema`）を使う。時系列は`isuscope series`で見る。接続は読み取り専用で、データの場所は設定から解決する。

## ログの残し方

- 各runの`logs/*.zst`（生のaccess log、状態ログ、ベンチ出力など）は、ベンチの反応を後から比べる証拠なので全runを手元に残す。`logs/`はGitに入れず、重要なrunだけ`git add -f isuscope-data/runs/<run-id>`で残す。生のaccess logは1 runで約12MB（2 node）。
- 状態ログのcache（`cache/states/`）は使い捨てのSQLiteで、isuscopeが最近使ったrunの分だけ残し、無ければ保存したログから作り直す。ベンチを起動するとhookが`scripts/isuscope-prune.py`で古いisuscopeが残したrunごとのcache（`runs/<id>/logs/.state-cache/`）を消し、`isuscope-data`をSpotlightとTime Machineの対象から外す。

## 会話との紐付け

isuscopeは各runを、ベンチを起動したClaude Codeのセッションの会話（`docs/agent-history/`）へ結び付ける。runの`agent_context`から、そのベンチを回した会話の位置が分かる。`[context.agent]`を有効にした後のベンチは、このrepoで起動した現在のセッションから実行する。

## 終了前

終了前の手順（計測を外した構成、確認のベンチ）は[終了前](../guides/finish.md)にある。
