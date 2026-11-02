"""Fakes for the phase 3 loop tests (docs/phases/phase-3-ollama-loop.md, section 10).

FakeOllama is a scripted httpx MockTransport handler: it pops one canned reply per request, records the request bytes,
and returns the reply as a 200 JSON response. Status(...) returns a non-200 response; an Exception item is raised.
"""

import anyio
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
    """Scripted replies. hold() makes the next request wait until release(); cancel_seen records a cancelled send."""

    def __init__(self, script: list):
        self.script = list(script)
        self.requests: list[bytes] = []
        self.timeouts: list[dict | None] = []
        self.cancel_seen = False
        self._gate: anyio.Event | None = None
        self._holding = False

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def hold(self) -> None:
        self._gate = anyio.Event()
        self._holding = True

    def release(self) -> None:
        if self._gate is not None:
            self._gate.set()

    def script_status(self, code: int, n: int) -> None:
        busy = '{"error":"server busy, please try again.  maximum pending requests exceeded"}'
        self.script[:0] = [Status(code, busy) for _ in range(n)]

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request.content)
        self.timeouts.append(request.extensions.get("timeout"))
        if self._holding:
            self._holding = False
            try:
                await self._gate.wait()
            except BaseException as exc:
                if isinstance(exc, anyio.get_cancelled_exc_class()):
                    self.cancel_seen = True
                raise
        if not self.script:
            raise AssertionError("fake Ollama: no scripted reply left")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, Status):
            return httpx.Response(item.code, text=item.text)
        return httpx.Response(200, json=item)


class FakeClock:
    """Virtual time. sleep() waits for advance(); virtual_sleep() advances time itself (for backoff tests)."""

    def __init__(self):
        self.t = 0.0
        self.waiters: list[tuple[float, anyio.Event]] = []

    def now(self) -> float:
        return self.t

    async def sleep(self, dt: float) -> None:
        if dt <= 0:
            return
        event = anyio.Event()
        self.waiters.append((self.t + dt, event))
        await event.wait()

    def advance(self, dt: float) -> None:
        self.t += dt
        due = [e for due_at, e in self.waiters if due_at <= self.t]
        self.waiters = [(d, e) for d, e in self.waiters if d > self.t]
        for event in due:
            event.set()

    async def virtual_sleep(self, dt: float) -> None:
        self.advance(dt)


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
