# lane: full-memory（全メモリ化）

あなたは、アプリが持つ状態をすべてメモリに置く書き換え（全メモリ化）を、このbranchで作る。読み取りと状態の判定はメモリで行い、書き込みはDBに残す。このlaneは、lane `state-log`・`local-env`・`n-plus-one`が終わると、そのbranchをmergeして自動で始まる。mainのセッションはその間も別の改善を続ける。このlaneは最後に最新のmainを1度だけ取り込んでから終わる。mainはこのbranchをmainへmergeし、その後にmainへ入ったcommitを自分で全メモリ化の形へ移してからdeployし、PASSしてスコアが上がれば残す。価値は、応答と永続性を変えずに、DBへの読み取りとDBでの判定をなくすこと。

## 前提

- 作業場所はこのworktreeとbranchだけ。`AGENTS.md`の規則に従う（練習中で`practice.md`があればそれにも従う）。remote node・deploy・ベンチ・isuscopeの記録操作・`.local/operation.lock`には触れない（hookが止める）。
- このbranchはmainのbranchから分かれ、始める前に`scripts/lane.py`がlane `state-log`（状態ログの呼び出し）、`local-env`（ローカル環境）、`n-plus-one`（N+1の修正）のbranchをmergeしている。衝突してmergeできなかったbranchがあれば、冒頭にそう書かれているので、最初に自分でmergeして解く（`state-log`と`n-plus-one`は同じ関数を変えることがある。両方の行を残す）。`docs/state-log.md`か`config/local/compose.yaml`が無ければ、そのlaneが失敗している。確かめられない範囲を最後の回答に書いて進める。索引やN+1以外の自明な修正はまだmainに入っていないことがあり、作業中もmainは改善を続ける。それらは「最新のmainを取り込む」で1度だけ受け取る。
- 読むもの: `docs/official/`（特に、更新の即時反映、整合性の検査、再起動の後に求められること）、`docs/state-log.md`、`docs/benchmark-scenario.md`、`config/local/compose.yaml`の冒頭のコメント（ローカルで動くもの・動かないもの）。
- 変えてよいもの: `webapp/`のアプリのコード、`docs/cache-design.md`、`config/local/flows/`（確かめるための操作の列）。schema、他の`config/`・`scripts/`・文書は変えない（mainを取り込んで入ってくる変更は別）。mainはこのlaneの間も別の改善を続けるので、変更はメモリ化に要る範囲に留め、衝突を小さくする。

## 作るもの

1. **store。** アプリが読むtableの行（または応答に要る形）をメモリに持つ型を作る。起動したときと、initializeの後に、DBから全部読み込む。initializeでは古いメモリを捨ててから読み込み、読み込みが終わるまで他のリクエストに古い状態を見せない。
2. **変更を確定する箇所を1つにする。** すべての書き込みはstoreの関数を通す。その関数は、(1) DBへ書く、(2) commitが成功したらメモリを変える、(3) 状態ログを書く、の順に行う。状態ログは、前の状態がメモリにあるので`state_log::changed`（作るときは`created`）で書き、`entered`は使わない。lane `state-log`が入れた呼び出しは、この関数へ移す。handlerがDBへ直接書く経路を残さない。
3. **読み取りはすべてstoreから。** DBへのSELECTは、起動時とinitializeの読み込みだけにする。
4. **同じ対象への同時の変更。** DBのtransactionや行lockで守っていた検査（重複、一意性、残高や在庫、状態の前提）は、storeのlockの中で同じ検査をしてからDBへ書く。lockは対象の単位を基本にし、全体を1つのlockで覆うなら理由を書く。lockを持ったままDBを待つ範囲は小さくする。
5. **書き込みは同期のまま。** 応答はDBのcommitの後に返す。同時に来た複数のリクエストの行を1つの文にまとめてcommitしてよい（各リクエストは自分の行のcommitを待って応答する）。後で書くこと（write-behind）、WAL、DBの退役はしない。

## 守ること

- **応答を変えない。** 返す値・件数・並び順・エラーとstatus codeを元のコードと一致させる。並び順はSQLのORDER BY（無ければ元の実行計画の順。主キー順など）を、メモリの上で同じにする。時刻の丸めや型（DBが返していた精度）も合わせる。
- **再起動しても同じ。** プロセスを止めて起動し直すと、DBから読み直して同じ状態になること。
- **書いた直後に読める。** 公式資料が即時反映を求める経路では、書いた直後の読み取りが新しい値を返すこと。
- **メモリの量。** 初期データとベンチ1回で増える分を見積もり、`docs/cache-design.md`に書く。

## 確かめる

1. **書き換える前に、** 代表的な流れを最初から最後まで決まった順に進める操作の列を、`config/local/flows/`にJSONで書く（形は`scripts/local-flow.py`の冒頭）。initializeから始め、得点につながる流れと状態の変化をひととおり通す。途中で止めて再起動を確かめる用に、前半と後半の2つのfileに分けたものも作る。
2. `make local-up`し、今の版に流して状態ログを取る: `make local-flow FLOW=config/local/flows/<流れ>.json`、`make local-state-log NAME=before`。状態ログが空なら、流れが状態を変えていないか、アプリが書いていない。
3. **書き換えた版で、** `make local-up`し、同じ流れを流して`make local-state-log NAME=after`。`python3 scripts/state-log-compare.py .local/local-runtime/state-log/before.jsonl .local/local-runtime/state-log/after.jsonl`で比べ、種類ごとに同じ数の対象が同じ順番で状態を通ること。違えば直す。直さない違いは、理由を`docs/cache-design.md`に書いて`--ignore-kind`で外す。
4. **再起動。** 前半の流れを流し、`make local-up`（アプリを作り直す。DBは残る）してから、`make local-flow FLOW=<後半> FROM=<前半>`で続きが通ること。
5. `cargo build --release`と既存のtestを通す。

この1〜5は、書き換えた版で一度通してから、「最新のmainを取り込む」の後にもう一度通す。終わったら`make local-down`する。

## 最新のmainを取り込む（1度だけ）

mainはこのlaneの間に索引・修正・メモリ化を入れている。取り込まずに終えると、mainはこのbranchを入れるときに大きな衝突を解くことになる。一方で、mainはこの間も改善を止めないので、追いかけ続けると終わらない。取り込みは1度だけにし、その後にmainへ入ったcommitはmainが自分で移す。分岐元branchは冒頭に書かれている（以下`<分岐元>`）。

1. 書き換えを確かめてcommitし終えたら、`git log --oneline HEAD..<分岐元>`でmainの新しいcommitを見る。無ければ5へ進む。取り込む直前の`<分岐元>`のhashを控える（`git rev-parse <分岐元>`）。
2. `git merge --no-edit <分岐元>`で取り込む。衝突は両方の意図を残して解く。mainの変更をこのbranchの形に合わせる:
   - mainが足した・変えたSELECTは、storeからの読み取りにする。
   - mainが足した・変えた書き込みは、変更を確定する関数を通す。DBへ直接書く経路を残さない。
   - mainが入れたcacheやメモリ化（`docs/cache-design.md`に書かれている）は、storeへまとめる。同じ状態をmainのcacheとstoreに二重に持たない。
   - 索引（schema）の変更はそのまま受け取る。
   衝突しなかった箇所も、`git diff <マージ前のHEAD> HEAD -- webapp/`でmain側の変更を読み、上の3つに当てはまるものを直す。`git merge --abort`でやり直してよい。
3. 解き終えたらmerge commitを作り、上の直しを別のcommitにする。
4. 「確かめる」の1〜5をもう一度通す。状態ログの`before`は取り込む前のmainで取ったので、mainの変更が状態の変化を変えた場合は差が出る。差がmainのcommitによるものか（`git log -p <分岐元commit>..<控えたhash>`で取り込んだ変更を読む）を確かめ、そうなら理由を`docs/cache-design.md`に書く。メモリ化による差なら直す。
5. ここで終える。この後に`<分岐元>`へ新しいcommitが入っていても、もう取り込まない。控えたhashを最後の回答に書く（mainはそれより後のcommitを自分で移す）。

`make local-*`は環境変数を前に付けずにそのまま呼ぶ（付けた形はlaneの権限で止められる）。

## docs/cache-design.md

`config/records.json`の「メモリ化の設計」の定義どおり、正本（DB）、storeに持つものと持たないもの、変更を確定する関数（`file:line`）、lockの範囲、起動時とinitialize後の読み込み、見積もったメモリの量を書く。流した流れ、比較と再起動の結果、確かめられなかったことも書く。

## commit

確かめられる単位で数個に分ける（例: 確かめるための流れ、storeと読み込み、読み取りの切り替え、書き込みの集約、文書）。途中のcommitでもbuildとtestを通す。mainはこのbranchを最後のcommitまでまとめてmergeする前提でよい。commit messageは英語で、本文に確かめたことを書く。

## 終わり方

最後の回答は日本語で、次を短く並べる。

1. commitごとに: hash、変えたこと。取り込んだmainのcommit（取り込んだ分岐元のhash。mainはこれより後のcommitを移す）と、衝突を解いた箇所
2. メモリに持ったものと持たなかったもの、変更を確定する関数
3. 状態ログの比較と再起動の結果、確かめられなかったこと
4. mainが取り込んで測るときに気をつけること（lockが粗い所、initializeにかかる時間、メモリの量、mainが後から入れたcommitを移すときに当てはめる規則：SELECTはstoreから、書き込みは変更を確定する関数を通す、cacheはstoreへまとめる）
