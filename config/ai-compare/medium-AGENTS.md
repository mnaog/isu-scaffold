# 中難度タスクの共通指示

この独立リポジトリだけを作業対象にする。webapp/rust/src/の実装と必要なテストを変更してよい。
既存テストを削除・無効化・弱体化しない。ただしnew_user_cache_integration.rsの「登録直後cards.is_none」という旧期待のみ更新できる。
medium_acceptance.rsと、それを読み込むinclude!行は共通受入検証なので変更しない。
4ロール用SQLiteやfork運用は使わず、commitと最終回答を完了記録とする。

参照可能な資料はこのリポジトリのファイルと共通検証の出力だけ。元のpracticeリポジトリ、別試行、会話履歴、修正後commit、外部検索は参照禁止。
ベンチマーカー内部閲覧・ログ追加は禁止。deploy・ベンチ・subagent・別AIは実行しない。
検証はpython3 verify.py check / test / mysql / acceptanceを使う。固定Rust 1.63、オフライン依存、ローカルの使い捨てMySQLを利用する。
DB結合テストはこの専用DBだけを初期化する。Docker、SSH、AWSは使わない。
Cargo.toml、Cargo.lock、SQL、verify.py、AGENTS.md、TASK.txt、rust-toolchain.toml、.cargo/、.local/environment.jsonは変更しない。
.local/はcommitしない。独自テストを追加する場合はsrc/内に置く。
変更をcommitし、修正内容、検証結果、commit IDを最終回答に残す。
