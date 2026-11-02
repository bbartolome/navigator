"""Ollama client and the ask/submit state machine (phase 3; async per phase 3b section 1).

Rules: docs/phases/phase-3-ollama-loop.md. Decisions recorded 2026-11-02 apply: call ids are call-NNNN, request-N uses
ask_n, and a fourth bounced call ends the ask.
"""

import base64
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field

from . import session as S
from .render import render_summary
from .schemas import NAVIGATOR_TOOLS

IMAGE_LIMIT = 1_000_000  # bytes, decoded (section 7)
ROUND_CAP = 12  # sends per ask (section 4)
BOUNCE_CAP = 3  # bounced calls allowed per ask; the next one ends the ask (decided 2026-11-02)
CONTEXT_LINES = 5  # retry trim: lines kept either side of an excerpt's range (section 6)
CARRY_FORWARD_MAX = 5
MONOTONIC = ("ask_n", "rounds")  # never restored after a failed send: audit file names derive from them


class TransportFailure(Exception):
    """A non-2xx status or an httpx error. The loop returns Error and restores the pre-send state."""


class ImageError(Exception):
    """An image is missing, unreadable or over the size limit. Raised before any state change."""


def canonical_json(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


# Outcome models (section 9)


class Excerpt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    start: int = Field(ge=1)
    end: int = Field(ge=1)
    text: str
    first_line: int | None = Field(default=None, ge=1)  # source line of text's first line; defaults to start


class Call(BaseModel):
    call_id: str
    name: str
    args: dict[str, Any]
    justification: str


class ToolCalls(BaseModel):
    status: Literal["tool_calls"]
    calls: list[Call]


class NeedInfo(BaseModel):
    status: Literal["need_info"]
    questions: list[str]


class Answered(BaseModel):
    status: Literal["answered"]
    tool: str
    result: dict[str, Any]
    carry_forward: list[str]


class Error(BaseModel):
    status: Literal["error"]
    reason: str


Outcome = Annotated[ToolCalls | NeedInfo | Answered | Error, Field(discriminator="status")]


class AskArgs(BaseModel):
    question: str
    answer_tool: str
    excerpts: list[Excerpt] = []
    images: list[str] = []  # image paths


class Result(BaseModel):
    call_id: str
    status: Literal["ran", "denied"]
    content: str
    image_path: str | None = None


class SubmitArgs(BaseModel):
    results: list[Result]


class Metrics(BaseModel):
    prompt_eval_count: int | None = None
    prompt_eval_cached_count: int | None = None  # None when the response omits it (phase 3 open question Q2)
    prompt_eval_duration: int | None = None  # nanoseconds


# Mode protocol and runtime (phase 3 section 1)


class Mode(Protocol):
    name: str
    charter: str
    answer_tools: dict[str, dict]

    def missing_fields(self, tool: str, args: dict) -> list[str]: ...

    def carry_forward(self, args: dict) -> list[str]: ...


class OllamaClient:
    """POST /api/chat. The timeout is None by default (main doc section 8); the job layer passes a deadline later."""

    def __init__(self, base_url: str, model: str, *, transport: httpx.AsyncBaseTransport | None = None):
        self.model = model
        self._http = httpx.AsyncClient(
            base_url=base_url, timeout=httpx.Timeout(None), transport=transport
        )

    @property
    def timeout(self) -> httpx.Timeout:
        return self._http.timeout

    def build_request(self, messages: list[dict], tools: list[dict], *, redact_images: bool = False) -> bytes:
        msgs = [_redact(m) for m in messages] if redact_images else messages
        return canonical_json({"messages": msgs, "model": self.model, "stream": False, "tools": tools})

    async def post(self, body: bytes) -> httpx.Response:
        return await self._http.post("/api/chat", content=body, headers={"Content-Type": "application/json"})

    async def aclose(self) -> None:
        await self._http.aclose()


@dataclass
class Runtime:
    client: OllamaClient
    mode: Mode


def _placeholder(b64: str) -> str:
    raw = base64.b64decode(b64)
    return f"{hashlib.sha256(raw).hexdigest()}:{len(raw)}"


def _redact(message: dict) -> dict:
    if not message.get("images"):
        return message
    return {**message, "images": [_placeholder(b) for b in message["images"]]}


# Reply classification (section 3)


@dataclass
class RawCall:
    name: str
    arguments: dict[str, Any]


@dataclass
class Reply:
    kind: Literal["malformed", "calls", "empty", "plain"]
    reason: str = ""
    message: dict[str, Any] | None = None
    calls: list[RawCall] = field(default_factory=list)
    text: str = ""


def classify(body: Any, tool_names: set[str]) -> Reply:
    if not isinstance(body, dict):
        return Reply("malformed", "the reply is not a JSON object")
    message = body.get("message")
    if not isinstance(message, dict):
        return Reply("malformed", "the reply has no message")
    raw_calls = message.get("tool_calls") or []
    if not isinstance(raw_calls, list):
        return Reply("malformed", "tool_calls is not a list")
    calls: list[RawCall] = []
    for i, item in enumerate(raw_calls):
        fn = item.get("function") if isinstance(item, dict) else None
        if not isinstance(fn, dict) or not isinstance(fn.get("name"), str):
            return Reply("malformed", f"tool_calls[{i}] has no function name")
        if not isinstance(fn.get("arguments"), dict):
            return Reply("malformed", f"tool_calls[{i}].function.arguments is not an object")
        if fn["name"] not in tool_names:
            return Reply("malformed", f"{fn['name']} is not an offered tool")
        args = fn["arguments"]
        if fn["name"] == "need_info":
            questions = args.get("questions")
            if not (isinstance(questions, list) and all(isinstance(q, str) for q in questions)):
                return Reply("malformed", "need_info.questions must be a list of strings")
        calls.append(RawCall(fn["name"], args))
    if calls:
        return Reply("calls", message=message, calls=calls)
    content = message.get("content") or ""
    if not isinstance(content, str):
        return Reply("malformed", "content is not a string")
    if content.strip() == "":
        return Reply("empty", message=message)
    return Reply("plain", message=message, text=content)


# Helpers


def _err(reason: str) -> Error:
    return Error(status="error", reason=reason)


def _received(message: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(message))  # exactly as received, detached from the reply object


def _one_line(value: Any) -> str:
    text = canonical_json(value).decode("utf-8") if isinstance(value, dict) else str(value)
    return " ".join(text.split())[:200]


def _with_suffix(question: str, tool: str) -> str:
    line = f"Answer by calling {tool}."
    stripped = question.rstrip()
    if stripped.split("\n")[-1].strip() == line:
        return stripped
    return stripped + "\n" + line


def _user_content(summary: str, excerpts: list[dict], question: str) -> str:
    parts = [summary]
    if excerpts:
        blocks = [f"--- {e['path']}:{e['start']}-{e['end']} ---\n{e['text']}" for e in excerpts]
        parts.append("EXCERPTS:\n" + "\n".join(blocks))
    parts.append(question)
    return "\n\n".join(parts)


def _user_message(summary: str, excerpts: list[dict], question: str, images: list[str]) -> dict[str, Any]:
    msg: dict[str, Any] = {"role": "user", "content": _user_content(summary, excerpts, question)}
    if images:
        msg["images"] = images
    return msg


def _trim_excerpts(excerpts: list[dict]) -> list[dict]:
    """Keep the needed lines plus CONTEXT_LINES on each side. Line i of text is source line first_line + i."""
    out = []
    for e in excerpts:
        first = e.get("first_line") or e["start"]
        lines = e["text"].split("\n")
        lo = max(0, e["start"] - CONTEXT_LINES - first)
        hi = min(len(lines) - 1, e["end"] + CONTEXT_LINES - first)
        out.append({**e, "text": "\n".join(lines[lo : hi + 1])})
    return out


def _encode_image(path: str) -> str:
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise ImageError(f"cannot read image {path}: {exc.strerror or exc}") from None
    if len(data) > IMAGE_LIMIT:
        raise ImageError(f"image {path} is {len(data)} bytes; the limit is {IMAGE_LIMIT}")
    return base64.b64encode(data).decode("ascii")


def _metrics(body: Any) -> Metrics:
    if not isinstance(body, dict):
        return Metrics()
    return Metrics(
        prompt_eval_count=body.get("prompt_eval_count"),
        prompt_eval_cached_count=body.get("prompt_eval_cached_count"),
        prompt_eval_duration=body.get("prompt_eval_duration"),
    )


def _record_metrics(state: S.SessionState, m: Metrics) -> None:
    state.metrics.sends += 1
    if m.prompt_eval_count is not None:
        state.metrics.prompt_eval_count += m.prompt_eval_count
    if m.prompt_eval_cached_count is not None:
        state.metrics.prompt_eval_cached_count += m.prompt_eval_cached_count
    if m.prompt_eval_duration is not None:
        state.metrics.prompt_eval_duration += m.prompt_eval_duration


def _stem(state: S.SessionState, retry: int | None) -> str:
    if retry is not None:
        return f"request-{state.ask_n}-retry{retry}"
    if state.rounds <= 1:
        return f"request-{state.ask_n}"
    return f"request-{state.ask_n}-r{state.rounds - 1}"


def _settle(state: S.SessionState, outcome: Outcome) -> Outcome:
    if isinstance(outcome, ToolCalls):
        state.phase = "AWAITING_SUBMIT"
    else:
        state.phase = "IDLE"
        state.pending = None
    return outcome


def _restore(state: S.SessionState, snapshot: S.SessionState) -> None:
    keep = {name: state.__dict__[name] for name in MONOTONIC}
    state.__dict__.update(snapshot.__dict__)
    state.__dict__.update(keep)


# Sending (sections 3 and 8)


async def _post(state: S.SessionState, rt: Runtime, body: bytes, redacted: bytes, stem: str) -> tuple[Any, Metrics]:
    directory = S.session_dir(state.session_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{stem}.json").write_bytes(redacted)  # written before the send
    reply_stem = "reply" + stem[len("request") :]
    try:
        resp = await rt.client.post(body)
    except httpx.HTTPError as exc:
        reason = f"{type(exc).__name__}: {exc}"
        (directory / f"{reply_stem}-error.txt").write_text(reason)
        raise TransportFailure(reason) from exc
    if resp.status_code // 100 != 2:
        reason = f"HTTP {resp.status_code}: {resp.text[:500]}"
        (directory / f"{reply_stem}-error.txt").write_text(reason)
        raise TransportFailure(reason)
    try:
        obj: Any = resp.json()
    except ValueError:
        obj = None
    metrics = _metrics(obj)
    _record_metrics(state, metrics)
    audit = obj if isinstance(obj, dict) else {"navigator_raw_text": resp.text}
    audit = {**audit, "navigator_metrics": metrics.model_dump()}
    (directory / f"{reply_stem}.json").write_bytes(canonical_json(audit))  # written after the response
    return obj, metrics


async def _loop(state: S.SessionState, rt: Runtime) -> Outcome:
    """The send loop of section 4. Runs until the ask has an Outcome."""
    parts = state.ask_parts or {}
    answer_tool = parts["answer_tool"]
    tools = [*NAVIGATOR_TOOLS, rt.mode.answer_tools[answer_tool]]
    tool_names = {t["function"]["name"] for t in tools}
    empties: list[tuple[int, int | None]] = []  # (request bytes, prompt_eval_count) for each empty reply
    retry: int | None = None

    while True:
        if state.rounds >= ROUND_CAP:
            return _settle(state, _err("round limit"))
        state.rounds += 1
        body = rt.client.build_request(state.messages, tools)
        redacted = rt.client.build_request(state.messages, tools, redact_images=True)
        obj, metrics = await _post(state, rt, body, redacted, _stem(state, retry))
        retry = None
        reply = classify(obj, tool_names)

        if reply.kind == "malformed":
            if not state.resent:
                state.resent = True
                state.messages.append(
                    {"role": "user", "content": f"Your reply was malformed: {reply.reason}. Call a tool with valid arguments."}
                )
                continue
            return _settle(state, _err(f"malformed reply twice: {reply.reason}"))

        if reply.kind == "plain":
            if not state.resent:
                state.resent = True
                state.messages.append(_received(reply.message or {}))
                state.messages.append(
                    {"role": "user", "content": f"Call {answer_tool} with that answer; don't reply in text."}
                )
                continue
            return _settle(state, _err("plain-text reply twice"))

        if reply.kind == "empty":
            empties.append((len(body), metrics.prompt_eval_count))
            if not state.retried:
                state.retried = True
                state.messages = _retry_messages(state)
                retry = 1
                continue
            (first_bytes, first_pec), (last_bytes, last_pec) = empties[0], empties[-1]
            return _settle(
                state,
                _err(
                    "empty reply twice; request sizes tried: "
                    f"{first_bytes} bytes and {last_bytes} bytes; prompt_eval_count {first_pec} and {last_pec}"
                ),
            )

        # reply.kind == "calls"
        assert reply.message is not None
        message = reply.message
        answer_calls = [c for c in reply.calls if c.name == answer_tool]
        if answer_calls:
            ans = answer_calls[0]
            missing = rt.mode.missing_fields(ans.name, ans.arguments)
            carry: list[str] = []
            if not missing:
                carry = rt.mode.carry_forward(ans.arguments)
                if len(carry) > CARRY_FORWARD_MAX:
                    missing = ["carry_forward (at most 5 lines)"]
            if not missing:
                S.set_carry_forward(state, carry)
                return _settle(
                    state,
                    Answered(status="answered", tool=ans.name, result=ans.arguments, carry_forward=carry),
                )
            # Missing fields go back as a bounce; the other calls in the reply are ignored.
            state.bounces += 1
            state.messages.append(_received(message))
            for c in reply.calls:
                if c is ans:
                    text = f"{ans.name}: missing or invalid fields: {', '.join(missing)}. Call {ans.name} again with them."
                else:
                    text = "ignored: the answer tool was called in the same reply"
                state.messages.append({"role": "tool", "tool_name": c.name, "content": text})
            if state.bounces > BOUNCE_CAP:
                return _settle(state, _err("repeated bounces"))
            continue

        ni = [c for c in reply.calls if c.name == "need_info"]
        if ni:
            return _settle(state, NeedInfo(status="need_info", questions=ni[0].arguments["questions"]))

        state.messages.append(_received(message))
        pending: list[S.PendingCall] = []
        for c in reply.calls:
            call_id = S.alloc_call_id(state)
            args = {k: v for k, v in c.arguments.items() if k != "justification"}
            key = canonical_json(args).decode("utf-8")
            justification = c.arguments.get("justification")
            bounce: str | None = None
            rule = ""
            given = ""
            if not isinstance(justification, str) or not justification.strip():
                bounce = (
                    f"{c.name}: justification is required. State what you expect to find and which question it "
                    "settles, then call again."
                )
                rule = "justification is required"
            else:
                given = justification
                earlier = _earlier_run(state, c.name, key, justification)
                if earlier is not None:
                    bounce = (
                        f"{c.name} with these arguments already ran as {earlier}; its result is above. Call again "
                        "only if the justification names what changed, or answer."
                    )
                    rule = f"repeat of {earlier}"
            if bounce is not None:
                S.log_action(
                    state,
                    S.ActionLogEntry(
                        call_id=call_id,
                        name=c.name,
                        args_summary=_one_line(args),
                        status="bounced",
                        result_summary=f"{c.name} bounced: {rule}",
                        args_key=key,
                        justification=given,
                    ),
                )
            pending.append(
                S.PendingCall(
                    call_id=call_id, name=c.name, args=args, args_key=key, justification=given, bounce=bounce
                )
            )

        bounced = [p for p in pending if p.bounce is not None]
        survivors = [p for p in pending if p.bounce is None]
        state.bounces += len(bounced)
        if state.bounces > BOUNCE_CAP:
            return _settle(state, _err("repeated bounces"))
        if not survivors:
            for p in pending:
                state.messages.append({"role": "tool", "tool_name": p.name, "content": p.bounce})
            continue
        state.pending = pending
        return _settle(
            state,
            ToolCalls(
                status="tool_calls",
                calls=[Call(call_id=p.call_id, name=p.name, args=p.args, justification=p.justification) for p in survivors],
            ),
        )


def _earlier_run(state: S.SessionState, name: str, key: str, justification: str) -> str | None:
    """Bounce rule 2: an earlier call with the same name and args whose justification matches after strip().lower()."""
    norm = justification.strip().lower()
    for e in state.action_log:
        if e.name == name and e.args_key == key and e.status in ("ran", "denied"):
            if e.justification.strip().lower() == norm:
                return e.call_id
    return None


def _retry_messages(state: S.SessionState) -> list[dict[str, Any]]:
    parts = state.ask_parts or {}
    first_user = state.messages[1]
    rebuilt = _user_message(
        parts["summary"],
        _trim_excerpts(parts["excerpts"]),
        parts["question"],
        list(first_user.get("images", [])),
    )
    return [state.messages[0], rebuilt]


# Entry points (section 4)


async def ask(state: S.SessionState, args: AskArgs, rt: Runtime) -> Outcome:
    snapshot = state.model_copy(deep=True)
    try:
        return await _ask(state, args, rt)
    except TransportFailure as exc:
        _restore(state, snapshot)
        return _err(str(exc))
    except ImageError as exc:
        return _err(str(exc))


async def _ask(state: S.SessionState, args: AskArgs, rt: Runtime) -> Outcome:
    if state.ended:
        return _err("session ended")
    if state.phase == "THINKING":
        return _err("busy")
    if state.phase == "AWAITING_SUBMIT":
        return _err("session awaiting submit")
    if args.answer_tool not in rt.mode.answer_tools:
        return _err(f"answer_tool {args.answer_tool!r} is not offered in {rt.mode.name} mode")
    images = [_encode_image(p) for p in args.images]  # may raise ImageError before any change
    question = _with_suffix(args.question, args.answer_tool)
    excerpts = [e.model_dump() for e in args.excerpts]
    summary = render_summary(state)

    state.ask_n += 1
    state.rounds = 0
    state.pending = None
    state.bounces = 0
    state.resent = False
    state.retried = False
    state.ask_parts = {"summary": summary, "excerpts": excerpts, "question": question, "answer_tool": args.answer_tool}
    state.messages = [{"role": "system", "content": rt.mode.charter}, _user_message(summary, excerpts, question, images)]
    state.phase = "THINKING"
    return await _loop(state, rt)


async def submit(state: S.SessionState, args: SubmitArgs, rt: Runtime) -> Outcome:
    snapshot = state.model_copy(deep=True)
    try:
        return await _submit(state, args, rt)
    except TransportFailure as exc:
        _restore(state, snapshot)
        return _err(str(exc))
    except ImageError as exc:
        return _err(str(exc))


async def _submit(state: S.SessionState, args: SubmitArgs, rt: Runtime) -> Outcome:
    if state.ended:
        return _err("session ended")
    if state.phase == "THINKING":
        return _err("busy")
    if state.phase != "AWAITING_SUBMIT" or state.pending is None:
        return _err("session not awaiting submit")

    pending = state.pending
    survivors = [p for p in pending if p.bounce is None]
    expected = {p.call_id for p in survivors}
    given = [r.call_id for r in args.results]
    missing = sorted(expected - set(given))
    extra = sorted(set(given) - expected)
    duplicated = sorted({c for c in given if given.count(c) > 1})
    if missing or extra or duplicated:
        return _err(
            f"results must match the outstanding calls: missing {missing}, unexpected {extra}, duplicated {duplicated}"
        )
    by_id = {r.call_id: r for r in args.results}
    images = {r.call_id: _encode_image(r.image_path) for r in args.results if r.image_path}  # may raise ImageError

    for p in pending:  # bounce messages come before the real results, in call order
        if p.bounce is not None:
            state.messages.append({"role": "tool", "tool_name": p.name, "content": p.bounce})
    for p in survivors:
        r = by_id[p.call_id]
        content = r.content if r.status == "ran" else f"DENIED: {r.content}"
        state.messages.append({"role": "tool", "tool_name": p.name, "content": content})
    for p in survivors:
        if p.call_id in images:
            state.messages.append({"role": "user", "content": f"{p.call_id} image", "images": [images[p.call_id]]})
    for p in survivors:
        _record_result(state, p, by_id[p.call_id])

    state.pending = None
    state.phase = "THINKING"
    return await _loop(state, rt)


def _record_result(state: S.SessionState, p: S.PendingCall, r: Result) -> None:
    S.log_action(
        state,
        S.ActionLogEntry(
            call_id=p.call_id,
            name=p.name,
            args_summary=_one_line(p.args),
            status=r.status,
            result_summary=_one_line(r.content),
            args_key=p.args_key,
            justification=p.justification,
        ),
    )
    if r.status != "ran":
        return
    if p.name == "read_file" and isinstance(p.args.get("start"), int) and isinstance(p.args.get("end"), int):
        S.add_file_read(
            state,
            S.FileRead(
                path=str(p.args.get("path", "")),
                start=p.args["start"],
                end=p.args["end"],
                what=p.justification or "read",
            ),
        )
    if p.name == "screenshot" and r.image_path:
        S.add_shot(state, str(p.args.get("target", "")), p.justification or "screenshot")
