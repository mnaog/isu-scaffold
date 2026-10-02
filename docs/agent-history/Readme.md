# Agent conversation history

このディレクトリには、このGitリポジトリで行ったCodex・Claude Code・OpenCodeとの会話を、読みやすいMarkdownへ縮約して保存します。

## 生成方法

このディレクトリと履歴ファイルは、各agentのhook／pluginから同じスクリプトで自動生成されます。

- 生成スクリプト: `~/.agent-history/agent_history.py`
- Codex: `~/.codex/hooks.json`（`~/.codex/hooks/repo_conversation_log.py`経由）
- Claude Code: `~/.claude/settings.json`
- OpenCode: `~/.config/opencode/plugins/agent-history.js`（XDG_CONFIG_HOME対応）
- `[User]`: `UserPromptSubmit` で受け取ったユーザーの発言
- `[Codex]`／`[Claude]`／`[OpenCode]`: agentの最終回答
- `[Commit]`: Bashの`PostToolUse`で検出した、成功した `git commit` のハッシュと件名。別リポジトリでのcommitはリポジトリ名付きで記録します

Gitリポジトリ直下に `.agent-history-disable` がある場合、この履歴は生成されません。

## このディレクトリのファイル

`YYYYMMDD-HHMMSS.md` は、1つのagentセッションに対応します。ファイル名は最初に記録されたイベントのローカル時刻です。同じ秒に複数セッションが始まった場合のみ、末尾に連番が付きます。headerの`- Agent:`がagentを示します。`- Agent:`のないファイルは、共通化前のCodex履歴です。

本文には会話・コミットと、操作ごとの短い時系列を保存します。各操作は時刻・ツール名・対象・終了状態・出力の先頭120文字と詳細リンクを1行にまとめます。リンクはJSONLの該当行を指します。同名の `.events.jsonl` には、受信した入力・出力を省略せず、セッション・ターン・呼び出しID、時刻、終了状態とともに保存します。OpenCodeの途中の公開テキストも記録します。推論は収集しません。分析・評価・重要度の選別は行いません。

`event_id` でMarkdownとJSONLを対応付け、`call_id` で開始・終了を結びます。`received_at` は記録処理の受信時刻、`source_at`／`started_at`／`ended_at` は提供元が渡した時刻です。`duration_ms` は提供元が渡した実行時間だけを保存し、不明は `null` とします。表示時刻はローカル時刻です。hookの受信時刻を使う行には「通知」と明記します。開始・終了hookの受信間隔には承認待ちやhook自身の時間が含まれ得ます。終了通知がなければ開始だけが残ります。`completed` は完了通知を受け取った意味で、成功の保証ではありません。

HTMLコメントには、重複防止と対応付けに使うagent、セッションID、ターンID（Codexは`turn_id`、Claude Codeは`prompt_id`、OpenCodeはユーザーメッセージID）、内部状態が入っています。isuscopeの`[context.agent]`は従来のheaderとマーカーでrunと会話を紐付けます。

## 生履歴

このディレクトリのMarkdownは縮約版です。ローカルの生履歴は通常、次の場所にあります。

- Codex: `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`
- Claude Code: `~/.claude/projects/<project>/<session>.jsonl`

## 取り扱い上の注意

会話には、リポジトリへコミットすべきでない情報が含まれる場合があります。Gitへ追加する前に内容を確認してください。この `Readme.md` は初回だけ自動生成されます。更新は `python3 ~/.agent-history/agent_history.py --refresh-readme <リポジトリ>` で明示的に行います。

## 補足メタデータと作業区間

`role`、`task_id`、`parent_session_id`、`run_id`、`phase`、`model`を共通項目として記録します。役割等は起動側の明示情報だけを採用し、コマンド本文から推測しません。不明はnull、未分類ツールのphaseはtoolです。

Codexの呼び出しIDと実セッションIDが一致する生ログのCommandExecutionから、実行時間・終了コードをtool_metadataとして追記します。元のhookイベントは変更しません。同じcall_idの補足なので、ツール件数や実行時間を二重計上しないでください。生ログ未提供・未対応形式・終了記録未到着では補足できません。通知時刻から実行時間は算出しません。

Claudeは同一セッションの実応答のモデル名を読み、unknownだったヘッダーを更新します。生ログ本文や推論は転記しません。ヘッダーは初めに確認したモデル、イベントのmodelはその時点で確認したモデルです。

起動時のSCAFFOLD_ROLE、SCAFFOLD_TASK_ID、SCAFFOLD_PARENT_SESSION_IDと、AGENT_HISTORY_RUN_ID、AGENT_HISTORY_PHASEを記録できます。実測した処理区間は `--agent codex --span build --session-id <実ID> --run-id <run ID> -- <コマンド>` で記録できます。phaseはtool/build/test/deploy/benchmark/postprocess/model_waitから明示指定します。model_waitは実際のモデル呼出しを囲む場合だけ使い、未分類時間をモデル待ちへ振り替えません。
