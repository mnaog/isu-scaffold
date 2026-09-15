# isuscope setup

このディレクトリは`isuscope init`が一度だけ生成する雛形です。AIまたは担当者は、ベンチを開始する前に次の順序で準備します。

1. ベンチ起動方法を調べ、標準のlocal、SSH、HTTP API方式なら`config/benchmark.env`へ設定する。保存した実出力を`config/benchmark-sample.log`へ置き、標準方式で表せない場合だけ`benchmark.sh`を拡張する
2. 必要なら`parse-benchmark.sh`で問題固有のbenchmark出力をmetric JSONLへ変換する
3. Codex・Claude Codeの会話履歴とrunを紐付ける場合は、新しいセッションを開始する前に共通の会話履歴hook（`~/.agent-history/agent_history.py`）を導入・信頼し、`[context.agent]`を有効化する
4. `make discover`が生成した`.isuscope/config.toml`のnode、role、identity fileを確認する。修正は生成物でなくprovider入力へ行う。roleは固定的な種類ではなく、複数指定・run間の変更が可能なcollector選択tag
5. `./scripts/inspect-environment.sh`、`./scripts/configure-draft.sh`で検出したlog pathを確認し、Nginxアクセスログに時刻、匿名化session、method、URIがあるか確認する
6. 生成済みのsysstat、perf、host-sampler、service-sampler、alp、slp、optionalなperf-flamegraph/offcpu collectorを確認する。負荷を担う少数のsystemd unitを`ISUSCOPE_SERVICE_UNITS`へ指定し、不要なら空のままにする。アクセスログ・slow logのpathとformat（時系列用field名を含む）を実環境へ合わせる。Flame Graph scriptsや`offcputime-bpfcc`がなければcollectorは`unavailable`になる。既定commandはalp 1.0.21とslp 0.2.1で検証済み。ALPの正確なcount、status、sum/avg、p50/p95/p99集約のため、`routes.toml`はpatternにcomma、replaceに`$1`などのcaptureを使わず、1規則から固定canonical routeへ置換する
7. app binaryや主要設定を`isuscope_fingerprint_paths`へ指定し、`./scripts/bootstrap.sh`で汎用`fingerprint.sh`と一緒に各nodeへ冪等配置する
8. `bash -n benchmark.sh`、`bash -n parse-benchmark.sh`、`bash -n setup.sh`、`isuscope list`を実行してから、不足する場合だけ`setup.sh`の`apply_environment`へ冪等な導入処理を追加する
9. ここで初めて`setup.sh`を実行し、`setup-state.json`が生成されることを確認する。標準ツールは自動installされない
10. `.isuscope/benchmark.sh --check`、`.isuscope/benchmark.sh --probe`、`isuscope doctor`を実行し、ベンチを起動せずfailureを解消する
11. `isuscope survey-run --hypothesis "初期状態の負荷構造を記録する"`を一度実行し、`isuscope brief latest`と`isuscope query latest --metric-prefix benchmark. --group-by scenario --limit 100`でcollector異常、主要metric、scenario、transitionを確認する。初期化を除くhost/service集約は`isuscope query latest --scope series --window load --metric-prefix service. --group-by node --group-by service`、時系列は`isuscope series latest --window load --metric <name>`で掘り下げる。動的routeが未正規化なら`isuscope routes suggest <run-id> --output .local/route-suggestions.toml`の候補を確認する。PASS後は出力されたIDを指定して`isuscope analyze RUN_ID VERDICT --analysis "結果"`で記録する

計測結果はリポジトリ内の`isuscope-data/`へ保存します。`run.json`、`source/`、`tooling/`、`structured.json.zst`は通常のGit操作で記録し、容量の大きいSQLiteと`logs/`は既定でGit管理から除外します。再現に必要な重要runの生ログは`isuscope pin <run-id>`で明示的にstageしてください。

remote変更を行う場合は、既存ファイルのbackup、設定検証、atomicな配置、必要最小限のreloadを行います。パッケージ導入やremote build、常駐agentは既存機能で代替できない場合だけ使用します。

標準log collectorは`sha256sum`、`gzip`、`tail`、`wc`を使い、`.1`〜`.5`と各`.gz`から開始時のlogを照合します。保持世代を越えたrotationや中間世代の欠落は、壊れた差分を返さず終了コード75で`unavailable`になります。非空logをalp/slpが1件も解析できなかった場合は設定不一致として`failed`になります。

`benchmark.sh`、`parse-benchmark.sh`、`setup.sh`、`config.toml`、`routes.toml`、`setup-state.json`およびisuscopeのversionは各runの`tooling/`へsnapshotされます。序盤の`survey-run`完了後は、仮説付きの`run`、小さい全体像を返す`brief`、同じselectorでrun間比較する`query --base`、結果を残す`analyze`を標準フローにします。`report`、`diff`、`metrics`は詳細診断、`series`は時系列、`enrich`は保存済みlogの再解析、`ui`は人が複数runを横断する用途に使います。

`[context.agent]`を有効にした場合、runはCodexの`CODEX_SESSION_ID`／`CODEX_THREAD_ID`、またはClaude Codeの`CLAUDE_CODE_SESSION_ID`と一致するhistory fileだけを採用し、最後のUser入力のID（Codexは`turn_id`、Claude Codeは`prompt_id`）をinput IDとして保存します。通常ターミナル、別セッション、hook未起動ではfallbackせず、benchmarkを開始しません。
