# Agent conversation history

このディレクトリには、このGitリポジトリで行ったCodex・Claude Codeとの会話を、読みやすいMarkdownへ縮約して保存します。

## 生成方法

このディレクトリと履歴ファイルは、両agentのhookから同じスクリプトで自動生成されます。

- 生成スクリプト: `~/.agent-history/agent_history.py`
- Codex: `~/.codex/hooks.json`（`~/.codex/hooks/repo_conversation_log.py`経由）
- Claude Code: `~/.claude/settings.json`
- `[User]`: `UserPromptSubmit` で受け取ったユーザーの発言
- `[Codex]`／`[Claude]`: `Stop` で受け取ったagentの最終回答
- `[Commit]`: Bashの`PostToolUse`で検出した、成功した `git commit` のハッシュと件名。別リポジトリでのcommitはリポジトリ名付きで記録します

Gitリポジトリ直下に `.agent-history-disable` がある場合、この履歴は生成されません。

## このディレクトリのファイル

`YYYYMMDD-HHMMSS.md` は、1つのagentセッションに対応します。ファイル名は、そのセッションで最初に記録されたユーザー発言のローカル時刻です。同じ秒に複数セッションが始まった場合のみ、末尾に連番が付きます。headerの`- Agent:`がCodexかClaude Codeかを示します。`- Agent:`のないファイルは、共通化前のCodex履歴です。

履歴にはユーザー発言、agentの最終回答、成功したコミットだけを残し、推論、ツール操作、途中経過は原則として省きます。HTMLコメントには、重複防止と対応付けに使うagent、セッションID、ターンID（Codexは`turn_id`、Claude Codeは`prompt_id`）、内部状態が入っています。isuscopeの`[context.agent]`はこのheaderとマーカーでrunと会話を紐付けます。

## 生履歴

このディレクトリのMarkdownは縮約版です。ローカルの生履歴は通常、次の場所にあります。

- Codex: `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`
- Claude Code: `~/.claude/projects/<project>/<session>.jsonl`

## 取り扱い上の注意

会話には、リポジトリへコミットすべきでない情報が含まれる場合があります。Gitへ追加する前に内容を確認してください。この `Readme.md` は初回だけ自動生成され、既存ファイルは上書きされません。
