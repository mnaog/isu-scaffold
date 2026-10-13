# lane: indexes

あなたは`make kickoff`が先行回収したアプリのSQLを読み、明らかな索引不足だけを先行して実装する。mainのセッションは同時に環境構築と初回ベンチ（baseline）を進め、baselineで重かったSQLとこのbranchのcommitを照合して、効くものから取り込んでdeployする。索引は最初の改善として最も早く効くことが多いので、速さを優先し、1索引ができるたびにcommitする。

## 前提

- 作業場所はこのworktreeとbranchだけ。`AGENTS.md`の規則に従う（練習中で`practice.md`があればそれにも従う）。remote node・deploy・ベンチ・isuscopeの記録操作・`.local/operation.lock`には触れない（hookが止める）。
- 変更してよいのはschema（`webapp/sql`、initializeが流す索引定義）と`docs/indexes.md`だけ。アプリのコードは変えない（N+1などはlane `obvious-fixes`の担当で、同じfileを触るとmergeで衝突する）。

## 進め方

1. アプリのコード（`webapp/rust`）からSQLを集め、WHERE・JOIN・ORDER BY・GROUP BYに使う列と、schemaの主キー・既存索引を照合する。
2. 呼ばれ方（requestごと・ループ内・一覧API）から重そうなものを先に、索引がない条件を選ぶ。複合索引は条件の等価列を先、範囲・ORDER BY列を後にする。
3. **1つの索引を1つのcommitにする。** mainは途中のcommitから順に取り込むので、効果が大きそうなものから順にcommitする。commit messageは英語で、1行目に索引、本文に対象SQL（`file:line`）と期待する観測値（どのSQLの行走査・時間が減るか）を書く。
4. 索引はinitializeが流すschemaへ入れる。initializeがtableを作り直すか、データ投入の後に索引を作るかを確かめ、initializeの所要時間を大きく延ばさない形にする。
5. `docs/indexes.md`へ索引ごとに、対象のSQL、`初期化後の行数: 未確認`、`効果を確かめたrun ID: 未測定`を残す（同じcommitに含める）。
6. 応答を変えない。ORDER BYの無いSQLの結果順が索引で変わり得る場合は、その索引を入れずに最後の回答で候補として挙げる。
7. `config/local/compose.yaml`があれば`make local-up`と`make local-check`でinitializeが通ることを確かめ、終わったら`make local-down`する。確かめられなかったことはcommit本文に書く。

## 終わり方

最後の回答は日本語で、commitごとに: hash、索引、対象SQL（`file:line`）、期待効果、initializeへの影響、確認したこと。入れなかった候補とその理由も短く並べる。
