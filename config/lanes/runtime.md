# lane: runtime

あなたは`make kickoff`が先行回収したRustアプリに、問題に依存しない実行時の設定（log、allocator、socket、DB接続、lock）の修正を先行して実装する。SQLと処理の中身はlane `indexes`・`n-plus-one`・`obvious-fixes`が、全メモリ化はlane `full-memory`が同時に行う。mainのセッションは同時に環境構築と初回ベンチ（baseline）を進め、baselineの観測と照合してから、このbranchのcommitを選んで取り込み、deployして測る。どれも数行の変更なので、速さを優先して短く終える（時間の上限は20分）。

## 前提

- 作業場所はこのworktreeとbranchだけ。`AGENTS.md`の規則に従う（練習中で`practice.md`があればそれにも従う）。remote node・deploy・ベンチ・isuscopeの記録操作・`.local/operation.lock`には触れない（hookが止める）。baseline前の変更をremoteへ反映しない規則は、mainが取り込み時に守る。
- 変更してよいのは`webapp/`のアプリのコード（起動処理、`Cargo.toml`と`Cargo.lock`）だけ。schema、`config/`、`scripts/`、他の文書は変えない。
- 他のlaneも起動処理（`main.rs`など）に行を足す。mergeで衝突しにくいよう、直すのに要る行だけを変え、周りを書き換えない（整形し直さない、`cargo fmt`をcrate全体へかけない）。

## 対象（1件1commit、上から順に）

1. **requestごとのlog。** 既定のlog levelがinfo・debugで、requestごとに1行以上書いている（`RUST_LOG`の既定値、tracingのsubscriber、`TraceLayer`やloggerのmiddleware、handler内の`info!`・`debug!`・`println!`）なら、既定をwarnにし、requestごとのmiddlewareを外す。warn・errorのlogと、起動時に1回だけ出るlogは残す。isuscopeの状態ログ（lane `state-log`が足す`state_log`の呼び出し）はtracingを通らないので触らない。環境変数`RUST_LOG`で戻せる形にする。
2. **DB接続。** DBへの接続がTLSを使い得るなら（sqlxのMySQLは既定で`ssl_mode Preferred`で、MySQL 8は自動で証明書を持つので、同じVPCの別nodeでも暗号化する）、`ssl_mode`を`Disabled`にする。接続poolの上限が同時に来るrequestの数より明らかに小さい（既定の10など）なら上げ、取り出しのたびのping（sqlxの`test_before_acquire`）が既定で有効なら止める。上限はMySQLの`max_connections`（`make tare`で1000）とアプリのprocess数の積が超えない値にし、値と理由をcommit本文に書く。
3. **allocator。** global allocatorを`mimalloc`にする（`#[global_allocator]`、`default-features = false`）。crateを取得できなければ入れずに最後の回答に書く。
4. **TCP_NODELAY。** HTTP serverが受けたsocketにTCP_NODELAYを付けていなければ付ける（axumは`axum::serve(...).tcp_nodelay(true)`、自前のacceptなら`set_nodelay(true)`）。frameworkが既定で付けているなら何もしない（actix-webは付ける）。
5. **async runtimeのMutex。** `tokio::sync::Mutex`・`RwLock`で守る区間に`.await`が無く短い（採番、小さなmapの読み書きなど）なら、`std::sync::Mutex`（または依存にあれば`parking_lot`）へ替える。区間の中で`.await`する、またはlockを持ったまま外部呼び出しやDBを待つものは替えない。

上の5つ以外（threadの数、HTTP clientの使い回し、serializeの改善、hash関数の変更など）は実装しない。見つけたら最後の回答に候補として書く。HTTP clientの使い回しはlane `obvious-fixes`の担当。

## 守ること

- **応答を変えない。** 返す値・件数・並び順・エラーとstatus codeを元のコードと一致させる。
- **1つの修正を1つのcommitにする。** mainが選んで取り込めるように、commit同士の依存を避ける。該当しない項目はcommitしない。commit messageは英語で、1行目に変更、本文に直した箇所（`file:line`）と期待する観測値（どのnodeのどのCPU・待ちが減るか）を書く。
- commitの前に`cargo build --release`（または`cargo check`）と既存のtestを通す。`config/local/compose.yaml`があれば`make local-up`と`make local-check`で動作も確かめ、終わったら`make local-down`する。確かめられなかったことはcommit本文に書く。

## 終わり方

最後の回答は日本語で、次を短く並べる。

1. commitごとに: hash、変更、期待効果、リスク、確認したこと
2. 該当しなかった項目（例: 既にwarn、frameworkが既にnodelay）と、実装しなかった候補とその理由・根拠の`file:line`

mainのセッションは`git log lane/runtime`とこの回答を読み、baselineの観測と照合して取り込む。
