# navigator

A Claude Code plugin that pairs Claude with a local Ollama navigator model through an MCP server.

Claude is the driver: it implements, runs tool calls and checks claims. The navigator model decides plans and settles
questions. Claude reaches the navigator only through `navigator_*` MCP tools, never by calling Ollama directly.

## Status

- Phases 1–6 are implemented: session state, the Ollama loop, async jobs with MCP Tasks, pair mode, scout mode, and
  compression.
- The plan is in [`docs/navigator-plugin-plan.md`](docs/navigator-plugin-plan.md).

## Requirements

- An Ollama server reachable at `NAVIGATOR_OLLAMA_URL`, with the `NAVIGATOR_MODEL` model loaded.
- With `NAVIGATOR_PROVIDER=anthropic`, the navigator needs Claude API credentials and no Ollama server.
- `uv` on the PATH is recommended. Without it, the launcher falls back to a pip-based virtualenv.

## Install

From GitHub:

```
/plugin install navigator --marketplace bbartolome/navigator
```

From a local clone:

```
claude plugin marketplace add <path-to-clone>
claude plugin install navigator@navigator --scope user
```

## Usage

- `/navigator:pair <task>` pairs on a task. The navigator sets the plan and settles questions while Claude implements,
  runs the navigator's tool calls and checks its claims. It replaces `to-llm`.
- `/navigator:scout [effort]` agrees a question list from the resolved tickets in `.scratch/<effort>/issues/`, or from the
  decisions you confirmed in this conversation when there are none, then settles each question in turn and ends with an
  Approve / Reject / Other prompt. It replaces `scout-ahead`.
- Subagents can run the same workflows through `navigator:pair-driver` and `navigator:scout-driver`. Each reads its skill's
  `SKILL.md`, so the steps stay in one place. Both skills stay user-only (`disable-model-invocation: true`), so the main
  session does not start them on its own. A subagent cannot ask the user, so when it needs an answer it ends with a
  `Needs user:` message. The parent asks the user and continues the subagent with SendMessage. The scout driver also
  takes the parent's confirmed decisions from its prompt, since it cannot see the parent conversation.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `NAVIGATOR_PROVIDER` | unset | `ollama` or `anthropic`. Unset or empty means `ollama`; any other value fails each job |
| `NAVIGATOR_OLLAMA_URL` | `http://host.docker.internal:11434` | Ollama endpoint. The `anthropic` provider does not read it |
| `NAVIGATOR_MODEL` | `qwen-agent:latest` for `ollama`; `claude-opus-5-5` for `anthropic` | Navigator model |
| `ANTHROPIC_API_KEY` | unset | Claude API key. The `anthropic` provider reads it; the SDK also reads other sources, such as an `ant auth login` profile |
| `NAVIGATOR_STATE_DIR` | `$CLAUDE_PLUGIN_DATA/sessions` | Session state; required when `CLAUDE_PLUGIN_DATA` is unset |
| `NAVIGATOR_JOB_TIMEOUT` | unset (no deadline) | Job deadline in seconds. The server also takes `--job-timeout <s>` |
| `NAVIGATOR_503_RETRIES` | `8` | Retries when Ollama answers 503, or when the Claude API answers 429, 503 or 529 |
| `NAVIGATOR_COMPRESSION` | `off` | `on` compresses summaries with LLMLingua-2 |
| `NAVIGATOR_COMPRESSION_RATE` | `0.33` | Compression rate, in (0, 1] |
| `NAVIGATOR_LLMLINGUA_MODEL` | `microsoft/llmlingua-2-xlm-roberta-large-meetingbank` | Compression model |
| `NAVIGATOR_UV_VERSION` | `0.13.6` | uv version the launcher installs when uv is missing; set in `mcp/navigator.json` |

`NAVIGATOR_PROVIDER`, `NAVIGATOR_MODEL` and `NAVIGATOR_OLLAMA_URL` are server-wide. The server reads them when a job
starts its navigator client, so a change takes effect after the server restarts. They are not per session or per call.
Set them in the `env` block of `mcp/navigator.json`, or export them in the shell before starting `claude`.

With the `anthropic` provider, the navigator sends excerpts, summaries and tool results to the Claude API. Export
`ANTHROPIC_API_KEY` in the shell before starting `claude`, not in the `env` block of `mcp/navigator.json`. With no
`NAVIGATOR_JOB_TIMEOUT`, the SDK applies a 600 s read timeout to each request, and `NAVIGATOR_MODEL=claude-opus-4-0` or
`claude-opus-4-1` fails every job; set `NAVIGATOR_JOB_TIMEOUT` to use them.

Compression is off by default. Turning it on requires the `compression` extra (see Development).

## Development

```
cd server
uv sync                  # base dependencies
uv run pytest            # 355 tests; the real-model test is skipped without the compression extra
uv sync --extra compression   # optional: llmlingua, torch and the model, for the real-model test
```

From the repo root, `claude plugin validate .` checks the plugin and marketplace manifests.

## Documentation

- [`docs/navigator-plugin-plan.md`](docs/navigator-plugin-plan.md): the plan.
- [`docs/phases/`](docs/phases/): one document per phase.
- [`docs/fix-navigator-record.md`](docs/fix-navigator-record.md): the `navigator_record` error fix.

## Licence

Apache-2.0. See [`LICENSE`](LICENSE).
