# Nginxのタレ

初回baselineを未変更で取った後に、1つの変更としてdeployで入れて`isuscope run`で測る。計測用の設定（LTSV access log）はAnsible bootstrapがkickoffで固定化するので、ここには含めない。

## make tare（baselineの後に1 commitで入れる）

`make tare`（`scripts/tare.py`）が、問題に依存しない接続・fdの上限を1つのcommitにする。初回baselineの後に早めに`make deploy`して`isuscope run`で測る。もう一度実行しても何も変えない。

| 対象 | 変更 |
| --- | --- |
| `nginx.conf` | `worker_rlimit_nofile 131072`、`worker_connections 32768`、httpの`keepalive_requests 10000`、mainの`error_log ... warn`（接続の不足はwarnにしか出ない） |
| siteとconf.dの設定 | address:portごとに1つのlistenへ`backlog=4096`。`proxy_pass http://host:port`をkeepalive付きのupstreamへ移す（`proxy_http_version 1.1`、`Connection ""`。継承していた`proxy_set_header`とHostは保つ） |
| Rustのunit | `LimitNOFILE=1048576` |
| `/etc/sysctl.d/99-tare.conf` | somaxconn、SYN backlog、netdev backlog、ephemeral port、TIME_WAITの再利用 |
| MySQL（`mysql.conf.d/zz-tare.cnf`） | `max_connections 1000`、`innodb_buffer_pool_size`（DB nodeのメモリの40%、MySQLだけのnodeなら70%） |
| deployのcommand | 先頭に`daemon-reload`と`sysctl -q -p /etc/sysctl.d/99-tare.conf`、MySQLの`is-active`の前に`restart mysql`。それぞれ`when`（`rust-service`、`tare-sysctl`、`mysql-*`）付きで、そのitemが変わったdeployでだけ動く |

fsync・binlogなど耐久性を変える設定と、下のB1のsendfileなどは入れない。practice-14ではこれらの既定値（keepalive_requests 1000、アプリのfd 1024、listen backlog 511、worker_connections 4096）で、後からFAILが4回出た。

## B1. ほぼ常に入れてよい（まとめて1回）

`worker_connections`、`worker_rlimit_nofile`、`keepalive_requests`、upstreamのkeepaliveは`make tare`が入れる。

| ファイル | 配置先 | 内容 |
| --- | --- | --- |
| `10-tare.conf` | `/etc/nginx/conf.d/10-tare.conf` | `sendfile`、`tcp_nopush`、`tcp_nodelay` |
| `upstream-keepalive.conf` | `make tare`がupstreamへ移さなかったproxy（`proxy_pass`が変数や別の形のもの）にだけ差し込む | upstreamの`keepalive 128`、`proxy_http_version 1.1`、`Connection ""` |
| `nginx-limits.conf` | `/etc/systemd/system/nginx.service.d/limits.conf` | `LimitNOFILE=131072`（`make tare`の`worker_rlimit_nofile`と揃える） |

入れ方: sync.jsonへ`config/nginx/tare/10-tare.conf`などのitemを足し、`post_deploy_commands`に`sudo nginx -t`と`sudo systemctl daemon-reload`、`sudo systemctl reload nginx`（LimitNOFILEの変更時は`restart`）を置く。`rollback_commands`にも同じ検証とreloadを置く。

根拠: practice-12はworker 4096とupstream keepalive 128の構成で366,502点まで到達した。practice-13はFD上限65,535を前提に各設定を積み上げた。

## B2. 問題によって効く（計測を見て1つずつ）

| 設定 | 効く条件 | 根拠 |
| --- | --- | --- |
| 静的ファイルをNginxで配信（`root`/`try_files`/`expires`） | 画像やJSをappが返している | practice-13でiconの304をNginx化し621,753→734,162点（+18%） |
| `open_file_cache`（FD上限とセット） | 静的ファイルへのアクセスが多い | practice-13で採用 |
| APIのgzipを止める | CPUが律速で帯域に余裕がある | practice-13でOFFにして+1.75〜2.34%。levelを3や6へ上げると−1〜1.5% |
| HTTP/2 | TLSがあり、clientが対応している | practice-13で+0.8% |
| TLS 1.3をAES-128-GCMに固定 | TLS終端のCPUが重い | practice-13で+1.28% |
| `client_body_buffer_size`の拡大 | 大きいbodyのPOSTがある | practice-13のicon uploadで一時fileへの書き出しを回避 |

## B3. 入れない（効かなかった、または壊した）

- TLS session cache（微減）、`ssl_buffer_size 4k`、`proxy_buffering off`、proxy bufferの128KiB化、gzip levelの引き上げ
- Nginx→appのUnix domain socket化、request headerのallowlist
- FD上限を上げずに`open_file_cache`だけ入れる

practice-13では、これらは誤差か微減で、実装や運用の複雑さに見合わなかった。同じ提案を繰り返す前にここを確認する。
