---
description: このセッションをmain（merge・次に測るものの判断・deploy・ベンチ・分析・採否を行うセッション）にする
allowed-tools: Bash(python3 scripts/main-session.py claim:*)
---
## 宣言の結果

!`python3 scripts/main-session.py claim --agent claude`

## mainの役割

上の結果がエラーなら、理由をそのまま人間へ伝えて止まる（mainはrepoのrootで動かす）。成功していれば、このセッションがmainになった。

mainは、ベンチを止めずに回し、その結果を正しく読む。改善の実装はworktree `improve`のセッション（`/improve`）が続け、投稿（`scripts/proposal.py`）で届く。mainは次を受け持つ。

- **merge**: improve・人間（worktree `human`）の投稿、laneのcommit、portalを取り込む。キューの追従と衝突の解消、取り込んだ変更の戻し（`git revert`）、build・deployが通らない小さな直しまではmainが行う。それ以上の実装はimproveへ返す。
- **次に測るものの判断**: 下の「キュー」。何をまとめて測るか、測らずに返すか、保留にするかをmainが決める。
- **deploy・ベンチ**: mainだけが、deploy・rollback・import・kickoff、ベンチ（`isuscope run`・`survey-run`・`make first-bench`）、isuscopeの記録、laneとscoutの起動・停止、`.local/operation.lock`を使う。ほかのセッションからの同じ操作は`scripts/tool-guard.py`が止める。
- **分析と採否**: runを分析し、最大の制約を特定して`isuscope analyze`に書く。improveはこの分析から次の変更を選ぶので、根拠（run ID、node別の観測値、状態ログの数字）と、次の最大の制約をはっきり書く。

improveが動いているかは、`.local/progress.log`の`improve started`（`/improve`が宣言すると出る）・`improve working`の行で分かる。improveが動いていない（人間が起動していない、止まっている）間は、これまでどおりmainが実装も行う。

### キュー

improve・人間・worktreeの投稿は、mainのベンチを待つキューに入る（`make proposals`、boardの上部）。

| 状態 | 意味 | キューに数える |
| --- | --- | --- |
| 待ち（`submitted`・`delivered`・`needs-sync`） | まだ測っていない。`needs-sync`はmainのbranchと衝突している | 数える |
| 次のベンチ（`merged`） | mainのbranchへmergeした | 数える |
| 測定中（`measuring`） | ベンチをbackgroundで起動したとき、hookがmerge済みのものを移す | 数えない |
| 保留（`held`） | mainが条件付きで寝かせた | 数えない |

improveは、キューの待ちが1件以下のときだけ投稿できる（`proposal.py wait`で空きを待つ）。人間の投稿は制限しない。投稿したbranchは、採否を出すまでmainのもの。

mainは自分で考えて、何をどうまとめて測るか、測らずに返すかを決める。

- **測らずに返す**: 前提が崩れた、最新の分析と合わない、確かめが足りない（ローカルでbuild・checkしていない、`docs/cache-design.md`を書かずにメモリ化している）なら、`python3 scripts/proposal.py decide <branch> rejected|deferred --note "理由"`（`--run`なし）で返す。積んでいた投稿も一緒に返る。
- **保留にする**: 今の最大の制約ではない場所を速くする変更は、今測ると効かないと出やすい。`python3 scripts/proposal.py hold <branch> --until "<この制約が最大になったら>"`で寝かせ、分析で制約がそこへ移ったら`unhold`で戻して先に測る。
- **まとめる**: 互いに独立した投稿は、1回のベンチでまとめて測ってよい。各投稿の期待する観測値で、投稿ごとに採否を付けられるかで決める。同じ制約を狙う投稿、整合性や永続化の時機を変える投稿、構成（台数・配置）を変える投稿は単独で測る。
- **先に測る**: FAILや整合性エラーの修正、直前の変更で落ちた点の戻し。

### ベンチの1周

1. 次に測るものを選び、`git merge --no-ff <branch>`して`make build`、commitして`make deploy`。
2. `isuscope run --hypothesis "..."`を`run_in_background`で起動する。仮説には、含めた投稿ごとに、branch・期待する観測値を並べる。hookが、merge済みの投稿を測定中にする。
3. 待つ間に:
   - hookが渡す見取り図・scoutの報告・新しい投稿を読む。
   - `python3 scripts/proposal.py sync`で、待っている投稿と保留をmainのbranchへ追従させる（`.local/proposal-sync`のworktreeで、各branchへmainのbranchをmergeする）。衝突すると、そこで止まって衝突を残す。解消して`git add`し、もう一度`sync`する。処理の意味がぶつかる（同じ処理を別々に変えている）なら、`git merge --abort`して投稿者へ返す。投稿者のworktreeでcheckoutされたままのbranchは追従できないので、投稿者に離れてもらう。
   - キューを見直し、次に測るものを決めておく。
4. 終わったら分析する。投稿ごとに`isuscope analyze RUN <判定> --analysis "..." --change <branch> --decision ...`を記録し（同じrunへ追記できる）、`python3 scripts/proposal.py decide <branch> accepted|rejected|deferred --run <短縮ID> --note "理由"`で返す。不採用は`git revert -m 1 <mergeのcommit>`で戻す。
5. 分析を記録したら、キューの残り（待ちと保留）の前提がまだ成り立つかを見直し、返す・保留にする・保留から戻すを決める。そのうえでimproveへ知らせる。

   ```bash
   python3 scripts/progress.py emit main analyzed "<短縮ID> score <n>（<base>比 ±x%）。次の最大の制約: <node・処理・数字>"
   ```

6. すぐ1へ戻る。待っている投稿が無ければ`python3 scripts/proposal.py pending`で確かめ、それでも無ければ、Monitorで追っている`.local/progress.log`に`proposal submitted`か`lane:<名前> commit`が出るまで待つ。sleepやpollingで待たない。`improve working`の行で、もうすぐ届く投稿が分かる。

### kickoffの間

worktree `portal`のセッション（`/portal`）が、サーバーの情報、ベンチ接続、当日マニュアルの順に作る。`.local/progress.log`の`portal`の行を追い、`environment`を見たらサーバーを起動する。`official`を見たら`git merge --no-ff portal`してlane `scenario`を起動し、`bench`を見たら同じくmergeして`.isuscope/benchmark.sh --check`を通す。`notice`は内容を確かめ、作業に関わるなら反映する。baselineの分析は、improveが最初の変更を選ぶ材料なので、`make first-bench`の後すぐに書いて`main analyzed`を出す。

### 終了前

開始時に決めた時刻になったら、`python3 scripts/proposal.py freeze --note "<最終構成のcommit>"`でimproveの新しい投稿を止め（`main freeze`の行が出る）、`docs/guides/finish.md`へ進む。キューに残った投稿は、測らずに`deferred`で返す。

目標は人間が`/goal`で渡す。渡されるまでは、`AGENTS.md`と`docs/guides/`の手順（kickoffなら`kickoff.md`、改善はAGENTS.mdの「判断の原則」、終了前なら`finish.md`）に沿って、今の段階から進める。

宣言の結果と、今の状態（`python3 scripts/proposal.py list`の投稿、`make lanes`のlane、improveが動いているか）を短く報告してから、次の手を始める。improveの行が無ければ、worktree `improve`で`/improve`を起動するよう人間に頼む。
