# Navigator plugin: plan

A Claude Code plugin named `navigator`. It bundles a Python MCP server and two user-triggered skills, and replaces the
`to-llm` and `scout-ahead` skills.

## 1. Context, goals, non-goals

### Context

`to-llm` and `scout-ahead` have Claude act as driver to a navigator model, `qwen-agent:latest` served by Ollama at
`host.docker.internal:11434`, reached with curl. Today Claude does all of the session bookkeeping:
- builds the rolling SESSION SUMMARY
- writes `request-N.json` and checks it with jq
- handles resends
- tracks repeated tool calls

Source skills:
- `<synced-skills>/to-llm/SKILL.md`
- `<synced-skills>/scout-ahead/SKILL.md`

### Goals

- A Python MCP server that keeps the navigator session state and does the compaction and the compression
  (LLMLingua-2). Claude submits a request and receives only the latest navigator response.
- Every tool call the navigator asks for goes back to Claude to run. Claude then submits the results.
- Two user-triggered skills, `/navigator:pair` (replaces to-llm) and `/navigator:scout` (replaces scout-ahead),
  that make Claude use the MCP tools.

### Non-goals

- The server running navigator tool calls itself.
- An autonomous loop with no driver.
- More than one navigator model per session.

## 2. Platform reference

### Claude Code plugins

- **Layout.** `.claude-plugin/plugin.json` holds the manifest. Every other component sits at the plugin root,
  including `skills/<name>/SKILL.md` and `.mcp.json`. `name` is the only required manifest field, and every
  component is namespaced under it. —
  [plugins-reference](https://code.claude.com/docs/en/plugins-reference)
- **Path variables.** `${CLAUDE_PLUGIN_ROOT}` is the install path and changes on update, so don't write state
  there. `${CLAUDE_PLUGIN_DATA}` is `<plugin-data>/<id>/` and persists across updates. Both resolve in
  stdio MCP `command`, `args` and `env`, and both are exported to the server process. — same page
- **Loading and validation.** Plugins load with `--plugin-dir <dir>`. `claude plugin validate <dir>` checks the
  manifest and the MCP entries. — same page
- **MCP tool names.** Plugin MCP tools are named `mcp__plugin_<plugin>_<server>__<tool>`, and allow rules accept
  `mcp__<server>__*`. For plugin `navigator` with server `navigator`, the rule is
  `mcp__plugin_navigator_navigator__*`. — [mcp](https://code.claude.com/docs/en/mcp),
  [permissions](https://code.claude.com/docs/en/permissions)
- **Tool-call timeout.** A per-server `timeout` in ms in `.mcp.json` overrides `MCP_TOOL_TIMEOUT`. The default is
  about 28 hours when that variable is unset. — [mcp](https://code.claude.com/docs/en/mcp)
- **Skill frontmatter.** — [skills](https://code.claude.com/docs/en/skills)
  - `disable-model-invocation: true` keeps Claude from loading the skill on its own. The user triggers it with
    `/name`.
  - `allowed-tools` pre-approves tools for the invoking turn only. It does not restrict which tools can be used.

### MCP Python SDK

- Package `mcp`, or `mcp[cli]` for the CLI.
- Server class: `from mcp.server import MCPServer`, with tools declared using `@mcp.tool()`.
- Transports: stdio, Streamable HTTP, SSE.
- Source: [python-sdk](https://github.com/modelcontextprotocol/python-sdk)
- Findings from reading the source of `mcp` 2.3.0 (the latest PyPI release when this was checked):
  - It serves protocol revision 2026-07-28.
  - It has no built-in tasks extension.
  - It has an `Extension` base class (SEP-2133, `mcp/server/extension.py`) with:
    - an `identifier`, advertised under `capabilities.extensions`
    - `methods()`, which returns `MethodBinding` entries for new request methods such as `tasks/get`
    - `intercept_tool_call(params, ctx, call_next)`
  - On 2026-07-28 connections the runner passes extension `resultType` values through unchanged.
  - The request context exposes `client_capabilities`.
  - `Context.report_progress(progress, total, message)` sends progress notifications.
  - `MCPServer(lifespan=...)` takes an async context manager.

### MCP Tasks extension

- Identifier: `io.modelcontextprotocol/tasks`, protocol revision 2026-07-28.
- Opt-in and discovery:
  - The client opts in per request in
    `params._meta["io.modelcontextprotocol/clientCapabilities"].extensions`.
  - The server advertises the extension in its `server/discover` capabilities.
- Task creation: the server answers `tools/call` with `resultType: "task"`, plus `taskId`, `status`, `ttlMs` and
  `pollIntervalMs`. The task must exist before the response is sent.
- Methods:
  - `tasks/get`: carries `result` on `completed`, or `error` on `failed`.
  - `tasks/update`
  - `tasks/cancel`: cooperative.
- Statuses: `working`, `input_required`, `completed`, `failed`, `cancelled`.
- Sources: [tasks overview](https://modelcontextprotocol.io/extensions/tasks/overview),
  [ext-tasks](https://github.com/modelcontextprotocol/ext-tasks). The only reference implementation is in TypeScript.

### Claude Code and long-running MCP calls

Source: [mcp](https://code.claude.com/docs/en/mcp)

- **MCP tasks.** Public beta, v2.1.304 or later.
  - Which sessions get them: terminal CLI sessions, Agent SDK sessions, and GitHub Actions runs. The VS Code
    extension and the desktop app don't.
  - Other conditions:
    - The call comes from the main conversation, not a subagent.
    - The session is interactive, or it is a stream-json `claude -p` / Agent SDK run with
      `CLAUDE_AUTO_BACKGROUND_TASKS=1`. A one-shot `claude -p "<prompt>"` gets no tasks.
    - A `tools/call` that can't become a task omits the extension from its `clientCapabilities`.
  - Polling: Claude Code polls `tasks/get` every `pollIntervalMs`, clamped to 250–30000 ms (default 2000).
  - Delivery: the result goes to Claude, and an idle Claude starts on it unprompted.
  - Cancelling: `/tasks` and `x`, or asking Claude, sends `tasks/cancel`.
  - A stdio server's process stops when the session exits.
- **Auto-backgrounding.**
  - A main-conversation MCP call still running after 2 minutes moves to a background task, and its result arrives
    as a task notification.
  - Not for subagent calls, or in `-p` mode unless `CLAUDE_AUTO_BACKGROUND_TASKS=1` is set.
  - The stdio idle window is 30 minutes without a response or progress notification
    (`CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT`).
  - Progress notifications don't extend the wall-clock `timeout`.
- **Remote Control** ([remote-control](https://code.claude.com/docs/en/remote-control)). The session runs locally,
  and local MCP servers stay available. The docs don't say whether MCP tasks apply to Remote Control sessions.
- **Probe, 2026-11-01, Claude Code 2.1.316.** A raw stdio test server observed the following:
  - Claude Code sent `server/discover` on 2026-07-28 and declared the tasks extension.
  - `tools/call` included a `progressToken`.
  - A one-shot `claude -p` left the tasks opt-in out of `tools/call`.
  - With `--input-format stream-json` and `CLAUDE_AUTO_BACKGROUND_TASKS=1`, `tools/call` included the opt-in. Then:
    - The server's `resultType: "task"` reply was accepted, and Claude Code polled `tasks/get` at the given
      interval.
    - Each `statusMessage` showed up as task progress.
    - When the task completed, Claude received the result and reported it without being prompted.

### Ollama concurrency

- `OLLAMA_NUM_PARALLEL` defaults to 1 parallel request per model.
- `OLLAMA_MAX_QUEUE` defaults to 512 queued requests, and overflow returns a 503.
- `OLLAMA_KEEP_ALIVE` defaults to 5 m, and a per-request `keep_alive` overrides it.
- Source: [faq](https://docs.ollama.com/faq)

### LLMLingua-2

- Package `llmlingua`. Its install requires are `transformers>=4.26.0`, `accelerate`, `torch`, `tiktoken`, `nltk`
  and `numpy`.
- Usage:
  ```python
  from llmlingua import PromptCompressor
  c = PromptCompressor(model_name="microsoft/llmlingua-2-xlm-roberta-large-meetingbank", use_llmlingua2=True)
  c.compress_prompt(prompt, rate=0.33, force_tokens=['\n', '?'])
  ```
- Models: `microsoft/llmlingua-2-xlm-roberta-large-meetingbank` and
  `microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank`. The README calls the second one
  "LLMLingua-2-small".
- Source: [LLMLingua](https://github.com/microsoft/LLMLingua)

### Ollama `/api/chat`

- `keep_alive` sets how long the model stays loaded after a request. The default is `5m`.
- `prompt_eval_duration` is "time spent in nanoseconds evaluating uncached prompt tokens".
- `prompt_eval_cached_count` is "number of prompt tokens read from the cache". It is documented under
  `/api/generate`.
- The API doc does not describe how prefix reuse works.
- Source: [api.md](https://github.com/ollama/ollama/blob/main/docs/api.md)

### Behaviours carried over from to-llm/SKILL.md

- The request payload has exactly the keys `model`, `messages`, `tools` and `stream: false`, with no `options`.
- The charter goes verbatim as the system prompt. Phase 4 copies it from to-llm/SKILL.md.
- Navigator tools:
  - `read_file`, `list_dir`, `grep`, `screenshot`, `web_search` and `need_info`.
  - Each takes a required `justification`, except `need_info`, which takes `questions`.
- Answer tools:
  - `set_plan` (steps plus carry_forward)
  - `decide` (decision, findings, carry_forward)
- Resend and retry rules:
  - One resend for a malformed or plain-text reply.
  - One retry for an empty reply, with excerpts trimmed to ±5 lines. If it is still empty, report the request
    sizes that were tried.
- Images are base64 in the message `images` field and kept under about 1 MB each.
- to-llm used a 2 h curl timeout. This plan drops it as a default (§9b).

## 3. Repo layout

The plugin root is the repo root, next to the existing tracked `.claude/skills/`.

```
.claude-plugin/plugin.json      # { "name": "navigator", ... }
.mcp.json                       # server "navigator": uv run --project ${CLAUDE_PLUGIN_ROOT}/server navigator-mcp
skills/pair/SKILL.md            # replaces to-llm
skills/scout/SKILL.md           # replaces scout-ahead
server/pyproject.toml           # deps: mcp, httpx, pydantic, llmlingua; script entry point navigator-mcp
server/src/navigator_mcp/
  server.py                     # MCP tool surface
  session.py                    # SessionState, persistence, action log
  render.py                     # SessionState -> SESSION SUMMARY text
  compress.py                   # LLMLingua-2 wrapper (lazy, optional, memoized)
  ollama.py                     # /api/chat client, resend/retry rules, metrics capture
  jobs.py                       # job queue, worker, MCP Tasks extension, progress fallback (§9b)
  schemas.py                    # navigator tool JSON schemas
  modes/pair.py                 # pair charter + answer tools
  modes/scout.py                # scout charter + answer tools
server/tests/
docs/navigator-plugin-plan.md
docs/phases/
```

## 4. MCP tool surface

Each call returns only the latest `Outcome` and never the session history. `navigator_ask` and `navigator_submit` run
as queued jobs (§9b). Their `Outcome` arrives as an MCP task result when the call opts in to tasks, or as the
(possibly backgrounded) tool result otherwise.

| Tool | Arguments | Returns |
| --- | --- | --- |
| `navigator_start` | `mode: "pair"\|"scout"`, `goal`, `context` | `session_id` |
| `navigator_ask` | `session_id`, `question`, `answer_tool`, `excerpts[{path,start,end,text}]`, `images?[path]` | `Outcome` |
| `navigator_submit` | `session_id`, `results[{call_id, status: "ran"\|"denied", content, image_path?}]` | `Outcome` |
| `navigator_record` | `session_id`, `kind`, `payload` | updated state ack |
| `navigator_status` | `session_id` | rendered summary, metrics, and the server's current job and queue |
| `navigator_end` | `session_id` | ack |
| `navigator_cancel` | `job_id` | ack. For the fallback path; on the tasks path, `tasks/cancel` does this |

`navigator_record.kind` is one of `decided`, `rejected_claim`, `clarification`, `step_done`, `challenge` or
`carry_forward_verdict`.

`Outcome` takes one of these shapes:
- `{status: "tool_calls", calls: [{call_id, name, args, justification}]}`
- `{status: "need_info", questions}`
- `{status: "answered", tool, result, carry_forward}`
- `{status: "error", reason}`

## 5. Responsibilities

### Server

- Builds every request at send time from the stored state (§6, §9a).
- Bounces a tool call with no justification, or an exact repeat of an earlier call that doesn't say what changed.
  The bounce goes back to the navigator without involving Claude.
- Applies the resend and retry rules (§2) and validates the required fields on answer tools.
- Base64-encodes images from `image_path` and enforces the size limit.
- Writes `request-N.json` and `reply-N.json` to the session state dir.

### Claude (driver)

- Runs or denies each navigator tool call, following the to-llm rules:
  - Paths must be inside the repo, outside `.git/` and `node_modules/`, and not secrets.
  - Web searches use WebSearch in standard mode.
  - Suspicious queries go to the user through AskUserQuestion.
- Captures screenshots headless with Playwright (`chromium.launch()`) to a file path, lowering the viewport to stay
  under the image size limit. Looks at each one before submitting it and withholds any that show a secret.
- Answers `need_info` with evidence.
- Checks claims and carry-forward lines against the source and records the verdicts with `navigator_record`.
- Implements and commits in pair mode.
- Uses AskUserQuestion for approvals and for challenges that stall.

## 6. Compaction

A pydantic `SessionState` holds:
- `goal`, `mode`
- step list and current step, done
- decided, open challenge, rejected claims (each with evidence), clarifications
- files read (`path:L-L (what it shows)`)
- web searches (`query -> URL (what it showed)`)
- screenshots (`shot-K: target (what it shows)`)
- action log (one line per past call)
- `carry_forward`
- scout mode only: the question list and the settled answers

`render.py` builds the SESSION SUMMARY from this state. It never copies text from an earlier request.

## 7. Compression

- LLMLingua-2 runs only on free-text prose fields: decision rationales, clarifications, challenge text, and
  descriptions of web results.
- Never compressed:
  - the charter and tool schemas
  - JSON
  - code excerpts and file contents
  - `path:line` refs and IDs
  - the question
- Paths and line refs stay outside the compressed spans. `force_tokens` keeps newlines and punctuation.
- Config: `NAVIGATOR_COMPRESSION=on|off`, `NAVIGATOR_COMPRESSION_RATE`, `NAVIGATOR_LLMLINGUA_MODEL`.
- The compressor loads lazily. If it can't load, the summary is sent uncompressed and `navigator_status` reports
  this.

## 8. Config

| Variable | Default |
| --- | --- |
| `NAVIGATOR_OLLAMA_URL` | `http://host.docker.internal:11434` |
| `NAVIGATOR_MODEL` | `qwen-agent:latest` |
| `NAVIGATOR_STATE_DIR` | `${CLAUDE_PLUGIN_DATA}/sessions` |
| `NAVIGATOR_JOB_TIMEOUT` / `--job-timeout <s>` | unset: no job deadline, httpx timeout `None`, no `timeout` on the `.mcp.json` entry |

## 9. Skills

Both skills use the same frontmatter base:

```yaml
disable-model-invocation: true
allowed-tools: mcp__plugin_navigator_navigator__*
```

Both skill bodies tell Claude to:
- Route every plan, decision, failure or scope question through the `navigator_*` tools.
- Never curl Ollama, and never decide alone.
- Run or deny each returned tool call, then submit the results with `navigator_submit`.
- Record claim and carry-forward verdicts with `navigator_record`.
- After `navigator_ask` or `navigator_submit`, wait for the task result or task notification. Don't poll, and don't
  call `navigator_status` in a loop (§9b).

### `pair` (replaces to-llm)

- One neutral question per `navigator_ask`, offering options without a preference. Challenges go in a separate
  turn.
- The first question gives the task, plan and code excerpts, and any plan/code mismatch, with
  `answer_tool=set_plan`. Later questions use `decide`.
- Report each `answered` outcome to the user in one line.

### `scout` (replaces scout-ahead)

- Build the context from the closed tickets' resolution comments.
- Agree the question list first, then settle one question per turn.
- Each settled answer gives the alternatives with the strongest case for and against each, the decision, and the
  reasoning. Prefer the simplest backwards-compatible option.
- When every question is settled, show a summary per question, then ask through AskUserQuestion:
  Approve / Reject / Other.

## 9a. Prompt caching

Payload construction:

- **Stable first, volatile last.**
  1. The system message: the charter, verbatim.
  2. `tools`: a fixed order, serialized as canonical JSON (sorted keys, fixed separators).
  3. SESSION SUMMARY fields that only grow, in insertion order: goal, decided, rejected claims, clarifications,
     files read, web searches, shots, action log.
  4. Fields that change on every question: step list and current step, open challenge, carry forward.
  5. The excerpts.
  6. The question.
- **Tool rounds within one request id.** Append the assistant tool-call message and the tool results after the
  existing messages, so the earlier bytes don't change. The summary is re-rendered only when a new question starts.
  This differs from to-llm, which rewrites the summary on every send.
- **Deterministic rendering.** No timestamps or random IDs in prompt text. Call IDs are sequential.
- **Deterministic compression.** LLMLingua-2 output is memoized per field, keyed on `hash(text, model, rate)`, so an
  unchanged field renders the same bytes on every send.
- **Measurement.**
  - Record `prompt_eval_count`, `prompt_eval_cached_count` (when the `/api/chat` response includes it) and
    `prompt_eval_duration` per request in the audit files, and report them in `navigator_status`.
  - A test asserts that the request prefix stays byte-identical across tool rounds.

## 9b. Async execution, queue, timeouts

- **Jobs.**
  - `navigator_ask` and `navigator_submit` each create
    `Job{job_id, session_id, kind, created_at, state: queued|running|completed|failed|cancelled}`.
  - A job runs the phase 3 state machine (Ollama send, bounces, resends, retries) up to the next `Outcome`. That
    `Outcome` is the job's result.
- **Queue.**
  - One process-wide FIFO queue with a single worker (Ollama's `OLLAMA_NUM_PARALLEL` default is also 1). The worker starts in the
    `MCPServer` lifespan.
  - **One active job per server.** While a job is queued or running, a new `navigator_ask` or `navigator_submit`
    returns `error{reason: "job <id> active"}`.
  - An Ollama 503 (its queue is full) is retried with backoff inside the job, and counts against the job deadline
    when one is set.
- **Delivery, preferred path: MCP Tasks.**
  - The server registers an SDK `Extension` subclass with identifier `io.modelcontextprotocol/tasks`.
  - Its `methods()` returns bindings for `tasks/get`, `tasks/cancel` and `tasks/update`.
  - Its `intercept_tool_call` checks the call for the opt-in and returns `resultType: "task"` straight away for
    the two job tools.
  - `statusMessage` shows one of `queued (position N)`, `waiting on navigator (request-N)` or `bounce/resend k`.
  - `pollIntervalMs` is 5000.
  - On completion, `tasks/get` carries `result`, which is the `Outcome` as a tool result.
  - `tasks/cancel` drops a queued job, or cancels the in-flight httpx request. Either way the job ends `cancelled`
    and the session state stays as it was before the job.
- **Delivery, fallback (no opt-in).**
  - This covers one-shot `-p`, the desktop app, VS Code, and possibly some Remote Control sessions (§2).
  - The tool call waits for the job to settle and sends progress notifications (queue position and stage) at an
    interval shorter than the stdio idle window.
  - Claude Code moves the call to the background after 2 minutes, and the result arrives as a task notification.
  - `navigator_cancel(job_id)` cancels the job.
- **Timeouts.** None by default.
  - An optional job deadline in seconds from enqueue can be given at server startup with `NAVIGATOR_JOB_TIMEOUT`
    (env in the `.mcp.json` entry) or `--job-timeout`.
  - When it is set, the httpx read timeout is bounded by the remaining time, and on expiry the job ends as
    `error{reason: "timeout", stage}`.
  - `.mcp.json` sets no `timeout`, so Claude Code's default of about 28 h applies.
  - Task `ttlMs` is `null`. The server drops a finished job's result once it has been fetched, or when the session
    ends.
- **Persistence.**
  - Jobs live in memory. A stdio server stops with the session.
  - When the server starts, a job that the audit files record as `running` is marked `failed`.

## 10. Milestones

1. **Scaffold.** Create the plugin manifest, `.mcp.json`, and a `uv` project with an `MCPServer` stdio server that
   exposes `navigator_start` and `navigator_status`. Check it with `claude plugin validate .` and load it with
   `claude --plugin-dir .`.
2. **Session state.** Build `SessionState`, rendering and persistence, with golden-summary tests.
3. **Ollama loop.** Build the Ollama client and the ask/submit loop with the bounce and resend/retry rules, tested
   against a fake Ollama. This includes the §9a payload layout, the prefix-stability test, and capturing the cache
   metrics.
3b. **Async jobs.** Job queue and worker, the MCP Tasks `Extension`, the progress-notification fallback, cancel, the
    optional job deadline, and the 503 backoff (§9b).
4. **Pair mode.** Pair charter, the `set_plan` and `decide` schemas, and `skills/pair`.
5. **Scout mode.** Scout charter, its answer-tool schemas, and `skills/scout`.
6. **Compression.** LLMLingua-2 behind a flag, plus:
   - tests that the protected spans come through unchanged
   - token counts before and after
   - an A/B run on one real session
7. **Retire.** Remove to-llm and scout-ahead.

## 11. Open questions

- Is `uv` available wherever the plugin runs?
- What should the default compression rate and model be? What are the install and download size and the CPU
  latency in the container?
- Does qwen-agent's answer quality change with compressed summaries?
- Do MCP tool calls prompt for permission after the skill's invoking turn? If they do, add a settings allow rule.
- How are the synced to-llm and scout-ahead skills removed?
- Should `keep_alive` be sent? It adds a fifth key to the to-llm payload.
- Does `/api/chat` return `prompt_eval_cached_count`, and how much of the prefix does Ollama reuse with this payload
  layout?
- Is the append-only tool-round layout (§9a) acceptable in place of to-llm's per-send summary rewrite?
- Do MCP tasks apply to Remote Control sessions? Check `claude --remote-control` in a terminal, server mode, and
  Remote Control started from the desktop app.
- Should `keep_alive` be sent now that a queue serializes requests?
- `uv` and `python3 -m venv` were both unavailable in the planning container. What runtime does `.mcp.json` launch
  the server with?

## 12. Verification

- `uv run pytest` passes in `server/`.
- `claude plugin validate .` passes.
- In a session started with `claude --plugin-dir .`, `/mcp` lists the `navigator` server.
- Running `/navigator:pair` on a small task, Claude calls only `navigator_*` tools for navigator traffic and never
  runs curl.
- Each `request-N.json` has exactly four top-level keys and a freshly rendered summary.
- Token counts are compared with `NAVIGATOR_COMPRESSION` on and off.
- In an interactive terminal session, `navigator_ask` returns a task straight away. `/tasks` shows its
  `statusMessage`, and Claude acts on the `Outcome` when the task completes.
- With a one-shot `claude -p`, the same call takes the fallback path and still returns the `Outcome`.
- A second `navigator_ask` while a job is active returns the `job <id> active` error.
- Remote Control check: run `claude --remote-control` and drive one `navigator_ask` from claude.ai.

## Phase documents

The implementation detail for each milestone (§10) is in its own doc:

1. [Phase 1: Scaffold plugin + MCP server](phases/phase-1-scaffold.md)
2. [Phase 2: Session state, rendering, persistence](phases/phase-2-session-state.md)
3. [Phase 3: Ollama client + ask/submit loop](phases/phase-3-ollama-loop.md)
   - [Phase 3b: Async jobs, queue, MCP Tasks](phases/phase-3b-async-queue.md)
4. [Phase 4: Pair mode + `skills/pair`](phases/phase-4-pair.md)
5. [Phase 5: Scout mode + `skills/scout`](phases/phase-5-scout.md)
6. [Phase 6: LLMLingua-2 compression](phases/phase-6-compression.md)
7. [Phase 7: Retire to-llm / scout-ahead](phases/phase-7-retire.md)
