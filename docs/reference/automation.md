# 自動化の仕様（kickoff・deploy・rollback・ベンチ接続）

大会ごとに変わる接続先、配布path、ベンチ起動方法だけを宣言し、node発見からisuscopeの初回`survey-run`直前までを再現可能にします。生成されるIP、SSH秘密鍵、調査結果は`.local/`へ置き、コード、同期規則、ベンチ接続はGitへ残します。

`discover`、`bootstrap`、`inspect`、設定draft生成・反映、`import`、`deploy`、`rollback`、ベンチ実行などの変更系操作は`.local/operation.lock`を共有します。scriptは`isuscope lock --path .local/operation.lock -- <script>`で自分自身を実行し直し、`isuscope run`/`survey-run`は`make discover`が書き出す`[lock] path`で同じlockを取ります。複数セッションから同時に開始した場合、後から来た操作は実行中のPID・開始時刻・操作名を表示して終了します。lockは`flock`で握るので、processが落ちればkernelが外し、古いlockは残りません。lock file自体は解放後も残ります。

## 1. local設定を作る

```bash
mkdir -p .local
cp config/environment.example.env .local/environment.env
```

kickoffは次の2コマンドで進めます。採用言語はRustに固定しており、`config/application.env`の`APPLICATION_LANGUAGE=rust`、`APPLICATION_PATH=webapp/rust`を使います。問題にRust実装がない場合だけ、このファイルを書き換えてから始めます。

```bash
make kickoff   # バックグラウンドで実行し、python3 scripts/progress.py follow main --from-start を追う
# 先行回収・commit後に、kickoff buildの調査とAI lane（config/lanes.jsonのkickoff）が並行で始まる
# 実行中に: 公式資料を保存・commitして python3 scripts/lane.py start scenario
# 実行中に: config/benchmark.env・benchmark-contract、benchmark.sh --check/--probe
# 実行中に: 自動生成されたbuild設定を確認してcommit（設定待ちなら作る）
# .local/draft/review.mdのFAILを直し、WARNを判断する
CONFIRM_DRAFT=true make kickoff-apply
# importを確認して初期状態をcommitし、python3 scripts/lane.py start static-edge
make first-bench   # bench-check → 配布状態のままsurvey-run → deploy（配布状態で測れなければdeploy → survey-run）
```

`kickoff`は最初に、実行したClaude Codeのセッションがこのrepoの`docs/agent-history/`へ会話を記録しているかを`scripts/check-agent-session.py`で確かめます。別のdirectoryで起動したセッションは、ここで止まります（ベンチをこの会話へ結び付けられず、`bench-check`の`isuscope doctor`で止まるため）。Claude Codeの外の端末から実行した場合は確かめません。続いてnode発見後、回収元の1台（既定では最初のapplication node）だけSSHを確立し、`/home/isucon/webapp/rust`と`/home/isucon/webapp/sql`のDDL・初期化script（`.sql`、`.sh`、`.py`、`.rb`、`.pl`）だけを先行回収します。1ファイル1MiB・合計16MiBを超えるものやそれ以外の初期データは後回しにし、`.local/code-schema-manifest.log`へ`INCLUDED`／`DEFERRED`として記録します。上限は`CODE_SCHEMA_MAX_FILE_BYTES`、`CODE_SCHEMA_MAX_TOTAL_BYTES`で変更できます。生成物の`target`、`node_modules`、`.git`は除外します。pathが異なる場合は`CODE_SOURCE_NODE`、`CODE_REMOTE_PATH`、`CODE_SCHEMA_REMOTE_PATH`、`CODE_SCHEMA_PATH`を環境変数で明示します。回収した`config/application.env`、`webapp/rust`、`webapp/sql`だけを自動commitし、他の未commit変更は含めません。この回収はコード読解開始用の暫定snapshotであり、全node一致の保証は後続の完全importが担当します。残りのnodeのSSH確立はこの転送と並行して行い（log は`.local/kickoff-other-ssh.log`）、同じkickoffの`bootstrap`はnodeの発見をやり直さず、全nodeへ届いていればSSHの確認も飛ばします。届かなかったnodeがあれば`bootstrap`のSSH段階が改めて確立し、失敗を報告します。

続いてkickoff build処理が1台目のCPU・OS・glibc・OpenSSL・Rust版を調査し、回収したCargo.toml・Cargo.lockと合わせてbuild設定を提案します。判断できれば`config/kickoff-build.json`を書いてすぐbuildを始め、判断できなければ理由を`.local/kickoff-build/proposal.json`へ残して設定を待ちます（下の「kickoff build」）。同時に`config/lanes.json`の`kickoff`にあるAI laneを起動します。その後、全nodeのSSH確立、Ansible導入、初期収束、inspection、draft生成を行い、最後に`scripts/review-draft.py`でdraftを実nodeと照合します。

### 進行の通知

`kickoff`、kickoff build処理、AI laneは、次に操作できることを`.local/progress.log`へ1行ずつ書きます（開始時刻、出どころ、状態、内容をtab区切り）。worktreeから書いてもルートrepoのfileへ集まります。kickoffの終了を待たず、この行を見て次の操作へ移ります。

どの行を誰が読み、読んだら何をするかは`config/progress.json`の宛先の表にあり、mainとimproveは`python3 scripts/progress.py follow <main|improve>`でその役割宛ての行だけを読みます（下の「投稿のキュー」の知らせも同じ）。

### AI lane

並行させるAIは、すべてClaude Codeのheadless実行（`claude -p`）にします。`scripts/lane.py`がlaneごとに`make worktree`と同じ方法でworktreeとbranch（`lane/<名前>`、場所はルートrepoの隣の`<repo>-<名前>`）を作り、`config/lanes/<名前>.md`のpromptをstdinで渡して1回だけ実行します。laneはそのbranchへcommitして終わり、mainのセッションが結果を読んでmergeします。

```bash
make lanes                                   # 全laneの状態
python3 scripts/lane.py status scenario      # status.jsonと最後の回答
python3 scripts/lane.py start <名前> --prompt-file <path> --purpose "..."   # 任意のlane
python3 scripts/lane.py stop <名前>
git merge lane/scenario                      # 取り込みはmainのセッションが判断して行う
```

- **設定**: `config/lanes.json`の`claude`にcommand、model（既定`claude-opus-5-5`）、effort（既定`high`）、permission mode、`allowed_tools`、時間上限を置きます。permission modeは`dontAsk`で、`allowed_tools`にないtool（Web、remote command、任意のshell）は確認なしで拒否されます。人間が途中で承認しないので、laneが自分の権限を広げられないようにするためです。laneごとの定義には、共通の`allowed_tools`へ足す`extra_allowed_tools`と、起動元のcommitに必要なfileの`requires`を書けます。
- **remote操作の禁止**: laneは環境変数`ISUCON_LANE`付きで動きます。`scripts/tool-guard.py`はこれを見て、SSH・コピー、Ansible、deploy・import・kickoffなどremoteへ触れるscriptとmake target、ベンチ、isuscopeの記録系command、`.local/operation.lock`、`git push`、laneやAI CLIの起動を拒否します。
- **起動**: `make kickoff`は先行回収のcommit直後に、`config/lanes.json`の`kickoff`のlaneを起動します。各laneが変えてよい範囲と終え方は、そのprompt（`config/lanes/<名前>.md`）にあります。`scenario`（`docs/benchmark-scenario.md`）は公式資料が要るので、mainが`docs/official/`をcommitした直後に`python3 scripts/lane.py start scenario`で起動します。`static-edge`（静的ファイルの配り方）はnginxの設定が要るので`requires`に`config/nginx/nginx.conf`があり、mainが完全importをcommitした直後に`python3 scripts/lane.py start static-edge`で起動します。`joins`にあるlane（`full-memory`の`state-log`・`local-env`）は、lane自身が終わったかを`python3 scripts/lane.py status <名前>`で見て`git merge`します（`joins`があるlaneには`lane.py status`と`git merge`が許可されます）。それらのcommitは、そのlaneのcommitとして数えません。`requires`（開始commitに要るfile）は指定したlaneにだけ効き、`extra_allowed_tools`でlaneごとにtoolを足せます（`full-memory`は`git merge`・`git switch`）。laneのcommit一覧と`lane:<名前> commit`の通知には、取り込んだmainのcommitを含めません。laneは起動時のbranchから分かれるので、commitの前に起動すると公式資料が見えません。実行中は15秒ごと（`LANE_POLL_SECONDS`）にbranchを見て、新しいcommitを`lane:<名前> commit`として知らせます。完了・実行中のlaneは再実行しません。`KICKOFF_LANES=`で止め、`KICKOFF_LANES=indexes`で選べます。laneの起動に失敗してもkickoffは続きます。mainがlaneをどの順でどう取り込むかは`docs/guides/kickoff.md`にあります。
- **記録**: `.local/lanes/<名前>/`に`status.json`（状態、branch、worktree、commit一覧、秒数、費用）、`stream.jsonl`（stream-json出力）、`result.md`（最後の回答）が残ります。Claudeが最後のcommitの後に書いた会話履歴（`docs/agent-history/`）は、lane.pyがbranchへcommitします。所要時間は`.local/stage-timing.tsv`へ`lane`として入ります。
- **時間上限**: laneごとの`timeout_minutes`を過ぎるとprocess groupを止めて`timeout`にします。それまでのcommitはbranchに残ります。
- **後始末**: mergeが済んだlaneは、worktreeで`make local-down`してから`git worktree remove`します。

手で並行作業を行う場合は`make worktree BRANCH=<name> PURPOSE="目的"`を使います。目的はbranchのdescriptionへ保存し、作業情報を`<worktree>/.local/lane.md`へ生成します。

### kickoff build

`make kickoff`は先行回収・commit後、独立したkickoff build処理を開始して全台bootstrapを進める。

build処理はremoteのCPU・glibc・OpenSSLと回収したCargo.toml・Cargo.lockから設定を提案し、`.local/kickoff-build/proposal.json`へ残す。判断できる場合は`config/kickoff-build.json`を書いてすぐbuildを始める（`configured`）。判断できる場合とは、単一binaryのpackageで、systemライブラリに依存する-sys crateがなく、`config/rust-builder/bases.json`にnodeのglibc以下（OpenSSLを使うならlibsslのmajorも一致）のbaseがあることを指す。自動生成された設定もcommitする。workspace、複数binary、`mysqlclient-sys`などのsystemライブラリ、合うbaseがない場合は理由を残して設定を待つ。`KICKOFF_BUILD_AUTO_CONFIG=0`なら常に待つ。

全台準備を待たず、`.local/progress.log`の`kickoff-build`の行か`.local/kickoff-build/status.json`と`environment.txt`を確認する。`needs_configuration`なら`config/kickoff-build.example.json`を参考に、回収したCargo.toml・Cargo.lock・toolchain指定、remoteのCPU・OS・glibc・Rust版、追加Cライブラリを照合して`config/kickoff-build.json`を作る。対象は`base_image`・`target`・`binary`・`dockerfile`。未記入のexampleを実設定へコピーしたままにしない。完成内容を一時ファイルに書き、renameで一括配置する。操作者自身の判断でよく、人間への追加承認は不要。

kickoff build処理は設定を最大30分待ち、設定完了後は既存の`local-build.py`を使って共有キャッシュへbuildする。設定が既にあれば調査・CPU照合後すぐbuildする。全台準備とbaselineはbuild処理の完了待ちを入れない。deployに必要なbuild完了は従来どおりdeployが保証する。

`configure-draft`はこのkickoff build設定をそのまま`local_builds`へ取り込む。draft生成より後に設定した場合は、設定済みとなった後にdraftを再生成するか、同じ設定をdraftへ反映してからreviewする。異なるimage・Dockerfile・targetに変える場合はキャッシュが別になることを認識する。設定とDockerfileは通常のcommitへ含める。

進行確認は`python3 scripts/kickoff-build.py status`、失敗後の再開は`python3 scripts/kickoff-build.py start`。ログは`.local/kickoff-build/build.log`。同時起動は専用lockで防ぐ。練習停止時は`python3 scripts/kickoff-build.py stop`でkickoff build処理も止める。停止してもソース・成果物・キャッシュは残す。ローカル検証用Composeはこのデプロイ用buildと別キャッシュであり、同時コンパイルによる端末の資源競合を避ける。

## mainの宣言とworktreeからの投稿

mainはrepoのrootで動くClaude Codeのセッション1つです。`/main`（`.claude/commands/main.md`）で宣言します。`scripts/main-session.py`はセッションID（`CLAUDE_CODE_SESSION_ID`）とroot worktreeのbranchを`.local/main-session.json`へ書きます。worktreeからは宣言できません。別のセッションが宣言し直すとmainが移り、前のmainを`previous`に残します。`show`で今のmain、`release`で宣言を外します。

```bash
python3 scripts/main-session.py show
python3 scripts/proposal.py submit --hypothesis "..." --expect "..." [--verified "..."] [--based-on <短縮ID>]   # worktreeで
make proposals                                  # 投稿の一覧（python3 scripts/proposal.py list [--all]）
python3 scripts/proposal.py show [<branch>]     # 1件の状態とmainの判断
python3 scripts/proposal.py wait [--timeout 3600]                       # improveが、キューの空きを待つ（background）
python3 scripts/proposal.py improve                                    # /improveが宣言する
python3 scripts/proposal.py pending                                     # mainが、まだ受け取っていない投稿を読む
python3 scripts/proposal.py sync [<branch> ...]                         # 待っている投稿をmainのbranchへ追従させる（ベンチの起動時にhookがbackgroundで始める）
python3 scripts/proposal.py hold <branch> --until "条件" / unhold <branch>   # mainが、条件付きで寝かせる・戻す
python3 scripts/proposal.py requeue [<branch> ...]                      # 測れなかったベンチの測定中を待ちへ戻す（次のベンチの起動時にhookも行う）
python3 scripts/proposal.py adopt                                       # isuscope analyzeの採否を投稿へ写す（通常はhookが行う）
python3 scripts/proposal.py decide <branch> accepted|rejected|deferred [--run <短縮ID>] [--note "理由"]   # mainが、runの無い判断を
python3 scripts/proposal.py freeze [--off] --note "..."                  # mainが、終了前にimproveの投稿を止める
scripts/runs.sh brief latest                    # worktreeから、rootのrunを読む（isuscopeの読み取りだけ）
python3 scripts/proposal.py withdraw [<branch>]
```

- **止めるもの**: 宣言がある間、`scripts/tool-guard.py`は、hookのpayloadのセッションIDとagentのセッションIDの環境変数のどれもmainと一致しない呼び出しから、mainだけの操作を止めます。対象はtool-guardの`MAIN_SCRIPTS`・`MAIN_MAKE_TARGETS`、`isuscope`の記録系command、`proposal.py`と`lane.py`のmainだけのsubcommand、`.local/operation.lock`です。宣言が無いとき、セッションIDが分からないときは止めません。
- **投稿**: worktreeで変更をcommitしてから`submit`します。root worktreeとmainのbranchからは投稿できず、commitしていない変更（`docs/agent-history/`を除く）があると止まります。投稿は`.local/proposals/<branch>.json`に、branch、commit、仮説、期待する観測、確かめたこと、根拠のrun（`--based-on`）、変えたfile、投稿者を残します。commitを足して投稿し直すと置き換わります。
- **mainへ渡す**: mainがベンチを`run_in_background`で起動すると、`bench-hook.py --post`が、そのセッションにまだ渡していないcommitの投稿を、`git merge-tree`でmainのbranchへ衝突なく入ると確かめたものだけ渡します。衝突するものは`needs-sync`にして衝突したfileを残し、渡しません（mainには、投稿者の追従待ちであることを1度だけ伝えます）。mainのbranchがcommitを含んだ投稿は`merged`になります。宣言がある間は、main以外のセッションには渡しません。ベンチが空いている間は、mainが`pending`で同じものを受け取ります（同じcommitは1度だけ）。
- **キュー**: ベンチを待つ投稿（`submitted`・`delivered`・`needs-sync`・`merged`）がキューです。mainがベンチを`run_in_background`で起動すると、`bench-hook.py --post`がmerge済み（`merged`）の投稿を`measuring`にしてキューから外します。`hold`した投稿（`held`）もキューに数えません。`hold`できるのは未mergeの投稿だけです。mainへ取り込み済み・測定中の投稿は、採否を決めて必要ならコードをrevertします。ベンチが起動できなかった・中断した・ベンチの外の原因で落ちたときは、`requeue`で測定中を待ち（mainのbranchにあれば`merged`）へ戻します。mainは投稿を好きな順で選び、好きに組み合わせて測ります（キューの並びは表示の順）。`/improve`で宣言したセッション（`.local/proposal-state/improve.json`）は、キュー全体の待ちが1件以下のときだけ新しい投稿を足せ（待っている自分の投稿の差し替えは足したことにならない）、それ以外は終了コード3で断られます。`wait`はキューに空きが出るまで2秒おきに投稿のfileを読み、空けば0、`--timeout`で2、`freeze`なら4で終わります。人間などimprove以外の投稿は制限しません。
- **追従**: 投稿したbranchは採否が出るまでmainのものです。`sync`は`.local/proposal-sync`のworktree（detached）で、待っている投稿と保留の各branchへmainのbranchを`--no-ff`でmergeし、投稿の`commit`をそのmergeへ進めます（mainが読んだ印も移すので、追従しただけでは渡し直しません）。衝突しないものから順に行い、衝突したら衝突をworktreeに残して止まります。解消して`git add`し、もう一度`sync`するとcommitして続けます。投稿の後にbranchが動いたもの、どこかのworktreeでcheckoutされているものは飛ばして理由を出します（`submit`は投稿者のworktreeをbranchから外すので、通常は起きません）。
- **積み重ね**: 取り込み前の投稿の上に積んだ投稿は、`submit`が`depends_on`に記録します。土台が`rejected`・`deferred`・取り下げになると、積んだ投稿も`deferred`でキューから外れます。
- **投稿者へ戻さない**: mainは、取り込めない投稿（衝突、取り込んで通らなくなったbuild・deploy）を直せるなら直し、直せないなら棄却します。`decide rejected`は`--run`なしでも使えるので、測るまでもなく棄却できる投稿はベンチにかけずに棄却します。
- **知らせ**: `proposal.py`とhookは、投稿の状態が変わるたびに`proposal <状態>`の行を`.local/progress.log`へ書きます（`improve`は`improve started`、`working`は`improve working`か`worktree working`、`freeze`は`main freeze`）。`main analyzed`は、mainの`isuscope analyze`の後にhook（`bench-hook.py --post`）が書きます。hookはまず、測定中の投稿ごとに、isuscopeのchange（IDはbranch名の`/`などを`-`に置き換えたもの。ベンチの起動時に投稿ごとに示す）に測定の開始より後に記録された採否を`decide`で写します（`provisional`はコードを残すので`accepted`）。測定中の投稿が残っていなければ、最後に記録された分析のrun・得点・冒頭を1回だけ書きます。ベンチの起動時に、前のrunで採否の付かなかった測定中の投稿（FAIL・中断のrunのもの）は待ちへ戻し、そのベンチで測り直します。測らずに棄却する、終了前に`deferred`にするなど、runの無い判断だけ`decide`を直接使います。どの行を誰が読むかは`config/progress.json`にあり、mainとimproveは`python3 scripts/progress.py follow <main|improve>`で自分宛ての行だけを読みます。
- **作業中**: worktree（improve・human・`make worktree`）は、作り始めたら`working "<狙う制約と変更>"`で`.local/proposal-state/working/<worktree名>.json`（名前は、repo名-の後ろ。`human`など）へ書き、ボードの「作業中」に出します。`submit`で消え、`working --done`でも消せます。worktreeが無くなったものは出しません。rootからは書けません（mainは実装しない）。
- **仮説**: `hypothesis`は、次のベンチへ取り込んだ（`merged`の）投稿の仮説と期待する観測値を`<branch>: <仮説> 期待: <観測値>`で並べます。mainは`isuscope run --hypothesis "$(python3 scripts/proposal.py hypothesis)"`で使い、runの仮説をベンチ待ちに出ていた文と同じにします。
- **runの読み取り**: isuscopeの設定（`.isuscope/config.toml`、discoverが書き、Gitに入れない）とrunはrootにしかありません。`scripts/runs.sh`は`ISUSCOPE_PROJECT`でrootを指してisuscopeを実行し、worktreeから同じrunを読めるようにします。別のprojectを指したisuscopeは読み取りのcommandだけを受け付けます。

- **判断**: `decide`の判断は、mainが取り込んで測ったcommitに付きます。mainが取り込んだ後に投稿者が同じbranchへ投稿し直していれば、判断は取り込んだ前のcommit（`history`）に付き、今の投稿は未判断のまま残ります。別のcommitに付けるときは`--commit`で指定します。`accepted`には測ったrun（`--run`）が要り、mainのbranchに入っていないcommitには付けられません。`merged`・`measuring`は取り込んだだけで終わりではないので、判断が返るまで`list`に残り、ベンチを起動するたびにmainへ「取り込んだが採否を返していない投稿」として示します。
- **表示**: `.claude/skills/isucon-main/`はClaude Codeのmod（function hooks）です。信頼したworkspaceで開くと`.claude/skills/`から自動で読み込まれます（`claude plugin list`で確かめ、読み込まれていなければ`/reload-plugins`）。10秒ごとと各turnの後に`python3 scripts/main-session.py badge`を呼び、mainのセッションのプロンプトの上に`★ MAIN`の帯と待っている投稿の数を、status lineに`★ MAIN`を出します。ほかのセッションには、先頭にworktreeの名前（`⎇ improve`など。directory名からrootの名前と`-`を除いたもの、rootなら`root`）を、続けて`WORKTREE`（rootなら`NOT MAIN`）、branch、mainのセッション、自分の投稿の状態を、宣言が無ければ`/main`の案内を出します。status lineにも同じworktreeの名前が出ます。表示のテストは`claude plugin test .claude/skills/isucon-main`、検査は`claude plugin validate .claude/skills/isucon-main`です。

Rustを採用しているため、draftは`webapp/`全体ではなく、`webapp/rust`（`sync.rust.example.json`のbuild設定とCargo.tomlのbinary名つき）、Rustのsystemd unit、`webapp/sql`内の1MiB以下の`.sql`・`.sh`、nginx・MySQL設定だけを同期対象に提案します。初期データはnodeに残し、配布しません。初期状態でRust用unitがない場合（他言語のunitだけが動いている場合）はrestart commandを入れずWARNにするので、unitを`config/systemd/`へ作ってitemとcommandを足してから切り替えます。

nginxは`nginx.conf`に加えて、`sites-enabled`のsymlinkが指す実体と`conf.d`の設定（計測用drop-inは除く）を提案し、Rust unitが読む環境変数file（`EnvironmentFile`、例: `/home/isucon/env.sh`）も`config/app/`へ回収します。秘密情報が入っていれば`.local/`へ移してからcommitします。unit fileを置き換えるので、post_deployとrollbackの最初に`sudo systemctl daemon-reload`を入れます。

Rust unitがあるのに、同じアプリの他言語のunit（例: `isuride-go.service`）が動いているnodeでは、切り替えの計画を`.local/draft/service-switch.json`へ出します。reviewはそのnodeのRust unitの停止をWARNにし、`kickoff-apply`が`scripts/switch-language-service.sh`で他言語のunitを止めてRust unitを有効化・起動します。公式マニュアルの切り替え手順と違わないかはWARNを読んで確かめます。

draft reviewはnodeごとに1回のSSHで事実を集め、次をFAILにします。

- 同期itemの配置先が`/`、`/etc`、`/home/isucon`などの広いsystem path、またはitem同士で重なる
- 配置先が存在しない、draftのdirectory／fileと種類が違う、ownerやgroupがnodeに存在しない
- 対象groupにnodeがいない、source nodeが対象groupに含まれない、roleのないnodeがある
- `post_deploy_commands`や`status_commands`で確認するserviceが動いていない

512MiBを超える配布item、読めないlog、draft生成時の警告、nginx／db roleの欠落、serverブロック内の`access_log`（`off`を含む。そのserverのリクエストは計測用LTSV logへ書かれない）はWARNです。nginxの計測用drop-inがないnodeはFAILです。

draft生成は、採用言語のコードからsessionを識別するheaderやcookie（`x-session`、`Cookie::build("app_session"`、`const SESSION_ID_KEY: &str = "SESSIONID"`など）を探し、`observability_nginx_session_source`の候補（例: `$http_x_session`、`$cookie_SESSIONID`）を`ansible-vars.json`へ入れてWARNで確認を促します。見つからなければ設定を促すWARNになります。`kickoff-apply`は反映後に計測用logのdrop-inだけを書き直します。アプリがSQLiteやPostgreSQLに依存する場合、またはPostgreSQLが動いている場合は、DB計測がMySQL slow logだけであることをWARNにします。inspectはapplication nodeだけを調べ、ベンチの台には接続しません（本番でベンチの台が参加者のaccountに置かれても、それは運営の台です）。inspectは各nodeのインスタンスの型（IMDSv2）・CPUの型番・数秒のCPU計測（1 thread、全vCPU、sha256）を`.local/inspection/<node>.json`へ残し、discoveryも`discovered-nodes.json`へ型を残します。reviewはそれを末尾の「Nodes」の表に並べ、型やCPUが混ざっているとき、同じ型で1 threadが15%以上遅いnodeがあるときにWARNを出します。結果は`.local/draft/review.md`へ残ります。FAILがある場合`kickoff`は非0で終わり、draftを直して`python3 scripts/review-draft.py`で再検査します。`kickoff-apply`は反映前にもう一度同じ検査を行い、FAILなら何も変更しません。WARNの判断は、`review.md`を読んだ操作者（人間またはその作業セッション）が行います。

`kickoff-apply`は検査済みdraftの反映、再discover、sync検査、完全import、採用言語の固定まで進めます。ベンチ前の検査（`make bench-check`）は`make first-bench`が最初に行います。deployで設定（nginx、MySQL、unit）を変えたときだけ単独で再実行します。個別の段階をやり直す場合は、以下の各節にある`scripts/`のscriptを直接実行します。kickoff・kickoff-apply・laneは初回`survey-run`、deploy、mergeを実行しません。

完全importをcommitしたら、`make first-bench`が`bench-check`、`isuscope survey-run --hypothesis "${HYPOTHESIS}"`（既定は「初期状態の負荷構造とベンチシナリオを記録する」）、`deploy`を続けて実行し、失敗した段階で止まります。

最初のdeployの前は、サーバーが完全importと同じ内容で動いています。importは各itemのdigestを`.local/imported-state.json`へ残し（nodeごとに内容が違うimportでは残しません）、`first-bench`は`scripts/imported-state.py`でHEADがまだその内容かを確かめ、`REQUIRE_ACTIVE=1 scripts/benchmark-prepare.sh`でアプリのunitが全nodeで動いてlistenするのを確かめます。どちらも通れば、そのcommitを`.local/current-import-commit`へ記録し、ローカルbuildとdeployを待たずに配布されたbuildのままsurvey-runを始めます。`check-deployed-commit.sh`は、deployの記録がない間はこの記録を稼働中のcommitとして照合します。survey-runの後に同じcommitをdeployするので、deploy経路は最初の改善の前に確かめられ、以後のrunは自前のbuildになります。survey-runの間に配る対象のfileを変えるcommit（`make tare`、laneのcommitの`cherry-pick`など）が入っていたら、測っていない変更を配らないようdeployを飛ばし（`deploy-skipped`）、次の`isuscope run`の前に`make deploy`します。どちらかが通らなければ`fallback`を出し、従来どおりdeployしてからsurvey-runします。`FIRST_BENCH_MODE=deploy`なら常にdeployが先です。AIが段階ごとに終了へ気づいて次を打つ待ち時間を消すためのもので、draftの判断とimportのcommitは前もって操作者が済ませます。`webapp/`か`config/`に未commitの変更があれば何もしません。lane（`ISUCON_LANE`）からは動かず、tool-guardも止めます。runへ会話を紐付けるため、mainのセッションから実行します。各段階は自分で操作lockを取るので、このcommand自体はlockを持ちません。進行は`.local/progress.log`の`first-bench`の行（`checked`、`distributed`／`fallback`、`baseline-ready`、`deployed`／`deploy-skipped`、`complete`、段階の`failed`）に出ます。`baseline-ready`が出たら、deployの終了を待たずに`isuscope brief latest`で分析を始めます。

ベンチの台には、既定では何も接続しません。`.local/environment.env`の`BENCHMARK_NODE_ACCESS`が`none`（既定）なら、`make discover`がinventoryの`all.vars.isucon_benchmark_node_access`へそれを書き、SSHの初期設定（鍵の登録を含む）はベンチの台を飛ばし、`scripts/run-ansible-playbook.sh`はどのplaybookにも`--limit 'all:!benchmark'`を付け、`scripts/ssh-node.sh`はベンチadapterからでもベンチの台へ接続しません。本番でベンチの台が参加者のaccountに置かれても、それは運営の台だからです。練習でベンチをベンチの台へのSSHで起動する（`BENCHMARK_TRANSPORT=ssh`）ときだけ`ssh`にし、`make discover`をやり直します（`ssh`でも、手での接続はhookが止めます）。`BENCHMARK_TRANSPORT=ssh`なのに`none`のままなら、`.isuscope/benchmark.sh --check`が止めます。

SSH確立はinventoryのnode名で重複排除し、`SSH_MAX_PARALLEL_NODES`（既定8。5台のapplication nodeとベンチの台2台でも1回で済む）台ずつ並列に行います。完全importはnodeごとに全itemのdigestを1回のSSHでまとめて計算し（`IMPORT_MAX_PARALLEL_NODES`、既定5）、localの内容がsourceのdigestと一致するitemは再転送しません。全itemのstagingと検証が終わるまで既存のlocalを置き換えず、途中で失敗した場合は元へ戻します。Ansible requirementsは内容hashが同じなら再installせず、bootstrap内のapplication nodeのfact収集も一度だけです。

lockを取る変更系操作は、終了時に`.local/operation-timing.tsv`へ開始時刻・操作名・秒数・終了codeを残します。外側のlockだけが記録されるので、`kickoff`・`bootstrap`・`kickoff-apply`の中の段階は`.local/stage-timing.tsv`へ同じ形式（開始時刻・操作名・段階・秒数・終了code）で残します。kickoff buildの状態ごとの滞在時間（設定待ちの`needs_configuration`、`building`など）も同じfileへ`kickoff-build`として入ります。Ansibleはtaskごとの経過時間（全hostの完了まで）を`.local/ansible-task-timing.tsv`へ残します（`ansible/callback_plugins/task_timing.py`、collection不要）。次回のkickoff改善はこれらの実測を根拠に判断します。練習の後は`python3 scripts/kickoff-report.py --start <練習・競技の開始時刻>`で、開始からbaselineまでの時間、上限（briefの`limits`）に当たったFAIL、isuscopeのベンチ後の処理の秒数、エージェントが確認で止まった回数と返事待ちの時間を、isuscopeと会話の記録からまとめて出します（`--hours`で区間、`--json`で機械向け）。

接続先の取得方法は2種類です。

- `DISCOVERY_PROVIDER=aws-cloudformation`: stack配下のEC2をName tagのregexで分類する
- `DISCOVERY_PROVIDER=static`: `config/nodes.example.json`を`.local/nodes.json`へコピーし、任意のSSH接続先を記述する

application nodeのインスタンスの型が2種類以上あるのに、ベンチの台（`group: benchmark`）が1台も無いと、`make discover`は型ごとのnodeを並べて止まります。型の違うnodeが混ざる構成では、片方（ネットワーク特化型など）が運営のベンチの台で、名前がappの正規表現に合っていることがあり、そのままではimportやdeployの対象にしてしまうためです。当日マニュアルとポータルで確かめ、ベンチの台なら`.local/node-overrides.json`で`{"name": "<node>", "group": "benchmark", "roles": ["benchmark"]}`にするか`DISCOVERY_BENCH_NAME_REGEX`を合わせ、全部が自分のnodeなら`DISCOVERY_ACCEPT_MIXED_TYPES=1`にして、`make discover`をやり直します。static providerでも`.local/nodes.json`に`instance_type`を書けば同じ検査をします。

SSH鍵が既に登録済みなら`SSH_BOOTSTRAP_METHOD=existing`、EC2 Instance ConnectからSSH鍵を登録するなら`eic`を使います。

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
| `.isuscope/config.toml` | `isuscope init --print config`とnode情報から作るisuscope実設定 |

`.isuscope/config.toml`にはIPとSSH identityが入るためGit管理しません。collectorの正本はisuscope側（`isuscope init`が配る設定）で、`make discover`が`isuscope init --print config`にlog pathとservice unitを渡して生成し、`[lock]`・`[ssh]`・`[[nodes]]`を追記します。collectorを足すときはisuscopeを更新してinstallし直します。IPやnode数が変わったら手編集せず`make discover`を再実行します。

負荷を担うsystemd unitが判明したら`.local/environment.env`の`ISUSCOPE_SERVICE_UNITS`へ空白区切りで指定します。`configure-draft`はinspectionで関連すると判断したrunning serviceの和集合も`.local/draft/isuscope.json`へ候補として出すため、`configure-apply`前に過不足を確認できます。生成されたservice-samplerはそのunitのcgroup v2だけを1秒間隔で読み、CPU core使用量、memory、read/write帯域、PID数を記録します。空の場合やcgroup v2でない環境では`unavailable`となります。同じunitについて、process-samplerは/procから全processのRSS合計・thread数を1秒ごとに読み、MainPIDの変化を再起動として数えます。こちらはcgroup v1でも動き、`brief`の`top_services`にピークと再起動回数が出ます。cgroupの`memory`（page cacheを含む）とRSSは別の値です。

## 3. SSHと全nodeを揃える

```bash
./scripts/bootstrap.sh
```

local venvへ固定versionのAnsibleを導入し、SSH接続を確立してから、Linux前提を全nodeへ、計測と運用の土台をapplication nodeへ冪等に適用します（`make kickoff`内でも実行されます）。性能は変えないので、初回baselineより前に入れて固定します。

Ansibleのcontrollerは対象nodeのPythonに合わせて選びます。`bootstrap.sh`はSSH確立後に`scripts/ansible-runtime.py`で全nodeの`/usr/bin/python3`の版を調べ、`legacy`（ansible-core 2.19、target Python 3.8〜3.13）か`modern`（ansible-core 2.21、target Python 3.9〜3.14）を`.local/ansible-runtime.env`へ記録します。両方に収まらない組み合わせはinventoryを分ける必要があるため停止します。依存版は`ansible/requirements-<profile>.txt`、venvは`.local/ansible-venv-<profile>`です。controller自体はPython 3.12または3.13で動かし（`config/ansible-controller.env`の`PYTHON_BIN`）、準備では`./scripts/ansible-install.sh --all`で両方を用意しておきます。Ansibleが対象nodeへ配るhelper（`tree-digest.py`など）は、`tests/test_remote_helpers.py`でPython 3.8でも動くことを確認します。

slpのreleaseとFlameGraphのscriptは、準備のうちに`./scripts/fetch-observability-tools.sh --update`でrepo（`ansible/roles/observability/files/vendor/`、x86_64とarm64）へ取り込んであり、bootstrapはそれをnodeへcopyします（`observability_tools_source: bundled`）。nodeがGitHubへ出られない、`git`が無い、rate limitに当たる場合も同じ版が入ります。各nodeにGitHubから取らせる場合だけ`download`にします。版を上げたらscriptを再実行してcommitし、引数なしの実行でSHA256SUMSと照合します。計測toolの取得（package、slp、FlameGraph）が失敗しても、log・時刻同期・journalなどの土台の設定は続けます。必須toolの欠落は`verify.yml`（`make bench-check`）が検出します。取得の失敗で止めたい場合は`observability_tools_strict: true`にし、取得だけをやり直すときは`--tags observability_acquire`を使います。access logの差分を集計する`isuscope-node`は、準備で`make isuscope-node`（`scripts/build-isuscope-node.sh`）がinstallしたisuscopeのsourceからmuslの静的binaryとして`.local/isuscope-node/`へ作り、bootstrapがnodeのarchitectureに合うものを`/usr/local/bin/isuscope-node`へcopyします。無ければbootstrapはそう表示して続けますが、`isuscope-node`は必須toolなので`verify.yml`（`make bench-check`）が失敗し、isuscopeの`alp` collectorも失敗します（awkとalpで集計する経路はありません）。isuscopeを入れ直したら作り直し、`./scripts/run-ansible-playbook.sh bootstrap.yml --tags observability_isuscope_node`で置き直します。置いたbinaryが手元のisuscopeと違うbuildなら、`isuscope doctor`（`make bench-check`）のpreflightが失敗します。perfは`perf stat -e task-clock -- true`で実際にeventを取れるかまで確認します。

| 項目 | 内容 | 無効化する変数 |
| --- | --- | --- |
| 計測tool | `sysstat`、`ethtool`（ENAの上限超過counter）、実行中kernel用のperf、pin済みの`slp`、FlameGraph scripts、準備でbuildした`isuscope-node`。off-CPU用の`bpfcc-tools`は既定では入れない | `observability_install_packages`、`observability_download_tools`（取得元は`observability_tools_source`、`observability_install_offcpu`で有効化） |
| perf権限 | `kernel.perf_event_paranoid=-1`、`kernel.kptr_restrict=0` | — |
| Nginx access log | `conf.d/00-isuscope-log.conf`でLTSVを`/var/log/nginx/isuscope-access.log`へ追加出力（既存のaccess_logは変えない）。sessionは`observability_nginx_session_source`で問題に合わせる | `observability_nginx_ltsv` |
| MySQL slow log | 既定は無効。SQLはisuscopeがperformance_schemaのstatement digestから取る（`mysql-digest`）。digestが使えないMariaDBなどでだけtrueにし、`zzz-isuscope-slow.cnf`で`long_query_time=0`を`/var/log/mysql/isuscope-slow.log`へ出力する | `observability_mysql_slow_log`（既定false） |
| log rotation | 上記2つを512MiBで世代交代し、logrotateを1時間ごとに実行 | — |
| 時刻同期 | chronyを有効化 | `observability_time_sync` |
| journal | 永続化し、512MiBで上限 | `observability_persistent_journal` |
| 自動更新 | `apt-daily`、`apt-daily-upgrade`、`unattended-upgrades`を停止 | `observability_stop_auto_updates` |
| Rust build cache | `/home/isucon/.cache/isucon-cargo-target`を作成し、cargoとrustcの有無を表示 | — |

nginx.confがhttpの中で`/etc/nginx/conf.d/*.conf`をincludeしていない場合、LTSV logは追加されず、draft検査がFAILにします。レギュレーションで禁止された項目だけ変数をfalseにします。終了前（`docs/guides/finish.md`）に計測を外すときは`observability_nginx_ltsv`と`observability_mysql_slow_log`をfalseにして`./scripts/run-ansible-playbook.sh bootstrap.yml --tags observability_nginx_log,observability_mysql_slow_log`を実行すると、設定を撤去してreload・再起動します。LTSVを外した後のnginx.confにhttpの`access_log`が1つも無いと、nginxは組み込みの既定のaccess logを書くので（`make tare`が既定のaccess_logを外している）、同じ変更で`access_log off;`を書きます。性能のためのNginx設定は[config/nginx/tare](../../config/nginx/tare/README.md)にあり、初回baselineの後にdeployで入れます。これら以外に事前に取得したtoolは`.local/tools/`へ置き、`observability_local_tools`で配布できます。

`isuscope_fingerprint_paths`には、全nodeで実体を比較したい絶対pathを列挙します。draftは、Rustの配った実行ファイルと、sync.jsonの`type: file`のitem（アプリのunit、env、nginx・MySQLの設定など）を書きます。directoryのitemは入れません（中身は`identity_command`がcommit済みの内容で比べるので、毎回のベンチ前に全nodeで大きなtreeを読み直さない）。kernel、OS、Nginx、MySQL、running serviceは宣言なしでも記録します。

## 4. 初期構成を調査する

```bash
./scripts/inspect-environment.sh
```

remoteを変更せず、OS、CPU、memory、running service、listen port、process名、主要runtime、application root・設定・systemd unit・log path・計測toolの候補を`.local/inspection/<node>.json`へ保存します。process引数や環境変数は収集しません。`make kickoff`の中では、bootstrapが集めたOS・CPU・memoryのfactsをその実行限りのcache（`ANSIBLE_FACT_CACHE_DIR`、終了時に削除）から読み、集め直しません。実行をまたいでcacheを残さないのは、同じnode名の前の環境を読まないためです。systemd unitのfragment pathは1回の`systemctl show`でまとめて引きます。

調査結果から設定候補を作ります。

```bash
./scripts/configure-draft.sh
```

`.local/draft/`にはnode role、`sync.json`、Ansible変数、isuscopeのlog pathと判断材料の`summary.json`が生成されます。これは機械的な候補です。特にapplicationのowner/group、remote path、service名、role分担を確認・修正してから反映します。

```bash
CONFIRM_DRAFT=true ./scripts/configure-apply.sh
make discover
./scripts/sync-check.sh
```

Git commitが存在する場合、`sync-check`はmanifest検証に加えて、Git管理中の配布byte数とnode複製後の合計byte数を表示します。local・remoteの配置先が重なるitemやbuild commandの名前の重複、改行を含むcommandは拒否します。ignored artifactはこの値にも実際のdeploy archiveにも含まれません。例外は`local_builds`が生成・検証した実行ファイルだけで、deployの空き容量検査にはそのサイズも加算します。

反映前の設定は`.local/draft-backup/`へ退避されます。node roleを反映した後の`discover`は`role_nginx`、`role_mysql`などのAnsible groupも生成します。

## 5. local正本とdeployを接続する

通常は`make kickoff`が生成し`make kickoff-apply`が反映したdraftから`config/sync.json`を始めます。Rustのbuild設定は`config/sync.rust.example.json`を参照します。

- `source_node`: 初期状態を回収するnode
- `items`: local/remote path、file/directory、配布先group、owner
- `pre_deploy_command`: 配布前に一度実行する処理（同期対象を変更してはいけない）
- `local_builds`: ローカルDockerでRustをbuildし、対象itemの`target/release/<binary>`へ追加する設定
- `build_commands`: 配布済みstaging itemをnode上でbuild・検証する、切替前の処理
- `post_deploy_commands`: config test、daemon-reload、restart、health check
- `rollback_commands`: 旧ファイル復元後に行うconfig test、daemon-reload、restart、health check
- `status_commands`: 通常確認に使うcommand

commandは単なる文字列なら全application node、`{"node_group":"role_nginx","command":"..."}`なら指定groupだけで実行します。異なる役割のnodeへ無関係なreloadを送らないよう、分担構成ではgroupを明示します。draftは、serviceの起動の検査（`is-active`）を、そのserviceの役割のgroup（mysqlは`role_mysql`、nginxは`role_nginx`、それ以外は`role_app`）に置きます。アプリの検査は、起動まで最大20秒待ちます。

### 役割を別のnodeへ移すとき

DBを別のnodeへ移すなど、nodeの役割を変えるときは、次をまとめて直してから1回でdeployします（eleventhでは、1つずつ気付いてdeployが3回止まった）。

1. `.local/node-overrides.json`で各nodeの`roles`を書き換え、`make discover`をやり直す。groupの所属（`role_mysql`など）はここから決まる。
2. `config/sync.json`で、移した役割のitemの`source_node`を、新しくその役割を持つnodeにする。古いnodeのままだと、deployの検証が`source_node ... is not in sync item group`で止まる。
3. `post_deploy_commands`・`rollback_commands`・`status_commands`の文字列のcommand（全application nodeで動く）を、役割のgroupを持つobjectにする。役割を外したnodeでは、そのserviceを`systemctl disable --now`しておく。止めたserviceの検査が全nodeに残っていると、deployが失敗してrollbackする。
4. 別のnodeのDBへつなぐアプリは、DBより先に起動し得る。deployは全nodeの検査を同時に流し、再起動の順序も保証されない。そのため、アプリが起動時にDBを待つか、再接続するようにする。起動の検査も、`is-active`を数秒待つ形にする。終了前の再起動試験（`make final-db-late`）でも同じことを確かめる。

commandのobjectには`"when": ["nginx-*"]`のように、そのcommandが扱うitemの名前（fnmatchのpattern）を書けます。deployはそのitemが変わったときだけ実行します（`scripts/deploy-plan.py`）。`when`のないcommandは毎回実行します。draftはnginxの検査とreloadに`nginx-*`、Rustの再起動に`rust-app`・`rust-service`・`app-env*`、daemon-reloadに`rust-service`を付け、`make tare`と`make durability`はMySQLの再起動に`mysql-*`、`99-tare.conf`の読み込み（`sysctl -q -p`。OS既定のfileは読み直さないので、存在しないkeyの警告が出ない）に`tare-sysctl`を付けます。Rustのコードだけを変えたdeployでMySQLを再起動しないので、毎回のベンチがbuffer poolの冷えた状態から始まることもありません。

`build_commands`は`name`、`node_group`、`item`、単一行の`command`を持つobjectです。全itemをstagingした後、live pathを切り替える前に実行されます。commandには次の環境変数が渡されます。

- `ISUCON_DEPLOY_RELEASE`: 今回のrelease ID
- `ISUCON_DEPLOY_REMOTE_PATH`: itemのlive配置先
- `ISUCON_DEPLOY_STAGING_PATH`: build対象のstaging配置先

build成果物は`ISUCON_DEPLOY_STAGING_PATH`配下へ配置し、実行ファイルなど必要なartifactを最後に検証します。失敗した場合はlive pathを切り替えず、transactionを中断してstagingを除去します。node上の永続build cacheはtransaction外に残るため、cacheには再生成可能なartifactだけを置き、秘密情報やruntime dataを保存しません。

Rustは`local_builds`を既定とし、配布サーバーではコンパイルしません。`config/sync.rust.example.json`の`binary`・service名・配置先に加えて、`base_image`（Linuxディストリビューションとglibc互換性）、`target`、`dockerfile`を配布環境に合わせて確認します。標準コンパイラはRust 1.99.0です。Dockerfile内で公式rustupから取得し、`RUSTUP_TOOLCHAIN=1.99.0`で選択します。準備ではOSを推測せず、`rust:replace-with-compatible-distribution`を残します。その代わり、`config/rust-builder/bases.json`の候補base（bullseye・bookworm・trixieのRust公式イメージと、そのglibc・libssl）×targetのbuilder imageを`make builder-images`（`local-build.py prepare-images`）で事前に作っておきます。当日の初回buildではrustup、apt、cross linkerの準備が省かれ、依存crateのcompileから始まります。取得できないtagがあれば止まるので、bases.jsonを直します。開始後はkickoff build処理が配布コードと実行環境からこの候補を選ぶか、操作者が互換性のある既存のRust公式イメージを設定します。例えば既存のbullseye環境ならbaseを維持し、Rust版だけを更新します。配布サーバーのRust版は`.local/kickoff-build/environment.txt`にあり、標準版の選択とは分けます。厳密に固定する場合は`base_image`をdigest指定にします。追加のCライブラリなどが必要なら、Git管理するDockerfileへ明示します。

```bash
make build                   # SSH/inventory不要。先行回収のcommit後から準備可能
make deploy                  # 必要ならbuildし、同じ成果物を全対象nodeへ配る
# draft作成後、適用前にbuildを先行させる場合:
make build SYNC_MANIFEST=.local/draft/sync.json
```

ローカルにはPython 3と起動済みのDockerが必要です。Linux Docker hostのARM64/AMD64でネイティブにcompilerを動かし、`x86_64-unknown-linux-gnu`または`aarch64-unknown-linux-gnu`を出力します。Docker DesktopのMac共有ディレクトリにtargetを置かず、Linux named volumeへCargo targetと依存取得cacheを保存します。初回だけimage・依存の取得が必要です。既存vendorを使うオフライン検証では`RUST_BUILD_VENDOR_DIR=/absolute/vendor make build`を使えます（imageは事前準備が必要）。vendorは今回のlockfileに対応したものを使います。

入力はcommit済みのitem全体と`Cargo.lock`です。配布物がworkspaceの一部で、`Cargo.lock`やpath依存がitemの外（親directory）にある場合は、`local_builds`（と`config/kickoff-build.json`）へ`source_root`（`webapp/`配下でitemを含むworkspace root）と`manifest_dir`（itemから見たbinaryのCargo.tomlの位置）を指定します。配布の`Cargo.lock`は再生成しません。標準以外のRust版が必要なら`toolchain`、target向けのCライブラリ（例: `libssl-dev:amd64`）は`packages`へ明示します。builderはtargetのarchitectureを追加し、`pkg-config`がtarget側のライブラリを参照するように設定します。ソースが未commitなら停止します。`make build`はDockerfile・manifestの調整中にも使えますが、deploy時はmanifest・Dockerfile・builder scriptもcommit済みであることが必要です。成果物は`.local/rust-build/`、直近の索引は`.local/local-build.json`に置き、Gitには追加しません。

worktreeからもルートリポジトリのcacheを共用します。専用build lockで同時書込みを直列化し、通常のkickoffsetupは止めません。ソースtree・build設定・image ID・builder scriptが同じならSHA-256検証後に完成済みbinaryを再利用します。ソースやCargo.lockが変わればCargoを再実行し、image・target等が同じなら依存crateのcacheを再利用します。imageやtargetが変われば別cacheになります。Docker image/volumeと`.local/rust-build`は明示的に削除するまで残ります。壊れたartifactは自動配布せず停止するため、該当input IDの成果物ディレクトリを除いて再buildしてください。

deployはローカルbuildを完了してから全nodeのpreflightに進みます。source archiveへ生成binaryだけを追加し、nodeのCPUを検査します。stagingでSHA-256・実行権限・`ldd`による共有ライブラリ解決を確認し、全台成功してからliveへ切り替えます。transactionごとの索引は`.local/deploy-transactions/<release>.artifacts.json`です。切替失敗時は従来どおりsourceとbinaryを一緒にrollbackします。`ldd`は実アプリの動作保証ではないので、当日の環境でhealth checkとベンチも必要です。

既存の`build_commands`契約は他用途向けに残します。同じitemへ`local_builds`とremote buildを同時指定することは禁止します。Ansibleのremote Rust cache準備も既定では無効です。明示的に旧方式を使う場合だけ`observability_remote_rust_build: true`にします。
```bash
./scripts/sync-check.sh
./scripts/import.sh
git add webapp config
git commit
make deploy
make status
```

`make final-check`・`make final-reboot`・`make final-db-late`（`scripts/final-verification.py`）は、終了前に主催者の追試で落ちる原因を確かめます（手順は`docs/guides/finish.md`）。問題ごとの値（envcheckのcommand、守るunitと管理ユーザー、確かめるURL、インスタンス種別と台数、再現スコアのしきい値）は`config/final-check.json`に書きます。確かめるunitは、その設定の`units`、`config/sync.json`の`systemctl`の行（is-active、enable、restartなど）、`config/systemd/`のunit、nginx・mysql・mariadbなどのmiddlewareで、nodeに無いunitと、止まっていてenableもされていないunitは数えません。結果は1行ずつOK・WARN・FAIL・INFOで出し、FAILがあれば1で終わります。再起動とDBの停止は操作lockの中で行い、mainのセッションだけが実行できます（`make final-check`は読み取りなので、どのセッションからも実行できます）。ISUCON9〜14の追試の手順と、実際に失格・事故になった例（enableしていないservice、DBより先に起動して落ちるアプリ、15分後に止められたDB、再起動で消える設定、追試の2回目で落ちるアプリ）から項目を選んでいます。

`make status`（`scripts/status.sh`）は全nodeを並列に検査します。nodeごとに1回のSSHで、そのnodeの様子（load、メモリ、disk、失敗したunit、待ち受けport、直近30分のOOM kill、直近10分のerror、MySQLの実行中のSQL）と、`config/sync.json`の`status_commands`の結果を返します。node上の各行は5秒、node全体は`STATUS_TIMEOUT_SECONDS`（既定20秒）で打ち切り、応答しないnodeは「no answer」、接続できないnodeは「SSH failed」と出します。終了コードが失敗になるのは`status_commands`の失敗と、応答しない・接続できないnodeだけです。このSSHは使い回しの接続（ControlMaster）を使いません。固まったnodeへの古い接続に乗って待たないためです。`scripts/ssh-node.sh`はどの接続にも`ServerAliveInterval=5`・`ServerAliveCountMax=3`を付け、接続した後にnodeが応答しなくなった場合も約15秒で切ります（接続の段階は`ConnectTimeout=5`）。メモリを使い切ったnodeには、時間制限の無い手打ちのsshが長く固まり、portが開くのを待つloopでは原因に気付くのが遅れます。

`import`は対象groupの全nodeでpathの内容・mode・symlinkをdigest比較し、不一致なら回収前に停止します。意図した差異だと確認した場合だけ`IMPORT_ALLOW_DIVERGENT=true ./scripts/import.sh`とし、itemごとの`source_node`から回収します。比較表は`.local/import-comparison.tsv`へ残ります。directory itemの`exclude`（itemからの相対path）は、digest比較と転送の両方から外します。draftのRust itemは`target`と`.git`を除外します。build成果物はGit管理もdeployもしないので、回収すると不要な転送が大きく増え、node間でbuild状態が違うだけで不一致として止まるためです。実行時に必要なファイルが除外pathの下にあるなら、`exclude`から外して明示的に回収します。

`deploy`は未コミットの同期対象を拒否します。全nodeでsudo、owner/group、空き容量、配置先をpreflightし、全fileをstagingし、build commandを完了してから切り替えます。一台でもbuild、配置、post-deploy検証に失敗すると、そのtransactionで触れた全対象を元へ戻し、live切替後なら`rollback_commands`で稼働プロセスも旧構成へ戻します。rollback自体が失敗した場合、stateは`rollback-failed`になります。

deployは最後に成功したdeployのcommit（`.local/current-deploy-commit`）からの差分だけを送ります。内容（commitのtree）、sync.jsonでの定義、作り方（`local_builds`の設定、builderのDockerfile、`scripts/local-build.py`）のどれも変わっていないitemはstagingも切替もせず、変わったitemの`local_builds`がなければローカルbuildも飛ばします。変わったitemが1つもなければremoteへ何も送らず、HEADをdeploy済みとして記録するだけです。deployの記録がないとき（最初のdeploy、配布状態のbaselineの後）は全itemを送ります。サーバーをdeploy以外で変えた後などは`DEPLOY_ALL=1 make deploy`で全itemを送ります。各nodeのpost-deploy・rollbackのcommandは、順序を保ったまま1回のSSHでまとめて実行します（それぞれ独立したsubshellで動き、最初の失敗で止まります）。`scripts/ssh-node.sh`は同じnodeへの接続を60秒使い回すので（ControlMaster、`/tmp/isucon-ssh-<uid>/`、`SSH_CONTROL_PERSIST=0`で無効）、短いSSHを続けて開く操作がhandshakeを毎回払いません。

transaction IDは既定で`<commit>-<UTC時刻>-<PID>`となり、同じコミットを繰り返しdeployできます。Gitコミットは`.local/current-commit`、transaction IDは`.local/current-release`へ保存し、明示rollbackが成功した場合は両方を直前の値へ戻します。remoteのrollback backupは既定で各配布先の直近3世代を保持し、`DEPLOY_BACKUP_RETENTION`で変更できます。明示的に戻す場合は、保持中のIDを指定して`make rollback RELEASE=<id>`を使います。差分deployのreleaseは、そのreleaseが送ったitemだけを戻し、そのitemに対応する`rollback_commands`だけを実行します。rollbackは全対象のbackupが揃っていることを確認してから復元を開始します。manifestへ秘密情報を記録せず、そのようなファイルは`.local/`で別管理します。

## 6. isuscopeへベンチを接続する

デフォルトは`mode = "command"`です。`external`を通常運用には使いません。

`config/benchmark.env`へ大会の起動方法を記述します。

- `BENCHMARK_TRANSPORT=local`: 操作端末でcommandを実行
- `BENCHMARK_TRANSPORT=ssh`: inventoryの`BENCHMARK_NODE`でcommandを実行（`.local/environment.env`の`BENCHMARK_NODE_ACCESS=ssh`が要る。下を参照）
- `BENCHMARK_TRANSPORT=http`: APIで開始し、必要ならrun IDを使って完了までpollする
- `BENCHMARK_PROBE_COMMAND`またはHTTP probe URL: ベンチを開始しない疎通確認
- `BENCHMARK_SAMPLE_FILE`: 実出力のサンプル。途中経過・タグ・最終結果の行を含めたまま保存する
- `BENCHMARK_SAMPLE_SCORE`、`BENCHMARK_SAMPLE_PASS`: sampleを人間が読んで得た最終scoreと合否。`--check`はregexがこの値を取り出すことを確認する
- `BENCHMARK_EXCLUDE_REGEX`: score・合否に使わない行。空なら`.isuscope/policy.toml`の`operator_line_pattern`（運営向けの行）
- score、PASS、FAILを見つける正規表現
- timeout

tokenやcookieだけは`config/benchmark-secrets.example.env`を`.local/benchmark-secrets.env`へコピーして保存します。

```bash
.isuscope/benchmark.sh --check
.isuscope/benchmark.sh --probe
```

初回はベンチの出力の形が分からないので、`BENCHMARK_SAMPLE_SCORE`と`BENCHMARK_SAMPLE_PASS`は空のままでかまいません。そのとき`--check`はWARNを出して通り、runではadapterが判定を保留（`pending`）して出力を残します。isuscopeはそのrunを`pending`として記録し、全collectorの計測はそのまま使えます。確定するまで次のベンチは始まりません。確定は`python3 scripts/benchmark-sample.py <run>`で行います。引数なしでは、保存された出力のうち結果を含みそうな行と、今のregexが読んだscore・合否を出すだけです（何も変えません）。読み取りが違えば`config/benchmark.env`のregexを直して見直します。合っていれば、出力を自分で読んだ値を渡して`--apply --score N --pass true|false`とすると、運営向けの行とadapter自身の結果行を除いた参加者向けの行を`config/benchmark-sample.log`へ書き、`BENCHMARK_SAMPLE_SCORE`・`BENCHMARK_SAMPLE_PASS`を記入し、contractを再sealして`--check`を通し、`isuscope rescore <run>`まで進めます。値をregexの読み取りから自動で埋めないのは、タグや途中経過の数字を拾ったregexを見逃さないためです。終わったら`config/benchmark.env`、sample、`config/benchmark-contract.json`をcommitします。手動でベンチを回して出力の形を調べる必要はありません。

initializeと負荷走行の境界は、最初のrunより前に`.isuscope/routes.toml`の`[initialize]`（methodとpath）で決めます。isuscopeは、ベンチの出力から境界が取れないrunでも、access logでこの要求が最後に応答を返し終えた時刻を負荷の始まりにし、出どころを`benchmark.initialize_source`に残します。`make kickoff`は先行回収のcommit直後に`python3 scripts/benchmark-markers.py auto-route`を実行し、アプリのコードにinitializeの経路の候補が1つだけなら`POST <path>`として書き込んでcommitし、`.local/progress.log`へ`initialize-route`を出します。候補が無い・複数のとき（`needs_choice`）と、`routes.toml`に未commitの変更があるときは書きません。mainが`suggest-route`で候補を見て、`set-route POST /api/initialize`のように書き込んでcommitします。境界がないと、ホスト・クライアント・SQLの要約にinitializeが混ざります。`bench-check`は境界がなければWARNを出します。ベンチが分かりやすい行を出す場合は、同じsampleから文言でも設定できます（`suggest`で参加者向けの行から候補を時刻なしで出し、`set "<開始の文言>" "<終了の文言>"`がsampleに順に現れることを確かめて`.isuscope/policy.toml`へ書きます）。

どちらの検査もベンチを起動しません。`check`は設定、regex、保存したサンプルを検査し、`probe`は実行ファイルまたはAPI endpointだけを確認します。標準adapterはcommandを一度だけ実行し、出力からscoreとPASS/FAILを取得してisuscope protocolへ変換し、二重起動も拒否します。scoreは最後に一致した行の最後の数値です。合否はFAILの行を優先し、終了コードが0でない場合と最終結果の行がない場合（途中で切れた出力）はFAILにします。使ったscoreの行と最終結果の行はrunのmessageに残るため、`isuscope brief`で誤った行を拾っていないか確認できます。別userで実行するときは`sudo -n -u USER sh -c '... && exec ./bench'`とし、`sudo -i`（login shellが入れ子のcommandを再解釈する）や独自のPASS/FAIL行の追加は避けます。adapterの判定は`tests/test_benchmark_adapter.py`のfixtureで回帰を確認します。HTTPのtokenやcookieは`.local/benchmark-secrets.env`またはGit管理外のheader fileへ置きます。

`make discover`はinspection draftで選んだNginx・MySQL log pathも生成済みisuscope設定へ反映します。初回run後、動的IDのためrouteが細分化されていれば次を実行します。

```bash
isuscope routes suggest <run-id> --output .local/route-suggestions.toml
```

`.local/route-suggestions.toml`は候補であり、自動適用されません。実例とpatternを確認し、必要な規則だけ`.isuscope/routes.toml`へ移します。

### isuxportalのポータルにつなぐ

ISUCON10〜14のポータル（isuxportal、`isucon/isucon14-portal`）は、選手のページと同じAPI（protobuf over HTTP、ログインのcookie）でベンチを受け付けます。当日のポータルがこれなら、`scripts/isuxportal-bench.py`を操作端末から動かします（`BENCHMARK_TRANSPORT=local`）。先に当日マニュアルで、ポータルの自動操作が禁止されていないかを確かめます。

```bash
python3 scripts/portal-cookie.py --portal https://portal.example   # ログインしたChrome（--remote-debugging-port=9222）からcookieを.local/benchmark-secrets.envへ
set -a; source .local/benchmark-secrets.env; set +a
python3 scripts/isuxportal-bench.py targets --portal https://portal.example   # 選手のサーバーとid
python3 scripts/isuxportal-bench.py probe --portal https://portal.example --target-id 123   # ログインと競技中かだけを確かめる
```

- `config/benchmark.env`は`BENCHMARK_COMMAND='python3 scripts/isuxportal-bench.py run --portal URL --target-id ID'`、`BENCHMARK_PROBE_COMMAND`は同じ引数の`probe`にします。`run`はベンチを依頼し、終わるまで待ち、選手向けの出力の後に`ISUXPORTAL job=<id> status=<s> passed=<true|false> score=<n>`の1行を出します。regexはこの行だけに合わせます（`config/benchmark.env`のコメント）。ベンチ自身の出力にある`score`や`failed`を拾わないためです。
- contractは`invocation.executable`を`scripts/isuxportal-bench.py`、`required_flags`を`{"--portal": "URL", "--target-id": "ID"}`にします。cookieは`PORTAL_COOKIE`（秘密設定）なので、取り直してもsealし直す必要はありません。
- 書き込みにはRailsのCSRF tokenが要るので、scriptはポータルのページの`<meta name="csrf-token">`を読み、そのときに更新されたcookieで依頼します。cookieが切れると、`run`は結果の行を出さずに終了コード2で止まり、cookieの取り直しを促します。
- 選手はジョブを取り消せません。`scripts/benchmark-stop.sh`やadapterの期限で止めても、ポータル上のベンチは最後まで動きます。

### isuscopeの設定で確認すること

- `make discover`が生成する`.isuscope/config.toml`のnode、role、SSH設定は生成物なので、直す場合はprovider入力とdraftを直して再生成する。roleは複数指定でき、collectorの対象nodeを選ぶtagとして使う
- 負荷を担う少数のsystemd unitだけを`ISUSCOPE_SERVICE_UNITS`へ指定する。アクセスログとslow logのpathと形式（時刻、匿名化session、method、URIなど時系列に使うfield）を実環境へ合わせる
- app binaryや単一の設定fileのpathを`isuscope_fingerprint_paths`へ指定すると、`fingerprint.sh`と一緒に各nodeへ冪等に配置される
- 標準のlog collectorは`.1`〜`.5`と各`.gz`から開始時のlogを照合し、保持世代を越えたrotationや欠落は壊れた差分を返さず`unavailable`になる。非空logをisuscope-node・slpが1件も解析できない場合は設定不一致として`failed`になる
- `.isuscope/routes.toml`は1規則から固定のcanonical routeへ置換し、patternにcomma、replaceに`$1`などのcaptureを使わない。初回runで動的URLが残ったら`isuscope routes suggest <run-id> --output .local/route-suggestions.toml`の候補を確認して移す
- 会話履歴とrunを紐付ける`[context.agent]`を使う場合は、新しいセッションを開始する前に共通hook（`~/.agent-history/agent_history.py`）を有効にしておく
- remote変更は既存fileのbackup、設定検証、atomicな配置、必要最小限のreloadで行い、package導入や常駐agentは既存機能で代替できない場合だけ使う

## 7. ベンチ前gateと初回run

```bash
make first-bench   # 次を続けて実行する（配布状態で測れないときはdeployが先）
# make bench-check
# isuscope survey-run --hypothesis "初期状態の負荷構造とベンチシナリオを記録する"
# make deploy
```

isuscopeは毎回のベンチの前（collectorより前）に、`.isuscope/policy.toml`の`prepare_command`（`scripts/benchmark-prepare.sh`）を実行します。sync.jsonのRust unitを、動いているnodeでだけ再起動し（`try-restart`）、そのunitのprocessがportをlistenするまで待ちます。再起動するのは、前回のベンチを処理したprocessがまだ動いているunitだけです（systemdの`InvocationID`を`.local/benchmark-invocations.json`へ残して比べます）。deployで前回のベンチの後に起動し直したprocessは、前回のrequestを抱えていないので再起動しません。手動のベンチが途中で切ったrequestのtransactionが接続poolに残ると、次のinitializeの`DROP TABLE`がlock待ちでtimeoutになることがあります。準備に失敗したrunは、ベンチを始めずにFAILとして残ります。

ベンチ出力の問題固有の行（scenarioごとの件数、FAILの理由、エラーの実例）は`.isuscope/parse-benchmark.sh`が読み、`.isuscope/policy.toml`の`[[benchmark.parsers]]`で最初からisuscopeへ登録してあります。既定の実装は、WARN・ERRORの行をcategory（`CODE=<n>`、無ければtimeout・connection・statusの語）ごとに`benchmark.warnings`・`benchmark.errors`で数えて実例をmessageに残し、クリティカル・`pass=false`の行をfailureとして残すので、当日に何も書かなくても警告の内訳は取れます。当日はscriptの`--- 当日に実装する ---`の範囲へ問題固有の内訳（シナリオごとの成功数、利用者の登録・離脱など）を足し、categoryの取り方が出力に合わなければ`category()`を直して、`isuscope enrich <run>`で既存のrunにも反映します。

同じ構成の再測定は、isuscopeが止めます。`.isuscope/policy.toml`の`identity_command`（`scripts/benchmark-identity.py`）は、配る対象のcommit済みの内容（`deployed_files`）、`config/benchmark.env`（`benchmark_env`）、配布状態かdeploy済みか（`build`）をJSONで出します。isuscopeはこれと、nodeのfingerprint（`.isuscope/fingerprint.sh`。Rustは配った実行ファイル、配る単一file（アプリのunitやnginx・MySQLの設定）。directoryの中身はidentityの`deployed_files`だけで比べる）、自分の計測設定（`config.toml`と重ねた`.isuscope/policy.toml`、`routes.toml`、adapterのscript、isuscopeの版）を、ベンチを実際に始めた直前のrunと比べ、すべて同じなら始めず、runも残しません。何も変えなかった編集（一致しなかった`sed`など）の後に、同じ構成を1run測り直さないためです。ばらつきの確認など意図した再測定は`isuscope run --repeat "理由" --hypothesis "..."`で明示します。fingerprintの対象は`configure-draft`がsync.jsonのitemから`isuscope_fingerprint_paths`へ書き、`kickoff-apply`が各nodeへ配ります。

振れ幅は、同じ構成（isuscopeの`config_key`が同じ）で測ったrunの得点から、`isuscope variance`が各runの開始時点で分かっていた値として出します（ボードもこれを使う）。APIごとの待ちの表（`ISUCON_WAITS`。lane `waits`がアプリへ写す`config/waits/waits.rs`が起動時に1度だけ読む）は、`scripts/waits.py set`がアプリのEnvironmentFileの行を書き（unitの`EnvironmentFile`をsync.jsonでrepoのfileへ戻して探す）、`make deploy`で配ります（buildは要らない）。`scripts/benchmark-identity.py`はcommit済みの表を`waits`としても出すので、各runの`run.json`の`identity.values.waits`に、そのrunで効いていた表が残り、変えた回は`changed identity.waits`と出ます。試した待ちは、tag `wait`・`wait:<METHOD PATH>`を付けたrunと、IDが`wait-`で始まるisuscopeのchangeに残し、`scripts/waits.py history`が両方を並べます。isuscopeだけで読むなら`isuscope sql "SELECT r.id, r.score, t.tag FROM runs r JOIN run_tags t ON t.run_id = r.id WHERE t.tag LIKE 'wait%' ORDER BY r.started_at"`と`isuscope change list`です。

ベンチの期限は`config/benchmark.env`の`BENCHMARK_TIMEOUT_SECONDS`だけで、adapterが止めます（isuscopeの`[benchmark] timeout_seconds`は使わない）。出力が`idle_timeout_seconds`（`.isuscope/policy.toml`で300秒）途絶えたときはisuscopeが止めます。`BENCHMARK_TRANSPORT=ssh`では、ローカルのSSHに加えて、ベンチnode側のコマンドも`timeout -k 10 <期限>`で包みます（ローカルはその30秒後に止めます）。pty無しのSSHを止めてもremoteのベンチは走り続け、次のrunと重なり得るためです。isuscopeがベンチを止めたとき（`benchmark.stopped_by`が`timeout`／`idle`／`interrupted`）は、`cleanup_command`の`.isuscope/benchmark.sh --cleanup`が、ベンチnodeに残ったprocess group（run時に`/tmp/isuscope-benchmark.pid`へ残したもの）を止め、持ち主の消えたadapterのlockを外します。

期限の前に、遅いベンチをAIへ知らせます。adapterは、最初に自然に終わった（exit 0で出力のある）ベンチの所要時間をbaselineとして`.local/benchmark-baseline-seconds`へ残します。以後のベンチがその`BENCHMARK_SLOW_FACTOR`倍（既定1.3。正常なrunのずれはおよそ1.1倍に収まる。小数も書け、`config/benchmark.env`で変えられます）を超えて動いていると、`.local/progress.log`へ`benchmark slow`の行を1行書きます。ベンチは止めません。AIは行を見て原因を確かめ、止めるなら`scripts/benchmark-stop.sh`を実行します。これはadapterが`.local/benchmark-run.pid`に残した`isuscope run`のprocessへSIGTERMを送るので、isuscopeはrunを`interrupted`として記録し、上の後始末を動かします。baselineを測り直したいときは`.local/benchmark-baseline-seconds`を消します。期限（`BENCHMARK_TIMEOUT_SECONDS`）だけに頼ると、止まったrunを毎回その期限まで待つことになります。

再起動の前に、`scripts/check-deployed-commit.sh`で、最後に成功したdeployのcommit（`.local/current-deploy-commit`）とHEADを、deployが配るファイル（sync.jsonのitem、local buildのDockerfile、sync.json自身）に限って比べます。違うcommitか、それらのファイルに未commitの変更があれば、ベンチを始めません。isuscopeはrunをHEADのcommitとして記録するため、そのまま回すと、記録上のcommitとサーバーで動いているcommitが違うrunになります。docsやisuscope-dataだけのcommitでは止まりません。deployの記録がない環境で意図して回す場合だけ、`SKIP_DEPLOYED_COMMIT_CHECK=1`を付けます。

`bench-check`はAnsibleの構文と全nodeのverify（SSH、disk、role別の必須service、必須command）、sync manifest、benchmark adapterのcheckとprobe、`isuscope doctor`を実行しますが、ベンチは起動しません。shell構文と空白の検査はCIが担当します。`isuscope doctor`は、保存したbenchmark sampleに`initialize_start_marker`・`initialize_finish_marker`の文言が含まれるかを確認し（含まれなければinitializeと負荷の区間分けができないのでWARN）、各collectorの`preflight`（全nodeで`/proc/stat`の1秒sampling、nginx access logの読取、`sudo -n`でのslow log読取など）を対象nodeで実行し、`config/benchmark-sample.log`へ全parserを適用し、最新runに動的IDを含むrouteが残っていないかも確認します。

### 開始後の構築完了待ちと計測設定の再生成

SSH接続だけでは初期DB投入の完了とみなさない。bootstrapは全対象nodeで
`cloud-init status --wait`を確認してからpackage・サービス設定を変更する。
待機上限は`bootstrap_provision_timeout_seconds`（既定1800秒）。失敗・timeout時は
設定変更前に停止し、構築ログを確認する。練習の復旧scriptは完了markerを消して
開始し、bootstrapは復旧processの終了とmarkerの再作成も確認する。
復旧成功のmarkerがある場合だけ、中断したcloud-initの終了コードを許容する。
先行コード回収はこの待機より前に行える。

このrepoが決めるisuscopeの設定（会話紐付け、運営向け出力の除外、ベンチの前後のcommand、parser）は
`.isuscope/policy.toml`を正本とする。`make discover`が生成する`.isuscope/config.toml`は
`[include] config`でこのfileを重ねるだけなので、変更はこのfileを直してcommitすれば次のrunから効く
（再生成は要らない）。`make bench-check`は重ねていることを確かめる。
練習では`[ADMIN]`を含む行を除外する。生成済みTOMLだけを編集しない。

systemdの調査は稼働中だけでなく、インストール済み・停止中のunitも対象にする。
Rust unitのfragmentは`/etc/systemd/system`、`/lib/systemd/system`、
`/usr/lib/systemd/system`からdraft候補にできる。unitが見つかったことと稼働確認は
別に扱い、draft reviewとdeployの起動検査は引き続き必要とする。


## ベンチの競技条件の確認

開始後に公式実行手順を `docs/official/` へ保存し、
`config/benchmark-contract.example.json` を `config/benchmark-contract.json` にコピーする。
操作者はmode、通常/初期化timeout、負荷時間、接続先を公式手順と照合し、
`conditions`、`reviewed_by`、`reviewed_at`、一次資料のpath/SHA-256を記入する。
条件がCLIで変更できない場合も、既定値・固定値・API側管理の別と根拠を書く。
CLI型では `invocation.executable` と `required_flags` に明示が必要な引数を列挙する。
大会ごとの値をscaffoldの既定値として埋め込まない。

確認後、実際のadapterと同じ順序で設定を読み、検査記録を確定する。

```bash
set -a
source config/benchmark.env
source .local/benchmark-secrets.env  # 存在する場合
set +a
python3 scripts/benchmark-contract.py --seal
.isuscope/benchmark.sh --check
```

`--seal`はベンチを起動しない。公式資料の意味を自動判定する承認処理でもない。
操作者自身の照合後に使う。必須引数の欠落・不一致・重複はseal時にも拒否する。
HTTP型は送信先・body・headerを含む実設定を操作者が照合する。
`--check`・`--probe`・本実行のすべてで、資料hashと実設定hashを検査する。
秘密設定による上書き、接続先変更、HTTP body変更も未確認のまま実行できない。
変更後は再照合してsealする。記録には秘密情報の値を保存せずhashだけを残す。
この確定ファイルは開始後に作るもので、開始前テンプレートへ含めない。


## 汎用修正を開始用テンプレートへ反映する

汎用修正はisu-scaffoldを正本としてcommitする。追加・変更した汎用ファイルを
`config/scaffold-export.json`へ列挙し、次のコマンドで確定commitから開始用repoへ反映する。

```bash
python3 scripts/sync-scaffold.py /absolute/path/to/ready --ref COMMIT --apply
python3 scripts/sync-scaffold.py /absolute/path/to/ready --ref COMMIT
```

対象に未commitの変更があれば上書きせず停止する。列挙外のファイルは変更しない。
出典commitとファイルhashは転送先の`docs/preparation/scaffold-source.json`に記録する。
アプリ・計測結果・会話・接続情報・当日のベンチ条件は転送しない。
開始用repoで準備の境界検査（`make preparation-check`）とテストを通し、差分と出典をcommitする。
新しいファイルを追加した際はexport対象へ追加し、単発の手コピーに戻さない。

## 回収するMySQL設定と追加SQLの配布

inspectionはMySQL/MariaDBの共有設定候補を個別に確認する。draftは回収元で存在した
conf.d、mysql.conf.d/mysql.cnf、mariadb.conf.d/mariadb.cnf等だけを列挙し、
`/etc/mysql`全体やnode固有の`debian.cnf`を同期しない。古いinspectionで子pathが不明なら再調査する。
環境固有の配置はdraftレビューで明示する。許可候補でも秘密を含む独自設定は操作者が除外する。

`sync_validate`はimport/check/deployの前に`check-deployment.py`を実行する。
Git管理される`webapp/sql/`のSQL/.sh（tests配下を除く）が同期対象から漏れると停止する。
ローカル専用ならsync.jsonの`local_only_files`へ`{"local":"webapp/sql/example.sql","reason":"具体的な除外理由"}`を記録する。
配布先・migrationの要件は`sync.json`の`deployment_requirements`へ記録する。各要件の`path`、`node_group`、`remote`、任意の`command`を同期itemと`post_deploy_commands`に照合する。ローカル専用なら`local_only_reason`を明示する。検査は作業履歴DBに依存しない。
統合済み要件は以後のdeploy時にも検査し、設定が消えた場合も検出する。
