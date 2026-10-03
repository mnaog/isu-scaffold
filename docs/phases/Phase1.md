# Phase 1: 初動・自明な改善・初回ベンチ分析

## 目的

公式から提供された一次情報を保存し、初期状態、ベンチマークの評価方法、主要な処理経路を把握する。

また、アプリケーションコードと設定ファイルをすべてローカルリポジトリで管理し、コマンドからサーバーへデプロイできる状態を作る。

セットアップとコード読解を直列に実行しない。SSH確立直後に採用言語のコードとDB schemaだけを先行importし、その初期commitを分岐点とする。mainで完全import、計測・deploy・初回baselineを準備する一方、別worktreeで安全で自明な改善を実装する。

初回baselineの比較可能性を守るため、並行worktreeの変更はbaseline取得後までremoteに反映しない。

## コード先行import後は並行して進める

```text
初期build: 1台目からCPU・OS・glibc・Rust版を調査
  → operatorが配布コードと照合してconfig/phase1-build.jsonを設定
  → 待機中のbuild処理が未修正コードを自動build（完全importを待たない）
  → 同じDockerfile・image・targetをdraftへ引き継ぎ、修正後は差分build

main: bootstrap、全node調査、完全import、role・deploy・benchmark adapter・isuscopeを準備
  → phase1-check
  → 未変更のbaselineをsurvey-run
  → スコア、シナリオ、HTTP、SQL、ホスト負荷を分析

ローカル実行環境: 回収済みコードに合わせてDB・依存service・初期化を構成
  → make local-up / local-check
  → workerが初期改善の動作確認・SQL調査に使う（baselineの開始を待たせない）

初期改善用の一つのworktree・同じセッション:
  採用言語とschemaを読み、改善候補を見つけた順に実装
  → 変更目的ごとにcommitし、localのformat・build・test
  → 検証済みcommitをoperatorへ渡し、残りの探索・修正を継続
  ← operatorから初回・以後の計測結果を受け取り、優先順位を更新

baseline分析後、受け渡しごと: mainを初期改善worktreeへ取り込む
  → 実測と変更根拠を照合
  → mainへ統合・deploy
  → 通常のisuscope runでbaselineと比較・採否
```

`kickoff`の途中でworktreeが表示された時点で初期改善セッションを起動する。`kickoff`終了や完全import、初回計測を待たない。operatorはセットアップを継続する。workerは`kickoff`がiTermの新しいタブへ自動起動する。既に起動済みなら同じlauncherを使い、終了済みの未完了タスクはoperatorが明示的に再開する。

このセッションは、通常workerへ1目的ずつ依頼する運用とは異なる。Phase 1全体で同じworktreeとコード読解の文脈を維持し、許可された範囲の自明な改善を自律的に見つけて修正する。個々の修正開始にoperatorの承認は挟まない。新しい常設ロールは増やさず、起動・記録上は`役割: worker`、`SCAFFOLD_ROLE=worker`に加えて「Phase 1の初期改善」を明示する。通常workerの1目的・完了後待機のルールは、この継続作業の途中の受け渡しには適用しない。

operatorは初回および以後の計測について、run ID、評価したcommit、上位SQL・HTTP・競合、エラー、判断と不明点を渡す。初期改善セッションはコード上の推測と実測を照合し、次の修正や既存修正の見直しに使う。キャッシュ・メモリ正本化・非同期化・サーバー分割などはPhase 2で扱う。

全候補の完成を待たず、検証済みcommitの範囲、変更根拠、検証結果、注意点をoperatorへ渡す。operatorは初回baseline分析後に受け取ったcommitを確認し、統合・deploy・ベンチ・採否を担当する。受け渡し済みcommitはamend/rebaseせず、後続の修正を新しいcommitとして積む。統合対象は動くbranch先端ではなく明示したcommitとし、operatorは作業中のworktreeを変更・削除しない。初期改善セッション自身が未commit変更を整理した区切りでmainを取り込む。

SQLiteでは「Phase 1の初期改善」を一つの継続タスクとして開始・見込みを記録する。途中の受け渡しは`working`のまま`worker_handoff`へ検証済みcommit・検証結果・配布要件を記録し、operatorは`worker_integrate`へ対象handoff IDを記録する。worker本人のnotesは変更しない。部分的な統合でタスク全体を`integrated`にしない。人間がPhase 2移行を決める際に残件を引き継ぎ、継続作業を終了して`developed`を記録する。operatorは最終成果の統合または見送りを確認してタスクを閉じ、その後は通常の1目的workerへ切り替える。

先行importはコード読解開始用の暫定snapshotである。mainの完全importで全配布先のdigest一致と設定を改めて確認する。並行worktreeは`webapp/`のコード・schemaとそのテストを所有する。mainは`config/`、`ansible/`、`scripts/`、`.isuscope/`、remote操作を所有する。競合を避けられない変更は、先に小さいcommitへ分離する。

## ローカル実行環境を構築する

Phase 0ではDockerと汎用の起動・検証コマンドだけを準備する。Phase 1のコード先行回収後、operatorが`config/local/compose.example.yaml`から`config/local/compose.yaml`を作り、採用Rust版・DB・追加service・schema・初期化・health endpointを実アプリに合わせる。手順は[ローカル実行環境](../local-development.md)を参照する。

構成をcommitして初期改善worktreeへ取り込んだら、worker自身が`make local-up`、`make local-check`、`make local-down`を実行してよい。新しい常設ロールは増やさない。DB・network・portはworktree単位で分離し、remote変更用lockを取らない。構築は初動と並行し、完成を初回baselineのgateにはしない。

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

初回baselineの準備と並行して、次をコードから抽出・実装する。

- WHERE、JOIN、ORDER BYに対する明らかなインデックス不足
- ループ内SQLや要素ごとの更新などのN+1
- 同一request内の重複query・重複計算
- 単一行のカウンタや必要以上のlocking readによる直列化
- 一括INSERT・UPDATE・UPSERTに置き換えられる逐次write
- transaction内の不要な処理とDB往復
- 明らかに不要なカラム、全件走査、外部呼び出し

インデックスはSQLと初期化後のデータ量から根拠を残す。整合性条件を変えるもの、キャッシュ、メモリ保持、非同期化、サーバー分割はこの並行レーンでは扱わない。

初回runから得た上位SQL・HTTP・エラーと候補を照合し、根拠が弱いcommitは統合しない。統合後は変更をまとめた最初の`isuscope run`で大きく改善することを確認し、その後はボトルネックごとの短いサイクルに切り替える。

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
- ローカル環境で実施した検証と未対応範囲が記録され、初期改善から利用できる（構築不能なら理由と代替の検証手段を記録）
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

## 3役の準備

operatorのClaude CodeとCodexの実セッションIDを`./scripts/scout operator <agent> <session-id>`で登録する。workerは[worker手順](../roles/worker.md)に従って開始・開発完了を共通SQLiteへ記録する。シナリオの整理先は`docs/benchmark-scenario.md`。利用資料は適用される公式ルールとAGENTS.mdの指示に従う。
Phase 1中にscoutは自動起動しない。人間がPhase 2への移行を決めた後に開始する。

## 3並列の開始と確認

`make kickoff`は先行回収・commit・worktree作成後、独立した初期build処理を開始し、iTermの対話型workerを起動してから全台bootstrapを進める。workerのCLI起動確認は最大20秒で、task登録確認は全台準備の後に行う（最大120秒）。親IDは起動ファイルでも明示し、開始登録・CLI紐付けまで成功しなければkickoffは非0で終了する。起動失敗時も全台準備を継続するが、最後に非0で終了し、operatorへ再起動を促す。

operatorは全台準備を待たず、`.local/phase1-build/status.json`と`environment.txt`を確認する。`needs_configuration`なら`config/phase1-build.example.json`を参考に、回収したCargo.toml・Cargo.lock・toolchain指定、remoteのCPU・OS・glibc・Rust版、追加Cライブラリを照合して`config/phase1-build.json`を作る。対象は`base_image`・`target`・`binary`・`dockerfile`。未記入のexampleを実設定へコピーしたままにしない。完成内容を一時ファイルに書き、renameで一括配置する。操作者自身の判断でよく、人間への追加承認は不要。

初期build処理は設定を最大30分待ち、設定完了後は既存の`local-build.py`を使って共有キャッシュへbuildする。設定が既にあれば調査・CPU照合後すぐbuildする。全台準備とbaselineはbuild処理の完了待ちを入れない。deployに必要なbuild完了は従来どおりdeployが保証する。

`configure-draft`はこの初期build設定をそのまま`local_builds`へ取り込む。draft生成より後に設定した場合は、設定済みとなった後にdraftを再生成するか、同じ設定をdraftへ反映してからreviewする。異なるimage・Dockerfile・targetに変える場合はキャッシュが別になることを認識する。設定とDockerfileは通常のcommitへ含める。

進行確認は`python3 scripts/phase1-build.py status`、失敗後の再開は`python3 scripts/phase1-build.py start`。ログは`.local/phase1-build/build.log`。同時起動は専用lockで防ぐ。練習停止時はiTerm内のworkerを終了し、`python3 scripts/phase1-build.py stop`で初期build処理も止める。停止してもソース・成果物・キャッシュは残す。ローカル検証用Composeはこのデプロイ用buildと別キャッシュであり、同時コンパイルによる端末の資源競合を避ける。
