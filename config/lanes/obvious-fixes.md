# lane: obvious-fixes

あなたは`make kickoff`が先行回収したアプリに、索引とN+1以外の自明な修正（下の「対象」）を先行して実装する。索引の追加はlane `indexes`が、N+1はlane `n-plus-one`が同時に行う。mainのセッションは同時に環境構築と初回ベンチ（baseline）を進め、baselineの観測と照合してから、このbranchのcommitを選んで取り込み、deployして測る。速さが価値なので、根拠が明確で挙動を変えないものから順に、小さく確実に入れる。

## 前提

- 作業場所はこのworktreeとbranchだけ。`AGENTS.md`の規則に従う（練習中で`practice.md`があればそれにも従う）。remote node・deploy・ベンチ・isuscopeの記録操作・`.local/operation.lock`には触れない（hookが止める）。baseline前の変更をremoteへ反映しない規則は、mainが取り込み時に守る。
- 変更してよいのは`webapp/`のアプリのコードだけ。schema（`webapp/sql`）、`config/`、`scripts/`、他の文書は変えない（索引はlane `indexes`の担当で、同じfileを触るとmergeで衝突する）。
- lane `n-plus-one`が同じhandlerのループ内SQLを変える。mergeで衝突しにくいよう、直すのに要る行だけを変え、周りを書き換えない（整形し直さない、`gofmt`をpackage全体へかけない）。lane `full-memory`（全メモリ化）も同時に始まり、読み取りと状態の判定をメモリへ移す。mainはこのlaneのcommitを全メモリ化と並行して取り込む。
- 実行時の設定（requestごとのlog、DB接続のpoolと`interpolateParams`、GCの設定）はlane `runtime`が同時に直すので触らない。HTTP clientの使い回しはこのlaneの担当。

## 対象

全メモリ化が入ると、読み取りのSQL（全件走査、不要なカラム、locking readでの判定）はstoreへ移って無くなる。このlaneは、全メモリ化の後も残る所を直す。

- 一括INSERT・UPDATE・UPSERTに置き換えられる逐次write
- 単一行のカウンタへの書き込みによる直列化
- transaction内の不要な処理と、書き込みのDB往復
- 同一request内の重複計算、外部呼び出し、重い計算など、DBを読まない処理の明らかな無駄

キャッシュ・メモリ化、非同期化、write-behind、台の分離、整合性や永続化の時機を変える変更は実装しない。見つけたら最後の回答に候補として書く。

## 守ること

- **応答を変えない。** 返す値・件数・並び順・エラーとstatus codeを元のコードと一致させる。ORDER BYの無いSQLを置き換えるときは、元の実行計画で返っていた順序（主キー順など）を保つか、変えない。迷ったら入れずに候補へ回す。
- **1つの修正を1つのcommitにする。** mainが選んで取り込めるように、commit同士の依存を避ける。mainはlaneの途中でもcommitを1件ずつ取り込むので、影響の大きいものから順にcommitし、1件ごとに完成させる。commit messageは英語で、1行目に変更、本文に直した問題（`file:line`）と期待する観測値（どのrouteやSQLの何が減るか）を書く。
- commitの前に、`webapp/go`で`go build ./...`と`go vet ./...`、既存のtest（`go test ./...`）を通す。`config/local/compose.yaml`があれば`make local-up`と`make local-check`で動作も確かめ、終わったら`make local-down`する。確かめられなかったことはcommit本文に書く。

## 終わり方

最後の回答は日本語で、次を短く並べる。

1. commitごとに: hash、対象route・SQL、期待効果、リスク、確認したこと
2. 実装しなかった候補（非同期化などkickoffの範囲外のもの、索引やN+1で解くべきものを含む）と、その理由・根拠の`file:line`

