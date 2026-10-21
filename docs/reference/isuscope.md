# isuscopeの使い方

isuscopeは、ベンチ1回ごとにscore、仮説、Gitの状態、HTTP・SQL・CPUの計測、状態ログ、会話の位置を1つのrunとして記録する自作CLIである。仕様とオプションは`isuscope --help`とrepo（github.com/mnaog/isuscope）のREADMEを正とする。設定は[自動化の仕様](automation.md)の手順で行い、`isuscope doctor`を通してから使う。

## 初回

`survey-run`は開始後の初回調査に使う（`make first-bench`が実行する）。観測は`run`と同じで、違うのは、問題に合わせて有効にした調査用のcollector（`score-probe`など）がsurvey-runでだけ動くことである。その後は、構成やroutingを大きく変えた場合も`run`を使う。

```bash
isuscope survey-run --hypothesis "初期状態の負荷構造を記録する"
isuscope brief latest
isuscope query latest --metric-prefix benchmark. --limit 100
isuscope analyze RUN_ID supported --analysis "観測結果と判断"
```

初回runのHTTP routeに動的IDが残っている場合は、`isuscope routes suggest <run-id> --output .local/route-suggestions.toml`で候補を作る。確認したものだけ`.isuscope/routes.toml`へ移し、再計測する。

## 毎回

仮説付きのベンチと分析を1単位にする。

```bash
isuscope run --hypothesis "変更理由と改善を期待する観測値"
isuscope brief latest
isuscope query latest --base BASE_RUN --metric-prefix benchmark. --limit 100
isuscope analyze RUN_ID supported --analysis "観測結果と判断"
```

- 仮説の対象は、`query --base`へ同じselectorを指定して比べる。HTTPは`--view http`と`--label route=...`、DBは`--view database --window load`（initializeを除いた負荷区間）、必要な`--label-contains digest=...`、`--group-by sql-shape`を使い、対象を絞らない巨大JSONを避ける。
- DBの比較では、`query --base`の`total_ms_delta_by_calls`（回数の増減による分）と`total_ms_delta_by_avg`（1回あたりの時間による分）で、合計時間の変化の理由を分ける。
- 判定には`supported`、`rejected`、`inconclusive`、`skipped`を使う。`analyze`には、実行結果か`isuscope list`で得たrun IDを明示する。本文でrunに触れるときは、isuscopeの出力が示す短縮ID（run IDの末尾8文字）にそろえる。
- PASSしたrunは、分析を記録するまで次のベンチを開始できない。FAILまたは中断したrunに分析は不要。
- 変更を残すか戻すかを決めたら、分析と同時に`--change <変更ID> --decision <accepted|provisional|rejected|deferred>`で記録する（`provisional`は`--revisit`必須）。仮説の判定と変更の採否は別に扱い、本文に「採用」と書くだけで済ませない。
- 同じ配布物・計測設定のrunは、isuscopeが始めない。測り直す（`isuscope run --repeat "理由"`）のは、mainが空き時間に最新の採用構成の振れ幅を調べるときと終了前の確認だけ。振れ幅は、同じ構成で測ったrunの得点を見て読む。

## 原因を探す

- 初期化の失敗やエラー・遅延の原因は、SSHで手調べする前に`brief`の次の欄を見る。どれもinitializeを含む。
  - `logs`: ベンチの間にアプリ・nginxのerror log・kernelが出したエラーを、型ごとに数えたもの
  - `database_io`: MySQLがfileの読み書きで待った時間
  - `database_memory`: buffer poolとtableの大きさ
- FAILしたrunの理由とエラーの実例は、`.isuscope/parse-benchmark.sh`がベンチの出力から`message`として残す。`isuscope list`の`failure`と、`brief`の`benchmark_messages`で読む。判断材料とmessageには、適用されるルールで参加者の利用が認められた出力だけを使う。
- ベンチ側の動き（誰が、何の後に、どれくらいの間隔で次の要求を出すか）は、毎回のrunの行動遷移（`brief`の`transitions`。遷移元→遷移先ごとの回数と、要求の開始から次の要求の開始までの間隔のp50/p95）で読む。応答の速さや内容を変えた回は、`isuscope query RUN --base BASE --view transitions`で前のrunと比べ、ベンチの反応が変わっていないか確かめる（割合の変化が大きい順に並び、消えた・現れた遷移も出る。`--label from=<route>`・`--label to=<route>`で絞る）。遷移に出ない見方（応答から次の要求までの時間、statusやsizeでの分岐、時刻ごとの主体の数）は、run directoryの`logs/nginx-log-raw-<node>-after-stdout.zst`（生のaccess log。`session`・`msec`・`reqtime`付き）を集計する。
- アプリの中の状態（対象がどの状態にいて、いつ変わったか）は、lane `state-log`が入れた状態ログ（`docs/state-log.md`）が毎回のrunに残している。`brief`の`states`欄、`isuscope states sql`、`isuscope states timeline`で読む。状態を変える処理を書き換えるときは、呼び出しも一緒に移す。
- より自由な分析や比較には`isuscope sql`（table定義は`--schema`）を使う。接続は読み取り専用で、データの場所は設定から解決する。

## ログの残し方

- 各runの`logs/*.zst`（生のaccess log、状態ログ、ベンチ出力など）は、ベンチの反応を後から比べる証拠なので全runを手元に残す。`logs/`はGitに入れず、重要なrunだけ`git add -f isuscope-data/runs/<run-id>`で残す。生のaccess logは1 runで約12MB（2 node）。
- 状態ログのcache（`runs/<id>/logs/.state-cache/`、`cache/states/`）は1 runで約200MBになる使い捨てのSQLiteで、消しても`states sql`・`timeline`が次に読むときに保存したログから作り直す。ベンチを起動するとhookが`scripts/isuscope-prune.py`で直近3 run以外のcacheを消し（10分以内に使ったものは残す）、`isuscope-data`をSpotlightとTime Machineの対象から外す。

## 会話との紐付け

isuscopeは各runを、ベンチを起動したClaude Codeのセッションの会話（`docs/agent-history/`）へ結び付ける。runの`agent_context`から、そのベンチを回した会話の位置が分かる。`[context.agent]`を有効にした後のベンチは、このrepoで起動した現在のセッションから実行する。

## 終了前

終了前はprofilerや重いログを外した構成へ切り替え、確認のベンチを通常の`run`で3回行う。同じ構成の2回目以降は`--repeat "最終確認"`を付ける（[終了前](../guides/finish.md)）。
