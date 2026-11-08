"""Phase 7 tests: AnthropicClient (docs/phases/phase-7-anthropic-provider.md, section 10, tests 6-14).

Plan: .scratch/phase-7-anthropic-provider/plan.md, section 2.6. The D-tests cover plan decisions D3, D4, D14, D16 and D17.
No test reaches the Claude API: every SDK request goes to a FakeAnthropic transport (plan D2), and HOME is an empty
directory so the SDK finds no profile (plan D15).
"""

import base64
import copy
import json

import httpx
import httpx2
import pytest

from fakes import DECIDE_TOOL, FakeClock, FakeOllama, Status, call
from fakes import runtime as ollama_runtime
from fakes_anthropic import (
    HAIKU,
    OPUS,
    FakeAnthropic,
    client,
    decide,
    error_body,
    fallback,
    message,
    runtime,
    text,
    thinking,
    tool_use,
    usage,
)
from navigator_mcp import session as S
from navigator_mcp.anthropic_client import FALLBACK_BETA, FALLBACK_MODELS, MAX_TOKENS, AnthropicClient
from navigator_mcp.ollama import (
    Answered,
    AskArgs,
    Call,
    Error,
    Result,
    SubmitArgs,
    ToolCalls,
    TransportFailure,
    _placeholder,
    ask,
    submit,
)
from navigator_mcp.schemas import NAVIGATOR_TOOLS

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("isolated_home")]

QUESTION = "Which module owns retries?"
TOOLS = [*NAVIGATOR_TOOLS, DECIDE_TOOL]
SIMPLE = [{"role": "system", "content": "CHARTER"}, {"role": "user", "content": "Q"}]

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 8
GIF = b"GIF89a" + b"\x00" * 8
WEBP = b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"\x00" * 4
BMP = b"BM" + b"\x00" * 30


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _ask(question=QUESTION, tool="decide", images=()) -> AskArgs:
    return AskArgs(question=question, answer_tool=tool, images=list(images))


def _submit(*results) -> SubmitArgs:
    return SubmitArgs(results=list(results))


def _ran(call_id, content="ok", image_path=None) -> Result:
    return Result(call_id=call_id, status="ran", content=content, image_path=image_path)


def _state():
    return S.create_session("pair", "Add retry to fetch_user", "Repo: api/.")


def _session_file(state, name):
    return S.session_dir(state.session_id) / name


def _restorable(state) -> dict:
    return state.model_dump(exclude={"ask_n", "rounds"})  # ask_n and rounds are monotonic, never restored


def _tool_result(tool_use_id: str, content: str) -> dict:
    return {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}


def _image(raw: bytes, media_type: str) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64(raw)}}


# 6 message conversion
def test_p7_06_message_conversion_and_merge():
    content = [
        thinking(),
        text("Reading both."),
        tool_use("toolu_1", "read_file", path="a.py", justification="j1"),
        tool_use("toolu_2", "read_file", path="b.py", justification="j2"),
    ]
    messages = [
        {"role": "system", "content": "CHARTER"},
        {"role": "user", "content": "Q", "images": [b64(PNG), b64(JPEG)]},
        {"role": "assistant", "content": "Reading both.", "tool_calls": [], "navigator_anthropic_content": content},
        # A run of adjacent user and tool messages: tool_result blocks go first, then the other blocks, each in order.
        {"role": "user", "content": "a note"},
        {"role": "tool", "tool_name": "read_file", "content": "b body", "navigator_call": 1},
        {"role": "tool", "tool_name": "read_file", "content": "a body", "navigator_call": 0},
        {"role": "user", "content": "call-0002 image", "images": [b64(PNG)]},
    ]
    kept = copy.deepcopy(messages)
    c = AnthropicClient(OPUS)
    raw = c.build_request(messages, TOOLS)
    body = json.loads(raw)

    assert body["system"] == "CHARTER"
    assert body["messages"] == [
        {"role": "user", "content": [_image(PNG, "image/png"), _image(JPEG, "image/jpeg"), {"type": "text", "text": "Q"}]},
        {"role": "assistant", "content": content},
        {
            "role": "user",
            "content": [
                _tool_result("toolu_2", "b body"),
                _tool_result("toolu_1", "a body"),
                {"type": "text", "text": "a note"},
                _image(PNG, "image/png"),
                {"type": "text", "text": "call-0002 image"},
            ],
        },
    ]
    assert body["tools"] == [
        {
            "name": t["function"]["name"],
            "description": t["function"]["description"],
            "input_schema": t["function"]["parameters"],
        }
        for t in TOOLS
    ]
    assert set(body) == {
        "model", "max_tokens", "system", "messages", "tools", "thinking", "output_config", "cache_control", "fallbacks"
    }
    assert body["max_tokens"] == MAX_TOKENS == 16000
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"] == {"effort": "medium"}
    assert body["cache_control"] == {"type": "ephemeral"}
    # deterministic (section 6.3), and the state is not changed
    assert c.build_request(messages, TOOLS) == raw
    assert messages == kept

    redacted = json.loads(c.build_request(messages, TOOLS, redact_images=True))
    first = redacted["messages"][0]["content"]
    assert first[0]["source"] == {"type": "base64", "media_type": "image/png", "data": _placeholder(b64(PNG))}
    assert first[1]["source"]["data"] == _placeholder(b64(JPEG))
    assert redacted["messages"][2]["content"][3]["source"]["data"] == _placeholder(b64(PNG))
    assert redacted["messages"][1] == body["messages"][1]
    assert messages == kept


@pytest.mark.parametrize(
    "raw, media_type",
    [(PNG, "image/png"), (JPEG, "image/jpeg"), (GIF, "image/gif"), (WEBP, "image/webp")],
)
def test_p7_06b_image_media_type_from_first_bytes(raw, media_type):
    messages = [*SIMPLE[:1], {"role": "user", "content": "Q", "images": [b64(raw)]}]
    body = json.loads(AnthropicClient(OPUS).build_request(messages, TOOLS))
    assert body["messages"][0]["content"][0]["source"]["media_type"] == media_type


# 7 tool result identifiers
@pytest.mark.parametrize("bounced", [1, 0], ids=["second-bounced", "first-bounced"])
async def test_p7_07_tool_result_ids_follow_navigator_call(anthropic_env, bounced):
    survivor = 1 - bounced
    ids = ["toolu_A", "toolu_B"]
    uses = [
        tool_use("toolu_A", "read_file", path="a.py", justification="ja"),
        tool_use("toolu_B", "read_file", path="b.py", justification="jb"),
    ]
    del uses[bounced]["input"]["justification"]
    first_content = [thinking(), *uses]
    fake = FakeAnthropic([message(*first_content, stop_reason="tool_use"), decide(decision="x", carry_forward=[])])
    st = _state()
    rt = runtime(fake)

    first = await ask(st, _ask(), rt)
    assert isinstance(first, ToolCalls)
    assert [c.args["path"] for c in first.calls] == [["a.py", "b.py"][survivor]]
    out = await submit(st, _submit(_ran(first.calls[0].call_id, "file body")), rt)
    assert isinstance(out, Answered)

    msgs = fake.bodies[1]["messages"]
    assert msgs[1] == {"role": "assistant", "content": first_content}
    bounce, result = msgs[2]["content"]
    assert bounce["tool_use_id"] == ids[bounced]
    assert bounce["content"].startswith("read_file: justification is required")
    assert result == _tool_result(ids[survivor], "file body")
    assert [m["navigator_call"] for m in st.messages if m["role"] == "tool"] == [bounced, survivor]


# 8 unknown image type
async def test_p7_08_unknown_image_type_is_transport_failure_and_state_restored(anthropic_env, tmp_path):
    img = tmp_path / "x.bmp"
    img.write_bytes(BMP)
    fake = FakeAnthropic([])
    st = _state()
    before = _restorable(st)
    out = await ask(st, _ask(images=[str(img)]), runtime(fake))
    assert out == Error(status="error", reason="image type is not supported by the anthropic provider")
    assert fake.bodies == []
    assert _restorable(st) == before


async def test_p7_08b_unknown_image_type_in_submit_restores_the_pending_state(anthropic_env, tmp_path):
    img = tmp_path / "shot.bmp"
    img.write_bytes(BMP)
    fake = FakeAnthropic([message(tool_use("toolu_1", "screenshot", target="home", justification="j"), stop_reason="tool_use")])
    st = _state()
    rt = runtime(fake)
    first = await ask(st, _ask(), rt)
    assert isinstance(first, ToolCalls)
    before = _restorable(st)
    out = await submit(st, _submit(_ran("call-0001", "shot", image_path=str(img))), rt)
    assert out == Error(status="error", reason="image type is not supported by the anthropic provider")
    assert len(fake.bodies) == 1
    assert _restorable(st) == before
    assert st.phase == "AWAITING_SUBMIT"


# 9 reply conversion
READ_A = tool_use("toolu_1", "read_file", path="a.py", justification="j")
READ_A_CALL = {"function": {"name": "read_file", "arguments": {"path": "a.py", "justification": "j"}}}


@pytest.mark.parametrize(
    "blocks, content, tool_calls",
    [
        ([text("just text")], "just text", None),
        ([READ_A], "", [READ_A_CALL]),
        ([thinking(), text("one"), READ_A, text("two")], "one\ntwo", [READ_A_CALL]),
    ],
    ids=["text-only", "tool-use-only", "text-and-tool-use"],
)
async def test_p7_09_reply_conversion(anthropic_env, blocks, content, tool_calls):
    reply = message(*blocks, usage_=usage(100, 7, cache_read=80, cache_creation=20))
    fake = FakeAnthropic([reply])
    c = client(fake)
    resp = await c.post(c.build_request(SIMPLE, TOOLS))
    assert isinstance(resp, httpx.Response)
    assert resp.status_code == 200
    body = resp.json()
    expected = {"role": "assistant", "content": content, "navigator_anthropic_content": blocks}
    if tool_calls is not None:
        expected["tool_calls"] = tool_calls
    assert body["message"] == expected
    assert body["prompt_eval_count"] == 200
    assert body["prompt_eval_cached_count"] == 80
    assert "prompt_eval_duration" not in body
    assert "navigator_error" not in body
    assert body["navigator_anthropic_response"]["id"] == reply["id"]
    assert body["navigator_anthropic_response"]["usage"]["input_tokens"] == 100


async def test_p7_09b_usage_counts_go_to_the_metrics(anthropic_env):
    fake = FakeAnthropic(
        [
            message(READ_A, stop_reason="tool_use", usage_=usage(100, 5, cache_read=80, cache_creation=20)),
            message(
                tool_use("toolu_2", "decide", decision="x", carry_forward=[]),
                stop_reason="tool_use",
                usage_=usage(50, 5, cache_read=150, cache_creation=0),
            ),
        ]
    )
    st = _state()
    rt = runtime(fake)
    await ask(st, _ask(), rt)
    out = await submit(st, _submit(_ran("call-0001")), rt)
    assert isinstance(out, Answered)
    assert st.metrics.sends == 2
    assert st.metrics.prompt_eval_count == 200 + 200
    assert st.metrics.prompt_eval_cached_count == 80 + 150
    assert st.metrics.prompt_eval_duration == 0
    audit = json.loads(_session_file(st, "reply-1.json").read_bytes())
    assert audit["navigator_metrics"] == {
        "prompt_eval_count": 200, "prompt_eval_cached_count": 80, "prompt_eval_duration": None
    }


# 10 refusal and max_tokens
@pytest.mark.parametrize(
    "stop_reason, stop_details, reason",
    [
        ("refusal", {"type": "refusal", "category": "cyber"}, "refused (cyber)"),
        ("refusal", {"type": "refusal", "category": None}, "refused (none)"),
        ("refusal", None, "refused (none)"),
        ("max_tokens", None, "reply cut off at max_tokens"),
    ],
)
async def test_p7_10_refusal_and_max_tokens_are_errors_with_an_audit_reply(anthropic_env, stop_reason, stop_details, reason):
    fake = FakeAnthropic([message(text("partial"), stop_reason=stop_reason, stop_details=stop_details)])
    st = _state()
    out = await ask(st, _ask(), runtime(fake))
    assert out == Error(status="error", reason=reason, stage="waiting on navigator (request-1)")
    assert st.phase == "IDLE"
    assert len(fake.bodies) == 1
    audit = json.loads(_session_file(st, "reply-1.json").read_bytes())
    assert audit["navigator_error"] == reason
    assert audit["navigator_anthropic_response"]["stop_reason"] == stop_reason


# 11 retry statuses
@pytest.mark.parametrize("code", [529, 429, 503])
async def test_p7_11_retry_statuses_back_off_then_busy(anthropic_env, code):
    fake = FakeAnthropic([decide(decision="x", carry_forward=[])])
    fake.script_status(code, 4)
    clock = FakeClock()
    sleeps: list[float] = []

    async def record(dt):
        sleeps.append(dt)
        await clock.virtual_sleep(dt)

    st = _state()
    out = await ask(st, _ask(), runtime(fake, sleep=record, max_503_retries=3))
    assert sleeps == [1, 2, 4]
    assert clock.now() == 7
    assert isinstance(out, Error)
    assert out.reason.startswith("anthropic busy (503): ")
    assert f"status {code}" in out.reason
    assert len(fake.bodies) == 4


async def test_p7_11b_retry_status_then_success(anthropic_env):
    fake = FakeAnthropic([decide(decision="x", carry_forward=[])])
    fake.script_status(529, 2)
    clock = FakeClock()
    out = await ask(_state(), _ask(), runtime(fake, sleep=clock.virtual_sleep, max_503_retries=3))
    assert isinstance(out, Answered)
    assert clock.now() == 1 + 2
    assert len(fake.bodies) == 3


# 12 other status
async def test_p7_12_status_401_is_transport_failure_and_state_restored(anthropic_env):
    fake = FakeAnthropic([Status(401, error_body(401))])
    st = _state()
    before = _restorable(st)
    out = await ask(st, _ask(), runtime(fake))
    assert isinstance(out, Error)
    assert out.reason.startswith("HTTP 401: ")
    assert "authentication_error" in out.reason
    assert len(fake.bodies) == 1
    assert _restorable(st) == before
    assert _session_file(st, "reply-1-error.txt").read_text() == out.reason


@pytest.mark.parametrize(
    "exc, name",
    [(httpx2.ReadTimeout("slow"), "ReadTimeout"), (httpx2.ConnectError("refused"), "ConnectError")],
)
async def test_p7_12b_sdk_timeout_and_connection_errors_are_transport_failures(anthropic_env, exc, name):
    fake = FakeAnthropic([exc])
    st = _state()
    before = _restorable(st)
    out = await ask(st, _ask(), runtime(fake))
    assert isinstance(out, Error)
    assert out.reason.startswith(f"{name}: ")
    assert len(fake.bodies) == 1
    assert _restorable(st) == before


# 13 fallbacks and the beta header
@pytest.mark.parametrize("model, fallbacks", [(OPUS, True), (HAIKU, False)])
async def test_p7_13_fallbacks_and_beta_only_for_fallback_models(anthropic_env, model, fallbacks):
    fake = FakeAnthropic([decide(decision="x", carry_forward=[])])
    st = _state()
    out = await ask(st, _ask(), runtime(fake, model))
    assert isinstance(out, Answered)
    wire, headers = fake.bodies[0], fake.headers[0]
    audit = json.loads(_session_file(st, "request-1.json").read_bytes())
    assert wire == audit  # the SDK sends exactly the fields of the audited body
    assert wire["model"] == model
    assert wire["max_tokens"] == 16000
    assert wire["system"] == "CHARTER"
    assert not {"tool_choice", "temperature", "top_p", "top_k"} & set(wire)
    if fallbacks:
        assert wire["fallbacks"] == "default"
        assert headers["anthropic-beta"] == FALLBACK_BETA == "server-side-fallback-2026-07-01"
    else:
        assert "fallbacks" not in wire
        assert "anthropic-beta" not in headers
    assert "test" in (headers.get("x-api-key"), headers.get("authorization", "").removeprefix("Bearer "))  # anthropic_env
    assert fake.urls[0].startswith("http://anthropic.test/v1/messages")


@pytest.mark.parametrize("model", sorted(FALLBACK_MODELS) + [HAIKU, "claude-opus-4-0"])
def test_p7_13b_fallbacks_field_for_exactly_the_four_models(model):
    body = json.loads(AnthropicClient(model).build_request(SIMPLE, TOOLS))
    assert ("fallbacks" in body) == (model in {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"})


# 14 full loop
async def test_p7_14_ask_and_submit_through_the_full_loop(anthropic_env):
    first_content = [
        thinking(),
        text("I will read it."),
        tool_use("toolu_1", "read_file", path="a.py", start=1, end=9, justification="see retries"),
    ]
    fake = FakeAnthropic(
        [
            message(*first_content, stop_reason="tool_use"),
            decide("toolu_2", decision="use backoff", carry_forward=["a"]),
        ]
    )
    st = _state()
    rt = runtime(fake)

    first = await ask(st, _ask(), rt)
    assert first == ToolCalls(
        status="tool_calls",
        calls=[Call(call_id="call-0001", name="read_file", args={"path": "a.py", "start": 1, "end": 9}, justification="see retries")],
    )
    assert st.phase == "AWAITING_SUBMIT"
    out = await submit(st, _submit(_ran("call-0001", "file body")), rt)
    assert out == Answered(
        status="answered", tool="decide", result={"decision": "use backoff", "carry_forward": ["a"]}, carry_forward=["a"]
    )
    assert st.phase == "IDLE"

    b0, b1 = fake.bodies
    assert b0["messages"][0]["role"] == "user"
    assert b0["messages"][0]["content"][-1]["text"].endswith("Answer by calling decide.")
    assert b1["messages"][:1] == b0["messages"]  # the prefix is stable for the prompt cache
    assert b1["messages"][1:] == [
        {"role": "assistant", "content": first_content},
        {"role": "user", "content": [_tool_result("toolu_1", "file body")]},
    ]
    assert b1["tools"] == b0["tools"]
    # state.messages keeps the Ollama format, plus the navigator_* keys
    assert [m["role"] for m in st.messages] == ["system", "user", "assistant", "tool"]
    assert st.messages[2]["navigator_anthropic_content"] == first_content
    assert st.messages[2]["tool_calls"] == [
        {"function": {"name": "read_file", "arguments": {"path": "a.py", "start": 1, "end": 9, "justification": "see retries"}}}
    ]
    assert st.messages[3] == {"role": "tool", "tool_name": "read_file", "content": "file body", "navigator_call": 0}
    for name in ("request-1.json", "reply-1.json", "request-1-r1.json", "reply-1-r1.json"):
        assert _session_file(st, name).exists(), name
    await rt.client.aclose()


# D4 echo serialization
async def test_p7_d4_next_request_echoes_the_wire_content(anthropic_env):
    content = [
        thinking(),
        fallback(),
        text("Reading a.py first."),
        tool_use("toolu_9", "read_file", path="a.py", justification="j"),
    ]
    fake = FakeAnthropic(
        [message(*content, stop_reason="tool_use", model="claude-sonnet-5-5"), decide(decision="x", carry_forward=[])]
    )
    st = _state()
    rt = runtime(fake)
    await ask(st, _ask(), rt)
    out = await submit(st, _submit(_ran("call-0001")), rt)
    assert isinstance(out, Answered)
    assert fake.bodies[1]["messages"][1] == {"role": "assistant", "content": content}
    sent = json.dumps(fake.bodies[1]["messages"][1])
    assert '"from"' in sent and '"from_"' not in sent
    assert '"citations": null' in sent
    assert "caller" not in sent and "toolset_name" not in sent
    audit = json.loads(_session_file(st, "reply-1.json").read_bytes())
    assert audit["navigator_anthropic_response"]["content"][1]["from"] == {"model": OPUS}


# D14 empty tool content
async def test_p7_d14_empty_tool_content_converts(anthropic_env):
    fake = FakeAnthropic([message(READ_A, stop_reason="tool_use"), decide(decision="x", carry_forward=[])])
    st = _state()
    rt = runtime(fake)
    await ask(st, _ask(), rt)
    out = await submit(st, _submit(_ran("call-0001", "")), rt)
    assert isinstance(out, Answered)
    assert fake.bodies[1]["messages"][2] == {"role": "user", "content": [_tool_result("toolu_1", "")]}


# D16 provider switch across a restart
async def test_p7_d16_ollama_ask_then_anthropic_submit_is_transport_failure(anthropic_env):
    ollama_fake = FakeOllama([call("read_file", path="a.py", justification="j")])
    st = _state()
    first = await ask(st, _ask(), ollama_runtime(ollama_fake))
    assert isinstance(first, ToolCalls)
    before = _restorable(st)
    fake = FakeAnthropic([])
    out = await submit(st, _submit(_ran("call-0001")), runtime(fake))
    assert out == Error(status="error", reason="the pending ask was made with a different provider")
    assert fake.bodies == []
    assert _restorable(st) == before
    assert st.phase == "AWAITING_SUBMIT"


def test_p7_d16_conversion_raises_for_an_ollama_assistant_message():
    messages = [
        *SIMPLE,
        {"role": "assistant", "content": "", "tool_calls": [READ_A_CALL]},
        {"role": "tool", "tool_name": "read_file", "content": "x", "navigator_call": 0},
    ]
    with pytest.raises(TransportFailure, match="the pending ask was made with a different provider"):
        AnthropicClient(OPUS).build_request(messages, TOOLS)


# D17 the SDK's non-streaming guard, and D3 the timeout conversion
async def test_p7_d17_default_model_with_16000_tokens_and_no_timeout_does_not_need_streaming(anthropic_env):
    fake = FakeAnthropic([decide(decision="x", carry_forward=[])])
    c = client(fake, "claude-opus-5-5")
    resp = await c.post(c.build_request(SIMPLE, TOOLS))  # no timeout, as with no NAVIGATOR_JOB_TIMEOUT
    assert resp.status_code == 200
    assert fake.timeouts[0]["read"] == 600  # the SDK's default non-streaming read timeout
    await c.aclose()


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_p7_d17_opus_4_0_needs_a_timeout_and_d3_timeout_reaches_the_transport(anthropic_env):
    fake = FakeAnthropic([decide(decision="x", carry_forward=[])])
    c = client(fake, "claude-opus-4-0")
    body = c.build_request(SIMPLE, TOOLS)
    with pytest.raises(ValueError, match="Streaming is required"):  # the guard runs on this path
        await c.post(body)
    assert fake.bodies == []
    resp = await c.post(body, timeout=httpx.Timeout(30.0))  # a job deadline: the guard is skipped
    assert resp.status_code == 200
    assert fake.timeouts[0] == {"connect": 30.0, "read": 30.0, "write": 30.0, "pool": 30.0}


async def test_p7_d3_job_deadline_becomes_the_sdk_timeout(anthropic_env):
    fake = FakeAnthropic([decide(decision="x", carry_forward=[])])
    out = await ask(_state(), _ask(), runtime(fake, deadline_remaining=lambda: 42.0))
    assert isinstance(out, Answered)
    assert fake.timeouts[0] == {"connect": 42.0, "read": 42.0, "write": 42.0, "pool": 42.0}


# D2 construction
async def test_p7_d2_client_without_transport_constructs_with_sdk_retries_off(anthropic_env):
    c = AnthropicClient(OPUS)
    assert c.name == "anthropic"
    assert c.model == OPUS
    assert c._sdk.max_retries == 0
    await c.aclose()
    with_transport = client(FakeAnthropic([]))
    assert with_transport._sdk.max_retries == 0
    await with_transport.aclose()
