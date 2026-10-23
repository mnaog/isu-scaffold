# 終了前: 最終構成を固定する

人間の指示で終了前に入ったら新しい変更を止め、最良の構成を、再起動後の追試でも同じように動く状態に固定する。最も高いスコアより、正しく再現できる最良の構成を選ぶ。終了直前は、失格や障害を防ぐ小さな修正以外で構成を変えない。

## 1. 公式の条件を確かめる

当日の公式文書から次を確かめ、原文を`docs/official/`へ保存する。過去の大会と同じだと決めつけない。

- 最終スコアに使うベンチ、追試の手順と必要なスコアの再現率
- envcheckなどの環境検査、再起動後に確認される永続データ、フロントエンドの表示確認
- 採点対象のサーバー、台数・インスタンスなどの制約、壊してはいけないservice・管理ユーザー・SSH設定
- 競技終了後に禁止される操作

確かめた値は`config/final-check.json`へ書いてcommitする。`make final-check`・`make final-reboot`はこれを使い、空の項目は確かめずにそう表示する。

| 項目 | 当日マニュアルの何から書くか |
| --- | --- |
| `envcheck_command` | 環境検査のcommand。再起動の後に全nodeで実行する |
| `protected_units`・`admin_users` | 変えると失格になるunitと管理ユーザー（例: `envcheck.service`、`isuadmin`）。enabledであること、ユーザーと`authorized_keys`があることを確かめる |
| `http_checks` | 追試で見る画面（node_group、URL、Hostヘッダー、期待するstatus）。initializeを呼ばずに確かめる |
| `instance_type`・`instance_count` | 指定のインスタンス種別と台数。EC2のAPIで照合する |
| `min_score_ratio` | 再現スコアのしきい値。既定の0.85は、過去の追試の規定（ISUCON13・14は75%以下でfail、12予選は85%）の厳しい方。当日マニュアルに規定があれば、その値へ書き換えること |
| `units` | sync.json・`config/systemd`・よくあるmiddleware（nginx、mysqlなど）以外に、再起動後に動いているべきunit |

## 2. 最終構成を選んで固定する

- isuscopeの履歴から、採用するrunとcommitを決める。未採用の実験や一時的な設定を取り除く（全サーバーがそのcommitの内容で動いているかは、`make final-check`がdeploy済みのcommitと照合する）。
- APIごとの待ちの表（`ISUCON_WAITS`）は最終構成の一部なので外さない。表はアプリのEnvironmentFileにあり、再起動の後も同じ表で動く。各runの`identity.waits`に、そのrunで効いていた表が残る（今の表は`python3 scripts/waits.py show`）。
- 計測を外す。NginxのLTSV access logと、有効にしていればMySQL slow logは、`ansible/playbooks/group_vars/all.yml`の`observability_nginx_ltsv`と`observability_mysql_slow_log`をfalseにし、bootstrapを該当tagだけ再実行して撤去する（手順は`docs/reference/automation.md`）。`make tare`がnginx.confの既定のaccess_logを外しているので、LTSVを外すときはnginx.confのhttpへ`access_log off;`を書く（httpに`access_log`が1つも無いと、nginxは組み込みの既定のlogを書く）。profiler、デバッグログ、調査用processも止める。Goのアプリの計測は、アプリのunitが読むEnvironmentFile（`ISUCON_WAITS`と同じfile）で`ISUSCOPE_STATE_LOG=`・`ISUSCOPE_APP_METRICS=`・`ISUCON_PROFILE_ADDR=`を空にして止め、`ISUCON_FLIGHT_RECORDER`は消す（どれも空なら何もしない。profileの窓口が聞いていれば`make final-check`が`Go profile endpoint`で知らせる）。PGOの`default.pgo`は残してよい（buildの最適化で、動いている間は何もしない）。障害調査に最低限要るログは残す。計測の有無を比べるためのベンチは回さない。
- `make final-check`を通す（再起動しない。数十秒）。FAILを全部直し、WARNは理由を確かめる。見る項目は次のとおり。
  - 手元: git（clean、push済み）、HEADとdeploy済みのcommit、最後のrunのPASSと、最後のrunと同じ構成のrunの最悪値が最後のスコアのしきい値以上か、EC2（全台running、同じVPC、台数・種別、security group）
  - 全node: 動いているのに自動起動しないunit・自動起動するのに動いていないunit・failedのunit、残った計測（LTSV log、slow log、pprofなどのprocess）、`nginx -t`、起動時のsysctl.dのfileと今の値の差（再起動で消える設定）、diskとメモリ、管理ユーザーと守るunit、`http_checks`

## 3. 最終ベンチと追試の再現

計測を外した最終構成で、通常の`isuscope run`を3回実行する。同じ構成の2回目以降は`--repeat "最終確認"`を付ける。3回目は、全サーバーをOS再起動した後に回し、主催者の追試を再現する（当日の公式手順を優先する）。再起動試験は、見つかった問題を直す時間を残して始める。

```text
make final-check
→ isuscope run（1回目）、isuscope run --repeat "最終確認"（2回目）
→ make final-reboot    全台を同時に再起動し、戻った後と5分後を確かめる
→ isuscope run --repeat "追試の再現"（3回目）
→ make final-reboot    3回目のベンチで書いたデータが、もう一度の再起動の後も残っているか
→ ブラウザでフロントエンドを確かめる
→ make final-check     最後のrunのPASSと再現率、git、全台running
```

`make final-reboot`がすること:

- 再起動の前に、DBのnodeで各表の行数を数えて`.local/final-snapshot.json`へ残す。
- DBの検出や行数の取得ができなければFAILにして、再起動を始めない。取得失敗を空のDBとして扱わない。
- 全台へ同時に`sudo systemctl reboot`を送り、boot IDが変わるまで待つ（`reboot_timeout_seconds`、既定600秒）。戻らないnodeはFAILにして、戻ったnodeは続けて確かめる。
- 戻ったら、`make final-check`のnodeの項目、`envcheck_command`、`http_checks`（initializeを呼ばない）、各表の行数が減っていないことを確かめる。
- `settle_seconds`（既定300秒）後に、unitをもう一度確かめる。遅れて落ちるものを捕まえるため。この確認は操作lockの外でbackgroundに行い、結果は`final settled`（通った）か`final failed`の行で届く（詳細は`.local/final-settle.log`）。待つ間もベンチやdeployはできる。`--settle 0`で飛ばせる。

行数は減っていないことしか確かめない。write-behindで書き込みを遅らせるアプリは、停止時に書き切ることまで確かめる（`docs/cache-design.md`）。主催者は再起動の後に長く置くこともある。

nodeの診断は、SSHの失敗・時間切れ・空の出力・途中で終わった出力もFAILにする。

- 3回のPASSと再現率は、最後の`make final-check`が出す。崩れた回があれば、原因を調べて直す。
- 整合性エラー・失格条件・タイムアウト、資源の余裕、ベンチ後のserviceの状態は、各runの`brief`で読める。
- メモリだけに持つデータや書き込み待ちのデータが再起動で失われないかは、`final-reboot`の行数の比較と3回目のrunで分かる（write-behindの書き切りは`docs/cache-design.md`）。
- 最終runをrun ID指定で分析する。

## 4. 終える

- 最終構成をcommitし、private GitHubリポジトリへpushする。`git status`をcleanにする。
- 公式の提出・終了手順を済ませる。
- 競技終了後は、公式アナウンスまでサーバーへ接続・操作せず、AWS環境を停止・削除しない。
