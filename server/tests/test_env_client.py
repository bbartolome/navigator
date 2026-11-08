"""Phase 7 tests: provider selection in _env_client and in the job layer (docs/phases/phase-7-anthropic-provider.md,
section 10, tests 1-4).

Plan: .scratch/phase-7-anthropic-provider/plan.md, section 2.6. The D-tests cover plan decisions D1 (a client factory
error fails the job, not the worker) and D5 (missing credentials fail the job at request time). No test reaches Ollama
or the Claude API: the Ollama client gets a FakeOllama transport, the SDK's send is blocked, and HOME is an empty
directory so the SDK finds no profile (plan D15).
"""

import os

import anyio
import httpx2
import pytest

from fakes import FakeClock, FakeOllama, StubMode, decide
from navigator_mcp import jobs as J
from navigator_mcp import ollama
from navigator_mcp import session as S
from navigator_mcp.anthropic_client import AnthropicClient
from navigator_mcp.jobs import GetTaskParams, JobManager, TasksExtension
from navigator_mcp.ollama import Answered, OllamaClient
from test_jobs import OPT_IN, ask_args, ctx, new_session, workers

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("isolated_home")]

OLLAMA_URL = "http://host.docker.internal:11434"
OLLAMA_MODEL = "qwen-agent:latest"
OPENAI_ERROR = "NAVIGATOR_PROVIDER must be ollama or anthropic, not 'openai'"


def env_manager() -> JobManager:
    """test_jobs.manager with no client_factory, so each job builds its client with the real _env_client."""
    clock = FakeClock()
    return JobManager(
        clock=clock.now,
        sleep=clock.sleep,
        backoff_sleep=clock.virtual_sleep,
        mode_for=lambda name: StubMode(),
    )


def state_bytes(sid: str) -> bytes:
    return (S.session_dir(sid) / "state.json").read_bytes()


# 1 unset, empty and ollama select OllamaClient with the defaults from before phase 7
@pytest.mark.parametrize("value", [None, "", "ollama", " Ollama "])
def test_p7_01_unset_empty_or_ollama_gives_ollama_with_the_old_defaults(monkeypatch, value):
    if value is not None:
        monkeypatch.setenv("NAVIGATOR_PROVIDER", value)
    client = J._env_client()
    assert type(client) is OllamaClient
    assert client.model == OLLAMA_MODEL
    assert str(client._http.base_url).rstrip("/") == OLLAMA_URL


def test_p7_01_ollama_still_reads_its_url_and_model(monkeypatch):
    monkeypatch.setenv("NAVIGATOR_PROVIDER", "ollama")
    monkeypatch.setenv("NAVIGATOR_OLLAMA_URL", "http://ollama.test:1234")
    monkeypatch.setenv("NAVIGATOR_MODEL", "other-model:7b")
    client = J._env_client()
    assert type(client) is OllamaClient
    assert client.model == "other-model:7b"
    assert str(client._http.base_url).rstrip("/") == "http://ollama.test:1234"


# 2 anthropic selects AnthropicClient with the default model, and needs no key to construct
def test_p7_02_anthropic_gives_anthropic_client_with_the_default_model_and_no_key(monkeypatch):
    monkeypatch.setenv("NAVIGATOR_PROVIDER", "anthropic")
    assert "ANTHROPIC_API_KEY" not in os.environ
    client = J._env_client()
    assert type(client) is AnthropicClient
    assert client.model == "claude-opus-5-5"
    assert client._sdk.api_key is None  # the missing key fails at request time (D5 test), not here


# 3 NAVIGATOR_MODEL applies to the anthropic provider
def test_p7_03_anthropic_uses_navigator_model(monkeypatch):
    monkeypatch.setenv("NAVIGATOR_PROVIDER", "anthropic")
    monkeypatch.setenv("NAVIGATOR_MODEL", "claude-sonnet-5-5")
    client = J._env_client()
    assert type(client) is AnthropicClient
    assert client.model == "claude-sonnet-5-5"


# 4 an unknown provider raises; the message has the normalized value (D12)
@pytest.mark.parametrize("value", ["openai", " OpenAI "])
def test_p7_04_unknown_provider_raises_value_error(monkeypatch, value):
    monkeypatch.setenv("NAVIGATOR_PROVIDER", value)
    with pytest.raises(ValueError) as info:
        J._env_client()
    assert str(info.value) == OPENAI_ERROR


# 4 and D1: the job fails with the same message, the session is restored, and the same worker then runs a valid job
async def test_p7_04_d1_unknown_provider_fails_the_job_and_the_worker_runs_on(monkeypatch):
    fake = FakeOllama([decide(decision="x", carry_forward=[])])
    built: list[tuple[str, str]] = []

    class ScriptedOllama(OllamaClient):  # what _env_client's Ollama branch builds, answered by FakeOllama
        def __init__(self, base_url: str, model: str):
            built.append((base_url, model))
            super().__init__(base_url, model, transport=fake.transport())

    monkeypatch.setattr(ollama, "OllamaClient", ScriptedOllama)
    monkeypatch.setenv("NAVIGATOR_PROVIDER", "openai")
    jobs = env_manager()
    ext = TasksExtension(jobs)
    sid = new_session()
    before = state_bytes(sid)
    async with workers(jobs):
        bad = jobs.enqueue(sid, "ask", ask_args())
        with anyio.fail_after(10):
            await bad.settled.wait()
        assert bad.state == "failed"
        assert bad.failure == OPENAI_ERROR
        assert state_bytes(sid) == before
        assert built == []  # a typing error never falls back to Ollama
        got = await ext._get(ctx(meta=OPT_IN), GetTaskParams(taskId=bad.job_id))
        assert got["error"] == {"code": -32603, "message": OPENAI_ERROR}

        monkeypatch.setenv("NAVIGATOR_PROVIDER", "ollama")
        good = jobs.enqueue(sid, "ask", ask_args())
        with anyio.fail_after(10):
            await good.settled.wait()
    assert good.state == "completed"
    assert isinstance(good.outcome, Answered)
    assert built == [(OLLAMA_URL, OLLAMA_MODEL)]
    assert len(fake.requests) == 1


# D5: no credentials. The client constructs, the first request fails before any HTTP send, and the job rolls back.
async def test_p7_d5_no_credentials_fail_the_job_at_request_time_with_no_network(monkeypatch):
    monkeypatch.setenv("NAVIGATOR_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:9")  # a leak fails fast, with a different message
    assert "ANTHROPIC_API_KEY" not in os.environ
    sent: list = []

    async def no_send(self, request, **kwargs):
        sent.append(request)
        raise AssertionError("the SDK sent an HTTP request")

    monkeypatch.setattr(httpx2.AsyncClient, "send", no_send)
    jobs = env_manager()
    sid = new_session()
    before = state_bytes(sid)
    async with workers(jobs):
        job = jobs.enqueue(sid, "ask", ask_args())
        with anyio.fail_after(10):
            await job.settled.wait()
    assert job.state == "failed"
    assert "Could not resolve authentication" in job.failure
    assert sent == []
    assert state_bytes(sid) == before
    moved = S.session_dir(sid) / f"cancelled-{job.job_id}"
    assert (moved / "request-1.json").exists()  # the failure came at the send, after the request was written
    assert not (S.session_dir(sid) / "request-1.json").exists()
