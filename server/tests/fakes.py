"""Fakes for the phase 3 loop tests (docs/phases/phase-3-ollama-loop.md, section 10).

FakeOllama is a scripted httpx MockTransport handler: it pops one canned reply per request, records the request bytes,
and returns the reply as a 200 JSON response. Status(...) returns a non-200 response; an Exception item is raised.
"""

import httpx

from navigator_mcp.ollama import OllamaClient, Runtime

CHARTER = "CHARTER"

DECIDE_TOOL = {
    "type": "function",
    "function": {
        "name": "decide",
        "description": "Answer the question.",
        "parameters": {
            "type": "object",
            "properties": {
                "decision": {"type": "string"},
                "carry_forward": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["decision", "carry_forward"],
        },
    },
}


class StubMode:
    """Phase 3 stub Mode: charter CHARTER and one answer tool, decide, requiring decision and carry_forward."""

    name = "pair"
    charter = CHARTER
    answer_tools = {"decide": DECIDE_TOOL}

    def missing_fields(self, tool: str, args: dict) -> list[str]:
        return [f for f in ("decision", "carry_forward") if f not in args]

    def carry_forward(self, args: dict) -> list[str]:
        return list(args["carry_forward"])


class Status:
    def __init__(self, code: int, text: str = ""):
        self.code = code
        self.text = text


class FakeOllama:
    def __init__(self, script: list):
        self.script = list(script)
        self.requests: list[bytes] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request.content)
        if not self.script:
            raise AssertionError("fake Ollama: no scripted reply left")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, Status):
            return httpx.Response(item.code, text=item.text)
        return httpx.Response(200, json=item)


def runtime(fake: FakeOllama, mode=None) -> Runtime:
    client = OllamaClient("http://ollama.test", "qwen-agent:latest", transport=fake.transport())
    return Runtime(client=client, mode=mode or StubMode())


def _message(pairs: list[tuple[str, dict]]) -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": n, "arguments": a}} for n, a in pairs],
    }


def call(name: str, **args) -> dict:
    return {"message": _message([(name, args)]), "done": True}


def calls(*pairs: tuple[str, dict]) -> dict:
    return {"message": _message(list(pairs)), "done": True}


def decide(**args) -> dict:
    return call("decide", **args)


def plain(text: str) -> dict:
    return {"message": {"role": "assistant", "content": text}, "done": True}


def empty() -> dict:
    return {"message": {"role": "assistant", "content": ""}, "done": True}
