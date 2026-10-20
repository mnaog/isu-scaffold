# 準備: 本番開始前にすること

## 目的と境界

本番開始前に許され、かつ問題固有の情報なしで実施できる準備を終える。開始時には、問題公開後にしかできない作業だけが残る状態にする。repo作成だけで準備完了とはしない。

開始後に初めて分かる配布AMI・台数・構成・コード・schema・依存版の選定と、実環境への適用は開始後に行う。練習運営側が事前に用意した配布物も、参加者へ渡すのは開始宣言後とする。ファイルを開始後用と名付けるだけで、開始前の参加者repoへ問題固有の情報を入れてはいけない。

事前に公開された公式情報と汎用tool・スクリプト・空の設定雛形を準備する。接続情報等が事前配布される場合は、その時点で許可された範囲を記録して準備する。

全項目を「確認済み」「未完了」「対象外」「公開待ち」に分け、確認日・使用版・根拠を`docs/preparation/readiness.md`へまとめる（長い検証記録は`docs/preparation/`の別ファイルに置き、readinessから参照する）。秘密情報と生の認証出力は`.local/`へ置く。「公開待ち」は本番でも開始前に取得できない情報に限る。CLI未導入、未検証の起動経路は公開待ちにしない。保留した事前確認があれば準備は未完了である。

## repoと再現条件

- 現在のscaffoldから独立したGit repoを作り、privateのGitHub repoへpushしてprivateであることを確認する。過去の履歴・別大会の成果物・接続情報はコピーしない。scaffoldのrootで`python3 scripts/new-contest-repo.py <新しいdirectory> --github <owner>/<name>`を実行すると、commit済みのscaffoldから`config/new-repo.json`の除外（scaffold自身の会話履歴、AI比較の実験、別件の資料）を除いてコピーし、新しい履歴で始め、privateのrepoを作ってpushする。本番では`practice.md`とAGENTS.mdの練習用の2行も消す（練習の開始用repoなら`--practice`で残す）。コピー元のcommitは`docs/preparation/scaffold-source.json`、準備状況のひな形は`docs/preparation/readiness.md`に入り、以降の確認はそのrepoで記録する。以降のAIセッションは新しいrepoの中で起動し直す（scaffoldで起動したセッションの会話は新しいrepoへ記録されず、isuscopeがベンチをその会話へ結び付けられない）。
- scaffold、isuscope、agent-history、各AI CLIの使用版を記録する。未コミットのテンプレ変更を使う場合はファイルのhashまたは差分も残す。
- 会話履歴以外のAI初期入力、起動プロンプト、モデル、推論設定、ハーネス設定を明示する。入力に別の問題の分析・コードを混ぜない。
- Gitのcommit・push、SSH鍵管理、`.local/`の除外、ClaudeのAGENTS symlinkを確認する。

## ローカル実行環境

- git、gh、bash、make、SSH、curl、tar、jq、zstd、Python 3.12以上を確認する。
- `./scripts/ansible-install.sh --all`を実行し、legacy・modernの両controllerのlocalhostでAnsible moduleが動くことを確認する。Pythonと解決した依存版を記録する。対象nodeのPythonは開始後に`ansible-runtime.py`が調べて選ぶ。
- 使用版のisuscopeをbuild/installし、`lock`、`routes`、`rescore`、`doctor`等の必要なコマンドがあることを確認する。version表示だけで同一buildとは判定しない。
- Rust、Cargo、linker、採用予定の汎用開発ツールを確認し、問題を使わない小さなRustプログラムでbuild・testする。事前に分かるRust版は準備し、配布アプリの正確なtoolchain・Cargo依存・DB schemaは公開後に合わせる。
- `./scripts/fetch-observability-tools.sh`で、repoへ取り込んだslp・FlameGraphがpin済みの版とSHA256SUMSに一致することを確認する。版を上げる場合だけ`--update`で取り直してcommitする。
- `make isuscope-node`で、nodeでaccess logの差分を集計する`isuscope-node`を、installしたisuscopeのsourceからLinuxの静的binary（amd64とarm64）としてbuildし、`.local/isuscope-node/`へ置く。bootstrapがnodeへcopyし、isuscopeの`alp` collectorがaccess logの差分をnode上で集計する（2コアで62万行を約1.2秒）。無いと`alp` collectorが失敗し、runにHTTPの計測が残らない。muslの静的binaryなのでnodeのOSに依存せず、Dockerも要らない。`./scripts/build-isuscope-node.sh --check`で、置いたbinaryがinstallしたisuscopeと同じbuildかを確かめ、BUILD_IDを記録する。isuscopeを入れ直したら作り直す。
- `make builder-images`で`config/rust-builder/bases.json`の候補base×targetのbuilder imageを作り、所要時間と取得できなかったtagを記録する。問題のコードは使わない。
- 配布可能な計測toolの取得元・版を固定する。ローカルへ事前取得する場合はhashと再取得手順を残す。サーバーのOS・architecture・kernelに依存するpackageの選択と導入は公開後に行う。
- `bash tests/kickoff-automation.sh`と`make test`で、問題や実サーバーを使わずimport/deploy/rollback、worktree、ボードの動作を確認する。

## AIと運用経路

- 利用予定のAIについて、CLI導入・認証・利用モデルでの新規セッション応答を確認する。代替モデルへ黙って切り替えない。
- 各利用予定CLIで会話履歴の保存を確認する。短い接続確認を使い、問題の探索は開始しない。
- AI laneが使う`claude -p`を、`config/lanes.json`のmodel・effort・permission modeで短く実行して応答を確認する（例: `echo "Reply OK" | claude -p --model claude-opus-5-5 --effort high --permission-mode dontAsk`）。問題の探索は開始しない。
- 開始後のコード先行回収と非同期buildの手順を確認する。
- ボード（`make board`）のローカル表示を確認する。テストデータ・常駐processは開始前に残さない。
- scout（Claude Code・Codex・Kimi Code）を`python3 scripts/scout/main.py once <name> --smoke`で接続確認する。探索とscoutの定期実行は開始しない。smokeセッションをscoutの会話入力として登録しない。Kimiは任意で、CLIの導入と`kimi login`の認証がなければ失敗として表示され、他の2体は動く。詳細は[scout](../roles/scout.md)。
- 禁止操作を止めるhookを確認する。Claude Codeは`.claude/settings.json`から`scripts/tool-guard.py`を呼ぶ。Codexのscoutは`.codex/hooks.json`から同じhookを呼ぶので、Codexで一度このrepoを開いてhookを信頼する。練習では`config/tool-guard.json`に、対象の開催回を表す語（`contest_keywords`、例: `isucon14`。アプリのhost名に含まれる語は入れない）とベンチマーカーの置き場所（`benchmarker_paths`、例: `/home/isucon/bench`）を書く。練習の資料を取得し終えてから書くか、取得してよい資料のURLを`allowed_urls`に入れる。練習が終わったら空に戻す。ベンチnodeへの直接の接続は、設定がなくても止まる。

## アカウント・接続・参加準備

- 本番に使用するAWS profile/regionと、事前公開された必要権限・quota・ネットワーク条件を整理する。AWSへのログイン・認証再確認は準備の必須条件にせず、実際にAWSを操作するときに必要なら行う。
- GitHub・AI CLIが使えることと必要なSSH鍵を確認する。
- 事前公開の公式レギュレーション、当日前の設定期限、参加登録・連絡手段・ポータル等の準備を確認する。参加者本人しか確認できない項目は本人の確認を記録する。適用しない項目は対象外として理由を記す。

## ポータル（portalのセッションの前提）

開始後にportalのセッション（`/portal`）がすぐベンチ接続へ入れるよう、ブラウザとログインは開始前に済ませる。

- `chrome-debug`（`--remote-debugging-port=9222`のChrome）を起動し、Playwright MCPからページを開けることを確かめる。
- ポータルにログインできるようになったら（参加登録、事前ログイン）、chrome-debugでログインしておく。開始時にログインし直しが要るか（セッションの期限、開始時の切り替え）を確かめて記録する。開始までログインできないポータルなら「公開待ち」にし、開始時に人間がログインする。
- ログインした状態で`python3 scripts/portal-cookie.py --portal <URL> --check`を流し、cookieが取れることを確かめる（値は出ない）。ポータルのURLを`docs/preparation/readiness.md`に記録する。
- 開始用repoで`make worktree BRANCH=portal PURPOSE="ポータルの情報とベンチ接続"`を作っておく（問題の情報は入らない）。

## improveの作業場所

- 開始用repoで`make worktree BRANCH=improve PURPOSE="継続的な改善（/improve）"`を作っておく。開始と同時に人間がここでClaude Codeを起動して`/improve`を打つ。改善はこのworktreeの中で、変更ごとのbranch（`improve-<名前>`）を切って進める（`improve/<名前>`はbranch `improve`と名前がぶつかるので使わない）。

## 人間の作業場所

- 開始用repoで`make worktree BRANCH=human PURPOSE="人間の作業場所"`を作っておく。人間は競技中ずっとここを触り、mainの作業ツリーには触れない。
- 意見は`python3 scripts/scout/main.py post human "<本文>"`（急ぎは`--emergency`）、コードの変更は投稿（`scripts/proposal.py submit`）でmainへ渡す。投稿は変更ごとのbranch（`git switch -c human-<名前> <mainのbranch>`）から行い、投稿したら別のbranchへ移る（投稿したbranchは、採否が出るまでmainが追従させる）（[scout](../roles/scout.md)の「人間（human）」）。開始前にworktree `human`から`post human "接続確認（意見はまだない）"`を1回流し、`.local/scout/reports.md`に`## human`が出ることを確かめる。この報告は最初のベンチでmainに渡るので、渡っても困らない文面にしておく。

## 開始後へ残すもの

開始後に公開されるAMI・サーバー情報・正式手順を受け取り、サーバー起動、SSH確立、コード/schema先行回収、remoteのtoolchain・計測導入、実設定照合、完全import、deploy、初回baselineへ進む。実アプリ固有の依存解決・シナリオ読解・route設定・collectorの実データ検証も開始後に行う（[kickoff](kickoff.md)）。開始前に利用できない問題情報を使って先に済ませない。

## 完了条件

- 上の事前項目に未完了がなく、対象外・公開待ちには理由がある
- 使用版・初期入力・開始時点の条件を記録している
- private repoへ準備結果をcommit・pushし、`git status`がcleanである
- 開始は人間が宣言する

作業中のagentはrepoの作成・初回pushと許可範囲の準備を進めてよい。未確認を確認済みと扱わず、必要な人間の判断・操作を具体的に残す。
