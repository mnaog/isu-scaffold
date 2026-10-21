# lane: static-edge

あなたは、完全importで回収したnginxの設定とアプリのコードを読み、静的ファイルの配り方だけを先行して直す。mainのセッションは完全importのcommitの直後にこのlaneを起動し、同時に初回ベンチ（baseline）を進める。baselineの観測（静的ファイルへの要求の回数、304の割合、入口nodeのnginxのCPU、送信量）と照合してから、このbranchのcommitを選んで取り込み、deployして測る。速さを優先し、1件ずつcommitする（時間の上限は30分）。

## 前提

- 作業場所はこのworktreeとbranchだけ。`AGENTS.md`の規則に従う（練習中で`practice.md`があればそれにも従う）。remote node・deploy・ベンチ・isuscopeの記録操作・`.local/operation.lock`には触れない（hookが止める）。
- 変更してよいのは`config/nginx/`のsiteとconf.dの設定（`config/sync.json`で`role_nginx`へ配るもの）と、下の4.のときだけ`config/sync.json`の`post_deploy_commands`。`nginx.conf`、アプリのコード、schema、他の文書は変えない。
- `make tare`（`scripts/tare.py`）が同じsiteの設定の`listen`（backlog）と`proxy_pass`（keepalive付きのupstream）を書き換え、`sync.json`のcommandを足す。mergeで衝突しにくいよう、`listen`・`proxy_pass`・`upstream`の行とその前後の行を変えず、静的ファイルの`location`の中か、新しい`location`を足すだけにする。整形し直さない。
- 計測用のLTSV access log（`conf.d/00-isuscope-log.conf`）はbootstrapが置く。server・locationへ`access_log`を書くと、そこへの要求がisuscopeの計測から消えるので書かない。
- 公式資料（`docs/official/`）と`docs/benchmark-scenario.md`があれば先に読む。キャッシュや圧縮についての規則（禁止、ベンチが検証する応答header、内容が変わる静的ファイル）があれば、それに反するものは入れない。

## 対象（1件1commit、上から順に）

1. **アプリが返している静的ファイル。** nginxがアプリへ転送しているpathのうち、アプリがファイルをそのまま返すもの（`ServeDir`・`NamedFile`・`include_bytes!`、公開directoryからの読み出しなど）を、nginxの`root`・`try_files`で配る。ファイルの場所がアプリの設定から分かり、nginxと同じnodeにあるときだけにする。nginxのuser（`www-data`など）がそのdirectoryを読めるか（home directoryの権限）は、mainがnodeで確かめる項目に書く。利用者が後から上げるファイル（画像のuploadなど）で、更新や権限の判定をアプリがしているものは移さない。
2. **長く変わらないasset。** 名前にhashを含むasset（`/assets/index-3f9a2c.js`のような名前を、HTMLやbuild設定から確かめる）に`expires max`か`Cache-Control: public, max-age=31536000, immutable`を付ける。
3. **それ以外の静的ファイル。** SPAの入口のHTML、favicon、名前にhashの無い画像・JS・CSSに`expires 1d`を付け、ブラウザ相当のclientが304の再検証を毎回しないようにする。内容がベンチの途中で変わり得るものは除く。2.と別のcommitにする。
4. **事前圧縮（gzip_static）。** 静的なJS・CSS・SVG・HTML・JSONに`gzip_static on`を付け、`.gz`をdeployのたびに作るcommandを`post_deploy_commands`の最後へ足す（`role_nginx`、`"when": ["nginx-*"]`、例: `sudo find <root> -type f \( -name '*.js' -o -name '*.css' -o -name '*.svg' -o -name '*.html' -o -name '*.json' \) -exec gzip -kf9 {} +`。`<root>`はnginxの設定の値）。ベンチが`Accept-Encoding: gzip`を送らなければ効かず、送っても点が下がることがあるので、他と混ぜず最後の別のcommitにする。
5. **open_file_cache。** 静的ファイルを配るserverに`open_file_cache max=10000 inactive=60s;`などを足す。fdの上限（`worker_rlimit_nofile`）を上げずに入れると枯渇するので、`make tare`の後に取り込むものとしてcommit本文に書く。

APIの応答のgzip、HTTP/2、TLSの設定、`proxy_buffering`、workerの数は実装しない。見つけたら最後の回答に候補として書く。

## 守ること

- **応答の中身を変えない。** 返すbody、status code、Content-Typeを元と一致させる（SPAのfallbackなど、`try_files`で返していたものを同じにする）。
- **1つの修正を1つのcommitにする。** commit messageは英語で、1行目に変更、本文に対象のpathとlocation（`file:line`）と期待する観測値（どのpathの要求・304・送信量、入口nginxのCPUの何が減るか）を書く。
- このlaneはnginxを起動できない。書式を`config/nginx/`の既存の設定と照らして確かめ、deployの`nginx -t`に任せることをcommit本文に書く。静的ファイルが手元に無く、名前や場所をnodeで確かめる必要があるものは、mainが確かめる項目として最後の回答に書く。

## 終わり方

最後の回答は日本語で、次を短く並べる。

1. commitごとに: hash、対象のpathとlocation、期待効果、リスク、確認したこと、`make tare`との順序
2. mainがnodeで確かめる項目（静的ファイルの場所と名前、hashの有無）
3. 実装しなかった候補とその理由・根拠の`file:line`

mainのセッションは`git log lane/static-edge`とこの回答を読み、baselineの観測と照合して取り込む。
