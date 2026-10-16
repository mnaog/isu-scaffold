# lane: obvious-fixes

あなたは`make kickoff`が先行回収したアプリに、`docs/guides/kickoff.md`「自明なボトルネックを一掃する」の修正を、索引とN+1以外について先行して実装する。索引の追加はlane `indexes`が、N+1はlane `n-plus-one`が同時に行う。mainのセッションは同時に環境構築と初回ベンチ（baseline）を進め、baselineの観測と照合してから、このbranchのcommitを選んで取り込み、deployして測る。速さが価値なので、根拠が明確で挙動を変えないものから順に、小さく確実に入れる。

## 前提

- 作業場所はこのworktreeとbranchだけ。`AGENTS.md`の規則に従う（練習中で`practice.md`があればそれにも従う）。remote node・deploy・ベンチ・isuscopeの記録操作・`.local/operation.lock`には触れない（hookが止める）。baseline前の変更をremoteへ反映しない規則は、mainが取り込み時に守る。
- 変更してよいのは`webapp/`のアプリのコードだけ。schema（`webapp/sql`）、`config/`、`scripts/`、他の文書は変えない（索引はlane `indexes`の担当で、同じfileを触るとmergeで衝突する）。
- lane `n-plus-one`が同じhandlerのループ内SQLを変える。mergeで衝突しにくいよう、直すのに要る行だけを変え、周りを書き換えない（整形し直さない、`cargo fmt`をcrate全体へかけない）。lane `full-memory`（全メモリ化）はN+1の後に始まり、このlaneの終わりは待たない。mainはこのlaneのcommitを全メモリ化と並行して取り込む。

## 対象

- 同一request内の重複計算（同じqueryの読み直しはlane `n-plus-one`の担当）
- 単一行のカウンタや必要以上のlocking readによる直列化
- 一括INSERT・UPDATE・UPSERTに置き換えられる逐次write
- transaction内の不要な処理とDB往復
- 明らかに不要なカラム、全件走査、外部呼び出し

キャッシュ・メモリ化、非同期化、write-behind、台の分離、整合性や永続化の時機を変える変更は実装しない。見つけたら最後の回答に候補として書く。

## 守ること

- **応答を変えない。** 返す値・件数・並び順・エラーとstatus codeを元のコードと一致させる。ORDER BYの無いSQLを置き換えるときは、元の実行計画で返っていた順序（主キー順など）を保つか、変えない。迷ったら入れずに候補へ回す。
- **1つの修正を1つのcommitにする。** mainが選んで取り込めるように、commit同士の依存を避ける。mainはlaneの途中でもcommitを1件ずつ取り込むので、影響の大きいものから順にcommitし、1件ごとに完成させる。commit messageは英語で、1行目に変更、本文に直した問題（`file:line`）と期待する観測値（どのrouteやSQLの何が減るか）を書く。
- commitの前に、変更したcrateで`cargo build --release`（または`cargo check`）と既存のtestを通す。`config/local/compose.yaml`があれば`make local-up`と`make local-check`で動作も確かめ、終わったら`make local-down`する。確かめられなかったことはcommit本文に書く。

## 終わり方

最後の回答は日本語で、次を短く並べる。

1. commitごとに: hash、対象route・SQL、期待効果、リスク、確認したこと
2. 実装しなかった候補（非同期化などkickoffの範囲外のもの、索引やN+1で解くべきものを含む）と、その理由・根拠の`file:line`

mainのセッションは`git log lane/obvious-fixes`とこの回答を読み、baselineの観測と照合して取り込む。
