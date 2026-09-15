# Nginxのタレ

初回baselineを未変更で取った後に、1つの変更としてdeployで入れて`isuscope run`で測る。計測用の設定（LTSV access log）はAnsible bootstrapが初動で固定化するので、ここには含めない。

## B1. ほぼ常に入れてよい（まとめて1回）

| ファイル | 配置先 | 内容 |
| --- | --- | --- |
| `10-tare.conf` | `/etc/nginx/conf.d/10-tare.conf` | `sendfile`、`tcp_nopush`、`tcp_nodelay`、`keepalive_requests 10000` |
| `upstream-keepalive.conf` | 問題のsite設定へ差し込む | upstreamの`keepalive 128`、`proxy_http_version 1.1`、`Connection ""` |
| `nginx-limits.conf` | `/etc/systemd/system/nginx.service.d/limits.conf` | `LimitNOFILE=65535` |

`nginx.conf`のmainとeventsはconf.dに書けないので、次をnginx.confへ直接反映する（`config/nginx/nginx.conf`をsync itemにしてdeployする）。

```nginx
worker_processes auto;
worker_rlimit_nofile 65535;
events { worker_connections 4096; }
```

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
