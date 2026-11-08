"""Fakes for the phase 7 Anthropic provider tests (docs/phases/phase-7-anthropic-provider.md, section 10).

FakeAnthropic is a scripted httpx2 MockTransport handler for the SDK (plan D2): it pops one item per request. A dict is
sent as a 200 Message JSON; a Status is a non-2xx response with that body; an Exception is raised in the transport.
It records each request body (parsed JSON), each request's headers, its URL and the timeout extension. Nothing reaches the
Claude API: the transport answers every host.
"""

import json
from itertools import count

import httpx2

from fakes import Status, StubMode
from navigator_mcp.anthropic_client import AnthropicClient
from navigator_mcp.ollama import Runtime

OPUS = "claude-opus-5-5"
HAIKU = "claude-haiku-5-5"

_ids = count(1)


class FakeAnthropic:
    def __init__(self, script: list):
        self.script = list(script)
        self.bodies: list[dict] = []
        self.headers: list[httpx2.Headers] = []
        self.timeouts: list[dict | None] = []
        self.urls: list[str] = []

    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self.handle)

    def script_status(self, code: int, n: int, text: str | None = None) -> None:
        body = text if text is not None else error_body(code)
        self.script[:0] = [Status(code, body) for _ in range(n)]

    async def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.bodies.append(json.loads(request.content))
        self.headers.append(request.headers)
        self.timeouts.append(request.extensions.get("timeout"))
        self.urls.append(str(request.url))
        if not self.script:
            raise AssertionError("fake Anthropic: no scripted reply left")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, Status):
            return httpx2.Response(item.code, text=item.text, headers={"content-type": "application/json"})
        return httpx2.Response(200, json=item)


def client(fake: FakeAnthropic, model: str = OPUS) -> AnthropicClient:
    return AnthropicClient(model, transport=fake.transport())


def runtime(fake: FakeAnthropic, model: str = OPUS, mode=None, **kwargs) -> Runtime:
    return Runtime(client=client(fake, model), mode=mode or StubMode(), **kwargs)


# Message JSON builders (the wire format of a beta messages.create response)


def usage(input_tokens=10, output_tokens=5, cache_read=0, cache_creation=0) -> dict:
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_creation,
    }


def message(*blocks: dict, stop_reason="end_turn", stop_details=None, model=OPUS, usage_=None) -> dict:
    return {
        "id": f"msg_{next(_ids):04d}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": list(blocks),
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "stop_details": stop_details,
        "usage": usage_ or usage(),
    }


def text(t: str) -> dict:
    return {"type": "text", "text": t, "citations": None}


def thinking(t: str = "thinking it over", signature: str = "sig-abc") -> dict:
    return {"type": "thinking", "thinking": t, "signature": signature}


def tool_use(block_id: str, name: str, **arguments) -> dict:
    return {"type": "tool_use", "id": block_id, "name": name, "input": arguments}


def fallback(from_model=OPUS, to_model="claude-sonnet-5-5", category="cyber") -> dict:
    return {
        "type": "fallback",
        "from": {"model": from_model},
        "to": {"model": to_model},
        "trigger": {"type": "refusal", "category": category},
    }


def decide(block_id: str = "toolu_decide", **arguments) -> dict:
    return message(tool_use(block_id, "decide", **arguments), stop_reason="tool_use")


def error_body(code: int) -> str:
    kind = {401: "authentication_error", 429: "rate_limit_error", 503: "api_error", 529: "overloaded_error"}
    return json.dumps({"type": "error", "error": {"type": kind.get(code, "api_error"), "message": f"status {code}"}})
