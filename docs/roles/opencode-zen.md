# OpenCode + DeepSeek V4.1 Flash / Zen

この文書はZen接続の検証履歴。現在の通常運用とscoutは[Lithos無印の接続手順](opencode-lithos.md)を使う。

2026-10-01にOpenCode CLI 1.18.34を`npm install -g opencode-ai@1.18.34`で導入した。モデルはZenの`opencode/deepseek-v4.1-flash`を明示する。

## ユーザーが行う認証

1. [OpenCode Zen](https://opencode.ai/auth)へログインし、課金・クレジットを設定してAPIキーを作成する。
2. 自分のターミナルで以下を実行し、APIキーを入力する。キーは会話やGit管理ファイルへ貼らない。

```bash
opencode auth login --provider opencode
```

APIキーはOpenCode標準の`~/.local/share/opencode/auth.json`に保存される。既存のZ.AI認証は変更しない。CLIを別のNode環境から使う場合は、その環境のPATHにopencodeがあることを確認する。

## 接続確認

```bash
opencode auth list
opencode models opencode --refresh
./scripts/scout once opencode --smoke
```

認証前は`models opencode`に無料モデルだけが表示されることを実機で確認した。モデル名の近い別モデルへ置き換えない。認証後も指定モデルが表示されなければ、利用可否とカタログを再確認する。

smokeが成功したらローカルログに「接続確認済み」が残る。定期実行の開始は人間がPhase 2への移行を決めた後の`make scout-start`で別途行う。

## scaffoldの設定

`config/operations.json`に以下を設定済み。

```json
{
  "model": "opencode/deepseek-v4.1-flash",
  "argv": ["opencode", "run", "--model", "{model}", "--format", "json", "--title", "scout-{session}"],
  "output": "opencode-json"
}
```

共通プロンプトはstdinに渡す。`--session`、`--continue`は付けず、毎回新しいセッションを作る。JSONイベントから実セッションIDと最後の正常完了messageのtextを取得する。探索途中の発言・tool出力は報告へ入れない。エラーや未完了は報告として保存しない。モデルIDは要求値として記録する（イベント自体には実際のモデルIDが含まれない）。

## 根拠と検証範囲

[Zen公式ドキュメント](https://opencode.ai/docs/zen)にDeepSeek V4.1 FlashのモデルID `deepseek-v4.1-flash` とZenの提供先が記載されている。[CLIドキュメント](https://opencode.ai/docs/cli/)と実機helpで認証・run引数を確認した。

ユーザーによるZen認証後、`./scripts/scout once opencode --smoke`がexit 0で「接続確認済み」を返した。指定モデルでの新規セッション起動・stdin入力・最終回答抽出まで実機確認済み。ログは`.local/operations/smoke/opencode/f8478e70-45fc-4aef-bbc8-0663c1445c77/`。途中発言の除外、エラー・未完了拒否は偽CLIテストでも確認済み。定期実行は開始していない。
