# 中難度の再現手順

practice-12の `3c551cb6a560e93c3dbdeecc7c5a33731fb83d26` から、登録・プレゼント受取後の完全な所持品キャッシュを実装する。依頼文は `config/ai-compare/medium-task.txt`、ルールは `medium-AGENTS.md`。既知の修正はテスト込み約266行のRust差分だが、解答の行数は制約にしない。容量増加は対象外。

低難度と同じローカルRust 1.63・4 jobs・offline vendorを使う。Cargo.lockの同一性を検査する。DBのみ追加し、MySQL 8.0.33 macOS ARM版の配布アーカイブをURL・SHA256で固定する。既存Homebrew MySQLやサービス設定は変更しない。

## 準備と実行

最初に低難度の `manage.py setup` が完了していること。

```bash
python3 scripts/ai-compare/medium.py setup-db
python3 scripts/ai-compare/medium.py prepare --trial opencode-medium-01
python3 scripts/ai-compare/medium.py prepare --trial codex-medium-01
python3 scripts/ai-compare/medium.py prepare --trial claude-medium-01

python3 scripts/ai-compare/run-opencode.py --destination ../ai-medium-trials/opencode-medium-01
python3 scripts/ai-compare/manage.py audit --destination ../ai-medium-trials/opencode-medium-01
python3 scripts/ai-compare/run-codex.py --destination ../ai-medium-trials/codex-medium-01
python3 scripts/ai-compare/manage.py audit --destination ../ai-medium-trials/codex-medium-01
python3 scripts/ai-compare/run-claude.py --destination ../ai-medium-trials/claude-medium-01
python3 scripts/ai-compare/manage.py audit --destination ../ai-medium-trials/claude-medium-01
```

再試行時は必ず新しいIDを使う。AIは別CLI・新規会話で起動し、このoperatorの履歴や解答は継承しない。速度比較のため直列に実行する。3者のモデル・推論・接続先・制限は低難度の設定JSONと同じ。

専用DBは127.0.0.1:23316、socketは `/tmp/isu-ai-compare-mysql.sock`。データ・設定・ログは `.local/ai-compare/mysql/`。テストは `new_user_cache_test` のみを使い、毎回DDLとfixtureから再作成する。DB停止は専用socketで次を実行する。

```bash
.local/ai-compare/mysql/mysql-8.0.33-macos13-arm64/bin/mysqladmin --no-defaults --socket=/tmp/isu-ai-compare-mysql.sock -uroot shutdown
```

setup-dbで再起動可能。DBの版・実行ファイルSHA・設定SHAもmanifestへ記録する。OS/SDK/Rustと同様に版が変わった結果は同条件の結果と混ぜない。

## 共通検証

- `check`: 固定Rustでコンパイル確認。
- `test`: DB不要の既存テストと追加テスト。
- `mysql`: 既存DB結合テスト。APIとDBの一致、重複・同時受取、強化・デッキ・報酬、DB失敗時の失効と再試行、login失効、cache clear、重複素材の集約。
- `acceptance`: operator所有の追加テスト。登録レスポンスは初期カード3枚のみ、完全な所持品はボーナス込み4枚、登録後一覧のSELECTゼロ、既存/新規素材・カード・コインの受取、受取後一覧、重複受取、既知の空配列、初回素材、cache clear後のDB一致。

SELECT数は専用MySQLの `SHOW GLOBAL STATUS LIKE 'Com_select'` の前後差で確認する。他のDB負荷を並走させない。[MySQL公式のステータス変数の定義](https://dev.mysql.com/doc/refman/8.0/en/server-status-variables.html)を参照。ベンチマーカーの内部やログは使わない。

受入テストは隠さず全者に同じものを配布し、編集禁止。独立auditはoperator側のverifierとmanifestを使う。さらに差分を読み、既存テストの弱体化・テストの読み込み無効化・容量変更・transaction順序などを確認する。テスト成功だけで正当と判定しない。

開始状態はcheck/test/mysqlが成功し、acceptanceが「登録後一覧SELECT 2」で失敗する。operator-medium-smoke-01へ既知修正を適用した正対照では4検証成功、各対象のSELECTゼロを確認した。この正対照はAIの成績に含めない。

準備・独立監査の時間はCLI作業時間から除外。ビルド・テスト実行とlock待ちを `.local/validation.jsonl` に分け、解き方はCLIの操作記録・最終回答から評価する。総時間から検証時間を引いた値をモデル待ち時間とは呼ばない。

## 実行結果と追加監査

[3者の結果と解き方](medium-process-analysis.md)に時間、成果物、観察した手順を記録した。結果JSONは `summarize.py --destination <trial> --output <json>` で生成できる。

完成差分のレビューで懸念が出た配列順は、後から別コピーで追加監査した。初回の共通受入テストとは別条件として扱う。

```bash
python3 scripts/ai-compare/audit-ordering.py --destination ../ai-medium-trials/codex-medium-01
```

既存の監査コピーは上書きしない。試行終了後、他のモデルやDB検証が走っていない状態で実行する。

## 出力上限の訂正

初回OpenCodeにoperatorが設定していた `limit.output=8192` は、ユーザーの指示で削除した。スキーマがcontext/outputの両方を要求するため、独自のlimitブロック全体を削除している。解決済みモデル設定はcontext/outputとも0（未登録）となる。新しい任意の上限や環境変数は設定していない。

OpenCode 1.18.34の標準処理には既定32,000の出力上限があるため、設定削除は「無制限」を意味しない。[標準の上限計算](https://github.com/anomalyco/opencode/blob/v1.18.34/packages/opencode/src/provider/transform.ts)と[リクエスト生成](https://github.com/anomalyco/opencode/blob/v1.18.34/packages/opencode/src/session/llm/request.ts)で確認した。

`opencode-medium-02` は初回と同じ課題を新規会話・新規リポジトリから実行する。初回の上限到達記録は保持し、モデル性能の失敗として集計しない。
