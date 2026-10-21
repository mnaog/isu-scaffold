---
description: このセッションをmain（merge・次に測るものの判断・deploy・ベンチ・分析・採否を行うセッション）にする
allowed-tools: Bash(python3 scripts/main-session.py claim:*)
---
## 宣言の結果

!`python3 scripts/main-session.py claim --agent claude`

## mainの役割

上の結果がエラーなら、理由をそのまま人間へ伝えて止まる（mainはrepoのrootで動かす）。成功していれば、このセッションがmainになった。

mainは、ベンチを止めずに回し、その結果を正しく読む。改善の実装はworktree `improve`のセッション（`/improve`）が続け、投稿（`scripts/proposal.py`）で届く。**mainは改善を実装しない。** アプリのコード・設定・つまみの値を自分で書かない（小さな直しも、improveが動いていない間も同じ）。scaffoldのcommandが作るcommit（`make tare`、`make durability`）、laneとportalの取り込みは実装に数えない。mainは次を受け持つ。

- **merge**: improve・人間（worktree `human`）の投稿、laneのcommit、portalを取り込む。キューの追従と衝突の解消（両方の意図を残すだけ）、取り込んだ変更の戻し（`git revert`）まで。buildやdeployが通らない投稿は直さずに戻し、理由を付けて投稿者へ返す。
- **次に測るものの判断**: 下の「キュー」。何をまとめて測るか、測らずに返すか、保留にするかをmainが決める。
- **deploy・ベンチ**: mainだけが、deploy・rollback・import・kickoff、ベンチ（`isuscope run`・`survey-run`・`make first-bench`）、isuscopeの記録、laneとscoutの起動・停止、`.local/operation.lock`を使う。ほかのセッションからの同じ操作は`scripts/tool-guard.py`が止める。
- **分析と採否**: runを分析し、最大の制約を特定して`isuscope analyze`に書く。improveはこの分析から次の変更を選ぶので、根拠（run ID、node別の観測値、状態ログの数字）と、次の最大の制約をはっきり書く。
- **振れ幅**: 測るものが無い間は、最新の採用構成を測り直して、その構成での振れ幅を先に調べておく（下の「振れ幅を測る」）。投稿はこの幅と比べて1回のrunで決め、2度は回さない。

improveが動いているかは、`.local/progress.log`の`improve started`（`/improve`が宣言すると出る）・`improve working`の行で分かる。improveが動いていない（人間が起動していない、止まっている）間も、mainは実装しない。人間に`/improve`の起動を頼み、その間は振れ幅を測る。

### キュー

improve・人間・worktreeの投稿は、mainのベンチを待つキューに入る（`make proposals`、boardの上部）。

| 状態 | 意味 | キューに数える |
| --- | --- | --- |
| 待ち（`submitted`・`delivered`・`needs-sync`） | まだ測っていない。`needs-sync`はmainのbranchと衝突している | 数える |
| 次のベンチ（`merged`） | mainのbranchへmergeした | 数える |
| 測定中（`measuring`） | ベンチをbackgroundで起動したとき、hookがmerge済みのものを移す | 数えない |
| 保留（`held`） | mainが条件付きで寝かせた | 数えない |

improveは、キューの待ちが1件以下のときだけ投稿できる（`proposal.py wait`で空きを待つ）。人間の投稿は制限しない。投稿したbranchは、採否を出すまでmainのもの。

何をどの順で、どう組み合わせて測るか、測らずに返すかは、mainが自分で考えて決める。キューの並び（投稿の古い順）は表示の順で、測る順ではない。人間の投稿もimproveの投稿も、そのときの最大の制約と照らして選ぶ。

- **測らずに返す**: 前提が崩れた、最新の分析と合わない、確かめが足りない（ローカルでbuild・checkしていない、`docs/cache-design.md`を書かずにメモリ化している）なら、`python3 scripts/proposal.py decide <branch> rejected|deferred --note "理由"`（`--run`なし）で返す。積んでいた投稿も一緒に返る。
- **保留にする**: 今の最大の制約ではない場所を速くする変更は、今測ると効かないと出やすい。未mergeの投稿を`python3 scripts/proposal.py hold <branch> --until "<この制約が最大になったら>"`で寝かせ、分析で制約がそこへ移ったら`unhold`で戻して先に測る。取り込み済み・測定中の投稿はholdできない。採否を決め、必要ならコードをrevertする。
- **まとめる**: 好きな組み合わせで1回のベンチにまとめてよい。投稿ごとの期待する観測値で、それぞれに採否を付けられるかを目安にする。整合性や永続化の時機を変える投稿や、同じ制約を狙う投稿を一緒にすると、落ちたときにどれが原因か分かりにくい（seventhでは、FxHashと待ち時間の戻しを一緒に入れて落ち、戻すのに3 runかかった）。
- **FAIL・整合性エラーの修正**: 土台が揺れていると、ほかの測定が信用できない。早めに入れる。

### ベンチの1周

1. 次に測るものを選び、`git merge --no-ff <branch>`して`make build`、commitして`make deploy`。
2. `isuscope run --hypothesis "..."`を`run_in_background`で起動する。仮説には、含めた投稿ごとに、branch・期待する観測値を並べる。hookが、merge済みの投稿を測定中にする。
3. 待つ間に:
   - hookが渡す見取り図・scoutの報告・新しい投稿を読む。
   - `python3 scripts/proposal.py sync`で、待っている投稿と保留をmainのbranchへ追従させる（`.local/proposal-sync`のworktreeで、各branchへmainのbranchをmergeする）。衝突すると、そこで止まって衝突を残す。解消して`git add`し、もう一度`sync`する。処理の意味がぶつかる（同じ処理を別々に変えている）なら、`git merge --abort`して投稿者へ返す。投稿者のworktreeでcheckoutされたままのbranchは追従できないので、投稿者に離れてもらう。
   - キューを見直し、次に測るものを決めておく。
4. 終わったら分析する。いつもの回は短く（スコア、効いた・効かなかった観測、次の最大の制約）、予想と違った回だけ深く掘る。採否は、`python3 scripts/spread.py --base <基準の構成のrunの短縮ID> --score <得点>`で、基準の構成の平均と振れ幅に照らして1回で決める。振れ幅より上なら採用、下なら不採用。振れの内側なら点の差としては扱わず、投稿の期待した観測値が狙いどおりに動き、ほかの指標に害が無ければ採用、そうでなければ不採用にする。同じ投稿を2度測らない。スコアが予想と違う方向に動いた回（速くしたのに下がった、FAILした）は、採否を決める前に`isuscope query RUN --base BASE --view transitions`でベンチ側の動きの変化（どの遷移の割合・間隔が変わったか）を見る。分かったベンチの仕様は、見取り図の該当する節へ書く。seventhでは分析がベンチ以外で一番長く（60 runで52分）、改善の周回を決めていた。投稿ごとに`isuscope analyze RUN <判定> --analysis "..." --change <branch> --decision ...`を記録し（同じrunへ追記できる）、`python3 scripts/proposal.py decide <branch> accepted|rejected|deferred --run <短縮ID> --note "理由"`で返す。不採用は`git revert -m 1 <mergeのcommit>`で戻す。ベンチが起動できなかった、中断した、ベンチの外の原因で落ちたときは、`python3 scripts/proposal.py requeue`で測定中の投稿を待ちへ戻す（投稿が原因で落ちたなら、戻さずに`rejected`で返す）。
5. 分析を記録したら、キューの残り（待ちと保留）の前提がまだ成り立つかを見直し、返す・保留にする・保留から戻すを決める。そのうえでimproveへ知らせる。

   ```bash
   python3 scripts/progress.py emit main analyzed "<短縮ID> score <n>（<base>比 ±x%）。次の最大の制約: <node・処理・数字>"
   ```

6. すぐ1へ戻る。待っている投稿が無ければ`python3 scripts/proposal.py pending`で確かめ、それでも無ければ、次の「振れ幅を測る」でベンチを使う。`proposal submitted`か`lane:<名前> commit`の行が出たら、振れ幅のrunが終わるのを待って（止めない）1へ戻る。sleepやpollingで待たない。`improve working`の行で、もうすぐ届く投稿が分かる。

### 振れ幅を測る

同じ構成でも点は振れる（ninthでは同じ構成の3回で643388〜720355、平均から−7%〜+4%）。投稿のrunを2回ずつ回すと周回が倍になるので、振れ幅は、ベンチが空いている時間に、最新の採用構成で先に測っておく。

- 測る構成: 最後に採否を決めた後のHEAD（測定中・merge済みで未測定の投稿が無い状態）。不採用をrevertした後なら、deployが最良構成へ戻っていることを確かめる。
- 起動: `isuscope run --repeat "振れ幅: <基準の短縮ID>と同じ構成" --hypothesis "振れ幅を測る（同じ構成のn回目）"`を`run_in_background`で。終わったら`isuscope analyze RUN skipped --analysis "振れ幅: ..."`を短く記録する（`--change`は付けない）。
- 回数: 同じ構成で3回（採用を決めたrunを含む）までは続けて測る。3回そろったら、次の投稿か構成の変化を待つ間だけ足す。
- 読み方: `python3 scripts/spread.py`がHEADの構成の回数・平均・平均からのずれと、判定の目安を出す。その構成の回数が3回未満の間は、直近に3回測った構成の振れ幅を借りる。
- 測り直しは、この振れ幅のためと終了前の確認（`docs/guides/finish.md`）だけにする。投稿・laneの変更は1回で決める。

### kickoffの間

worktree `portal`のセッション（`/portal`）が、サーバーの情報、ベンチ接続、当日マニュアルの順に作る。`.local/progress.log`の`portal`の行を追い、`environment`を見たらサーバーを起動する。`official`を見たら`git merge --no-ff portal`してlane `scenario`を起動し、`bench`を見たら同じくmergeして`.isuscope/benchmark.sh --check`を通す。`notice`は内容を確かめ、作業に関わるなら反映する。完全importをcommitしたら、すぐlane `static-edge`（静的ファイルの配り方）を`python3 scripts/lane.py start static-edge`で起動する。lane `full-memory`（全メモリ化）はkickoffと同時に始まる。`lane:full-memory complete`が出たら次のベンチで最優先に測る。mergeの前に、laneの最後の回答にある取り込んだmainのhashより後にmainへ入った`webapp/`の変更を`git revert`で戻し（自分では移さない）、`main analyzed`の行で、どの投稿を全メモリ化の形で作り直してほしいかをimproveへ伝える。baselineの分析は、improveが最初の変更を選ぶ材料なので、`make first-bench`の後すぐに書いて`main analyzed`を出す。

### 終了前

開始時に決めた時刻になったら、`python3 scripts/proposal.py freeze --note "<最終構成のcommit>"`でimproveの新しい投稿を止め（`main freeze`の行が出る）、`docs/guides/finish.md`へ進む。キューに残った投稿は、測らずに`deferred`で返す。

目標は人間が`/goal`で渡す。渡されるまでは、`AGENTS.md`と`docs/guides/`の手順（kickoffなら`kickoff.md`、改善はAGENTS.mdの「判断の原則」、終了前なら`finish.md`）に沿って、今の段階から進める。

宣言の結果と、今の状態（`python3 scripts/proposal.py list`の投稿、`make lanes`のlane、improveが動いているか）を短く報告してから、次の手を始める。improveの行が無ければ、worktree `improve`で`/improve`を起動するよう人間に頼む。
