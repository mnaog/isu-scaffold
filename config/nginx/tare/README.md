# Nginxのタレ

初回baselineを未変更で取った後に、1つの変更としてdeployで入れて`isuscope run`で測る。計測用の設定（LTSV access log）はAnsible bootstrapがkickoffで固定化するので、ここには含めない。

## make tare（baselineの後に1 commitで入れる）

`make tare`（`scripts/tare.py`）が、問題に依存しない接続・fdの上限を1つのcommitにする。初回baselineの後に早めに`make deploy`して`isuscope run`で測る。もう一度実行しても何も変えない。

| 対象 | 変更 |
| --- | --- |
| `nginx.conf` | `worker_rlimit_nofile 131072`、`worker_connections 32768`、httpの`keepalive_requests 10000`、mainの`error_log ... warn`（接続の不足はwarnにしか出ない）、httpの既定の`access_log`を外す（計測用のLTSVがbuffer付きで全requestを書くので、既定のlogはbufferなしの二重の書き込みになる） |
| siteとconf.dの設定 | address:portごとに1つのlistenへ`backlog=4096`。`proxy_pass http://host:port`をkeepalive付きのupstreamへ移す（`proxy_http_version 1.1`、`Connection ""`。継承していた`proxy_set_header`とHostは保つ） |
| Goのunit | `LimitNOFILE=1048576` |
| `/etc/sysctl.d/99-tare.conf` | somaxconn、SYN backlog、netdev backlog、ephemeral port、TIME_WAITの再利用 |
| MySQL（`mysql.conf.d/zz-tare.cnf`） | `max_connections 1000`、`innodb_buffer_pool_size`（DB nodeのメモリの40%、MySQLだけのnodeなら70%） |
| deployのcommand | 先頭に`daemon-reload`と`sysctl -q -p /etc/sysctl.d/99-tare.conf`、MySQLの`is-active`の前に`restart mysql`。それぞれ`when`（`go-service`、`tare-sysctl`、`mysql-*`）付きで、そのitemが変わったdeployでだけ動く |

fsync・binlogなど耐久性を変える設定は`make durability`（`scripts/durability.py`）が別の1 commitにし、DBの書き込み待ちが見えたら入れる。下のB1のsendfileなどは入れない。静的ファイルの配り方（B2の静的配信・`open_file_cache`、Cache-Control、gzip_static）はlane `static-edge`が1件1commitで作る。これらの上限が既定値のままだと、負荷が上がったときに接続やfdが足りずにFAILする。

## B1. 問題に依存しない設定（ファイル）

`worker_connections`、`worker_rlimit_nofile`、`keepalive_requests`、upstreamのkeepaliveは`make tare`が入れる。

| ファイル | 配置先 | 内容 |
| --- | --- | --- |
| `10-tare.conf` | `/etc/nginx/conf.d/10-tare.conf` | `sendfile`、`tcp_nopush`、`tcp_nodelay` |
| `upstream-keepalive.conf` | `make tare`がupstreamへ移さなかったproxy（`proxy_pass`が変数や別の形のもの）にだけ差し込む | upstreamの`keepalive 128`、`proxy_http_version 1.1`、`Connection ""` |
| `nginx-limits.conf` | `/etc/systemd/system/nginx.service.d/limits.conf` | `LimitNOFILE=131072`（`make tare`の`worker_rlimit_nofile`と揃える） |

入れ方: sync.jsonへ`config/nginx/tare/10-tare.conf`などのitemを足し、`post_deploy_commands`に`sudo nginx -t`と`sudo systemctl daemon-reload`、`sudo systemctl reload nginx`（LimitNOFILEの変更時は`restart`）を置く。`rollback_commands`にも同じ検証とreloadを置く。


## B2. 問題によって効く設定

| 設定 | 効く条件 |
| --- | --- |
| 静的ファイルをNginxで配信（`root`/`try_files`/`expires`） | 画像やJSをappが返している |
| `open_file_cache`（FD上限とセット） | 静的ファイルへのアクセスが多い |
| APIのgzipを止める | CPUが律速で帯域に余裕がある（levelを上げると逆にCPUを使う） |
| HTTP/2 | TLSがあり、clientが対応している |
| TLS 1.3をAES-128-GCMに固定 | TLS終端のCPUが重い |
| `client_body_buffer_size`の拡大 | 大きいbodyのPOSTがあり、一時fileへ書き出している |

## B3. 効きにくい、または副作用のあるもの

- TLS session cache、`ssl_buffer_size 4k`、`proxy_buffering off`、proxy bufferの拡大、gzip levelの引き上げ：差が出にくく、bufferやCPUの使い方が変わる
- Nginx→appのUnix domain socket化、request headerのallowlist：実装と運用が複雑になる割に差が出にくい
- FD上限を上げずに`open_file_cache`だけ入れる：FDが枯渇する
