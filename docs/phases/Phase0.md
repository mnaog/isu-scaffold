# Phase 0: 大会用リポジトリの準備

## 目的

完成済みのスターターテンプレートから大会用リポジトリを作成し、開始後すぐにPhase 1へ入れる状態にする。

## やること

- テンプレートを大会用ディレクトリへコピーし、`git init`して初期状態をコミットする
- `gh repo create <owner>/<name> --private --source . --remote origin --push`でGitHubにprivateリポジトリを作成してpushし、privateであることを確認する
- `./scripts/ansible-install.sh`を実行し、controllerでAnsibleが起動することを確認する
- `isuscope --version`で`lock`、`pin`、`routes`を含む最新のisuscopeが入っていることを確認する
- 会話履歴hookを導入する（`git clone git@github.com:mnaog/agent-history.git ~/project/agent-history && ~/project/agent-history/install.sh`）。導入済みでも`git pull`してから`install.sh`を再実行してよい
- 大会用リポジトリ内で新しいCodexまたはClaude Codeのセッションを開始して1往復し、`docs/agent-history/`へ履歴が保存されることを確認する
- `git status`がcleanな状態で開始を待つ

作業中のagentはリポジトリの作成と初回pushまで実行してよい。リポジトリ名や作成先が不明な場合は人間へ確認する。

## 完了条件

大会用のprivateリポジトリが作成され、問題公開後すぐにPhase 1の調査を開始できる。
