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
`version`/`description`/`author` optional (validator warns when they are missing). Decided 2026-11-02: the server config lives at `mcp/navigator.json`, and `plugin.json` sets `"mcpServers": "./mcp/navigator.json"`. A root `.mcp.json` is also read as a project server, which duplicated the plugin's server. `.mcp.json` at the plugin root is
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

The command is a launcher script, so the plugin doesn't depend on `uv` or `pip` being installed (main doc §2, "Python
runtime for the plugin").

```json
{
  "mcpServers": {
    "navigator": {
      "command": "${CLAUDE_PLUGIN_ROOT}/scripts/launch.sh",
      "args": []
    }
  }
}
```

### `scripts/launch.sh`

`CLAUDE_PLUGIN_ROOT` and `CLAUDE_PLUGIN_DATA` are exported to stdio MCP server processes (main doc §2). stdout carries
the MCP protocol, so every installer line goes to stderr. Untested sketch:

```bash
#!/usr/bin/env bash
set -eu
D="${CLAUDE_PLUGIN_DATA:?}"; R="${CLAUDE_PLUGIN_ROOT:?}"
V="$D/venv"; STAMP="$D/pyproject.stamp"
log(){ echo "[navigator] $*" >&2; }
if [ ! -x "$V/bin/navigator-mcp" ] || ! cmp -s "$R/server/pyproject.toml" "$STAMP"; then
  UV="$(command -v uv || true)"
  if [ -z "$UV" ] && [ ! -x "$D/uv-bin/uv" ]; then
    curl -LsSf "https://astral.sh/uv/${NAVIGATOR_UV_VERSION:?pin required}/install.sh" \
      | UV_UNMANAGED_INSTALL="$D/uv-bin" sh >&2 || log "uv install failed; falling back to venv"
  fi
  [ -z "$UV" ] && [ -x "$D/uv-bin/uv" ] && UV="$D/uv-bin/uv"
  rm -rf "$V"
  if [ -n "$UV" ]; then
    "$UV" venv "$V" >&2 && "$UV" pip install --python "$V/bin/python" "$R/server" >&2
  else
    python3 -c 'import sys;sys.exit(sys.version_info<(3,10))' || { log "need Python >= 3.10"; exit 1; }
    python3 -m venv "$V" >&2 2>&1 || { rm -rf "$V"; python3 -m venv --without-pip "$V" >&2
      curl -sSf https://bootstrap.pypa.io/get-pip.py | "$V/bin/python" >&2; }
    "$V/bin/python" -m pip install "$R/server" >&2
  fi
  cp "$R/server/pyproject.toml" "$STAMP"
fi
exec "$V/bin/navigator-mcp" "$@"
```

- Fallback order: an existing `uv`; a pinned uv in `${CLAUDE_PLUGIN_DATA}/uv-bin`; a venv, with a `--without-pip` +
  get-pip fallback when `ensurepip` is missing.
- Reinstalls only when `server/pyproject.toml` changes. Later starts are offline.
- llmlingua/torch are not installed here (phase 6 adds an explicit step).
- `NAVIGATOR_UV_VERSION` is a placeholder for the pin. The value is chosen when implementing (open questions below).

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
- Launcher: which uv version to pin, and how to verify the installer download? The uv installer docs show a
  version-pinned URL but no checksum step.
- Does a first-run install fit within Claude Code's MCP server startup timeout (`MCP_TIMEOUT`)? If not, add an
  optional SessionStart warm-up hook. The docs don't say whether SessionStart finishes before MCP servers start, so the
  launcher must work without it.
- Development commands below still use `uv` directly in `server/`. That needs `uv` on the developer's machine, not
  the user's.
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
