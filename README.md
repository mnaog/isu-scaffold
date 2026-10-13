# ISUCON starter template

一人参加のISUCONで、コードと設定をローカルリポジトリから管理・デプロイし、isuscopeで計測と改善履歴を残すためのテンプレートです。採用言語はRustに固定しています。

## 使い方

```bash
mkdir -p .local
cp config/environment.example.env .local/environment.env   # provider、SSH、node分類を設定

make kickoff                           # コード先行回収・初期build・draft生成と実nodeでの検査
CONFIRM_DRAFT=true make kickoff-apply  # .local/draft/review.mdを判断してから反映・完全import
make deploy
make phase1-check
isuscope survey-run --hypothesis "初期状態の負荷構造を記録する"
```

| 資料 | 内容 |
| --- | --- |
| [AGENTS.md](AGENTS.md) | ディレクトリの役割、作業ルール、isuscopeの運用（Codex・Claude Code共通の指示） |
| [docs/initial-automation.md](docs/initial-automation.md) | 初動の各コマンド、draft検査、deploy、ベンチ接続、isuscope設定の詳細 |
| [docs/phases/](docs/phases/) | Phaseごとの目的、判断基準、完了条件 |

## templateの検査

`scripts/`などを変更してpushすると、GitHub Actionsの`test` workflowがisuscopeをbuildし、shell構文と`tests/initial-automation.sh`を実行します。EC2やSSHは使わず、偽のremoteでdiscover、lock、import、deploy、rollback、draft検査などを検査します。手元で確認する場合は`./tests/initial-automation.sh`を直接実行します。

## Phase 1のローカル実行・ビルド

コード回収後、`config/local/compose.example.yaml`を当日のアプリに合わせて`compose.yaml`へコピーし、`make local-up` / `make local-check` / `make local-down`で検証します。DB・network・portはworktreeごとに分離し、初回baselineの開始を待たせません。[ローカル実行環境](docs/local-development.md)と[Phase 1](docs/phases/Phase1.md)を参照してください。

配布用Rustは`make build`でローカルからLinux向けにbuildし、`make deploy`が同じ成果物を全対象nodeへ配ります。Docker cacheを再利用し、配布サーバーでのコンパイルを既定から外しています。Rustは1.99.0を標準とし、target・OS・実行時依存は当日の環境に合わせて設定してください。

## Rust toolchain

通常開発・CI・LinuxビルダーはRust 1.99.0を使用する。Macはrustup経由のcargo/rustcを使用し、`rustup show active-toolchain`で確認する。Linuxビルダーは配布先と互換性のあるOS/glibcを維持し、コンパイラだけを更新する。Editionと各アプリのMSRVは変更しない。隔離されたAI比較実験の旧toolchainは再現用に保持する。
