# isuscope data

`isuscope run`と、Phase 1で一度だけ使う`isuscope survey-run`の結果を保存するディレクトリです。

## Git管理

通常のコミットでは、各runの次の情報を記録します。

- `run.json`: スコア、成否、仮説、分析、実行時刻
- `source/`: commit hashと作業ツリーの差分
- `tooling/`: 実行時のisuscope設定
- `structured.json.zst`: 再構築用の構造化データ

次のデータは容量が大きい、または再構築できるため`.gitignore`で除外します。

- `isuscope.sqlite3*`
- `runs/*/logs/`
- 未完了runと一時ファイル

重要なrunの生ログまでGitへ残す場合は、run IDを指定してstageします。

```bash
make isuscope-pin RUN=<run-id>
git diff --cached --stat
git commit
```

生ログにはアクセスログやslow query logが含まれます。秘密情報が記録されていないことと容量を確認してからコミットしてください。
