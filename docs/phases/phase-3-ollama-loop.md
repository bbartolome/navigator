# Phase 3: Ollama client and ask/submit loop

Milestone 3 of [navigator-plugin-plan.md](../navigator-plugin-plan.md) (§10). Depends on phase 1 (MCP server, `navigator_start`/`navigator_status`) and phase 2 (`SessionState`, `render.py`, persistence).

Scope: `server/src/navigator_mcp/ollama.py`, the `navigator_ask` and `navigator_submit` handlers in `server.py`, and `Outcome` models. Out of scope: charters and answer-tool schemas (phases 4-5), compression (phase 6), `navigator_record` and `navigator_end` beyond what the loop needs.

## 1. Inputs from earlier phases and from modes

From phase 2: `SessionState` (§6) and `render.render_summary(state, *, volatile: bool) -> str`. Phase 3 requires `render_summary` to expose the two blocks of §9a items 3 and 4 separately, as `render_stable(state)` and `render_volatile(state)`. Both are pure functions of state.

From `schemas.py` (§3): `NAVIGATOR_TOOLS: list[dict]`, the exploration tools of §2 (`read_file`, `list_dir`, `grep`, `screenshot`, `web_search`, `need_info`) in fixed order, each in Ollama tool-definition shape (see §3 below).

From a mode object (phases 4-5 supply the real ones; phase 3 tests use a stub). `answer_tools` is keyed by tool name:

```python
class Mode(Protocol):
    name: Literal["pair", "scout"]
    charter: str                              # system message, sent verbatim
    answer_tools: dict[str, dict]             # name -> Ollama tool definition
    def missing_fields(self, tool: str, args: dict) -> list[str]: ...
        # [] when args satisfy the answer tool's required fields (§5 "validates the required fields")
    def carry_forward(self, args: dict) -> list[str]: ...
```

The `tools` array of a request is `NAVIGATOR_TOOLS + [answer_tools[ask.answer_tool]]`. `navigator_ask` rejects an `answer_tool` not in `mode.answer_tools` with `Outcome(status="error")`.

## 2. Ollama API facts

Verified against [ollama docs/api.md](https://github.com/ollama/ollama/blob/main/docs/api.md), `/api/chat` section:

- Request fields: `model` (required), `messages`, `tools`, `think`, `format`, `options`, `stream`, `keep_alive`. `stream: false` returns one response object.
- Message fields: `role` (`system`, `user`, `assistant`, `tool`), `content`, `thinking`, `images` (base64 strings), `tool_calls`, `tool_name`.
- Tool definition: `{"type":"function","function":{"name","description","parameters":{"type":"object","properties":{...},"required":[...]}}}`.
- A tool call in a response sits at `message.tool_calls[i].function.{name, arguments}`. `arguments` is a JSON object, not a string. The docs' examples show no call id or index, and the page mentions no `tool_call_id`.
- Returning results: append the assistant message that carries `tool_calls`, then one message `{"role":"tool","content":"<text>","tool_name":"<name>"}` per result.
- Final-object fields: `done`, `done_reason`, `total_duration`, `load_duration`, `prompt_eval_count`, `prompt_eval_duration`, `eval_count`, `eval_duration` (durations in nanoseconds).
- `prompt_eval_cached_count` appears only in the `/api/generate` section, not in the `/api/chat` fields or examples. See open question Q2.

httpx, from [Timeouts](https://www.python-httpx.org/advanced/timeouts/) and [Transports](https://www.python-httpx.org/advanced/transports/):

- `httpx.Timeout(10.0, connect=60.0)`: positional value is the default, keywords override per type. Types: connect, read, write, pool. Each bounds one operation (a connect, one received chunk, one sent chunk, one pool acquisition), not the whole request. The documented default is 5 s.
- `httpx.MockTransport(handler)`: `handler(request) -> httpx.Response`, passed as `httpx.Client(transport=transport)`. The page does not document `AsyncClient` with `MockTransport` or async handlers. See Q5.

## 3. `ollama.py`

### Client

```python
class OllamaClient:
    def __init__(self, base_url: str, model: str, *, transport: httpx.BaseTransport | None = None): ...
    def chat(self, messages: list[dict], tools: list[dict]) -> ChatReply: ...
```

- `httpx.AsyncClient(base_url=..., timeout=httpx.Timeout(None), transport=transport)`: no timeout by default (main doc §8, §9b). When `NAVIGATOR_JOB_TIMEOUT` is set, phase 3b passes the remaining job time instead. Phase 3b §1 lists the changes this phase takes on: async `chat` and loop, the `on_stage`/`deadline_remaining`/`sleep` parameters, the 503 backoff, `Error.stage`, and anyio tests. The sync-client wording elsewhere in this doc is superseded by that list. `transport` is the test seam. Sync is chosen so the `MockTransport` form documented above applies.
- `POST /api/chat`. Body is `canonical_json(payload)` sent as `content=` bytes with header `Content-Type: application/json`. The bytes written to `request-N.json` are the bytes sent.
- Config comes from §8: `NAVIGATOR_OLLAMA_URL`, `NAVIGATOR_MODEL`.

### Request JSON

Exactly four top-level keys (§2, §12):

```json
{"messages":[...],"model":"qwen-agent:latest","stream":false,"tools":[...]}
```

No `options`, no `keep_alive` (Q1). Message objects carry only `role`, `content`, and where applicable `images`, `tool_calls`, `tool_name`.

### Canonical serialization

`canonical_json(obj) = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")`. It is applied to the whole payload, so each tool definition and each message is serialized with sorted keys too (§9a item 2). List order is never sorted. `NAVIGATOR_TOOLS` order is fixed by `schemas.py`; the answer tool is always last.

### Reply parsing

`ChatReply` fields: `message: dict`, `tool_calls: list[RawCall]`, `text: str`, `metrics: Metrics`, `raw: dict`.

- `RawCall(name: str, arguments: dict)`, read from `message.tool_calls[].function`.
- Classification of a 200 response, in order:
  1. Body not JSON, or `message` missing, or any `tool_calls[].function.arguments` not a dict, or any `function.name` not in the request's `tools`: **malformed**.
  2. `tool_calls` non-empty: **calls**.
  3. `content.strip() == ""` and no `tool_calls`: **empty**.
  4. Otherwise (`content` non-empty, no `tool_calls`): **plain text**.
- A non-2xx status or an `httpx.HTTPError` (including timeouts) is not resent. It returns `Outcome(status="error", reason=<status or exception class and message>)`. Session state stays at the pre-send state so Claude can call the same tool again.

### Metrics

```python
class Metrics(BaseModel):
    prompt_eval_count: int | None
    prompt_eval_cached_count: int | None   # None when the response omits it
    prompt_eval_duration: int | None       # ns
```

Each is read from the top level of the response object with `.get()`. The three values are stored in `reply-N.json` under a sibling key (§7) and accumulated per session for `navigator_status`.

## 4. State machine

In the server this state machine runs inside a job from phase 3b (main doc §9b). The `Outcome` returned below is the
job's result.

Session-level phase, stored in `SessionState` and persisted (phase 2):

```
IDLE --ask--> THINKING --tool_calls--> AWAITING_SUBMIT --submit--> THINKING
                 |                                                    |
                 +--need_info--> IDLE      +--answered / error--> IDLE
```

- `navigator_ask` is valid only in `IDLE`. In any other phase it returns `Outcome(status="error", reason="session awaiting submit")` (or "busy") and changes nothing.
- `navigator_submit` is valid only in `AWAITING_SUBMIT`. Otherwise, same error form.
- `THINKING` is held only while a handler runs. A handler always ends in `IDLE` or `AWAITING_SUBMIT`.

### `navigator_ask` steps

1. Validate arguments. Load images (§7). On failure return `error`; state unchanged.
2. Increment `ask_n`. Set `pending = None`, `bounces = 0`, `resent = False`, `retried = False`.
3. Build `messages` once: `[system(charter), user(stable block + volatile block + excerpts + question)]`. The question's last line is `Answer by calling <answer_tool>.`, appended by the server if the caller's text does not already end with it.
4. `send_loop()`.

### `navigator_submit` steps

1. Require one result per outstanding `call_id` in `pending`, no extras, no duplicates. Otherwise return `error` listing the diff; state unchanged.
2. Append to `messages` (append-only, §5):
   - One `{"role":"tool","tool_name":<call.name>,"content":<content>}` per result, in the original call order. A `denied` result sends `content = "DENIED: " + content`.
   - For each result with `image_path`: after all tool messages, one `{"role":"user","content":"<call_id> image","images":[b64]}`, per to-llm. Image loading follows §7.
3. Update the action log and the files-read/web-searches/shots lists in `SessionState` (phase 2 API), but do not re-render the already-sent prefix.
4. `send_loop()`.

### `send_loop()`

Repeat until an `Outcome` is produced:

1. Write `request-N-<suffix>.json` (§8), send, write `reply-N-<suffix>.json`.
2. Classify the reply (§3).
   - **calls**: split into answer-tool calls and exploration calls.
     - Any call named like a tool not offered is already malformed (§3).
     - If an answer-tool call is present: validate with `mode.missing_fields`. Missing fields go back as a bounce (§5). Valid: return `answered` with `tool`, `result=args`, `carry_forward=mode.carry_forward(args)`. An answer-tool call mixed with exploration calls: the answer call wins; the exploration calls are logged in `reply-N` and ignored.
     - `need_info` (alone or with others): return `need_info` with `questions`. State goes to `IDLE`. Claude answers and calls `navigator_ask` again with the same question (to-llm).
     - Exploration calls: assign call ids (§5), run the bounce rules (§5). If every call is bounced, append the assistant message and the bounce tool messages and continue the loop. If at least one call survives, return `tool_calls` with the surviving calls, store `pending`, state `AWAITING_SUBMIT`. Bounced calls of the same reply have their bounce tool messages appended at submit time, before the real results, in original call order.
   - **malformed / plain text**: §6.
   - **empty**: §6.
3. On each continuation, the prior assistant message is appended exactly as received (`message` object from the reply), so its bytes in the next request equal its bytes in `reply-N`.

Maximum rounds per ask: 12 sends, counting bounces, resends and tool rounds. Exceeding it returns `error` ("round limit"), state `IDLE`.

## 5. call_id and bounce rules

- `call_id = "call-{k:04d}"` (from phase 2 `alloc_call_id`), `k` a per-session counter starting at 1, incremented per call in reply order, never reset across asks. Bounced calls consume an id. No random or time-derived component (§9a). Decided 2026-11-02: the `call-NNNN` format wins over `c{k}`.
- Ollama has no call id (§2). The id exists only between server and Claude. In the Ollama messages a result is matched by `tool_name` and message order.
- `Outcome.calls[i]` = `{call_id, name, args (arguments minus "justification"), justification}`.

Bounce rules, evaluated per exploration call in order:

1. **Missing justification.** `arguments.get("justification")` absent, not a string, or blank after strip (`need_info` is exempt). Bounce text: `"{name}: justification is required. State what you expect to find and which question it settles, then call again."`
2. **Exact repeat.** The call has the same `name` and the same `args` (compared as `canonical_json` of `arguments` with `justification` removed) as an earlier call in this session's action log, and its `justification` does not differ in a way the server can check. The server cannot judge "what changed", so the rule is: the repeat is bounced unless its justification string, normalized by `strip().lower()`, differs from every earlier justification for that call. Bounce text: `"{name} with these arguments already ran as {earlier_call_id}; its result is above. Call again only if the justification names what changed, or answer."`
3. Order: rule 1 before rule 2. A bounced call is logged in the action log as `"{name} bounced: {rule}"`.

A bounce is delivered as `{"role":"tool","tool_name":name,"content":<text>}`. Per ask, three bounced calls are allowed; a fourth bounce returns `error` ("repeated bounces"). Decided 2026-11-02. Claude sees nothing for a bounce unless the loop ends.

## 6. Malformed, empty, plain-text replies

Counters are per ask: `resent`, `retried`.

- **Malformed**: if `resent` is false, set it, append a user message `"Your reply was malformed: <specific reason>. Call a tool with valid arguments."`, resend (suffix `r1`). If already `resent`, return `error`.
- **Plain text**: if `resent` is false, set it, append the assistant message as received, then a user message `"Call <answer_tool> with that answer; don't reply in text."`, resend. Otherwise return `error`.
- **Empty**: if `retried` is false, set it and rebuild the request from the first message: the same system, tools and stable block, but each excerpt is trimmed to the lines the question needs plus 5 on each side. Trim rule: the caller supplies `excerpts[{path,start,end,text}]` where `start`/`end` are the needed range, and `text` is the surrounding context; the server keeps lines `[start-5, end+5]` of `text` (1-based, clamped, line `i` of `text` is source line `excerpt.first_line + i`). Note: this requires `excerpt.first_line`, or `text` is assumed to begin at `start - context_before`. See Q6. The earlier tool-round messages are dropped on retry. Send as `request-N-retryK.json`. If the retry is also empty, return `error` with `reason` containing the sizes tried: UTF-8 bytes and `prompt_eval_count` (if present) of the original and the retry.
- `resent` and `retried` are independent; one ask can use one of each.
- The resend message and trimmed rebuild are the only places the request prefix may change within an ask (§9a).

## 7. Images

- `image_path` (from `navigator_ask.images` or `navigator_submit.results[].image_path`) is read as bytes, base64-encoded with `base64.b64encode(...).decode("ascii")` (standard alphabet, padded), and placed in the message's `images` list.
- Size check: the decoded file must be at most 1,000,000 bytes (to-llm: about 1 MB). Over the limit returns `error` naming the path and size, with no state change, so Claude can retake it at a lower viewport (§5).
- Missing file or unreadable file: `error`, no state change.
- An `ask` image is attached to the first user message's `images`. A submit image rides on its own user message (§4).
- Images are not re-attached on a later ask. The summary lists them as `shot-K` (§6, phase 2). Audit files store `images` as `"<sha256 hex>:<byte length>"` placeholders, not the base64 text. The bytes sent are unchanged.

## 8. Audit files

Directory `${NAVIGATOR_STATE_DIR}/<session_id>/` (§8 config).

| File | Content |
| --- | --- |
| `request-N.json` | First send of ask N: the exact bytes sent, except images (§7) |
| `request-N-rK.json` | K-th later send of ask N (tool round, bounce, resend), K = 1, 2, ... |
| `request-N-retryK.json` | Trimmed retry after an empty reply (§6) |
| `reply-N.json`, `reply-N-rK.json`, `reply-N-retryK.json` | Matching raw response object, plus a top-level key `"navigator_metrics": {prompt_eval_count, prompt_eval_cached_count, prompt_eval_duration}` |

- `reply-*` files are the only ones with extra keys. `request-*` files have exactly the four keys of §3.
- Files are written before the send (`request`) and after the response (`reply`), and never rewritten.
- On an HTTP or transport failure, no `reply` file is written. A sibling `reply-...error.txt` holds the exception text. Open: Q7.

## 9. Outcome models

Pydantic v2, discriminated on `status` (§4):

```python
class Call(BaseModel):      call_id: str; name: str; args: dict; justification: str
class ToolCalls(BaseModel): status: Literal["tool_calls"]; calls: list[Call]
class NeedInfo(BaseModel):  status: Literal["need_info"];  questions: list[str]
class Answered(BaseModel):  status: Literal["answered"];   tool: str; result: dict; carry_forward: list[str]
class Error(BaseModel):     status: Literal["error"];      reason: str
Outcome = Annotated[ToolCalls | NeedInfo | Answered | Error, Field(discriminator="status")]
```

`need_info.questions` is the navigator's `questions` argument unchanged. The server returns only these shapes (§4); metrics are exposed through `navigator_status`, not the `Outcome`.

## 10. Tests

All in `server/tests/test_ollama_loop.py`, using `httpx.MockTransport` through `OllamaClient(transport=...)`. The fake handler is a scripted queue: it pops one canned response body per request, records `request.content` (bytes), and returns `httpx.Response(200, json=body)`. A temp dir stands in for `NAVIGATOR_STATE_DIR`. A stub `Mode` has charter `"CHARTER"` and one answer tool `decide` requiring `decision` and `carry_forward`.

Helper bodies: `call(name, **args)` = `{"message":{"role":"assistant","content":"","tool_calls":[{"function":{"name":name,"arguments":args}}]},"done":true,"prompt_eval_count":100,"prompt_eval_duration":5}`.

| # | Test | Input | Expected |
| --- | --- | --- | --- |
| 1 | request shape | one `ask`, reply `decide(...)` | recorded body parses to keys exactly `{messages,model,stream,tools}`; `stream` is `false`; `messages[0]` is `{role:"system",content:"CHARTER"}`; `tools[-1].function.name == "decide"`; body bytes equal `canonical_json(parsed)` |
| 2 | timeout | construct client with no job deadline | `client.timeout == httpx.Timeout(None)` |
| 3 | answered | reply `decide(decision="x", carry_forward=["a"])` | `Answered(tool="decide", result={...}, carry_forward=["a"])`; state `IDLE` |
| 4 | tool round | reply `read_file(path=..., justification="j")`, then `submit` result `ran`, reply `decide` | first outcome `ToolCalls` with `call_id == "call-0001"`, `args` without `justification`, state `AWAITING_SUBMIT`; second request `messages` has, after the first request's messages, an assistant message with the exact `tool_calls` and `{role:"tool",tool_name:"read_file",content:...}` |
| 5 | call ids | two calls in one reply, then another in the next round | `call-0001`, `call-0002`, then `call-0003` |
| 6 | submit mismatch | submit missing `c2` | `Error`; state still `AWAITING_SUBMIT`; no request sent |
| 7 | denied | `status:"denied"`, content `"outside repo"` | tool message content `"DENIED: outside repo"` |
| 8 | no justification | `read_file` without it, then `decide` | no `ToolCalls` outcome; second request ends with assistant message plus tool message containing `"justification is required"`; final `Answered`; one id consumed |
| 9 | exact repeat | round 1 `read_file(a)`, submit, round 2 `read_file(a)` with identical justification, then `decide` | round 2 is bounced (tool message contains `"already ran as call-0001"`); `Answered` returned with no second `ToolCalls` |
| 10 | repeat with new justification | same, justification text differs | second call surfaces as `ToolCalls` `c2` |
| 11 | bounce cap | four consecutive bounced replies | `Error("repeated bounces")` |
| 12 | malformed | `arguments` is the string `"{}"`, then valid `decide` | second request ends with the malformed user message; `Answered`. With two malformed replies: `Error` |
| 13 | plain text | `content:"hi"` no tool_calls, then `decide` | second request has assistant `"hi"` then user `"Call decide with that answer; don't reply in text."` |
| 14 | empty then ok | empty reply, then `decide` | retry file `request-1-retry1.json` exists; each excerpt in it has at most `(end-start+1)+10` lines; earlier tool-round messages absent |
| 15 | empty twice | two empty replies | `Error` whose `reason` contains both byte sizes; two retry-related files exist; exactly 2 sends |
| 16 | need_info | reply `need_info(questions=["q"])` | `NeedInfo(["q"])`; state `IDLE`; a following `ask` is accepted |
| 17 | answer with missing fields | `decide(decision="x")` | bounce asks for `carry_forward`; next valid reply yields `Answered` |
| 18 | images | 1,000,000-byte PNG fixture and 1,000,001-byte one | first accepted, `images[0] == base64.b64encode(bytes)`; second returns `Error` and sends nothing |
| 19 | HTTP errors | handler returns 500; handler raises `httpx.ReadTimeout` | `Error`; state unchanged; no resend |
| 20 | metrics | reply with `prompt_eval_count:100`, `prompt_eval_duration:5`, `prompt_eval_cached_count:80`; another without the cached key | `reply-*.json["navigator_metrics"]` equals `{100, 80, 5}` and `{100, None, 5}`; `navigator_status` sums per session |
| 21 | audit files | ask with 2 tool rounds | files `request-1.json`, `request-1-r1.json`, `request-1-r2.json` and the three replies exist; `request-*` have four keys; images replaced by placeholder |
| 22 | prefix byte-stability | ask with 3 tool rounds | for each consecutive pair of recorded request bodies, the serialized `messages` array of the earlier send, minus its closing `]`, is a byte prefix of the later one; `tools` bytes and `model` equal across all sends; the system message bytes equal across asks |
| 23 | deterministic render across asks | two sessions with the same state and question | the request bodies are byte-identical |
| 24 | summary not re-rendered mid-ask | mutate `SessionState` between rounds (as submit does) | first user message's `content` bytes unchanged in every later send of the same ask |

Test 22's prefix check is on messages after canonical serialization. Because `sort_keys` and `messages` come before `model` in the byte stream, the comparison is on the `"messages":[...` slice, not on the whole body.

## 11. Open questions (from §11 of the main doc, plus implementation unknowns)

- **Q1 (keep_alive).** Should `keep_alive` be sent? `api.md` documents it (default `5m`); sending it makes the payload five keys instead of §2's four. Does a 2 h session with gaps over 5 minutes cause model reloads that cost more than the fifth key? How is it measured from `load_duration`?
- **Q2 (cached count).** Does `/api/chat` return `prompt_eval_cached_count`? `api.md` documents it only under `/api/generate`. Until measured against the real server, the field is `None` when absent and test 20 covers both cases. How much of the prefix does Ollama reuse with this layout, and is `prompt_eval_duration` alone enough to infer it?
- **Q3 (append-only layout).** Is the append-only tool-round layout of §9a acceptable in place of to-llm's per-send summary rewrite? In particular, does the navigator answer worse when the summary does not show results from the current round?
- **Q4 (tool-result matching).** The docs show `tool_name` and no call id. When one reply has two calls to the same tool, is the result matched by message order? This phase relies on original call order and does not verify it against the server.
- **Q5 (MockTransport async).** Does `MockTransport` accept async handlers with `AsyncClient`? The transports page does not say. Phase 3b read it from the httpx `master` source (`mock.py`). Confirm it against the pinned release.
- **Q6 (excerpt trim).** `excerpts[{path,start,end,text}]` (§4) has no field for the first line number of `text`. Does the ±5 trim apply to `text` as supplied (with `start`/`end` giving the needed range inside it), and does `navigator_ask` need a `first_line` field?
- **Q7 (failure audit).** Is a `reply-...error.txt` sibling acceptable in the audit directory, given §5 names only `request-N.json` and `reply-N.json`?
- **Q8 (limits).** The 12-send round cap and 3-bounce cap are not in the main doc. Are they the right values?
- **Q9 (repeat rule).** Is the justification-text difference check an acceptable server-side proxy for "says what changed" (§5)?

## 12. Done criteria

- `uv run pytest` passes in `server/`, including tests 1-24 against the fake Ollama.
- `navigator_ask` and `navigator_submit` are registered in `server.py` and return `Outcome` JSON in the four shapes of §4; the `mcp` tool schema shows the arguments of §4.
- Every `request-N*.json` written by the tests has exactly the keys `messages`, `model`, `stream`, `tools`.
- Prefix byte-stability test (22) passes.
- Metrics are written to every `reply-*` file and summed in `navigator_status`.
- Q1-Q4 are each either answered by one run against a real Ollama (recorded in the PR) or left open with the answer noted.
