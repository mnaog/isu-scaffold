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

## workerを渡す・受け取る

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

見送る場合は`state='dismissed', notes='理由'`を記録する。作業内容の統合状態とisuscopeでの変更採否は別の記録であり、採否を複製しない。最後にoperatorがworktreeを片付ける。

## scoutとボード

[scout運用](scout.md)に従う。ボードは人間が読み、必要な気づきをoperatorへ渡す。自動通知・注入はない。`docs/scout-board.md`の差分は通常のコミットへ含める。
