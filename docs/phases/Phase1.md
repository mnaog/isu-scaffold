# Phase 1: 初動・自明な改善・初回ベンチ分析

## 目的

公式から提供された一次情報を保存し、初期状態、ベンチマークの評価方法、主要な処理経路を把握する。

また、アプリケーションコードと設定ファイルをすべてローカルリポジトリで管理し、コマンドからサーバーへデプロイできる状態を作る。

SSH確立直後に採用言語のコードとDB schemaを先行importし、初期状態をcommitする。その時点で`make kickoff`がClaude Codeのlane（`claude -p`、各自のworktreeとbranch）を4本起動する。`code-map`が`docs/code-map.md`を書き、`indexes`が索引を、`obvious-fixes`が索引以外の自明な修正を、それぞれ1件1commitで先行実装し、`local-env`がローカル実行環境と`docs/local-verification.md`を作る。kickoffは調査結果から`docs/environment.md`も自動で書く。mainのセッションはその間に公式資料・ベンチ接続・初期build設定・draftの判断を進め、公式資料をcommitしたらlane `scenario`を起動して`docs/benchmark-scenario.md`を書かせる。

調査・修正・ローカル検証・deploy・ベンチ・採否判断の責任はmainのセッションが持つ。laneの成果は根拠を照合してからmergeする。初回baseline取得前の性能変更はremoteへ反映しない。初期状態の回収と改善commitは分け、比較可能性を守る。

## 初動と改善の流れ

1. `make kickoff`をバックグラウンドで始め、`.local/progress.log`を追う。コードとschemaの先行回収・commitの後、build laneとAI laneが並行で始まる。
2. kickoffと並行して、公式資料を保存・commitしてlane `scenario`を起動し、`config/benchmark.env`・`benchmark-contract`の用意、`--check`・`--probe`を済ませる。build設定が自動生成されたら確認してcommitし、設定待ちなら`config/phase1-build.json`を作る。
3. draftを判断して`kickoff-apply`し、完全importをcommitする。
4. 人間の確認を待たずに`make first-bench`を実行する。`phase1-check`の後、配布状態のままの初期状態で`survey-run`を取り、その後に同じcommitをdeployする（配布状態で測れない場合はdeployを先にする）。`baseline-ready`が出たらスコア・エラー・HTTP・SQL・ホスト負荷を分析し、lane `code-map`の地図を照合に使う。
5. 観測とコードから改善を選ぶ。lane `indexes`・`obvious-fixes`のcommitは観測と照合して効くものから`git cherry-pick`し（laneの完了を待たない）、残りは実装・ローカル検証・commit・deployを進める。
6. 通常の`isuscope run`で比較し、仮説の判定と変更の採否を記録する。障害があれば原因調査から修正・再評価まで続ける。

先行importは読解用の暫定snapshotであり、全配布先のdigest一致と設定の正本化は完全importで確認する。並行作業はlane（`scripts/lane.py`）か`make worktree`で行う。remote操作はmain側で共通lockを使う。

## ローカル実行環境を構築する

Phase 0ではDockerと汎用の起動・検証コマンドだけを準備する。Phase 1のコード先行回収後、lane `local-env`（`config/lanes/local-env.md`）が`config/local/compose.example.yaml`から`config/local/compose.yaml`を作り、採用Rust版・DB・追加service・schema・初期化・health endpointを実アプリに合わせる。手順は[ローカル実行環境](../local-development.md)を参照する。

構成をcommitしたら、各worktreeで`make local-up`、`make local-check`、`make local-down`を実行してよい。DB・network・portはworktree単位で分離し、remote変更用lockを取らない。構築は初動と並行し、完成を初回baselineのgateにはしない。

ローカルで初期化、代表API、SQLの実行計画、修正前後の挙動を確認する。schemaのみ・縮小データ・フルデータのどれか、欠けているserviceや検証範囲を`docs/local-verification.md`へ記録し、health成功だけをシナリオ検証済みと扱わない。初期データは許可された取得元だけを使い、未回収ならその制限を明示する。性能変更の最終採否は配布サーバーの共有ベンチで決める。

## 公式情報を保存する

大会運営から提供された情報を`docs/official/`へ保存する。

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

コマンドと各段階の詳細は[初動自動化](../initial-automation.md)を正とする。ここでは判断基準だけを定める。

- 練習でAWS環境を自分で作る場合は、構成定義を`infra/`へ保存し、停止・再開・削除をコマンドで行えるようにする。認証情報はGitへ保存しない
- `make kickoff`が出す`.local/draft/review.md`のFAILはすべて直し、WARNは内容を読んで判断してから`make kickoff-apply`へ進む。draftのremote path、owner、service、roleは推測にすぎない
- nodeの分類が誤っている場合はprovider入力やdraftを直して再生成し、生成物を手作業で直し続けない
- 台数とrole、CPU・メモリ、OS・kernel・nginx・MySQLの版、配布サーバーの言語の版、開始からの計時を`docs/environment.md`へ残す。kickoffが調査結果から`scripts/environment-report.py`で自動で書くので、確かめてcommitし、開始宣言の時刻など足りないものを書き足す
- importで配布先のdigestが一致しない場合は、sourceを確認するまで進めない
- 初期状態をcommitし、`make deploy`、`make status`、明示的な`make rollback`で稼働プロセスまで旧構成へ戻ることを確認する
- 計測と運用の土台は初動のAnsibleで固定化される。レギュレーションで禁止された項目だけ変数で外し、性能を変える設定は初回baselineの後に入れる
- ベンチ接続はisuscopeの`command` modeを標準とし、手動入力の`external` modeを通常運用にしない
- `make phase1-check`はベンチを起動しない。初回ベンチは独立した明示操作にする

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

初期状態のまま`isuscope survey-run`を一度だけ実行し、標準観測と行動遷移を記録する。その出力をsampleにしたら、`scripts/benchmark-markers.py`でinitializeの境界の文言を設定し、以後のrunでinitializeと負荷走行を分ける。完全importのcommit後に`make first-bench`で、人間の確認を待たずに実行する。

最初のdeployの前は、サーバーが完全importと同じ内容（配布状態）で動いている。`make first-bench`は、HEADがimportしたdigestのままで（`scripts/imported-state.py`）、アプリのunitが全nodeで動いてlistenしていれば、そのcommitを稼働中として記録し（`.local/current-import-commit`）、ローカルbuildとdeployを待たずにbaselineを取る。その後に同じcommitをdeployし、deploy経路を最初の改善より前に確かめる。baselineの実行ファイルは配布されたbuild、2本目以降は自前のbuild（Rust 1.99.0）になるので、最初の改善runの比較にはその差が混ざり得る。配布サーバーのRust版（`.local/phase1-build/environment.txt`）が大きく違う場合は`docs/baseline.md`に書き、小さな差の判定に注意する。配布状態で測れないとき（記録がない、importの後に配る対象を変えた、unitが動いていない、既にdeployした）は、deployしてから測る。`FIRST_BENCH_MODE=deploy`なら常にdeployが先になる。追加でスコアの振れ幅を見る場合は`survey-run`を繰り返さず、以後は通常の`isuscope run`を使う。手順は[初動自動化](../initial-automation.md)の「ベンチ前gateと初回run」を参照する。

確認するもの：

- スコアとシナリオ別結果
- エラーと失敗理由
- APIごとの件数とレイテンシー
- CPU、メモリ、ディスクI/O
- SQLの回数と実行時間
- ベンチ中に飽和した資源

## ベンチマークシナリオを分析する

`AGENTS.md`とそこから指定された追加指示、公式レギュレーション・当日マニュアルに従い、利用が認められた資料と計測結果から次を分かる範囲で`docs/benchmark-scenario.md`（ベンチの見取り図）へ整理する。資料とコードから書ける部分は、公式資料のcommit直後に起動するlane `scenario`が初回ベンチより前に書き、mainのセッションは初回runの観測で推測の部分を確かめて直す。

各項目に根拠を残し、確認済みの事実と観測からの推測を区別する。許可された資料から確認できない並列数・繰り返し条件・整合性チェックなどは不明と記録する。

- どのシナリオがあるか
- 各シナリオがどのAPIをどの順番で呼ぶか
- 何をすると得点が増えるか
- 何が失敗するとシナリオが止まるか
- 並列数や繰り返し条件
- initialize後に期待される状態
- 整合性チェックと失格条件
- 得点へ直接つながる処理

主要な処理について、次のつながりを`docs/code-map.md`（コードの地図）へ整理する。コードの先行回収の直後にlane `code-map`が書き始め、mainのセッションは`lane/code-map`をmergeしてから、公式資料と初回runの観測で得点・失敗の対応を補う。

```text
シナリオ
  → API
  → handler
  → SQL・外部処理
  → 使用するサーバーやサービス
  → 得点または失敗条件
```

## 自明なボトルネックを一掃する

次をコードから調べ、初回baselineの観測と照合して実装する。

- WHERE、JOIN、ORDER BYに対する明らかなインデックス不足
- ループ内SQLや要素ごとの更新などのN+1
- 同一request内の重複query・重複計算
- 単一行のカウンタや必要以上のlocking readによる直列化
- 一括INSERT・UPDATE・UPSERTに置き換えられる逐次write
- transaction内の不要な処理とDB往復
- 明らかに不要なカラム、全件走査、外部呼び出し

インデックスは、対象のSQL、初期化後の行数、効果を確かめたrun IDを`docs/indexes.md`へ残す。読み取りのキャッシュ・メモリ化と、DB・アプリ・nginxを別のnodeへ分ける配置変更は、次節の条件でPhase 1でも入れてよい。整合性条件を変えるもの、非同期化、write-behindなど永続化の時機を変えるものはPhase 1では実装しない。

この一掃は初動の待ち時間と並行して進める作業であり、次の判断を止めるgateではない。先行回収の直後からlane `indexes`が`lane/indexes`へ索引を、lane `obvious-fixes`が`lane/obvious-fixes`へ索引以外の修正を、1件1commitで実装する（deployはしない）。commitのたびに`.local/progress.log`へ`lane:<名前> commit`が出るので、初回runから得た上位SQL・HTTP・エラーと照合し、効くcommitからlaneの完了を待たずに`git cherry-pick`する。根拠が弱い変更は保留する。変更を反映した最初の`isuscope run`で大きく改善することを確認し、その後はボトルネックごとの短いサイクルに切り替える。

## baselineの後に次の変更を選ぶ

初回baselineの分析では、SQLの候補を消化する前に、観測された最大の制約を特定し、根拠とともに`docs/baseline.md`へ残す。`isuscope brief`のnode別CPU・iowait・disk util・network、`database_io`、`logs`から、少なくとも次を確認する。

- 負荷が1台に集中し、他のnodeが空いていないか
- CPUよりI/O待ち（iowait、disk util、COMMITやfile待ち）が支配的でないか
- ネットワーク帯域・接続数・エラーが得点を止めていないか

接続・fdの上限は問題に依存しない既定値なので、baselineの後に早めに`make tare`で1つの変更として入れて測る。practice-14ではこの既定値（keepalive_requests 1000、アプリのfd 1024、listen backlog 511、worker_connections 4096）で、後からFAILが4回出た。

最大の制約がSQL・index・N+1なら、それを次の変更にする。最大の制約が1台への集中や繰り返しの読み取りなら、残りのSQL候補を一掃するまで待たず、次のどちらかをPhase 1のまま入れてよい。

- DB・アプリ・nginxを別のnodeへ分ける配置変更
- 読み取りのキャッシュ・メモリ化

どちらも根拠（run ID、node別の観測値、期待する効果）を残し、1つずつ入れて通常の`isuscope run`で測る。メモリ化は1つの読み取り経路ずつ進め、データの正本、更新時の反映、無効化、initialize後と再起動後の再構築方法を`docs/cache-design.md`へ書いてから入れる。非同期化やwrite-behindなど、整合性条件や永続化の時機を変える変更が必要なら、根拠を添えて人間へPhase 2への移行を提案する。提案と移行の判断を待つ間も、Phase 1の範囲の修正は続けてよい。

## 残すもの

記録の置き場所は固定で、一覧は`config/records.json`にある。`make board`の「記録」で、人間が一覧と中身を見る。決まった場所以外に書かない。

- `docs/official/`：公式の一次情報
- `docs/environment.md`：環境
- `config/benchmark-contract.json`：ベンチの競技条件
- `docs/benchmark-scenario.md`：ベンチの見取り図
- `docs/code-map.md`：コードの地図
- `docs/local-verification.md`：ローカル検証の範囲
- `docs/baseline.md`：初回baselineの制約
- `docs/indexes.md`：インデックスの根拠
- `docs/cache-design.md`：メモリ化の設計（入れた場合）
- isuscopeによる初回ベンチ結果
- 初期状態のコードと設定
- ローカルから実行できるデプロイコマンド
- 初期状態へ戻せるGitコミット

## 完了条件

- 公式から提供された一次情報が`docs/official/`に揃っている
- コードと設定ファイルがローカルリポジトリで管理されている
- ローカル環境で実施した検証と未対応範囲が記録され、改善の検証に利用できる（構築不能なら理由と代替の検証手段を記録）
- ローカルからコマンドでデプロイできる
- サーバーを直接編集せずに構成を再現できる
- 初期状態へ戻せる
- isuscopeでベンチと計測を実行できる
- 許可された資料・計測で確認できる得点、失敗条件、主要シナリオを説明でき、推測と不明点を記録している
- 確認できた主要シナリオとアプリ内の処理経路が対応付けられている
- 初回baselineで観測された最大の制約と、その根拠が記録されている
- 主要SQLの明らかなインデックス不足が解消されている
- 高頻度経路の主要なN+1、重複処理、逐次writeが解消されている
- 自明な修正を統合したrunをbaselineと比較し、採否を記録している

条件を満たしたら作業中のagent（CodexまたはClaude Code）はPhase 2への移行を提案し、人間が移行を決定する。最大の制約がPhase 1の範囲外にある場合は、SQLの項目が残っていてもその時点で移行を提案する（「baselineの後に次の変更を選ぶ」）。旧Phase 2の自明な改善はこのPhaseへ統合済みで、読み取りのメモリ化と台の分離もこのPhaseで始めてよい。現在のPhase 2は、非同期化など整合性や永続化の時機を変える変更を含むアーキテクチャ改善を扱う。

## 非同期buildの開始と確認

`make kickoff`は先行回収・commit後、独立した初期build処理を開始して全台bootstrapを進める。

build処理はremoteのCPU・glibc・OpenSSLと回収したCargo.toml・Cargo.lockから設定を提案し、`.local/phase1-build/proposal.json`へ残す。判断できる場合は`config/phase1-build.json`を書いてすぐbuildを始める（`configured`）。判断できる場合とは、単一binaryのpackageで、systemライブラリに依存する-sys crateがなく、`config/rust-builder/bases.json`にnodeのglibc以下（OpenSSLを使うならlibsslのmajorも一致）のbaseがあることを指す。自動生成された設定も内容を確認してcommitする。workspace、複数binary、`mysqlclient-sys`などのsystemライブラリ、合うbaseがない場合は理由を残して設定を待つ。`PHASE1_BUILD_AUTO_CONFIG=0`なら常に待つ。

全台準備を待たず、`.local/progress.log`の`phase1-build`の行か`.local/phase1-build/status.json`と`environment.txt`を確認する。`needs_configuration`なら`config/phase1-build.example.json`を参考に、回収したCargo.toml・Cargo.lock・toolchain指定、remoteのCPU・OS・glibc・Rust版、追加Cライブラリを照合して`config/phase1-build.json`を作る。対象は`base_image`・`target`・`binary`・`dockerfile`。未記入のexampleを実設定へコピーしたままにしない。完成内容を一時ファイルに書き、renameで一括配置する。操作者自身の判断でよく、人間への追加承認は不要。

初期build処理は設定を最大30分待ち、設定完了後は既存の`local-build.py`を使って共有キャッシュへbuildする。設定が既にあれば調査・CPU照合後すぐbuildする。全台準備とbaselineはbuild処理の完了待ちを入れない。deployに必要なbuild完了は従来どおりdeployが保証する。

`configure-draft`はこの初期build設定をそのまま`local_builds`へ取り込む。draft生成より後に設定した場合は、設定済みとなった後にdraftを再生成するか、同じ設定をdraftへ反映してからreviewする。異なるimage・Dockerfile・targetに変える場合はキャッシュが別になることを認識する。設定とDockerfileは通常のcommitへ含める。

進行確認は`python3 scripts/phase1-build.py status`、失敗後の再開は`python3 scripts/phase1-build.py start`。ログは`.local/phase1-build/build.log`。同時起動は専用lockで防ぐ。練習停止時は`python3 scripts/phase1-build.py stop`で初期build処理も止める。停止してもソース・成果物・キャッシュは残す。ローカル検証用Composeはこのデプロイ用buildと別キャッシュであり、同時コンパイルによる端末の資源競合を避ける。
