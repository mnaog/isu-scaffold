# lane: scenario

あなたは公式資料とアプリのコードから、`docs/benchmark-scenario.md`（ベンチの見取り図）を書く。mainのセッションは`docs/official/`をcommitした直後にこのlaneを起動し、初回ベンチの分析でこの見取り図を使う。速さが価値なので、得点とFAILに効く部分から書く。

## 前提

- 作業場所はこのworktreeとbranchだけ。`AGENTS.md`の規則に従う（練習中で`practice.md`があればそれにも従う）。remote node・deploy・ベンチ・isuscopeの記録操作・`.local/operation.lock`には触れない（hookが止める）。
- 読むもの: `docs/official/`（参加者向けの公式資料）、`webapp/`のコードとschema。ベンチマーカーの内部コード、運営向けの出力（ISUCON12の`[ADMIN]`行など）、過去の改善結果は使わない。
- 書くもの: `docs/benchmark-scenario.md`だけ。

## 書く内容

`config/records.json`の「ベンチの見取り図」の定義と`docs/guides/kickoff.md`「ベンチマークシナリオを分析する」に沿って、次を書く。

1. **得点**: 何をすると何点か。資料の表・式をそのまま引用し、出典（`docs/official/`のfileと見出し）を付ける。
2. **FAILと減点**: 何が起きるとシナリオが止まるか、失格になるか。整合性チェックの内容と、チェックが前提にしている状態の順序（支払いと完了、在庫と注文など）。応答を速くしたり処理を前倒ししたりする変更は、この順序を崩すと落ちるため。
3. **シナリオ**: どのシナリオがあり、各シナリオがどのAPIをどの順番で呼ぶか。並列数と繰り返しの条件。APIの一覧には、各APIのhandler（`file:line`）と主なSQL・外部処理の列を付ける。handler・middleware・SQL・table・外へのHTTPは`make codemap`（Goのsourceから機械的に作る表。`JSON=1`でJSON）から写し、手で探さない。表に`unresolved`と出たhandlerだけをコードで確かめる。シナリオ → API → handler → SQLのつながりはここに書く。
4. **initialize**: 呼ばれた後に期待される状態と時間制限。
5. **得点へ直接つながる処理**: 上の各項目を、コードのhandler（`file:line`）と対応付ける。

各項目に根拠を付け、「確認済みの事実」「資料やコードからの推測」「不明」を見出しか表の列で分ける。許可された資料で確かめられない並列数・繰り返し・内部条件は不明と書き、推測で埋めない。

## 終わり方

`docs/benchmark-scenario.md`をcommitする（commit messageは英語）。最後の回答は日本語で、得点に最も効く処理の上位と、FAILの条件で特に注意すべきものを短く並べる。mainのセッションは`git merge lane/scenario`で取り込み、初回runの観測で推測の部分を確かめる。
