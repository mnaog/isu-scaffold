# OpenCodeとLithosの無印モデル

2026-10-01、ユーザーの指定で通常のOpenCodeとOpenCode scoutをLithosの無印へ統一した。

- モデルID: `lithosai/deepseek-ai/DeepSeek-V4.1-Flash`
- API: `https://api.lithosai.cloud/v1`
- OpenCodeアダプター: `@ai-sdk/openai-compatible`
- 通常起動: `opencode`
- 明示起動: `opencode --model lithosai/deepseek-ai/DeepSeek-V4.1-Flash`
- scout設定: `config/operations.json` の `scouts.opencode`

このMacでは `~/.config/opencode/opencode.jsonc` にproviderとデフォルトモデルを設定している。APIキーはGit対象外の `.local/lithos-ultra/api-key` をファイル参照する。ディレクトリ名は初回導入時のものを維持しているが、使用モデルは無印である。キーをGitへ追加しない。

別の端末では[公式手順](https://docs.lithosai.com/agent-integrations/opencode)で同じprovider ID・モデルIDを登録する。上位プランへの自動フォールバックは設定しない。既存のZen認証は削除していない。

接続確認は `opencode models lithosai` と `./scripts/scout once opencode --smoke` を使う。定期実行の開始はPhase 2移行の判断後に行う。性能の比較結果は[実測レポート](../research/lithos-opencode-latency-20261001.md)を参照する。

2026年10月1日、モデル登録の確認とscoutのsmokeに成功し、「接続確認済み」の応答を得た。
