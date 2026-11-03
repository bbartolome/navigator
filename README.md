# navigator

A Claude Code plugin that pairs Claude with a local Ollama navigator model through an MCP server.

Claude is the driver: it implements, runs tool calls and checks claims. The navigator model decides plans and settles
questions. Claude reaches the navigator only through `navigator_*` MCP tools, never by calling Ollama directly.

## Status

- Phases 1–6 are implemented: session state, the Ollama loop, async jobs with MCP Tasks, pair mode, scout mode, and
  compression.
- Phase 7, retiring the old `to-llm` and `scout-ahead` skills, is not done.
- The plan is in [`docs/navigator-plugin-plan.md`](docs/navigator-plugin-plan.md).

## Requirements

- An Ollama server reachable at `NAVIGATOR_OLLAMA_URL`, with the `NAVIGATOR_MODEL` model loaded.
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
- `/navigator:scout` agrees a question list from closed tickets, then settles each question in turn and ends with an
  Approve / Reject / Other prompt. It replaces `scout-ahead`.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `NAVIGATOR_OLLAMA_URL` | `http://host.docker.internal:11434` | Ollama endpoint |
| `NAVIGATOR_MODEL` | `qwen-agent:latest` | Navigator model |
| `NAVIGATOR_STATE_DIR` | `$CLAUDE_PLUGIN_DATA/sessions` | Session state; required when `CLAUDE_PLUGIN_DATA` is unset |
| `NAVIGATOR_JOB_TIMEOUT` | unset (no deadline) | Job deadline in seconds. The server also takes `--job-timeout <s>` |
| `NAVIGATOR_503_RETRIES` | `8` | Retries when Ollama answers 503 |
| `NAVIGATOR_COMPRESSION` | `off` | `on` compresses summaries with LLMLingua-2 |
| `NAVIGATOR_COMPRESSION_RATE` | `0.33` | Compression rate, in (0, 1] |
| `NAVIGATOR_LLMLINGUA_MODEL` | `microsoft/llmlingua-2-xlm-roberta-large-meetingbank` | Compression model |
| `NAVIGATOR_UV_VERSION` | `0.13.6` | uv version the launcher installs when uv is missing; set in `mcp/navigator.json` |

`NAVIGATOR_MODEL` and `NAVIGATOR_OLLAMA_URL` are server-wide. The server reads them when a job starts its Ollama
client, so a change takes effect after the server restarts. They are not per session or per call. Set them in the
`env` block of `mcp/navigator.json`, or export them in the shell before starting `claude`.

Compression is off by default. Turning it on requires the `compression` extra (see Development).

## Development

```
cd server
uv sync                  # base dependencies
uv run pytest            # 231 tests; the real-model test is skipped without the compression extra
uv sync --extra compression   # optional: llmlingua, torch and the model, for the real-model test
```

From the repo root, `claude plugin validate .` checks the plugin and marketplace manifests.

## Documentation

- [`docs/navigator-plugin-plan.md`](docs/navigator-plugin-plan.md): the plan.
- [`docs/phases/`](docs/phases/): one document per phase.
- [`docs/fix-navigator-record.md`](docs/fix-navigator-record.md): the `navigator_record` error fix.

## Licence

Apache-2.0. See [`LICENSE`](LICENSE).
