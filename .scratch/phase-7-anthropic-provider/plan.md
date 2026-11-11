# Phase 7 implementation plan: optional Anthropic provider

Spec: `docs/phases/phase-7-anthropic-provider.md`. Branch: `anthropic-provider/spec`. Nothing is committed; the
implementation stays in the working tree.

Status: APPROVED by an Opus reviewer on round three (v3). v4 folds in the reviewer's seven minor notes. Changes from v3
are marked (v4).

## 0. Baseline and verified facts

Baseline: `cd server && uv run --frozen pytest -m "not slow"` gives 290 passed, 1 deselected (run 2026-11-08).

Code facts:

- `ollama.py`: `OllamaClient` at 139. `Runtime` at 165, with `client: OllamaClient`. `build_request` at 150.
  `_post` at 361; the Busy503 f-string is at 380. `_loop` calls `_post` at 428 and `classify` at 433.
  `_retry_messages` at 588 returns `[system, rebuilt user]`. Answer-tool bounce loop at 499-504. No-survivor bounce at
  563-565. `_submit` bounce at 688-690, results at 691-694. `state.bounces` resets only in `_ask` (645), and `_submit`
  never changes it.
- `jobs.py`: `client_factory` type at 122. `_run` at 231. `job.snapshot` and `files_before` are set before the factory
  call. `rt = ollama.Runtime(client=self._client_factory(), ...)` is at about 244-252. The `try:` is at about 255.
  `worker()` at 205-213 has no guard. `server.py:25-26` runs the worker in the lifespan task group. `_env_client` is at
  452.
- `_run` catches `Exception` inside its `try`, then calls `_rollback` and `_finish(failed)`.
- `ask` and `submit` catch only `TransportFailure` and `ImageError`.
- Session state is saved as `model_dump_json`. `messages` is a `list[dict]`, so the new `navigator_*` keys reach the
  session file.
- `server/uv.lock` is tracked by git. `anthropic` is not in it. `scripts/launch.sh` installs from `pyproject.toml`.
- `tests/test_plugin_manifest.py` asserts that the `pyproject.toml` version equals the `plugin.json` version (both
  0.1.1).
- `tests/test_jobs.py:482` asserts the text `ollama busy (503)`. `tests/test_jobs.py` test 26 is the pattern for
  backoff tests: `backoff=record` with `FakeClock`.
- `tests/fakes.py` has `FakeClock` with `sleep`, `advance` and `virtual_sleep`, and `FakeOllama`, which uses navigator's
  `httpx.MockTransport`.
- `tests/test_stdio_tasks.py:92` passes `dict(os.environ)` to the server subprocess.
- `tests/golden/` already holds golden files (`g1_empty_pair.txt` and others).

SDK facts (`anthropic` 2.6.0, the PyPI latest; installed in `/tmp/anthropic-sdk-probe/lib`, not in the repo):

- Requires Python >=3.10. Depends on `httpx2`, not `httpx`.
- `anthropic.APITimeoutError` subclasses `APIConnectionError`. `RateLimitError` subclasses `APIStatusError`.
- `anthropic.Timeout` is `httpx2.Timeout`. The SDK rejects an `httpx` Timeout (`_base_client.py:161-168`, a
  `TypeError`). It checks `isinstance(http_client, httpx2.AsyncClient)` (`_base_client.py:1888-1891`).
- `messages.create` (non-beta) has `thinking`, `output_config` and `cache_control`, but no `fallbacks` and no `betas`.
  `beta.messages.create` has both. The `anthropic-beta` header is sent only when `betas` is passed.
- An injected transport works: `anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(h))`. The transport
  handles every host, and an explicit transport ignores environment proxies (`httpx2/_client.py:1495`).
- The SDK sends `anthropic-version: 2026-10-21` by default.
- Construction and credentials (probed):
  - With no key, no profile and no config env vars, `AsyncAnthropic()` constructs. The first request raises a plain
    `TypeError` ("Could not resolve authentication method..."), before any HTTP request.
  - With `ANTHROPIC_CONFIG_DIR` set to an empty directory and no key, construction raises `CredentialsError`. Do not set
    `ANTHROPIC_CONFIG_DIR` in tests.
  - With `ANTHROPIC_PROFILE` set and no key, construction raises `CredentialsError`.
  - A bad profile on disk raises `CredentialsError` only when `<config_dir>/active_config` exists or `ANTHROPIC_CONFIG_DIR`
    is set. Otherwise the SDK ignores the profile directory (`lib/credentials/_chain.py`, step 5).
  - With `ANTHROPIC_API_KEY` set, the key wins and construction succeeds.
  - Setting `HOME` to an empty directory hides `~/.config/anthropic`. The SDK reads `Path.home()/.config/anthropic`
    (`lib/credentials/_constants.py:81`) and no XDG path.
- Echo serialization:
  - Default `model_dump(mode="json", exclude_none=True)` gives `"from_"` for a fallback block. The wire name is `from`.
  - `model_dump(mode="json", by_alias=True, exclude_unset=True)["content"]` gives the wire content exactly: `citations:
    null` is kept, and the SDK-added `caller` and `toolset_name` are omitted.
  - An unknown block type parses as `BetaTextBlock` with no `.text` attribute. Select blocks by the `type` key of the
    dumped dict, not by class.
- Long non-streaming guard: the SDK raises `ValueError("Streaming is required...")` for model IDs in
  `MODEL_NONSTREAMING_TOKENS` (the Opus 4 and 4.1 IDs, limit 8192) when `max_tokens` is 16000 and no timeout is passed.
  It does not raise for the 5-series IDs (v4, verify in step 2).

API facts (from the `claude-api` skill):

- Model IDs, `thinking: {"type": "adaptive"}`, `output_config: {"effort": "medium"}`, top-level `cache_control`, and
  `fallbacks: "default"` with beta `server-side-fallback-2026-07-01` are all correct for the four fallback models.
- `tool_choice` `any` and `tool` return 400 on the new models. The spec does not send `tool_choice`.
- Consecutive same-role messages are accepted. Thinking blocks must be passed back unchanged.
- "A retry" is listed as not an edit in the skill's preserved-thinking table. `_retry_messages` therefore starts a new
  conversation, as spec 6.3 says.
- Non-streaming mid-output fallback declines omit the declined partial. The `fallback` block is an audit marker and is
  echoed where it appears.
- `stop_details` is set only when `stop_reason == "refusal"`.

Not verified offline (no API key; live calls cost money):

- Live acceptance of the echoed blocks.
- Live behavior of `fallback` blocks in a multi-round ask.

## 1. Deviations from the spec, and open decisions

**D1. The `ValueError` in `_env_client` kills the worker (required fix).** `_run` calls `self._client_factory()` before
its `try` (about line 245; the `try` is at about 255). `worker()` has no guard, and `server.py:25-26` runs it in the
lifespan task group. A bad `NAVIGATOR_PROVIDER` would therefore stop the worker, probably the server, and leave the
job `running`. The spec's section 3 and test 4 need the job to fail with the message. Fix: call the factory in its own
`try`. On exception, call `_rollback`, then `_finish(job, "failed", failure=str(exc) or type(exc).__name__)`, and
return. Build `Runtime` from the returned client. This is safe: `job.snapshot` and `files_before` are already set,
`_rollback` only re-saves the snapshot, and `_finish` sets `settled`, so `cancel()` cannot hang.

**D2. httpx and httpx2.** The SDK requires `httpx2` objects. The spec's test 14 uses `http_client=httpx.AsyncClient(...)`,
which the SDK rejects. Tests use `anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(...))`. Production
code does not import `httpx2`; tests may, since it is a transitive dependency. `AnthropicClient(model, *, transport=None)`
passes `transport` to `DefaultAsyncHttpxClient` only when it is set.

**D3. Timeout conversion.** `_post` passes an `httpx.Timeout` from navigator's `httpx`. The SDK rejects it. `post`
converts it to `anthropic.Timeout(connect=t.connect, read=t.read, write=t.write, pool=t.pool)` when `timeout` is not
`None`.

**D4. Echo serialization.** `message.navigator_anthropic_content` is
`msg.model_dump(mode="json", by_alias=True, exclude_unset=True)["content"]`. The default dump emits `from_`, which is
invalid. The audit field `navigator_anthropic_response` uses `msg.model_dump(mode="json", by_alias=True)`, the full dump.
The spec's section 7 says `model_dump(mode="json")`; this is the alias-correct form of the same thing.

**D5. Missing credentials.** The spec says construction fails. For a plain missing key it does not: the failure is a
`TypeError` at the first request. The observable outcome matches the spec: the job fails with the SDK message, and `_run`
rolls back. Construction can still raise `CredentialsError` when `ANTHROPIC_PROFILE` or `ANTHROPIC_CONFIG_DIR` is set, or
when `<config_dir>/active_config` exists and the profile is bad (v4). The D1 guard covers all these cases. `_env_client`
does not check the key, as the spec requires.

**D6. `_post` changes by one line.** Section 8 says `_post` does not change. Section 4 says the Busy503 text uses the
client name. Only the f-string changes: `f"{rt.client.name} busy (503): ..."`. Mention this in the report.

**D7. `uv.lock`.** The spec omits it, but it is tracked. Run `uv lock` in `server/` after adding the dependency, and
check `git diff --stat server/uv.lock`. Only `anthropic`, `httpx2` and their dependencies may be added. Do not upgrade
other packages. The lock matters for `uv run --frozen`; the launcher installs from `pyproject.toml`.

**D8. Version bump (open).** The spec says nothing about a version. The manifest test requires `pyproject.toml` and
`plugin.json` to match. Do not bump. The launcher reinstalls on any change to `pyproject.toml` content. Ask the user in
the report.

**D9. Withdrawn.** Retry in `_retry_messages` is not a history edit. See section 0.

**D10. Index of `tool_use` blocks (clarification).** Spec 6.2 says "the `tool_use` block at the index `navigator_call`".
Index only the `tool_use` blocks, in order, of the latest `assistant` message before the tool message. This matches
`reply.calls`.

**D11. Index in `_submit` (clarification).** Use `enumerate(pending)` and skip bounced entries for results. Do not use
`survivors.index(p)`. Test 7 must cover this (section 2.6).

**D12. Normalized provider in the error.** The error message uses the normalized value (trimmed, lowercase).

**D13. Stop reasons (open question).** Only `refusal` and `max_tokens` set `navigator_error`, as the spec says. The skill
notes that `model_context_window_exceeded` can also cut a reply short, and the spec does not map it. Keep the spec's
behavior. Raise it in the report.

**D14. Empty tool content.** A tool message with `content == ""` becomes `tool_result` with `content: ""`. A test checks
that conversion does not crash. Live acceptance is unverified.

**D15. Test isolation.** The README tells users to export `ANTHROPIC_API_KEY` and `NAVIGATOR_PROVIDER` in their shell, and
the subprocess test passes `os.environ` through. Three fixtures handle this. All three live in `tests/conftest.py`
(v4: pytest does not discover fixtures defined in a helper module).

- `_clean_env`, autouse, all tests. It unsets with `monkeypatch.delenv(..., raising=False)`: `NAVIGATOR_PROVIDER`,
  `NAVIGATOR_MODEL`, `NAVIGATOR_OLLAMA_URL`, `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`,
  `ANTHROPIC_PROFILE`, `ANTHROPIC_CONFIG_DIR`, `ANTHROPIC_IDENTITY_TOKEN`, `ANTHROPIC_IDENTITY_TOKEN_FILE`,
  `ANTHROPIC_FEDERATION_RULE_ID`, `ANTHROPIC_ORGANIZATION_ID`, `ANTHROPIC_SERVICE_ACCOUNT_ID`,
  `ANTHROPIC_WORKSPACE_ID`, `ANTHROPIC_SCOPE`, `ANTHROPIC_CUSTOM_HEADERS`. Do not set `ANTHROPIC_CONFIG_DIR`.
- `isolated_home`, not autouse. It sets `HOME` to `tmp_path / "home"` with monkeypatch. Only `test_env_client.py` and
  `test_anthropic_client.py` request it, through `pytestmark = pytest.mark.usefixtures("isolated_home")`. A global `HOME`
  change would move the Hugging Face cache that the slow compression tests use.
- `anthropic_env`, not autouse. It sets `ANTHROPIC_API_KEY=test` and `ANTHROPIC_BASE_URL=http://anthropic.test`. Every
  test that sends a request through the SDK requests it.
- The D5 test does not request `anthropic_env`. It sets `ANTHROPIC_BASE_URL=http://127.0.0.1:9` so a leak would fail
  fast, and asserts that the failure contains "Could not resolve authentication".

**D16. Provider switch across a restart.** A session left in `AWAITING_SUBMIT` by an Ollama ask has assistant messages
with no `navigator_anthropic_content`. If the job runs with `anthropic`, `_convert` must not raise `KeyError`. It raises
`TransportFailure("the pending ask was made with a different provider")`. `build_request` runs in `_loop` outside
`_post`'s try, so `submit` catches the error and restores the state.

**D17. README numbers and the streaming guard.** The `NAVIGATOR_503_RETRIES` row covers both providers: Ollama 503, and
anthropic 429, 503 and 529. With no `NAVIGATOR_JOB_TIMEOUT`, the SDK applies a read timeout of 600 s, while Ollama has
none. The `max_tokens` of 16000 is under the SDK's non-streaming guard for 5-series models (450 s < 600 s). But the SDK
raises `ValueError` for `claude-opus-4-0` and `claude-opus-4-1` (limit 8192) when no timeout is passed. So with no
`NAVIGATOR_JOB_TIMEOUT`, `NAVIGATOR_MODEL=claude-opus-4-0` fails every job. With a job timeout set, it does not. The
README says so in one line. No code change.

**D18. Lazy import.** `_env_client` imports `AnthropicClient` inside the `anthropic` branch, so the Ollama path does not
load the SDK. There is no circular import either way.

## 2. Design

### 2.1 `server/src/navigator_mcp/ollama.py`

1. Add the `NavigatorClient` Protocol exactly as in spec section 4.
2. `OllamaClient` gets `name = "ollama"`. Nothing else changes except as below.
3. `Runtime.client: NavigatorClient`.
4. `OllamaClient.build_request`: strip every message key that starts with `navigator_`, then apply `_redact`. Build new
   dicts; do not mutate. `canonical_json` sorts keys, so the Ollama bytes do not change.
5. `navigator_call` on tool messages, at three sites:
   - The answer-tool bounce loop (499-504): `for i, c in enumerate(reply.calls)`.
   - The no-survivor bounce (563-565): `for i, p in enumerate(pending)`.
   - `_submit` bounce (688-690): `for i, p in enumerate(pending)`. Results (691-694): iterate `enumerate(pending)` and skip
     `p.bounce is not None`, so the index is the position in `pending` (D11).
6. `_loop`, after the `try` (427-432) and before `classify` (433):
   ```python
   if isinstance(obj, dict) and isinstance(obj.get("navigator_error"), str):
       return _settle(state, Error(status="error", reason=obj["navigator_error"], stage=current_stage))
   ```
7. The Busy503 f-string at 380 uses `rt.client.name` (D6).

### 2.2 `server/src/navigator_mcp/anthropic_client.py` (new)

Imports: `anthropic`; `httpx` (navigator's, for `Response` and the exception types); `canonical_json`, `TransportFailure`
and `_placeholder` from `.ollama`. `ollama.py` never imports `anthropic_client`, so there is no cycle.

Constants: `FALLBACK_MODELS` (the four IDs), `FALLBACK_BETA = "server-side-fallback-2026-07-01"`, `MAX_TOKENS = 16000`,
`RETRY_STATUSES = {429, 503, 529}`.

`class AnthropicClient`: `name = "anthropic"`. `__init__(self, model, *, transport=None)` builds
`anthropic.AsyncAnthropic(max_retries=0, ...)`. It adds `http_client=anthropic.DefaultAsyncHttpxClient(transport=transport)`
only when `transport` is set (D2).

`build_request(messages, tools, *, redact_images=False) -> bytes`:
- `messages[0]` must have role `system`. Its content goes to `system`, unconverted.
- Convert the rest with `_convert`, and the tools with `_convert_tools`.
- If `redact_images`, replace each image block's `data` with `_placeholder(data)`, after conversion.
- Body: `model`, `max_tokens`, `system`, `messages`, `tools`, `thinking={"type": "adaptive"}`,
  `output_config={"effort": "medium"}`, `cache_control={"type": "ephemeral"}`. Add `fallbacks: "default"` only for
  `FALLBACK_MODELS`. No `tool_choice`, `temperature`, `top_p` or `top_k`.
- Return `canonical_json(body)`.

`_convert` (spec section 6):
- `user` becomes image blocks (one per `images` entry, media type from `_media_type`), then one text block.
- `tool` becomes a `tool_result` block. Its `tool_use_id` comes from `_tool_use_id(messages, i)` (D10). Its `content` is
  the message content.
- `assistant` becomes `{"role": "assistant", "content": m["navigator_anthropic_content"]}`, unchanged. If the key is
  missing, raise `TransportFailure("the pending ask was made with a different provider")` (D16).
- Merge runs of adjacent `user` and `tool` messages into one `user` message: `tool_result` blocks first, in order, then
  the other blocks, in order.

`_media_type(b64)`: decode, then check the first bytes against spec 6.1. No match raises
`TransportFailure("image type is not supported by the anthropic provider")`.

`_tool_use_id(messages, i)`: walk back to the latest `assistant` message. Take its `tool_use` blocks in order, and return
the id at index `navigator_call` (D10).

`_convert_tools`: `name`, `description` and `input_schema` (from `parameters`), in the same order.

`post(body, timeout=None) -> httpx.Response`:
1. `json.loads(body)`. Build the kwargs: `model`, `max_tokens`, `system`, `messages`, `tools`, `thinking`,
   `output_config`, `cache_control`.
2. If `fallbacks` is in the body, pass `fallbacks` and `betas=[FALLBACK_BETA]`.
3. If `timeout` is not `None`, pass the converted `anthropic.Timeout` (D3).
4. `msg = await self._sdk.beta.messages.create(**kwargs)`.
5. Errors (spec section 8): catch `APITimeoutError` first and raise `httpx.ReadTimeout(str(exc))`. Then catch
   `APIConnectionError` and raise `httpx.ConnectError(str(exc))`. Then catch `APIStatusError` and return
   `httpx.Response(503 if status in RETRY_STATUSES else status, text=exc.response.text)`.
6. Success:
   - `echo = msg.model_dump(mode="json", by_alias=True, exclude_unset=True)["content"]` (D4).
   - `dumped = msg.model_dump(mode="json", by_alias=True)` for the audit.
   - Build `text` as the `text` values of the echo blocks whose `type` is `"text"`, joined with `\n`. Build
     `tool_calls` from the echo blocks whose `type` is `"tool_use"`: `{"function": {"name": b["name"], "arguments":
     b["input"]}}`. Select by the `type` key, not by class.
7. Reply body:
   - `message`: `role`; `content` (the text, step 6); `navigator_anthropic_content` (the echo); and `tool_calls` only
     when there are any.
   - `prompt_eval_count = input + (cache_read or 0) + (cache_creation or 0)`.
   - `prompt_eval_cached_count = cache_read or 0`.
   - `navigator_anthropic_response = dumped`.
   - `navigator_error`: `refused (<category>)` when `stop_reason == "refusal"`, where the category is
     `stop_details.category` or `none` (`stop_details` may be missing). `reply cut off at max_tokens` when
     `stop_reason == "max_tokens"`.
8. Return `httpx.Response(200, json=body)`.

`aclose`: `await self._sdk.close()`.

### 2.3 `server/src/navigator_mcp/jobs.py`

1. `client_factory: Callable[[], ollama.NavigatorClient] | None` (line 122).
2. `_run` (D1): guard the factory call as described in D1, then build `Runtime` from the returned client.
3. `_env_client` (spec section 3 rules, D12, D18):
   - `raw = os.environ.get("NAVIGATOR_PROVIDER", "")`; `provider = raw.strip().lower()`.
   - `""` or `ollama`: `OllamaClient` with the existing URL and model defaults.
   - `anthropic`: import `AnthropicClient` here, then return `AnthropicClient(os.environ.get("NAVIGATOR_MODEL",
     "claude-opus-5-5"))`.
   - Otherwise `ValueError(f"NAVIGATOR_PROVIDER must be ollama or anthropic, not '{provider}'")`.

### 2.4 `server/pyproject.toml` and `server/uv.lock`

Add `"anthropic>=2.6.0"` to `dependencies`. Run `uv lock` in `server/` and check the lock diff (D7). No extra (spec
section 9). No version bump (D8). After the lock, confirm with a non-frozen `uv run pytest` that the lock and
`pyproject.toml` agree. Confirm that `AnthropicClient` with the default model and `max_tokens` 16000 does not raise the
streaming guard (D17).

### 2.5 `README.md`

- Requirements: one item. With `NAVIGATOR_PROVIDER=anthropic`, the navigator needs Claude API credentials and no Ollama
  server.
- Configuration: add `NAVIGATOR_PROVIDER` and `ANTHROPIC_API_KEY`. Change the `NAVIGATOR_MODEL` default to show both
  defaults. Update the `NAVIGATOR_503_RETRIES` row (D17).
- Data sentence: with the `anthropic` provider, excerpts and tool results go to the Claude API.
- Tell users to export `ANTHROPIC_API_KEY` in the shell before starting `claude`. Do not suggest `mcp/navigator.json`.
- Lines 55-56 say the server reads `NAVIGATOR_MODEL` and `NAVIGATOR_OLLAMA_URL`. Add `NAVIGATOR_PROVIDER` there too.
- Note the SDK's default read timeout, and the `claude-opus-4-0` streaming limit when no `NAVIGATOR_JOB_TIMEOUT` is set
  (D17).

### 2.6 Tests

Shared fakes in `server/tests/fakes_anthropic.py`:
- `FakeAnthropic`: an `httpx2.MockTransport` handler. Scripted replies are Anthropic `Message` JSON, or `Status` items for
  non-2xx responses. It records each request body and each request's headers.

Fixtures in `server/tests/conftest.py` (D15): `_clean_env` (autouse), `isolated_home`, and `anthropic_env`.

| File | Covers |
| --- | --- |
| `tests/conftest.py` (edit) | D15 fixtures. |
| `tests/test_ollama_loop.py` (add) | Test 5, in two parts. See section 3, step 1. Expected bytes live in `tests/golden/`. |
| `tests/test_env_client.py` | Tests 1, 2, 3, 4, with `isolated_home`. Job failure for an unknown provider (D1). Job failure with no key (D5), without `anthropic_env`, with the base URL `http://127.0.0.1:9`. |
| `tests/test_anthropic_client.py` | See the table below. |

Test table for `test_anthropic_client.py`:

- Test 6: conversion and the merge of adjacent `user` and `tool` messages.
- Test 7: pair mode, two `read_file` calls. Two cases: (a) the first call survives and the second is bounced; (b) the
  first call is bounced and the second survives. In both, each `tool_result` gets the `tool_use_id` of its own
  `tool_use` block. Case (b) is what catches the `survivors.index` error in D11 (v4).
- Test 8: unknown image type; state restored.
- Test 9: reply conversion; usage metrics.
- Test 10: refusal and max_tokens; audit file written.
- Test 11: 529 retries, then `Busy503` with `anthropic busy (503): ...`. Use `Runtime(sleep=clock.virtual_sleep,
  max_503_retries=...)` with `FakeClock` from `tests/fakes.py`, as `test_jobs.py` test 26 does (v4). Real sleeps would take
  minutes.
- Test 12: 401; state restored.
- Test 13: headers and body. For `claude-opus-5-5`: the recorded wire body has `"fallbacks": "default"` and the recorded
  headers have `anthropic-beta: server-side-fallback-2026-07-01`. The audit bytes also have `fallbacks` (v4). For
  `claude-haiku-5-5`: no `fallbacks` in the wire body, no `anthropic-beta` header, and none in the audit bytes.
- Test 14: ask and submit through the full loop.
- D4 echo test: a fake reply holds a `fallback` block and a text block with `citations: null`. The next request's
  assistant content equals the reply's wire `content`.
- D16 test: an Ollama-format assistant message in state gives `TransportFailure`.
- D14 test: empty tool content does not crash conversion.
- D17 test: `AnthropicClient("claude-opus-5-5")` with `max_tokens` 16000 and no timeout does not raise.

Tests do not reach the Claude API. Every test that sends a request through the SDK requests `anthropic_env`.

## 3. Implementation order (subagents)

- **Step 1 (subagent A): seam, fixtures, and test 5.**
  1. Add the three fixtures of D15 to `conftest.py`. `_clean_env` is autouse. Do not set `ANTHROPIC_CONFIG_DIR`.
  2. Add test 5 to `test_ollama_loop.py`. Before any source edit, run a scripted scenario on unmodified code that reaches
     all three `navigator_call` sites, within the caps. The arithmetic (v4): the scenario is one ask with
     - an answer-tool bounce, which adds 1 to `state.bounces` (line 497);
     - a no-survivor reply with one unjustified call, which adds 1 (line 560);
     - a mixed reply with one bounced call and one justified call, which adds 1 (total 3, and `3 > BOUNCE_CAP` is false);
     - a `_submit` with a valid result for the justified call.
     Total bounces: 3. `_submit` does not reset `bounces`. Do not add a fourth bounced call, or the ask ends with
     "repeated bounces". Capture the canonical request bytes for each send, and store them as files under
     `tests/golden/` (for example `tests/golden/g5_*.json`). Do not paste them into the test. Confirm the test passes on
     unmodified code.
  3. Make the `ollama.py` edits in section 2.1.
  4. Gate: the 290 existing tests pass with no change, and the new test 5 passes with its golden bytes unchanged.
- **Step 2 (subagent B): Anthropic client.** Section 2.2, section 2.4, `tests/fakes_anthropic.py` and
  `tests/test_anthropic_client.py`. Depends on step 1. Gate: all tests pass, and the D17 check passes.
- **Step 3 (subagent C): job wiring, README, and env tests.** Section 2.3, section 2.5, and `tests/test_env_client.py`.
  Depends on step 2. Gate: the full suite passes.
- **Verification (me).** Run the full suite. Review `git diff --stat`. Check each spec section against the diff.

## 4. Out of scope

Streaming, Batches, Bedrock, Vertex AI, Foundry, Managed Agents, version bumps, committing and pushing, and the
`model_context_window_exceeded` mapping (D13).

## 5. Risks

- Live acceptance of the echoed blocks and of multi-round fallback behavior. Not testable offline.
- Missing-key and bad-profile errors come from the SDK, and their text is the SDK's. The job fails with whatever message the
  SDK gives.
- `httpx2` is transitive. Tests import it directly.
- `model_context_window_exceeded` is not mapped (D13).
- Opus 4 model IDs fail the SDK's streaming guard without a job timeout (D17).
