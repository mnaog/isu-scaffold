# Phase 1: 初動・自明な改善・初回ベンチ分析

## 目的

公式から提供された一次情報を保存し、初期状態、ベンチマークの評価方法、主要な処理経路を把握する。

また、アプリケーションコードと設定ファイルをすべてローカルリポジトリで管理し、コマンドからサーバーへデプロイできる状態を作る。

セットアップとコード読解を直列に実行しない。SSH確立直後に採用言語のコードとDB schemaだけを先行importし、その初期commitを分岐点とする。mainで完全import、計測・deploy・初回baselineを準備する一方、別worktreeで安全で自明な改善を実装する。

初回baselineの比較可能性を守るため、並行worktreeの変更はbaseline取得後までremoteに反映しない。

## コード先行import後は2レーンで進める

```text
main: bootstrap、全node調査、完全import、role・deploy・benchmark adapter・isuscopeを準備
  → phase1-check
  → 未変更のbaselineをsurvey-run
  → スコア、シナリオ、HTTP、SQL、ホスト負荷を分析

別worktree: 採用言語とschemaを読む
  → インデックス不足、N+1、直列化、重複SQL、過大なtransactionを抽出
  → 安全な修正を小さいcommitに分ける
  → localのformat・build・testだけ実行

baseline分析後: mainを並行worktreeへ取り込む
  → 実測と変更根拠を照合
  → mainへ統合・deploy
  → 通常のisuscope runでbaselineと比較・採否
```

先行importはコード読解開始用の暫定snapshotである。mainの完全importで全配布先のdigest一致と設定を改めて確認する。並行worktreeは`webapp/`のコード・schemaとそのテストを所有する。mainは`config/`、`ansible/`、`scripts/`、`.isuscope/`、remote操作を所有する。競合を避けられない変更は、先に小さいcommitへ分離する。

## 公式情報を保存する

大会運営から提供された情報を`docs/official/`へ保存する。

対象には次を含める。

- レギュレーション
- 問題文
- アプリケーションマニュアル
- API仕様
- ベンチマーカーの仕様
- スコア計算方法と失格条件
- サーバー構成とスペック
- 提出、再起動、終了時の手順
- 当日アナウンス
- FAQ、訂正、追加情報
- 配布されたその他の文書

公式情報は要約だけで済ませず、可能な限り原文を保存する。URLから取得した場合は、取得元URLと取得日時も記録する。

認証情報、Cookie、SSH秘密鍵などは保存しない。

## AWS環境を立ち上げる

公式手順に従って、大会・練習用のAWS環境を立ち上げる。

- 使用するAWSアカウントとリージョンを確認する
- CloudFormationなどの構成定義をローカルへ保存する
- コマンドから環境を作成できるようにする
- 作成されたサーバー、IPアドレス、役割を確認する
- SSH接続を確認する
- 停止、再開、削除方法を確認する
- AWSの認証情報はGitへ保存しない

AWS環境の構成定義もローカルを正とする。

## 接続先を発見して初期状態を揃える

`config/environment.example.env`を`.local/environment.env`へコピーし、CloudFormationまたはstatic provider、SSH、node分類を設定する。

```bash
make discover
./scripts/bootstrap.sh
```

`discover`が生成した`.local/nodes.snapshot.json`、`.local/ansible-inventory.json`、`.isuscope/config.toml`を確認する。nodeの分類が誤っている場合はprovider入力を直して再生成し、生成物を手作業で直し続けない。

`bootstrap`はoperator鍵、任意のpackage、isuscope fingerprint helperを全nodeへ冪等に配置する。package導入と必須command・serviceは`ansible/playbooks/group_vars/all.yml`で明示する。レギュレーション確認前は自動package導入を有効にしない。

`./scripts/inspect-environment.sh`で初期構成を調べ、`./scripts/configure-draft.sh`でnode role、回収・配布対象、Ansible変数、log pathの候補を作る。`.local/draft/`のremote path、owner、service、roleを確認した後だけ`CONFIRM_DRAFT=true ./scripts/configure-apply.sh`で反映し、`make discover`を再実行する。

`./scripts/import.sh`は対象node間のdigestを比較してから初期状態を回収する。不一致ならsourceを確認するまで進めない。初期状態をcommitし、全台preflight・staging・失敗時のtransaction rollbackを行う`make deploy`と、role別の`make status`が通ることを確認する。`rollback_commands`には旧ファイル復元後のconfig検査、restart/reload、health checkを定義し、明示rollbackで稼働プロセスまで旧構成へ戻ることを確認する。

`config/benchmark.env`へlocal、SSH、HTTP APIのいずれかのベンチ起動方法、起動しないprobe、実出力sample、score・PASS/FAILの規則を設定し、`.isuscope/benchmark.sh --check`と`.isuscope/benchmark.sh --probe`を通す。ベンチ接続はisuscopeの`command` modeを標準とし、手動入力の`external` modeを通常運用にしない。

その後、次をベンチ前のgateにする。

```bash
make phase1-check
```

この検査はshellとAnsibleの構文、SSH、disk、必須service、sync manifest、benchmark adapter、isuscope設定を確認するが、ベンチは起動しない。

## 初期状態を保存する

- 配布されたコードを`webapp/`へ保存する
- 各サーバーで使用されている設定ファイルを回収し、種類ごとに`config/`へ保存する
- CloudFormationなどのAWS構成定義を`infra/`へ保存する
- 接続先や端末固有の一時情報は、Git管理外の`.local/`へ保存する
- Gitで初期状態をコミットする
- 各サービスの起動、停止、再起動方法を確認する
- 初期状態へ戻す方法を確保する

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

## デプロイを準備する

ローカルのコードと設定を、コマンドからサーバーへ反映できるようにする。

デプロイ処理には必要に応じて次を含める。

- アプリケーションのビルドと配置
- node上でbuildする場合の永続cacheと、live切替前の成果物検証
- 設定ファイルの配置
- 設定ファイルの検証
- サービスのreloadまたはrestart
- デプロイ後の状態確認
- 失敗時のファイル復元と、旧構成でのrestart/reload・状態確認

手作業のコピーを通常のデプロイ手順にしない。

初期状態をデプロイしたあと、サービスが正常に起動し、ベンチマークを実行できることを確認する。

## isuscopeを準備する

- サーバーとSSH接続を設定する
- ベンチマークの実行方法を設定する
- 必要なログとcollectorを設定する
- 動的URLの正規化を設定する
- Codex・Claude Codeの会話履歴を`docs/agent-history`へ紐付ける
- 計測結果をリポジトリ内の`isuscope-data/`へ保存する
- 軽量なrun履歴はGitへ残し、重要なrunだけ生ログもpinする
- `isuscope doctor`を成功させる

動的URLが初回runで細分化された場合は、`isuscope routes suggest <run-id> --output .local/route-suggestions.toml`で候補を作り、確認した規則だけ`.isuscope/routes.toml`へ反映する。

## 初回ベンチを実行する

初期状態のまま`isuscope survey-run`を一度だけ実行し、標準観測と行動遷移を記録する。

```bash
isuscope survey-run --hypothesis "初期状態の負荷構造とベンチシナリオを記録する"
isuscope brief latest
isuscope query latest --metric-prefix benchmark. --group-by scenario --limit 100
isuscope analyze RUN_ID supported --analysis "初回観測の結果と判断"
```

run IDは実行結果か`isuscope list`で確認する。追加でスコアの振れ幅を見る場合は`survey-run`を繰り返さず、以後は通常の`isuscope run`を使う。

確認するもの：

- スコアとシナリオ別結果
- エラーと失敗理由
- APIごとの件数とレイテンシー
- CPU、メモリ、ディスクI/O
- SQLの回数と実行時間
- ベンチ中に飽和した資源

## ベンチマークシナリオを分析する

ベンチマーカーのコードや公式仕様を読み、次を整理する。

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
- ローカルから実行できるデプロイコマンド
- 初期状態へ戻せるGitコミット

## 完了条件

- 公式から提供された一次情報が`docs/official/`に揃っている
- コードと設定ファイルがローカルリポジトリで管理されている
- ローカルからコマンドでデプロイできる
- サーバーを直接編集せずに構成を再現できる
- 初期状態へ戻せる
- isuscopeでベンチと計測を実行できる
- ベンチマークの得点、失敗条件、主要シナリオを説明できる
- 主要シナリオとアプリ内の処理経路が対応付けられている
- 主要SQLの明らかなインデックス不足が解消されている
- 高頻度経路の主要なN+1、重複処理、逐次writeが解消されている
- 自明な修正を統合したrunをbaselineと比較し、採否を記録している

条件を満たしたらCodexはPhase 2への移行を提案し、人間が移行を決定する。旧Phase 2の自明な改善はこのPhaseへ統合済みで、現在のPhase 2はアーキテクチャ改善を扱う。
