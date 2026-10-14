# scout

役割はscout。現状を把握して自由に探索し、見落とし・疑問・仮説を最大300文字で報告する。3体（Claude Code、Codex、Kimi Code）の指示と入力形式は同じで、探索角度や専門分野は割り当てない。「発見なし」「順調そう」も最新報告として保存する。
共通指示は[scout-prompt.txt](scout-prompt.txt)。読み取り専用はプロンプトで指示する。CLI権限制御は必須条件ではなく、OSレベルで保証するものではない。

報告の読み手はmainのセッションで、人間の中継を待たない。Claude Codeのmainがベンチ（`isuscope run`・`survey-run`・`make first-bench`）を`run_in_background`で起動すると、PostToolUseのhook（`scripts/tool-guard.py --post`）が最新報告を投稿からの経過分つきで会話へ渡す。mainはベンチの完了を待つ間にそれを読み、観測と照合して、採るものは次の仮説候補に、採らないものは理由をそのrunの`isuscope analyze`に一言残す。foregroundのベンチはPreToolUseのhookが止める。Codexにはbackgroundで起動する手段がないので、ベンチを起動する直前に`docs/scout-board.md`を読む。

## 起動と停止

Python 3.9以上とSQLite（Python標準ライブラリ）を使用する。main worktreeのrootから操作する。

```bash
python3 scripts/scouts/main.py init
python3 scripts/scouts/main.py conversation claude '<参照する実セッションID>'
python3 scripts/scouts/main.py conversation codex '<参照する実セッションID>'
python3 scripts/scouts/main.py check
python3 scripts/scouts/main.py input   # LLMを呼ばず入力プレビューを生成
# 人間がPhase 2への移行を決めた後
make scout-start
make scout-status
make scout-stop
```

開始は明示コマンドだけ。Phaseを推測して自動起動しない。常駐処理は作業セッションの終了から独立して動く。各scoutは新しいCLIセッションで入力生成→探索→最新状況の再確認→報告保存を行い、報告を保存し`docs/scout-board.md`へ書き出した時刻から480秒（8分）後に次回実行する。開始時刻からの固定間隔ではないので、探索に時間がかかった回はその分だけ次回も後ろへずれる。3体の時刻は独立している。
停止時は待機と実行中CLIを終了する。再開時は保存済みの次回予定を尊重する。失敗時は480秒から倍に延ばし（960、1920秒）、最大3600秒とする。空回答・300字超過も失敗とし、最後の成功報告は残す。

同じscoutの手動実行と常駐実行にも共通のOSファイルlockを使う。これはローカル実行の多重起動防止のみであり、作業担当の調整や実験leaseではない。lockファイルは削除しない。

## 設定と接続確認

`config/scouts.json`に呼び出しargv、model、出力形式、時間、シナリオ参照先、比較元runを置く。端末固有の上書きはファイル全体を`.local/scouts.json`へコピーする。秘密をGit管理設定に入れない。設定変更は常駐処理を停止してから行い、再起動する。

argvはshell文字列でなく配列。`{model}`、`{session}`（各回のUUID）、`{output}`（最終回答ファイル）を置換し、共通プロンプトはstdinに渡す。resume/continue指定は使わない。adapterは`file`、`claude-json`、`codex-json`、`text`を持つ。別CLIがstdin非対応なら、stdinを読み取ってそのCLIへ渡す小さなadapterを設定する。

未導入CLI・認証不足・model ID未確認は利用不可として表示し、代替モデルへ切り替えない。Kimi Code/Kimi K3は実機確認後に`unavailable`を除き、正確なargv/modelを設定する。Kimiが利用不可のままでも他の2体は動く。採用・除外の判断待ちやPhase 0の完了待ちは設けない。

```bash
python3 scripts/scouts/main.py once claude --smoke
python3 scripts/scouts/main.py once codex --smoke
python3 scripts/scouts/main.py once kimi --smoke
```

smokeは共通指示に接続確認指示を加えて各CLIを新規起動する。探索や報告の投稿はせず、ローカルに応答と実行ログを保存する。通常の単発運用は`python3 scripts/scouts/main.py once <name>`。

## 入力と保存先

- シナリオ: Phase 1の`docs/benchmark-scenario.md`。公式ルールとAGENTS.mdの指示に従う資料だけで整理する。
- 計測: `isuscope list`の最新の終了run（失敗・中断も明示）、`brief --limit 5`、HTTP・SQL load・ホストload・benchmarkの`query --limit 8`。比較元は設定の`base_run`を明示使用し、未指定なら比較なし。採用runを推測しない。
- 会話: 明示指定した会話セッションだけ（利用するagent分のみ指定）。各直近6000文字、元ファイルとIDを添える。
- 根拠: run ID、比較元ID、runのcommit/dirty、現在のcommit/dirty、取得時刻、実行した取得コマンド、資料のパス。

入力生成はLLMを使わない。`isuscope`の読み取りはボードと同じ上限付きの処理（`scripts/board/reads.py`）を使い、件数は増やさない。`isuscope`未設定・計測欠落はエラー情報として渡す。計測処理やrunの採否変更は実装しない。

DBはmain worktreeの`.local/scouts/state.sqlite3`。全worktreeが同じ場所を参照する（通常の非bareリポジトリが対象）。入力・CLI出力・詳細実行ログは`.local/scouts/`に保存する。別worktreeの`.local/`へ複製しない。

最新成功報告を`docs/scout-board.md`へ自動上書き出力する。自動commitはしない。過去投稿・他scout報告は入力へ入れない。Gitファイルは報告だけを保存し、変化の多い実行状態はSQLiteに置く。

会話の指定はagentごとに最新の明示登録で置き換わる。未指定のagentは入力へ含めない。履歴DBや更新時刻から会話を自動選択しない。

## 投稿前の最新確認

scoutは最終回答の直前に`python3 scripts/scouts/main.py current`を実行する。このコマンドは最新の終了runと`brief --limit 5`、現在のcommit・working tree・diff統計、明示登録した会話の末尾を標準出力へ返す。DB初期化・入力ファイル保存・他scout報告の取得は行わない。探索中に状況が変わった場合は同じセッション内で指摘に関係する根拠を再確認し、解消済みの指摘を取り下げる。再確認に失敗したら報告本文に明示する。CLIの再起動や探索全体のやり直しは増やさない。

これはscoutへの実行指示であり、投稿時点との完全な同時性を保証する仕組みではない。mainのセッションへ渡す報告には投稿からの経過分を添えるので、古い報告はmainが観測と照合して扱う。
