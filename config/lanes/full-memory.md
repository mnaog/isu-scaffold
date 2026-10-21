# lane: full-memory（全メモリ化）

あなたは、アプリが持つ状態をすべてメモリに置く書き換え（全メモリ化）を、このbranchで作る。読み取りと状態の判定はメモリで行い、書き込みはDBに残す。このlaneは、`make kickoff`がコードを先行回収した直後に、lane `state-log`・`local-env`・`indexes`・`n-plus-one`・`obvious-fixes`・`runtime`と同時に始まる。序盤の点の大半はDBの読み取りで決まり、このlaneがそれを解くので、早く終えることに価値がある。mainのセッションはその間に環境構築と初回ベンチを進め、improveは全メモリ化が書き換えない所（設定・node分割など）を改善する。このlaneは、`state-log`と`local-env`が終わったら自分で取り込んで確かめ、最後に最新のmainを1度だけ取り込んでから終わる。mainはこのbranchをmainへmergeしてdeployし、PASSしてスコアが上がれば残す。価値は、応答と永続性を変えずに、DBへの読み取りとDBでの判定をなくすこと。

## 前提

- 作業場所はこのworktreeとbranchだけ。`AGENTS.md`の規則に従う（練習中で`practice.md`があればそれにも従う）。remote node・deploy・ベンチ・isuscopeの記録操作・`.local/operation.lock`には触れない（hookが止める）。
- このbranchは、先行回収したコードのcommitから分かれている。lane `state-log`（状態ログの呼び出し）と`local-env`（ローカル環境）は同時に動いていて、まだこのbranchに無い。取り込む時機は「確かめる」にある。終わった後に`docs/state-log.md`か`config/local/compose.yaml`が無ければ、そのlaneが失敗している。確かめられない範囲を最後の回答に書いて進める。
- `indexes`・`n-plus-one`・`obvious-fixes`・`runtime`も同時に動いており、mainが取り込んだ分は「最新のmainを取り込む」で1度だけ受け取る。N+1や読み取りのSQLの修正は、全メモリ化で読み取りがstoreへ移れば要らなくなるので、衝突したらstoreの形を残す。
- 読むもの: `docs/official/`（あれば。特に、更新の即時反映、整合性の検査、再起動の後に求められること）、`docs/benchmark-scenario.md`（あれば）、状態ログの書き手`config/state-log/state_log.rs`（lane `state-log`がこれを写す）。`docs/state-log.md`と`config/local/compose.yaml`の冒頭のコメント（ローカルで動くもの・動かないもの）は、取り込んだ後に読む。
- 変えてよいもの: `webapp/`のアプリのコード、`docs/cache-design.md`、`config/local/flows/`（確かめるための操作の列）。schema、他の`config/`・`scripts/`・文書は変えない（mainを取り込んで入ってくる変更は別）。mainはこのlaneの間も別の改善を続けるので、変更はメモリ化に要る範囲に留め、衝突を小さくする。

## 作るもの

1. **store。** アプリが読むtableの行（または応答に要る形）をメモリに持つ型を作る。起動したときと、initializeの後に、DBから全部読み込む。initializeでは古いメモリを捨ててから読み込み、読み込みが終わるまで他のリクエストに古い状態を見せない。
2. **変更を確定する箇所を1つにする。** すべての書き込みはstoreの関数を通す。その関数は、(1) DBへ書く、(2) commitが成功したらメモリを変える、(3) 状態ログを書く、の順に行う。状態ログは、前の状態がメモリにあるので`state_log::changed`（作るときは`created`）で書き、`entered`は使わない。lane `state-log`が入れた呼び出しは、取り込むとき（「確かめる」の4）にこの関数へ移す。取り込むまでは、状態ログを書く場所だけを関数に用意しておく。handlerがDBへ直接書く経路を残さない。
3. **読み取りはすべてstoreから。** DBへのSELECTは、起動時とinitializeの読み込みだけにする。
4. **同じ対象への同時の変更。** DBのtransactionや行lockで守っていた検査（重複、一意性、残高や在庫、状態の前提）は、storeのlockの中で同じ検査をしてからDBへ書く。lockは対象の単位を基本にし、全体を1つのlockで覆うなら理由を書く。lockを持ったままDBを待つ範囲は小さくする。
5. **書き込みは同期のまま。** 応答はDBのcommitの後に返す。同時に来た複数のリクエストの行を1つの文にまとめてcommitしてよい（各リクエストは自分の行のcommitを待って応答する）。後で書くこと（write-behind）、WAL、DBの退役はしない。

## 守ること

- **応答を変えない。** 返す値・件数・並び順・エラーとstatus codeを元のコードと一致させる。並び順はSQLのORDER BY（無ければ元の実行計画の順。主キー順など）を、メモリの上で同じにする。時刻の丸めや型（DBが返していた精度）も合わせる。
- **再起動しても同じ。** プロセスを止めて起動し直すと、DBから読み直して同じ状態になること。
- **書いた直後に読める。** 公式資料が即時反映を求める経路では、書いた直後の読み取りが新しい値を返すこと。
- **メモリの量。** 初期データとベンチ1回で増える分を見積もり、`docs/cache-design.md`に書く。

## 確かめる

`state-log`と`local-env`はこのlaneと同時に始まり、ふつうは書き換えより先に終わる。書き換えを先に進め、確かめる段で取り込む。

1. **書き換える前に、** 代表的な流れを最初から最後まで決まった順に進める操作の列を、`config/local/flows/`にJSONで書く（形は`scripts/local-flow.py`の冒頭）。initializeから始め、得点につながる流れと状態の変化をひととおり通す。途中で止めて再起動を確かめる用に、前半と後半の2つのfileに分けたものも作る。3でcommitの無い版へ切り替えるので、これらのfileは3が済むまでcommitしない。
2. **書き換えて、**`cargo build --release`と既存のtestを通し、commitする。
3. **書き換える前の版で状態ログを取る。** `python3 scripts/lane.py status state-log`と`local-env`の`state`を見る。終わっていなければ`docs/cache-design.md`を書くなど残りを先に進めてから見直し、それでも終わっていなければ、その時点のbranchを使って確かめられなかった範囲を最後の回答に書く（`local-env`が失敗して`config/local/compose.yaml`が無ければ、3・5・6を飛ばして4の取り込みだけ行う）。そろったら:
   - `git switch --detach <分岐元commit>`、`git merge --no-edit lane/state-log lane/local-env`（このmergeは使い捨て）
   - `make local-up`、`make local-flow FLOW=config/local/flows/<流れ>.json`、`make local-state-log NAME=before`。状態ログが空なら、流れが状態を変えていないか、アプリが書いていない
   - `git switch <このbranch>`で戻り、1のfileをcommitする
4. **取り込む。** `git merge --no-edit lane/state-log lane/local-env`。`state-log`が入れた呼び出しは、変更を確定する関数へ移す（「作るもの」の2）。buildとtestを通してcommitする。
5. **書き換えた版で、** `make local-up`し、同じ流れを流して`make local-state-log NAME=after`。`python3 scripts/state-log-compare.py .local/local-runtime/state-log/before.jsonl .local/local-runtime/state-log/after.jsonl`で比べ、種類ごとに同じ数の対象が同じ順番で状態を通ること。違えば直す。直さない違いは、理由を`docs/cache-design.md`に書いて`--ignore-kind`で外す。
6. **再起動。** 前半の流れを流し、`make local-up`（アプリを作り直す。DBは残る）してから、`make local-flow FLOW=<後半> FROM=<前半>`で続きが通ること。

5と6は、「最新のmainを取り込む」の後にもう一度通す（`before`は取り直さない）。終わったら`make local-down`する。

## 最新のmainを取り込む（1度だけ）

mainはこのlaneの間に、laneの索引・修正とimproveの投稿を取り込んでいる。取り込まずに終えると、mainはこのbranchを入れるときに大きな衝突を抱える。一方で、mainはこの間も取り込みを止めないので、追いかけ続けると終わらない。取り込みは1度だけにする。その後にmainへ入った変更で全メモリ化の形へ移す必要があるものは、mainが戻してimproveへ作り直しを頼む。分岐元branchは冒頭に書かれている（以下`<分岐元>`）。

1. 書き換えを確かめてcommitし終えたら、`git log --oneline HEAD..<分岐元>`でmainの新しいcommitを見る。無ければ5へ進む。取り込む直前の`<分岐元>`のhashを控える（`git rev-parse <分岐元>`）。
2. `git merge --no-edit <分岐元>`で取り込む。衝突は両方の意図を残して解く。mainの変更をこのbranchの形に合わせる:
   - mainが足した・変えたSELECTは、storeからの読み取りにする。
   - mainが足した・変えた書き込みは、変更を確定する関数を通す。DBへ直接書く経路を残さない。
   - mainが入れたcacheやメモリ化（`docs/cache-design.md`に書かれている）は、storeへまとめる。同じ状態をmainのcacheとstoreに二重に持たない。
   - 索引（schema）の変更はそのまま受け取る。
   衝突しなかった箇所も、`git diff <マージ前のHEAD> HEAD -- webapp/`でmain側の変更を読み、上の3つに当てはまるものを直す。`git merge --abort`でやり直してよい。
3. 解き終えたらmerge commitを作り、上の直しを別のcommitにする。
4. 「確かめる」の5と6をもう一度通し、buildとtestも通す。状態ログの`before`は取り込む前のmainで取ったので、mainの変更が状態の変化を変えた場合は差が出る。差がmainのcommitによるものか（`git log -p <分岐元commit>..<控えたhash>`で取り込んだ変更を読む）を確かめ、そうなら理由を`docs/cache-design.md`に書く。メモリ化による差なら直す。
5. ここで終える。この後に`<分岐元>`へ新しいcommitが入っていても、もう取り込まない。控えたhashを最後の回答に書く（mainはそれより後の変更を戻してimproveへ返す）。

`make local-*`は環境変数を前に付けずにそのまま呼ぶ（付けた形はlaneの権限で止められる）。

## docs/cache-design.md

`config/records.json`の「メモリ化の設計」の定義どおり、正本（DB）、storeに持つものと持たないもの、変更を確定する関数（`file:line`）、lockの範囲、起動時とinitialize後の読み込み、見積もったメモリの量を書く。流した流れ、比較と再起動の結果、確かめられなかったことも書く。

## commit

確かめられる単位で数個に分ける（例: 確かめるための流れ、storeと読み込み、読み取りの切り替え、書き込みの集約、文書）。途中のcommitでもbuildとtestを通す。mainはこのbranchを最後のcommitまでまとめてmergeする前提でよい。commit messageは英語で、本文に確かめたことを書く。

## 終わり方

最後の回答は日本語で、次を短く並べる。

1. commitごとに: hash、変えたこと。取り込んだmainのcommit（取り込んだ分岐元のhash。mainはこれより後の変更を戻す）と、衝突を解いた箇所
2. メモリに持ったものと持たなかったもの、変更を確定する関数
3. 状態ログの比較と再起動の結果、確かめられなかったこと
4. mainが取り込んで測るときに気をつけること（lockが粗い所、initializeにかかる時間、メモリの量、後からmainへ入った変更を作り直すときに当てはめる規則：SELECTはstoreから、書き込みは変更を確定する関数を通す、cacheはstoreへまとめる）
