# scout

役割はscout。現状を把握して自由に探索し、見落とし・疑問・仮説を最大300文字で報告する。4体の指示と入力形式は同じで、探索角度や専門分野は割り当てない。「発見なし」「順調そう」も最新報告として保存する。
共通指示は[scout-prompt.txt](scout-prompt.txt)。読み取り専用はプロンプトで指示する。CLI権限制御は必須条件ではなく、OSレベルで保証するものではない。

## 起動と停止

Python 3.9以上とSQLite（Python標準ライブラリ）を使用する。scaffoldはmain worktreeのrootから操作する。

```bash
make operations-init
./scripts/scout operator codex '<実際のoperatorセッションID>'
./scripts/scout operator claude '<実際のoperatorセッションID>'
./scripts/scout check
./scripts/scout input   # LLMを呼ばず入力プレビューを生成
make board             # http://127.0.0.1:8765 。Ctrl-Cで画面のserverだけ停止
# 別ターミナルで、人間がPhase 2への移行を決めた後
make scout-start
make scout-status
make scout-stop
```

開始は明示コマンドだけ。Phaseを推測して自動起動しない。ボードに開始・停止スイッチはない。常駐処理はoperatorのセッション終了から独立して動く。各scoutは新しいCLIセッションで入力生成→探索→報告保存を行い、Git用出力まで完了した時点から900秒後に次回実行する。4体の時刻は独立している。
停止時は待機と実行中CLIを終了する。再開時は保存済みの次回予定を尊重する。失敗時は900秒から最大3600秒まで間隔を延ばし、ボードにエラーを表示する。空回答・300字超過も失敗とし、最後の成功報告は残す。

同じscoutの手動実行と常駐実行にも共通のOSファイルlockを使う。これはローカル実行の多重起動防止のみであり、operatorの担当調整や実験leaseではない。lockファイルは削除しない。

## 設定と接続確認

`config/operations.json`に呼び出しargv、model、出力形式、時間、シナリオ参照先、比較元runを置く。端末固有の上書きはファイル全体を`.local/operations.json`へコピーする。秘密をGit管理設定に入れない。設定変更は常駐処理を停止してから行い、再起動する。

argvはshell文字列でなく配列。`{model}`、`{session}`（各回のUUID）、`{output}`（最終回答ファイル）を置換し、共通プロンプトはstdinに渡す。resume/continue指定は使わない。adapterは`file`、`claude-json`、`codex-json`、`opencode-json`、`text`を持つ。別CLIがstdin非対応なら、stdinを読み取ってそのCLIへ渡す小さなadapterを設定する。

未導入CLI・認証不足・model ID未確認は利用不可として表示し、代替モデルへ切り替えない。Kimi Code/Kimi K3は実機確認後に`unavailable`を除き、正確なargv/modelを設定する。OpenCodeはLithos無印の`lithosai/deepseek-ai/DeepSeek-V4.1-Flash`を設定済みで、[Lithos接続手順](opencode-lithos.md)に従って接続する。Kimiは任意で、利用可能なら動かし、未導入・未設定なら利用不可のまま他を動かす。採用・除外の判断待ちやPhase 0の完了待ちは設けない。未利用のscoutがあっても他は動く。

```bash
./scripts/scout once claude --smoke
./scripts/scout once codex --smoke
./scripts/scout once kimi --smoke
./scripts/scout once opencode --smoke
```

smokeは共通指示に接続確認指示を加えて各CLIを新規起動する。探索や運用報告の投稿はせず、ローカルに応答と実行ログを保存する。通常の単発運用は`./scripts/scout once <name>`。

Codexの非対話実行は[公式ドキュメント](https://developers.openai.com/codex/noninteractive/)と実機`codex exec --help`を確認している。接続検証の結果と未確認事項は[実機検証](connection-check.md)に記録する。

## 入力と保存先

- シナリオ: Phase 1の`docs/benchmark-scenario.md`。公式ルールとAGENTS.mdの指示に従う資料だけで整理する。
- 計測: `isuscope list`の最新の終了run（失敗・中断も明示）、`brief --limit 5`、HTTP・SQL load・ホストload・benchmarkの`query --limit 8`。比較元は設定の`base_run`を明示使用し、未指定なら比較なし。採用runを推測しない。
- 作業: 共通SQLiteの作業中・詰まり・開発完了／統合待ちworker。
- 会話: 登録したoperator 2セッションだけ。各直近6000文字、元ファイルとIDを添える。
- 根拠: run ID、比較元ID、runのcommit/dirty、現在のcommit/dirty、取得時刻、実行した取得コマンド、資料のパス。

入力生成はLLMを使わない。`isuscope`未設定・計測欠落はエラー情報として渡す。ボードにも同じ取得処理でスコア・主要HTTP/SQL/ホスト負荷・比較結果を表示し、計測処理やrunの採否管理は実装しない。画面は5秒、計測は30秒ごとに更新する。スコア推移は直近100件のrunのうち終了したrunを開始時刻順で表示する。PASSは折れ線、FAIL・中断は×印、スコア欠落は未取得欄に表示し、点にカーソルを合わせるかフォーカスすると時刻・run ID・commitを確認できる。

DBはGit共通ディレクトリの親、すなわちmain worktreeの`.local/operations/state.sqlite3`。全worktreeが同じ場所を参照する（通常の非bareリポジトリが対象）。入力・CLI出力・詳細実行ログは`.local/operations/`に保存する。worker側の`.local/`へ複製しない。

最新成功報告4件を`docs/scout-board.md`へ自動上書き出力する。自動commitはしない。過去投稿・他scout報告は入力へ入れない。Gitファイルは報告だけを保存し、変化の多い実行状態やworker記録はSQLiteに置く。画面はloopbackでGETのみを提供する。
