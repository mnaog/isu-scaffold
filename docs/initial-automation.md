# 初動自動化

大会ごとに変わる接続先、配布path、ベンチ起動方法だけを宣言し、node発見からisuscopeの初回`survey-run`直前までを再現可能にします。生成されるIP、SSH秘密鍵、調査結果は`.local/`へ置き、コード、同期規則、ベンチ接続はGitへ残します。

`discover`、`bootstrap`、`inspect`、設定draft生成・反映、`import`、`deploy`、`rollback`、ベンチ実行などの変更系操作は`.local/operation.lock`を共有します。scriptは`isuscope lock --path .local/operation.lock -- <script>`で自分自身を実行し直し、`isuscope run`/`survey-run`は`make discover`が書き出す`[lock] path`で同じlockを取ります。複数セッションから同時に開始した場合、後から来た操作は実行中のPID・開始時刻・操作名を表示して終了します。processが存在しない古いlockだけは次回操作時に自動回収します。

## 1. local設定を作る

```bash
mkdir -p .local
cp config/environment.example.env .local/environment.env
```

初動は次の2コマンドで進めます。採用言語はRustに固定しており、`config/application.env`の`APPLICATION_LANGUAGE=rust`、`APPLICATION_PATH=webapp/rust`を使います。問題にRust実装がない場合だけ、このファイルを書き換えてから始めます。

```bash
make kickoff
# 表示されたworktreeで別セッションのコード読解を始める
# .local/draft/review.mdのFAILを直し、WARNを判断する
CONFIRM_DRAFT=true make kickoff-apply
# importを確認して初期状態をcommit
make deploy
make phase1-check
```

`kickoff`はまずnode発見後、回収元の1台（既定では最初のapplication node）だけSSHを確立し、`/home/isucon/webapp/rust`と`/home/isucon/webapp/sql`のDDL・初期化script（`.sql`、`.sh`、`.py`、`.rb`、`.pl`）だけを先行回収します。1ファイル1MiB・合計16MiBを超えるものやそれ以外の初期データは後回しにし、`.local/code-schema-manifest.log`へ`INCLUDED`／`DEFERRED`として記録します。上限は`CODE_SCHEMA_MAX_FILE_BYTES`、`CODE_SCHEMA_MAX_TOTAL_BYTES`で変更できます。生成物の`target`、`node_modules`、`.git`は除外します。pathが異なる場合は`CODE_SOURCE_NODE`、`CODE_REMOTE_PATH`、`CODE_SCHEMA_REMOTE_PATH`、`CODE_SCHEMA_PATH`を環境変数で明示します。回収した`config/application.env`、`webapp/rust`、`webapp/sql`だけを自動commitし、他の未commit変更は含めません。この回収はコード読解開始用の暫定snapshotであり、全node一致の保証は後続の完全importが担当します。

続いて先行回収のcommitから別worktree（lane）を作り、目的・触ってよい範囲・mainへの報告形式と、他のlaneの一覧をまとめた引き継ぎ文を`<worktree>/.local/lane.md`へ生成します。laneを追加する場合は`make worktree BRANCH=<name> PURPOSE="何をするlaneか"`を使います。目的は必須で、`git config branch.<name>.description`へ保存されるため、branchを消せば一緒に消えます。進み具合や取り込み状況は記録せず、毎回gitから計算します。worktreeの場所は途中と最後に表示されるので、別セッションにこのファイルを読ませて開始します。その後、全nodeのSSH確立、Ansible導入、初期収束、inspection、draft生成を行い、最後に`scripts/review-draft.py`でdraftを実nodeと照合します。

Rustを採用しているため、draftは`webapp/`全体ではなく、`webapp/rust`（`sync.rust.example.json`のbuild設定とCargo.tomlのbinary名つき）、Rustのsystemd unit、`webapp/sql`内の1MiB以下の`.sql`・`.sh`、nginx・MySQL設定だけを同期対象に提案します。初期データはnodeに残し、配布しません。初期状態でRust用unitがない場合（他言語のunitだけが動いている場合）はrestart commandを入れずWARNにするので、unitを`config/systemd/`へ作ってitemとcommandを足してから切り替えます。

draft reviewはnodeごとに1回のSSHで事実を集め、次をFAILにします。

- 同期itemの配置先が`/`、`/etc`、`/home/isucon`などの広いsystem path、またはitem同士で重なる
- 配置先が存在しない、draftのdirectory／fileと種類が違う、ownerやgroupがnodeに存在しない
- 対象groupにnodeがいない、source nodeが対象groupに含まれない、roleのないnodeがある
- `post_deploy_commands`や`status_commands`で確認するserviceが動いていない

512MiBを超える配布item、読めないlog、draft生成時の警告、nginx／db roleの欠落、serverブロック内の`access_log`（`off`を含む。そのserverのリクエストは計測用LTSV logへ書かれない）はWARNです。nginxの計測用drop-inがないnodeはFAILです。

draft生成は、採用言語のコードからsessionを識別するheaderやcookie（`x-session`、`Cookie::build("app_session"`、`const SESSION_ID_KEY: &str = "SESSIONID"`など）を探し、`observability_nginx_session_source`の候補（例: `$http_x_session`、`$cookie_SESSIONID`）を`ansible-vars.json`へ入れてWARNで確認を促します。見つからなければ設定を促すWARNになります。`kickoff-apply`は反映後に計測用logのdrop-inだけを書き直します。アプリがSQLiteやPostgreSQLに依存する場合、またはPostgreSQLが動いている場合は、DB計測がMySQL slow logだけであることをWARNにします。結果は`.local/draft/review.md`へ残ります。FAILがある場合`kickoff`は非0で終わり、draftを直して`python3 scripts/review-draft.py`で再検査します。`kickoff-apply`は反映前にもう一度同じ検査を行い、FAILなら何も変更しません。WARNの判断は、`review.md`を読んだ操作者（人間またはその作業セッション）が行います。

`kickoff-apply`は検査済みdraftの反映、再discover、sync検査、完全import、採用言語の固定まで進めます。ベンチ前gateは`make phase1-check`を一度だけ実行します。個別の段階をやり直す場合は、以下の各節にある`scripts/`のscriptを直接実行します。初回`survey-run`、deploy、mergeは自動実行しません。

SSH確立はinventoryのnode名で重複排除し、`SSH_MAX_PARALLEL_NODES`（既定5）台ずつ並列に行います。完全importはnodeごとに全itemのdigestを1回のSSHでまとめて計算し（`IMPORT_MAX_PARALLEL_NODES`、既定5）、localの内容がsourceのdigestと一致するitemは再転送しません。全itemのstagingと検証が終わるまで既存のlocalを置き換えず、途中で失敗した場合は元へ戻します。Ansible requirementsは内容hashが同じなら再installせず、bootstrap内のapplication nodeのfact収集も一度だけです。

lockを取る変更系操作は、終了時に`.local/operation-timing.tsv`へ開始時刻・操作名・秒数・終了codeを残します。次回の初動改善はこの実測を根拠に判断します。

接続先の取得方法は2種類です。

- `DISCOVERY_PROVIDER=aws-cloudformation`: stack配下のEC2をName tagのregexで分類する
- `DISCOVERY_PROVIDER=static`: `config/nodes.example.json`を`.local/nodes.json`へコピーし、任意のSSH接続先を記述する

SSH鍵が既に登録済みなら`SSH_BOOTSTRAP_METHOD=existing`、EC2 Instance Connectからoperator鍵を登録するなら`eic`を使います。

## 2. nodeと実設定を生成する

```bash
make discover
```

providerの出力は共通形式へ正規化され、次を一度に生成します。

| ファイル | 内容 |
|---|---|
| `.local/discovered-nodes.json` | providerから得た正規化済みnode |
| `.local/nodes.snapshot.json` | 生成日時を含む確認用snapshot |
| `.local/ansible-inventory.json` | AnsibleとSSH helperが使う接続先 |
| `.local/isuscope-nodes.toml` | isuscopeへ反映したapplication node |
| `.isuscope/config.toml` | `config.template.toml`とnode情報から作るisuscope実設定 |

`.isuscope/config.toml`にはIPとSSH identityが入るためGit管理しません。collectorの正本は`.isuscope/config.template.toml`です。IPやnode数が変わったら手編集せず`make discover`を再実行します。

負荷を担うsystemd unitが判明したら`.local/environment.env`の`ISUSCOPE_SERVICE_UNITS`へ空白区切りで指定します。`configure-draft`はinspectionで関連すると判断したrunning serviceの和集合も`.local/draft/isuscope.json`へ候補として出すため、`configure-apply`前に過不足を確認できます。生成されたservice-samplerはそのunitのcgroup v2だけを1秒間隔で読み、CPU core使用量、memory、read/write帯域、PID数を記録します。空の場合やcgroup v2でない環境では`unavailable`となります。

## 3. SSHと全nodeを揃える

```bash
./scripts/bootstrap.sh
```

local venvへ固定versionのAnsibleを導入し、SSH接続を確立してから、Linux前提を全nodeへ、計測と運用の土台をapplication nodeへ冪等に適用します（`make kickoff`内でも実行されます）。性能は変えないので、初回baselineより前に入れて固定します。

| 項目 | 内容 | 無効化する変数 |
| --- | --- | --- |
| 計測tool | `sysstat`、実行中kernel用のperf、pin済みの`alp`・`slp`、FlameGraph scripts。off-CPU用の`bpfcc-tools`は既定では入れない | `observability_install_packages`、`observability_download_tools`（`observability_install_offcpu`で有効化） |
| perf権限 | `kernel.perf_event_paranoid=-1`、`kernel.kptr_restrict=0` | — |
| Nginx access log | `conf.d/00-isuscope-log.conf`でLTSVを`/var/log/nginx/isuscope-access.log`へ追加出力（既存のaccess_logは変えない）。sessionは`observability_nginx_session_source`で問題に合わせる | `observability_nginx_ltsv` |
| MySQL slow log | `zzz-isuscope-slow.cnf`で`long_query_time=0`を`/var/log/mysql/isuscope-slow.log`へ出力し、MySQLを再起動 | `observability_mysql_slow_log` |
| log rotation | 上記2つを512MiBで世代交代し、logrotateを1時間ごとに実行 | — |
| 時刻同期 | chronyを有効化 | `observability_time_sync` |
| journal | 永続化し、512MiBで上限 | `observability_persistent_journal` |
| 自動更新 | `apt-daily`、`apt-daily-upgrade`、`unattended-upgrades`を停止 | `observability_stop_auto_updates` |
| Rust build cache | `/home/isucon/.cache/isucon-cargo-target`を作成し、cargoとrustcの有無を表示 | — |

nginx.confがhttpの中で`/etc/nginx/conf.d/*.conf`をincludeしていない場合、LTSV logは追加されず、draft検査がFAILにします。レギュレーションで禁止された項目だけ変数をfalseにします。Phase 3で計測を外すときは`observability_nginx_ltsv`と`observability_mysql_slow_log`をfalseにして`./scripts/run-ansible-playbook.sh bootstrap.yml --tags observability_nginx_log,observability_mysql_slow_log`を実行すると、設定を撤去してreload・再起動します。性能のためのNginx設定は[config/nginx/tare](../config/nginx/tare/README.md)にあり、初回baselineの後にdeployで入れます。事前に取得したtoolは`.local/tools/`へ置き、`observability_local_tools`で配布できます。

`isuscope_fingerprint_paths`には、app binaryや主要設定など、全nodeで実体を比較したい絶対pathを列挙します。kernel、OS、Nginx、MySQL、running serviceは宣言なしでも記録します。

## 4. 初期構成を調査する

```bash
./scripts/inspect-environment.sh
```

remoteを変更せず、OS、CPU、memory、running service、listen port、process名、主要runtime、application root・設定・systemd unit・log path・計測toolの候補を`.local/inspection/<node>.json`へ保存します。process引数や環境変数は収集しません。

調査結果から設定候補を作ります。

```bash
./scripts/configure-draft.sh
```

`.local/draft/`にはnode role、`sync.json`、Ansible変数、isuscopeのlog pathと判断材料の`summary.json`が生成されます。これは機械的な候補です。特にapplicationのowner/group、remote path、service名、role分担を確認・修正してから反映します。

```bash
CONFIRM_DRAFT=true ./scripts/configure-apply.sh
make discover
./scripts/sync-check.sh
```

Git commitが存在する場合、`sync-check`はmanifest検証に加えて、Git管理中の配布byte数とnode複製後の合計byte数を表示します。local・remoteの配置先が重なるitemやbuild commandの名前の重複、改行を含むcommandは拒否します。ignored artifactはこの値にも実際のdeploy archiveにも含まれません。

反映前の設定は`.local/draft-backup/`へ退避されます。node roleを反映した後の`discover`は`role_nginx`、`role_mysql`などのAnsible groupも生成します。

## 5. local正本とdeployを接続する

通常は`make kickoff`が生成し`make kickoff-apply`が反映したdraftから`config/sync.json`を始めます。Rustのbuild設定は`config/sync.rust.example.json`を参照します。

- `source_node`: 初期状態を回収するnode
- `items`: local/remote path、file/directory、配布先group、owner
- `pre_deploy_command`: local buildなど、配布前に一度実行する処理
- `build_commands`: 配布済みstaging itemをnode上でbuild・検証する、切替前の処理
- `post_deploy_commands`: config test、daemon-reload、restart、health check
- `rollback_commands`: 旧ファイル復元後に行うconfig test、daemon-reload、restart、health check
- `status_commands`: 通常確認に使うcommand

commandは単なる文字列なら全application node、`{"node_group":"role_nginx","command":"..."}`なら指定groupだけで実行します。異なる役割のnodeへ無関係なreloadを送らないよう、分担構成ではgroupを明示します。

`build_commands`は`name`、`node_group`、`item`、単一行の`command`を持つobjectです。全itemをstagingした後、live pathを切り替える前に実行されます。commandには次の環境変数が渡されます。

- `ISUCON_DEPLOY_RELEASE`: 今回のrelease ID
- `ISUCON_DEPLOY_REMOTE_PATH`: itemのlive配置先
- `ISUCON_DEPLOY_STAGING_PATH`: build対象のstaging配置先

build成果物は`ISUCON_DEPLOY_STAGING_PATH`配下へ配置し、実行ファイルなど必要なartifactを最後に検証します。失敗した場合はlive pathを切り替えず、transactionを中断してstagingを除去します。node上の永続build cacheはtransaction外に残るため、cacheには再生成可能なartifactだけを置き、秘密情報やruntime dataを保存しません。

Rustでは永続的な`CARGO_TARGET_DIR`を使い、完成したbinaryだけをstaging item内の従来pathへコピーします。設定例は`config/sync.rust.example.json`です。`replace-with-binary-name`と`replace-with-service-name`、directory構成、実行userを当日のアプリに合わせて変更してください。初回importが`webapp/rust/target`を回収した場合は、内容を確認してlocalから除去してから初期commitします。例の`pre_deploy_command`はlocal targetが残ったdeployを拒否します。cacheが空なら現在のlive targetから一度だけseedし、以後はCargoのfingerprintで依存crateを再利用します。

```bash
./scripts/sync-check.sh
./scripts/import.sh
git add webapp config
git commit
make deploy
make status
```

`import`は対象groupの全nodeでpathの内容・mode・symlinkをdigest比較し、不一致なら回収前に停止します。意図した差異だと確認した場合だけ`IMPORT_ALLOW_DIVERGENT=true ./scripts/import.sh`とし、itemごとの`source_node`から回収します。比較表は`.local/import-comparison.tsv`へ残ります。

`deploy`は未コミットの同期対象を拒否します。全nodeでsudo、owner/group、空き容量、配置先をpreflightし、全fileをstagingし、build commandを完了してから切り替えます。一台でもbuild、配置、post-deploy検証に失敗すると、そのtransactionで触れた全対象を元へ戻し、live切替後なら`rollback_commands`で稼働プロセスも旧構成へ戻します。rollback自体が失敗した場合、stateは`rollback-failed`になります。

transaction IDは既定で`<commit>-<UTC時刻>-<PID>`となり、同じコミットを繰り返しdeployできます。Gitコミットは`.local/current-commit`、transaction IDは`.local/current-release`へ保存し、明示rollbackが成功した場合は両方を直前の値へ戻します。remoteのrollback backupは既定で各配布先の直近3世代を保持し、`DEPLOY_BACKUP_RETENTION`で変更できます。明示的に戻す場合は、保持中のIDを指定して`make rollback RELEASE=<id>`を使います。rollbackは全対象のbackupが揃っていることを確認してから復元を開始します。manifestへ秘密情報を記録せず、そのようなファイルは`.local/`で別管理します。

## 6. isuscopeへベンチを接続する

デフォルトは`mode = "command"`です。`external`を通常運用には使いません。

`config/benchmark.env`へ大会の起動方法を記述します。

- `BENCHMARK_TRANSPORT=local`: 操作端末でcommandを実行
- `BENCHMARK_TRANSPORT=ssh`: inventoryの`BENCHMARK_NODE`でcommandを実行
- `BENCHMARK_TRANSPORT=http`: APIで開始し、必要ならrun IDを使って完了までpollする
- `BENCHMARK_PROBE_COMMAND`またはHTTP probe URL: ベンチを開始しない疎通確認
- `BENCHMARK_SAMPLE_FILE`: 実出力の短いサンプル。scoreと最終PASS/FAILのregexを事前検証する
- score、PASS、FAILを見つける正規表現
- timeout

tokenやcookieだけは`config/benchmark-secrets.example.env`を`.local/benchmark-secrets.env`へコピーして保存します。

```bash
.isuscope/benchmark.sh --check
.isuscope/benchmark.sh --probe
```

どちらの検査もベンチを起動しません。`check`は設定、regex、保存したサンプルを検査し、`probe`は実行ファイルまたはAPI endpointだけを確認します。標準adapterはcommandを一度だけ実行し、出力からscoreとPASS/FAILを取得してisuscope protocolへ変換し、二重起動も拒否します。HTTPのtokenやcookieは`.local/benchmark-secrets.env`またはGit管理外のheader fileへ置きます。

`make discover`はinspection draftで選んだNginx・MySQL log pathも生成済みisuscope設定へ反映します。初回run後、動的IDのためrouteが細分化されていれば次を実行します。

```bash
isuscope routes suggest <run-id> --output .local/route-suggestions.toml
```

`.local/route-suggestions.toml`は候補であり、自動適用されません。実例とpatternを確認し、必要な規則だけ`.isuscope/routes.toml`へ移します。

### isuscopeの設定で確認すること

- `make discover`が生成する`.isuscope/config.toml`のnode、role、SSH設定は生成物なので、直す場合はprovider入力とdraftを直して再生成する。roleは複数指定でき、collectorの対象nodeを選ぶtagとして使う
- 負荷を担う少数のsystemd unitだけを`ISUSCOPE_SERVICE_UNITS`へ指定する。アクセスログとslow logのpathと形式（時刻、匿名化session、method、URIなど時系列に使うfield）を実環境へ合わせる
- app binaryや主要設定のpathを`isuscope_fingerprint_paths`へ指定すると、`fingerprint.sh`と一緒に各nodeへ冪等に配置される
- 標準のlog collectorは`.1`〜`.5`と各`.gz`から開始時のlogを照合し、保持世代を越えたrotationや欠落は壊れた差分を返さず`unavailable`になる。非空logをalp/slpが1件も解析できない場合は設定不一致として`failed`になる
- `.isuscope/routes.toml`は1規則から固定のcanonical routeへ置換し、patternにcomma、replaceに`$1`などのcaptureを使わない。初回runで動的URLが残ったら`isuscope routes suggest <run-id> --output .local/route-suggestions.toml`の候補を確認して移す
- 会話履歴とrunを紐付ける`[context.agent]`を使う場合は、新しいセッションを開始する前に共通hook（`~/.agent-history/agent_history.py`）を有効にしておく
- remote変更は既存fileのbackup、設定検証、atomicな配置、必要最小限のreloadで行い、package導入や常駐agentは既存機能で代替できない場合だけ使う

## 7. ベンチ前gateと初回run

```bash
make phase1-check
isuscope survey-run --hypothesis "初期状態の負荷構造とベンチシナリオを記録する"
```

`phase1-check`はAnsibleの構文と全nodeのverify（SSH、disk、role別の必須service、必須command）、sync manifest、benchmark adapterのcheckとprobe、`isuscope doctor`を実行しますが、ベンチは起動しません。shell構文と空白の検査はCIが担当します。`isuscope doctor`は、保存したbenchmark sampleに`initialize_start_marker`・`initialize_finish_marker`の文言が含まれるかを確認し（含まれなければinitializeと負荷の区間分けができないのでWARN）、各collectorの`preflight`（全nodeで`/proc/stat`の1秒sampling、nginx access logの読取、`sudo -n`でのslow log読取など）を対象nodeで実行し、`config/benchmark-sample.log`へ全parserを適用し、最新runに動的IDを含むrouteが残っていないかも確認します。
