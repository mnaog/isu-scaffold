# lane: waits

あなたは`make kickoff`が先行回収したアプリに、APIごとの小さな待ち（wait）を入れる口を作る。待ちの表は環境変数`ISUCON_WAITS`で、mainのセッションが空き時間に値を変え、得点や負荷が良くなる箇所を探す（`scripts/waits.py`）。値はbuildし直さずに変えられ、表はアプリのEnvironmentFileに置かれるので、再起動と主催者の追試でも同じ表で動く。このlaneが作るのは口だけで、表は空のまま。価値は、表が空のときに挙動と速さを変えずに、どのAPIにも前後の待ちを入れられること。

## 前提

- 作業場所はこのworktreeとbranchだけ。`AGENTS.md`の規則に従う（練習中で`practice.md`があればそれにも従う）。remote node・deploy・ベンチ・isuscopeの記録操作・`.local/operation.lock`には触れない（hookが止める）。baseline前の変更をremoteへ反映しない規則は、mainが取り込み時に守る。
- 変更してよいのは`webapp/`のアプリのコードだけ。schema（`webapp/sql`）、`config/`、`scripts/`、文書は変えない（表を置く環境変数の行は、mainが`scripts/waits.py set`で書く）。
- lane `full-memory`・`indexes`・`n-plus-one`・`obvious-fixes`・`state-log`・`runtime`が同時に同じcrateを変える（`runtime`はrouterからrequestごとのlogのmiddlewareを外す）。mergeで衝突しにくいよう、変更はmoduleの追加、`mod waits;`の1行、routerへのmiddlewareの追加だけにし、周りの行を書き換えない（整形し直さない、`cargo fmt`をcrate全体へかけない）。

## 手順

1. **moduleを写す。** `config/waits/waits.rs`をcrateの`src/`へそのまま写し（`cp config/waits/waits.rs webapp/rust/src/waits.rs`）、crateのroot（`main.rs`か`lib.rs`）に`mod waits;`を1行足す。中身は変えない。表の形と意味はfile冒頭のコメントにある。
2. **middlewareを1つ足す。** アプリのframeworkと版に合わせて、すべてのrouteを通るmiddlewareを書く（axum 0.7の例はfile冒頭のコメント）。
   - `waits::global()`が空なら、何もせずに次へ渡す。表が空のときに応答・status code・速さを変えない。
   - 待つかを決めるのは、要求のmethodとpath（`uri().path()`。query stringを含めない）。
   - `before`はhandlerの前、`after`はhandlerの後で応答を返す前に、非同期のsleep（`tokio::time::sleep`など）で待つ。threadを止めるsleepを使わない。応答の本文を読み出してbufferし直さない。
   - routerの外で返す応答（静的ファイルをアプリが返すなら、それも含めて）も同じmiddlewareを通るように、routerの最後に足す。initializeも通ってよい（表に書かなければ待たない）。
3. **確かめる。** `cargo build --release`と既存のtestを通す。`config/local/compose.yaml`があれば`make local-up`で動かし、`make local-check`が通ることを確かめる。`ISUCON_WAITS`を付けて起動を確かめられる場合は、routeを1つ表に書いて、そのrouteだけ応答がその分遅れること、標準エラーに`waits: ...`の行が出ることを確かめる。`make local-down`する。確かめられなかったことはcommit本文に書く。

## commit

1つのcommitにする（moduleの追加、`mod waits;`、middleware）。commit messageは英語で、本文に、middlewareを足した場所（`file:line`）、確かめたこと。

## 終わり方

最後の回答は日本語で、次を短く並べる。

1. commitのhash、変えたfile、middlewareを足した場所（`file:line`）
2. 表に書けるrouteの一覧（methodとpath。pathの変わる区切りは`:name`で書く）。isuscopeのroute名と違う書き方になる箇所があれば、その対応
3. 確かめたこと、確かめられなかったこと、mainが取り込み時に気をつけること（他のlaneと触る行が重なりそうな箇所）

