> ## Documentation Index
> Fetch the complete documentation index at: https://docs.lithosai.com/llms.txt
> Use this file to discover all available pages before exploring further.

# Agent Integrations

> Connect popular coding agents to LithosAI.

The LithosAI APIs support many coding agents, including Codex, Claude Code, and
OpenCode. OpenCode connects directly. Codex and Claude Code each require a
different protocol, so a local translation proxy is needed.

LithosAI exposes the OpenAI Chat Completions API. Cline, Hermes Agent, Kilo Code, omp,
OpenClaw, OpenCode, OpenHands, and Pi speak that protocol directly. Codex and Claude Code
require a local translation proxy because they use different protocols.

| Agent | Client protocol | Integration path |
| - | - | - |
| [Cline](/agent-integrations/cline) | Chat Completions | Direct |
| [Hermes Agent](/agent-integrations/hermes-agent) | Chat Completions | Direct |
| [Codex](/agent-integrations/codex) | Responses API | LiteLLM compatibility adapter |
| [Kilo Code](/agent-integrations/kilo-code) | Chat Completions | Direct |
| [Claude Code](/agent-integrations/claude-code) | Anthropic Messages API | LiteLLM compatibility adapter |
| [omp](/agent-integrations/omp) | Chat Completions | Direct |
| [OpenClaw](/agent-integrations/openclaw) | Chat Completions | Direct |
| [OpenCode](/agent-integrations/opencode) | Chat Completions | Direct |
| [OpenHands](/agent-integrations/openhands) | Chat Completions | Direct |
| [Pi](/agent-integrations/pi) | Chat Completions | Direct |

## Before you begin

Install the agent you want to use, create a key on the [API Keys](https://console.lithosai.cloud/keys)
page, and confirm the model ID on the [Models](https://console.lithosai.cloud/models) page.
Export your key in the shell where you start the agent or its local proxy.

```bash theme={null}
export LITHOSAI_API_KEY="your-key"
```

<Note>
  LithosAI validates tool schemas strictly. An agent that loads an MCP server whose tool
  parameters are not a JSON Schema object is rejected with `invalid 'parameters' schema`.
  Disable that server or correct its schema.
</Note>

## Common issues

These apply to every agent guide.

### Limitations

* The minimal configuration enables text and tool calls. Turn on image input, reasoning, or
  other capability flags only when the selected LithosAI model supports them.
* Context window and output limits are local metadata that the agent cannot read from
  LithosAI. Copy them from the [Models](https://console.lithosai.cloud/models) page; each
  agent page names the field to set.
* Keep your LithosAI API key in the agent's own settings or an environment variable. Do not
  commit it to a project repository.

### Troubleshooting

| Symptom | Cause |
| - | - |
| Authentication fails | The LithosAI API key is missing, expired, or not visible to the process that runs the agent. |
| The model is not found or not selectable | The model ID does not exactly match the value on the [Models](https://console.lithosai.cloud/models) page. |
| Context usage or compaction is inaccurate | Update the context window and output limit from the Models page. |
| Requests return `404` | The base URL must be `https://api.lithosai.cloud/v1`, unless the agent page says otherwise. |
