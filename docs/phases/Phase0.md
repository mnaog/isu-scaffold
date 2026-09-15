# Phase 0: 大会用リポジトリの準備

## 目的

完成済みのスターターテンプレートから大会用リポジトリを作成し、開始後すぐにPhase 1へ入れる状態にする。

## やること

- テンプレートを大会用ディレクトリへコピーする
- `git init`して初期状態をコミットする
- GitHubにprivateリポジトリを作成してpushする
- `make repo-init REPO=<name>`を使う場合はGitHub CLIの認証を確認する
- `./scripts/ansible-install.sh`を実行し、controllerでAnsibleが起動することを確認する
- 作成したリポジトリがprivateであることを確認する
- 大会用リポジトリ内からCodexを起動する
- Codex履歴がそのリポジトリへ保存されることを確認する
- `git status`がcleanな状態で開始を待つ

Codexはリポジトリの作成と初回pushまで実行してよい。リポジトリ名や作成先が不明な場合は人間へ確認する。

## 完了条件

大会用のprivateリポジトリが作成され、問題公開後すぐにPhase 1の調査を開始できる。
