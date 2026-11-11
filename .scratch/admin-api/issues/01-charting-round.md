Type: grilling
Status: resolved
Blocked by:

# Charting round: scope, topology, access and contract of the admin API

## Question

What is the admin API for, what does it manage, which process serves it, how is it reached and secured, how does
the UI stay current, and who owns its contract?

## Answer

Settled by the navigator in scout mode (session `720a82843ef5411e8521e97f08bd8015`) and approved by the user on
2026-11-03. On q4 the user chose the navigator's answer over the driver's challenge.

1. **Deliverable.** An implementation-ready phase spec in `docs/phases/` that embeds an HTTP contract as an OpenAPI
   document committed in this repo, for the separate UI repo to build against.
2. **Session.** A *navigator session*: one `session_id`, one directory under the state root holding `state.json`,
   `job-<id>.json` and the `request-*`/`reply-*` audit files. Not a Claude Code session; the server records no Claude
   Code id, pid or owner.
3. **Logs.** The session's on-disk record: `state.json` (action log, decisions, rejected claims, plan or questions,
   settled answers, carry-forward, phase, metrics), the per-ask Ollama transcript (`request-N*.json` /
   `reply-N*.json`, written once by `ollama.py`), and job history (`job-<id>.json`, written at enqueue, start and
   finish). Server stderr is out: the server writes none.
4. **Operations.** v1 = list, view (+logs), end. Cancel-job is in scope for this effort but deferred from v1. Start,
   ask/submit (non-goals: no autonomous loop, no server-run tool calls), edit state and delete/purge are out of scope.
5. **Topology.** A standalone admin process: a new entry point that reads and writes the shared state dir, owns the
   one configurable port, imports `session.py` rather than `server.py`, and never runs `JobManager.recover()`. Its
   `end` cannot use the in-memory `check_no_active_job`; it must check on-disk `jobs/<job_id>.pending.json` instead.
   Cancel-job can later reach the owner via `server_pid` in `pending.json`.
6. **Access.** Started by the operator, never by `launch.sh` (one per Claude Code instance). `NAVIGATOR_ADMIN_PORT`
   (with a default) and `NAVIGATOR_ADMIN_HOST` (default `127.0.0.1`). Bearer token required: `NAVIGATOR_ADMIN_TOKEN`,
   else generated into `<state_root>/admin.token`. CORS via `NAVIGATOR_ADMIN_CORS_ORIGINS`, default allow-all (the
   token is the boundary). The operator sets `NAVIGATOR_STATE_DIR` to the MCP processes' state dir.
7. **Updates.** The UI polls in v1. Push (SSE) is a later additive endpoint; any push needs a file-watch layer because
   changes come from other processes.
8. **Contract.** This repo owns the OpenAPI document; the UI repo consumes it; UI design is out of scope. A
   golden-file style test keeps it in step with the routes (precedent: `server/tests/golden/`). The version lives in
   OpenAPI `info.version`; no `/v1` path prefix.

Rejected navigator claims, recorded in the session: `recover()` is not topology-specific (every MCP process runs it
at startup); `starlette`, `uvicorn` and `sse-starlette` are already installed transitively via `mcp` 2.3.0, so
stdlib `http.server` is not the only no-new-install choice; where the admin process runs (container or host) is
unverified.
