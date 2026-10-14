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
| `docs/` | `official/`へ一次情報、`preparation.md`（準備）・`initial-work.md`（初動）・`improvement.md`（改善）・`finish.md`（終了前）へ進め方、`agent-history/`へCodex・Claude Code・OpenCodeの会話・操作履歴（自動生成）を保存する。調査やシナリオ分析もここへ残す。 |
| `.claude/` | Claude Codeがこの`AGENTS.md`を起動時に読み込むためのrule symlinkと、禁止操作を止めるhook（`tool-guard.py`）と、foregroundのベンチを止めてベンチ中にscout報告を渡すhook（`bench-hook.py`）の設定（`settings.json`）。`CLAUDE.md`は作成しない。 |
| `.codex/` | Codexが同じhookを読む`hooks.json`。初めて開いたときにCodexでhookを信頼する。 |
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

## 必ず残す記録

ベンチの見取り図（`docs/benchmark-scenario.md`）、コードの地図（`docs/code-map.md`）など、必ず残す記録は置き場所が固定で、一覧は`config/records.json`にある。各手順書の「残すもの」が指す場所へ書き、別の場所や別名のファイルに分けない。人間は`make board`の「記録」で読む。

## 作業ルール

- アプリケーションと設定はローカルリポジトリを正とする。
- サーバーごとにコードを複製せず、役割の違いは設定とデプロイ処理で扱う。サーバー上のファイルを直接編集しない。
- 秘密情報は`.local/`へ置く。再現に必要な定義は`webapp/`、`config/`、`infra/`、`scripts/`へ残す。
- 公式情報は要約だけで済ませず、可能な限り原文を`docs/official/`へ保存する。
- 本番開始前の準備は`docs/guides/preparation.md`に従う。開始後は区切りを設けず、初動は`docs/guides/initial-work.md`、改善は`docs/guides/improvement.md`、終了前の固定は`docs/guides/finish.md`を状況に応じて参照する。
- isuscopeの軽量なrun履歴は通常のコミットへ含める。重要なrunの生ログを残す場合は`git add -f isuscope-data/runs/<run-id>`でstageする。
- ベンチnodeへの直接の接続、ベンチマーカーの閲覧・コピー、現在練習中の開催回のコードの取得と攻略法の検索は、Claude CodeとCodexのPreToolUse hook（`scripts/tool-guard.py`）が止める。対象の開催回とベンチマーカーの置き場所は`config/tool-guard.json`で宣言する。`scripts/ssh-node.sh`も、ベンチadapterと初動の検査が付ける`ISUCON_ALLOW_BENCHMARK_NODE=1`がない限りベンチnodeへ接続しない。止められたら別経路で回避せず、ベンチは`isuscope run`・`survey-run`から動かす。
- `.local/operation.lock`を変更系操作の共通排他とする。実行中のlockは基本的に手作業で消さず、別セッションの終了を待つ。status・checkなどのread-only操作は並行してよい。

## 作業の進め方

作業するAI（mainのセッション）は、調査・実装・検証・deploy・ベンチ・採否判断まで一貫して進める。問題を理解したセッションで修正と評価を続ける。
並行させるAIは、すべてClaude Codeのheadless実行（`claude -p`）のlaneにする。laneは`scripts/lane.py`が自分のworktreeとbranch（`lane/<名前>`）で1回だけ動かし、そのbranchへcommitして終わる。mainのセッションは結果（`python3 scripts/lane.py status <名前>`）を読み、根拠を照合してから`git merge`で取り込む。laneは出力が決まった有限のタスクだけに使い、常駐させない。モデル・effort・使えるtool・時間上限・promptは`config/lanes.json`と`config/lanes/`が正で、laneは許可したtool以外を確認なしで拒否される。laneのremote node・deploy・ベンチ・isuscopeの記録・`.local/operation.lock`・push・laneの起動は`scripts/tool-guard.py`が止める。
remote変更・deploy・共有ベンチはmain側で既存の共通lockを使い、実行中の操作と競合させない。作業中の他worktreeを変更・削除しない。
人間は`make board`で開く読み取り専用の画面（ボード）で、スコアの推移、runの比較と負荷、必ず残す記録を見る。詳細は`docs/board.md`。
scout（Claude Code・Codex・Kimi Code）は`make first-bench`が初回baselineの後に起動し（`FIRST_BENCH_SCOUT=0`なら起動しない。手では`make scout-start`）、`make scout-stop`で止める。各scoutは前回の報告を保存してから8分後に新しいセッションで探索し、最新の報告を`.local/scout/reports.md`へ書く（Gitには入れない。`make scout-stop`が`docs/scout-reports.md`へ残すので、それをcommitする）。参照する会話は、scoutを起動したセッション（`make first-bench`・`make scout-start`を実行したmainのセッション）が自動で登録される。別の会話にするときは`python3 scripts/scout/main.py conversation <agent> <実セッションID>`で明示し、更新時刻から推測しない。報告はmainのセッションがベンチの待ち時間に読む。詳細は`docs/roles/scout.md`。

## sfs

sfs（spawn-forked-session）は、現在の会話履歴と作業状態を引き継ぐ子セッションを、独立したGit worktree上に開くコマンドです。ユーザーが「sfs」と指示したら、対象リポジトリのルートで `/Users/nao/project/spawn-forked-session/spawn-forked-session` を引数なしで実行してください。子には作業指示を送らず、入力待ちにしてください。

## 準備と開始後の境界

準備は本番開始前に利用できる情報だけで行える作業をすべて扱う。汎用tool、認証、権限、操作・検証の仕組み、問題に依存しない動作確認はここで済ませる。事前公開された条件は準備で反映する。開始後に初めて分かる配布AMI・台数・構成・コード・schema・依存版の選定と実環境への適用は開始後に行う。練習運営側が事前に用意した配布物も、参加者へ渡すのは開始宣言後とする。ファイルを開始後用と名付けるだけで、開始前の参加者repoへ問題固有情報を入れてはいけない。

## 初動の自動化

mainのAIセッション（Codex・Claude Code）は、このrepoの中で起動する。isuscopeは各ベンチをこのrepoの`docs/agent-history/`にある会話へ結び付けるので、別のdirectory（scaffoldなど）で起動したセッションからはベンチを動かせない（`make kickoff`が最初に`scripts/check-agent-session.py`で止める）。

大会開始後は、`config/environment.example.env`を`.local/environment.env`へコピーしてprovider、SSH、node分類を設定し、次の順で初動を進める。

```text
make kickoff（バックグラウンドで実行し、.local/progress.logを追う）
  → 回収元1台のSSH確立後、webapp/rustとDDLを先行回収し、その範囲だけ自動commit（code-ready）
  → 並行して1台目のbuild環境を調査。build設定を自動生成できれば即build、できなければ設定待ち
  → 並行してlane code-map（docs/code-map.md）、indexes（索引を1件1commit）、obvious-fixes（索引以外の自明な修正を1件1commit）、
    local-env（config/local/compose.yamlとdocs/local-verification.md）を起動
  → 全nodeのSSH確立、Ansible導入、初期収束、初期構成の調査。調査結果からdocs/environment.mdを自動で書く
  → node role、同期対象、Ansible変数、log pathの候補を.local/draftへ生成
  → draftを実nodeと照合し、.local/draft/review.mdを出して停止（draft-ready）
kickoffの実行中にmainのセッションが並行して進める
  → 公式資料をdocs/official/へ保存してcommitし、すぐlane scenario（docs/benchmark-scenario.md）を起動
  → config/benchmark.envとbenchmark-contractを用意し、.isuscope/benchmark.sh --check、--probe（discoverの後なら実行できる）
  → build設定待ちならconfig/initial-build.jsonを作る。自動生成されたら確認してcommit
  → initializeの経路を.isuscope/routes.tomlの[initialize]へ書いてcommit（scripts/benchmark-markers.py suggest-route → set-route）。
    isuscopeがaccess logからinitializeと負荷走行を分けるので、初回runから要約にinitializeが混ざらない
  → laneが終わったものから結果を読んでmergeする（code-map、local-env、scenarioは文書と構成だけ）
review.mdのFAILを直し、WARNをすべて判断してからCONFIRM_DRAFT=true make kickoff-apply
  → draftを再検査して反映し、全配布先のdigest一致を確認して完全import
完全importを確認して初期状態をcommit
make first-bench（人間の確認を待たずにすぐ実行する。失敗した段階で止まる）
  → make bench-check: 全node、同期、ベンチadapterと接続先、isuscope doctorをベンチなしで検査
  → HEADが完全importの内容のままで、アプリのunitが全nodeで動いていれば、配布されたbuildのままsurvey-runで初回ベンチ
    （ローカルbuildとdeployを待たない。baseline-readyで分析を始める）。scoutを起動し、その後に同じcommitをmake deploy
  → そうでなければ、make deploy（初期buildの完了を待つ）の後にsurvey-run、続けてscoutを起動
  → 出力の形を確かめる前ならresultはPENDING。python3 scripts/benchmark-sample.py <run>で読み取りを確かめ、
    --apply --score N --pass true|false（自分で読んだ値）でsample・contract・--check・rescoreまで済ませる
初回runの分析後に観測に基づいて改善を実装・検証
  → lane indexes・obvious-fixesのcommitをbaselineの観測と照合して選んで取り込む（laneの途中でもcommit単位で取り込める）
  → deployし、通常のisuscope runでbaselineと比較
```

時間の大半は「kickoffが終わるのを待つ間」と「deployから初回ベンチまで」に出る。mainのセッションは`make kickoff`の終了を待たず、`.local/progress.log`の行（`code-ready`、`initial-build configured / needs_configuration / complete / failed`、`lane:<名前> complete / failed`、`draft-ready / draft-failed`、段階の`failed`）を見て次の操作へ移る。Claude Codeならkickoffを`run_in_background`で起動し、Monitorで`tail -n +1 -f .local/progress.log`を追う。Codexでは別のshellでkickoffを動かし、同じfileを作業の区切りごとに読む。ベンチ接続の準備はkickoffの結果を使わないので、kickoffの開始と同時に始める。`make first-bench`は段階の間でAIの手番を待たない。本番当日はRustの初期build（キャッシュなしで4〜5分）が最も長い待ちなので、最初のdeploy前はサーバーが配布状態のまま動いていることを使い、baselineをbuildとdeployの前に取る。`bench-check`はdeployに依存しないので、deployの前に流す。段階を個別にやり直すときは`make bench-check`、`make deploy`、`isuscope survey-run`を直接使う。deployで設定（nginx、MySQL、unit）を変えたときだけ`bench-check`を再実行する。

採用言語はRustに固定する（`config/application.env`）。先行回収は完全importを待たない暫定回収であり、対象はコードとschemaだけに限定する。完全な初期状態の正本化と全配布先の一致確認は`kickoff-apply`のimportで完了する。`kickoff`は再実行でき、回収済みならimportを飛ばし、初期buildと完了・実行中のlaneは重複起動しない。draftの判断は`review.md`を読んだ操作者（人間またはこのセッション）が行い、`make`の中で別のAIを呼んで承認させない。`make`は初動・deploy・検査とボード・scout常駐処理の入口に絞っており、個別の段階をやり直す場合は`scripts/`の該当scriptを直接実行する（変更系scriptは自分で操作lockを取る）。

- 計測と運用の土台（計測tool、LTSV access log、時刻同期、journal、自動更新停止）は初動のAnsibleで固定化し（SQLはisuscopeがperformance_schemaから取るので、slow logは既定で無効）、レギュレーションで禁止された項目だけ変数で外す。性能を変える設定（`config/nginx/tare`、networking sysctl、MySQLの性能設定）は初回baselineの後に1つの変更として入れる。接続・fdの上限（nginxのworker_connections・backlog・keepalive、アプリのLimitNOFILE、sysctl、MySQLのmax_connectionsとbuffer pool）は`make tare`が1つのcommitにするので、baselineの後に早めにdeployして`isuscope run`で測る。
- ベンチ（`isuscope run`・`survey-run`・`make first-bench`）は、Claude CodeではBashの`run_in_background`で起動する。foregroundの起動はPreToolUseのhook（`scripts/bench-hook.py`）が止める。起動すると同じhookがPostToolUseで並列で動く調査エージェント（scout）の最新報告を投稿からの経過分つきで渡すので、完了の通知を待つ間に確認し、古くなっている可能性を踏まえてそれぞれの視点を次回の改善の参考にする。同じ待ち時間に次の候補を`make build`しておく。sleepやpollingで完了を待たない。Codexはbackgroundで起動できないので、ベンチを起動する直前に`.local/scout/reports.md`を読む。
- ベンチの前には毎回、isuscopeの`prepare_command`（`scripts/benchmark-prepare.sh`）が、前回のベンチを処理したprocessがまだ動いているアプリのunitを再起動し、listenを待つ。前のベンチが途中で切ったrequestのtransactionが、次のinitializeを止めないようにするため（deployで起動し直したprocessは再起動しない）。前回のベンチと同じ配布物・計測設定なら、isuscopeがベンチを始めない（`identity_command`とfingerprintで比べる）ので、意図した再測定だけ`isuscope run --repeat "理由"`を付ける。その前に、最後にdeployしたcommitとHEADを、deployが配るファイルに限って照合し、ずれていればベンチを始めない（`scripts/check-deployed-commit.sh`）。止まったら、commitして`make deploy`するか、HEADを戻す。
- `kickoff`、`bootstrap`、`bench-check`、laneからベンチを起動しない。初回ベンチは`make first-bench`（または`isuscope survey-run`）を独立した明示操作としてmainのセッションから実行し、runへその会話を紐付ける。import のcommitが済んだら、作業中のAIが人間の確認を待たずに実行する。
- kickoffが起動するlaneは`config/lanes.json`の`kickoff`（既定は`code-map`、`indexes`、`obvious-fixes`、`local-env`）。`scenario`は公式資料が要るので、mainが`docs/official/`をcommitした直後に`python3 scripts/lane.py start scenario`で起動する。`KICKOFF_LANES=`で止め、`KICKOFF_LANES="code-map"`のように選べる。baseline後に並行させたい調査や実装は、promptを書いて`python3 scripts/lane.py start <名前> --prompt-file <path> --purpose "..."`で起動する。進行は`make lanes`で見る。
- laneの`code-map`・`scenario`は文書だけ、`local-env`はローカル環境の構成と文書だけなので、結果を読んで終わり次第mainへmergeしてよい。`docs/environment.md`はkickoffが調査結果から自動で書くので、確かめてcommitする（手で補足するときは1行目の生成markerを消す。baseline後に`python3 scripts/environment-report.py`で計時を延ばせる）。`indexes`と`obvious-fixes`は1件1commitで積み、commitのたびに`.local/progress.log`へ`lane:<名前> commit <hash>`が出る。初回baselineの分析の後、観測と照合して効くcommitから`git cherry-pick`で取り込み（laneの完了は待たない）、deployして`isuscope run`で測る。laneの最後の回答にある「実装しなかった候補」も次の変更を選ぶ材料にする。
- 並行作業を手で行うなら`make worktree BRANCH=<name> PURPOSE="..."`で作る。目的はbranchへ記録する。変更目的ごとにcommitを分け、検証結果と評価したcommitを残す。
- 並行worktreeでも目的に必要なコード・設定・文書を扱ってよい。remote変更、deploy、ベンチ、`.local/operation.lock`を使う操作はmain側だけが行う。
- ローカル実行環境は各worktreeで`make local-up / local-check / local-down`で操作してよい。worktree専用のDB・network・port・lockを使う。初回baselineの開始はローカル環境の完成を待たない。詳細は`docs/local-development.md`を参照する。
- 自明な修正は初回baselineを取るまでremoteへ反映しない。baselineの分析後にmainの最新設定を取り込み、変更根拠を照合してから統合する。
- `config/sync.json`のitemとcommandには対象node groupを明示し、役割を持たないnodeへ設定や再起動を配らない。再起動・reloadのcommandには`"when"`で対象のitem名（pattern）を書き、そのitemが変わったdeployでだけ実行させる。deployは前回のdeployから変わったitemだけを送る（全部送るなら`DEPLOY_ALL=1`）。
- ベンチの間（`isuscope run`が操作lockを持つ間）に、次の候補のcommitを`make build`でbuildしておく。`make build`は操作lockを取らないので、ベンチ後の`make deploy`はbuild済みの成果物を使い、lockの中は配布・切替・検証だけになる。
- `.local/`の接続情報や生成物をGitへ追加しない。固定化すべき構成だけを`ansible/`、`config/`、`scripts/`へ残す。
- deploy、rollback、import、ベンチ接続、isuscope設定の詳しい契約は`docs/reference/automation.md`を正とする。

## isuscopeの使い方

このリポジトリでは、ベンチマークの測定結果と改善履歴をisuscopeで管理する。isuscopeは、ベンチ1回ごとにscore、仮説、Gitの状態、HTTP・SQL・CPUの計測、会話の位置を1つのrunとして記録する自作CLIである。仕様とオプションは`isuscope --help`とリポジトリ（github.com/mnaog/isuscope）のREADMEを正とする。

初回は[自動化の仕様](docs/reference/automation.md)の手順でisuscopeを設定し、`isuscope doctor`を通してから`isuscope survey-run`で初期状態と行動遷移を一度だけ記録する。

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

初期化の失敗やエラー・遅延の原因は、SSHで手調べする前に`brief`の次の欄を見る。`logs`はベンチの間にアプリ・nginxのerror log・kernelが出したエラーを型ごとに数えたもの、`database_io`はMySQLがfileの読み書きで待った時間、`database_memory`はbuffer poolとtableの大きさで、どれもinitializeを含む。DBの比較では`query --base`の`total_ms_delta_by_calls`（回数の増減による分）と`total_ms_delta_by_avg`（1回あたりの時間による分）で、合計時間の変化の理由を分ける。

FAILしたrunの理由とエラーの実例は、`.isuscope/parse-benchmark.sh`がベンチの出力から`message`として残し、`isuscope list`の`failure`と`brief`の`benchmark_messages`で読む。判断材料とmessageには、適用されるルールで参加者の利用が認められた出力だけを使う。

`survey-run`は開始後の初回調査だけに使い、その後は構成やroutingを大きく変えた場合も`run`を使う。時間が最大の制約なので、同じ変更の比較のためにベンチを重ねない。終了前はprofilerや重いログを外した構成へ切り替え、確認のベンチは通常の`run`で一度だけ行う。

isuscopeの取得データについてより自由度の高い分析や比較には、`isuscope sql`（table定義は`--schema`）で確認する。接続は読み取り専用で、データの場所は設定から解決する。

初回runのHTTP routeに動的IDが残っている場合は、`isuscope routes suggest <run-id> --output .local/route-suggestions.toml`で`.local/route-suggestions.toml`を作る。候補を確認したものだけ`.isuscope/routes.toml`へ移し、再計測する。

`[context.agent]`を有効にした後のベンチは、会話履歴を正しく紐付けるため現在のCodexまたはClaude Codeのセッションから実行する。
