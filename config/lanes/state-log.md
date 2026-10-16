# lane: state-log

あなたは`make kickoff`が先行回収したアプリに、isuscopeの状態ログを入れる。状態ログは、アプリの中の対象（注文、予約、配送など）が状態を変えるたびに書く1行のJSONで、isuscopeが毎回のrunで運ぶ。mainのセッションはbaselineの後の最初のdeployでこのbranchを取り込み、以後のrunごとに`isuscope brief`の`states`欄、`isuscope states sql`・`states timeline`で、状態にいた時間、待っている対象と空いている対象の数、ある対象の前後の変化を、計測を足すrunなしで読む。価値は、挙動と速さを変えずに、状態の変化を漏れなく書くこと。

## 前提

- 作業場所はこのworktreeとbranchだけ。`AGENTS.md`の規則に従う（練習中で`practice.md`があればそれにも従う）。remote node・deploy・ベンチ・isuscopeの記録操作・`.local/operation.lock`には触れない（hookが止める）。baseline前の変更をremoteへ反映しない規則は、mainが取り込み時に守る。
- 変更してよいのは`webapp/`のアプリのコードと`docs/state-log.md`だけ。schema（`webapp/sql`）、`config/`、`scripts/`、他の文書は変えない。
- lane `indexes`・`n-plus-one`・`obvious-fixes`が同時に同じコードを変える。mergeで衝突しにくいよう、変更は書き手のmoduleの追加と呼び出しの追加だけにし、周りの行を書き換えない（整形し直さない、`cargo fmt`をcrate全体へかけない）。

## 書き手

- `config/state-log/state_log.rs`をcrateの`src/`へそのまま写し（`cp config/state-log/state_log.rs webapp/rust/src/state_log.rs`）、crateのroot（`main.rs`か`lib.rs`）に`mod state_log;`を1行足す。中身は変えない。追加のcrateは要らない。
- 使い方はfile冒頭のコメントのとおり。`state_log::created`（作られた）、`state_log::changed`（前の状態から変わった）、`state_log::entered`（前の状態を知らずに変えた）、transactionの中なら`state_log::Pending`へためてcommitの後に`emit()`。
- 書き手はリクエストを待たせない（queueへ入れるだけで、あふれたら捨てて数える）。書き先は`ISUSCOPE_STATE_LOG`（無ければ`/var/tmp/isuscope-state.jsonl`、空なら書かない）。

## 手順

1. **状態を持つ対象を探す。** 状態を表す列（status、state、phaseなど）や状態ごとの表を持つtable、それを変えるSQL（`UPDATE ... SET status`、状態の履歴表へのINSERT、行の作成）と、メモリに持つ状態を変える処理。対象の種類（kind。tableの単数形など）と状態の名前を、アプリが使う値のまま書き出す。決まった値をとる状態だけを対象にし、座標・残高・件数のように値が連続する、または回数の多い更新は対象にしない。
2. **状態を変える箇所すべてに呼び出しを入れる。** 作る箇所、変える箇所、消す箇所（消えた状態として`to`に書く）。同じ変化を2か所で書かない。
   - 前の状態がコードの中で分かっていれば（条件で確かめた、直前に読んだ）`changed`。分からなければ`entered`にする。**前の状態を知るためにSQLや処理を足さない。** isuscopeが前の行から補う。
   - DBのtransactionの中で変えるなら、`Pending`へためて、commitが成功した後に`emit()`する。commitに失敗した変化を書かないため。
   - `attrs`には、関係する対象のID（別のkindの対象。`("order", order_id.into())`のように、そのkindの名前をkeyにする）と、集計に使いそうな数値（距離、金額、件数など）を少しだけ入れる。入れ子にしない。新しく計算しない（手元にある値だけ）。
   - initializeの処理（データの入れ直し）では書かない。初期データの対象は、最初に変わった行の`from`で状態が分かる。
3. **応答と速さを変えない。** 返す値・並び順・status code・SQLの回数を変えない。呼び出しはlockを持つ範囲の外へ置ける場合は外へ置く。
4. **確かめる。** `cargo build --release`と既存のtestを通す。`config/local/compose.yaml`があれば`make local-up`で動かし、`ISUSCOPE_STATE_LOG`を確認できる場所へ向けて、代表的な操作で1行以上出ることと形を確かめ、`make local-down`する。確かめられなかったことは`docs/state-log.md`とcommit本文に書く。
5. **`docs/state-log.md`を書く。** 対象の種類ごとに、状態の一覧、遷移と書いている箇所（関数と`file:line`、`changed`・`entered`・`created`のどれか）、`attrs`のkey。書かなかった状態の変化と理由（前の状態が分からず`entered`にした箇所も）。mainと、後で状態の処理を書き換える人が、呼び出しを残すための表にする。

## commit

1. 書き手のmoduleの追加（`state_log.rs`と`mod state_log;`の1行）。
2. 呼び出しの追加と`docs/state-log.md`。

commit messageは英語で、1行目に変更、本文に対象の種類と状態、確かめたこと。

## 終わり方

最後の回答は日本語で、次を短く並べる。

1. commitごとに: hash、変えたfile、確かめたこと
2. 対象の種類ごとの状態と遷移の一覧（`entered`にした箇所はそう書く）
3. 書かなかった状態の変化と理由、mainが取り込み時に気をつけること（lockの中に残した呼び出し、他のlaneと触る関数が重なりそうな箇所）

mainのセッションは`git log lane/state-log`とこの回答を読み、baselineの後の最初のdeployで、他のlaneのcommitより先に取り込む。
