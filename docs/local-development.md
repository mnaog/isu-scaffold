# Phase 1のローカル実行環境

コード回収後、配布サーバーの準備と並行して、回収したアプリをDockerで起動する。初回baselineはローカル環境の完成を待たない。各worktreeで構成を用意し、検証する。Phase 0で準備するのはDocker Composeと汎用コマンドだけで、問題のコードやデータを先読みしない。

## 構成する

1. `config/local/compose.example.yaml`を`config/local/compose.yaml`へコピーする。
2. Rust版、DB版、追加service、アプリの環境変数、SQL投入元を実アプリに合わせる。`LOCAL_DB_IMAGE`と`LOCAL_RUST_IMAGE`は未設定なら起動を拒否する。Phase 1で当日の環境を確認して設定する。
3. 許可されたschema・初期データを設定する。例では`.local/local-runtime/init/`のSQLをMySQLの初回起動時に投入する。`LOCAL_INIT_DIR`で別ディレクトリも指定できる。DB volumeが既存なら自動で再投入しない。
4. 必要なOS依存はDockerfileへ宣言する。`EXTRA_PACKAGES` build argも使える。初期化scriptが必要とするDB client・ファイル配置・権限も確認する。
5. 構成をcommitする。秘密情報はtrackedなComposeへ書かず、ローカル専用の値か`.local/`からの注入を使う。

標準の入口はservice名`app`、container port `8080`、HTTP `GET /health`である。異なるアプリはComposeと`LOCAL_HEALTH_PATH`を合わせる。`local-check`は全serviceのrunning/health状態とHTTP 200を検査する。代表APIや初期化の検証はアプリごとに追加し、health確認だけで完了としない。

## 実行する

```bash
# Phase 1でLOCAL_DB_IMAGEとLOCAL_RUST_IMAGEをexportした後に実行
LOCAL_DATA_LEVEL=schema make local-up
make local-check
make local-logs
make local-down
# このworktree専用のDBとbuild cacheを削除して初期化し直す場合:
LOCAL_RESET=yes make local-reset
```

`local-up`は未commitのコードも含む現在の`webapp/`をread-onlyでmountし、RustをLinuxのnative CPUでbuildして起動する。編集後は再度`local-up`でアプリを作り直す。`local-down`はデータとcacheを保持する。操作はworktree内の専用lockで直列化され、remote操作のlockは取らない。

DB・Cargo・target volumeとnetworkはworktreeの絶対pathから作るCompose projectごとに分離する。固定container名、external resource、host network、privileged実行、書込み可能なbind mount、固定公開portを拒否する。アプリの公開portは127.0.0.1上で自動割当され、URLは標準出力と`.local/local-runtime/status.json`に残る。DBはhostへ公開しない。worktreeを削除する前に、そのworktreeで`local-down`（不要データも消すなら`local-reset`）を実行する。

起動の待機上限は既定600秒で、`LOCAL_START_TIMEOUT`で変更できる。失敗時はcontainerを残してログを調べられるようにする。停止は`local-down`で行う。

事前取得済みvendorを使う場合は`LOCAL_USE_VENDOR=1 LOCAL_VENDOR_DIR=/absolute/vendor make local-up`とする。通常はCargoが依存を取得し、worktree専用Linux volumeへ保存する。ローカル開発用のnative build cacheは、`make build`のdeploy用cross build cacheとは別である。deploy用cacheは引き続きルートrepoで共有する。

## 検証範囲を記録する

`LOCAL_DATA_LEVEL`に`schema`、`subset`、`full`などを指定する。これは操作者の申告であり、コマンドがデータの完全性を保証するものではない。起動記録にはcommit・webappの未commit変更の有無も保存する。起動後に編集した場合は再起動・再検証する。

schemaのみならAPIのデータ依存部分や初期化を検証できない場合がある。フルデータ未取得、必要な外部service未構築、初期化script非対応などは検証結果に明記する。ローカルでは挙動比較・SQL調査・回帰確認を進め、性能の採否は配布サーバーでの共有ベンチで決める。
