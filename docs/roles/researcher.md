# researcher

役割: researcher。`SCAFFOLD_ROLE=researcher`。operatorの現在の課題・問いから探索を深め、具体的な改善仮説と根拠・成立条件・未確認点を提案する。解法や探索角度は固定しない。採否・優先順位・追加調査・workerへの実装依頼はoperatorが判断する。scoutの独立した定期探索は変更しない。

共有アプリ・設定の変更、remote操作、deploy、ベンチは担当しない。自身の調査成果物・提案・実行状態は保存できる。練習ルールはAGENTS.mdと[起動プロンプト](researcher-prompt.txt)に従う。禁止資料を自動収集しない。非対話起動では、調査結果のJSONをscaffoldが保存・正式公開する。途中のCLI出力やメモは提案として扱わない。指示はプロンプトによる契約で、OS sandboxの保証ではない。

## 操作

main worktreeのrootで操作する。Python・SQLiteと既存operationsのCLI adapterを使う。定常運用の開始・Phase移行は開発完了と別で、人間が決める。以下のrequest/publish/start/retryは外部モデルを起動し得るため、実接続検証は人間の確認後に行う。

```bash
# 調べてほしい内容をそのまま渡す（問いを共有し、解法は固定しない）
./scripts/researcher request '一覧APIが遅い原因と、改善できそうな箇所を調べて'
# 手動調査した提案の正式公開（必須JSON形式は起動プロンプトを参照）
./scripts/researcher publish .local/proposal.json
./scripts/researcher status
make board
# 調査・レビュー共通サービスの停止（公開済み成果物は保持）
make researcher-stop
# 起動/再起動。未レビューを再開。失敗済みは自動再試行しない
make researcher-start
# statusの失敗job_idを指定。到着済みの相手レビューは再実行しない
./scripts/researcher retry '<job_id>'
./scripts/researcher export
```

requestの引数は依頼文そのもの。人間がoperatorへ「こういう内容を調べて」と伝え、operatorがその問いを渡す。ファイルの作成は不要。長文は`request -`で標準入力、保存済み依頼文は`request --file <path>`でも渡せる。requestは調査依頼IDを返す。publishは提案ID・版・SHA256を返し、SQLiteへの公開と両レビューの待ち行列登録を同じtransactionで確定する。その後ボードへ出力し非同期サービスを起動する。モデル応答を待たずCLIは戻る。起動失敗でも公開済み提案はstatusから即時に読め、startで未レビューを再開できる。明示stop後はrequest/publish/retryしても起動せず待ち行列に保存し、startを待つ。

Codex・Claude・researcherに各1つの独立した実行枠があり、一方の遅延・失敗は他方の開始を妨げない。同じprovider内は公開順に処理する。重複通知は同じID・版・内容なら同一公開として扱い、異なる本文は拒否する。改訂は同じproposal_idでrevisionを増やし、改訂版にも両レビューを作る。原版の内容・結果は保持する。

状態はpending（未レビュー）、running（処理中）、complete（完了）、failed（失敗）。statusとボードに失敗理由を表示する。timeout・不正JSON・対象hash不一致・成果物欠落は失敗。stopは進行中CLIを終了し失敗として残す。クラッシュした親プロセスのCLIは既存process_guardで終了する。statusは中断を検出して失敗表示し、start/retry/stopはその状態をDBへ確定する。retryは失敗jobだけを受け付け、試行IDを新しくして履歴を保持する。

## 保存と対応関係

正本はmainの`.local/operations/state.sqlite3`。提案は公開時のJSON本文・hash・元の問い・対象commit・run_ids・根拠の参照先とsnapshotを固定する。対象commitは40桁で、参照runなしは空配列。不明点も必須欄に不明と記載する。snapshotは観測した抜粋を残し、未コミット内容はdirtyであることを明記する。参照元自体が後から更新されても公開時の内容は読み取れる。根拠全体の自動コピーや妥当性保証はしない。秘密情報・禁止資料を入れない。

`docs/research/proposals/<ID>/<revision>/proposal.json`と到着した各reviewの試行別JSON、`docs/scout-board.md`の概要・状態・参照を通常のコミットに含める。自動commitはしない。exportでDBから復元できる。DB commit後にexportが失敗した場合も提案はstatusで読めるため、exportを再実行する。同じ版の変更や完了レビューの上書き再試行はしない。SQLiteとGit成果物をバックアップし、停止のために削除しない。

実行ログ・入力プロンプト・PID・exit・実セッション情報は`.local/operations/research/<attempt_id>/`。`research_attempts`でjobと試行を紐付ける。CLI起動・終了と成果物保存は区別し、workerの作業状態は変更しない。operatorセッション登録・scout入力選別にも介入しない。

SQLiteの短いtransactionで二重取得を防止する。別の担当lockやleaseは追加しない。`.local/operation.lock`を使うremote変更系操作は呼ばず、外部応答待ちにDB transaction・共通操作lockを保持しない。既存ボードexportの短い排他だけを再利用する。

## 別担当との接続

`config/operations.json`の`research.researcher`にOpenCode＋DSV4.1、`research.reviewers.codex/claude`に各argv・model・output、`research.timeout_seconds`に上限を設定する。既存scout設定から独立している。端末専用の`.local/operations.json`は全体上書きなので、既存ファイルにもresearchブロックを追加する。設定変更後はstop/startする。

argvはshellでなく配列。`{model}`、`{output}`、`{session}`を置換しプロンプトをstdinへ渡す。resume/continueは使わない。既存のfile/text/codex-json/claude-json/opencode-json adapterを再利用する。モデルID・認証・実行環境の構築や実機検証は別担当。設定済みIDは既存設定からの接続候補で、この追加機能での実接続成功は未確認。外部送信の検証は人間へ事前確認する。

子CLIへ`SCAFFOLD_ROLE=researcher|reviewer`、`SCAFFOLD_AGENT=opencode|codex|claude`、`SCAFFOLD_RESEARCH_JOB_ID`、`SCAFFOLD_INVOCATION_ID`を渡す。operator/workerのセッションID、親ID、task ID、CODEX_THREAD_ID、CLAUDECODEは継承しない。INVOCATION_IDは実セッションIDではない。実IDはadapterのmodel.jsonで確認する。履歴側は新しいroleを区別し、operatorとして自動登録しない。worker-db/fork側の契約は変更しない。時間計測整備、モデル比較、文脈forkから統合までの通し確認は今回の対象外。

## 今回の検証結果（2026-10-01）

偽CLI・一時Gitリポジトリで既存operations 16件とresearcher追加11件が成功。`make operations-test`は両方を実行する。公開前後の状態、両providerの独立進行、遅延中の片側結果の閲覧、片側/両側失敗、起動/出力失敗時の提案保持、版固定、重複通知・多重起動・atomic claim、停止・SIGKILL・CLI子プロセス終了・再起動・明示retry、成果物欠落/hash不一致/timeout、researcherから両レビューまでの接続、HTTPボードの提案表示を検証した。既存のworker作業/プロセス記録の分離・scout入力選別・定期実行・ボードの回帰テストも成功。

`bash tests/initial-automation.sh`のfixture検証、Python構文検査、ボードJavaScript構文検査も成功。これはモック検証であり、実CLIから外部モデルへの送信、実計測runを使った調査品質、実ブラウザでの目視は未検証。実行環境検証担当は人間の確認を得て別途接続を確認する。定常運用・Phase移行は開始していない。
