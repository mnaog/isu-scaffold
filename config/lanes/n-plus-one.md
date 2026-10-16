# lane: n-plus-one

あなたは`make kickoff`が先行回収したアプリのN+1を、先行して直す。索引はlane `indexes`が、N+1以外の自明な修正はlane `obvious-fixes`が同時に行う。このlaneと`state-log`・`local-env`が終わると、lane `full-memory`（全メモリ化）がこのbranchをmergeして自動で始まる。全メモリ化はこのlaneの終わりを待ち、時間の上限（15分）で止められてもそれまでのcommitで始まるので、速さが価値。高頻度の経路から順に、小さく確実に入れ、残りは候補として回答に書いて早く終える。mainのセッションは同時に環境構築と初回ベンチ（baseline）を進め、baselineの後にこのbranchを取り込む。

## 前提

- 作業場所はこのworktreeとbranchだけ。`AGENTS.md`の規則に従う（練習中で`practice.md`があればそれにも従う）。remote node・deploy・ベンチ・isuscopeの記録操作・`.local/operation.lock`には触れない（hookが止める）。baseline前の変更をremoteへ反映しない規則は、mainが取り込み時に守る。
- 変更してよいのは`webapp/`のアプリのコードだけ。schema（`webapp/sql`）、`config/`、`scripts/`、他の文書は変えない（索引はlane `indexes`の担当）。
- lane `obvious-fixes`が同じhandlerの別の箇所を変えることがある。mergeで衝突しにくいよう、N+1を直すのに要る行だけを変え、周りを書き換えない（整形し直さない、`cargo fmt`をcrate全体へかけない）。

## 対象

- ループ内SQL：要素ごとのSELECTを、IN・JOINなど1つのqueryにまとめる。要素ごとのUPDATE・INSERTも1つの文にまとめる
- 同一request内で同じ行・同じ集計を読み直す重複query

それ以外（lockingや単一行のカウンタ、transactionの範囲、不要なカラム・全件走査、外部呼び出し）はlane `obvious-fixes`の担当なので触らない。キャッシュ・メモリ化、非同期化、write-behind、整合性や永続化の時機を変える変更は実装しない。見つけたら最後の回答に候補として書く。

## 守ること

- **応答を変えない。** 返す値・件数・並び順・エラーとstatus codeを元のコードと一致させる。IN・JOINにまとめると並び順が変わり得るので、元のループの順（ORDER BYが無ければ元の実行計画の順。主キー順など）をqueryのORDER BYかアプリ側の並べ直しで保つ。迷ったら入れずに候補へ回す。
- **1つのhandlerの修正を1つのcommitにする。** mainがcommitを選んで取り込めるように、commit同士の依存を避ける。呼ばれる回数の多いhandlerから順にcommitし、1件ごとに完成させる。commit messageは英語で、1行目に変更、本文に直した問題（`file:line`）と期待する観測値（どのrouteやSQLの何が減るか）を書く。
- commitの前に、変更したcrateで`cargo build --release`（または`cargo check`）と既存のtestを通す。`config/local/compose.yaml`があれば`make local-up`と`make local-check`で動作も確かめ、終わったら`make local-down`する。確かめられなかったことはcommit本文に書く。

## 終わり方

最後の回答は日本語で、次を短く並べる。

1. commitごとに: hash、対象route・SQL、期待効果、リスク、確認したこと
2. 実装しなかった候補と、その理由・根拠の`file:line`

mainのセッションは`git log lane/n-plus-one`とこの回答を読み、`git merge --no-ff lane/n-plus-one`で取り込む（全メモリ化も同じbranchをmergeしているので、cherry-pickにしない）。
