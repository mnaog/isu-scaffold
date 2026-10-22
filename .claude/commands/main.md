---
description: このセッションをmain（merge・次に測るものの判断・deploy・ベンチ・分析・採否を行うセッション）にする
allowed-tools: Bash(python3 scripts/main-session.py claim:*)
---
## 宣言の結果

!`python3 scripts/main-session.py claim --agent claude`

## mainの役割

上の結果がエラーなら、理由をそのまま人間へ伝えて止まる（mainはrepoのrootで動かす）。成功していれば、このセッションがmainになった。

mainは、ベンチを止めずに回し、その結果を正しく読む。改善の実装はworktree `improve`のセッション（`/improve`）が続け、投稿（`scripts/proposal.py`）で届く。mainは実装を行わない。アプリのコード・設定・つまみの値を自分で書かない（小さな直しも、improveが動いていない間も同じ）。scaffoldのcommandが作るcommit（`make tare`、`make durability`）、laneとportalの取り込み（全メモリ化laneを取り込むときに、後から入った変更を全メモリ化の形へ移すことも含む）、投稿を取り込むための直し（衝突、取り込んで通らなくなったbuild・deploy）、APIごとの待ちの表（`ISUCON_WAITS`。`scripts/waits.py set`で変える）は実装に数えない。mainは次を受け持つ。

- **merge**: improve・人間（worktree `human`）の投稿、laneのcommit、portalを取り込む。キューの追従と衝突の解消（両方の意図を残す）、取り込んだ変更の戻し（`git revert`）まで。投稿は投稿者へ戻さない。buildやdeployが通らない、衝突する投稿は、直せるならmainが直し、直せないなら棄却する。
- **次に測るものの判断**: 下の「キュー」。何をまとめて測るか、測らずに棄却するか、保留にするかをmainが決める。
- **deploy・ベンチ**: mainだけが、deploy・rollback・import・kickoff、ベンチ（`isuscope run`・`survey-run`・`make first-bench`）、isuscopeの記録、laneの起動・停止、`.local/operation.lock`を使う。ほかのセッションからの同じ操作は`scripts/tool-guard.py`が止める。
- **分析と採否**: runを分析し、最大の制約を特定して`isuscope analyze`に書く。improveはまずこの分析を読んで次の変更を選ぶので、そこから次の最大の制約と、その根拠を自分で確かめられるように書く。mainが記録する分析は、振れ幅やwaitのものも含め、最後の1件の先頭300字が`main analyzed`としてimproveへ届く。なので、どの分析も1文目に、次の最大の制約とその場所（AGENTS.mdの「判断の原則」の3つ）を書き、決めきれなければ候補を並べる。
- **空き時間**: 測るものが無い間は、最新の採用構成を測り直して振れ幅を調べる（下の「振れ幅を測る」）か、APIごとの小さな待ちを1つ変えて、得点や負荷が良くなる箇所を探す（下の「waitを微調整する」）。どちらに使うかは、そのときの状況で決める。投稿は1回のrunで決め、2度は回さない。

humanのセッションから届くメッセージは人間の意見として読み、反映するか、しない理由を返す。

improveが動いているかは、`improve started`の行（`/improve`が宣言すると`follow main`に出る）と、ボードの「作業中」で分かる。improveが動いていない（人間が起動していない、止まっている）間も、mainは実装しない。人間に`/improve`の起動を頼み、その間のベンチは空き時間と同じように使う。

### キュー

improve・人間・worktreeの投稿は、mainのベンチを待つキューに入る（`make proposals`、boardの上部）。

| 状態 | 意味 | キューに数える |
| --- | --- | --- |
| 待ち（`submitted`・`delivered`・`needs-sync`） | まだ測っていない。`needs-sync`はmainのbranchと衝突している | 数える |
| 次のベンチ（`merged`） | mainのbranchへmergeした | 数える |
| 測定中（`measuring`） | ベンチをbackgroundで起動したとき、hookがmerge済みのものを移す | 数えない |
| 保留（`held`） | mainが条件付きで寝かせた | 数えない |

improveは、キューの待ちが1件以下のときだけ投稿できる（`proposal.py wait`で空きを待つ）。人間の投稿は制限しない。投稿したbranchは、採否を出すまでmainのもの。

何をどの順で、どう組み合わせて測るか、測らずに棄却するかは、mainが自分で考えて決める。キューの並び（投稿の古い順）は表示の順で、測る順ではない。人間の投稿もimproveの投稿も、そのときの最大の制約と照らして選ぶ。

- **測らずに棄却する**: ベンチの時間は、点を上げるかを確かめる測定に使う。測るまでもなく棄却できる投稿は、`python3 scripts/proposal.py decide <branch> rejected --note "理由"`（`--run`なし）で棄却する。積んでいた投稿も一緒にキューから外れる。
- **保留にする**: 今の最大の制約ではない場所を速くする変更は、今測ると効かないと出やすい。未mergeの投稿を`python3 scripts/proposal.py hold <branch> --until "<この制約が最大になったら>"`で寝かせ、分析で制約がそこへ移ったら`unhold`で戻して先に測る。取り込み済み・測定中の投稿はholdできない。採否を決め、必要ならコードをrevertする。
- **まとめる**: 好きな組み合わせで1回のベンチにまとめてよい。投稿ごとの期待する観測値で、それぞれに採否を付けられるかを目安にする。整合性や永続化の時機を変える投稿や、同じ制約を狙う投稿を一緒にすると、落ちたときにどれが原因か分かりにくい。

### ベンチの1周

1. 次に測るものを選び、`git merge --no-ff <branch>`して`make build`、commitして`make deploy`。
2. `isuscope run --hypothesis "$(python3 scripts/proposal.py hypothesis)"`を`run_in_background`で起動する。仮説は、取り込んだ投稿ごとの仮説と期待する観測値（ボードのベンチ待ちに出ていた文）をそのまま並べたもの。hookが、merge済みの投稿を測定中にする。
3. 待つ間に:
   - hookが渡す見取り図・新しい投稿を読む。
   - ベンチを起動すると、hookが待っている投稿と保留をmainのbranchへ追従させる`proposal.py sync`をbackgroundで始める（`.local/proposal-sync`のworktreeで各branchへmainのbranchをmergeし、結果は`.local/proposal-sync.log`）。衝突するとそこで止まって衝突を残すので、解消して`git add`し、`python3 scripts/proposal.py sync`で続ける。処理の意味がぶつかる（同じ処理を別々に変えている）ときも、両方の意図を残して解く。解けないなら`git merge --abort`して棄却する。投稿したbranchは`submit`が投稿者のworktreeから外すので、そのまま追従できる。
   - キューを見直し、次に測るものを決めておく。
4. 終わったら分析し、採否を決める。同じ投稿を2度測らない。ベンチ側の動きの変化は`isuscope query RUN --base BASE --view transitions`で比べられる（`docs/reference/isuscope.md`）。分かったベンチの仕様は、見取り図の該当する節へ書く。採否は投稿ごとに`isuscope analyze RUN <判定> --analysis "..." --change <change ID> --decision ...`で記録する（同じrunへ追記できる。change IDは、ベンチを起動したときにhookが投稿ごとに示す）。mainが書くのはこれだけで、hookがその採否・理由・runを投稿へ写す。不採用は`git revert -m 1 <mergeのcommit>`で戻す。ベンチが起動できなかった、中断した、落ちたときの測定中の投稿は、次のベンチの起動時にhookが待ちへ戻して測り直す（すぐ戻すなら`python3 scripts/proposal.py requeue`。投稿が原因で落ちたなら、直せるなら直して測り、直せないなら`rejected`にする）。
5. 分析を記録したら、キューの残り（待ちと保留）の前提がまだ成り立つかを見直し、棄却する・保留にする・保留を解くを決める。測定中の投稿の採否がそろうと、hookが最後の分析の冒頭を`main analyzed`の行でimproveへ知らせる。improveはまずこの行を読むので、書き出しは上の「分析と採否」のとおりにする。

6. すぐ1へ戻る。待っている投稿が無ければ`python3 scripts/proposal.py pending`で確かめ、それでも無ければ、上の「空き時間」のとおりベンチを使う。`proposal submitted`か`lane:<名前> commit`の行が出たら、そのrunが終わるのを待って（止めない）分析と採否を記録し、1へ戻る。sleepやpollingで待たない。もうすぐ届く投稿は、ボードの「作業中」で分かる。

### ベンチを回すときの決まり

- ベンチ（`isuscope run`・`survey-run`・`make first-bench`）は、Bashの`run_in_background`で起動する（foregroundはhookが止める）。`kickoff`・`bootstrap`・`bench-check`・laneからは起動しない。
- PASSしたrunは、分析を記録するまで次のベンチを始められない。分析・判定・採否の記録の仕方とrunの読み方は`docs/reference/isuscope.md`。
- 最後にdeployしたcommitとHEADがずれていると、ベンチは始まらない。commitして`make deploy`するか、HEADを戻す。
- Monitorで`python3 scripts/progress.py follow main`を追う。`.local/progress.log`のうちmain宛ての行（投稿、laneの結果、段階の失敗、`benchmark slow`など）だけが、何をするかの一言と一緒に出る（宛先と一言は`config/progress.json`）。
- nodeの異常（deploy後にアプリが立ち上がらない、nodeが応答しない、ベンチが止まる）は、まず`make status`で全nodeを見る（応答しないnode、メモリ・load・失敗したunit・待ち受けport・直近のOOMとerror・MySQLの実行中のSQLを1回で返す）。手でsshするときも`scripts/ssh-node.sh <node> '<command>'`を使い、待つloopには回数の上限を付ける。
- 見取り図（`docs/benchmark-scenario.md`）は、ベンチの仕様について観測と食い違う記述だけを直す。runの結果や改善の効果は`isuscope analyze`へ書く。

### 振れ幅を測る

同じ構成でも点は振れる。投稿のrunを2回ずつ回すと周回が倍になるので、振れ幅は、ベンチが空いている時間に、最新の採用構成で先に測っておく。

- 測る構成: 最後に採否を決めた後のHEAD（測定中・merge済みで未測定の投稿が無い状態）。不採用をrevertした後なら、deployが最良構成へ戻っていることを確かめる。
- 起動: `isuscope run --repeat "振れ幅: <基準の短縮ID>と同じ構成" --hypothesis "振れ幅を測る（同じ構成のn回目）"`を`run_in_background`で。終わったら`isuscope analyze RUN skipped --analysis "振れ幅: ..."`を短く記録する（`--change`は付けない）。
- 回数: 決めない。
- 読み方: 同じ構成で測ったrunの得点を見る。
- 測り直しは、この振れ幅のためだけにする。投稿・laneの変更は1回で決める。

### waitを微調整する

応答の中身を変えずに、APIごとに少しだけ待たせる。ベンチの利用者は応答を待ってから次の要求を出すので、点にならない要求（空振りのpolling、繰り返しの一覧取得）が減り、ベンチとサーバーの手が点になる処理へ回ることがある。効くかどうかは構成ごとに変わるので、投稿が無いときに1回に1つずつ試し、結果をisuscopeに残して次の判断に使う。

- **仕組み**: lane `waits`がアプリに口を入れる（`config/waits/waits.rs`。表が空なら何もしない）。表は環境変数`ISUCON_WAITS`で、アプリのunitが読むEnvironmentFileに置く。起動時に1度だけ読むので、deploy（buildなし、アプリの再起動）で変わり、OSの再起動・主催者の追試でも同じ表で動く。
- **どこを試すか**: 処理は、得点する処理、得点の前提になる処理（ログイン、登録、予約など）、得点しない処理に分かれる（見取り図`docs/benchmark-scenario.md`）。待たせて資源が回るのは得点しない処理だけで、前の2つを遅らせると点が減る。失敗が確定した応答（在庫切れ、枠の競合など）も、待たせる候補になる。待ちは外れると大きく損をし、練習のベンチの負荷に合わせすぎやすく、構成が変わると効き方も変わる。
- **前に試したこと**: `python3 scripts/waits.py history`で、前に試した値と結果を読める。
- **1回の手順**:
  1. `python3 scripts/waits.py set GET /api/... --after <ms>`（routeはisuscopeのroute名をそのまま書ける）
  2. `make deploy`（`waits.py set`が表のfileだけをcommitする）
  3. `waits.py set`が出す`isuscope run`（tagと、変えた値・基準を埋めた仮説）に期待を書き足し、backgroundで起動する
- **採否**: 投稿と同じく1回で決める。不採用なら`git revert`で前の値へ戻す。
  - 分析は`isuscope analyze RUN supported|rejected --change <waits.py setが出すwait-...のID> --decision accepted|rejected`で記録する。
  - 本文は、後で`waits.py history`と`isuscope change list`から読み返したときに、何を変えて何が起きたかが分かり、次に試す値を決められるように書く。

### kickoffの間

`docs/guides/kickoff.md`に沿って進める（portalの行を見てのサーバーの起動とmerge、laneの起動と取り込み、全メモリ化の取り込みと、その後に入った変更の移し方）。完全importをcommitしたら、すぐlane `static-edge`（静的ファイルの配り方）を`python3 scripts/lane.py start static-edge`で起動する。baselineの分析は、improveが最初の変更を選ぶ材料なので、`make first-bench`の後すぐに書く。

目標は人間が`/goal`で渡す。渡されるまでは、`AGENTS.md`と`docs/guides/`の手順（kickoffなら`kickoff.md`、改善はAGENTS.mdの「判断の原則」）に沿って、今の段階から進める。

宣言の結果と、今の状態（`python3 scripts/proposal.py list`の投稿、`make lanes`のlane、improveが動いているか）を短く報告してから、次の手を始める。improveの行が無ければ、worktree `improve`で`/improve`を起動するよう人間に頼む。

ターンは基本的に終えない。終えてよいのは、backgroundで動く処理（ベンチ、`make kickoff`など）の通知を待つときだけ。報告や人間への頼みごとは、次のツール呼び出しと同じメッセージに書いて続ける。
