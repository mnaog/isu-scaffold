# 準備状況

判定: **未完了**

`scripts/new-contest-repo.py`が作った時点のひな形です。項目は[準備](../guides/preparation.md)に対応します。状態は「確認済み」「未完了」「対象外」「公開待ち」のどれかにし、確認日・使用版・根拠を書きます。「公開待ち」は本番でも開始前に取得できない情報に限ります。長い検証記録は`docs/preparation/`の別fileに置き、ここから参照します。秘密情報と生の認証出力は`.local/`へ置きます。

- 作成元: {{SOURCE}}
- 作成日時: {{CREATED_AT}}
- 種別: {{MODE}}

## repoと再現条件

| 項目 | 状態 | 確認日 | 使用版・根拠 |
| --- | --- | --- | --- |
| private GitHub repoへのpushとprivateであることの確認 | 未完了 | | |
| scaffold・isuscope・agent-history・各AI CLIの使用版 | 未完了 | | scaffoldは`docs/preparation/scaffold-source.json` |
| AIの初期入力・起動プロンプト・モデル・推論設定・ハーネス設定 | 未完了 | | |
| commit・push、SSH鍵、`.local/`の除外、`.claude/rules/agents.md`のsymlink | 未完了 | | |

## ローカル実行環境

| 項目 | 状態 | 確認日 | 使用版・根拠 |
| --- | --- | --- | --- |
| git、gh、bash、make、SSH、curl、tar、jq、zstd、Python 3.12以上 | 未完了 | | |
| `./scripts/ansible-install.sh --all`（legacy・modernのlocalhost） | 未完了 | | |
| isuscopeのbuild/installと必要なコマンド | 未完了 | | |
| Rust・Cargo・linkerと、問題を使わないbuild・test | 未完了 | | |
| `./scripts/fetch-observability-tools.sh`（slp・FlameGraph） | 未完了 | | |
| `make isuscope-node`と`./scripts/build-isuscope-node.sh --check`（BUILD_ID） | 未完了 | | |
| `make builder-images`（所要時間、取得できなかったtag） | 未完了 | | |
| `bash tests/kickoff-automation.sh`と`make test` | 未完了 | | |

## AIと運用経路

| 項目 | 状態 | 確認日 | 使用版・根拠 |
| --- | --- | --- | --- |
| 利用予定のAI CLIの認証と、利用モデルでの新規セッション応答 | 未完了 | | |
| 各CLIの会話履歴の保存 | 未完了 | | |
| lane用の`claude -p`（`config/lanes.json`のmodel・effort・permission mode） | 未完了 | | |
| ボード（`make board`）の表示 | 未完了 | | |
| 禁止操作のhook（`.claude/settings.json`、`.codex/hooks.json`、`config/tool-guard.json`） | 未完了 | | |

## アカウント・接続・参加準備

| 項目 | 状態 | 確認日 | 使用版・根拠 |
| --- | --- | --- | --- |
| AWS profile・region、事前公開された権限・quota・ネットワーク条件 | 未完了 | | |
| GitHub・AI CLIの利用とSSH鍵 | 未完了 | | |
| 公式レギュレーションの再確認（`docs/official/`） | 未完了 | | |
| 参加登録・連絡手段・ポータル・当日前の設定期限 | 未完了 | | 本人しか確認できない項目は本人の確認を記録する |

## ポータル

| 項目 | 状態 | 確認日 | 使用版・根拠 |
| --- | --- | --- | --- |
| chrome-debug（port 9222）の起動とPlaywright MCPからの接続 | 未完了 | | |
| ポータルのURLとchrome-debugでのログイン（開始時のログインし直しの要否） | 未完了 | | |
| `scripts/portal-cookie.py --check` | 未完了 | | |
| worktree `portal`（`make worktree BRANCH=portal`） | 未完了 | | |

## improveの作業場所

| 項目 | 状態 | 確認日 | 使用版・根拠 |
| --- | --- | --- | --- |
| worktree `improve`（`make worktree BRANCH=improve`）で`/improve`が動き、`scripts/runs.sh list`がrootのrunを読める | 未完了 | | |

## 人間の作業場所

| 項目 | 状態 | 確認日 | 使用版・根拠 |
| --- | --- | --- | --- |
| worktree `human`（`make worktree BRANCH=human`）と`scripts/scout/main.py post human`の確認 | 未完了 | | |
