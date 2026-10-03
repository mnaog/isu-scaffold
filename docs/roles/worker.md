# worker

workerはCodex（GPT-6-Astra、推論medium）に固定する。起動時に役割をworkerと明示し、この文書を読む。`SCAFFOLD_ROLE=worker`も設定する。
Phase 1の初期改善セッションでは[Phase 1の継続運用](../phases/Phase1.md)を優先する。一つのworktreeで探索・修正・検証を続け、途中の受け渡しでは待機せず、タスクを開発完了にも変更しない。以下の1目的・完了後待機は通常workerの運用である。

目的・変更範囲・完了条件が明確で、独立して統合・評価できる1つの改善仮説または修正を担当する。同じ仮説の実現と整合性保証に必要な複数ファイルの変更はまとめる。独立した改善を混ぜない。所要時間だけを理由に分割しない。

自分のworktreeで、作業開始を記録してから実装とローカル検証を行う。見込みは分単位の概算でよく、予測精度の保証は不要。大きく変わったら残り時間を更新し、詰まったら理由を残す。範囲が大きく広がる場合はoperatorへ判断を戻す。

## SQLiteへの記録

`./scripts/worker-db`は標準入力のSQLを共通SQLiteへ実行する薄い入口。Pythonは内部実装であり、操作ごとの独自コマンドは設けない。作業情報はINSERT / UPDATEで記録する。SQL全体は1 transactionで実行し、エラー時は戻す。

fork起動側はモデル`gpt-6-astra`と`model_reasoning_effort="medium"`を指定し、以下を環境へ渡す。Codexの子IDは`CODEX_THREAD_ID`からも取得できる。親IDは推測せず、起動側から渡す。

```bash
export SCAFFOLD_ROLE=worker
export SCAFFOLD_AGENT=codex
export SCAFFOLD_PARENT_SESSION_ID='<親operatorの実セッションID>'
export SCAFFOLD_SESSION_ID='<子workerの実セッションID>'
# 任意: SCAFFOLD_BASE_COMMIT, SCAFFOLD_TASK_ID
```

新規worker記録はagentが`codex`のものだけを受け付ける。既存の履歴は保持する。

開始時のtask ID、時刻、worktree、branch、分岐元commitは自動取得する。`make worktree`で作ったbranchは作成時のbase commitを使用し、それ以外は`SCAFFOLD_BASE_COMMIT`、未指定なら`merge-base HEAD main`を使う。既に変更したbranchを後付け登録する場合は正確な分岐元を指定する。

```bash
./scripts/worker-db <<'SQL'
INSERT INTO worker_start(task, estimate_minutes, completion_criteria, planned_validation)
VALUES ('一覧取得のN+1解消', 5, '同じレスポンスでSQLを一括取得する', 'cargo testと対象のローカル検証');
SQL
```

返り値に生成されたtask IDを含む。以後は現在のworktreeの未統合タスクを自動選択する。再開等で明示する場合は`SCAFFOLD_TASK_ID`を設定する。

```bash
./scripts/worker-db <<'SQL'
UPDATE workers SET remaining_minutes=3, notes='呼び出し元も修正中'
WHERE task_id=(SELECT task_id FROM worker_context);
SQL
```

詰まった場合は`state='blocked'`と理由、再開時は`state='working'`を記録する。
commitと検証を終えたら、必ず開発完了を記録する。注意点がなければ「なし」と記す。

```bash
./scripts/worker-db <<'SQL'
UPDATE workers SET state='developed', completed_at=strftime('%s','now'),
 result_commit=(SELECT head_commit FROM worker_context),
 validation='cargo test成功。対象ケースのローカル検証成功', notes='共有ベンチでの確認待ち'
WHERE task_id=(SELECT task_id FROM worker_context);
SQL
```

開発完了後は待機する。自分でmerge、deploy、共有ベンチ、worktree削除を行わない。開発完了は統合済みではない。

## forkスキルとの接続契約

通常workerをforkスキルで起動する場合、スキルは実際の会話履歴をforkし、専用branch/worktreeと人間が見られる独立ターミナルを使う。今回スキルそのものは変更しない。要約からの新規セッションやサブエージェントへの置き換えはしない。

接続入口は上記の環境変数、`worker_start`、`workers`、`worker_processes`。一時表`worker_context`には`task_id,parent_session_id,session_id,agent,worktree,branch,base_commit,head_commit`がある。

起動側が明示した`.local/worker-context.json`がある場合、`worker-db`が親IDを補完し、開始登録と同じtransactionで`worker_processes`へのCLI登録・task紐付けを行う。自分の実セッションIDは環境から取得し、親IDや子IDの不一致は拒否する。起動情報がある場合はworker_processesへ手動INSERTしない。CLI終了は起動側が記録する。従来の手動起動ではCLI起動・終了を起動側が`worker_processes`へ記録する。`task_id`は作業開始までNULLでよく、後で紐付ける。起動側は実際のCLI PIDとセッションIDをINSERTし、終了時に`exited_at`と`exit_code`をUPDATEする。プロセス終了からworkersのstateを更新する処理はない。異常終了しても作業状態は残る。

DBの場所は`./scripts/scout db-path`で取得でき、通常のsqlite3から直接操作することもできる。その場合は`worker_start`と`worker_context`はないため、`workers`の機械項目も明示する。schemaは`scripts/operations/schema.sql`。秘密・接続情報は記録しない。

## ローカル実行環境

Phase 1でoperatorが用意した`config/local/compose.yaml`を使い、自分のworktreeで`make local-up`、`make local-check`、`make local-logs`、`make local-down`を実行してよい。DB・port・networkはworktree単位で分離する。データ削除が必要なら`LOCAL_RESET=yes make local-reset`を明示する。remoteのdeploy・共有ベンチは実行しない。詳細は[ローカル実行環境](../local-development.md)を参照する。

## workerを見えるiTermで起動する

workerはiTermの新しいタブを前面に開き、対話型Codexをタブのフォアグラウンドで実行する。バックグラウンドの`codex exec`やログファイルだけの起動で代替しない。
単独起動・起動失敗後の再試行はmainで`make worker-start WORKTREE=/absolute/path/to/worktree`を実行する。Phase 1の通常開始は`kickoff`が自動起動する。親の実セッションIDは`CODEX_THREAD_ID`から渡す。Claude operatorは`python3 scripts/worker-iterm.py <worktree> --parent <実セッションID>`を使う。
Phase 1ではコード回収・worktree作成直後、kickoff終了を待たずに起動する。タブ内の起動成功とSQLiteの開始記録を確認する。開始前検査だけなら同scriptの`--check`を使う。進行中・停止中の未完了タスクがあるworktreeでは新規起動せず既存セッションを再開する。
この起動方法はPhase 1の独立セッション用で、過去の問題分析会話をforkしない。タブ内で終了操作を行い、CLI終了とタスク状態は別に記録する。練習停止の指示がある間は起動しない。

## 固定された担当と観測受信

起動情報の`mode`と`purpose`が担当の正本。`phase1`は先行改善の継続、`task`は一つの目的だけを扱い完了後待機する。起動情報がある場合はworker_startのtask名も固定のpurposeから登録する。別目的の調査・実装は別workerへ割り当てるため、自分の担当を変更しない。

worker-dbの返り値`updates`に自分宛ての観測・統合結果が最新50件まで含まれる。区切りで読み直す場合は `SELECT * FROM worker_inbox;` を実行する。これは追加依頼ではない。初期化失敗など担当範囲外の問題が届いても、自分の作業を切り替えずoperatorへ報告する。`notes`は自分の作業進捗専用。他workerのnotesは更新しない。
