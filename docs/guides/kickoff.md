# kickoff: 自明な改善と初回ベンチ分析

## 目的

公式から提供された一次情報を保存し、初期状態、ベンチマークの評価方法、主要な処理経路を把握する。

また、アプリケーションコードと設定ファイルをすべてローカルリポジトリで管理し、コマンドからサーバーへデプロイできる状態を作る。

SSH確立直後に採用言語のコードとDB schemaを先行importし、初期状態をcommitする。その時点で`make kickoff`がClaude Codeのlane（`claude -p`、各自のworktreeとbranch）を8本起動する。`full-memory`が全メモリ化（読み取りと状態の判定をメモリへ移す）を、`indexes`が索引を、`n-plus-one`がN+1を、`obvious-fixes`が索引とN+1以外の自明な修正のうち全メモリ化の後も残るもの（書き込みと、DBを読まない処理）を、`runtime`が問題に依存しない実行時の設定（requestごとのlog、DB接続のpoolと`interpolateParams`、GC）を、それぞれ1件1commitで先行実装し、`local-env`がローカル実行環境を作り、`state-log`が状態を変える箇所へisuscopeの状態ログの呼び出しを入れて`docs/state-log.md`を書き、`waits`がAPIごとの待ちの口（表`ISUCON_WAITS`は空のまま。値はmainが空き時間に変える）を入れる。kickoffは、initializeの経路がコードに1つだけなら`.isuscope/routes.toml`の`[initialize]`も設定してcommitする。開始と同時に、worktree `portal`で起動したもう1つのセッションが`/portal`で、ポータルと当日マニュアルの保存、サーバーの起動に要る情報、ベンチ接続を作る。mainのセッションはその間にサーバーの起動、kickoff build設定、draftの判断を進め、portalの公式資料をmergeしたらlane `scenario`を起動して`docs/benchmark-scenario.md`を書かせ、完全importをcommitしたらlane `static-edge`を起動して静的ファイルの配り方（nginxからの配信、Cache-Control、gzip_static、open_file_cache）を1件1commitで先行実装させる。

merge・次に測るものの判断・deploy・ベンチ・分析・採否の責任はmainのセッションが持ち、改善の調査・実装・ローカル検証はimproveのセッション（`/improve`）が持つ（mainは改善を実装しない。詳しくは`/main`）。laneの成果は根拠を照合してからmergeする。初回baseline取得前の性能変更はremoteへ反映しない。初期状態の回収と改善commitは分け、比較可能性を守る。

## 流れ

chrome-debugでのポータルのログインと、worktree `portal`・`improve`は準備で済ませてある（[準備](preparation.md)の「ポータル」「improveの作業場所」）。開始と同時に、人間が3つの端末で`make session ROLE=main`・`ROLE=portal`・`ROLE=improve`を打ち、名前の付いたセッションを起動する（[準備](preparation.md)の「セッションの名前と、セッション同士の話」）。mainはrootで`/main`から始まり（続けて`/goal`）、portalとimproveは各worktreeで`/portal`・`/improve`から始まる。ボードはmainの起動と一緒に立つ。improveは`code-ready`の後にコードとschemaを読み始め、mainがbaselineの分析を`main analyzed`の行で知らせたら最初の変更を作る。ログインが切れていたら、そのときに人間が入り直す。portalは結果を`.local/progress.log`へ`portal <official|environment|bench|notice>`の行で書き、mainはkickoffの他の行と一緒に追う。

```text
portal（mainと並行。手順は.claude/commands/portal.md）
  → サーバーの起動に要る情報を.local/environment.envへ（portal environment）
  → ベンチ接続を作り、実際に1回回して、--check・--probeを通してcommit（portal bench）
  → 当日マニュアルなどの原文をdocs/official/へ保存してcommit（portal official）
  → その後はポータルの告知と質問への回答を見張る（portal notice）
main: portal environmentを見たらサーバーを起動し、.local/environment.env（形はconfig/environment.example.env）の
  provider、SSH、node分類を確かめてから
make kickoff（backgroundで実行し、.local/progress.logを追う）
  → 回収元1台のSSH確立後、webapp/goとDDLを先行回収し、その範囲だけ自動commit（code-ready）
  → 並行して1台目のbuild環境を調査。build設定を自動生成できれば即build、できなければ設定待ち
  → initializeの経路がコードに1つだけなら.isuscope/routes.tomlの[initialize]へ書いてcommit（initialize-route）。
    isuscopeがaccess logからinitializeと負荷走行を分けるので、初回runから要約にinitializeが混ざらない
  → 並行してlane full-memory（全メモリ化。書き込みはDBのまま）、indexes（索引を1件1commit）、
    n-plus-one（N+1を1handler1commit）、obvious-fixes（索引とN+1以外の、全メモリ化の後も残る自明な修正を1件1commit）、
    runtime（log・DB接続・allocator・TCP_NODELAY・Mutexを1件1commit）、local-env（config/local/compose.yaml）、
    state-log（状態ログの書き手と呼び出し、docs/state-log.md）、waits（APIごとの待ちの口。表は空）を起動。
    full-memoryは、state-log・local-envが終わったら自分で取り込んで確かめる
  → 全nodeのSSH確立、Ansible導入、初期収束、初期構成の調査
  → node role、同期対象、Ansible変数、log pathの候補を.local/draftへ生成
  → draftを実nodeと照合し、.local/draft/review.mdを出して停止（draft-ready）
make kickoffの実行中にmainのセッションが並行して進める
  → portal officialを見たらgit merge --no-ff portalし、すぐlane scenario（docs/benchmark-scenario.md）を起動
  → portal benchを見たらgit merge --no-ff portalし、.isuscope/benchmark.sh --check。portal noticeは内容を確かめる
  → build設定待ちならconfig/kickoff-build.jsonを作る。自動生成されたら確認してcommit
  → progress.logのinitialize-routeがneeds_choice（候補が無い・複数）なら、選んでset-routeしてcommit
    （scripts/benchmark-markers.py suggest-route → set-route）
  → laneが終わったものから結果を読んでmergeする（local-env、scenarioは文書と構成だけ）
review.mdのFAILを直し、WARNをすべて判断してからCONFIRM_DRAFT=true make kickoff-apply
  → draftを再検査して反映し、全配布先のdigest一致を確認して完全import
完全importを確認して初期状態をcommit
  → すぐlane static-edge（python3 scripts/lane.py start static-edge。nginxの設定のcommitが要る）を起動
make first-bench（人間の確認を待たずにすぐ実行する。失敗した段階で止まる）
  → make bench-check: 全node、同期、ベンチadapterと接続先、isuscope doctorをベンチなしで検査
  → HEADが完全importの内容のままで、アプリのunitが全nodeで動いていれば、配布されたbuildのままsurvey-runで初回ベンチ
    （ローカルbuildとdeployを待たない。baseline-readyで分析を始める）。その後に同じcommitをmake deploy
  → そうでなければ、make deploy（kickoff buildの完了を待つ）の後にsurvey-run
  → 出力の形を確かめる前ならresultはPENDING。python3 scripts/benchmark-sample.py <run>で読み取りを確かめ、
    --apply --score N --pass true|false（自分で読んだ値）でsample・contract・--check・rescoreまで済ませる
初回runを分析し（スコア・エラー・HTTP・SQL・ホスト負荷。最大の制約と根拠をisuscope analyzeに書く）。hookがその冒頭を`main analyzed`でimproveへ知らせる。improveが観測に基づいて改善を実装・検証して投稿する
  → baselineの後の最初のdeployで、lane state-log、続けてlane n-plus-oneを`git merge --no-ff`で取り込む
    （次のrunからbriefのstates欄が埋まり、状態についての問いに計測を足さずに答えられる）
  → lane indexes・obvious-fixes・runtime・static-edgeのcommitをbaselineの観測と照合して選んで取り込む（laneの途中でもcommit単位で取り込める）
  → 全メモリ化の間もimproveは全メモリ化が書き換えない所の改善を止めない。lane full-memoryが終わったら（mainを1度だけ取り込み済み）、
    mainへmergeし、laneが取り込んだ後にmainへ入ったwebapp/の変更をベンチを待つ間に全メモリ化の形へ移し、
    deployして次のベンチで最優先に測る
  → deployし、通常のisuscope runでbaselineと比較して、仮説の判定と変更の採否を記録する。障害があれば原因調査から修正・再評価まで続ける
```

### 待ち時間を使う

時間の大半は「`make kickoff`が終わるのを待つ間」と「deployから初回ベンチまで」に出る。mainのセッションは`make kickoff`の終了を待たず、`.local/progress.log`の行を見て次の操作へ移る。`make kickoff`は`run_in_background`で起動し、Monitorで`python3 scripts/progress.py follow main --from-start`を追う（main宛ての行だけが、何をするかの一言と一緒に出る。宛先と一言は`config/progress.json`）。

- 公式資料とベンチ接続は`make kickoff`の結果を使わないので、portalのセッションが開始と同時に始める。
- `make first-bench`は段階の間でAIの手番を待たない。
- kickoff buildは手元のGoで数十秒で終わる。最初のdeploy前はサーバーが配布状態のまま動いていることを使い、baselineをbuildとdeployの前に取る。
- `bench-check`はdeployに依存しないので、deployの前に流す。deployで設定（nginx、MySQL、unit）を変えたときだけ再実行する。
- 段階を個別にやり直すときは`make bench-check`、`make deploy`、`isuscope survey-run`を直接使う。

### 再実行と判断の分担

- 採用言語はGoに固定する（`config/application.env`）。
- 先行回収は完全importを待たない読解用の暫定snapshotで、対象はコードとschemaだけに限る。初期状態の正本化と全配布先のdigest一致の確認は、`kickoff-apply`のimportで完了する。
- `make kickoff`は再実行できる。回収済みならimportを飛ばし、kickoff buildと、完了・実行中のlaneは重複起動しない。
- draftの判断は`review.md`を読んだ操作者（人間またはこのセッション）が行う。`make`の中で別のAIを呼んで承認させない。
- `make`はkickoff・deploy・検査とボードの入口に絞っている。個別の段階をやり直す場合は`scripts/`の該当scriptを直接実行する（変更系scriptは自分で操作lockを取る）。
- 並行作業はlane（`scripts/lane.py`）か`make worktree`で行う。remote操作はmain側で共通lockを使う。

### laneの起動と取り込み

- `make kickoff`が起動するlaneは`config/lanes.json`の`kickoff`（既定は`full-memory`、`indexes`、`n-plus-one`、`obvious-fixes`、`local-env`、`state-log`、`waits`、`runtime`）。`KICKOFF_LANES=`で止め、`KICKOFF_LANES="indexes"`のように選べる。
- `scenario`は公式資料が要るので、mainが`docs/official/`をcommitした直後に`python3 scripts/lane.py start scenario`で起動する。
- `static-edge`はnginxの設定が要るので（`requires`の`config/nginx/nginx.conf`）、mainが完全importをcommitした直後に`python3 scripts/lane.py start static-edge`で起動する。`listen`・`proxy_pass`を変える`make tare`とは別の行だけを変えるが、衝突したら両方の意図を残す。open_file_cacheのcommitは`make tare`（fdの上限）の後に取り込む。
- `full-memory`（全メモリ化）は、先行回収の直後に他のkickoffのlaneと同時に始まり、他のlaneの終わりを待たない。`config/lanes.json`の`joins`にある`state-log`・`local-env`は、laneが書き換えを済ませた後に、終わったかを`lane.py status`で見て自分で取り込み、書き換える前と後の状態ログを比べる。`n-plus-one`などの読み取りの修正は、mainが取り込んだ分を「最新のmainを取り込む」で受け取る。
- 全メモリ化の間も、improveは全メモリ化が書き換えない所（設定・node分割・nginx・MySQLの設定など）の改善を止めない。laneは終わる前に最新のmainを1度だけ自分のbranchへmergeして確かめ直し、取り込んだmainのhashを最後の回答に書く。mainは`git merge lane/full-memory`で取り込み、`git log <そのhash>..`のうち`webapp/`を変えたcommitを、ベンチを待つ間に自分で全メモリ化の形へ移す（SELECTはstoreから読む、書き込みは変更を確定する関数を通す、cacheやメモリ化はstoreへまとめる。`docs/cache-design.md`に正本と関数の場所がある）。移す手間より測った改善が小さいcommitは、移さずに捨ててよい。
- baseline後に並行させたい調査や実装は、promptを書いて`python3 scripts/lane.py start <名前> --prompt-file <path> --purpose "..."`で起動する。進行は`make lanes`で見る。
- `scenario`は文書だけ、`local-env`はローカル環境の構成と文書だけなので、結果を読んで終わり次第mainへmergeしてよい。
- `state-log`は書き手のmoduleと呼び出しを2つのcommitにする。baselineの後の最初のdeployで、他のlaneより先に`git merge --no-ff lane/state-log`で取り込む（全メモリ化も同じbranchをmergeしているので、cherry-pickにしない）。取り込む前に、`docs/state-log.md`と最後の回答で、lockの中に残した呼び出しや`entered`にした箇所を確かめる。
- `n-plus-one`は1handler1commitで積む。baselineの後の最初のdeployで、`state-log`の次に`git merge --no-ff lane/n-plus-one`で丸ごと取り込む（buildが通らない・応答を変えるcommitがあれば、mergeの後に`git revert`する）。全メモリ化が入ると読み取りはstoreへ移るので、全メモリ化が先に終わっていれば取り込まなくてよい。
- `waits`はmoduleとmiddlewareの1commit。表が空なら挙動を変えないので、baselineの後の最初のdeployで`git merge --no-ff lane/waits`で取り込む。表に書けるrouteの一覧はlaneの最後の回答にある。
- `indexes`・`obvious-fixes`・`runtime`・`static-edge`は1件1commitで積み、commitのたびに`.local/progress.log`へ`lane:<名前> commit <hash>`が出る。初回baselineの分析の後、観測と照合して効くcommitから`git cherry-pick`で取り込み（laneの完了は待たない）、deployして`isuscope run`で測る。laneの最後の回答にある「実装しなかった候補」も次の変更を選ぶ材料にする。

## ローカル実行環境を構築する

準備ではDockerと汎用の起動・検証コマンドだけを用意する。開始後のコード先行回収の後、lane `local-env`（`config/lanes/local-env.md`）が`config/local/compose.example.yaml`から`config/local/compose.yaml`を作り、go.modに合うGoの版・DB・追加service・schema・初期化・health endpointを実アプリに合わせる。手順は[ローカル実行環境](../local-development.md)を参照する。

構成をcommitしたら、各worktreeで`make local-up`、`make local-check`、`make local-down`を実行してよい。DB・network・portはworktree単位で分離し、remote変更用lockを取らない。構築はkickoffと並行し、完成を初回baselineのgateにはしない。

ローカルで初期化、代表API、SQLの実行計画、修正前後の挙動を確認する。起動・確認の手順と、手元で動かないもの（欠けているservice、初期データの範囲）は`config/local/compose.yaml`の冒頭のコメントにある。health成功だけをシナリオ検証済みと扱わない。初期データは許可された取得元だけを使い、未回収ならその制限を明示する。性能変更の最終採否は配布サーバーの共有ベンチで決める。

## 公式情報を保存する

portalのセッション（`/portal`）が、大会運営から提供された情報を`docs/official/`へ保存する。

対象には次を含める。

- レギュレーション
- 問題文
- アプリケーションマニュアル
- API仕様
- 参加者向けに公開されたベンチマーカーの仕様（内部コードは対象外）
- スコア計算方法と失格条件
- サーバー構成とスペック
- 提出、再起動、終了時の手順
- 当日アナウンス
- FAQ、訂正、追加情報
- 配布されたその他の文書

公式情報は要約だけで済ませず、可能な限り原文を保存する。URLから取得した場合は、取得元URLと取得日時も記録する。

認証情報、Cookie、SSH秘密鍵などは保存しない。

## 環境を立ち上げて初期状態を保存する

コマンドと各段階の詳細は[自動化の仕様](../reference/automation.md)を正とする。ここでは判断基準だけを定める。

- 練習でAWS環境を自分で作る場合は、構成定義を`infra/`へ保存し、停止・再開・削除をコマンドで行えるようにする。認証情報はGitへ保存しない
- `make kickoff`が出す`.local/draft/review.md`のFAILはすべて直し、WARNは内容を読んで判断してから`make kickoff-apply`へ進む。`review.md`の末尾の「Nodes」の表は、nodeごとのインスタンスの型・CPUの型番・inspectで測ったCPUの速さ（最も速いnodeに対する1 threadと全vCPUの割合）を並べる。型やCPUが混ざっている、または同じ型なのに15%以上遅いnodeがあればWARNになる。そのときは役割（nginx・app・DB）とnginxのupstreamのweightを、node名の順ではなくこの表で決める（CPUを食う役は1 threadの速いnodeへ、入口はネットワーク特化型（c6inなど）があればそこへ、weightは全vCPUの割合で）。draftのremote path、owner、service、roleは推測にすぎない
- nodeの分類が誤っている場合はprovider入力やdraftを直して再生成し、生成物を手作業で直し続けない
- importで配布先のdigestが一致しない場合は、sourceを確認するまで進めない
- 初期状態をcommitする（`make rollback`で、その状態へ稼働プロセスまで戻せる）
- 計測と運用の土台（計測tool、LTSV access log、時刻同期、journal、自動更新停止）はkickoffのAnsibleで固定化される。SQLはisuscopeがperformance_schemaから取るので、slow logは既定で無効。レギュレーションで禁止された項目だけ変数で外す
- 性能を変える設定（`config/nginx/tare`、networking sysctl、MySQLの性能設定）は、初回baselineの後に1つの変更として入れる
- ベンチ接続はisuscopeの`command` modeを標準とし、手動入力の`external` modeを通常運用にしない
- `make bench-check`はベンチを起動しない。初回ベンチは独立した明示操作にする

## ローカルを正とする

大会で使用するコードと設定ファイルは、ローカルリポジトリを正とする。

対象には次を含める。

- アプリケーションコード
- nginxなどのリバースプロキシ設定
- MySQLなどのDB設定
- systemdのunitとoverride
- DBスキーマと初期化処理
- DNSやキャッシュなどの設定
- cronや定期実行処理
- その他、動作に必要な設定とスクリプト

サーバー上のファイルを直接編集する運用は避ける。緊急で直接変更した場合は、直ちにローカルへ反映し、Git差分として残す。

秘密情報はGitへ追加せず、無視された環境ファイルなどで管理する。

## デプロイと計測を準備する

手作業のコピーを通常のデプロイ手順にしない。deployは、build成果物をlive切替前に検証し、設定を検証してから必要なserviceだけをreload・restartし、失敗時はファイルと稼働プロセスを旧構成へ戻せるようにする。

isuscopeは、全nodeへのSSH、ベンチ起動、ログとcollector、動的URLの正規化、会話履歴との紐付けを`isuscope doctor`で確認してから使う。計測結果は`isuscope-data/`へ保存し、軽量なrun履歴はGitへ残して、重要なrunだけ`git add -f isuscope-data/runs/<run-id>`で生ログも残す。

## 初回ベンチを実行する

初期状態のまま`isuscope survey-run`を一度だけ実行し、標準観測と行動遷移を記録する（行動遷移と生のaccess logは以後の`run`でも毎回残る）。initializeと負荷走行の境界は、初回runより前に`.isuscope/routes.toml`の`[initialize]`で決めておく。コードに候補が1つだけならkickoffが書いてcommitする。`.local/progress.log`の`initialize-route`が`needs_choice`なら、`scripts/benchmark-markers.py suggest-route`の候補から選んで`set-route`する。完全importのcommit後に`make first-bench`で、人間の確認を待たずに実行する。

最初のdeployの前は、配布状態のままbaselineを取り、その後に同じcommitをdeployする（配布状態で測れなければ先にdeployする。動きは[自動化の仕様](../reference/automation.md)の「ベンチ前gateと初回run」）。baselineは配布されたbuild、2本目以降は自前のbuildなので、最初の改善runとの比較にはその差が混ざり得る。

確認するもの：

- スコアとシナリオ別結果
- エラーと失敗理由
- APIごとの件数とレイテンシー
- CPU、メモリ、ディスクI/O
- SQLの回数と実行時間
- ベンチ中に飽和した資源

## ベンチマークシナリオを分析する

`AGENTS.md`とそこから指定された追加指示、公式レギュレーション・当日マニュアルに従い、利用が認められた資料と計測結果から次を分かる範囲で`docs/benchmark-scenario.md`（ベンチの見取り図）へ整理する。資料とコードから書ける部分は、公式資料のcommit直後に起動するlane `scenario`が初回ベンチより前に書き、mainのセッションは初回run以降の観測で推測の部分を確かめ、該当する節の中で事実へ書き換える（出典にrun IDを付け、観測の節を追記しない）。見取り図に書くのはベンチの仕様（得点、FAILと整合性の条件、シナリオの流れ、応答へのベンチの反応）だけで、runのスコア・API回数・改善の効果・最大の制約の推移は書かない（isuscopeの`analyze`に残る）。

各項目に根拠を残し、確認済みの事実と観測からの推測を区別する。許可された資料から確認できない並列数・繰り返し条件・整合性チェックなどは不明と記録する。

- どのシナリオがあるか
- 各シナリオがどのAPIをどの順番で呼ぶか
- 何をすると得点が増えるか
- 何が失敗するとシナリオが止まるか
- 並列数や繰り返し条件
- initialize後に期待される状態
- 整合性チェックと、それが前提にする状態の順序、失格条件
- 得点へ直接つながる処理
- 得点行動の入口: 利用者が得点する行動の前にどの応答の何を見るか、その中身や見えている時間が変わると行動の回数がどう変わるか

APIの一覧には、各APIのhandler（`file:line`）と主なSQL・外部処理を付ける。シナリオ → API → handler → SQL → 得点・失敗のつながりは、この見取り図だけに置く（コードの地図は別に作らない）。

## baselineの後に次の変更を選ぶ

mainは初回baselineの分析で、観測された最大の制約を特定し、根拠とともに初回runの`isuscope analyze`に書く。improveはこの分析から最初の変更を選ぶ。node別のCPU・iowait・disk・network、`database_io`、`logs`など、読める欄は[isuscopeの使い方](../reference/isuscope.md)にある。

接続・fdの上限（nginxのworker_connections・backlog・keepalive、アプリのLimitNOFILE、sysctl、MySQLのmax_connectionsとbuffer pool）は問題に依存しない既定値で、`make tare`が1つのcommitにする。baselineの後に早めにdeployして`isuscope run`で測る。`make tare`は、計測用のLTSVと二重に書くnginxの既定のaccess logも外す。

MySQLの耐久性（commitごとのfsync、binlog、doublewrite、redoの容量）は`make durability`が1つのcommitにする。書き込みの永続化の時機を変えるので`make tare`とは分け、runにDBの書き込み待ち（COMMITの時間、redo・binlogの待ち、DB nodeのiowait）が見えたら入れて測る。通常の停止と再起動では書いたデータが残り、失うのはOSが落ちたときの最後の1秒だけ。

次の変更の選び方は`AGENTS.md`の「判断の原則」に従う。全メモリ化はlane `full-memory`が行うので、improveはそれと重なるメモリ化を作らない。

## 残すもの

記録の置き場所は固定で、一覧は`config/records.json`にある。`make board`の「記録」で、人間が一覧と中身を見る。決まった場所以外に書かない。

- `docs/official/`：公式の一次情報
- `docs/benchmark-scenario.md`：ベンチの見取り図
- `docs/state-log.md`：状態ログの対象
- `docs/cache-design.md`：メモリ化の設計（入れた場合）
- isuscopeによる初回ベンチ結果
- 初期状態のコードと設定
- ローカルから実行できるデプロイコマンド
- 初期状態へ戻せるGitコミット

## 非同期buildの開始と確認

`make kickoff`は先行回収・commit後に、kickoff build処理を独立して始める。`kickoff-build`の行が`configured`なら、生成された`config/kickoff-build.json`をcommitする。`needs_configuration`なら、`.local/kickoff-build/proposal.json`の理由を読み、`config/kickoff-build.example.json`を参考に`config/kickoff-build.json`を作ってcommitする（人間の承認は要らない）。処理の動きと設定の作り方は[自動化の仕様](../reference/automation.md)の「kickoff build」にある。
