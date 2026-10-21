---
description: このセッションをimprove（最新runの最大の制約を調べて改善を作り、mainへ投稿し続けるセッション）にする
allowed-tools: Bash(git rev-parse:*), Bash(python3 scripts/progress.py path), Bash(python3 scripts/main-session.py show), Bash(python3 scripts/proposal.py improve)
---
## 今いる場所

!`git rev-parse --abbrev-ref HEAD; git rev-parse --show-toplevel; git rev-parse --path-format=absolute --git-common-dir; python3 scripts/progress.py path; python3 scripts/main-session.py show`

## improveの宣言

!`python3 scripts/proposal.py improve`

## improveの役割

目的は、点を上げる変更を途切れなくmainへ渡すこと。改善の調査・実装・ローカル検証・投稿を続ける。このセッションはmainではない。merge、どれを次に測るか、deploy、ベンチ、分析、採否はmain（`/main`）が行い、ほかのセッションのそれらの操作は`scripts/tool-guard.py`が止める。mainは自分で考えて採否を決め、ベンチにかけずに棄却することもある。投稿は戻ってこない。取り込めない投稿はmainが直すか棄却する。

前提は準備（`docs/guides/preparation.md`）で済んでいる。このセッションは、準備で作ったworktree `improve`で動いている。

- 宣言がエラー（worktreeでない）なら、そう人間へ伝えて止まる。mainの作業ツリーでcommitすると、mainのmergeやkickoffの自動commitと取り合いになるため。
- mainの宣言が無ければ（上の`main-session.py show`）、そう人間へ伝える。投稿は宣言の後のmainへ渡るので、作業は始めてよい。

### キュー

投稿はmainのベンチを待つキューに入る。**improveが投稿できるのは、キュー全体の待ちが1件以下のときだけ**（人間など他の投稿は制限しない）。待ちが多いほど、投稿の前提にしたrunが古くなるため。

- 待ちが2件以上なら`submit`は終了コード3で断る。そのときは`python3 scripts/proposal.py wait`を`run_in_background`で起動し、終わりの通知が来たら投稿する。待つ間は、深掘り（次の制約や、予想と違った動きの調査）、時間のかかる実装、ただ待つ、のどれでもよい。
- mainは投稿を好きな順で選び、まとめて測ることもある。キューの並びは測る順ではない。
- 投稿したbranchは、採否が出るまでmainのもの。commitも投稿し直しもしない。mainがベンチを待つ間に、mainのbranchへ追従させ、衝突も解消する。直したくなったら新しいbranchで作り、古い方は`python3 scripts/proposal.py withdraw <branch>`で取り下げる。
- キューと、保留（mainが「この制約が最大になったら測る」と寝かせたもの）は、`make proposals`とboardの上部で見える。
- mainの`main freeze`（終了前の固定）の後は、`submit`も`wait`も終了コード4で止まる。新しい変更は作らない。

### 知らせの受け取り

Monitorで`python3 scripts/progress.py follow improve`を追う。`.local/progress.log`のうちimprove宛ての行だけが、何をするかの一言と一緒に出る（宛先と一言は`config/progress.json`）。sleepやpollingで待たない。

### runの読み方

runはrootにしか無いので、`scripts/runs.sh`で読む（isuscopeの読み取りをrootで実行する）。何をどう読むかは`docs/reference/isuscope.md`。

```bash
scripts/runs.sh list --limit 5
scripts/runs.sh brief latest                 # latest_analysisにmainの分析
scripts/runs.sh query latest --base BASE_RUN --view http --label route=...
scripts/runs.sh query latest --base BASE_RUN --view transitions   # ベンチ側の動きの変化
scripts/runs.sh states sql latest "SELECT ..."
scripts/runs.sh change list
```

laneは`make lanes`。

### 1つの変更の流れ

1. **選ぶ**: mainの最新の分析が示す最大の制約から選ぶ（AGENTS.mdの「判断の原則」）。分析とrunが合わない、予想と違う動きがあるときは、その理由を先に調べる。ロジックを変える案（順位付け、待ちの条件、上限など）は、実装の前に状態ログで前提を確かめ、確かめた数字を仮説に書く。キューにある投稿とは、作業が重ならず、一緒に測っても効果を切り分けられるものを選ぶ。APIの前後に一律に待つだけの変更は、mainが`ISUCON_WAITS`（`scripts/waits.py`）で試すので作らない（`waits.py history`に結果がある）。応答の中身や状態で待ちを変えるものは作ってよい。
2. **branchを切る**: `git switch -c improve-<短い名前> <mainのbranch>`（mainのbranchは上の`main-session.py show`の`branch`）。取り込み前の自分の投稿の上に積むなら、そのbranchから切る（`submit`が積んだことを記録し、土台が棄却・取り下げになると一緒にキューから外れる）。切ったら`python3 scripts/proposal.py working "<狙う制約と変更>"`で、ボードの「作業中」に出す（`improve working`の行も出る。投稿すると消え、やめたら`working --done`）。
3. **作る**: 変更の目的ごとにcommitを分ける。メモリ化やキャッシュは、`docs/cache-design.md`に正本・反映・無効化・再構築を書いてから入れる。
4. **確かめる**: 最後に判定するのはmainのベンチなので、ここで要るのは、buildが通り、ベンチを落とさない（応答と状態の変わり方を壊さない）と分かっていることまで。使える道具は`make build`、テスト、`make local-up / local-check`、`make local-flow`と状態ログの比較（`docs/local-development.md`）。サーバーの様子は`make status`と`scripts/ssh-node.sh <node> '<読むだけのcommand>'`で見てよいが、サーバー上を変えない。
5. **投稿する**:

   ```bash
   python3 scripts/proposal.py submit --based-on <根拠のrunの短縮ID> \
     --hypothesis "何を変えて、なぜ点が上がるか（確かめた数字）" \
     --expect "期待する観測値（route・SQL・CPUなど、briefとqueryで確かめられるもの）"
   ```

   ローカルで確かめたことがあれば`--verified "..."`で添える（mainが読む）。

   終了コード3（キューが埋まっている）なら、上の「キュー」のとおり`wait`をbackgroundで起動する。
6. **次へ**: `submit`がこのworktreeを投稿したbranchから外す（HEADは同じcommit）。次のbranchを切り、採否を待たずに次の候補を進めてよい。調整の続きのように直前の結果を見ないと作れない変更は、結果を待つか、その間に別の制約を調べる。
7. **返事を読む**: `rejected`・`deferred`は、mainの分析とrunで理由を確かめて次の案に活かす。速くしたのに点が下がった変更は、`--view transitions`で比較元とベンチ側の動きを比べ、何に反応したかを確かめてから次を作る。採用された変更の上で、最大の制約がどこへ動いたかを見て次を選ぶ。

### kickoffの間

- `code-ready`の後、コード・schema・`docs/official/`・（lane `scenario`が書いたら）`docs/benchmark-scenario.md`を読み、得点の仕組みと重そうな処理の見当をつける。
- 初回baselineの前の性能変更はmainがremoteへ入れない。投稿は作ってよいが、測るのはbaselineの後。
- lane `indexes`・`n-plus-one`・`obvious-fixes`（kickoffが起動）と`full-memory`（kickoffと同時に起動）の範囲は作らない。`make lanes`と`lane:<名前> commit`の行で確かめる。

### しないこと

- deploy・rollback・ベンチ・isuscopeの記録（analyze、change decide）・投稿の採否と保留・追従（`sync`）・laneの起動と停止。
- rootの作業ツリーや、他のworktree、投稿したbranchを変える。

今いる場所、mainの宣言、キューの待ち、今の段階（kickoffの途中か、最新runとmainの分析があるか）を短く報告してから始める。
