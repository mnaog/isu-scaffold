# operator

起動時に「役割: operator」と明示し、この文書を読む。`SCAFFOLD_ROLE=operator`を設定する。
Claude CodeとCodexを常時開くが、積極的に動かすのは基本的に片方。新しい担当調整・実験所有権・leaseは設けない。既存の`.local/operation.lock`を維持する。

operatorはコード編集を担当せず、観測、ボトルネック調査と計測、人間との対話、worker起動、変更の統合、deploy、ベンチ、worktreeの後片付けを担う。Phase移行とscout開始は人間が判断する。

## セッションを登録する

実際のoperatorセッションIDをそれぞれ明示登録する。新しいoperatorセッションへ切り替えたときは再登録する。これは会話入力の識別用であり、操作権の取得ではない。

```bash
./scripts/scout operator codex "$CODEX_THREAD_ID"
./scripts/scout operator claude '<Claude Codeの実セッションID>'
```

scout入力はagent-historyのAgentとSessionの両headerが一致したファイルだけを読む。未登録・未発見の場合は欠落を表示し、最近のworkerログで代替しない。直近操作の監視は実装しない。

## Phase 1の初期改善セッション

`kickoff`はworktree作成直後にiTermでCodex／GPT-6-Astra mediumの初期改善セッションを自動起動する。operatorは起動と開始記録を確認し、並行する初期build調査の結果を見てconfig/phase1-build.jsonを確定する。全台準備の終了を待たない。起動・記録上はworkerだが、1目的ずつ依頼せず、同じworktreeで自明な改善を継続する。operatorは環境準備・初回計測を進め、run ID・対象commit付きの観測結果を渡す。検証済みcommitを途中で受け取り、baseline分析後に統合・評価する。部分統合でタスク全体を統合済みにせず、作業中のworktreeを片付けない。詳しくは[Phase 1](../phases/Phase1.md)に従う。

## 通常workerを渡す・受け取る

workerはCodex（`gpt-6-astra`、`model_reasoning_effort="medium"`）で起動する。調査と改善案の判断はoperatorが担い、必要に応じてscoutの短報を読む。

`make worktree BRANCH=... PURPOSE="..."`で1目的のworktreeを用意し、役割、目的、変更範囲、完了条件、親セッションIDを渡す。初回baseline前のremote反映は禁止。
開発完了のcommit、ローカル検証、注意点を確認し、mainの最新設定と変更根拠を照合して統合する。deployとベンチ・採否は従来通りisuscopeで扱う。

統合後、mainで以下を記録する。squash/cherry-pickでも統合後commitを記録できる。未統合タスクを勝手に統合済みにしない。

```bash
./scripts/worker-db <<'SQL'
UPDATE workers SET state='integrated', integrated_at=strftime('%s','now'),
 integration_commit=(SELECT head_commit FROM worker_context)
WHERE task_id='<対象task ID>' AND state='developed';
SQL
```

見送る場合は`state='dismissed'`とし、理由を`worker_updates`へ`kind='dismissal'`で記録する。作業内容の統合状態とisuscopeでの変更採否は別の記録であり、採否を複製しない。最後にoperatorがworktreeを片付ける。

## scoutとボード

[scout運用](scout.md)に従う。ボードは人間が読み、必要な気づきをoperatorへ渡す。自動通知・注入はない。`docs/scout-board.md`の差分は通常のコミットへ含める。

## workerを見えるiTermで起動する

workerはiTermの新しいタブを前面に開き、対話型Codexをタブのフォアグラウンドで実行する。バックグラウンドの`codex exec`やログファイルだけの起動で代替しない。
単独起動・起動失敗後の再試行はmainで`make worker-start WORKTREE=/absolute/path/to/worktree`を実行する。Phase 1の通常開始は`kickoff`が自動起動する。親の実セッションIDは`CODEX_THREAD_ID`から渡す。Claude operatorは`python3 scripts/worker-iterm.py <worktree> --parent <実セッションID>`を使う。
Phase 1ではコード回収・worktree作成直後、kickoff終了を待たずに起動する。タブ内の起動成功とSQLiteの開始記録を確認する。開始前検査だけなら同scriptの`--check`を使う。進行中・停止中の未完了タスクがあるworktreeでは新規起動せず既存セッションを再開する。
この起動方法はPhase 1の独立セッション用で、過去の問題分析会話をforkしない。タブ内で終了操作を行い、CLI終了とタスク状態は別に記録する。練習停止の指示がある間は起動しない。

## 依頼先を変えない

先行改善workerに別目的の調査・実装を追加しない。新しい目的は `make worktree BRANCH=... PURPOSE="..."` と `make worker-start WORKTREE=...` で別workerへ渡す。既存taskの目的・worktree・親子IDはDBで変更を拒否する。

他workerの`notes`は作業者本人の進捗であり、operatorから書き換えない。`./scripts/worker-db`は他worktreeのnotes変更を拒否する。観測共有と統合結果は`worker_updates`へ記録する。これは新規依頼の経路ではない。

```sql
INSERT INTO worker_updates(task_id,kind,body,run_id,commit_hash)
VALUES ('対象task ID','observation','観測事実と不明点','完全run ID','評価commitの40文字SHA');
```

`kind`は`observation`、`integration`、`dismissal`のみ。観測にはrun IDと評価commitが必須。統合範囲や見送り理由もnotesの上書きではなくこの表へ残す。直接SQLiteで検査を迂回しない。

単独の`make worker-start`は親ID・目的・lane種別を`.local/worker-context.json`と起動promptへ明示し、実セッションIDによるtask開始とCLI紐付けを確認してから成功する。CLIだけ動いて登録が止まった場合は非0となるが、既存CLIを重複起動しない。修復後は `python3 scripts/worker-iterm.py WORKTREE --check-started` で確認する。
