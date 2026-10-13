# lane: obvious-fixes

あなたは`make kickoff`が先行回収したアプリに、`docs/phases/Phase1.md`「自明なボトルネックを一掃する」の修正を先行して実装する。mainのセッションは同時に環境構築と初回ベンチ（baseline）を進め、baselineの観測と照合してから、このbranchのcommitを選んで取り込み、deployして測る。速さが価値なので、根拠が明確で挙動を変えないものから順に、小さく確実に入れる。

## 前提

- 作業場所はこのworktreeとbranchだけ。`AGENTS.md`と`practice.md`の規則に従う。remote node・deploy・ベンチ・isuscopeの記録操作・`.local/operation.lock`には触れない（hookが止める）。baseline前の変更をremoteへ反映しない規則は、mainが取り込み時に守る。
- 変更してよいのは`webapp/`と`docs/indexes.md`だけ。`config/`、`scripts/`、他の文書は変えない。

## 対象

- WHERE、JOIN、ORDER BYに対する明らかなインデックス不足
- ループ内SQLや要素ごとの更新などのN+1
- 同一request内の重複query・重複計算
- 単一行のカウンタや必要以上のlocking readによる直列化
- 一括INSERT・UPDATE・UPSERTに置き換えられる逐次write
- transaction内の不要な処理とDB往復
- 明らかに不要なカラム、全件走査、外部呼び出し

キャッシュ・メモリ化、非同期化、write-behind、台の分離、整合性や永続化の時機を変える変更は実装しない。見つけたら最後の回答に候補として書く。

## 守ること

- **応答を変えない。** 返す値・件数・並び順・エラーとstatus codeを元のコードと一致させる。ORDER BYの無いSQLを置き換えるときは、元の実行計画で返っていた順序（主キー順など）を保つか、変えない。迷ったら入れずに候補へ回す。
- 索引はinitializeが流すschema（`webapp/sql`）へ入れる。initializeの所要時間を大きく延ばさないかも確かめる。`docs/indexes.md`には対象のSQL、`初期化後の行数: 未確認`、`効果を確かめたrun ID: 未測定`の形で残す。
- **1つの修正を1つのcommitにする。** mainが選んで取り込めるように、commit同士の依存を避ける（索引は索引だけ、N+1はhandlerごと）。commit messageは英語で、1行目に変更、本文に直した問題（`file:line`）と期待する観測値（どのrouteやSQLの何が減るか）を書く。
- commitの前に、変更したcrateで`cargo build --release`（または`cargo check`）と既存のtestを通す。`config/local/compose.yaml`があれば`make local-up`と`make local-check`で動作も確かめ、終わったら`make local-down`する。確かめられなかったことはcommit本文に書く。

## 終わり方

最後の回答は日本語で、次を短く並べる。

1. commitごとに: hash、対象route・SQL、期待効果、リスク、確認したこと
2. 実装しなかった候補（Phase 1の範囲外のものを含む）と、その理由・根拠の`file:line`

mainのセッションは`git log lane/obvious-fixes`とこの回答を読み、baselineの観測と照合して取り込む。
