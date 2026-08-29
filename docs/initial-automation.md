# 初動自動化

大会ごとに変わる接続先、配布path、ベンチ起動方法だけを宣言し、node発見からisuscopeの初回`survey-run`直前までを再現可能にします。生成されるIP、SSH秘密鍵、調査結果は`.local/`へ置き、コード、同期規則、ベンチ接続はGitへ残します。

`discover`、`bootstrap`、`inspect`、設定draft生成・反映、`import`、`deploy`、`rollback`、ベンチ実行などの変更系操作は`.local/operation.lock`を共有します。複数セッションから同時に開始した場合、後から来た操作は実行中のPID・開始時刻・操作名を表示して終了します。processが存在しない古いlockだけは次回操作時に自動回収します。

## 1. local設定を作る

```bash
mkdir -p .local
cp config/environment.example.env .local/environment.env
```

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
make bootstrap
```

local venvへ固定versionのAnsibleを導入し、SSH接続を確立してから、Linux前提を全nodeへ、任意package、計測tool、isuscope fingerprint helperをapplication nodeへ冪等に適用します。package導入はレギュレーション確認後に`ansible/playbooks/group_vars/all.yml`で有効化します。事前に取得した`alp`などは`.local/tools/`へ置き、`observability_local_tools`で配布できます。

`isuscope_fingerprint_paths`には、app binaryや主要設定など、全nodeで実体を比較したい絶対pathを列挙します。kernel、OS、Nginx、MySQL、running serviceは宣言なしでも記録します。

## 4. 初期構成を調査する

```bash
make inspect
```

remoteを変更せず、OS、CPU、memory、running service、listen port、process名、主要runtime、application root・設定・systemd unit・log path・計測toolの候補を`.local/inspection/<node>.json`へ保存します。process引数や環境変数は収集しません。

調査結果から設定候補を作ります。

```bash
make configure-draft
```

`.local/draft/`にはnode role、`sync.json`、Ansible変数、isuscopeのlog pathと判断材料の`summary.json`が生成されます。これは機械的な候補です。特にapplicationのowner/group、remote path、service名、role分担を確認・修正してから反映します。

```bash
CONFIRM_DRAFT=true make configure-apply
make discover
make sync-check
```

反映前の設定は`.local/draft-backup/`へ退避されます。node roleを反映した後の`discover`は`role_nginx`、`role_mysql`などのAnsible groupも生成します。

## 5. local正本とdeployを接続する

`config/sync.example.json`を参考に`config/sync.json`を編集します。

- `source_node`: 初期状態を回収するnode
- `items`: local/remote path、file/directory、配布先group、owner
- `pre_deploy_command`: local buildなど、配布前に一度実行する処理
- `build_commands`: 配布済みstaging itemをnode上でbuild・検証する、切替前の処理
- `post_deploy_commands`: config test、daemon-reload、restart、health check
- `status_commands`: 通常確認に使うcommand

commandは単なる文字列なら全application node、`{"node_group":"role_nginx","command":"..."}`なら指定groupだけで実行します。異なる役割のnodeへ無関係なreloadを送らないよう、分担構成ではgroupを明示します。

`build_commands`は`name`、`node_group`、`item`、単一行の`command`を持つobjectです。全itemをstagingした後、live pathを切り替える前に実行されます。commandには次の環境変数が渡されます。

- `ISUCON_DEPLOY_RELEASE`: 今回のrelease ID
- `ISUCON_DEPLOY_REMOTE_PATH`: itemのlive配置先
- `ISUCON_DEPLOY_STAGING_PATH`: build対象のstaging配置先

build成果物は`ISUCON_DEPLOY_STAGING_PATH`配下へ配置し、実行ファイルなど必要なartifactを最後に検証します。失敗した場合はlive pathを切り替えず、transactionを中断してstagingを除去します。node上の永続build cacheはtransaction外に残るため、cacheには再生成可能なartifactだけを置き、秘密情報やruntime dataを保存しません。

Rustでは永続的な`CARGO_TARGET_DIR`を使い、完成したbinaryだけをstaging item内の従来pathへコピーします。設定例は`config/sync.rust.example.json`です。`replace-with-binary-name`と`replace-with-service-name`、directory構成、実行userを当日のアプリに合わせて変更してください。初回importが`webapp/rust/target`を回収した場合は、内容を確認してlocalから除去してから初期commitします。例の`pre_deploy_command`はlocal targetが残ったdeployを拒否します。cacheが空なら現在のlive targetから一度だけseedし、以後はCargoのfingerprintで依存crateを再利用します。

```bash
make sync-check
make import
git add webapp config
git commit
make deploy
make status
```

`import`は対象groupの全nodeでpathの内容・mode・symlinkをdigest比較し、不一致なら回収前に停止します。意図した差異だと確認した場合だけ`IMPORT_ALLOW_DIVERGENT=true make import`とし、itemごとの`source_node`から回収します。比較表は`.local/import-comparison.tsv`へ残ります。

`deploy`は未コミットの同期対象を拒否します。全nodeでsudo、owner/group、空き容量、配置先をpreflightし、全fileをstagingし、build commandを完了してから切り替えます。一台でもbuild、配置、post-deploy検証に失敗すると、そのtransactionで触れた全対象を元へ戻します。状態は`.local/deploy-transactions/<release>.state`へ残ります。明示的に戻す場合は`make rollback RELEASE=<id>`を使います。manifestへ秘密情報を記録せず、そのようなファイルは`.local/`で別管理します。

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
make benchmark-check
make benchmark-probe
```

どちらの検査もベンチを起動しません。`check`は設定、regex、保存したサンプルを検査し、`probe`は実行ファイルまたはAPI endpointだけを確認します。標準adapterはcommandを一度だけ実行し、出力からscoreとPASS/FAILを取得してisuscope protocolへ変換し、二重起動も拒否します。HTTPのtokenやcookieは`.local/benchmark-secrets.env`またはGit管理外のheader fileへ置きます。

`make discover`はinspection draftで選んだNginx・MySQL log pathも生成済みisuscope設定へ反映します。初回run後、動的IDのためrouteが細分化されていれば次を実行します。

```bash
make routes-suggest RUN=<run-id>
```

`.local/route-suggestions.toml`は候補であり、自動適用されません。実例とpatternを確認し、必要な規則だけ`.isuscope/routes.toml`へ移します。

## 7. ベンチ前gateと初回run

```bash
make phase1-check
make survey HYPOTHESIS="初期状態の負荷構造とベンチシナリオを記録する"
```

`phase1-check`はshell・Ansible構文、全nodeのSSH、disk、必須service、sync manifest、benchmark adapter、ベンチ接続先、isuscope doctorを検査しますが、ベンチは起動しません。`survey`だけが初回ベンチを実行します。
