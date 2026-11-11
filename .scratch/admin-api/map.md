Label: wayfinder:map

# Admin API for navigator sessions

## Destination

An implementation-ready phase spec in `docs/phases/` for a standalone admin process, with an OpenAPI contract
committed in this repo that the separate UI repo builds against: list, view (+logs) and end navigator sessions.

## Notes

- Domain: the navigator MCP server (`server/src/navigator_mcp`), its shared state dir and its phase docs. Read
  `docs/navigator-plugin-plan.md` and the relevant `docs/phases/` file before deciding anything that touches
  sessions or jobs.
- Skills: `grilling` and `domain-modeling` for grilling tickets; `research` for research tickets; `codebase-design`
  where a ticket draws a module seam. `GLOSSARY.md` holds the vocabulary.
- Planning only: tickets produce decisions, not code. When the frontier is empty, write the phase doc and the
  OpenAPI document from the resolved tickets.
- Several MCP server processes (one per Claude Code instance) share one state dir. Check every design against that.

## Decisions so far

- [Charting round: scope, topology, access and contract of the admin API](issues/01-charting-round.md): navigator
  sessions; logs = state + transcript + job history; v1 list/view/end; standalone admin process; env-configured
  port/host, bearer token, CORS; polling; this repo owns the OpenAPI.
- [HTTP stack and OpenAPI generation](issues/05-http-stack-and-openapi.md): starlette 1.10 + uvicorn 0.54 (declare them; launch.sh ignores uv.lock); OpenAPI likely from pydantic schemas + one route table, golden-tested (FastAPI or hand-authored as alternatives); CORSMiddleware outermost + small bearer ASGI middleware.

## Not yet specified

- Cancel-job: how the admin process asks the owning MCP process (found via `server_pid` in `pending.json`) to cancel
  a job, and what the owner must poll or listen for. In scope, deferred from v1; sharpen once ticket 03 settles how
  the admin and MCP processes coordinate.
- Push updates (SSE): the event model and file-watch layer for a later additive endpoint.
- The phase doc's test plan and done criteria, including the contract drift test; shaped by tickets 02 and 05.

## Out of scope

- Starting sessions, asking the navigator or submitting tool results from the UI: the plan's non-goals rule out an
  autonomous loop and server-run tool calls ([charting round](issues/01-charting-round.md)).
- Editing session state, and deleting or purging session files ([charting round](issues/01-charting-round.md)).
- The UI's design and implementation: a separate repo.
- Fixing `JobManager.recover()`, which on start-up fails every queued or running job in the shared state dir, other
  instances' included. A pre-existing bug to file on its own; the admin process never calls it.
