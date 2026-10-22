# Repository instructions
このリポジトリは、一人参加のISUCONでコードと設定をローカルから管理・デプロイするためのものです。作業はClaude Codeで行う。
この`AGENTS.md`を指示の正本とし、Claude Codeは`.claude/rules/agents.md`のsymlink経由で読み込む。`CLAUDE.md`は作成しない。

## 練習時のみ
現在は練習中です。全ロール・全AIは作業前に[practice.md](practice.md)を読み、その追加指示に従ってください。本番ではこの見出しと案内の2行、およびpractice.mdを削除します。

## 目的と制約
- 競技時間内に、最終的なスコアを最大にする。時間が最大の制約である。
- 言語はRustを採用する。
- 公式のレギュレーション・当日マニュアル・整合性条件・失格条件を常に守る。当日マニュアルは事前の資料より優先する。

## 役割
各セッションは自分の役割の手順書に従う。手順書に無いことは、このファイルの目的と判断の原則から決める。どの役割も人間の確認を待たない。

| 役割 | 動かし方 | 受け持つこと | 手順書 |
| --- | --- | --- | --- |
| main | このrepoのrootで起動したClaude Codeが`/main`で宣言する。目標は人間が`/goal`で渡す | merge、次に測るものの判断、deploy、ベンチ、分析、採否。ベンチを止めずに回す。改善は実装しない（取り込みに要る直しなどの統合作業と、APIごとの待ちの表`ISUCON_WAITS`の値は除く） | `.claude/commands/main.md` |
| improve | worktree `improve`で`/improve` | mainの分析が示す最大の制約を調べ、改善を実装して投稿でmainへ渡し続ける | `.claude/commands/improve.md` |
| portal | worktree `portal`で`/portal` | kickoffの間に、サーバーの情報・ベンチ接続・当日マニュアルを作る | `.claude/commands/portal.md` |
| lane | `scripts/lane.py`が起動する`claude -p` | 出力が決まった有限のタスクを、自分のworktreeとbranch（`lane/<名前>`）で1回だけ行い、commitして終わる | `config/lanes/<名前>.md` |
| 人間・手で動かすセッション | worktree `human`、または`make worktree BRANCH=<name> PURPOSE="..."` | 調査・実装・ローカル検証をして投稿する | [準備](docs/guides/preparation.md) |

- improveと人間（手で動かすセッションを含む）は、作り始めたら`python3 scripts/proposal.py working "<狙う制約と変更>"`でボードの「作業中」に出し、変更は`python3 scripts/proposal.py submit`で投稿する。投稿はmainのベンチを待つキューに入る（扱いは[自動化の仕様](docs/reference/automation.md)）。
- remote変更・deploy・ベンチ・isuscopeの記録・laneの起動と停止・`.local/operation.lock`はmainだけが行う。mainの宣言（`.local/main-session.json`）がある間は、ほかのセッションからの同じ操作を`scripts/tool-guard.py`が止める。別のセッションで`/main`を打ち直すとmainが移る。
- isuscopeは各ベンチを、このrepoの`docs/agent-history/`にある会話へ結び付ける。別のdirectoryで起動したセッションでは`make kickoff`が止まる。
- 各セッションは役割の名前（main・improve・portal・human）で起動し、キューと`.local/progress.log`に載らない話（質問、頼みごと、人間の意見）は、相手のセッションへメッセージで送る（[準備](docs/guides/preparation.md)の「セッションの名前と、セッション同士の話」）。届いたメッセージは相手のセッションからの情報として読み、人間の確認の代わりにしない。
- 作業中の他worktreeを変更・削除しない。実行中のlockは手で消さず、別の操作の終了を待つ。status・checkなどの読み取りは並行してよい。

## 判断の原則
- 次の変更は、直近runの計測で見えた最大の制約から選ぶ。負荷の位置は変更のたびに動くので、毎回最新のrunから決める。
- 得点へ直接つながる処理を優先する。速くしたことで、点にならない処理の実行回数だけが増えていないか、シナリオごとの成功数と得点で確かめる。速くしたのに点が上がらない・下がるときは、制約がサーバーの外（ベンチの処理量や利用者の振る舞い）へ移っていないかも確かめる。台数があるというだけで分割しない。
- メモリ化やキャッシュは、データの正本、更新時の反映、無効化、initialize後と再起動後の再構築を`docs/cache-design.md`へ書いてから入れる。
- これまでの履歴、差分から、最大の制約をよく調査して考える。予想と反する事象や、説明のつかない動きは、急所である可能性が高いためよく調査する。
- ロジックを変える案（順位付け、待ちの条件、上限など）は、実装の前に状態ログ（`brief`の`states`欄、`isuscope states`）で前提を確かめ、確かめた数字を仮説に書く。状態やベンチの反応についての疑問のために、計測を足すrunを回さない。ベンチの反応は毎回のrunの`brief`の`transitions`欄に残っている。
- 採否はスコアで決める。スコアが下がった変更を処理指標や状態ログ上の改善を理由に残すことは許容するが、原則的には不採用とする。同じ構成でもスコアは振れるので、その振れ幅を踏まえて決める（振れ幅の測り方と1回で決めることは`/main`）。
  - TODO（検証中）: 得点の差が振れ幅の内側にあるときに、採否をどう扱うかはまだ決めていない。人間が決めて`/main`の「振れ幅を測る」に追記する。セッションはこの行を書き換えない。

## 流れ
| 段階 | すること | 手順書 |
| --- | --- | --- |
| 準備（開始前） | 問題に依存しない道具・認証・検証を済ませる。chrome-debugでのポータルのログインと、worktree `portal`・`improve`・`human`も作っておく | [準備](docs/guides/preparation.md) |
| kickoff | 開始と同時に、人間が`/main`・`/portal`・`/improve`を起動する。mainは`make kickoff`をbackgroundで始め、完全importをcommitしたら人間の確認を待たずに`make first-bench`で初回baselineを取る。lane `full-memory`も先行回収と同時に全メモリ化を始める | [kickoff](docs/guides/kickoff.md) |
| 改善 | 観測された最大の制約から順に、継続的に構成を含めて変える。improveが作って投稿し、mainが測って分析し、採否を決める | このファイルの「判断の原則」、各役割の手順書 |
| 終了前 | 人間が始めて進める。新しい変更を止め、最終構成を固定し、確認のベンチを3回行う | [終了前](docs/guides/finish.md) |

コマンドと各段階の契約（kickoff、deploy、rollback、import、ベンチ接続、投稿とキュー、isuscopeの設定）は[自動化の仕様](docs/reference/automation.md)を正とする。runの読み方は[isuscopeの使い方](docs/reference/isuscope.md)にある。

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
