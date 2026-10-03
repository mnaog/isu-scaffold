# Phase 1: 初動・自明な改善・初回ベンチ分析

## 目的

公式から提供された一次情報を保存し、初期状態、ベンチマークの評価方法、主要な処理経路を把握する。

また、アプリケーションコードと設定ファイルをすべてローカルリポジトリで管理し、コマンドからサーバーへデプロイできる状態を作る。

SSH確立直後に採用言語のコードとDB schemaを先行importし、初期状態をcommitする。初期buildと環境準備の待ち時間にコード読解を進める。別セッション・worktreeの自動起動は行わない。

調査・修正・ローカル検証・deploy・ベンチ・採否判断は同じセッションで進める。初回baseline取得前の性能変更はremoteへ反映しない。初期状態の回収と改善commitは分け、比較可能性を守る。

## 初動と改善の流れ

1. コードとschemaを先行回収し、初期状態をcommitする。
2. 非同期buildの調査結果と配布コードを照合し、`config/phase1-build.json`を確定する。
3. 全台準備・完全import・role・deploy・benchmark adapter・isuscopeを準備する。
4. `phase1-check`後、初期状態の`survey-run`を明示実行し、スコア・エラー・HTTP・SQL・ホスト負荷を分析する。
5. 観測とコードから改善を選び、実装・ローカル検証・commit・deployを進める。
6. 通常の`isuscope run`で比較し、仮説の判定と変更の採否を記録する。障害があれば原因調査から修正・再評価まで続ける。

先行importは読解用の暫定snapshotであり、全配布先のdigest一致と設定の正本化は完全importで確認する。並行作業が必要な場合のみworktreeを作成する。remote操作はmain側で共通lockを使う。

## ローカル実行環境を構築する

Phase 0ではDockerと汎用の起動・検証コマンドだけを準備する。Phase 1のコード先行回収後、作業するAIが`config/local/compose.example.yaml`から`config/local/compose.yaml`を作り、採用Rust版・DB・追加service・schema・初期化・health endpointを実アプリに合わせる。手順は[ローカル実行環境](../local-development.md)を参照する。

構成をcommitしたら、各worktreeで`make local-up`、`make local-check`、`make local-down`を実行してよい。DB・network・portはworktree単位で分離し、remote変更用lockを取らない。構築は初動と並行し、完成を初回baselineのgateにはしない。

ローカルで初期化、代表API、SQLの実行計画、修正前後の挙動を確認する。schemaのみ・縮小データ・フルデータのどれか、欠けているserviceや検証範囲を記録し、health成功だけをシナリオ検証済みと扱わない。初期データは許可された取得元だけを使い、未回収ならその制限を明示する。性能変更の最終採否は配布サーバーの共有ベンチで決める。

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

isuscopeは、全nodeへのSSH、ベンチ起動、ログとcollector、動的URLの正規化、会話履歴との紐付けを`isuscope doctor`で確認してから使う。計測結果は`isuscope-data/`へ保存し、軽量なrun履歴はGitへ残して、重要なrunだけ`isuscope pin`で生ログも残す。

## 初回ベンチを実行する

初期状態のまま`isuscope survey-run`を一度だけ実行し、標準観測と行動遷移を記録する。追加でスコアの振れ幅を見る場合は`survey-run`を繰り返さず、以後は通常の`isuscope run`を使う。手順は[初動自動化](../initial-automation.md)の「ベンチ前gateと初回run」を参照する。

確認するもの：

- スコアとシナリオ別結果
- エラーと失敗理由
- APIごとの件数とレイテンシー
- CPU、メモリ、ディスクI/O
- SQLの回数と実行時間
- ベンチ中に飽和した資源

## ベンチマークシナリオを分析する

`AGENTS.md`とそこから指定された追加指示、公式レギュレーション・当日マニュアルに従い、利用が認められた資料と計測結果から次を分かる範囲で整理する。

各項目に根拠を残し、確認済みの事実と観測からの推測を区別する。許可された資料から確認できない並列数・繰り返し条件・整合性チェックなどは不明と記録する。

- どのシナリオがあるか
- 各シナリオがどのAPIをどの順番で呼ぶか
- 何をすると得点が増えるか
- 何が失敗するとシナリオが止まるか
- 並列数や繰り返し条件
- initialize後に期待される状態
- 整合性チェックと失格条件
- 得点へ直接つながる処理

主要な処理について、次のつながりを整理する。

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

インデックスはSQLと初期化後のデータ量から根拠を残す。整合性条件を変えるもの、キャッシュ、メモリ保持、非同期化、サーバー分割はPhase 1では扱わない。

初回runから得た上位SQL・HTTP・エラーと候補を照合し、根拠が弱い変更は保留する。変更を反映した最初の`isuscope run`で大きく改善することを確認し、その後はボトルネックごとの短いサイクルに切り替える。

## 残すもの

- `docs/official/`
- `docs/benchmark-scenario.md`
- isuscopeによる初回ベンチ結果
- 初期状態のコードと設定
- ローカル実行環境の構成・データ範囲・動作検証結果
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
- 主要SQLの明らかなインデックス不足が解消されている
- 高頻度経路の主要なN+1、重複処理、逐次writeが解消されている
- 自明な修正を統合したrunをbaselineと比較し、採否を記録している

条件を満たしたら作業中のagent（CodexまたはClaude Code）はPhase 2への移行を提案し、人間が移行を決定する。旧Phase 2の自明な改善はこのPhaseへ統合済みで、現在のPhase 2はアーキテクチャ改善を扱う。

## scoutの準備

参照する会話の実セッションIDを`./scripts/scout conversation <agent> <session-id>`で指定する。シナリオは`docs/benchmark-scenario.md`へ整理する。Phase 1中にscoutは自動起動せず、人間がPhase 2への移行を決めた後に開始する。

## 非同期buildの開始と確認

`make kickoff`は先行回収・commit後、独立した初期build処理を開始して全台bootstrapを進める。worktreeやAIセッションの作成は行わない。

全台準備を待たず、`.local/phase1-build/status.json`と`environment.txt`を確認する。`needs_configuration`なら`config/phase1-build.example.json`を参考に、回収したCargo.toml・Cargo.lock・toolchain指定、remoteのCPU・OS・glibc・Rust版、追加Cライブラリを照合して`config/phase1-build.json`を作る。対象は`base_image`・`target`・`binary`・`dockerfile`。未記入のexampleを実設定へコピーしたままにしない。完成内容を一時ファイルに書き、renameで一括配置する。操作者自身の判断でよく、人間への追加承認は不要。

初期build処理は設定を最大30分待ち、設定完了後は既存の`local-build.py`を使って共有キャッシュへbuildする。設定が既にあれば調査・CPU照合後すぐbuildする。全台準備とbaselineはbuild処理の完了待ちを入れない。deployに必要なbuild完了は従来どおりdeployが保証する。

`configure-draft`はこの初期build設定をそのまま`local_builds`へ取り込む。draft生成より後に設定した場合は、設定済みとなった後にdraftを再生成するか、同じ設定をdraftへ反映してからreviewする。異なるimage・Dockerfile・targetに変える場合はキャッシュが別になることを認識する。設定とDockerfileは通常のcommitへ含める。

進行確認は`python3 scripts/phase1-build.py status`、失敗後の再開は`python3 scripts/phase1-build.py start`。ログは`.local/phase1-build/build.log`。同時起動は専用lockで防ぐ。練習停止時は`python3 scripts/phase1-build.py stop`で初期build処理も止める。停止してもソース・成果物・キャッシュは残す。ローカル検証用Composeはこのデプロイ用buildと別キャッシュであり、同時コンパイルによる端末の資源競合を避ける。
