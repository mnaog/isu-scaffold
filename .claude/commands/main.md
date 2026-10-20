---
description: このセッションをmain（merge・次に測るものの判断・deploy・ベンチ・分析・採否を行うセッション）にする
allowed-tools: Bash(python3 scripts/main-session.py claim:*)
---
## 宣言の結果

!`python3 scripts/main-session.py claim --agent claude`

## mainの役割

上の結果がエラーなら、理由をそのまま人間へ伝えて止まる（mainはrepoのrootで動かす）。成功していれば、このセッションがmainになった。

mainは、ベンチを止めずに回し、その結果を正しく読む。改善の実装はworktree `improve`のセッション（`/improve`）が続け、投稿（`scripts/proposal.py`）で届く。mainは次を受け持つ。

- **merge**: improve・人間（worktree `human`）の投稿、laneのcommit、portalを取り込む。衝突の解消、取り込んだ変更の戻し（`git revert`）、build・deployが通らない小さな直しまではmainが行う。それ以上の実装はimproveへ返す。
- **次に測るものの判断**: 下の「次に測るもの」。
- **deploy・ベンチ**: mainだけが、deploy・rollback・import・kickoff、ベンチ（`isuscope run`・`survey-run`・`make first-bench`）、isuscopeの記録、laneとscoutの起動・停止、`.local/operation.lock`を使う。ほかのセッションからの同じ操作は`scripts/tool-guard.py`が止める。
- **分析と採否**: runを分析し、最大の制約を特定して`isuscope analyze`に書く。improveはこの分析から次の変更を選ぶので、根拠（run ID、node別の観測値、状態ログの数字）と、次の最大の制約をはっきり書く。

improveが動いているかは、`.local/progress.log`の`improve started`・`improve working`の行で分かる。improveが動いていない（人間が起動していない、止まっている）間は、これまでどおりmainが実装も行う。

### ベンチの1周

1. 次に測るものを選び、`git merge --no-ff <branch>`して`make build`、commitして`make deploy`。
2. `isuscope run --hypothesis "..."`を`run_in_background`で起動する。仮説には、含めた投稿ごとに、branch・期待する観測値を並べる。
3. 待つ間に、hookが渡す見取り図・scoutの報告・新しい投稿を読み、次に測るものを選んでおく。
4. 終わったら分析する。投稿をまとめて測ったときは、投稿ごとに`isuscope analyze RUN <判定> --analysis "..." --change <branch> --decision ...`を記録し（同じrunへ追記できる）、`python3 scripts/proposal.py decide <branch> accepted|rejected|deferred --run <短縮ID> --note "理由"`で返す。
5. 分析を記録したら、improveへ知らせる。

   ```bash
   python3 scripts/progress.py emit main analyzed "<短縮ID> score <n>（<base>比 ±x%）。次の最大の制約: <node・処理・数字>"
   ```

6. すぐ1へ戻る。待っている投稿が無ければ`python3 scripts/proposal.py pending`で確かめ、それでも無ければ、Monitorで追っている`.local/progress.log`に`proposal submitted`か`lane:<名前> commit`が出るまで待つ。sleepやpollingで待たない。`improve working`の行で、もうすぐ届く投稿が分かる。それを待つか、今ある投稿だけで先に回すかを決める（ベンチを空けておくより、今ある投稿を先に測る方がよいことが多い）。

### 次に測るもの

投稿は`pending`かベンチの起動時のhookで届く（`根拠のrun`はimproveがその投稿を作るときに読んだrun）。

- 先に測る: FAILや整合性エラーの修正、直前の変更で落ちた点の戻し。
- 次に: 最新runの最大の制約を狙う投稿。根拠のrunより後に最大の制約が動いていたら、測る前に`deferred`で理由を返すか、そのまま測るかを決める。
- 互いに独立した投稿（触る処理と狙う制約が違う）は、1回のベンチでまとめて測ってよい。スコアが下がったとき、または期待した観測が出なかった投稿があるときは、効いていない側を戻して測り直す。
- 同じ制約を狙う複数の投稿、整合性や永続化の時機を変える投稿、構成（台数・配置）を変える投稿は、効き方を切り分けられるように単独で測る。
- 投稿の確かめが足りない（ローカルでbuild・checkしていない）、`docs/cache-design.md`を書かずにメモリ化している、と分かったら、測らずにimproveへ返す。

### kickoffの間

worktree `portal`のセッション（`/portal`）が、サーバーの情報、ベンチ接続、当日マニュアルの順に作る。`.local/progress.log`の`portal`の行を追い、`environment`を見たらサーバーを起動する。`official`を見たら`git merge --no-ff portal`してlane `scenario`を起動し、`bench`を見たら同じくmergeして`.isuscope/benchmark.sh --check`を通す。`notice`は内容を確かめ、作業に関わるなら反映する。baselineの分析は、improveが最初の変更を選ぶ材料なので、`make first-bench`の後すぐに書いて`main analyzed`を出す。

### 終了前

開始時に決めた時刻になったら、`python3 scripts/progress.py emit main freeze "<最終構成のcommit>"`でimproveに新しい変更を止めさせ、`docs/guides/finish.md`へ進む。

目標は人間が`/goal`で渡す。渡されるまでは、`AGENTS.md`と`docs/guides/`の手順（kickoffなら`kickoff.md`、改善はAGENTS.mdの「判断の原則」、終了前なら`finish.md`）に沿って、今の段階から進める。

宣言の結果と、今の状態（`python3 scripts/proposal.py list`の投稿、`make lanes`のlane、improveが動いているか）を短く報告してから、次の手を始める。improveの行が無ければ、worktree `improve`で`/improve`を起動するよう人間に頼む。
