# Phase 3b: Async jobs, queue, MCP Tasks

Milestone 3b of [navigator-plugin-plan.md](../navigator-plugin-plan.md) (§10). Depends on phase 1 (`server.py`, `.mcp.json`), phase 2 (`SessionState`, persistence) and phase 3 ([Ollama client and ask/submit state machine](phase-3-ollama-loop.md)). Spec source is main doc §9b; platform facts are in §2 ("MCP Python SDK", "MCP Tasks extension", "Claude Code and long-running MCP calls", "Ollama concurrency"); tool surface is §4; config is §8; open questions are §11.

Scope: `server/src/navigator_mcp/jobs.py`, the `navigator_ask` / `navigator_submit` / `navigator_cancel` registrations in `server.py`, the `lifespan`, and the changes to phase 3 listed in section 1. Out of scope: `Outcome` models, loop rules and `SessionState` (phase 3 and phase 2 definitions are used unchanged), compression, charters.

Sources checked:
- `mcp` 2.3.0 wheel, read-only, never imported. Cited as `mcp/...:line`. The extracted copy was deleted.
- [ext-tasks](https://github.com/modelcontextprotocol/ext-tasks) `specification/2026-07-28/tasks.md` (cited `tasks.md:line`) and `schema/2026-07-28/schema.json` (`$defs`: Task, CompletedTask, FailedTask, CreateTaskResult, GetTaskResult).
- httpx `master` `httpx/_transports/mock.py` and the httpx docs (Timeouts, Transports, Async).

## 1. Required changes to phase 3

Decision for the cancel-in-flight requirement: **(a) `ollama.py` uses `httpx.AsyncClient`.** Phase 3 must change as follows.

1. `OllamaClient.chat` becomes `async def chat(...)`. The client is `httpx.AsyncClient(base_url=..., timeout=httpx.Timeout(None), transport=transport)`. `send_loop`, the `navigator_ask` / `navigator_submit` bodies and every caller become `async`. Phase 3 §3 was written for a sync `httpx.Client`; it now points here.
2. Default timeout is `httpx.Timeout(None)` (no timeout). `chat(..., timeout: httpx.Timeout | None = <client default>)` accepts a per-request override. httpx documents per-request `timeout=` and `timeout=None` to disable (Timeouts docs; the `timeout=None` example is at `docs/advanced/timeouts.md:19-38`). Phase 3 test 2 already asserts `Timeout(None)`.
3. `MockTransport` with `AsyncClient`: `MockTransport` subclasses `AsyncBaseTransport` and `BaseTransport`; `handle_async_request` awaits the handler result when it is a coroutine (`httpx/_transports/mock.py:15-43`, master). The Transports docs page does not document this, so this resolves phase 3 Q5 by source only. The pinned release of httpx was not read. Phase 3 tests move to `@pytest.mark.anyio` (asyncio backend) with the same scripted-queue handler, which may be `def` or `async def`.
4. Why not (b): `anyio.to_thread.run_sync` cannot interrupt the worker thread, and the httpx Timeouts, Transports and Async pages document no way to abort an in-flight sync request from another thread. Neither claim was verified against the anyio docs. With (a), cancellation is cancellation of the awaiting task (section 3). Whether httpx closes the connection on cancel is not documented in what was read; see Q4.
5. Add three keyword-only parameters to the phase 3 entry points, which jobs.py calls as `run_ask(session, args, ctx) -> Outcome` and `run_submit(...)`:
   - `on_stage: Callable[[str], None]`: called before each send with `"waiting on navigator (request-N)"` (N and suffix as in the phase 3 §8 audit file name) and, on a bounce or resend continuation, `"bounce/resend k"` (k counts continuations within the ask).
   - `deadline_remaining: Callable[[], float | None]`: called before each send. `None` means no deadline. A value `<= 0` makes the loop return `Error(reason="timeout", stage=<current stage>)` without sending. Otherwise the send uses `httpx.Timeout(remaining)`.
   - `sleep: Callable[[float], Awaitable[None]]`: injected for the 503 backoff (section 6).
6. `Error` gains an optional `stage: str | None = None` (main doc §9b: `error{reason: "timeout", stage}`). No other `Outcome` change.
7. 503 handling is added inside `chat` (section 6). Phase 3 §3 "non-2xx is not resent" keeps for every other status.
8. Phase 3 tool registrations in `server.py` are replaced by the section 2.6 wrappers. The phase 3 handler bodies are reused, not rewritten.

## 2. `jobs.py`

### 2.1 Job

```python
JobState = Literal["queued", "running", "completed", "failed", "cancelled"]

class Job:   # plain class or dataclass; not serialized to the client
    job_id: str                  # uuid4 hex; also the MCP taskId
    session_id: str
    kind: Literal["ask", "submit"]
    args: dict                   # validated tool arguments
    created_at: datetime         # UTC, ISO 8601 on the wire
    last_updated_at: datetime
    state: JobState
    stage: str                   # current statusMessage text (section 4.3)
    phase_before: str            # phase 3 session phase when enqueued
    snapshot: SessionState       # deep copy taken when the job starts running
    deadline: float | None       # clock() value, or None
    outcome: Outcome | None      # set when state == "completed"
    failure: str | None          # set when state == "failed"
    scope: anyio.CancelScope | None
    settled: anyio.Event         # set on any terminal state
    fetched: bool
```

State transitions: `queued -> running -> completed | failed | cancelled`; `queued -> cancelled`.
- `completed`: the state machine returned an `Outcome`. This includes `Outcome(status="error")` (phase 3 error, timeout, bounce cap).
- `failed`: an unhandled exception in the worker, or recovery after restart (section 8). Not used for `Outcome` errors.
- `cancelled`: section 5.

### 2.2 Manager, queue, workers

`JobManager(state_dir, concurrency, job_timeout, *, clock=time.monotonic, sleep=anyio.sleep)`.
- One process-wide FIFO: a `collections.deque[Job]` guarded by an `anyio.Condition`.
- `enqueue(session_id, kind, args) -> Job`: raises `ActiveJobError(job_id)` if any job is `queued` or `running` (section 2.3). Otherwise appends, writes the job file (section 8), notifies.
- `async def worker()`: loop `wait for deque non-empty; pop left; skip if state == "cancelled"; run`.
- `run(job)`:
  1. Set `running`; `job.snapshot = session.model_copy(deep=True)` (phase 2 `SessionState`).
  2. `with anyio.CancelScope() as job.scope:` start a task group with two tasks: the state machine call (`run_ask` / `run_submit`) and, when `job.deadline` is set, a watcher `await sleep(deadline - clock()); timed_out = True; scope.cancel()`.
  3. Result mapping: returned `Outcome` -> `completed`. Cancelled by `cancel(job)` -> `cancelled`. Cancelled by the watcher -> `completed` with `Error(reason="timeout", stage=job.stage)`. Any other exception -> `failed`, `failure=str(exc)`.
  4. In a shielded scope (`CancelScope(shield=True)`): on `cancelled` or timeout, restore the session to `job.snapshot` and persist through phase 2; write the job file; `job.settled.set()`.
- The deadline watcher uses the injected `clock` / `sleep` so tests control time (section 9). The watcher exists because httpx timeouts bound individual operations (connect, read, write, pool), not the whole request (phase 3 §2), so they cannot enforce a job-level deadline alone. The derived `httpx.Timeout(remaining)` is still passed (section 1.5).
- Exactly one worker is started. The one-active-job-per-server rule (main doc §9b) means at most one job exists, so there is no concurrency setting.

### 2.3 One active job per server

`enqueue` checks `any(j.state in ("queued","running") for j in jobs)` before creating a job, regardless of session. On conflict the tool call returns `Outcome(status="error", reason=f"job {active.job_id} active")` as a normal tool result (no task is created, `resultType` stays `complete`). The check runs before phase 3's phase checks and before argument validation of the session. A `completed` job whose result is not yet fetched is not active.

### 2.4 Invoking the phase 3 state machine

The worker calls the phase 3 handler for the job's `kind` with `on_stage` set to `job.stage = s; job.last_updated_at = now`, and `deadline_remaining = lambda: None if job.deadline is None else job.deadline - clock()`. The returned `Outcome` is stored unchanged. Phase 3 rules (bounces, resend, retry, `AWAITING_SUBMIT` / `IDLE` transitions, audit files) are not repeated or changed here.

### 2.5 Lifespan

```python
@asynccontextmanager
async def lifespan(app: MCPServer):
    jobs.recover()                                   # section 8
    async with anyio.create_task_group() as tg:
        for _ in range(concurrency): tg.start_soon(jobs.worker)
        yield {"jobs": jobs}
        tg.cancel_scope.cancel()
```

`MCPServer(lifespan=...)` takes `Callable[[MCPServer], AbstractAsyncContextManager[T]]` (`mcp/server/mcpserver/server.py:145-152, 179`); `lifespan_wrapper` enters it as `async with lifespan(app) as context` and the yielded value becomes `ctx.lifespan_context` (`mcp/server/context.py:41`). `jobs` is also a module-level singleton so the extension can be constructed before the lifespan runs.

### 2.6 Tool registrations (`server.py`)

```python
jobs = JobManager.from_env()
mcp = MCPServer("navigator", extensions=[TasksExtension(jobs)], lifespan=lifespan)

@mcp.tool()
async def navigator_ask(session_id: str, question: str, answer_tool: str, excerpts: list[Excerpt],
                        images: list[str] | None = None, ctx: Context = None) -> Outcome | Queued:
    return jobs.enqueue_queued("ask", {...})                # fallback path, section 7

@mcp.tool()
async def navigator_submit(session_id: str, results: list[Result], ctx: Context = None) -> Outcome | Queued: ...

@mcp.tool()
async def navigator_cancel(job_id: str) -> dict: ...
```

`extensions=` is `Sequence[Extension] | None` on `MCPServer.__init__` (`server.py:172`). Parameters of type `Context` are injected (docstring examples at `server.py:707-722`; `report_progress` at `mcpserver/context.py:120`). The tool functions are reached only when the interceptor calls `call_next` (opt-in absent or other tool). The `Outcome` return and argument models are the phase 3 registrations.

## 3. MCP Tasks extension

```python
class TasksExtension(Extension):
    identifier = "io.modelcontextprotocol/tasks"
    def __init__(self, jobs: JobManager): ...
    # settings() not overridden: advertises {} (extension.py:118-123; tasks.md:57 "no extension-specific settings")
    def methods(self) -> Sequence[MethodBinding]: ...
    async def intercept_tool_call(self, params, ctx, call_next): ...
```

SDK facts (`mcp/server/extension.py`):
- `Extension.identifier` is validated at subclass definition (`:108-116`). `settings()` `:118`, `methods()` `:133`, `intercept_tool_call(params: CallToolRequestParams, ctx: ServerRequestContext[Any, Any], call_next: CallNext) -> HandlerResult` `:137-156`.
- `MethodBinding(method, params_type, handler, protocol_versions=None)` `:57-93`. `params_type` is validated before `handler` and should subclass `RequestParams`. `method` must not be a spec method (`:84-88`); `tasks/*` is not. Handler type is `Callable[[ServerRequestContext[Any, Any], Any], Awaitable[HandlerResult]]` (`:38`).
- Applied by `MCPServer._apply_extension` (`mcpserver/server.py:328-352`): registers each method through `add_request_handler`, errors if the method is already registered, and sets `capabilities.extensions[identifier] = settings()`. `_install_extension_interceptor` (`:354-367`) wraps `tools/call` only when `intercept_tool_call` is overridden.
- A handler returns `HandlerResult = BaseModel | dict[str, Any] | None` (`mcp/server/context.py:132`). A dict is serialized by `ServerRunner._serialize` (`mcp/server/runner.py:341-394`): on 2026-07-28 connections an extension `resultType` outside the core vocabulary skips the core-result sieve (`:364-377`); `resultType: "complete"` is added when absent (`:387-393`); the `serverInfo` `_meta` stamp is added (`:395-417`). The runner does not yet reject extension `resultType` for clients that did not opt in (TODO at `:370-372`), so the opt-in check is the interceptor's job.

`methods()` returns:

| method | `params_type` (subclass of `RequestParams`) | handler |
| --- | --- | --- |
| `tasks/get` | `GetTaskParams`: `taskId: str` | `_get` |
| `tasks/cancel` | `CancelTaskParams`: `taskId: str` | `_cancel` |
| `tasks/update` | `UpdateTaskParams`: `taskId: str`, `inputResponses: dict[str, Any]` | `_update` |

`RequestParams` is exported by `mcp_types` (imported at `mcp/server/runner.py:44`). Whether it applies camelCase aliasing automatically is not read; declare `Field(alias="taskId")` etc. with `populate_by_name=True` (Q3).

Handlers:
- Opt-in enforcement is off by default: handlers check only the task id. `require_client_extension(ctx, identifier)` (`mcpserver/server.py:1391-1418`; raises `MCPError` code -32021 with `requiredCapabilities`), which `tasks.md:~790-800` describes for non-declaring clients, is switched on only if the Q1 probe shows that Claude Code sends the opt-in on `tasks/get` and `tasks/cancel` (Q2).
- Unknown or already-fetched `taskId`: `raise MCPError(code=-32602, message="Failed to retrieve task: Task not found")` (`tasks.md:793-811`). `MCPError` construction as at `server.py:1388`; import path to be confirmed (Q3).
- `_cancel`: `jobs.cancel(job_id)` (section 5), return `{}`. Cancellation is cooperative and the ack is empty (`tasks.md:398-410`). A task already terminal is acked unchanged.
- `_update`: no task ever enters `input_required`, so `inputResponses` are ignored (`tasks.md:379`). Known task: return `{}`. Unknown: -32602.
- `_get`: returns the task object of section 4.2 and, when the job is terminal, marks it `fetched` and drops it after the response is built (main doc §9b).

### `intercept_tool_call`

```python
async def intercept_tool_call(self, params, ctx, call_next):
    if params.name not in ("navigator_ask", "navigator_submit") or not self._opted_in(ctx):
        return await call_next(ctx)
    try:
        job = self.jobs.enqueue(kind, validated_args(params))
    except ActiveJobError:
        return await call_next(ctx)          # tool body returns error{"job <id> active"}, resultType complete
    return task_dict(job, create=True)
```

Opt-in detection, `_opted_in(ctx)`: true when `ctx.protocol_version == "2026-07-28"` and `"io.modelcontextprotocol/tasks"` is a key of
- `(ctx.params or {}).get("_meta", {}).get("io.modelcontextprotocol/clientCapabilities", {}).get("extensions", {})` (key per `tasks.md:33`; the SDK constant `CLIENT_CAPABILITIES_META_KEY` lives in `mcp_types`, not in the wheel), or
- `ctx.session.client_capabilities.extensions` (`mcp/server/session.py:70-77`). On the modern path the runner builds a fresh `Connection` from each request's `_meta` (`runner.py:857-869`), so this is per request.

`ctx` is a `ServerRequestContext` with `.params`, `.meta`, `.session`, `.protocol_version` (`mcp/server/context.py:30-49`). A server must not return `CreateTaskResult` to a client that did not opt in on that request (`tasks.md:61`). Invalid arguments: `validated_args` failure also falls to `call_next`, which produces the SDK's standard validation error. The task exists in `jobs` before the dict is returned (`tasks.md:300`): `enqueue` is synchronous and precedes the return.

## 4. Task object

### 4.1 Fields

Per `Task` (`tasks.md:109-148`; schema `$defs/Task`): `taskId`, `status`, `statusMessage?`, `createdAt`, `lastUpdatedAt`, `ttlMs`, `pollIntervalMs?`. Timestamps are UTC ISO 8601 (`2026-11-01T10:30:00Z`).

Status mapping: `queued`, `running` -> `working`; `completed` -> `completed`; `failed` -> `failed`; `cancelled` -> `cancelled`. `input_required` is never used. Constants: `pollIntervalMs = 5000`, `ttlMs = null`.

### 4.2 Shapes

Create (`CreateTaskResult = Result & Task`, `tasks.md:258-296`):

```json
{"resultType":"task","taskId":"<job_id>","status":"working","statusMessage":"queued (position 1)",
 "createdAt":"...Z","lastUpdatedAt":"...Z","ttlMs":null,"pollIntervalMs":5000}
```

`tasks/get`, working: same without `resultType:"task"`; `"resultType":"complete"` (`tasks.md:338`; also stamped by `runner.py:387-393`), `status:"working"`, current `statusMessage`.

`tasks/get`, completed (`CompletedTask.result: JSONObject`, the original request's result shape, `tasks.md:189-197`):

```json
{"resultType":"complete","taskId":"...","status":"completed","statusMessage":"completed",
 "createdAt":"...","lastUpdatedAt":"...","ttlMs":null,"pollIntervalMs":5000,
 "result":{"content":[{"type":"text","text":"<Outcome as JSON>"}],
           "structuredContent":{"status":"answered", "...":"..."},
           "isError":false}}
```

`result` is the `CallToolResult` the fallback path would have returned for the same `Outcome`. `isError` is `true` when `Outcome.status == "error"`; the task is still `completed` (`tasks.md:157`).

`tasks/get`, failed (`FailedTask.error`, JSON-RPC error, `tasks.md:203-209`; `failed` only for JSON-RPC errors, `:158`):

```json
{"resultType":"complete","taskId":"...","status":"failed","statusMessage":"<failure>",
 "createdAt":"...","lastUpdatedAt":"...","ttlMs":null,"pollIntervalMs":5000,
 "error":{"code":-32603,"message":"<failure>"}}
```

`tasks/get`, cancelled: as working with `status:"cancelled"`, `statusMessage:"cancelled"`, no `result`/`error` (`tasks.md:215-217`).

### 4.3 `statusMessage`

| Job state | `statusMessage` |
| --- | --- |
| `queued` | `queued (position N)` (N is 1-based index in the deque) |
| `running` | `waiting on navigator (request-N)` at each send; `bounce/resend k` on a bounce or resend continuation |
| `completed` | `completed` |
| `cancelled` | `cancelled` |
| `failed` | the failure text |

The three `working` strings are from main doc §9b. The terminal strings are chosen here (not in the main doc). `notifications/tasks` and `subscriptions/listen` (`tasks.md:412-500`) are not implemented; clients poll (Claude Code polls `tasks/get`, main doc §2).

## 5. Cancellation

`JobManager.cancel(job_id)`, shared by `tasks/cancel` and `navigator_cancel`:

| Job state | Effect | Final state |
| --- | --- | --- |
| `queued` | removed from the deque (or skipped when popped); no snapshot was taken, session untouched | `cancelled` |
| `running` | `job.scope.cancel()`. The awaiting `AsyncClient.post` is cancelled. In the shielded finalizer the session is restored from `job.snapshot` and persisted (phase 3 `SessionState`, including its phase, `pending`, `ask_n`). Audit files already written stay on disk and are moved to `<session>/cancelled-<job_id>/` so the next ask's `request-N.json` is not overwritten (Q5) | `cancelled` |
| terminal | no-op | unchanged |
| unknown | `tasks/cancel`: -32602; `navigator_cancel`: ack `{"job_id", "state": "unknown"}` | n/a |

`navigator_cancel(job_id)` returns `{"job_id": ..., "state": <state after cancel>}`. The active-job slot is free once `settled` is set, so a new ask may follow immediately.

## 6. Ollama 503 backoff

Inside `OllamaClient.chat`, on HTTP 503 (Ollama queue full, main doc §2 "Ollama concurrency"):
- Resend the same bytes, with no new audit file and no `stage` change.
- Delays: 1, 2, 4, 8, 16, then 30 s repeated. No jitter. `await sleep(delay)` uses the injected `sleep`.
- The sleep is cancellable (it is part of the cancelled scope) and clipped to the remaining deadline. With a deadline the watcher ends the job as `Error(reason="timeout", stage)`.
- Cap: at most `NAVIGATOR_503_RETRIES` resends (default 8; the schedule above takes about 2.5 minutes). When the cap is reached, the job ends as `Error(reason="ollama busy (503)", stage)`, carrying the 503 body. The body is `{"error":"server busy, please try again.  maximum pending requests exceeded"}` (main doc §2, "Ollama behaviour").
- 499, 500, 400, 404, every other non-2xx and every `httpx.HTTPError` follow phase 3 §3 (`Error` with status and body, no resend).
- `keep_alive` is added to the payload only when `NAVIGATOR_KEEP_ALIVE` is set (main doc §8). Unset, the payload keeps the four keys.

## 7. Fallback path (no opt-in): `queued` + `navigator-wait`

Applies when `intercept_tool_call` finds no tasks opt-in (main doc §9b). Main doc §2 records where this happens: one-shot `-p`, the desktop app, VS Code, and Remote Control without `CLAUDE_AUTO_BACKGROUND_TASKS=1`.

`jobs.enqueue_queued(kind, args)`:
1. `enqueue`. On `ActiveJobError`, return `Outcome(status="error", reason="job <id> active")`.
2. Return `Queued{status: "queued", job_id, result_path, wait_command}` immediately.
   - `result_path` is absolute: `${NAVIGATOR_STATE_DIR}/jobs/<job_id>.result.json`.
   - `wait_command = "navigator-wait " + shlex.quote(result_path)`.
   - The path is given explicitly because `${CLAUDE_PLUGIN_DATA}` is not in the environment of Bash-tool commands ([manifest reference, Environment variables](https://code.claude.com/docs/en/plugins-reference)).
3. At every terminal state the worker writes `result_path` atomically: a temp file in the same dir, then `os.replace`, as in phase 2's persistence.
   - Contents: `{job_id, state: "completed"|"cancelled"|"failed", outcome, failure, server_pid}`.
4. At enqueue the worker also writes `${NAVIGATOR_STATE_DIR}/jobs/<job_id>.pending.json` containing `{job_id, server_pid}`. It deletes the file once the result is written.

`bin/navigator-wait <result_path>` (plugin `bin/`, on the Bash tool's PATH while the plugin is enabled; main doc §3):
- A stdlib-only `#!/usr/bin/env python3` script.
- It loops on a 1 s interval inside the process, until one of these holds:
  - `result_path` exists.
  - The `server_pid` named in the sibling `.pending.json` no longer exists (`os.kill(pid, 0)` raises `ProcessLookupError`).
  - Neither file exists.
- Claude runs it once with `run_in_background: true`. The loop is inside the command, and Claude doesn't poll.
- Output: the `outcome` JSON on stdout, one line.
- Exit codes:

  | Code | Meaning |
  | --- | --- |
  | 0 | `completed` |
  | 3 | `cancelled` (stdout `{"status":"error","reason":"cancelled"}`) |
  | 4 | `failed` (stdout `{"status":"error","reason":<failure>}`) |
  | 5 | server gone before a result (stdout `{"status":"error","reason":"server exited"}`) |
  | 2 | bad arguments or unknown job |

- `navigator_cancel(job_id)` cancels the job (section 5). The result file is then written with `state: "cancelled"`, so the waiting `navigator-wait` exits 3.
- The skill text (phases 4–5) acts on the printed `Outcome` exactly as it acts on a task result.

## 8. Config, persistence, recovery

- `NAVIGATOR_JOB_TIMEOUT` (seconds, float) or `--job-timeout <s>` (CLI wins when both are set): `job.deadline = clock() + timeout` at enqueue, so queue time counts. Unset: `deadline = None`, `httpx.Timeout(None)`, no watcher. No `timeout` in `.mcp.json` (phase 1; main doc §8, §9b).
- Job file `${NAVIGATOR_STATE_DIR}/<session_id>/job-<job_id>.json`: `{job_id, kind, state, created_at, finished_at, phase_before, failure}`, written at enqueue, at `running` and at every terminal state.
- `jobs.recover()` (lifespan, before workers): for each job file with state `queued` or `running`, set `state="failed"`, `failure="server restarted"`, and set the persisted session phase back to `phase_before` (the pre-job `IDLE` or `AWAITING_SUBMIT`, with `pending` as persisted by phase 3). In-memory jobs are not restored; a later `tasks/get` for such an id returns -32602 (main doc §9b "Persistence").
- When a session ends (`navigator_end`), its finished jobs are dropped. Their `.result.json` files are deleted on `navigator_end`, or by `recover()` at the next start.
- `NAVIGATOR_503_RETRIES`: integer, default 8 (section 6). `NAVIGATOR_KEEP_ALIVE`: unset by default, passed through as the `keep_alive` value when set (section 6).

## 9. Tests

`server/tests/test_jobs.py`, `@pytest.mark.anyio`, asyncio backend. Fixtures:
- `FakeOllama`: phase 3's scripted-queue `httpx.MockTransport` handler, extended with a gate: `FakeOllama.hold()` makes the next request `await` an `anyio.Event` (released by `release()`), and `script_status(503, n)` returns `httpx.Response(503)` n times before the scripted reply. It records request bodies and whether the handler saw cancellation.
- `FakeClock`: `now()`, `async sleep(dt)` (registers a waiter), `advance(dt)` (wakes due waiters). Injected as `clock` and `sleep` into `JobManager` and `OllamaClient`.
- `ctx` factory building a `ServerRequestContext` (kw_only dataclass, `context.py:30-49`) with `params={"name": ..., "arguments": ..., "_meta": {...}}`, `protocol_version="2026-07-28"`, and a stub `session` exposing `client_capabilities` and an async `report_progress` that records calls.
- `OPT_IN = {"io.modelcontextprotocol/clientCapabilities": {"extensions": {"io.modelcontextprotocol/tasks": {}}}}`.
- Stub `Mode` and `decide(...)` helper bodies from phase 3 §10.

| # | Test | Input | Expected |
| --- | --- | --- | --- |
| 1 | queued then running then completed | enqueue ask; `FakeOllama.hold()`; step the worker | job states `queued -> running -> completed`; `job.outcome == Answered(...)` after `release()`; `settled` set |
| 2 | statusMessage sequence | ask with a bounced first reply then `decide`; hold each send | `stage` takes `waiting on navigator (request-1)`, then `bounce/resend 1`, then terminal `completed` |
| 3 | queued position | enqueue with the worker not started | `stage == "queued (position 1)"`; `tasks/get` returns `status:"working"` with that message |
| 4 | tasks path, opted in | `intercept_tool_call` with `navigator_ask` and `_meta=OPT_IN` | returns dict with exactly keys `resultType, taskId, status, statusMessage, createdAt, lastUpdatedAt, ttlMs, pollIntervalMs`; `resultType=="task"`, `status=="working"`, `ttlMs is None`, `pollIntervalMs==5000`; `call_next` not called; `jobs.get(taskId)` exists |
| 5 | opted in via capabilities only | `_meta` without the key; `session.client_capabilities.extensions` contains the id | same as test 4 |
| 6 | not opted in | `_meta={}` and no capability | `call_next` called once; no task dict; the tool returns `Queued` (test 14) |
| 7 | other tool opted in | `navigator_status` with `OPT_IN` | `call_next` called; no job created |
| 8 | tasks/get completed | complete a job, call `_get` | keys per section 4.2 incl. `result.content[0].text` parsing to the Outcome, `isError false`, `resultType "complete"`; second `_get` raises `MCPError` code -32602 |
| 9 | completed with error Outcome | script two empty replies | `status:"completed"`, `result.isError == true` |
| 10 | tasks/get failed | worker body raises `RuntimeError("boom")` | `status:"failed"`, `error == {"code":-32603,"message":"boom"}`, no `result` |
| 11 | unknown taskId | `_get`, `_cancel`, `_update` with `"nope"` | `MCPError` -32602 |
| 12 | tasks/update | known task, `inputResponses={"k":{}}` | returns `{}`; job unchanged |
| 13 | non-declaring client on tasks/* | `_get` with no opt-in, known task | returns the task (enforcement off by default). With enforcement on (Q2): `MCPError` -32021 with `data.requiredCapabilities.extensions` containing the id |
| 14 | fallback queued | not opted in, `FakeOllama.hold()` | the tool returns `Queued` with an absolute `result_path` and a `wait_command`; `.pending.json` exists; no result file yet |
| 15 | result file | release the fake, then wait for the job to settle | `result_path` exists with `state="completed"` and an `outcome` equal to the task-path Outcome for the same script; `.pending.json` removed |
| 16 | cancel queued | enqueue with worker stopped; `tasks/cancel` | `{}` ack; state `cancelled`; session equals pre-job state; no request sent to `FakeOllama`; worker started afterwards skips it |
| 17 | cancel running | `hold()`, start job, `tasks/cancel` | `FakeOllama` handler saw cancellation; state `cancelled`; `SessionState` deep-equals the snapshot (phase, `ask_n`, `pending`, messages); audit files moved to `cancelled-<job_id>/`; next `ask` writes `request-1.json` |
| 18 | cancel running via `navigator_cancel` | same | returns `{"job_id":..., "state":"cancelled"}` |
| 19 | cancel terminal | cancel a completed job | no-op; state `completed` |
| 20 | navigator-wait | run `bin/navigator-wait` as a subprocess on: a completed result file; a cancelled one; a failed one; a `.pending.json` whose `server_pid` is a dead pid; a missing path | exit codes 0, 3, 4, 5, 2; stdout is one JSON line matching the table in section 7 |
| 21 | timeout unset | no timeout; `FakeClock.advance(10**7)` while held | job still `running`; client timeout is `httpx.Timeout(None)`; no watcher task |
| 22 | timeout set | `job_timeout=30`; hold; `advance(31)` | job `completed` with `Error(reason="timeout", stage="waiting on navigator (request-1)")`; session restored; `FakeOllama` saw cancellation |
| 23 | httpx timeout derived | `job_timeout=30`, `advance(10)` before the send | recorded request timeout equals `httpx.Timeout(20.0)` |
| 24 | timeout while queued | enqueue behind a held job is impossible (section 2.3), so enqueue with the worker stopped, `advance(31)`, start worker | `Error(reason="timeout", stage="queued")`; nothing sent |
| 25 | 503 retry | `script_status(503, 3)` then `decide` | 4 sends with identical bytes; `sleep` called with 1, 2, 4; `Answered`; one `request-1.json` |
| 26 | 503 retry cap | `NAVIGATOR_503_RETRIES=8`, `script_status(503, 9)` | `sleep` args `1,2,4,8,16,30,30,30`; job ends `Error(reason="ollama busy (503)")` with the body; 9 sends |
| 27 | 503 with deadline | `job_timeout=5`, `script_status(503, 10)` | final `Error(reason="timeout", stage=...)`; no send after the deadline |
| 28 | 503 cancelled | cancel during a backoff sleep | `cancelled`; session restored |
| 29 | second ask while active | job 1 held `running`; `intercept_tool_call` (opted in) then `navigator_ask` again | second call returns `Error(reason="job <id1> active")` as a normal tool result (no task dict); `navigator_submit` on another session gets the same; after job 1 settles a new ask is accepted |
| 30 | active slot precedes phase checks | job active, ask on a session in `AWAITING_SUBMIT` | `reason == "job <id> active"` |
| 31 | unfetched completed job not active | job completed, result not fetched | a new ask is accepted |
| 32 | recovery | job files with state `running` and `queued` in two session dirs; start lifespan | both files become `failed` with `failure=="server restarted"`; session phases equal `phase_before`; no workers ran them |
| 33 | lifespan | enter and exit `lifespan` | `concurrency` workers started and cancelled cleanly; `ctx.lifespan_context["jobs"] is jobs` |
| 34 | registration | build `MCPServer` with the extension | `capabilities.extensions` contains `"io.modelcontextprotocol/tasks": {}`; `tasks/get`, `tasks/cancel`, `tasks/update` handlers registered; no `ValueError` |
| 35 | stdio smoke (raw JSON-RPC, as the main doc §2 probe) | subprocess server; `server/discover`, `tools/call navigator_ask` with and without `OPT_IN` and a `progressToken`; poll `tasks/get` | with opt-in: task dict then `completed` via polling; without: blocking result plus progress notifications |

Phase 3 tests 1-24 are re-run after the async conversion of section 1.

## 10. Done criteria

- `uv run pytest` passes in `server/` (phase 3 tests under the async client, plus tests 1-35 above).
- `navigator_ask` / `navigator_submit` with the opt-in `_meta` return the section 4.2 create dict without invoking the tool body; without it they block and send progress.
- A second ask while a job is queued or running returns `job <id> active`.
- Cancel of a queued and of a running job leaves `SessionState` equal to its pre-job value and aborts the in-flight `FakeOllama` request.
- With `NAVIGATOR_JOB_TIMEOUT` unset no deadline exists; when set, expiry yields `Error(reason="timeout", stage)`.
- 503 is retried with the section 6 schedule.
- Startup recovery marks stale jobs `failed` and restores session phase.
- Main doc §12 manual checks for this milestone (interactive task, `claude -p` fallback, second-ask error) are run and results recorded.

## 11. Open questions

From main doc §11:
- **Remote Control.** Resolved by the main doc §2 probe: MCP Tasks with `CLAUDE_AUTO_BACKGROUND_TASKS=1`, otherwise section 7.
- **`keep_alive`.** Resolved: not sent unless `NAVIGATOR_KEEP_ALIVE` is set (section 6).

This phase:
- **Q1.** Resolved: Claude Code 2.1.316 sends `io.modelcontextprotocol/clientCapabilities` with the tasks extension on every `tasks/get` poll (main doc §2, Remote Control probe). `tasks/cancel` was not observed.
- **Q2.** Enforcement stays off by default. Q1 shows it would work for Claude Code's `tasks/get`, but other clients have not been checked.
- **Q3.** Not read from the wheel: `RequestParams` alias/`populate_by_name` configuration, the `mcp_types.CLIENT_CAPABILITIES_META_KEY` value (spec text gives `io.modelcontextprotocol/clientCapabilities`), and the import path of `MCPError`. Confirm against the installed package.
- **Q4.** Does cancelling the task awaiting `AsyncClient.post` close the connection and abort generation in Ollama? Not stated in the httpx docs read; the test only asserts the handler saw cancellation.
- **Q5.** Restoring `ask_n` and moving audit files on cancel (section 5) is a design choice not in phase 3 §8. Alternative: keep `ask_n` and the files.
- **Q6.** Resolved: no concurrency setting. One worker, per the one-active-job-per-server rule.
- **Q7.** Resolved: `NAVIGATOR_503_RETRIES` cap (section 6).
- **Q8.** Does Claude Code re-request `tasks/get` after receiving the result? Section 3 drops a job after one completed fetch (main doc §9b), so a repeat returns -32602.
- **Q9.** Does phase 2 persist `THINKING` and `pending` such that `phase_before` is enough to recover (section 8)?
- **Q10.** The `error` object shape of `FailedTask` (`schema.json` `$defs/Error`) was not inspected beyond `code` and `message` from the `tasks.md` examples.
