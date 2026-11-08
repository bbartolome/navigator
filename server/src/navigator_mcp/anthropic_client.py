"""Claude API client for the navigator loop (phase 7: docs/phases/phase-7-anthropic-provider.md).

The loop keeps the Ollama message and reply formats. AnthropicClient converts the request in build_request (sections 5
and 6) and the reply in post (sections 7 and 8), so _loop does not know the Anthropic format.

The SDK uses httpx2, not httpx. This module never imports httpx2: it returns navigator's httpx objects to _post, and it
gives the SDK an anthropic.Timeout (plan D2, D3).
"""

import base64
import json
from typing import Any

import anthropic
import httpx

from .ollama import TransportFailure, _placeholder, canonical_json

FALLBACK_MODELS = frozenset({"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"})
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOKENS = 16000
RETRY_STATUSES = frozenset({429, 503, 529})  # _post retries these as 503 (section 8)

_SENT_FIELDS = ("model", "max_tokens", "system", "messages", "tools", "thinking", "output_config", "cache_control")
_PROVIDER_SWITCH = "the pending ask was made with a different provider"  # plan D16

# (prefix, offset, media type) from spec section 6.1. WEBP also needs "RIFF" at byte 0 (checked in _media_type).
_MAGIC = (
    (b"\x89PNG", 0, "image/png"),
    (b"\xff\xd8\xff", 0, "image/jpeg"),
    (b"GIF8", 0, "image/gif"),
)


class AnthropicClient:
    """messages.create on the beta resource, with SDK retries off: the navigator loop does the retries (section 8)."""

    name = "anthropic"

    def __init__(self, model: str, *, transport: Any = None):
        self.model = model
        kwargs: dict[str, Any] = {}
        if transport is not None:  # tests only: an httpx2 transport (plan D2)
            kwargs["http_client"] = anthropic.DefaultAsyncHttpxClient(transport=transport)
        self._sdk = anthropic.AsyncAnthropic(max_retries=0, **kwargs)

    def build_request(self, messages: list[dict], tools: list[dict], *, redact_images: bool = False) -> bytes:
        if not messages or messages[0].get("role") != "system":
            raise TransportFailure("the first message must be the system message")
        converted = _convert(messages)
        if redact_images:
            converted = [_redact(m) for m in converted]
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            "system": messages[0]["content"],
            "messages": converted,
            "tools": _convert_tools(tools),
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": "medium"},
            "cache_control": {"type": "ephemeral"},
        }
        if self.model in FALLBACK_MODELS:
            body["fallbacks"] = "default"
        return canonical_json(body)

    async def post(self, body: bytes, timeout: httpx.Timeout | None = None) -> httpx.Response:
        req = json.loads(body)
        kwargs: dict[str, Any] = {k: req[k] for k in _SENT_FIELDS}
        if "fallbacks" in req:
            kwargs["fallbacks"] = req["fallbacks"]
            kwargs["betas"] = [FALLBACK_BETA]
        if timeout is not None:  # the SDK rejects an httpx Timeout (plan D3)
            kwargs["timeout"] = anthropic.Timeout(
                connect=timeout.connect, read=timeout.read, write=timeout.write, pool=timeout.pool
            )
        try:
            msg = await self._sdk.beta.messages.create(**kwargs)
        except anthropic.APITimeoutError as exc:  # a subclass of APIConnectionError: catch it first
            raise httpx.ReadTimeout(str(exc)) from exc
        except anthropic.APIConnectionError as exc:
            raise httpx.ConnectError(str(exc)) from exc
        except anthropic.APIStatusError as exc:
            status = 503 if exc.status_code in RETRY_STATUSES else exc.status_code
            return httpx.Response(status, text=exc.response.text)
        return httpx.Response(200, json=_reply(msg))

    async def aclose(self) -> None:
        await self._sdk.close()


# Request conversion (sections 5 and 6)


def _convert_tools(tools: list[dict]) -> list[dict]:
    return [
        {
            "name": t["function"]["name"],
            "description": t["function"]["description"],
            "input_schema": t["function"]["parameters"],
        }
        for t in tools
    ]


def _convert(messages: list[dict]) -> list[dict]:
    """messages[1:] as Anthropic messages. Adjacent user and tool messages become one user message (section 6 rule 5)."""
    out: list[dict] = []
    results: list[dict] = []  # tool_result blocks of the current user/tool run
    others: list[dict] = []  # the other blocks of the run

    def flush() -> None:
        if results or others:
            out.append({"role": "user", "content": [*results, *others]})
            results.clear()
            others.clear()

    for i in range(1, len(messages)):
        m = messages[i]
        role = m.get("role")
        if role == "assistant":
            flush()
            if "navigator_anthropic_content" not in m:
                raise TransportFailure(_PROVIDER_SWITCH)
            out.append({"role": "assistant", "content": m["navigator_anthropic_content"]})
        elif role == "tool":
            results.append({"type": "tool_result", "tool_use_id": _tool_use_id(messages, i), "content": m["content"]})
        else:  # user
            for b64 in m.get("images") or []:
                others.append(
                    {"type": "image", "source": {"type": "base64", "media_type": _media_type(b64), "data": b64}}
                )
            others.append({"type": "text", "text": m["content"]})
    flush()
    return out


def _tool_use_id(messages: list[dict], i: int) -> str:
    """The id of tool_use block number navigator_call in the latest assistant message before messages[i] (plan D10)."""
    for j in range(i - 1, 0, -1):
        prev = messages[j]
        if prev.get("role") != "assistant":
            continue
        content = prev.get("navigator_anthropic_content")
        if content is None:
            raise TransportFailure(_PROVIDER_SWITCH)
        uses = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
        index = messages[i].get("navigator_call")
        if not isinstance(index, int) or not 0 <= index < len(uses):
            raise TransportFailure(f"tool message {i} has no matching tool_use block")
        return uses[index]["id"]
    raise TransportFailure(f"tool message {i} has no assistant message before it")


def _media_type(b64: str) -> str:
    raw = base64.b64decode(b64)
    for prefix, offset, media_type in _MAGIC:
        if raw[offset : offset + len(prefix)] == prefix:
            return media_type
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    raise TransportFailure("image type is not supported by the anthropic provider")


def _redact(message: dict) -> dict:
    content = message["content"]
    if message["role"] != "user" or not any(b.get("type") == "image" for b in content):
        return message
    blocks = [
        {**b, "source": {**b["source"], "data": _placeholder(b["source"]["data"])}} if b.get("type") == "image" else b
        for b in content
    ]
    return {**message, "content": blocks}


# Reply conversion (section 7)


def _reply(msg: Any) -> dict[str, Any]:
    """The Ollama reply body for a BetaMessage. Blocks are selected by their dumped "type" key, never by class."""
    echo = msg.model_dump(mode="json", by_alias=True, exclude_unset=True)["content"]  # the wire content (plan D4)
    dumped = msg.model_dump(mode="json", by_alias=True)  # for the audit file only
    text = "\n".join(b["text"] for b in echo if b.get("type") == "text")
    tool_calls = [
        {"function": {"name": b["name"], "arguments": b["input"]}} for b in echo if b.get("type") == "tool_use"
    ]
    message: dict[str, Any] = {"role": "assistant", "content": text, "navigator_anthropic_content": echo}
    if tool_calls:
        message["tool_calls"] = tool_calls
    usage = dumped.get("usage") or {}
    cache_read = usage.get("cache_read_input_tokens") or 0
    cache_creation = usage.get("cache_creation_input_tokens") or 0
    reply: dict[str, Any] = {
        "message": message,
        "prompt_eval_count": (usage.get("input_tokens") or 0) + cache_read + cache_creation,
        "prompt_eval_cached_count": cache_read,
        "navigator_anthropic_response": dumped,
    }
    stop_reason = dumped.get("stop_reason")
    if stop_reason == "refusal":
        category = (dumped.get("stop_details") or {}).get("category") or "none"
        reply["navigator_error"] = f"refused ({category})"
    elif stop_reason == "max_tokens":
        reply["navigator_error"] = "reply cut off at max_tokens"
    return reply
