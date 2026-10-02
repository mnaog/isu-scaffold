# 低難度タスクのローカル比較環境

practice-12のRust 1.63コンパイルエラー修正を、同じ開始コード・依頼文・検証条件で繰り返すための手順。AIはローカルでコードを読み、編集・検証・commitする。Docker、EC2、DB、deploy、ベンチは使わない。4ロール運用やforkの検証とは独立している。

## 固定する条件

| 項目 | 条件 |
| --- | --- |
| 開始コード | practice-12の `b2705e0c3f5ba3cca2d2ade99d5a71c19a3a64ff` |
| 配布範囲 | `webapp/rust`と既存テストが参照するDDL 1ファイル |
| Rust | `1.63.0-x86_64-apple-darwin` |
| 実行場所 | Apple Silicon Macのローカル。x86_64実行には既存のRosettaを使用 |
| 依存関係 | 元のCargo.lockを維持し、事前にvendor化。検証時は `--locked --offline` |
| ビルド | 4 jobs、incremental無効、各試行に独立した事前ビルド済みtarget |
| 共通依頼 | `config/ai-compare/low-task.txt` |
| 共通指示 | `config/ai-compare/AGENTS.md` |
| 成果物 | 実装差分、commit、最終回答、check/testの結果 |

Rustupは1.28.2、依存取得専用のCargoは1.85.1に固定する。課題のコンパイルは常にRust 1.63。導入先はscaffold内の `.local/ai-compare/native/` で、既存のHomebrew版Rustやシェル設定は変更しない。バージョン指定と専用ホームは[Rustupの仕組み](https://rust-lang.github.io/rustup/installation/index.html)を使う。

OS・clang・SDK・Pythonの版も記録し、試行生成・実行・独立検証時に変化を検出する。OS自体を再構築する仕組みではないので、変更後はsetupから新しい比較を始め、以前の条件と混ぜない。Linux上のアプリ動作を保証する課題でもない。

## 初回準備

scaffoldのルートで実行する。Python 3.12以上、Git、isuscope、Command Line Tools、x86_64実行可能なApple Silicon Mac、隣のpractice-12リポジトリが必要。

```bash
python3 scripts/ai-compare/manage.py setup
```

ソースの場所が異なる場合は `--source /path/to/practice-12` を指定する。setupはRust導入、依存取得、事前ビルド、開始コードのエラー再現まで行う。再実行可能。初回に必要なネットワーク通信と依存ビルドはAIの作業時間に含めない。

`.local/ai-compare/native/environment.json` に環境情報、`initial-check.log` と `initial-test.log` に開始時の失敗を保存する。開始コードは両コマンドとも元の公開範囲エラーで失敗することを検査する。

## 試行を生成する

モデルやエージェントを変えるたびに、新しいtrial IDを指定する。

```bash
python3 scripts/ai-compare/manage.py prepare --trial codex-low-01
python3 scripts/ai-compare/manage.py prepare --trial claude-low-01
python3 scripts/ai-compare/manage.py prepare --trial opencode-low-01
```

既定の生成先はscaffoldの隣の `ai-low-trials/<trial>/`。`--destination` で変更できる。元リポジトリから許可したソースだけを取り出し、履歴1件・remoteなしの独立Gitリポジトリを作る。修正後commit、過去の会話、ベンチ記録は渡さない。既存のtrial IDや生成先は上書きしない。

各試行でcheck/testを事前実行し、依存キャッシュと失敗状態を揃える。準備ログは `.local/preparation.jsonl`。開始commit、全配布ファイルのSHA256、環境情報は `.local/manifest.json` とscaffold側の `.local/ai-compare/native/trials/` に保存する。

## エージェントを実行する

生成先を作業ディレクトリにして、新しい会話に `TASK.txt` の全文を渡す。元のpractice-12や他の試行を参照させない。ベンチマーカー内部閲覧・ログ追加・外部検索・subagentは禁止。設定とツール権限も比較する試行間で揃える。

`run` は任意のエージェントCLIを包み、開始・終了時刻と終了コードを記録する。CLI固有のモデル指定・推論設定は実際の引数や設定側にも指定する。以下の山括弧部分は実値へ置き換える。

```text
python3 scripts/ai-compare/manage.py run \
  --destination ../ai-low-trials/codex-low-01 \
  --agent codex --model <実際のモデルID> \
  --endpoint <接続先名またはURL。秘密は含めない> \
  --reasoning <推論設定> --harness <CLI版と権限設定> \
  --settings <使用する設定ファイル> \
  -- <新規会話でTASK.txtを渡すCLIコマンドと引数>
```

`--settings` は省略・複数指定可能。内容をコピーせずSHA256だけ保存する。CLI実行ファイルのパスとSHA256、argvも記録するため、APIキーを引数に書かない。認証は各CLIの既存の方法を使う。新たなAPI依頼をsetup/prepareが自動で行うことはない。

タイマーはCLI起動から終了までを含む。対話モードで人間が放置した時間も含むため、時間比較には同じ方式の非対話実行を使う。CLIの正常終了と課題の完了は別に扱う。`.local/run.json` が実行記録。会話・モデル応答待ち等の詳細は各エージェントの履歴とagent-historyで扱う。

速度比較は1試行ずつ行う。検証コマンドはMacの共通ビルドlockで直列化し、待ち時間と実行時間を別記録するが、他プロセスのCPU負荷までは隔離しない。並行起動の動作確認とモデルの速度比較は別に扱う。

## 独立検証

```bash
python3 scripts/ai-compare/manage.py audit --destination ../ai-low-trials/codex-low-01
```

operator側のmanifestと検証スクリプトを使い、保護ファイルの変更、環境変化、未commit状態を検出し、check/testを再実行する。結果は試行の `.local/audit.json` と `audit-check.log`、`audit-test.log` に保存する。

さらに人間またはoperatorがdiffを確認し、既存テストが削除・弱体化されていないこと、動作を維持した修正であることを判断する。元の修正とのdiff一致は要求しない。ignoredのDB結合テストは今回の対象外。

再試行は新しいtrial ID・新しい会話で行い、失敗した試行も残す。AIが使う `verify.py` の実行区間は `.local/validation.jsonl` に残る。run、manifest、監査結果、会話のセッションIDを組み合わせて比較する。

## 準備時の確認結果

2026年10月1日、このMac（macOS 15.4、Apple clang 12.0.0、SDK 11.0）でsetupとその再実行を確認した。最初に試したARM版Rust 1.63では生成されたproc-macroライブラリに対するmacOSの署名エラーでSIGKILLになったため、既存のHomebrew版Rustと同じx86_64実行に固定した。アプリのソースや依存関係を変えてこの問題を回避してはいない。

検証専用の `operator-smoke-01` に既知の修正を適用し、check成功、test 15件成功・DB結合テスト1件ignored、commit作成、audit成功を確認した。この試行は環境の動作確認であり、AIの成績には数えない。未修正の `codex-low-01`、`claude-low-01`、`opencode-low-01` は全配布ファイルのハッシュと初期commit `f42133e22f57` が一致し、各リポジトリは履歴1件・remoteなし・変更なしである。

手順の保護機能は次で検証する。試行ID重複、環境設定改変、検証スクリプト改変、子プロセス失敗の記録と再利用拒否、ビルド環境変数の混入防止の5件が成功した。

```bash
python3 -m unittest discover -s tests -p test_ai_compare.py -v
```

途中で作成した専用AWSスタック `isu-ai-low-task` は削除済み。EC2のterminatedも確認した。

## OpenCodeでの実行

Lithos無印のDeepSeek V4.1 Flash、max推論、OpenCode build agent、agent-history有効で実行する入口を用意した。設定は `config/ai-compare/opencode.json`。キーは環境変数 `LITHOS_API_KEY`、またはGit対象外の既存 `.local/lithos-ultra/api-key` から読む。`--key-file` でも指定できる。

```bash
python3 scripts/ai-compare/manage.py prepare --trial opencode-low-03
python3 scripts/ai-compare/run-opencode.py --destination ../ai-low-trials/opencode-low-03
python3 scripts/ai-compare/manage.py audit --destination ../ai-low-trials/opencode-low-03
```

新しいtrialと新しい会話を使い、stdoutのJSONイベントとstderrを試行の `.local/` に残す。agent-historyが生成する `docs/agent-history/` は `.git/info/exclude` で課題のcommitから除外する。履歴は消さず、計測・会話の証跡として残す。

2026年10月1日の[実行結果](opencode-low-02.json)は、CLI起動から終了まで48.12秒、修正・commit・独立検証成功。check成功、test 15件成功・1件ignored。変更はadmin.rsの2つのハンドラの公開範囲のみで、既存の修正と同じだった。この1試行で他モデルとの優劣は判断しない。

最初の `opencode-low-01` は、cwdだけを渡しPWDを更新していなかったため、OpenCodeがscaffoldを作業場所として認識した。読み取り段階で停止し、ハーネス不備として比較から除外した。ログは残している。`run`でPWDを更新・OLDPWDを除去し、OpenCodeにも `--dir` を明示して、新しい `opencode-low-02` で実行し直した。子プロセスのcwdとPWDの一致は手順のテストにも加えた。

## Codexでの実行

`run-codex.py` は別のCodex CLIプロセスを新規会話で起動する。比較を指揮する会話には答えが含まれるため、その履歴は継承せず共通TASK.txtだけを渡す。モデルは `gpt-6-astra`、推論は `medium`。既存ChatGPT認証とagent-history hookを使用し、web検索・subagent・MCPを無効にする。設定は `config/ai-compare/codex.json`。モデルと推論の指定方法は[公式設定リファレンス](https://learn.chatgpt.com/docs/config-file/config-reference)と実機CLI helpで確認した。

```bash
python3 scripts/ai-compare/manage.py prepare --trial codex-low-02
python3 scripts/ai-compare/run-codex.py --destination ../ai-low-trials/codex-low-02
python3 scripts/ai-compare/manage.py audit --destination ../ai-low-trials/codex-low-02
```

2026年10月1日の[Codex実行結果](codex-low-01.json)は、76.71秒で修正・commit完了、独立検証も成功。実セッションのturn contextでも `gpt-6-astra` と `medium` を確認した。OpenCodeと初期commit・全配布ファイルのハッシュが一致し、結果の差分も同じだった。

| 実行 | 設定 | CLI起動から終了 | 独立検証 |
| --- | --- | ---: | --- |
| OpenCode | DS V4.1 Flash／Lithos無印／max | 48.12秒 | check成功、15 passed、1 ignored |
| Codex | GPT-6-Astra／medium | 76.71秒 | check成功、15 passed、1 ignored |

各1試行の結果。ハーネスと指定された推論設定は異なる。CodexのJSONイベントにはツール時刻がなく、hook受信時刻を実行時間と同一視しないため、ツール時間の合計はこの比較表に入れていない。

## Claude Codeでの実行

`run-claude.py` は新規のClaude Codeプロセスで共通TASK.txtを実行する。`claude-opus-5-5`とmediumを固定し、既存サブスクリプション認証とagent-historyを使う。web検索・subagent・MCP・LSP・auto-memoryは無効、fast modeはoff。設定は `config/ai-compare/claude.json`。

```bash
python3 scripts/ai-compare/manage.py prepare --trial claude-low-02
python3 scripts/ai-compare/run-claude.py --destination ../ai-low-trials/claude-low-02
python3 scripts/ai-compare/manage.py audit --destination ../ai-low-trials/claude-low-02
```

2026年10月1日の[Claude実行結果](claude-low-01.json)は43.56秒、修正・commit・独立検証成功。実際の応答モデルもOpus 5.5、権限拒否は0件。check成功、test 15件成功・1件ignored。他の2試行と開始コード・共通入力・最終差分が一致した。実行中、operatorは中難度候補のGit差分を読み取り調査したが、別のビルドやモデル比較は実行していない。

## 中難度候補の規模

[調査記録](medium-candidate.json)に、`3c551cb → 2ce2355`の範囲と検証上の注意を記録した。Rust 3ファイルで追加228行・削除38行、合計266行の差分。文書込み272行。テストを含め約300行の差分という意味では候補に合うが、新規実装だけで300行ではない。

登録時の完全な所持品キャッシュ、プレゼント受取後の維持、確認SELECTの削減を一つの課題にする。元commitに含まれるキャッシュ容量増加は別の設定変更なので課題から外す。MySQL正本・失敗時の失効・DBフォールバック・初期化の整合性を保つことを完了条件に含める。

中難度はローカルの使い捨てMySQLで既存結合テストを実行し、API/DB結果と対象SELECT削減を確認する環境が必要。既存テストには「登録後は所持品不明」という旧動作の期待があるため、その期待は今回の目的に合わせて更新する。低難度のテスト変更禁止ルールをそのまま転用しない。中難度の実行環境と共通受入テストは[再現手順](medium-environment.md)を参照。

## 解き方の分析

[低難度タスクの解き方の比較](low-process-analysis.md)に、実セッションの操作に基づく比較と、中難度で観察する項目をまとめた。速さだけでなく、調査の根拠、変更範囲、検証の信頼性、手戻り、人間の介入量を評価する。

中難度も同じ3設定で実行した。[結果と解き方](medium-process-analysis.md)、[再現手順](medium-environment.md)を参照。
