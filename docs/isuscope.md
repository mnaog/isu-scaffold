# isuscopeでの計測と分析

ベンチ結果を読む・比較する前に読む。運用の約束（仮説・分析・採否の記録）は`AGENTS.md`の「isuscopeの使い方」を正とし、ここには結果の読み方を置く。CLIの仕様とオプションは`isuscope --help`とリポジトリ（github.com/mnaog/isuscope）のREADMEを正とし、食い違えばそちらに従う。接続・設定の準備は[初動自動化](initial-automation.md)の6〜7節を参照する。

## 基本の流れ

```bash
# Phase 1の初回だけ
isuscope survey-run --hypothesis "初期状態の負荷構造を記録する"

# 通常の改善
isuscope run --hypothesis "変更理由と改善を期待する観測値"
isuscope brief latest
isuscope query latest --base BASE_RUN --metric-prefix benchmark. --limit 100
isuscope analyze RUN_ID supported --analysis "観測結果と判断" \
  --change <変更ID> --decision <accepted|provisional|rejected|deferred>
```

`provisional`には`--revisit`が必要。

## 比較の絞り込み

仮説の対象は`query --base`へ同じselectorを指定して比較し、対象を絞らない巨大JSONを避ける。

- HTTP: `--view http`と`--label route=...`
- DB: `--view database --window load`（initializeを除いた負荷区間）、必要に応じて`--label-contains digest=...`、`--group-by sql-shape`
- DBの合計時間の変化は、`total_ms_delta_by_calls`（回数の増減による分）と`total_ms_delta_by_avg`（1回あたりの時間による分）で理由を分ける

より自由度の高い分析や比較は`isuscope sql`（table定義は`--schema`）で行う。接続は読み取り専用で、データの場所は設定から解決する。

## 失敗・エラー・遅延の原因

SSHで手調べする前に`brief`の次の欄を見る。どれもinitializeを含む。

- `logs`: ベンチの間にアプリ・nginxのerror log・kernelが出したエラーを型ごとに数えたもの
- `database_io`: MySQLがfileの読み書きで待った時間
- `database_memory`: buffer poolとtableの大きさ

FAILしたrunの理由とエラーの実例は、`.isuscope/parse-benchmark.sh`がベンチの出力から`message`として残し、`isuscope list`の`failure`と`brief`の`benchmark_messages`で読む。判断材料とmessageには、適用されるルールで参加者の利用が認められた出力だけを使う。

## routeの正規化

初回runのHTTP routeに動的IDが残っている場合は、`isuscope routes suggest <run-id> --output .local/route-suggestions.toml`で候補を作る。確認したものだけ`.isuscope/routes.toml`へ移し、再計測する。
