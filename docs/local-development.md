# ローカル実行環境

コード回収後、配布サーバーの準備と並行して、回収したアプリをDockerで起動する。構成（起動・確認の手順と手元で動かないものは`compose.yaml`の冒頭のコメント）は、`make kickoff`が起動するlane `local-env`（`config/lanes/local-env.md`）が作る（取り込みは`docs/guides/kickoff.md`）。初回baselineはローカル環境の完成を待たない。各worktreeで構成を用意し、検証する。準備で用意するのはDocker Composeと汎用コマンドだけで、問題のコードやデータを先読みしない。

## 構成する

1. `config/local/compose.example.yaml`を`config/local/compose.yaml`へコピーする。
2. DB版、追加service、アプリの環境変数、SQL投入元を実アプリに合わせる。`LOCAL_DB_IMAGE`と`LOCAL_GO_IMAGE`は未設定なら起動を拒否する。開始後に当日の環境を確認して設定する。`LOCAL_GO_IMAGE`はgo.modの`go`行に合う`golang`のimage（例: `golang:1.23-bookworm`）にする。
3. 許可されたschema・初期データを設定する。例では`.local/local-runtime/init/`のSQLをMySQLの初回起動時に投入する。`LOCAL_INIT_DIR`で別ディレクトリも指定できる。DB volumeが既存なら自動で再投入しない。
4. 必要なOS依存はDockerfileへ宣言する。`EXTRA_PACKAGES` build argも使える。初期化scriptが必要とするDB client・ファイル配置・権限も確認する。
5. 構成をcommitする。秘密情報はtrackedなComposeへ書かず、ローカル専用の値か`.local/`からの注入を使う。

標準の入口はservice名`app`、container port `8080`、HTTP `GET /health`である。異なるアプリはComposeと`LOCAL_HEALTH_PATH`を合わせる。`local-check`は全serviceのrunning/health状態とHTTP 200を検査する。代表APIや初期化の検証はアプリごとに追加し、health確認だけで完了としない。

## 実行する

```bash
# 開始後にLOCAL_DB_IMAGEとLOCAL_GO_IMAGEをexportしてから実行
LOCAL_DATA_LEVEL=schema make local-up
make local-check
make local-logs
make local-down
# このworktree専用のDBとbuild cacheを削除して初期化し直す場合:
LOCAL_RESET=yes make local-reset
```

`local-up`は未commitのコードも含む現在の`webapp/`をread-onlyでmountし、GoをLinuxのcontainerでbuildして起動する。編集後は再度`local-up`でアプリを作り直す。他のserviceが依存し`build`を持たないservice（DB・cacheなど）は作り直さず、healthyになるまで待ってから残りを作り直す。Composeの設定を変えればCompose自身が作り直すが、bind mountした設定fileの中身を変えたときは`local-down`してから`local-up`する。`local-down`はデータとcacheを保持する。操作はworktree内の専用lockで直列化され、remote操作のlockは取らない。

DB・Goのcache volumeとnetworkはworktreeの絶対pathから作るCompose projectごとに分離する。固定container名、external resource、host network、privileged実行、書込み可能なbind mount、固定公開portを拒否する。アプリの公開portは127.0.0.1上で自動割当され、URLは標準出力と`.local/local-runtime/status.json`に残る。DBはhostへ公開しない。worktreeを削除する前に、そのworktreeで`local-down`（不要データも消すなら`local-reset`）を実行する。

起動の待機上限は既定600秒で、`LOCAL_START_TIMEOUT`で変更できる。失敗時はcontainerを残してログを調べられるようにする。停止は`local-down`で行う。

`local-up`のGoのbuild cacheとmodule cacheはworktree専用のLinux volumeへ保存する。ローカル開発用のcacheは、`make build`のdeploy用build cacheとは別である。deploy用cacheは引き続きルートrepoで共有する。

## 同じ操作を流して比べる

書き換えの前と後で挙動が変わっていないかは、決まった操作の列を両方の版に流し、アプリの状態ログを比べて確かめる。

```bash
make local-flow FLOW=config/local/flows/<流れ>.json      # 操作の列を流す（形はscripts/local-flow.pyの冒頭）
make local-state-log NAME=before                        # アプリのcontainerから状態ログを取り出す
# 書き換えてmake local-upし、同じ流れを流してNAME=after
python3 scripts/state-log-compare.py .local/local-runtime/state-log/before.jsonl .local/local-runtime/state-log/after.jsonl
make local-flow FLOW=<後半>.json FROM=<前半>               # 前半の値とcookieから続ける（間にmake local-upで再起動）
```

`local-flow`は`local-up`が記録したloopbackのURLにだけ送る。`local-state-log`は各serviceの`ISUSCOPE_STATE_LOG`（無ければ`/var/tmp/isuscope-state.jsonl`）を`docker compose cp`で取り出す。`local-up`はアプリのcontainerを作り直すので、状態ログは`local-up`のたびに空から始まる。比べるのは、種類ごとに、同じ数の対象が同じ順番で状態を通ったかで、IDの違いは見ない。

## 検証範囲を記録する

`LOCAL_DATA_LEVEL`に`schema`、`subset`、`full`などを指定する。これは操作者の申告であり、コマンドがデータの完全性を保証するものではない。起動記録にはcommit・webappの未commit変更の有無も保存する。起動後に編集した場合は再起動・再検証する。

schemaのみならAPIのデータ依存部分や初期化を検証できない場合がある。フルデータ未取得、必要な外部service未構築、初期化script非対応などは検証結果に明記する。ローカルでは挙動比較・SQL調査・回帰確認を進め、性能の採否は配布サーバーでの共有ベンチで決める。
