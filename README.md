# navigator

A Claude Code plugin that pairs Claude with a local Ollama navigator model through an MCP server.

Claude is the driver: it implements, runs tool calls, and checks claims. The navigator model, `qwen-agent:latest` by
default, decides plans and settles questions. Claude reaches it only through `navigator_*` MCP tools, never by calling
Ollama directly.

Status: milestone 1 (scaffold). The server answers stub tools only. Setup and usage will be documented as the pair and
scout skills land.

Configuration: `NAVIGATOR_OLLAMA_URL` (default `http://host.docker.internal:11434`), `NAVIGATOR_MODEL`, and
`NAVIGATOR_STATE_DIR`. See `docs/navigator-plugin-plan.md` section 8 for the full list.

Development: `cd server && uv run pytest`. The plan is in `docs/navigator-plugin-plan.md`.
