"""Phase 3 tests: Ollama client and ask/submit loop (docs/phases/phase-3-ollama-loop.md, section 10).

Numbering follows the phase doc table. Decisions recorded 2026-11-02 apply: call ids are call-NNNN, request-N uses
ask_n, and a fourth bounced call ends the ask.
"""

import base64
import json
import re
from pathlib import Path

import httpx
import pytest

from fakes import Status, decide, call, calls, empty, FakeOllama, plain, runtime
from navigator_mcp import session as S
from navigator_mcp.ollama import (
    BOUNCE_CAP,
    Answered,
    AskArgs,
    Error,
    Excerpt,
    NeedInfo,
    Result,
    SubmitArgs,
    ToolCalls,
    ask,
    canonical_json,
    submit,
)

pytestmark = pytest.mark.anyio

GOLDEN = Path(__file__).parent / "golden"
QUESTION = "Which module owns retries?"


def _ask(question=QUESTION, tool="decide", excerpts=(), images=()) -> AskArgs:
    return AskArgs(question=question, answer_tool=tool, excerpts=list(excerpts), images=list(images))


def _submit(*results) -> SubmitArgs:
    return SubmitArgs(results=list(results))


def _ran(call_id, content="ok") -> Result:
    return Result(call_id=call_id, status="ran", content=content)


def _state():
    return S.create_session("pair", "Add retry to fetch_user", "Repo: api/.")


def _session_file(state, name):
    return S.session_dir(state.session_id) / name


def _messages(body: bytes) -> list[dict]:
    return json.loads(body)["messages"]


# 1 request shape
async def test_01_request_shape():
    fake = FakeOllama([decide(decision="x", carry_forward=["a"])])
    st = _state()
    await ask(st, _ask(), runtime(fake))
    body = json.loads(fake.requests[0])
    assert set(body) == {"messages", "model", "stream", "tools"}
    assert body["stream"] is False
    assert body["model"] == "qwen-agent:latest"
    assert body["messages"][0] == {"role": "system", "content": "CHARTER"}
    assert body["tools"][-1]["function"]["name"] == "decide"
    assert fake.requests[0] == canonical_json(body)


# 2 timeout
def test_02_default_timeout_is_none():
    fake = FakeOllama([])
    assert runtime(fake).client.timeout == httpx.Timeout(None)


# 3 answered
async def test_03_answered_returns_to_idle():
    fake = FakeOllama([decide(decision="x", carry_forward=["a"])])
    st = _state()
    out = await ask(st, _ask(), runtime(fake))
    assert out == Answered(status="answered", tool="decide", result={"decision": "x", "carry_forward": ["a"]}, carry_forward=["a"])
    assert st.phase == "IDLE"
    assert st.carry_forward == ["a"]


# 4 tool round
async def test_04_tool_round_then_submit_appends_assistant_and_result():
    fake = FakeOllama([call("read_file", path="a.py", justification="j"), decide(decision="x", carry_forward=[])])
    st = _state()
    rt = runtime(fake)
    first = await ask(st, _ask(), rt)
    assert isinstance(first, ToolCalls)
    assert first.calls[0].call_id == "call-0001"
    assert first.calls[0].args == {"path": "a.py"}
    assert first.calls[0].justification == "j"
    assert st.phase == "AWAITING_SUBMIT"

    out = await submit(st, _submit(_ran("call-0001", "file body")), rt)
    assert isinstance(out, Answered)
    first_msgs = _messages(fake.requests[0])
    second_msgs = _messages(fake.requests[1])
    assert second_msgs[: len(first_msgs)] == first_msgs
    assert second_msgs[len(first_msgs)] == {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": "read_file", "arguments": {"path": "a.py", "justification": "j"}}}],
    }
    assert second_msgs[len(first_msgs) + 1] == {"role": "tool", "tool_name": "read_file", "content": "file body"}


# 5 call ids
async def test_05_call_ids_count_across_replies():
    fake = FakeOllama(
        [
            calls(("read_file", {"path": "a", "justification": "j1"}), ("list_dir", {"path": "d", "justification": "j2"})),
            call("grep", pattern="x", justification="j3"),
        ]
    )
    st = _state()
    rt = runtime(fake)
    first = await ask(st, _ask(), rt)
    assert [c.call_id for c in first.calls] == ["call-0001", "call-0002"]
    second = await submit(st, _submit(_ran("call-0001"), _ran("call-0002")), rt)
    assert isinstance(second, ToolCalls)
    assert [c.call_id for c in second.calls] == ["call-0003"]


# 6 submit mismatch
async def test_06_submit_missing_result_is_error_and_sends_nothing():
    fake = FakeOllama(
        [calls(("read_file", {"path": "a", "justification": "j1"}), ("list_dir", {"path": "d", "justification": "j2"}))]
    )
    st = _state()
    rt = runtime(fake)
    await ask(st, _ask(), rt)
    out = await submit(st, _submit(_ran("call-0001")), rt)
    assert isinstance(out, Error)
    assert "call-0002" in out.reason
    assert st.phase == "AWAITING_SUBMIT"
    assert len(fake.requests) == 1


# 7 denied
async def test_07_denied_result_is_prefixed():
    fake = FakeOllama([call("read_file", path=".git/config", justification="j"), decide(decision="x", carry_forward=[])])
    st = _state()
    rt = runtime(fake)
    await ask(st, _ask(), rt)
    await submit(st, _submit(Result(call_id="call-0001", status="denied", content="outside repo")), rt)
    tool = [m for m in _messages(fake.requests[1]) if m["role"] == "tool"][0]
    assert tool["content"] == "DENIED: outside repo"


# 8 no justification
async def test_08_missing_justification_is_bounced_and_consumes_an_id():
    fake = FakeOllama([call("read_file", path="a"), decide(decision="x", carry_forward=[])])
    st = _state()
    out = await ask(st, _ask(), runtime(fake))
    assert isinstance(out, Answered)
    msgs = _messages(fake.requests[1])
    assert msgs[-2]["role"] == "assistant"
    assert msgs[-1]["role"] == "tool"
    assert "justification is required" in msgs[-1]["content"]
    assert st.next_call_seq == 2


# 9 exact repeat
async def test_09_exact_repeat_with_same_justification_is_bounced():
    fake = FakeOllama(
        [
            call("read_file", path="a", justification="j"),
            call("read_file", path="a", justification="j"),
            decide(decision="x", carry_forward=[]),
        ]
    )
    st = _state()
    rt = runtime(fake)
    await ask(st, _ask(), rt)
    out = await submit(st, _submit(_ran("call-0001")), rt)
    assert isinstance(out, Answered)
    assert "already ran as call-0001" in _messages(fake.requests[2])[-1]["content"]
    assert len(fake.requests) == 3


# 10 repeat with new justification
async def test_10_repeat_with_new_justification_is_run():
    fake = FakeOllama(
        [call("read_file", path="a", justification="j1"), call("read_file", path="a", justification="j2")]
    )
    st = _state()
    rt = runtime(fake)
    await ask(st, _ask(), rt)
    out = await submit(st, _submit(_ran("call-0001")), rt)
    assert isinstance(out, ToolCalls)
    assert [c.call_id for c in out.calls] == ["call-0002"]


# 11 bounce cap
async def test_11_four_bounced_replies_end_with_repeated_bounces():
    fake = FakeOllama([call("read_file", path=f"{i}") for i in range(4)])
    out = await ask(_state(), _ask(), runtime(fake))
    assert out == Error(status="error", reason="repeated bounces")
    assert len(fake.requests) == 4


# 12 malformed
async def test_12_malformed_reply_is_resent_once():
    bad = {"message": {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "decide", "arguments": "{}"}}]}}
    fake = FakeOllama([bad, decide(decision="x", carry_forward=[])])
    out = await ask(_state(), _ask(), runtime(fake))
    assert isinstance(out, Answered)
    last = _messages(fake.requests[1])[-1]
    assert last["role"] == "user"
    assert last["content"].startswith("Your reply was malformed:")


async def test_12b_two_malformed_replies_error():
    bad = {"message": {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "decide", "arguments": "{}"}}]}}
    fake = FakeOllama([bad, bad])
    out = await ask(_state(), _ask(), runtime(fake))
    assert isinstance(out, Error)


# 13 plain text
async def test_13_plain_text_is_resent_with_instruction():
    fake = FakeOllama([plain("hi"), decide(decision="x", carry_forward=[])])
    await ask(_state(), _ask(), runtime(fake))
    msgs = _messages(fake.requests[1])
    assert msgs[-2] == {"role": "assistant", "content": "hi"}
    assert msgs[-1] == {"role": "user", "content": "Call decide with that answer; don't reply in text."}


# 14 empty then ok (trimmed retry, tool-round messages dropped)
async def test_14_empty_reply_retries_with_trimmed_excerpts():
    body_text = "\n".join(f"line {i}" for i in range(1, 41))  # 40 lines, source lines 1..40
    excerpt = Excerpt(path="api/users.py", start=20, end=22, text=body_text, first_line=1)
    fake = FakeOllama(
        [call("read_file", path="a", justification="j"), empty(), decide(decision="x", carry_forward=[])]
    )
    st = _state()
    rt = runtime(fake)
    await ask(st, _ask(excerpts=[excerpt]), rt)
    await submit(st, _submit(_ran("call-0001")), rt)

    retry = _session_file(st, "request-1-retry1.json")
    assert retry.exists()
    retry_body = json.loads(retry.read_bytes())
    assert len(retry_body["messages"]) == 2  # system and the rebuilt first user message only
    content = retry_body["messages"][1]["content"]
    block = content.split("--- api/users.py:20-22 ---\n", 1)[1].split("\n\n", 1)[0]
    assert len(block.split("\n")) <= (22 - 20 + 1) + 10


# 15 empty twice
async def test_15_empty_twice_errors_with_both_sizes():
    fake = FakeOllama([empty(), empty()])
    st = _state()
    out = await ask(st, _ask(), runtime(fake))
    assert isinstance(out, Error)
    assert str(len(fake.requests[0])) in out.reason
    assert str(len(fake.requests[1])) in out.reason
    assert len(fake.requests) == 2
    assert _session_file(st, "request-1-retry1.json").exists()
    assert _session_file(st, "reply-1-retry1.json").exists()


# 16 need_info
async def test_16_need_info_returns_to_idle_and_next_ask_is_accepted():
    fake = FakeOllama([call("need_info", questions=["q"]), decide(decision="x", carry_forward=[])])
    st = _state()
    rt = runtime(fake)
    out = await ask(st, _ask(), rt)
    assert out == NeedInfo(status="need_info", questions=["q"])
    assert st.phase == "IDLE"
    again = await ask(st, _ask(), rt)
    assert isinstance(again, Answered)


# 17 answer with missing fields
async def test_17_answer_missing_field_is_bounced_then_accepted():
    fake = FakeOllama([decide(decision="x"), decide(decision="x", carry_forward=["b"])])
    st = _state()
    out = await ask(st, _ask(), runtime(fake))
    assert isinstance(out, Answered)
    assert "carry_forward" in _messages(fake.requests[1])[-1]["content"]


# 18 images
async def test_18_image_limit_is_inclusive(tmp_path):
    ok = tmp_path / "ok.png"
    ok.write_bytes(b"a" * 1_000_000)
    big = tmp_path / "big.png"
    big.write_bytes(b"a" * 1_000_001)
    fake = FakeOllama([decide(decision="x", carry_forward=[])])
    st = _state()
    rt = runtime(fake)
    out = await ask(st, _ask(images=[str(ok)]), rt)
    assert isinstance(out, Answered)
    first_user = _messages(fake.requests[0])[1]
    assert first_user["images"][0] == base64.b64encode(b"a" * 1_000_000).decode("ascii")

    out2 = await ask(st, _ask(images=[str(big)]), rt)
    assert isinstance(out2, Error)
    assert len(fake.requests) == 1


async def test_18b_missing_image_file_is_error_without_state_change(tmp_path):
    st = _state()
    before = st.model_dump()
    out = await ask(st, _ask(images=[str(tmp_path / "nope.png")]), runtime(FakeOllama([])))
    assert isinstance(out, Error)
    assert st.model_dump() == before


# 19 HTTP errors
async def test_19a_http_500_is_error_and_state_unchanged_without_resend():
    fake = FakeOllama([Status(500, "boom")])
    st = _state()
    before = st.model_dump(exclude={"ask_n", "rounds"})
    out = await ask(st, _ask(), runtime(fake))
    assert isinstance(out, Error)
    assert len(fake.requests) == 1
    assert st.model_dump(exclude={"ask_n", "rounds"}) == before


async def test_19b_read_timeout_is_error_and_state_unchanged_without_resend():
    fake = FakeOllama([httpx.ReadTimeout("slow")])
    st = _state()
    before = st.model_dump(exclude={"ask_n", "rounds"})
    out = await ask(st, _ask(), runtime(fake))
    assert isinstance(out, Error)
    assert len(fake.requests) == 1
    assert st.model_dump(exclude={"ask_n", "rounds"}) == before


# 20 metrics
async def test_20_metrics_recorded_per_reply_and_summed_per_session():
    with_cache = {"message": {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "read_file", "arguments": {"path": "a", "justification": "j"}}}]},
                  "prompt_eval_count": 100, "prompt_eval_cached_count": 80, "prompt_eval_duration": 5}
    no_cache = {"message": {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "decide", "arguments": {"decision": "x", "carry_forward": []}}}]},
                "prompt_eval_count": 100, "prompt_eval_duration": 5}
    fake = FakeOllama([with_cache, no_cache])
    st = _state()
    rt = runtime(fake)
    await ask(st, _ask(), rt)
    await submit(st, _submit(_ran("call-0001")), rt)
    assert json.loads(_session_file(st, "reply-1.json").read_text())["navigator_metrics"] == {
        "prompt_eval_count": 100,
        "prompt_eval_cached_count": 80,
        "prompt_eval_duration": 5,
    }
    assert json.loads(_session_file(st, "reply-1-r1.json").read_text())["navigator_metrics"] == {
        "prompt_eval_count": 100,
        "prompt_eval_cached_count": None,
        "prompt_eval_duration": 5,
    }
    assert st.metrics.sends == 2
    assert st.metrics.prompt_eval_count == 200
    assert st.metrics.prompt_eval_cached_count == 80
    assert st.metrics.prompt_eval_duration == 10


# 21 audit files
async def test_21_audit_files_for_two_tool_rounds(tmp_path):
    img = tmp_path / "shot.png"
    img.write_bytes(b"tiny")
    fake = FakeOllama(
        [
            call("read_file", path="a", justification="j1"),
            call("read_file", path="b", justification="j2"),
            decide(decision="x", carry_forward=[]),
        ]
    )
    st = _state()
    rt = runtime(fake)
    await ask(st, _ask(images=[str(img)]), rt)
    await submit(st, _submit(_ran("call-0001")), rt)
    await submit(st, _submit(_ran("call-0002")), rt)
    for name in ["request-1.json", "request-1-r1.json", "request-1-r2.json", "reply-1.json", "reply-1-r1.json", "reply-1-r2.json"]:
        assert _session_file(st, name).exists(), name
    for name in ["request-1.json", "request-1-r1.json", "request-1-r2.json"]:
        body = json.loads(_session_file(st, name).read_bytes())
        assert set(body) == {"messages", "model", "stream", "tools"}
    placeholder = json.loads(_session_file(st, "request-1.json").read_bytes())["messages"][1]["images"][0]
    assert re.fullmatch(r"[0-9a-f]{64}:\d+", placeholder)


# 22 prefix byte-stability
def _messages_slice(body: bytes) -> bytes:
    start = body.index(b'"messages":[')
    return body[start : body.index(b'],"model":')]  # up to but not including the closing ]


async def test_22_request_prefix_is_byte_stable_across_tool_rounds():
    fake = FakeOllama(
        [
            call("read_file", path="a", justification="j1"),
            call("read_file", path="b", justification="j2"),
            call("read_file", path="c", justification="j3"),
            decide(decision="x", carry_forward=[]),
        ]
    )
    st = _state()
    rt = runtime(fake)
    await ask(st, _ask(), rt)
    for i, cid in enumerate(["call-0001", "call-0002", "call-0003"], start=1):
        await submit(st, _submit(_ran(cid, f"result {i}")), rt)
    assert len(fake.requests) == 4
    for earlier, later in zip(fake.requests, fake.requests[1:]):
        assert _messages_slice(later).startswith(_messages_slice(earlier))
    tools_tail = {r[r.index(b'"tools":['):] for r in fake.requests}
    assert len(tools_tail) == 1


# 23 deterministic render across asks
async def test_23_same_state_and_question_give_identical_request_bodies():
    fake_a = FakeOllama([decide(decision="x", carry_forward=[])])
    fake_b = FakeOllama([decide(decision="x", carry_forward=[])])
    await ask(_state(), _ask(), runtime(fake_a))
    await ask(_state(), _ask(), runtime(fake_b))
    assert fake_a.requests[0] == fake_b.requests[0]


# 24 summary not re-rendered mid-ask
async def test_24_first_user_message_is_unchanged_by_state_mutation_mid_ask():
    fake = FakeOllama([call("read_file", path="a", justification="j"), decide(decision="x", carry_forward=[])])
    st = _state()
    rt = runtime(fake)
    await ask(st, _ask(), rt)
    S.apply_record(st, "decided", {"text": "added while awaiting submit"})
    await submit(st, _submit(_ran("call-0001")), rt)
    first_user_first = _messages(fake.requests[0])[1]["content"]
    first_user_later = _messages(fake.requests[1])[1]["content"]
    assert first_user_first == first_user_later
    assert "added while awaiting submit" not in first_user_later


# Additional coverage for the loop rules the table implies


async def test_question_already_ending_with_suffix_is_not_doubled():
    fake = FakeOllama([decide(decision="x", carry_forward=[])])
    await ask(_state(), _ask(question="Which?\nAnswer by calling decide."), runtime(fake))
    content = _messages(fake.requests[0])[1]["content"]
    assert content.count("Answer by calling decide.") == 1
    assert content.endswith("Answer by calling decide.")


async def test_ask_and_submit_guard_phases():
    fake = FakeOllama([call("read_file", path="a", justification="j")])
    st = _state()
    rt = runtime(fake)
    await ask(st, _ask(), rt)
    assert (await ask(st, _ask(), rt)).reason == "session awaiting submit"
    st.phase = "IDLE"
    assert (await submit(st, _submit(_ran("call-0001")), rt)).reason == "session not awaiting submit"


# Phase 7 test 5: the Ollama request bytes carry no navigator_call key and are the same as before phase 7


async def _g5_sends() -> tuple[list[bytes], S.SessionState]:
    """One ask that reaches the three navigator_call sites with 3 bounces (the cap allows 3), then one submit.

    Send 1 carries the answer-tool bounce, send 2 the no-survivor bounce, send 3 the _submit bounce and result.
    """
    fake = FakeOllama(
        [
            # answer-tool bounce: decide lacks carry_forward, read_file is ignored (bounces 1)
            calls(("read_file", {"path": "a", "justification": "j1"}), ("decide", {"decision": "x"})),
            # no survivor: one call with no justification (bounces 2)
            call("read_file", path="b"),
            # mixed: the first call has no justification, the second survives (bounces 3)
            calls(("read_file", {"path": "c"}), ("read_file", {"path": "d", "justification": "j2"})),
            decide(decision="x", carry_forward=[]),
        ]
    )
    st = _state()
    rt = runtime(fake)
    first = await ask(st, _ask(), rt)
    assert isinstance(first, ToolCalls)
    assert [c.call_id for c in first.calls] == ["call-0003"]
    out = await submit(st, _submit(_ran("call-0003", "d body")), rt)
    assert isinstance(out, Answered)
    assert st.bounces == BOUNCE_CAP
    return fake.requests, st


async def test_p7_05_ollama_request_bytes_match_golden_and_have_no_navigator_call():
    requests, st = await _g5_sends()
    assert len(requests) == 4
    for i, body in enumerate(requests):
        assert body == (GOLDEN / f"g5_ollama_navigator_call_{i}.json").read_bytes(), f"send {i}"
        assert b"navigator_call" not in body, f"send {i}"
    # The state has the key, so the strip is exercised. The result's index is its place in pending, not in survivors.
    tool_msgs = [(m["tool_name"], m["navigator_call"]) for m in st.messages if m["role"] == "tool"]
    assert tool_msgs == [("read_file", 0), ("decide", 1), ("read_file", 0), ("read_file", 0), ("read_file", 1)]
