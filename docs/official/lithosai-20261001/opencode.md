> ## Documentation Index
> Fetch the complete documentation index at: https://docs.lithosai.com/llms.txt
> Use this file to discover all available pages before exploring further.

# Use LithosAI with OpenCode

> Connect OpenCode directly to LithosAI through the OpenAI Chat Completions API.

## Before you begin

Create a key on the [API Keys](https://console.lithosai.cloud/keys) page, confirm the model ID
on the [Models](https://console.lithosai.cloud/models) page, and export the key in the shell where you start OpenCode.

```bash theme={null}
export LITHOSAI_API_KEY="your-key"
```

OpenCode reaches LithosAI directly, with no translation proxy or extra adapter. After you
[install OpenCode](https://opencode.ai/docs), declare the provider in `opencode.json`, either
in a project or at `~/.config/opencode/opencode.json`.

<Note>
  This connection uses OpenCode's OpenAI-compatible Chat Completions adapter. Features that
  require another API protocol are not available through this provider configuration.
</Note>

```json opencode.json theme={null}
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "lithosai": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "LithosAI",
      "options": {
        "baseURL": "https://api.lithosai.cloud/v1",
        "apiKey": "{env:LITHOSAI_API_KEY}"
      },
      "models": {
        "moonshotai/Kimi-K3": { "name": "Kimi K3" },
        "moonshotai/Kimi-K3-fast": { "name": "Kimi K3 fast" }
      }
    }
  },
  "model": "lithosai/moonshotai/Kimi-K3"
}
```

`@ai-sdk/openai-compatible` is the package for Chat Completions providers, and OpenCode
bundles it. Start OpenCode, then run `/models` to confirm that the LithosAI models appear.

```bash theme={null}
opencode
```

Issues common to every agent are listed under [Common issues](/agent-integrations#common-issues).
