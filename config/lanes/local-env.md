# lane: local-env

あなたは`make kickoff`が先行回収したアプリを、ローカルのDockerで動かす構成を作る。mainのセッションは同時にベンチ接続と初回ベンチを準備しており、他のlane（`indexes`、`obvious-fixes`）とmainは、この構成をmergeした後に`make local-up`・`make local-check`で修正を確かめる。手順と制約は`docs/local-development.md`が正。

## 前提

- 作業場所はこのworktreeとbranchだけ。`AGENTS.md`の規則に従う（練習中で`practice.md`があればそれにも従う）。remote node・deploy・ベンチ・isuscopeの記録操作・`.local/operation.lock`には触れない（hookが止める）。
- 変更してよいのは`config/local/`と`docs/local-verification.md`だけ。アプリのコード・schemaは変えない。
- 初期データは許可された取得元（回収済みの`webapp/sql`など）だけを使う。サーバーからデータを取りに行かない。

## 作ること

1. `config/local/compose.example.yaml`を`config/local/compose.yaml`へコピーし、回収したコード（`webapp/rust`のCargo.toml・接続設定の読み方・listen port・必要な環境変数）とschema（`webapp/sql`）に合わせる。DBの版は回収した設定やschemaの書き方から合うものを選ぶ。
2. **mainが`make local-up`だけで起動できる形にする。** `LOCAL_DB_IMAGE`・`LOCAL_RUST_IMAGE`などは`compose.yaml`へ具体的な値を書く（環境変数や`.local/`への手作業を要求しない）。schemaと初期化SQLはcommitされたfileから読み込む。
3. healthの経路が無いアプリなら、`LOCAL_HEALTH_PATH`に相当する既存のGETの経路を`compose.yaml`の近くに記し、代表的なAPI（initializeを含む）を`curl`で叩いて確かめる手順も書く。
4. `make local-up`、`make local-check`を通す。失敗したら`make local-logs`で原因を見て直す。終わったら`make local-down`する。
5. `docs/local-verification.md`へ、`config/records.json`の「ローカル検証の範囲」の定義どおり、データの範囲（schemaだけ・縮小・全量）、欠けているservice、確かめたこととまだ確かめていないこと、起動・確認のコマンドを書く。healthだけ通った状態をシナリオ検証済みと書かない。

小さく確実に進め、動いた段階でcommitする（構成、検証範囲の文書の順）。commit messageは英語。

## 終わり方

最後の回答は日本語で、起動と確認のコマンド、確かめたAPI、確かめられなかったことと理由を短く並べる。mainのセッションは`git merge lane/local-env`で取り込む。
