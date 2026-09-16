# Repository instructions

このリポジトリは、一人参加のISUCONでコードと設定をローカルから管理・デプロイするためのものです。

この`AGENTS.md`をAI向け指示の正本とする。Codexは直接、Claude Codeは`.claude/rules/agents.md`のsymlink経由で同じ内容を読み込む。

## ディレクトリ

| ディレクトリ | 扱うもの |
| --- | --- |
| `webapp/` | 配布されたアプリケーション、静的ファイル、DBスキーマ、初期化処理。ここを正本として各サーバーへデプロイする。 |
| `config/` | nginx、MySQL、systemd、sysctlなどの設定と、node・同期・ベンチ接続の宣言。remote配置先は`sync.json`で明示する。 |
| `infra/` | CloudFormationなど、AWS環境を再現するための構成定義。認証情報や実行ごとに変わる出力は含めない。 |
| `ansible/` | 全nodeの初期access、toolchain、observability前提を冪等に揃えるplaybookと変数。 |
| `scripts/` | `import`、`deploy`、`restart`、`status`、`rollback`など、ローカルから環境を操作する処理。通常操作はMakefileから呼び出す。 |
| `docs/` | `official/`へ一次情報、`phases/`へ進行手順、`agent-history/`へCodex・Claude Codeの会話履歴（自動生成）を保存する。調査やシナリオ分析もここへ残す。 |
| `.claude/` | Claude Codeがこの`AGENTS.md`を起動時に読み込むためのrule symlink。`CLAUDE.md`は作成しない。 |
| `.isuscope/` | node、collector、route正規化、ベンチ実行方法など、isuscopeの計測設定。 |
| `isuscope-data/` | スコア、仮説、分析、Git状態など、isuscopeのrun。生ログは既定で除外し、重要なrunだけpinする。 |
| `.local/` | Public IP、秘密情報、AWSの一時出力など、環境固有の情報。Git管理しない。 |

## 過去の記録の手がかり

| 記録 | 場所 | 内容 |
| --- | --- | --- |
| 会話履歴 | `docs/agent-history/` | Codex・Claude Codeとの会話の縮約版（人間の入力、AIの最終回答、commit）。1ファイルが1セッションで、headerの`- Agent:`と`- Session:`で識別する。共通hook `~/.agent-history/agent_history.py`が自動生成するため、手で編集しない。形式は同ディレクトリの`Readme.md`を参照。 |
| ベンチ記録 | `isuscope-data/` | score、仮説、分析、採否。`isuscope list`、`isuscope brief <run>`で読む。runの`agent_context`から、そのベンチを実行した会話の位置が分かる。 |
| Codexの生ログ | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` | 縮約版にない推論・ツール操作を含む。ローカルのみ。 |
| Claude Codeの生ログ | `~/.claude/projects/<作業ディレクトリのパスの/を-にした名前>/<Session>.jsonl` | 同上。 |

生ログは内部形式が非公開で変わり得るため読むだけにし、秘密情報を含み得るのでリポジトリへコピーしない。

## 作業ルール

- アプリケーションと設定はローカルリポジトリを正とする。
- サーバーごとにコードを複製せず、役割の違いは設定とデプロイ処理で扱う。
- サーバー上のファイルを直接編集し続けない。緊急で変更した場合は、直ちにローカルへ反映してGit差分を残す。
- 秘密情報は`.local/`へ置く。再現に必要な定義は`webapp/`、`config/`、`infra/`、`scripts/`へ残す。
- 公式情報は要約だけで済ませず、可能な限り原文を`docs/official/`へ保存する。
- 現在のPhaseと完了条件は`docs/phases/`に従い、Phaseの移行は人間が決定する。
- isuscopeの軽量なrun履歴は通常のコミットへ含める。重要なrunの生ログを残す場合は`isuscope pin <run-id>`を使う。
- `.local/operation.lock`を変更系操作の共通排他とする。実行中のlockを手作業で消さず、別セッションは終了を待つ。status・checkなどのread-only操作は並行してよい。

## 初動の自動化

大会開始後は、`config/environment.example.env`を`.local/environment.env`へコピーしてprovider、SSH、node分類を設定し、次の順で初動を進める。

```text
make kickoff
  → 回収元1台のSSH確立後、webapp/rustとDDLを先行回収し、その範囲だけ自動commit
  → 別worktreeと引き継ぎ文を作成（ここでコード読解セッションを開始）
  → 全nodeのSSH確立、Ansible導入、初期収束、初期構成の調査
  → node role、同期対象、Ansible変数、log pathの候補を.local/draftへ生成
  → draftを実nodeと照合し、.local/draft/review.mdを出して停止
review.mdのFAILを直し、WARNをすべて判断してからCONFIRM_DRAFT=true make kickoff-apply
  → draftを再検査して反映し、全配布先のdigest一致を確認して完全import
make deploy
  → 全台preflight・staging後に切り替え、失敗時はtransaction全体を復旧
config/benchmark.envを設定して.isuscope/benchmark.sh --check、--probe
make phase1-check
  → 全node、同期、ベンチadapterと接続先、isuscope doctorをベンチなしで検査
isuscope survey-run --hypothesis "..."
  → 人間が確認した後に初回ベンチを一度だけ実行
初回runの分析後に別worktreeをmainへ統合
  → deployし、通常のisuscope runでbaselineと比較
```

採用言語はRustに固定する（`config/application.env`）。先行回収は完全importを待たない暫定回収であり、対象はコードとschemaだけに限定する。完全な初期状態の正本化と全配布先の一致確認は`kickoff-apply`のimportで完了する。`kickoff`は再実行でき、回収済みならimportを飛ばし、worktreeも再利用する。draftの判断は`review.md`を読んだ操作者（人間またはこのセッション）が行い、`make`の中で別のAIを呼んで承認させない。`make`は初動・deploy・検査の入口だけに絞っており、個別の段階をやり直す場合は`scripts/`の該当scriptを直接実行する（変更系scriptは自分で操作lockを取る）。

- 計測と運用の土台（計測tool、LTSV access log、slow log、時刻同期、journal、自動更新停止）は初動のAnsibleで固定化し、レギュレーションで禁止された項目だけ変数で外す。性能を変える設定（`config/nginx/tare`、networking sysctl、MySQLの性能設定）は初回baselineの後に1つの変更として入れる。
- `kickoff`、`bootstrap`、`phase1-check`からベンチを起動しない。`isuscope survey-run`は必ず独立した明示操作にする。
- 1 worktree＝1 branch＝1目的とし、`make worktree BRANCH=<name> PURPOSE="..."`で作る。目的はbranchへ記録され、他のlaneの一覧は引き継ぎ文に出る。
- 並行worktreeは`webapp/`のコード・schemaとローカルテストだけを扱い、remote変更、deploy、ベンチ、`.local/operation.lock`を使う操作はmain側だけが行う。
- 自明な修正は初回baselineを取るまでremoteへ反映しない。baselineの分析後にmainの最新設定を取り込み、変更根拠を照合してから統合する。
- `config/sync.json`のitemとcommandには対象node groupを明示し、役割を持たないnodeへ設定や再起動を配らない。
- `.local/`の接続情報や生成物をGitへ追加しない。固定化すべき構成だけを`ansible/`、`config/`、`scripts/`へ残す。
- deploy、rollback、import、ベンチ接続、isuscope設定の詳しい契約は`docs/initial-automation.md`を正とする。

## isuscopeの使い方

このリポジトリでは、ベンチマークの測定結果と改善履歴をisuscopeで管理する。isuscopeは、ベンチ1回ごとにscore、仮説、Gitの状態、HTTP・SQL・CPUの計測、会話の位置を1つのrunとして記録する自作CLIである。仕様とオプションは`isuscope --help`とリポジトリ（github.com/mnaog/isuscope）のREADMEを正とする。

初回は[初動自動化](docs/initial-automation.md)の手順でisuscopeを設定し、`isuscope doctor`を通してから`isuscope survey-run`で初期状態と行動遷移を一度だけ記録する。

```bash
isuscope survey-run --hypothesis "初期状態の負荷構造を記録する"
isuscope brief latest
isuscope query latest --metric-prefix benchmark. --group-by scenario --limit 100
isuscope analyze RUN_ID supported --analysis "観測結果と判断"
```

通常の改善は、仮説付きのベンチと分析を一単位にする。

```bash
isuscope run --hypothesis "変更理由と改善を期待する観測値"
isuscope brief latest
isuscope query latest --base BASE_RUN --metric-prefix benchmark. --group-by scenario --limit 100
isuscope analyze RUN_ID supported --analysis "観測結果と判断"
```

仮説の対象は`query --base`へ同じselectorを指定して比較する。HTTPは`--view http`と`--label route=...`、DBは`--view database`、必要な`--source`、`--label-contains digest=...`、`--group-by sql-shape`を使い、対象を絞らない巨大JSONを避ける。

判定には`supported`、`rejected`、`inconclusive`、`skipped`を使う。更新対象を誤らないよう、`analyze`には実行結果か`isuscope list`で得たrun IDを明示する。PASSしたrunは分析を記録するまで次のベンチを開始できない。FAILまたは中断したrunには分析は不要。

`survey-run`はPhase 1の初回調査だけに使い、その後は構成やroutingを大きく変えた場合も`run`を使う。時間が最大の制約なので、同じ変更の比較のためにベンチを重ねない。終了前はprofilerや重いログを外した構成へ切り替え、確認のベンチは通常の`run`で一度だけ行う。

最初は`isuscope brief latest`で全体を確認し、`isuscope query latest --base BASE_RUN ...`で仮説対象だけを比較する。初期化を除くhost/service集約は`query --scope series --window load`、時間帯を掘り下げる場合は`isuscope series latest --window load --metric <name>`を使う。`whole`、`initialize`、`load`を意図に応じて選び、初期化負荷と通常負荷を混ぜない。`report`、`diff`、`metrics`はcompactな出力だけでは足りない場合の詳細診断に限定する。人が複数runを横断して確認するときは`isuscope ui`を使う。collectorの失敗はbriefのcoverage issueを入口にし、必要ならreportのcoverageとrun配下のlogで確認する。

初回runのHTTP routeに動的IDが残っている場合は、`isuscope routes suggest <run-id> --output .local/route-suggestions.toml`で`.local/route-suggestions.toml`を作る。候補を確認したものだけ`.isuscope/routes.toml`へ移し、再計測する。

`[context.agent]`を有効にした後のベンチは、会話履歴を正しく紐付けるため現在のCodexまたはClaude Codeのセッションから実行する。
