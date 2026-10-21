# Repository instructions
このリポジトリは、一人参加のISUCONでコードと設定をローカルから管理・デプロイするためのものです。作業はClaude Codeで行う。
この`AGENTS.md`を指示の正本とし、Claude Codeは`.claude/rules/agents.md`のsymlink経由で読み込む。`CLAUDE.md`は作成しない。

## 練習時のみ
現在は練習中です。全ロール・全AIは作業前に[practice.md](practice.md)を読み、その追加指示に従ってください。本番ではこの見出しと案内の2行、およびpractice.mdを削除します。

## 目的と制約
- 競技時間内に、最終的なスコアを最大にする。時間が最大の制約である。
- 言語はRustを採用する。
- 公式のレギュレーション・当日マニュアル・整合性条件・失格条件を常に守る。当日マニュアルは事前の資料より優先する。
- 改善はmainとimproveの2つのセッションで進める。mainのセッション（このrepoで起動したClaude Code）は、merge、次に測るものの判断、deploy、ベンチ、分析、採否を受け持ち、ベンチを止めずに回す。improveのセッション（worktree `improve`で`/improve`）は、mainの分析が示す最大の制約を調べて改善を実装・ローカル検証し、投稿でmainへ渡し続ける。どちらも人間の確認を待たない。mainは改善を実装しない（improveが動いていない間も同じで、人間に起動を頼む）。測るものが無い間、mainは最新の採用構成を測り直して振れ幅を調べておく。
- mainは、このrepoのrootで起動したClaude Codeが最初に`/main`で宣言する。isuscopeは各ベンチをこのrepoの`docs/agent-history/`にある会話へ結び付けるので、別のdirectoryで起動したセッションでは`make kickoff`が止まる。目標は人間が`/main`の後に`/goal`で渡す。
- 宣言は`.local/main-session.json`に残る。宣言がある間は、deploy・rollback・import・kickoff、ベンチ、isuscopeの記録、laneとscoutの起動・停止、`.local/operation.lock`を、ほかのセッションから`scripts/tool-guard.py`が止める。別のセッションで`/main`を打ち直すとmainが移る。

## 判断の原則
- 次の変更は、直近runの計測で見えた最大の制約から選ぶ。負荷の位置は変更のたびに動くので、毎回最新のrunから決める。
- 得点へ直接つながる処理を優先する。速くしたことで、点にならない処理の実行回数だけが増えていないか、シナリオごとの成功数と得点で確かめる。台数があるというだけで分割しない。
- メモリ化やキャッシュは、データの正本、更新時の反映、無効化、initialize後と再起動後の再構築を`docs/cache-design.md`へ書いてから入れる。
- これまでの履歴、差分から、最大の制約をよく調査して考える。予想と反する事象や、説明のつかない動きは、急所である可能性が高いためよく調査する。
- ロジックを変える案（順位付け、待ちの条件、上限など）は、実装の前に状態ログ（`brief`の`states`欄、`isuscope states`）で前提を確かめ、確かめた数字を仮説に書く。状態についての疑問のために、計測を足すrunを回さない。
- 採否はスコアで決める。スコアが下がった変更を処理指標や状態ログ上の改善を理由に残すことは許容するが、原則的には不採用とする。同じ構成でもスコアは振れる。振れ幅はmainが空き時間に最新の採用構成で測っておき、投稿のrunは1回で、その幅（`python3 scripts/spread.py`）と比べて決める。幅の内側の差は点の差として扱わず、狙った観測値と害の有無で決める。本質をよく考える。

## 流れ
| 段階 | すること | 手順書 |
| --- | --- | --- |
| 準備（開始前） | 問題に依存しない道具・認証・検証を済ませる。chrome-debugでのポータルのログインと、worktree `portal`・`improve`・`human`も作っておく | [準備](docs/guides/preparation.md) |
| kickoff | 開始と同時に、人間がroot（`/main`）・worktree `portal`（`/portal`）・worktree `improve`（`/improve`）でセッションを起動する。portalのセッションがサーバーの情報とベンチ接続を作る間に、`make kickoff`をbackgroundで始めて`.local/progress.log`を追い、サーバーの起動、portalのmergeとlane `scenario`の起動、draftの判断を進める。完全importをcommitしたら、人間の確認を待たずに`make first-bench`で初回baselineを取る。laneのcommitを観測と照合して取り込む。lane `full-memory`も先行回収と同時に全メモリ化を始め、`state-log`・`local-env`が終わったら自分で取り込んで確かめる。その間もimproveは全メモリ化が書き換えない所（設定・node分割など）の改善を止めない。laneが取り込んだ後にmainへ入った変更で全メモリ化の形へ移す必要があるものは、mainが戻してimproveへ作り直しを頼む | [kickoff](docs/guides/kickoff.md) |
| 改善 | 観測された最大の制約から順に、継続的に構成を含めて変える。improveが作って投稿し、mainが測って分析し、採否を返す | このファイルの「判断の原則」、`/main`・`/improve`（`.claude/commands/`） |
| 終了前 | 開始時に決めた時刻になったら新しい変更を止め、最終構成を固定し、確認のベンチを3回行う。追試で落ちる原因は`make final-check`・`make final-db-late`・`make final-reboot`で確かめる。時刻が決まっていなければ開始時に人間に確認する | [終了前](docs/guides/finish.md) |

コマンドと各段階の契約（kickoff、deploy、rollback、import、ベンチ接続、isuscopeの設定）は[自動化の仕様](docs/reference/automation.md)を正とする。isuscopeの読み方は[isuscopeの使い方](docs/reference/isuscope.md)にある。

## ベンチの回し方

ベンチ1回と分析1回を1単位にする。

```bash
isuscope run --hypothesis "変更理由と改善を期待する観測値"
isuscope brief latest
isuscope query latest --base BASE_RUN --metric-prefix benchmark. --limit 100
isuscope analyze RUN_ID supported --analysis "観測結果と判断" --change CHANGE_ID --decision accepted
```

- ベンチ（`isuscope run`・`survey-run`・`make first-bench`）は、Bashの`run_in_background`で起動する。foregroundの起動はhookが止める。`kickoff`・`bootstrap`・`bench-check`・laneからは起動しない。
- PASSしたrunは、分析を記録するまで次のベンチを始められない。仮説の判定（`supported`・`rejected`・`inconclusive`・`skipped`）と変更の採否（`accepted`・`provisional`・`rejected`・`deferred`）は別に記録する。
- 何をどの順で測るか、どう組み合わせるかはmainが決める。まとめて測るときは、仮説に変更ごとの期待を並べ、`analyze`を変更ごとに`--change`で同じrunへ記録する。整合性や永続化の時機を変える変更、同じ制約を狙う変更を一緒にすると、落ちたときに原因を切り分けにくい。
- 分析を記録したら、`python3 scripts/progress.py emit main analyzed "<短縮ID> score <n>。次の最大の制約: ..."`でimproveへ知らせる。
- 待つ間に、hookが渡す見取り図（`docs/benchmark-scenario.md`）、scoutの報告、worktreeからの投稿を読み、今回の仮説と次の候補が得点の仕組み・負荷の流れ・失敗の条件と合っているか確かめる。ベンチの仕様について観測と食い違う記述があれば、該当箇所を直す（runの結果や改善の効果は書かない）。次に測るものを選んだら、投稿なら`git merge --no-ff <branch>`し、`make build`しておく。sleepやpollingで待たない。
- Monitorで`.local/progress.log`を追う。`benchmark slow`が出たら、アプリ・nginxのerror logとnodeの負荷を確かめ、止まっていれば`scripts/benchmark-stop.sh`で止める。
- nodeの異常（deploy後にアプリが立ち上がらない、nodeが応答しない、ベンチが止まる）は、手打ちのsshより先に`make status`で全nodeを見る。全nodeを並列に20秒で打ち切り、応答しないnode、メモリ・load・失敗したunit・待ち受けport・直近のOOMとerror・MySQLの実行中のSQLを1回で返す。手でsshするときも`scripts/ssh-node.sh <node> '<command>'`を使い、待つloopには回数の上限を付ける。
- 同じ構成はisuscopeが測り直さない。測り直す（`--repeat "理由"`）のは、mainが空き時間に最新の採用構成の振れ幅を調べるときと、終了前の確認だけ。投稿やlaneの変更は1回のrunで、`python3 scripts/spread.py --base <基準> --score <得点>`の振れ幅と比べて決め、2度回さない。
- 最後にdeployしたcommitとHEADがずれていると、ベンチは始まらない。commitして`make deploy`するか、HEADを戻す。

### isuscopeで読む・比べる
- 比べるときは対象を絞る。HTTPは`--view http --label route=...`、DBは`--view database --window load`（initializeを除く）に`--group-by sql-shape`や`--label-contains digest=...`を足す。絞らない巨大JSONを避ける。
- DBの合計時間の変化は、`total_ms_delta_by_calls`（回数による分）と`total_ms_delta_by_avg`（1回の時間による分）に分けて読む。
- ベンチ側の動きは`isuscope query latest --base BASE_RUN --view transitions`で比べる（遷移ごとの回数・全体に占める割合・間隔。割合の変化が大きい順）。スコアが予想と違う方向に動いた回は、ここで何が変わったかを見る。
- 状態は`isuscope states sql latest "SELECT ..."`（表は`--schema`）で集計し、1つの対象の前後は`isuscope states timeline latest --kind <種類> --id <ID> --related`で時刻順に並べる（`--around <時刻>`で前後に絞る）。1つのSQLは30秒で止まる。止まったら、行ごとに数え直す相関サブクエリをやめ、示された書き方（attrsで結ぶなら`state_attrs`の索引、ある状態にいる数の推移なら累積和）に直す。
- 自由な集計は`isuscope sql`（読み取り専用。表は`--schema`）、時系列は`isuscope series`で見る。
- `analyze`のrun IDは、実行結果か`isuscope list`のものを明示する。本文では短縮ID（末尾8文字）で書く。`provisional`には`--revisit`が要る。FAILと中断のrunに分析は要らない。

## 並行作業
- lane: `claude -p`が自分のworktreeとbranch（`lane/<名前>`）で1回だけ動き、commitして終わる。出力が決まった有限のタスクにだけ使い、常駐させない。mainは`python3 scripts/lane.py status <名前>`で結果を読み、根拠を照合してから取り込む。起動と取り込みの順番は[kickoff](docs/guides/kickoff.md#laneの起動と取り込み)にある。
- improve: 準備で作ったworktree `improve`で起動したセッションが`/improve`で、最新runとmainの分析から最大の制約を選び、変更ごとのbranch（`improve-<名前>`）で実装・ローカル検証して投稿する。投稿できるのはキュー全体の待ちが1件以下のときだけで、空きは`python3 scripts/proposal.py wait`（background）で待つ。採否を待たずに次の変更へ進む。runはrootにしか無いので`scripts/runs.sh`（isuscopeの読み取り）で読む。mainとは`.local/progress.log`の行（`improve started / working`、`proposal submitted / measuring / held / synced / accepted / rejected / deferred`、`main analyzed / freeze`）で知らせ合い、Monitorで追う。
- portal: kickoffの間、準備で作ったworktree `portal`で起動したセッションが`/portal`で、サーバーの起動に要る情報、ベンチ接続、当日マニュアルの保存の順に進め、`.local/progress.log`へ`portal environment / bench / official / notice`を書く。mainはその行を見て`git merge --no-ff portal`する（[kickoff](docs/guides/kickoff.md)）。
- scout（Codex 2体）: `make first-bench`がbaselineの後に起動し、報告の4分後ごとに新しいセッションで探索して`.local/scout/reports.md`へ書く。報告はベンチの起動時にhookが渡す。古い可能性を踏まえ、それぞれの視点を次の改善の参考にする。止めるのは`make scout-stop`。詳細は[scout](docs/roles/scout.md)。
- 人間（scoutの`human`）: 準備で作ったworktree `human`で作業し（投稿は変更ごとのbranchから行い、投稿したら別のbranchへ移る）、意見を`python3 scripts/scout/main.py post human "..."`で書く。報告は他のscoutより先に、ベンチの起動時に渡る。`--emergency`の報告は次のツールの直後に渡るので、今の作業を続ける前に読み、反映するか理由を人間へ伝える。コードは投稿で届く。
- 人間や手で動かすClaude Codeは、`make worktree BRANCH=<name> PURPOSE="..."`のworktreeで調査・実装・ローカル検証をし、変更目的ごとにcommitを分ける。ローカル環境は各worktreeで`make local-up / local-check / local-down`で使える（[ローカル実行環境](docs/local-development.md)）。
- worktreeの変更は`python3 scripts/proposal.py submit --hypothesis ... --expect ... --verified ... --based-on <run>`で投稿し、mainのベンチを待つキューに入れる（`make proposals`、boardの上部）。投稿したbranchは採否が出るまでmainのもので、mainがベンチを待つ間に`proposal.py sync`でmainのbranchへ追従させ、衝突も解消する。mainは、何をまとめて測るか、測らずに返すか、条件付きで保留（`hold --until`）にするかを自分で決め、`python3 scripts/proposal.py decide <branch> accepted|rejected|deferred [--run <短縮ID>]`で返す。ベンチが空いているときは、mainが`pending`で新しい投稿を受け取る。終了前は`proposal.py freeze`でimproveの投稿を止める。
- remote変更・deploy・ベンチ・`.local/operation.lock`を使う操作はmainだけが行う（mainの宣言がある間はhookが止める）。実行中のlockは手で消さず、別の操作の終了を待つ。status・checkなどの読み取りは並行してよい。作業中の他worktreeを変更・削除しない。

## 守ること
- アプリケーションと設定はローカルリポジトリを正とする。サーバー上のファイルを直接編集しない。サーバーごとにコードを複製せず、役割の違いは設定とdeployで扱う。
- 秘密情報と環境固有の情報は`.local/`へ置き、Gitへ入れない。再現に必要な定義は`webapp/`・`config/`・`ansible/`・`infra/`・`scripts/`へ残す。
- 公式情報は要約だけで済ませず、可能な限り原文を`docs/official/`へ保存する。
- 必ず残す記録（ベンチの見取り図、状態ログの対象など）は置き場所が固定で、一覧は`config/records.json`にある。各手順書の「残すもの」が指す場所へ書き、別の場所や別名のファイルに分けない。
- ベンチnodeへの直接の接続、ベンチマーカーの閲覧・コピー、練習中の開催回のコードの取得と攻略法の検索は、hook（`scripts/tool-guard.py`）が止める。止められたら別の経路で回避しない。

## 場所
| 場所 | 中身 |
| --- | --- |
| `webapp/` | 配布されたアプリケーション、静的ファイル、DB schema、初期化処理。deployの正本 |
| `config/` | nginx・MySQL・systemd・sysctlの設定と、node・同期（`sync.json`）・ベンチ接続・laneの宣言 |
| `scripts/` | import・deploy・restart・status・rollbackなどの操作。通常はMakefileから呼ぶ |
| `ansible/`・`infra/` | 全nodeを揃えるplaybook、AWS環境を再現する構成定義 |
| `docs/` | `guides/`（進め方）、`reference/`（仕様）、`official/`（公式の原文）、必ず残す記録 |
| `docs/agent-history/` | 会話・commit・操作の時系列。hookが自動で書くので、手で編集しない |
| `.isuscope/`・`isuscope-data/` | isuscopeの設定とrun。軽いrun履歴は通常のcommitに含め、生ログは重要なrunだけ`git add -f isuscope-data/runs/<run-id>`で残す |
| `.claude/` | rulesのsymlink、hook（`tool-guard.py`、`bench-hook.py`）の設定、mainを宣言する`/main`（`commands/main.md`）、improveの`/improve`（`commands/improve.md`）、portalの`/portal`、mainのプロンプトの上に`★ MAIN`の帯を出すmod（`skills/isucon-main/`） |
| `.local/` | 接続情報・秘密情報・生成物。Git管理しない |

- 過去のベンチは`isuscope list`・`isuscope brief <run>`で読む。
- Claude Codeの生ログ（`~/.claude/projects/<作業ディレクトリのパスの/を-にした名前>/<Session>.jsonl`）は、内部形式が変わり得るので読むだけにし、秘密情報を含み得るのでリポジトリへコピーしない。
