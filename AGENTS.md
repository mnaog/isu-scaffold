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

## 過去の記録の手がかり

| 記録 | 場所 | 内容 |
| --- | --- | --- |
| 会話履歴 | `docs/agent-history/` | Codex・Claude Code・OpenCodeの会話・commit・操作の時系列。1セッションが`.md`と同名の`.events.jsonl`の1組で、headerの`- Agent:`と`- Session:`で識別する。自動生成のため手で編集しない。形式は同ディレクトリの`Readme.md`を参照。 |
| ベンチ記録 | `isuscope-data/` | score、仮説、分析、採否。`isuscope list`、`isuscope brief <run>`で読む。runの`agent_context`から、そのベンチを実行した会話の位置が分かる。 |
| Codexの生ログ | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` | 縮約版にない推論・ツール操作を含む。ローカルのみ。 |
| Claude Codeの生ログ | `~/.claude/projects/<作業ディレクトリのパスの/を-にした名前>/<Session>.jsonl` | 同上。 |

生ログは内部形式が非公開で変わり得るため読むだけにする。

## 禁止事項

- サーバー上のファイルを直接編集する。変更はローカルの正本からdeployする。
- `.local/`の接続情報・秘密情報・生成物や、生ログをGitへ追加・コピーする。
- 実行中の`.local/operation.lock`を手作業で消す。基本的に別セッションの終了を待つ。
- worktreeからremote変更・deploy・ベンチ・`.local/operation.lock`を使う操作を行う。作業中の他worktreeを変更・削除する。
- `kickoff`、`bootstrap`、`phase1-check`からベンチを起動する。`make`の中で別のAIを呼んでdraftを承認させる。
- Phaseの移行、scoutの開始、初回`survey-run`の実行をAIだけで決める。
- 初回baselineを取る前に、自明な修正や性能を変える設定をremoteへ反映する。
- 開始前の参加者repoへ問題固有情報を入れる（「Phase 0とPhase 1の境界」）。
- 役割を持たないnodeへ設定や再起動を配る。
- 適用されるルールで参加者の利用が認められていない出力を、判断材料やベンチ出力のparserが残す`message`に使う。
- PASSしたrunの分析を記録せずに次のベンチを始める。同じ変更の比較のためにベンチを重ねる。

## 作業ルール

- アプリケーションと設定はローカルリポジトリを正とする。サーバーごとにコードを複製せず、役割の違いは設定とデプロイ処理で扱う。
- 秘密情報と環境固有の生成物は`.local/`へ置く。再現・固定化に必要な定義は`webapp/`、`config/`、`infra/`、`ansible/`、`scripts/`へ残す。
- 公式情報は要約だけで済ませず、可能な限り原文を`docs/official/`へ保存する。
- 現在のPhaseと完了条件は`docs/phases/`に従う。
- `.local/operation.lock`を変更系操作の共通排他とする。変更系scriptは自分でlockを取る。status・checkなどのread-only操作は並行してよい。
- `config/sync.json`のitemとcommandには対象node groupを明示する。
- isuscopeの軽量なrun履歴は通常のコミットへ含める。重要なrunの生ログを残す場合は`git add -f isuscope-data/runs/<run-id>`でstageする。
- 変更目的ごとにcommitを分け、検証結果と評価したcommitを残す。

## 作業の進め方

作業するAIは、調査・実装・検証・deploy・ベンチ・採否判断まで一貫して進める。固定の役割分担や別セッションの起動を必須にしない。問題を理解したセッションで修正と評価を続ける。

並行作業が必要な場合のみ`make worktree BRANCH=<name> PURPOSE="..."`でworktreeを作り、目的をbranchへ記録する。worktreeでも目的に必要なコード・設定・文書を扱ってよく、`make local-up / local-check / local-down`はworktree専用のDB・network・port・lockで動く（[ローカル実行環境](docs/local-development.md)）。remote変更・deploy・共有ベンチはmain側で行う。

scoutは人間がPhase 2開始を決めてから`make scout-start`で起動し、`make scout-stop`で止める。参照する会話は`./scripts/scout conversation <agent> <実セッションID>`で明示する。更新時刻から推測しない。ボードは`make board`で開く読み取り専用画面。最新報告の`docs/scout-board.md`は通常のコミットに含める。詳細は`docs/roles/scout.md`に従う。

## Phase 0とPhase 1の境界

Phase 0は本番開始前に利用できる情報だけで行える準備をすべて扱う。汎用tool、認証、権限、操作・検証の仕組み、問題に依存しない動作確認はここで済ませる。事前公開された条件はPhase 0で反映する。開始後に初めて分かる配布AMI・台数・構成・コード・schema・依存版の選定と実環境への適用はPhase 1で行う。練習運営側が事前に用意した配布物も、参加者へ渡すのは開始宣言後とする。ファイルをPhase 1と名付けるだけで、開始前の参加者repoへ問題固有情報を入れてはいけない。

## 初動

大会開始後の各段階の手順と契約（discover、draft検査、import、build、deploy、rollback、ベンチ接続、isuscope設定）は[docs/initial-automation.md](docs/initial-automation.md)を正とし、進行と完了条件は[Phase 1](docs/phases/Phase1.md)に従う。`config/environment.example.env`を`.local/environment.env`へコピーしてprovider、SSH、node分類を設定し、次の入口を順に使う。

```text
make kickoff                            # コード・schemaの先行回収とcommit、初期build、全台準備、draft生成と検査。.local/draft/review.mdを出して停止
CONFIRM_DRAFT=true make kickoff-apply   # review.mdのFAILを直しWARNをすべて判断した後に、反映と完全import
make build                              # 先行回収のcommit後、設定確認済みなら初動と並行してよい
make deploy                             # 同じbinaryを全台へ配布し、失敗時はtransaction全体を復旧
make phase1-check                       # 全node、同期、ベンチadapter、isuscope doctorをベンチなしで検査
isuscope survey-run --hypothesis "..."  # 人間が確認した後に一度だけ
```

- 採用言語はRustに固定する（`config/application.env`）。
- 先行回収はコードとschemaだけの暫定snapshotで、待ち時間にコードを読むためのもの。初期状態の正本化と全配布先の一致確認は`kickoff-apply`の完全importで完了する。`kickoff`は再実行でき、回収済みならimportを飛ばし、初期buildは重複起動しない。
- draftの判断は`review.md`を読んだ操作者（人間またはこのセッション）が行う。
- 計測と運用の土台（計測tool、LTSV access log、slow log、時刻同期、journal、自動更新停止）は初動のAnsibleで固定化し、レギュレーションで禁止された項目だけ変数で外す。性能を変える設定（`config/nginx/tare`、networking sysctl、MySQLの性能設定）は初回baselineの後に1つの変更として入れる。
- 自明な修正はbaselineの分析後にmainの最新設定を取り込み、変更根拠を照合してから統合する。
- ローカル実行環境は初動と並行して構成し、初回baselineの開始を待たせない。
- `make`は初動・deploy・検査とボード・scoutの入口に絞っている。個別の段階をやり直す場合は`scripts/`の該当scriptを直接実行する。

## isuscopeの使い方

ベンチマークの測定結果と改善履歴はisuscopeで管理する。isuscopeは、ベンチ1回ごとにscore、仮説、Gitの状態、HTTP・SQL・CPUの計測、会話の位置を1つのrunとして記録する自作CLIである。仕様は`isuscope --help`とリポジトリ（github.com/mnaog/isuscope）のREADMEを正とする。コマンド例、比較の絞り込み、失敗・遅延の原因の調べ方は[docs/isuscope.md](docs/isuscope.md)にあり、ベンチ結果を読む前に読む。

- `isuscope doctor`を通してから、Phase 1の初回だけ`survey-run`で初期状態と行動遷移を記録する。その後は構成やroutingを大きく変えた場合も`run`を使う。
- 通常の改善は、仮説付きの`isuscope run`と`isuscope analyze`を一単位にする。PASSしたrunは分析を記録するまで次のベンチを開始できない。FAILまたは中断したrunには分析は不要。
- 判定には`supported`、`rejected`、`inconclusive`、`skipped`を使う。`analyze`には実行結果か`isuscope list`で得たrun IDを明示する。本文でrunに触れるときは短縮ID（run IDの末尾8文字）にそろえる。
- 変更を残すか戻すかを決めたら、分析と同時に`--change <変更ID> --decision <accepted|provisional|rejected|deferred>`で記録する（`provisional`は`--revisit`必須）。仮説の判定と変更の採否は別に扱い、本文に「採用」と書くだけで済ませない。
- 時間が最大の制約である。終了前はprofilerや重いログを外した構成へ切り替え、確認のベンチは通常の`run`で一度だけ行う。
- `[context.agent]`を有効にした後のベンチは、会話履歴を正しく紐付けるため現在のCodexまたはClaude Codeのセッションから実行する。
