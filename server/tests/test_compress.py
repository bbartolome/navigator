"""Phase 6 tests: LLMLingua-2 compression behind a flag (docs/phases/phase-6-compression.md, section 6).

Tests 1-11 use a fake compressor and need no llmlingua. Test 12 runs only when the compression extra is installed.
"""

import json
import random
import re
import sys

import pytest

from fakes import FakeOllama, call, decide, StubMode
from navigator_mcp import compress
from navigator_mcp import session as S
from navigator_mcp.ollama import AskArgs, Answered, Runtime, OllamaClient, ask, submit, Result, SubmitArgs
from navigator_mcp.render import render_summary

pytestmark = pytest.mark.anyio

PROTECTED_EXAMPLE = (
    "Chose approach B because the retry in server/src/navigator_mcp/ollama.py:120-140 hides errors. "
    "See call c-0007 and `render(state)`. Counts: 3 files."
)


class FakeCompressor:
    """Returns the input with every third word removed; digits are always kept (force_reserve_digit)."""

    def __init__(self):
        self.calls: list[tuple[str, float]] = []

    def compress_prompt(self, context, rate=0.5, force_tokens=(), drop_consecutive=False, force_reserve_digit=False, **kw):
        (text,) = context
        self.calls.append((text, rate))
        parts = re.split(r"(\s+)", text)
        out, words = [], 0
        for part in parts:
            if not part or part.isspace():
                out.append(part)
                continue
            words += 1
            if words % 3 == 0 and not any(ch.isdigit() for ch in part):
                continue
            out.append(part)
        kept = "".join(out)
        return {"compressed_prompt": kept, "origin_tokens": words, "compressed_tokens": len(kept.split())}


class CountingFactory:
    def __init__(self, compressor=None, exc=None):
        self.compressor = compressor or FakeCompressor()
        self.exc = exc
        self.calls = 0

    def __call__(self, model):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return self.compressor


@pytest.fixture(autouse=True)
def reset_compress(monkeypatch):
    monkeypatch.delenv("NAVIGATOR_COMPRESSION", raising=False)
    monkeypatch.delenv("NAVIGATOR_COMPRESSION_RATE", raising=False)
    monkeypatch.delenv("NAVIGATOR_LLMLINGUA_MODEL", raising=False)
    compress.reset()
    yield
    compress.reset()


def _on(monkeypatch, factory, rate=None):
    monkeypatch.setenv("NAVIGATOR_COMPRESSION", "on")
    if rate is not None:
        monkeypatch.setenv("NAVIGATOR_COMPRESSION_RATE", rate)
    compress.set_factory(factory)


# 1 protected spans byte-identical; property over random interleavings
def test_01_protected_spans_survive_and_digits_are_kept(monkeypatch):
    _on(monkeypatch, CountingFactory())
    out = compress.compress_text(PROTECTED_EXAMPLE).text
    for span in ["server/src/navigator_mcp/ollama.py:120-140", "c-0007", "`render(state)`"]:
        assert span.encode() in out.encode(), span
    assert "3" in out


def test_01b_random_interleavings_keep_protected_spans_in_order(monkeypatch):
    _on(monkeypatch, CountingFactory())
    rng = random.Random(7)
    words = ["alpha", "beta", "gamma", "delta", "epsilon"]
    protected = ["src/app/main.py:10-20", "call-0042", "shot-3", "`f(x)`", "https://example.com/a"]
    for _ in range(60):
        pieces = []
        for _ in range(rng.randint(3, 14)):
            pieces.append(rng.choice(protected) if rng.random() < 0.4 else rng.choice(words))
        text = " ".join(pieces)
        spans = [s for kind, s in compress.split_protected(text) if kind == "protected"]
        out = compress.compress_text(text).text
        pos = 0
        for span in spans:
            idx = out.find(span, pos)
            assert idx >= 0, (text, out, span)
            pos = idx + len(span)


# 2 post-check fallback
def test_02_a_mangled_reassembly_falls_back_to_the_original(monkeypatch):
    _on(monkeypatch, CountingFactory())
    monkeypatch.setattr(compress, "_reassemble", lambda spans, compressed: "src/app/main.py:9-9 mangled")
    assert compress.compress_text(PROTECTED_EXAMPLE).text == PROTECTED_EXAMPLE


# 3 code fence bytes
def test_03_fenced_code_is_byte_identical_including_trailing_spaces(monkeypatch):
    _on(monkeypatch, CountingFactory())
    fence = "```python\ndef f():   \n    return 1  \n```"
    text = f"Plain words before the block here. {fence} And some words after the block."
    assert fence in compress.compress_text(text).text


# 4 memo hit determinism
def test_04_memo_hit_returns_the_first_result_and_skips_the_compressor(monkeypatch):
    factory = CountingFactory()
    _on(monkeypatch, factory)
    first = compress.compress_text("plain prose with no protected parts at all")
    second = compress.compress_text("plain prose with no protected parts at all")
    assert second.text == first.text
    assert second.memo_hit is True
    assert len(factory.compressor.calls) == 1


def test_04b_changing_rate_or_model_is_a_miss(monkeypatch):
    factory = CountingFactory()
    _on(monkeypatch, factory)
    text = "plain prose with no protected parts at all"
    compress.compress_text(text)
    monkeypatch.setenv("NAVIGATOR_COMPRESSION_RATE", "0.5")
    assert compress.compress_text(text).memo_hit is False
    monkeypatch.setenv("NAVIGATOR_LLMLINGUA_MODEL", "other/model")
    assert compress.compress_text(text).memo_hit is False


def test_04c_same_state_renders_byte_identically_with_compression_on(monkeypatch):
    _on(monkeypatch, CountingFactory())
    st = S.create_session("pair", "Add retry to fetch_user")
    st.decided.append(S.Decided(text="Use backoff", rationale=PROTECTED_EXAMPLE))
    transform = compress.make_transform()
    assert render_summary(st, transform) == render_summary(st, transform)


# 5 memo before load
def test_05_a_primed_memo_is_used_without_loading_the_model(monkeypatch):
    factory = CountingFactory()
    _on(monkeypatch, factory)
    text = "plain prose with no protected parts at all"
    primed = compress.compress_text(text)
    compress.forget_loaded_model()  # the model is gone, but the memo stays
    monkeypatch.setattr(compress, "_get_compressor", lambda cfg: (_ for _ in ()).throw(AssertionError("loaded")))
    again = compress.compress_text(text)
    assert again.text == primed.text
    assert again.memo_hit is True


# 6 llmlingua missing (ImportError)
def test_06_missing_llmlingua_is_unavailable_and_attempted_once(monkeypatch):
    monkeypatch.setenv("NAVIGATOR_COMPRESSION", "on")
    monkeypatch.setitem(sys.modules, "llmlingua", None)  # import llmlingua raises ImportError
    for _ in range(3):
        assert compress.compress_text(PROTECTED_EXAMPLE).text == PROTECTED_EXAMPLE
    status = compress.status()
    assert status["state"] == "unavailable"
    assert status["reason"]
    assert compress.load_attempts() == 1


# 7 model load fails
def test_07_model_load_failure_is_unavailable_and_attempted_once(monkeypatch):
    factory = CountingFactory(exc=OSError("no cache, no network"))
    _on(monkeypatch, factory)
    for _ in range(3):
        assert compress.compress_text(PROTECTED_EXAMPLE).text == PROTECTED_EXAMPLE
    assert compress.status()["state"] == "unavailable"
    assert factory.calls == 1


# 8 off
def test_08_off_is_identity_and_never_touches_the_loader(monkeypatch):
    factory = CountingFactory()
    compress.set_factory(factory)
    monkeypatch.setenv("NAVIGATOR_COMPRESSION", "off")
    assert compress.compress_text(PROTECTED_EXAMPLE).text == PROTECTED_EXAMPLE
    assert compress.make_transform() is None
    assert factory.calls == 0
    assert compress.status()["state"] == "off"


# 9 bad config
@pytest.mark.parametrize("rate", ["abc", "0"])
def test_09_bad_rate_is_unavailable_and_uncompressed(monkeypatch, rate):
    factory = CountingFactory()
    _on(monkeypatch, factory, rate=rate)
    assert compress.compress_text(PROTECTED_EXAMPLE).text == PROTECTED_EXAMPLE
    status = compress.status()
    assert status["state"] == "unavailable"
    assert "NAVIGATOR_COMPRESSION_RATE" in status["reason"]
    assert factory.calls == 0


# 10 prefix stability with compression on
def _messages_slice(body: bytes) -> bytes:
    start = body.index(b'"messages":[')
    return body[start : body.index(b'],"model":')]


async def test_10_request_prefix_is_byte_stable_with_compression_on(monkeypatch):
    _on(monkeypatch, CountingFactory())
    st = S.create_session("pair", "Add retry to fetch_user")
    st.decided.append(S.Decided(text="Use backoff", rationale=PROTECTED_EXAMPLE))
    fake = FakeOllama(
        [call("read_file", path="a", justification="j1"), call("read_file", path="b", justification="j2"), decide(decision="x", carry_forward=[])]
    )
    from fakes import StubMode as _Stub  # the stub answer tool is decide

    rt = Runtime(
        client=OllamaClient("http://ollama.test", "qwen-agent:latest", transport=fake.transport()),
        mode=_Stub(),
        transform=compress.make_transform(),
    )
    await ask(st, AskArgs(question="Q?", answer_tool="decide"), rt)
    await submit(st, SubmitArgs(results=[Result(call_id="call-0001", status="ran", content="a")]), rt)
    await submit(st, SubmitArgs(results=[Result(call_id="call-0002", status="ran", content="b")]), rt)
    assert len(fake.requests) == 3
    for earlier, later in zip(fake.requests, fake.requests[1:]):
        assert _messages_slice(later).startswith(_messages_slice(earlier))


# 11 status counters
def test_11_status_shows_misses_then_hits(monkeypatch):
    _on(monkeypatch, CountingFactory())
    text = "plain prose with no protected parts at all"
    compress.compress_text(text)
    assert compress.status()["memo_misses"] >= 1
    before = compress.status()["memo_hits"]
    compress.compress_text(text)
    assert compress.status()["memo_hits"] == before + 1


def test_11b_navigator_status_reports_the_compression_object(monkeypatch):
    from navigator_mcp import server

    _on(monkeypatch, CountingFactory())
    session_id = S.create_session("pair", "Goal").session_id
    text = server.navigator_status(session_id)
    assert "COMPRESSION: " in text
    payload = json.loads(text.split("COMPRESSION: ", 1)[1])
    assert payload["enabled"] is True
    assert payload["state"] == "active"


# 12 real model, only when the extra is installed
@pytest.mark.slow
def test_12_real_model_keeps_protected_spans():
    pytest.importorskip("llmlingua")
    monkeypatch_env = {"NAVIGATOR_COMPRESSION": "on"}
    import os

    os.environ.update(monkeypatch_env)
    try:
        compress.reset()
        out = compress.compress_text(PROTECTED_EXAMPLE).text
        assert "server/src/navigator_mcp/ollama.py:120-140" in out
        assert "`render(state)`" in out
    finally:
        os.environ.pop("NAVIGATOR_COMPRESSION", None)
        compress.reset()
