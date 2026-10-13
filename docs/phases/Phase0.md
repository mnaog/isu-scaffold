# Phase 0: 本番開始前の準備

## 目的と境界

本番開始前に許され、かつ問題固有の情報なしで実施できる準備を終える。Phase 1開始時には、問題公開後にしかできない作業だけが残る状態にする。repo作成だけでPhase 0完了とはしない。

事前に公開された公式情報と汎用tool・スクリプト・空の設定雛形を準備する。接続情報等が事前配布される場合は、その時点で許可された範囲を記録して準備する。

全項目を「確認済み」「未完了」「対象外」「公開待ち」に分け、確認日・使用版・根拠を`docs/phase0/readiness.md`へまとめる（長い検証記録は`docs/phase0/`の別ファイルに置き、readinessから参照する）。秘密情報と生の認証出力は`.local/`へ置く。「公開待ち」は本番でも開始前に取得できない情報に限る。CLI未導入、未検証の起動経路は公開待ちにしない。保留した事前確認があればPhase 0は未完了である。

## repoと再現条件

- 現在のscaffoldから独立したGit repoを作り、privateのGitHub repoへpushしてprivateであることを確認する。過去の履歴・別大会の成果物・接続情報はコピーしない。scaffoldのrootで`python3 scripts/new-contest-repo.py <新しいdirectory> --github <owner>/<name>`を実行すると、commit済みのscaffoldから`config/new-repo.json`の除外（scaffold自身の会話履歴、AI比較の実験、別件の資料）を除いてコピーし、新しい履歴で始め、privateのrepoを作ってpushする。本番では`practice.md`とAGENTS.mdの練習用の2行も消す（練習の開始用repoなら`--practice`で残す）。コピー元のcommitは`docs/phase0/scaffold-source.json`、準備状況のひな形は`docs/phase0/readiness.md`に入り、以降の確認はそのrepoで記録する。以降のAIセッションは新しいrepoの中で起動し直す（scaffoldで起動したセッションの会話は新しいrepoへ記録されず、isuscopeがベンチをその会話へ結び付けられない）。
- scaffold、isuscope、agent-history、各AI CLIの使用版を記録する。未コミットのテンプレ変更を使う場合はファイルのhashまたは差分も残す。
- 会話履歴以外のAI初期入力、起動プロンプト、モデル、推論設定、ハーネス設定を明示する。入力に別の問題の分析・コードを混ぜない。
- Gitのcommit・push、SSH鍵管理、`.local/`の除外、ClaudeのAGENTS symlinkを確認する。

## ローカル実行環境

- git、gh、bash、make、SSH、curl、tar、jq、zstd、Python 3.12以上を確認する。
- `./scripts/ansible-install.sh --all`を実行し、legacy・modernの両controllerのlocalhostでAnsible moduleが動くことを確認する。Pythonと解決した依存版を記録する。対象nodeのPythonはPhase 1で`ansible-runtime.py`が調べて選ぶ。
- 使用版のisuscopeをbuild/installし、`lock`、`routes`、`rescore`、`doctor`等の必要なコマンドがあることを確認する。version表示だけで同一buildとは判定しない。
- Rust、Cargo、linker、採用予定の汎用開発ツールを確認し、問題を使わない小さなRustプログラムでbuild・testする。事前に分かるRust版は準備し、配布アプリの正確なtoolchain・Cargo依存・DB schemaは公開後に合わせる。
- `./scripts/fetch-observability-tools.sh`で、repoへ取り込んだalp・slp・FlameGraphがpin済みの版とSHA256SUMSに一致することを確認する。版を上げる場合だけ`--update`で取り直してcommitする。
- `make builder-images`で`config/rust-builder/bases.json`の候補base×targetのbuilder imageを作り、所要時間と取得できなかったtagを記録する。問題のコードは使わない。
- 配布可能な計測toolの取得元・版を固定する。ローカルへ事前取得する場合はhashと再取得手順を残す。サーバーのOS・architecture・kernelに依存するpackageの選択と導入は公開後に行う。
- `bash tests/initial-automation.sh`と`make test`で、問題や実サーバーを使わずimport/deploy/rollback、worktree、ボードの動作を確認する。

## AIと運用経路

- 利用予定のAIについて、CLI導入・認証・利用モデルでの新規セッション応答を確認する。代替モデルへ黙って切り替えない。
- 各利用予定CLIで会話履歴の保存を確認する。短い接続確認を使い、問題の探索は開始しない。
- AI laneが使う`claude -p`を、`config/lanes.json`のmodel・effort・permission modeで短く実行して応答を確認する（例: `echo "Reply OK" | claude -p --model claude-opus-5-5 --effort high --permission-mode dontAsk`）。問題の探索は開始しない。
- Phase 1のコード先行回収と非同期buildの手順を確認する。
- ボード（`make board`）のローカル表示を確認する。テストデータ・常駐processは開始前に残さない。
- 禁止操作を止めるhookを確認する。Claude Codeは`.claude/settings.json`、Codexは`.codex/hooks.json`から`scripts/tool-guard.py`を呼ぶ。Codexでは初めて開いたときにhookを信頼する。練習では`config/tool-guard.json`に、対象の開催回を表す語（`contest_keywords`、例: `isucon14`。アプリのhost名に含まれる語は入れない）とベンチマーカーの置き場所（`benchmarker_paths`、例: `/home/isucon/bench`）を書く。練習の資料を取得し終えてから書くか、取得してよい資料のURLを`allowed_urls`に入れる。練習が終わったら空に戻す。ベンチnodeへの直接の接続は、設定がなくても止まる。

## アカウント・接続・参加準備

- 本番に使用するAWS profile/regionと、事前公開された必要権限・quota・ネットワーク条件を整理する。AWSへのログイン・認証再確認はPhase 0の必須条件にせず、実際にAWSを操作するときに必要なら行う。
- GitHub・AI CLIが使えることと必要なSSH鍵を確認する。
- 事前公開の公式レギュレーション、当日前の設定期限、参加登録・連絡手段・ポータル等の準備を確認する。参加者本人しか確認できない項目は本人の確認を記録する。適用しない項目は対象外として理由を記す。

## Phase 1へ残すもの

開始後に公開されるAMI・サーバー情報・正式手順を受け取り、サーバー起動、SSH確立、コード/schema先行回収、remoteのtoolchain・計測導入、実設定照合、完全import、deploy、初回baselineへ進む。実アプリ固有の依存解決・シナリオ読解・route設定・collectorの実データ検証もPhase 1で行う。開始前に利用できない問題情報を使って先に済ませない。

## 完了条件

- 上の事前項目に未完了がなく、対象外・公開待ちには理由がある
- 使用版・初期入力・開始時点の条件を記録している
- private repoへ準備結果をcommit・pushし、`git status`がcleanである
- 人間がPhase 1の開始を決定する

作業中のagentはrepoの作成・初回pushと許可範囲の準備を進めてよい。未確認を確認済みと扱わず、必要な人間の判断・操作を具体的に残す。
