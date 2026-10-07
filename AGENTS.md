# Repository instructions

このリポジトリは、一人参加のISUCONでコードと設定をローカルから管理・デプロイするためのものです。

この`AGENTS.md`をAI向け指示の正本とする。Codexは直接、Claude Codeは`.claude/rules/agents.md`のsymlink経由で同じ内容を読み込む。

## 練習時のみ
現在は練習中です。全ロール・全AIは作業前に[practice.md](practice.md)を読み、その追加指示に従ってください。本番ではこの見出しと案内の2行、およびpractice.mdを削除してください。

## ディレクトリ

| ディレクトリ | 扱うもの |
| --- | --- |
| `webapp/` | 配布されたアプリケーション、静的ファイル、DBスキーマ、初期化処理。ここを正本として各サーバーへデプロイする。 |
| `config/` | nginx、MySQL、systemd、sysctlなどの設定と、node・同期・ベンチ接続の宣言。remote配置先は`sync.json`で明示する。 |
| `infra/` | CloudFormationなど、AWS環境を再現するための構成定義。認証情報や実行ごとに変わる出力は含めない。 |
| `ansible/` | 全nodeの初期access、toolchain、observability前提を冪等に揃えるplaybookと変数。 |
| `scripts/` | `import`、`deploy`、`restart`、`status`、`rollback`など、ローカルから環境を操作する処理。通常操作はMakefileから呼び出す。 |
| `docs/` | `official/`へ一次情報、`phases/`へ進行手順、`agent-history/`へCodex・Claude Code・OpenCodeの会話・操作履歴（自動生成）を保存する。調査やシナリオ分析もここへ残す。 |
| `.claude/` | Claude Codeがこの`AGENTS.md`を起動時に読み込むためのrule symlink。`CLAUDE.md`は作成しない。 |
| `.isuscope/` | node、collector、route正規化、ベンチ実行方法など、isuscopeの計測設定。 |
| `isuscope-data/` | スコア、仮説、分析、Git状態など、isuscopeのrun。生ログは既定で除外し、重要なrunだけ強制追加する。 |
| `.local/` | Public IP、秘密情報、AWSの一時出力など、環境固有の情報。Git管理しない。 |

## 過去の記録の手がかり

| 記録 | 場所 | 内容 |
| --- | --- | --- |
| 会話履歴 | `docs/agent-history/` | Codex・Claude Code・OpenCodeの会話・commit・操作の時系列をMarkdown、受信した入出力と計測メタデータを同名の`.events.jsonl`へ保存する。1組が1セッションで、headerの`- Agent:`と`- Session:`で識別する。共通処理 `~/.agent-history/agent_history.py`がhook／pluginから自動生成するため、手で編集しない。形式は同ディレクトリの`Readme.md`を参照。 |
| ベンチ記録 | `isuscope-data/` | score、仮説、分析、採否。`isuscope list`、`isuscope brief <run>`で読む。runの`agent_context`から、そのベンチを実行した会話の位置が分かる。 |
| Codexの生ログ | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` | 縮約版にない推論・ツール操作を含む。ローカルのみ。 |
| Claude Codeの生ログ | `~/.claude/projects/<作業ディレクトリのパスの/を-にした名前>/<Session>.jsonl` | 同上。 |

生ログは内部形式が非公開で変わり得るため読むだけにし、秘密情報を含み得るのでリポジトリへコピーしない。

## 作業ルール

- アプリケーションと設定はローカルリポジトリを正とする。
- サーバーごとにコードを複製せず、役割の違いは設定とデプロイ処理で扱う。サーバー上のファイルを直接編集しない。
- 秘密情報は`.local/`へ置く。再現に必要な定義は`webapp/`、`config/`、`infra/`、`scripts/`へ残す。
- 公式情報は要約だけで済ませず、可能な限り原文を`docs/official/`へ保存する。
- 現在のPhaseと完了条件は`docs/phases/`に従い、Phaseの移行は人間が決定する。
- isuscopeの軽量なrun履歴は通常のコミットへ含める。重要なrunの生ログを残す場合は`git add -f isuscope-data/runs/<run-id>`でstageする。
- `.local/operation.lock`を変更系操作の共通排他とする。実行中のlockは基本的に手作業で消さず、別セッションの終了を待つ。status・checkなどのread-only操作は並行してよい。

## 作業の進め方

作業するAIは、調査・実装・検証・deploy・ベンチ・採否判断まで一貫して進める。固定の役割分担や別セッションの起動を必須にしない。問題を理解したセッションで修正と評価を続ける。
並行作業が必要な場合のみworktreeを使う。remote変更・deploy・共有ベンチはmain側で既存の共通lockを使い、実行中の操作と競合させない。作業中の他worktreeを変更・削除しない。
scoutは人間がPhase 2開始を決めてから`make scout-start`で起動し、`make scout-stop`で止める。参照する会話は`./scripts/scout conversation <agent> <実セッションID>`で明示する。更新時刻から推測しない。ボードは`make board`で開く読み取り専用画面。最新報告の`docs/scout-board.md`は通常のコミットに含める。詳細は`docs/roles/scout.md`に従う。

## Phase 0とPhase 1の境界

Phase 0は本番開始前に利用できる情報だけで行える準備をすべて扱う。汎用tool、認証、権限、操作・検証の仕組み、問題に依存しない動作確認はここで済ませる。事前公開された条件はPhase 0で反映する。開始後に初めて分かる配布AMI・台数・構成・コード・schema・依存版の選定と実環境への適用はPhase 1で行う。練習運営側が事前に用意した配布物も、参加者へ渡すのは開始宣言後とする。ファイルをPhase 1と名付けるだけで、開始前の参加者repoへ問題固有情報を入れてはいけない。

## 初動の自動化

大会開始後は、`config/environment.example.env`を`.local/environment.env`へコピーしてprovider、SSH、node分類を設定し、次の順で初動を進める。

```text
make kickoff
  → 回収元1台のSSH確立後、webapp/rustとDDLを先行回収し、その範囲だけ自動commit
  → 並行して1台目のbuild環境を調査。config/phase1-build.jsonを確定すると初期buildを自動開始
  → 並行してローカル実行環境を構成し、make local-up / local-checkで初期改善に使う
  → 全nodeのSSH確立、Ansible導入、初期収束、初期構成の調査
  → node role、同期対象、Ansible変数、log pathの候補を.local/draftへ生成
  → draftを実nodeと照合し、.local/draft/review.mdを出して停止
review.mdのFAILを直し、WARNをすべて判断してからCONFIRM_DRAFT=true make kickoff-apply
  → draftを再検査して反映し、全配布先のdigest一致を確認して完全import
make build（先行回収のcommit後、設定確認済みなら初動と並行可能）
  → ローカルDockerでLinux向けRustをbuildし、ルートrepoのcacheへ保存
make deploy
  → ローカル成果物を再利用・必要時buildし、同じbinaryを全台stagingへ配布
  → 全台preflight・staging検査後に切り替え、失敗時はtransaction全体を復旧
config/benchmark.envを設定して.isuscope/benchmark.sh --check、--probe
make phase1-check
  → 全node、同期、ベンチadapterと接続先、isuscope doctorをベンチなしで検査
isuscope survey-run --hypothesis "..."
  → 人間が確認した後に初回ベンチを一度だけ実行
初回runの分析後に観測に基づいて改善を実装・検証
  → deployし、通常のisuscope runでbaselineと比較
```

採用言語はRustに固定する（`config/application.env`）。先行回収は完全importを待たない暫定回収であり、対象はコードとschemaだけに限定する。完全な初期状態の正本化と全配布先の一致確認は`kickoff-apply`のimportで完了する。`kickoff`は再実行でき、回収済みならimportを飛ばし、初期buildは重複起動しない。draftの判断は`review.md`を読んだ操作者（人間またはこのセッション）が行い、`make`の中で別のAIを呼んで承認させない。`make`は初動・deploy・検査とローカル運用ボード・scout常駐処理の入口に絞っており、個別の段階をやり直す場合は`scripts/`の該当scriptを直接実行する（変更系scriptは自分で操作lockを取る）。

- 計測と運用の土台（計測tool、LTSV access log、slow log、時刻同期、journal、自動更新停止）は初動のAnsibleで固定化し、レギュレーションで禁止された項目だけ変数で外す。性能を変える設定（`config/nginx/tare`、networking sysctl、MySQLの性能設定）は初回baselineの後に1つの変更として入れる。
- `kickoff`、`bootstrap`、`phase1-check`からベンチを起動しない。`isuscope survey-run`は必ず独立した明示操作にする。
- 並行作業が必要なら`make worktree BRANCH=<name> PURPOSE="..."`で作る。目的はbranchへ記録する。変更目的ごとにcommitを分け、検証結果と評価したcommitを残す。
- 並行worktreeでも目的に必要なコード・設定・文書を扱ってよい。remote変更、deploy、ベンチ、`.local/operation.lock`を使う操作はmain側だけが行う。
- Phase 1のローカル実行環境は各worktreeで`make local-up / local-check / local-down`で操作してよい。worktree専用のDB・network・port・lockを使う。初回baselineの開始はローカル環境の完成を待たない。詳細は`docs/local-development.md`を参照する。
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
isuscope query latest --metric-prefix benchmark. --limit 100
isuscope analyze RUN_ID supported --analysis "観測結果と判断"
```

通常の改善は、仮説付きのベンチと分析を一単位にする。

```bash
isuscope run --hypothesis "変更理由と改善を期待する観測値"
isuscope brief latest
isuscope query latest --base BASE_RUN --metric-prefix benchmark. --limit 100
isuscope analyze RUN_ID supported --analysis "観測結果と判断"
```

仮説の対象は`query --base`へ同じselectorを指定して比較する。HTTPは`--view http`と`--label route=...`、DBは`--view database --window load`（initializeを除いた負荷区間）、必要な`--label-contains digest=...`、`--group-by sql-shape`を使い、対象を絞らない巨大JSONを避ける。

判定には`supported`、`rejected`、`inconclusive`、`skipped`を使う。更新対象を誤らないよう、`analyze`には実行結果か`isuscope list`で得たrun IDを明示する。仮説や分析の本文でrunに触れるときは、isuscopeの出力が示す短縮ID（run IDの末尾8文字）にそろえる。PASSしたrunは分析を記録するまで次のベンチを開始できない。FAILまたは中断したrunには分析は不要。

変更を残すか戻すかを決めたら、分析と同時に`--change <変更ID> --decision <accepted|provisional|rejected|deferred>`で記録する（`provisional`は`--revisit`必須）。仮説の判定と変更の採否は別に扱い、本文に「採用」と書くだけで済ませない。

FAILしたrunの理由とエラーの実例は、`.isuscope/parse-benchmark.sh`がベンチの出力から`message`として残し、`isuscope list`の`failure`と`brief`の`benchmark_messages`で読む。判断材料とmessageには、適用されるルールで参加者の利用が認められた出力だけを使う。

`survey-run`はPhase 1の初回調査だけに使い、その後は構成やroutingを大きく変えた場合も`run`を使う。時間が最大の制約なので、同じ変更の比較のためにベンチを重ねない。終了前はprofilerや重いログを外した構成へ切り替え、確認のベンチは通常の`run`で一度だけ行う。

isuscopeの取得データについてより自由度の高い分析や比較には、`isuscope sql`（table定義は`--schema`）で確認する。接続は読み取り専用で、データの場所は設定から解決する。

初回runのHTTP routeに動的IDが残っている場合は、`isuscope routes suggest <run-id> --output .local/route-suggestions.toml`で`.local/route-suggestions.toml`を作る。候補を確認したものだけ`.isuscope/routes.toml`へ移し、再計測する。

`[context.agent]`を有効にした後のベンチは、会話履歴を正しく紐付けるため現在のCodexまたはClaude Codeのセッションから実行する。
