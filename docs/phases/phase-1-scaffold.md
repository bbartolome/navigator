# Phase 1: Scaffold plugin + MCP server

Milestone 1 in [navigator-plugin-plan.md](../navigator-plugin-plan.md) §10. Layout is §3, tool surface is §4,
platform reference is §2. Nothing here changes those sections.

## Scope

Create the plugin manifest, `.mcp.json`, and a `uv` project whose stdio MCP server exposes two stub tools,
`navigator_start` and `navigator_status`. No session persistence, no Ollama, no skills (phases 2 to 5).

## Files to create

```
.claude-plugin/plugin.json
.mcp.json
server/pyproject.toml
server/src/navigator_mcp/__init__.py      # empty
server/src/navigator_mcp/server.py
```

### `.claude-plugin/plugin.json`

Fields per [plugins-reference](https://code.claude.com/docs/en/plugins-reference) (Fields table): `name` required,
`version`/`description`/`author` optional (validator warns when they are missing). `.mcp.json` at the plugin root is
loaded by default, so no `mcpServers` key is set.

```json
{
  "name": "navigator",
  "version": "0.1.0",
  "description": "Pairs Claude with a local Ollama navigator model through an MCP server.",
  "author": { "name": "Ben B" }
}
```

### `.mcp.json`

Entry format: top-level `mcpServers` map; an entry with no `type` is read as stdio; `${CLAUDE_PLUGIN_ROOT}` resolves in
`command`, `args`, `env` ([mcp](https://code.claude.com/docs/en/mcp), "Plugin-provided MCP servers"). Values per §3
and §8. No `timeout` is set, so Claude Code's default applies (main doc §9b). To set a job deadline, add
`"env": {"NAVIGATOR_JOB_TIMEOUT": "<seconds>"}`, or append `"--job-timeout", "<seconds>"` to `args`.

```json
{
  "mcpServers": {
    "navigator": {
      "command": "uv",
      "args": ["run", "--project", "${CLAUDE_PLUGIN_ROOT}/server", "navigator-mcp"]
    }
  }
}
```

### `server/pyproject.toml`

`[project.scripts]` format and the `[build-system]` requirement for `uv run <script>` are from
[uv init](https://docs.astral.sh/uv/concepts/projects/init/) (a build system installs the project into the
environment). Python 3.11+ is from the [python-sdk README](https://github.com/modelcontextprotocol/python-sdk).
Only `mcp` is added now; `httpx`, `pydantic`, `llmlingua` (§3) are added by the phases that import them.

```toml
[project]
name = "navigator-mcp"
version = "0.1.0"
description = "Navigator MCP server"
requires-python = ">=3.11"
dependencies = ["mcp>=2.3.0"]

[project.scripts]
navigator-mcp = "navigator_mcp.server:main"

[build-system]
requires = ["uv_build>=0.13.6,<0.14"]
build-backend = "uv_build"
```

### `server/src/navigator_mcp/server.py`

API facts, from the python-sdk source on `main`
([mcp/server/__init__.py](https://github.com/modelcontextprotocol/python-sdk/blob/main/src/mcp/server/__init__.py),
[mcp/server/mcpserver/server.py](https://github.com/modelcontextprotocol/python-sdk/blob/main/src/mcp/server/mcpserver/server.py)):
- `from mcp.server import MCPServer` is exported.
- `MCPServer(name: str | None = None, ...)`.
- `run(transport: Literal["stdio", "sse", "streamable-http"] = "stdio", **kwargs)`; stdio is the default.
- Tools are declared with `@mcp.tool()` ([README](https://github.com/modelcontextprotocol/python-sdk)).

```python
from typing import Literal

from mcp.server import MCPServer

mcp = MCPServer("navigator")

_sessions: dict[str, dict[str, str]] = {}


@mcp.tool()
def navigator_start(mode: Literal["pair", "scout"], goal: str, context: str) -> str:
    """Start a navigator session and return its session_id. Stub: stores the inputs in memory."""
    session_id = f"s{len(_sessions) + 1}"
    _sessions[session_id] = {"mode": mode, "goal": goal, "context": context}
    return session_id


@mcp.tool()
def navigator_status(session_id: str) -> str:
    """Return the session summary and metrics. Stub: returns the stored inputs as text."""
    s = _sessions.get(session_id)
    if s is None:
        raise ValueError(f"unknown session_id: {session_id}")
    return f"session {session_id}\nmode: {s['mode']}\ngoal: {s['goal']}\nmetrics: none"


def main() -> None:
    mcp.run()  # transport defaults to "stdio"
```

Stub behaviour:

| Tool | Args | Behaviour |
| --- | --- | --- |
| `navigator_start` | `mode: "pair"\|"scout"`, `goal: str`, `context: str` | Returns `"s1"`, `"s2"`, ... in call order. Stores inputs in a process-local dict. |
| `navigator_status` | `session_id: str` | Returns the three-line text above for a known id. Raises `ValueError` for an unknown id. |

## Commands

Run in order from the repo root unless stated.

```
cd server && uv sync                      # creates server/.venv, installs mcp
cd server && uv run navigator-mcp         # starts the stdio server; blocks on stdin; Ctrl-D exits
claude plugin validate .                  # manifest + .mcp.json entry checks
claude --plugin-dir .                     # then run /mcp in the session
```

`claude plugin validate` checks `.mcp.json` entries on Claude Code v2.1.281 or later
([plugins-reference](https://code.claude.com/docs/en/plugins-reference)).

## Smoke tests

1. **Entry point resolves.** `cd server && uv run navigator-mcp </dev/null` exits with status 0 and prints nothing to
   stdout.
2. **JSON-RPC over stdio.** Pipe these lines (one JSON object per line) to `uv run navigator-mcp`:
   ```
   {"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}
   {"jsonrpc":"2.0","method":"notifications/initialized"}
   {"jsonrpc":"2.0","id":2,"method":"tools/list"}
   {"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"navigator_start","arguments":{"mode":"pair","goal":"g","context":"c"}}}
   {"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"navigator_status","arguments":{"session_id":"s1"}}}
   ```
   Expected: id 1 result has `serverInfo.name == "navigator"`; id 2 lists exactly `navigator_start` and
   `navigator_status`, with `mode` in the `navigator_start` input schema constrained to `pair`/`scout`; id 3 content
   text is `s1`; id 4 content text is `session s1\nmode: pair\ngoal: g\nmetrics: none`.
3. **Unknown id.** `navigator_status` with `session_id: "nope"` returns a result with `isError: true`.
4. **Plugin load.** In `claude --plugin-dir .`, `/mcp` lists server `navigator` as connected with 2 tools, marked as
   plugin-provided ([mcp](https://code.claude.com/docs/en/mcp), "`/mcp` behavior"). The callable tool names are
   `mcp__plugin_navigator_navigator__navigator_start` and `mcp__plugin_navigator_navigator__navigator_status`.

## Done criteria

- `uv sync` and `uv run navigator-mcp` succeed in `server/`.
- `claude plugin validate .` reports `Validation passed` with no warnings.
- Smoke tests 1 to 4 produce the expected outputs.
- `/mcp` in a `claude --plugin-dir .` session shows `navigator` connected with two tools.

## Open questions

Phase-affecting questions from §11:
- Is `uv` available wherever the plugin runs? (Smoke test 4 depends on it; `.mcp.json` `command` is `uv`.)
- Do MCP tool calls prompt for permission after the skill's invoking turn? (Observe during smoke test 4 by calling a
  tool from the session with no allow rule; the allow rule `mcp__plugin_navigator_navigator__*` is only added if it
  prompts.)

Unverified platform and library details:
- Is `MCPServer` present in the PyPI `mcp` release pinned above? PyPI reports `info.version` 2.3.0 and
  `requires_python >=3.10`; `MCPServer` was checked only on `main`. The README states Python 3.11+. Which bound is
  correct?
- Does an uncaught exception in a tool handler produce `isError: true` in the `tools/call` result (smoke test 3)?
  The python-sdk docs fetched do not state this.
- Does the server accept `protocolVersion` `2025-06-18` in smoke test 2, or does the SDK require a different value?
- Does anything printed to stdout other than JSON-RPC break the stdio transport? The README fetched has no logging
  note; check the SDK docs at https://py.sdk.modelcontextprotocol.io/.
- Does `uv run --project <dir>` run the script with the user's working directory unchanged? The `--project` flag
  comes from §3; it was not checked in the uv docs.
- Is the `uv_build` version range in `[build-system]` current? It is copied from the uv init page.
- Is `session_id` format `s<N>` acceptable, or should it be unique across server restarts? (Phase 2 owns persistence.)
