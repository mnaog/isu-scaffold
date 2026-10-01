# 接続・動作検証（2026-10-01 JST）

| 構成 | 実機の結果 |
| --- | --- |
| Claude Code 2.1.284 / `claude-opus-5-5` | `claude auth status`でログイン済みを確認。`once claude --smoke`が「接続確認済み」を返した。初回は既存設定の`opus`で実行し、応答の`modelUsage`でIDを確認、明示IDに設定し直して再検証成功。 |
| Codex CLI 0.159.2 / `gpt-6-astra` | `codex login status`でChatGPTログイン済みを確認。既存設定のモデルIDを明示し、`once codex --smoke`が「接続確認済み」を返した。JSONの`thread.started`から実セッションIDも取得できた。 |
| Kimi Code / Kimi K3 | PATHと一般的なローカルbinディレクトリにCLIが見つからない。`once kimi --smoke`は利用不可理由を表示。実CLI起動・認証・正確なモデルID・非対話引数は未確認。代替モデルは使用していない。 |
| OpenCode 1.18.34 / DeepSeek V4.1 Flash (Lithos Base) | 通常利用とscoutを `lithosai/deepseek-ai/DeepSeek-V4.1-Flash` に統一。`opencode models lithosai`で登録を確認し、`once opencode --smoke`がexit 0で「接続確認済み」を返した。以前のZen接続検証も成功済み。 |

実応答・モデル情報・実行ログは`.local/operations/smoke/`に保存。認証情報は転記していない。CLIの会話記録は既存hookが通常通り生成するが、operatorとして登録しないためscout入力には入らない。

## 自動テストと画面

`make operations-test`で一時Gitリポジトリと偽CLIを使用し、以下を検証する。

- workerの開始、見込み更新、詰まり、開発完了、統合済み。検証・結果commitなしの完了を拒否。
- task/親子セッション/worktree/base commitの記録、複数worktreeの共通DB参照、SQL transactionのrollback。
- CLI正常終了が作業完了へ遷移しないこと。
- 300文字の保存、301文字の拒否、最新報告の上書き、Git用出力、失敗時の前回報告保持。
- 偽時計で投稿処理の完了から900秒後を確認。短い設定間隔で実際の再起動・新規セッションを確認。
- 同じscoutと常駐処理の多重起動防止、停止、再開時の予定保持、timeout、失敗間隔の上限。
- 常駐処理をSIGKILLした場合のCLI終了。
- 明示したoperatorのAgent/Session両方による会話選択、run選択と比較selector、HTTPのGET限定。

既存の`bash tests/initial-automation.sh`も成功。実ブラウザで未設定・利用不可の表示、操作ボタンがないことを確認。ブラウザ内の模擬レスポンスでworkerの開発完了、4体の「発見なし」、HTTP・SQL・ホスト負荷・スコア比較の表示も確認した（DBへ模擬データを登録していない）。

このscaffoldには`.isuscope/config.toml`、完了run、`docs/benchmark-scenario.md`がまだない。実runを用いた計測内容の表示・比較と、実環境を探索するscout実行は未確認。既存isuscopeのCLI helpと出力構造を確認して実装し、偽入力で取得・表示を検証した。運用の定期実行は開始していない。

## 実機側で残る準備

OpenCodeはLithos無印への切り替えとsmokeを完了。[現在の接続手順](opencode-lithos.md)を参照する。

Kimi Codeを導入・認証し、指定モデルの利用可否と正式IDを確認する。`config/operations.json`または端末専用の`.local/operations.json`へ正確なargv/modelを設定し、該当する`unavailable`を外してsmokeを実行する。モデルが提供されていなければ利用不可のままにする。

Phase 1のシナリオ整理とisuscope初期設定・初回計測を既存手順で行い、operator 2セッションのIDと比較元runを設定する。人間がPhase 2移行を決定後、入力プレビューを確認して開始する。`spawn-forked-session`の改修・接続は今回の範囲外で、[worker手順](worker.md)に環境変数とSQLの契約を用意した。

OpenCode JSON adapterの追加テストを含む`make operations-test`は15件成功。探索途中のtextとtool出力を除外し、最後の正常完了messageの本文だけを採用し、error・未完了応答は拒否する。
