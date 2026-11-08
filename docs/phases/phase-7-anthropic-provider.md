# Phase 7: optional Anthropic provider

This document is written in ASD-STE100 Simplified Technical English. File names, function names, variable names and
API field names are technical names. They are in code font.

## 1. Purpose

The navigator uses only Ollama at this time. This phase adds a second provider: the Claude API from Anthropic.

The new variable `NAVIGATOR_PROVIDER` selects the provider. The variable is optional. When it is not set, the
navigator operates as it does now.

When you use the Anthropic provider, the navigator sends the summary, excerpts, tool results and images to the
Claude API. When you use Ollama, this data stays on the Ollama server.

## 2. Scope

These files change:

- `server/src/navigator_mcp/anthropic_client.py`: new file. It contains `AnthropicClient` and its conversion
  functions. Do not name the file `anthropic.py`. That name hides the `anthropic` package.
- `server/src/navigator_mcp/ollama.py`: the `NavigatorClient` protocol, the type of `Runtime.client`, the
  `navigator_call` key on tool messages, the `navigator_error` check in `_loop`, the client name in the `Busy503`
  message, and the key filter in `OllamaClient.build_request`.
- `server/src/navigator_mcp/jobs.py`: `_env_client` and the type of `client_factory`.
- `server/pyproject.toml`: the `anthropic` dependency.
- `README.md`: the Requirements section and the Configuration table.
- `server/tests/`: new tests (section 10).

These items do not change:

- The mode protocol, the charter, the answer tools, the navigator tool schemas in `schemas.py`, and the session file
  format, except the new `navigator_call` key (section 6).
- `mcp/navigator.json`. Do not put an API key in this file.
- `scripts/launch.sh`. The launcher installs the base dependencies, and `anthropic` is a base dependency.
- Streaming, Batches, Bedrock, Vertex AI and Foundry. These are out of scope.

## 3. Configuration

The server reads these variables when a job starts its client. This is the same rule as for `NAVIGATOR_OLLAMA_URL`.
A change has an effect after the server starts again.

| Variable | Default | Purpose |
| --- | --- | --- |
| `NAVIGATOR_PROVIDER` | unset | `ollama` or `anthropic`. Unset or empty is the same as `ollama`. |
| `NAVIGATOR_MODEL` | `qwen-agent:latest` for `ollama`; `claude-opus-5-5` for `anthropic` | The navigator model. |
| `NAVIGATOR_OLLAMA_URL` | `http://host.docker.internal:11434` | Ollama endpoint. The `anthropic` provider does not read it. |
| `ANTHROPIC_API_KEY` | unset | The API key. Only the `anthropic` provider reads it. |

Rules for `NAVIGATOR_PROVIDER`:

1. Remove the spaces at the two ends of the value. Then change the value to lowercase.
2. If the value is empty or `ollama`, use `OllamaClient`. Use the same arguments and defaults as now.
3. If the value is `anthropic`, use `AnthropicClient`.
4. If the value is a different value, raise `ValueError`. Use this message:
   `NAVIGATOR_PROVIDER must be ollama or anthropic, not '<value>'`.

Do not use Ollama when the value is not correct. A typing error must not send data to the wrong provider.

The job layer calls `_env_client` inside the worker. Thus a `ValueError` makes the job fail. The task failure
message is the `ValueError` message.

The `anthropic` SDK finds the credentials. It reads `ANTHROPIC_API_KEY` first. It can also use other sources, for
example a profile from `ant auth login`. The navigator does not examine the key. If the SDK finds no credentials,
the job fails with the SDK message.

The SDK also reads `ANTHROPIC_BASE_URL`. The navigator does not add a variable for the base URL.

## 4. Client interface

Add this protocol to `ollama.py`:

```python
class NavigatorClient(Protocol):
    name: str  # "ollama" or "anthropic"
    model: str

    def build_request(self, messages: list[dict], tools: list[dict], *, redact_images: bool = False) -> bytes: ...

    async def post(self, body: bytes, timeout: httpx.Timeout | None = None) -> httpx.Response: ...

    async def aclose(self) -> None: ...
```

- `OllamaClient` gets `name = "ollama"`. It does not change in other ways, except the key filter in section 6.
- `Runtime.client` changes its type to `NavigatorClient`.
- `JobManager.__init__` changes the type of `client_factory` to `Callable[[], NavigatorClient] | None`.
- The `Busy503` message uses the client name: `f"{rt.client.name} busy (503): ..."`. For Ollama, the text is the
  same as now.

`state.messages` keeps the Ollama message format. `classify`, `_received`, `_metrics` and `_post` read the Ollama
reply format. `AnthropicClient` changes the request and the reply at its two ends. Thus `_loop` does not know the
Anthropic format.

## 5. The request from `AnthropicClient.build_request`

`build_request` makes a JSON body for `messages.create`. Use `canonical_json` to write it. The audit file
`request-*.json` gets these bytes.

The body has these fields:

| Field | Value |
| --- | --- |
| `model` | `self.model` |
| `max_tokens` | `16000` |
| `system` | The content of the first message, which has the role `system`. |
| `messages` | The converted messages (section 6). |
| `tools` | The converted tools (this section). |
| `thinking` | `{"type": "adaptive"}` |
| `output_config` | `{"effort": "high"}` |
| `cache_control` | `{"type": "ephemeral"}` |
| `fallbacks` | `"default"`. Only for the models in `FALLBACK_MODELS`. |

`FALLBACK_MODELS` is `claude-fable-5-1`, `claude-opus-5-5`, `claude-opus-5` and `claude-sonnet-5-5`. For a different
model, do not send `fallbacks`.

Do not send `tool_choice`. The API default is `auto`. The current models refuse `any` and `tool`.

Do not send `temperature`, `top_p` or `top_k`.

Convert each tool from the Ollama format to the Anthropic format:

| Ollama field | Anthropic field |
| --- | --- |
| `function.name` | `name` |
| `function.description` | `description` |
| `function.parameters` | `input_schema` |

Keep the order of the tools. Thus the tool list is the same for each send in an ask, and the prompt cache stays
correct.

If `redact_images` is true, replace the `data` of each `image` block with `_placeholder(data)`.

## 6. Message conversion

Convert `state.messages` to Anthropic messages with these rules. Apply the rules in the order of the messages.

1. Do not convert the first message. It has the role `system` and goes into the `system` field.
2. A `user` message becomes `user` content blocks. Put one `image` block for each entry in `images` first. Then put
   one `text` block with `content`.
3. An `assistant` message has the key `navigator_anthropic_content`. Use this value as the content, with no change.
   It contains the `thinking`, `text`, `tool_use` and `fallback` blocks of the reply.
4. A `tool` message becomes a `tool_result` block. Its `content` is the message `content`.
5. Put adjacent `user` and `tool` messages into one `user` message. Put the `tool_result` blocks first, in their
   order. Then put the other blocks, in their order.

### 6.1 Image type

Ollama images are base64 data with no media type. Find the media type from the first bytes of the decoded data:

| First bytes | `media_type` |
| --- | --- |
| `89 50 4E 47` | `image/png` |
| `FF D8 FF` | `image/jpeg` |
| `47 49 46 38` | `image/gif` |
| `52 49 46 46`, and `57 45 42 50` at byte 8 | `image/webp` |

If the type is not in this table, raise `TransportFailure`. Use this message:
`image type is not supported by the anthropic provider`. `ask` and `submit` then restore the state from before the
send and return an error.

### 6.2 Tool result identifiers

Each `tool_result` block must have the `tool_use_id` of its `tool_use` block. Ollama tool messages have no
identifier. They have only `tool_name`.

The tool messages are not always in the order of the calls. `_submit` adds the bounce messages first, then the
results. If one reply has two calls with the same name, the name does not identify the call.

Thus add the key `navigator_call` to each tool message. The value is the index of the call in its reply, from 0.
Add the key at these three locations in `ollama.py`:

- The bounce of an answer tool call: the index of `c` in `reply.calls`.
- The bounce when no call survives: the index of `p` in `pending`.
- `_submit`, for the bounce messages and for the results: the index of `p` in `pending`.

`pending` has the same order as `reply.calls`. Thus the index is the same in the two lists.

`AnthropicClient` finds the latest `assistant` message before the tool message. It takes the `tool_use` block at the
index `navigator_call` in that message. It uses the `id` of that block as the `tool_use_id`.

`OllamaClient.build_request` must remove each message key that starts with `navigator_`. Thus the request bytes to
Ollama are the same as now. The session file gets the new key.

### 6.3 History rules

The Claude API connects each `thinking` block to its model and its conversation. Do not change or remove an earlier
message in an ask. `_loop` adds messages only at the end, and `_retry_messages` starts a new conversation. Thus the
current loop obeys this rule.

Make the conversion deterministic. The same `state.messages` must always give the same bytes.

## 7. The reply from `AnthropicClient.post`

`post` does these steps:

1. Parse `body`.
2. Call `client.beta.messages.create` on an `anthropic.AsyncAnthropic` client. Send the parsed fields.
3. If the body has `fallbacks`, also send `betas=["server-side-fallback-2026-07-01"]`.
4. Send `timeout`, if it is not `None`.
5. Make the SDK client with `max_retries=0`. The navigator loop does the retries (section 8).
6. Convert the response to an Ollama reply body.
7. Return `httpx.Response(200, json=<reply body>)`.

The reply body has these fields:

| Field | Value |
| --- | --- |
| `message.role` | `"assistant"` |
| `message.content` | The `text` blocks, joined with `"\n"`. |
| `message.tool_calls` | One `{"function": {"name": ..., "arguments": ...}}` for each `tool_use` block, in order. `arguments` is the block `input`. Do not include the field if there are no `tool_use` blocks. |
| `message.navigator_anthropic_content` | `response.content`, from `model_dump(mode="json")`. |
| `prompt_eval_count` | `input_tokens + cache_read_input_tokens + cache_creation_input_tokens` |
| `prompt_eval_cached_count` | `cache_read_input_tokens` |
| `prompt_eval_duration` | Do not include. |
| `navigator_error` | Only for the stop reasons in the next table. |
| `navigator_anthropic_response` | The full response, from `model_dump(mode="json")`. It is for the audit file only. |

`classify` reads `message.content` and `message.tool_calls`, as for Ollama. `_record_metrics` adds the counts.

| `stop_reason` | `navigator_error` |
| --- | --- |
| `refusal` | `refused (<category>)`. `<category>` is `stop_details.category`, or `none`. |
| `max_tokens` | `reply cut off at max_tokens` |

Add this check to `_loop`, after `_post` and before `classify`:

```python
if isinstance(obj, dict) and isinstance(obj.get("navigator_error"), str):
    return _settle(state, Error(status="error", reason=obj["navigator_error"], stage=current_stage))
```

Ollama never sets `navigator_error`. Thus this check has no effect on Ollama.

## 8. Errors

`_post` does not change. `AnthropicClient.post` changes each SDK error into a form that `_post` knows:

| SDK error | `post` does this | `_post` result |
| --- | --- | --- |
| `anthropic.APIStatusError` with status 429, 503 or 529 | Return `httpx.Response(503, text=<error body>)` | Retry with `BACKOFF`, up to `NAVIGATOR_503_RETRIES`. Then `Busy503`. |
| `anthropic.APIStatusError` with a different status | Return `httpx.Response(<status>, text=<error body>)` | `TransportFailure("HTTP <status>: ...")` |
| `anthropic.APITimeoutError` | Raise `httpx.ReadTimeout(str(exc))` | `TransportFailure` |
| `anthropic.APIConnectionError` | Raise `httpx.ConnectError(str(exc))` | `TransportFailure` |

Catch `APITimeoutError` before `APIConnectionError`. `APITimeoutError` is a subclass of `APIConnectionError`.

## 9. Dependency

Add `anthropic` to `dependencies` in `server/pyproject.toml`. Use the current release as the lower bound.

Do not make it an optional extra. `scripts/launch.sh` installs only the base dependencies. Thus plugin users do not
get optional extras.

The change to `pyproject.toml` makes the launcher install the server again, one time.

## 10. Tests

All current tests must pass with no change.

Add these tests:

1. `_env_client` with `NAVIGATOR_PROVIDER` unset, empty, `ollama` and ` Ollama ` returns `OllamaClient`. Its URL and
   model are the same as now.
2. `_env_client` with `anthropic` returns `AnthropicClient` with the model `claude-opus-5-5`.
3. `_env_client` with `anthropic` and `NAVIGATOR_MODEL=claude-sonnet-5-5` uses `claude-sonnet-5-5`.
4. `_env_client` with `openai` raises `ValueError` with the message in section 3. A job with this value fails with
   the same message.
5. The Ollama request bytes do not contain `navigator_call`. They are the same as before this phase.
6. Message conversion: the system message, a user message with a PNG image and a JPEG image, an assistant message,
   and the merge of adjacent `user` and `tool` messages.
7. Tool result identifiers: one reply has two `read_file` calls. The second call is bounced and the first call
   survives. Each `tool_result` gets the correct `tool_use_id`.
8. An image with an unknown type gives a `TransportFailure`. The state is restored.
9. Reply conversion: text only, `tool_use` only, and text with `tool_use`. The usage counts go to the metrics.
10. `stop_reason` `refusal` and `max_tokens` give an `Error` with the reason in section 7. The audit reply file is
    written.
11. Status 529 retries and then gives `Busy503`, with the message `anthropic busy (503): ...`.
12. Status 401 gives a `TransportFailure`. The state is restored.
13. `fallbacks` and the beta value are in the request for `claude-opus-5-5`. They are not in the request for
    `claude-haiku-5-5`.
14. An ask and a submit through the full loop, with a fake HTTP transport. Give the transport to the SDK with
    `AsyncAnthropic(api_key="test", base_url="http://anthropic.test", http_client=httpx.AsyncClient(transport=...))`.

The tests must not send requests to the Claude API.

## 11. README

- Requirements: add one item. With `NAVIGATOR_PROVIDER=anthropic`, the navigator needs Claude API credentials and
  not an Ollama server.
- Configuration: add `NAVIGATOR_PROVIDER` and `ANTHROPIC_API_KEY`. Change the `NAVIGATOR_MODEL` default to show the
  two defaults.
- Add one sentence about data: with the `anthropic` provider, the navigator sends excerpts and tool results to the
  Claude API.
- Tell users to export `ANTHROPIC_API_KEY` in the shell before they start `claude`. Do not tell them to put it in
  `mcp/navigator.json`.

## 12. Open questions

1. Is `high` the correct effort for the navigator? An option is a new variable, `NAVIGATOR_EFFORT`.
2. Is `16000` enough for `max_tokens`? A larger value can make it necessary to use streaming.
3. Must `NAVIGATOR_MODEL` be shared by the two providers? If a user sets it for Ollama and then selects `anthropic`,
   the Claude API returns 404. An option is a new variable, `NAVIGATOR_ANTHROPIC_MODEL`.
4. Must the `anthropic` provider get its own retry count? It now uses `NAVIGATOR_503_RETRIES` for 429, 503 and 529.
