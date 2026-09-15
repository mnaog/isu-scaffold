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

## 環境を立ち上げて初期状態を保存する

コマンドと各段階の詳細は[初動自動化](../initial-automation.md)を正とする。ここでは判断基準だけを定める。

- 練習でAWS環境を自分で作る場合は、構成定義を`infra/`へ保存し、停止・再開・削除をコマンドで行えるようにする。認証情報はGitへ保存しない
- `make kickoff`が出す`.local/draft/review.md`のFAILはすべて直し、WARNは内容を読んで判断してから`make kickoff-apply`へ進む。draftのremote path、owner、service、roleは推測にすぎない
- nodeの分類が誤っている場合はprovider入力やdraftを直して再生成し、生成物を手作業で直し続けない
- importで配布先のdigestが一致しない場合は、sourceを確認するまで進めない
- 初期状態をcommitし、`make deploy`、`make status`、明示的な`make rollback`で稼働プロセスまで旧構成へ戻ることを確認する
- package導入は、レギュレーションを確認してからAnsible変数で明示的に有効化する
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

条件を満たしたら作業中のagent（CodexまたはClaude Code）はPhase 2への移行を提案し、人間が移行を決定する。旧Phase 2の自明な改善はこのPhaseへ統合済みで、現在のPhase 2はアーキテクチャ改善を扱う。
