---
description: このセッションをmain（deploy・ベンチ・採否の判断を行うセッション）にする
allowed-tools: Bash(python3 scripts/main-session.py claim:*)
---
## 宣言の結果

!`python3 scripts/main-session.py claim --agent claude`

## mainの役割

上の結果がエラーなら、理由をそのまま人間へ伝えて止まる（mainはrepoのrootで動かす）。成功していれば、このセッションがmainになった。

- mainだけが、deploy・rollback・import・kickoff、ベンチ（`isuscope run`・`survey-run`・`make first-bench`）、isuscopeの記録、laneとscoutの起動・停止、`.local/operation.lock`を使う。ほかのセッションからの同じ操作は`scripts/tool-guard.py`が止める。
- worktreeで人間やClaude Codeが作った変更は、投稿（`scripts/proposal.py`）として届く。ベンチを`run_in_background`で起動すると、scoutの報告と一緒に、まだ読んでいない投稿のうちmainのbranchへ衝突なく入るものが渡される。待つ間に読み、次に測るものを選んで`git merge --no-ff <branch>`と`make build`をしておく。1回のベンチで測る変更は1つにする。
- 投稿を測って採否を決めたら、isuscopeの`analyze --change ... --decision ...`に加えて、`python3 scripts/proposal.py decide <branch> accepted|rejected|deferred --run <短縮ID> --note "理由"`で投稿者へ返す。
- 目標は人間が`/goal`で渡す。渡されるまでは、`AGENTS.md`と`docs/guides/`の手順（kickoffなら`kickoff.md`、改善はAGENTS.mdの「判断の原則」、終了前なら`finish.md`）に沿って、今の段階から進める。

宣言の結果と、今の状態（`python3 scripts/proposal.py list`の投稿、`make lanes`のlane）を短く報告してから、次の手を始める。
