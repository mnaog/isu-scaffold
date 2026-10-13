# lane: code-map

あなたは`make kickoff`が先行回収したアプリのコードから、`docs/code-map.md`（コードの地図）を書く。mainのセッションは同時に環境構築・ベンチ接続・初回ベンチを進めており、初回ベンチの分析でこの地図を使う。速さが価値なので、網羅より「得点と負荷に効く経路が先に分かる」ことを優先する。

## 前提

- 作業場所はこのworktreeとbranchだけ。`AGENTS.md`と`practice.md`の規則に従う。remote node・deploy・ベンチ・isuscopeの記録操作・`.local/operation.lock`には触れない（hookが止める）。
- 読むもの: `webapp/`配下の回収済みコードとschema（`webapp/rust`、`webapp/sql`）。`docs/official/`があれば読む。公式資料がまだ無い場合、得点や失敗条件は推測として書く。
- 書くもの: `docs/code-map.md`だけ。コードは変更しない。

## 書く内容

`config/records.json`の「コードの地図」の定義（シナリオ → API → handler → SQL・外部処理 → サーバー → 得点・失敗）に沿って、次の順で書く。

1. **API一覧**: method、path、handler（`file:line`）、認証、主に触るtable。1行1API の表にする。
2. **主要経路の詳細**: 呼ばれ方から重そうな経路を先に。handlerごとに、発行するSQL（table、WHERE・JOIN・ORDER BY・LIMIT）、ループ内SQL（N+1）、transactionとlock（`FOR UPDATE`など）、外部HTTP・ファイルI/O、応答の並び順を決めている箇所。
3. **schemaと索引**: tableごとの主キー・既存索引と、上のSQLが使う列。索引が無いWHERE・JOIN・ORDER BYを列挙する（行数は不明なら不明と書く）。
4. **initialize**: 何をreset・投入するか、外部command、時間がかかりそうな処理。
5. **静的ファイルとnginx**: アプリが返している静的ファイル、画像、大きな応答。
6. **得点・失敗との対応**: 公式資料かコードから確認できたものと推測を分けて書く。

各項目に根拠の`file:line`を付ける。確認済みの事実・推測・不明を区別する。

## 終わり方

`docs/code-map.md`をcommitする（commit messageは英語で、何を地図にしたか）。最後の回答は日本語で、地図から見えた負荷の大きそうな経路の上位5件と、その根拠の`file:line`を短く並べる。mainのセッションはこの回答と`git merge lane/code-map`で受け取る。
