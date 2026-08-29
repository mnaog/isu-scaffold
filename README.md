# ISUCON starter template

一人参加で、コードと設定をローカルリポジトリから管理・デプロイするためのテンプレートです。

## ディレクトリ

```text
webapp/          配布されたアプリケーション一式
config/          サーバーへ配置するミドルウェア・OS設定
infra/           AWS環境の構成定義
ansible/         全nodeの初期状態を揃えるplaybook
scripts/         ローカルから環境を操作するコマンド
docs/            公式情報、Phase、調査記録
AGENTS.md        CodexとClaude Codeで共有する作業指示
.claude/         Claude Code向けのAGENTS.md読込設定
.isuscope/       isuscopeの計測方法と問題固有設定
isuscope-data/   isuscopeが保存するrunと分析結果
.local/          端末・環境固有の情報（Git管理外）
```

それぞれの役割は次のとおりです。

| ディレクトリ | 内容 |
| --- | --- |
| `webapp/` | 配布されたアプリケーション、静的ファイル、DBスキーマ、初期化処理を保存します。ここを正本として各サーバーへデプロイします。 |
| `config/` | nginx、MySQL、systemd、sysctlなど、アプリケーション外の設定を種類ごとに保存します。サーバー上の配置先はデプロイスクリプトで明示します。 |
| `infra/` | CloudFormationなど、AWS環境を再現するための構成定義を保存します。認証情報や実行ごとに変わる出力は含めません。 |
| `ansible/` | SSH確立後のpackage、toolchain、isuscope helper、node検査を冪等に揃え、初期構成を調査します。問題固有値は変数へ設定します。 |
| `scripts/` | `import`、`deploy`、`restart`、`status`、`rollback`など、ローカルから環境を操作する処理を置きます。通常操作はMakefileから呼び出します。 |
| `docs/` | `official/`へ一次情報、`phases/`へ進行手順、`codex-history/`へ会話履歴を保存します。調査やシナリオ分析もここへ残します。 |
| `AGENTS.md` | このリポジトリで作業するAI向け指示の正本です。Codexは直接、Claude Codeはsymlink経由で読み込みます。 |
| `.claude/` | Claude Codeが`AGENTS.md`を起動時に読み込むためのrule symlinkを置きます。`CLAUDE.md`は作成しません。 |
| `.isuscope/` | node、collector、route正規化、ベンチ実行方法など、isuscopeの計測設定を管理します。 |
| `isuscope-data/` | スコア、仮説、分析、Git状態などをrun単位で保存します。生ログは既定で除外し、重要なrunだけpinします。 |
| `.local/` | Public IP、秘密情報、AWSの一時出力など、環境固有の情報を置きます。ディレクトリ全体をGit管理しません。 |

サーバーごとにコードを複製せず、役割の違いは設定とデプロイ処理で扱います。秘密情報は`.local/`へ置き、再現に必要な定義は`webapp/`、`config/`、`infra/`、`ansible/`、`scripts/`へ残します。

## 大会開始後の初動

環境を作成したら、環境変数の雛形をlocal stateへコピーして編集します。CloudFormationからの自動検出と、任意環境向けのstatic node定義を選べます。

```bash
mkdir -p .local
cp config/environment.example.env .local/environment.env
make discover
make bootstrap
```

`discover`はproviderからGit管理外のAnsible inventoryとisuscope実設定を再生成します。`bootstrap`はlocal venvへAnsibleを導入し、選択したSSH方式で接続して全nodeを同じ初期状態へ揃えます。

続いて初期構成を調査し、宣言ファイルでimport・deploy・ベンチ接続を完成させます。

```bash
make inspect
make configure-draft
# .local/draft/を確認・修正する
CONFIRM_DRAFT=true make configure-apply
make discover
make sync-check
make import
# 初期状態をcommitし、対象roleと検証・再起動commandを確認する
make deploy
make benchmark-check
make benchmark-probe
make phase1-check
make survey HYPOTHESIS="初期状態の負荷構造を記録する"
```

`configure-draft`は調査結果からnode role、同期対象、Ansible変数、isuscopeのlog path候補を`.local/draft/`へ作ります。所有者、配置先、service名は大会環境によって異なるため、人間が確認したdraftだけを明示的に反映します。

詳細は[初動自動化](docs/initial-automation.md)を参照してください。

## 基本フロー

```text
サーバーの初期状態をimport
  → webapp/とconfig/へ保存
  → 初期commit
  → ローカルで変更
  → コマンドでdeploy・再起動・状態確認
  → isuscopeでベンチ
  → 結果と変更をcommit
```

サーバー上を直接変更した場合は、直ちにローカルへ反映します。サーバーごとにコードを複製せず、役割の違いはデプロイ処理と設定で扱います。

変更系操作とベンチ実行は`.local/operation.lock`で排他されます。別のCodexセッションが実行中なら、PID・開始時刻・操作名を表示して終了します。異常終了で残ったlockは、記録されたprocessが存在しないことを確認して次回操作時に自動回収します。`status`や設定検査などのread-only操作は並行実行できます。

実環境の接続先や公式ファイルの配置が確定したあと、Phase 1で`config/sync.json`と`config/benchmark.env`を完成させます。ベンチはlocal、SSH、HTTP APIを標準adapterで扱えます。node上のbuildは`build_commands`でstaging切替前に実行でき、Rustの永続Cargo cache例は`config/sync.rust.example.json`にあります。標準の宣言で表せない独自APIだけ、大会構成へ合わせてadapterを拡張します。
