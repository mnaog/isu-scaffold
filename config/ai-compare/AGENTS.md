# 低難度タスクの共通指示

この独立リポジトリだけを作業対象にする。アプリケーションの正本はローカルのwebapp/。
webapp/rust/src/の実装だけを変更し、既存テストの削除・無効化・期待値の変更はしない。
この環境確認では4ロール用SQLiteやforkの運用は使わない。commitと最終回答を完了記録とする。

参照できる資料は、このリポジトリのファイルと共通検証コマンドの出力だけ。
元のpracticeリポジトリ、別の試行、会話履歴、修正後のcommit、外部検索は参照しない。
ベンチマーカー内部の閲覧・ログ追加は禁止。ベンチやdeployは実行しない。
subagentや別のAIは起動しない。

検証には `python3 verify.py check` と `python3 verify.py test` を使う。
共通スクリプトがこのMac上の固定したRust 1.63.0でオフライン検証する。
Docker、SSH、AWSは使わない。.local/の環境設定は変更・commitしない。
Cargo.toml、Cargo.lock、SQL、verify.py、AGENTS.md、TASK.txt、rust-toolchain.toml、.cargo/は変更しない。
testはDB不要の既存テストを実行する。ignoredのDB結合テストは今回の対象外。

変更をcommitして、原因、修正内容、検証結果、commit IDを最終回答に残す。
