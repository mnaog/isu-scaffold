---
description: このセッションをportal（ポータルと当日マニュアルを読み、ベンチ接続を作るセッション）にする
allowed-tools: Bash(git rev-parse:*)
---
## 今いる場所

!`git rev-parse --abbrev-ref HEAD; git rev-parse --show-toplevel; git rev-parse --path-format=absolute --git-common-dir`

## portalの役割

kickoffの間、mainと並行して、大会のポータルと当日マニュアルから情報を取り、ベンチ接続を作る。このセッションはmainではない（deploy・kickoff・ベンチはmainが行い、ほかのセッションからは`scripts/tool-guard.py`が止める）。

上の場所がworktree（`make worktree BRANCH=portal PURPOSE="ポータルの情報とベンチ接続"`で作ったもの。2行目と3行目の親が違う）でなければ、そう人間へ伝えて止まる。mainと同じ作業ツリーでcommitすると、kickoffの自動commitと取り合いになるため。

ブラウザは、Playwright MCPがつながっている`--remote-debugging-port=9222`のChromeを使う。ポータルへのログインは人間がそのChromeで済ませている。ログインの画面が出たら、人間に頼んで待つ。

進めた結果は、rootの`.local/progress.log`へ1行ずつ書く。mainはkickoffの間この行を見ている。

```bash
python3 scripts/progress.py emit portal <official|environment|bench|notice> "<要点>"
```

### 1. 公式資料（最優先）

- ポータルの告知、当日マニュアル、レギュレーション、ポータルが指す資料を開き、原文を`docs/official/`へ保存する。取得元URLと取得時刻を書く。
- ログインが要るページは公開情報ではない。保存してよいのはこのprivateのrepoだけ。
- commitしたら`portal official "<commit> <保存したファイル>"`。mainはこれを見てmergeし、lane `scenario`を起動する。

### 2. サーバーの起動に要る情報

- AMI、CloudFormationのテンプレート、台数・種類、region、SSHのuserと鍵の扱いを当日マニュアルとポータルから読む。
- 分かったものをrootの`.local/environment.env`（形は`config/environment.example.env`）へ書き、`portal environment "<要点>"`。サーバーの起動はmainが行う。

### 3. ベンチ接続

- 当日マニュアルとレギュレーションで、ポータルの自動操作（ベンチの依頼、APIの利用、ページの読み取り）が禁止されていないかを先に確かめる。禁止なら作らずに止まり、`portal bench "blocked: <根拠の場所>"`を出して人間に伝える。
- ポータルがisuxportal（ISUCON10〜14のポータル。ページの`isux:`のmetaや`/api/session`で分かる）なら、`docs/reference/automation.md`の「isuxportalのポータルにつなぐ」の手順で作る。
  - `python3 scripts/portal-cookie.py --portal URL`
  - `scripts/isuxportal-bench.py targets`で、ベンチを向けるサーバーのidを決める。
- isuxportalでなければ、ポータルのページがベンチの依頼に使うAPIをブラウザのnetworkから読み、同じ形（終わりに1行の結果を出し、合否を終了コードで返す）のscriptを作る。
- `config/benchmark.env`と`config/benchmark-contract.json`（「ベンチの競技条件の確認」）を書く。続けて`python3 scripts/benchmark-contract.py --seal`、`.isuscope/benchmark.sh --check`、`.isuscope/benchmark.sh --probe`を通す。
- ベンチ自体は依頼しない。初回はmainの`make first-bench`が回す。
- commitしたら`portal bench "<commit> <transport、targetなど要点>"`。mainはこれを見てmergeする。
- cookieは`.local/benchmark-secrets.env`（Gitの外）に置き、値を会話にもcommitにも出さない。

### 4. その後の見張り

- 数分おきに、ポータルの告知・質問への回答・通知を見る。新しいものがあれば、原文を`docs/official/`へ足してcommitし、`portal notice "<要約と、作業に関わるか>"`。
- cookieが切れたら`scripts/portal-cookie.py`を流し直す。ログインし直しが要るなら人間に頼む。
- mainのbranchの変更が要るときは`git merge main`する。質問をポータルへ送るかどうかは、人間とmainに任せる。

今いる場所と、最初に開くページを短く報告してから始める。
