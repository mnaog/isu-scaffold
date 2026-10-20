---
description: このセッションをimprove（最新runの最大の制約を調べて改善を作り、mainへ投稿し続けるセッション）にする
allowed-tools: Bash(git rev-parse:*), Bash(python3 scripts/progress.py path), Bash(python3 scripts/main-session.py show)
---
## 今いる場所

!`git rev-parse --abbrev-ref HEAD; git rev-parse --show-toplevel; git rev-parse --path-format=absolute --git-common-dir; python3 scripts/progress.py path; python3 scripts/main-session.py show`

## improveの役割

目的は、点を上げる変更を途切れなくmainへ渡すこと。改善の調査・実装・ローカル検証・投稿を続ける。このセッションはmainではない。merge、どれを次に測るか、deploy、ベンチ、分析、採否はmain（`/main`）が行い、ほかのセッションのそれらの操作は`scripts/tool-guard.py`が止める。

前提は準備（`docs/guides/preparation.md`）で済んでいる。このセッションは、準備で作ったworktree `improve`で動いている。

- 上の場所がworktreeでない（2行目と3行目の親が同じ）なら、そう人間へ伝えて止まる。mainの作業ツリーでcommitすると、mainのmergeやkickoffの自動commitと取り合いになるため。
- mainの宣言が無ければ、そう人間へ伝える。投稿は宣言の後のmainへ渡るので、作業は始めてよい。

### 知らせの受け取り

上の4行目が、mainと共有する`.local/progress.log`。Monitorで`tail -n 0 -f <その場所>`を追い、次の行で動く。sleepやpollingで待たない。

| 行 | すること |
| --- | --- |
| `kickoff code-ready` | コードとschemaが入った。読み始める（下の「kickoffの間」） |
| `first-bench baseline-ready` | 初回runができた。mainの分析を待つ間にrunを読む |
| `main analyzed` | mainが分析を記録した。行の要約（スコア、次の最大の制約）とrunの分析を読み、次の変更を選び直す |
| `proposal accepted / rejected / deferred` | 自分の投稿なら、理由とrunを読む |
| `lane:<名前> commit / complete` | laneの内容を確かめ、同じ変更を作らない |
| `portal notice` | 当日の告知。作業に関わるか確かめる |
| `main freeze` | 終了前。新しい変更を止める |

始めたら`python3 scripts/progress.py emit improve started "<worktree>"`を1回出す。mainはこの行で、improveが動いていることを知る。

### runの読み方

runはrootにしか無いので、`scripts/runs.sh`で読む（isuscopeの読み取りをrootで実行する）。

```bash
scripts/runs.sh list --limit 5
scripts/runs.sh brief latest                 # latest_analysisにmainの分析
scripts/runs.sh query latest --base BASE_RUN --view http --label route=...
scripts/runs.sh states sql latest "SELECT ..."
scripts/runs.sh change list
```

scoutと人間の報告は、rootの`.local/scout/reports.md`にある（上の4行目と同じ`.local`）。投稿の一覧は`make proposals`、laneは`make lanes`。

### 1つの変更の流れ

1. **選ぶ**: mainの最新の分析が示す最大の制約から選ぶ（AGENTS.mdの「判断の原則」）。分析とrunが合わない、予想と違う動きがあるときは、その理由を先に調べる。ロジックを変える案（順位付け、待ちの条件、上限など）は、実装の前に状態ログで前提を確かめ、確かめた数字を仮説に書く。
2. **branchを切る**: `git switch -c improve-<短い名前> <mainのbranch>`（mainのbranchは上の`main-session.py show`の`branch`）。前の投稿がまだ取り込まれておらず、その上に積む変更なら、そのbranchから切り、`--note`に依存を書く。切ったら`python3 scripts/progress.py emit improve working "<branch>: <狙う制約>"`を出す（mainが、次のベンチに入れるために待つかを決める）。
3. **作る**: 変更の目的ごとにcommitを分ける。メモリ化やキャッシュは、`docs/cache-design.md`に正本・反映・無効化・再構築を書いてから入れる。
4. **確かめる**: `make build`、テスト、`make local-up / local-check`、状態の変わり方が変わる変更は`make local-flow`と状態ログの比較（`docs/local-development.md`）。サーバーの様子は`make status`と`scripts/ssh-node.sh <node> '<読むだけのcommand>'`で見てよいが、サーバー上を変えない。
5. **投稿する**:

   ```bash
   python3 scripts/proposal.py submit --based-on <根拠のrunの短縮ID> \
     --hypothesis "何を変えて、なぜ点が上がるか（確かめた数字）" \
     --expect "期待する観測値（route・SQL・CPUなど、briefとqueryで確かめられるもの）" \
     --verified "ローカルで確かめたこと"
   ```

6. **すぐ次へ**: 採否を待たない。次の候補（同じ制約の次の手、2番目の制約）を新しいbranchで始める。mainは独立した投稿をまとめて1回のベンチで測ることがあるので、同じ箇所を触る変更を何本も並べない（`needs-sync`で返る）。
7. **返事を読む**: `needs-sync`なら、そのbranchでmainのbranchをmergeして投稿し直す。`rejected`は、mainの分析とrunで理由を確かめて次の案に活かす。採用された変更の上で、最大の制約がどこへ動いたかを見て次を選ぶ。

### kickoffの間

- `code-ready`の後、コード・schema・`docs/official/`・（lane `scenario`が書いたら）`docs/benchmark-scenario.md`を読み、得点の仕組みと重そうな処理の見当をつける。
- 初回baselineの前の性能変更はmainがremoteへ入れない。投稿は作ってよいが、測るのはbaselineの後。
- lane `indexes`・`obvious-fixes`（kickoffが起動）と`full-memory`（baselineの後にmainが起動）の範囲は作らない。`make lanes`と`lane:<名前> commit`の行で確かめる。

### しないこと

- deploy・rollback・ベンチ・isuscopeの記録（analyze、change decide）・投稿の採否・laneとscoutの起動と停止。
- rootの作業ツリーや、他のworktreeを変える。
- mainの`main freeze`の行（終了前に新しい変更を止める）の後に、新しい変更を投稿する。止めた後は、mainから頼まれた修正だけを作る。

今いる場所、mainの宣言、今の段階（kickoffの途中か、最新runとmainの分析があるか）を短く報告してから始める。
