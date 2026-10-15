---
description: このセッションをportal（ポータルから、サーバーの情報とベンチ接続を最短で作るセッション）にする
allowed-tools: Bash(git rev-parse:*)
---
## 今いる場所

!`git rev-parse --abbrev-ref HEAD; git rev-parse --show-toplevel; git rev-parse --path-format=absolute --git-common-dir`

## portalの役割

目的は、mainの最初のベンチを1分でも早く回せるようにすること。kickoffの間、mainと並行して、大会のポータルからサーバーの情報とベンチ接続を作る。このセッションはmainではない（deploy・kickoff・isuscopeでのベンチはmainが行い、ほかのセッションからは`scripts/tool-guard.py`が止める）。

前提は準備（`docs/guides/preparation.md`の「ポータル」）で済んでいる: chrome-debug（port 9222。Playwright MCPがつながる）でポータルにログイン済みで、このセッションは準備で作ったworktree `portal`で動いている。

- 上の場所がworktreeでない（2行目と3行目の親が同じ）なら、そう人間へ伝えて止まる。mainと同じ作業ツリーでcommitすると、kickoffの自動commitと取り合いになるため。
- ログインの画面が出たら、人間に頼んで待つ。

進めた結果は、rootの`.local/progress.log`へ1行ずつ書く。mainはkickoffの間この行を見ている。

```bash
python3 scripts/progress.py emit portal <environment|bench|official|notice> "<要点>"
```

しないこと（ベンチを遅らせるだけ）: ポータルの各ページの写し・索引・要約を作る、`docs/reference/`などの手順書を直す、scaffoldのテストを流す、memoryを書く。ベンチの枠は気にしない。分からなければ依頼して確かめる方が早い。

### 1. サーバーの情報（最優先。mainはこれでサーバーを起動する）

- ポータルと当日マニュアルから、AMI、CloudFormationのテンプレート、台数・種類、region、SSHのuserと鍵の扱い、既に動いているサーバーのIPを読む。
- rootの`.local/environment.env`（形は`config/environment.example.env`）へ書き、`portal environment "<要点>"`。テンプレートなど秘密を含むものは`.local/`に置く。

### 2. ベンチ接続（mainの最初のベンチに間に合わせる）

- 当日マニュアルとポータルの告知で、ベンチの自動実行が禁止されていないかだけを確かめる。禁止なら作らずに`portal bench "blocked: <根拠の場所>"`を出し、人間に伝える。読んだマニュアルは、そのまま原文を`docs/official/`へ保存しておく（3とcontractで使う）。
- isuxportal（ISUCON10〜14。`/api/session`がprotobufを返す）なら`scripts/isuxportal-bench.py`を使う（`docs/reference/automation.md`の「isuxportalのポータルにつなぐ」）。cookieは`python3 scripts/portal-cookie.py --portal URL`。
- 違う種類なら、まずブラウザでベンチを1回依頼し、その通信（URL、method、header、form、応答）と結果の画面を見る。それを真似るscriptを`scripts/`に書く。
  - `run`: 依頼して終わるまで待ち、選手向けの出力の後に、結果を1行（例 `PORTALJOB job=<id> passed=<true|false> score=<n>`）出す。合否は終了コードでも返す。
  - `probe`: ログインとベンチ対象だけを確かめる（依頼しない）。
- scriptで実際に1回回す。その出力から`config/benchmark.env`（score・PASS・FAILのregexは最後の1行だけに合わせる）、`BENCHMARK_SAMPLE_SCORE`・`BENCHMARK_SAMPLE_PASS`、`config/benchmark-sample.log`を作る。
- `config/benchmark-contract.json`（sourcesは保存したマニュアル）を書き、`python3 scripts/benchmark-contract.py --seal`、`.isuscope/benchmark.sh --check`、`.isuscope/benchmark.sh --probe`を通す。
- commitして`portal bench "<commit> <transport、target、1回回した結果>"`。mainはこれを見てmergeし、isuscopeでベンチを回す。
- cookieは`.local/benchmark-secrets.env`（Gitの外）に置き、値を会話にもcommitにも出さない。

### 3. 公式資料

- 当日マニュアル、レギュレーション、アプリのマニュアルの原文を`docs/official/`へ保存し（2で保存したものは済み）、取得元URLと取得時刻を`docs/official/README.md`へ1行ずつ足す。
- commitして`portal official "<commit>"`。mainはこれを見てmergeし、lane `scenario`を起動する。

### 4. その後の見張り

- 数分おきに、ポータルの告知・質問への回答・通知を見る。新しいものがあれば原文を`docs/official/`へ足してcommitし、`portal notice "<要約と、作業に関わるか>"`。
- cookieが切れたら`scripts/portal-cookie.py`を流し直す。ログインし直しが要るなら人間に頼む。
- mainのbranchの変更が要るときは`git merge main`する。質問をポータルへ送るかどうかは、人間とmainに任せる。

今いる場所と、最初に開くページを短く報告してから始める。
