"""Phase 3b tests: job queue, MCP Tasks extension, cancel, deadline, 503 backoff, fallback, recovery.

Phase doc: docs/phases/phase-3b-async-queue.md section 9, tests 1-34. Test 20 (navigator-wait) is in test_wait.py;
test 35 (stdio) is in test_stdio_tasks.py.
"""

import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import anyio
import pytest
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.exceptions import MCPError

from fakes import FakeClock, FakeOllama, Status, StubMode, call, decide, empty
from navigator_mcp import jobs as J
from navigator_mcp import ollama
from navigator_mcp import server as srv
from navigator_mcp import session as S
from navigator_mcp.jobs import ActiveJobError, CancelTaskParams, GetTaskParams, JobManager, TasksExtension, UpdateTaskParams
from navigator_mcp.ollama import Answered, AskArgs, Error, OllamaClient, Result, SubmitArgs

pytestmark = pytest.mark.anyio

EXT_ID = "io.modelcontextprotocol/tasks"
OPT_IN = {"io.modelcontextprotocol/clientCapabilities": {"extensions": {EXT_ID: {}}}}
TASK_KEYS = {"resultType", "taskId", "status", "statusMessage", "createdAt", "lastUpdatedAt", "ttlMs", "pollIntervalMs"}


def manager(fake, clock, *, job_timeout=None, backoff=None, retries=8) -> JobManager:
    return JobManager(
        job_timeout=job_timeout,
        clock=clock.now,
        sleep=clock.sleep,
        backoff_sleep=backoff or clock.virtual_sleep,
        client_factory=lambda: OllamaClient("http://ollama.test", "qwen-agent:latest", transport=fake.transport()),
        mode_for=lambda name: StubMode(),
        max_503_retries=retries,
    )


def ctx(name="navigator_ask", meta=None, caps=None, protocol="2026-07-28", arguments=None):
    return SimpleNamespace(
        params={"name": name, "arguments": arguments or {}, "_meta": meta or {}},
        protocol_version=protocol,
        session=SimpleNamespace(client_capabilities=SimpleNamespace(extensions=caps or {})),
    )


def tool_params(name: str, arguments: dict):
    return SimpleNamespace(name=name, arguments=arguments)


def new_session() -> str:
    return S.create_session("pair", "Goal").session_id


def ask_args(question: str = "Which?") -> AskArgs:
    return AskArgs(question=question, answer_tool="decide")


@asynccontextmanager
async def workers(jobs: JobManager):
    async with anyio.create_task_group() as tg:
        tg.start_soon(jobs.worker)
        try:
            yield
        finally:
            tg.cancel_scope.cancel()


async def until(pred, steps: int = 5000) -> None:
    for _ in range(steps):
        if pred():
            return
        await anyio.sleep(0)
    raise AssertionError("condition not reached")


# 1 queued, running, completed
async def test_01_queued_then_running_then_completed():
    fake = FakeOllama([decide(decision="x", carry_forward=[])])
    fake.hold()
    jobs = manager(fake, FakeClock())
    job = jobs.enqueue(new_session(), "ask", ask_args())
    assert job.state == "queued"
    async with workers(jobs):
        await until(lambda: job.state == "running")
        fake.release()
        await job.settled.wait()
    assert job.state == "completed"
    assert isinstance(job.outcome, Answered)


# 2 statusMessage sequence
async def test_02_stage_sequence_for_a_bounce_then_answer():
    fake = FakeOllama([call("read_file", path="a"), decide(decision="x", carry_forward=[])])
    jobs = manager(fake, FakeClock())
    job = jobs.enqueue(new_session(), "ask", ask_args())
    async with workers(jobs):
        await job.settled.wait()
    assert job.stage_history == ["waiting on navigator (request-1)", "bounce/resend 1"]
    assert job.state == "completed"


# 3 queued position
async def test_03_queued_position_is_reported_by_tasks_get():
    jobs = manager(FakeOllama([]), FakeClock())
    ext = TasksExtension(jobs)
    job = jobs.enqueue(new_session(), "ask", ask_args())
    got = await ext._get(ctx(), GetTaskParams(taskId=job.job_id))
    assert got["status"] == "working"
    assert got["statusMessage"] == "queued (position 1)"


# 4 tasks path, opted in
async def test_04_opted_in_call_returns_create_task_without_running_the_tool():
    jobs = manager(FakeOllama([]), FakeClock())
    ext = TasksExtension(jobs)
    called = []

    async def call_next(c):
        called.append(c)
        return "NEXT"

    sid = new_session()
    args = {"session_id": sid, "question": "Q?", "answer_tool": "decide"}
    result = await ext.intercept_tool_call(tool_params("navigator_ask", args), ctx(meta=OPT_IN, arguments=args), call_next)
    assert set(result) == TASK_KEYS
    assert result["resultType"] == "task"
    assert result["status"] == "working"
    assert result["ttlMs"] is None
    assert result["pollIntervalMs"] == 5000
    assert called == []
    assert jobs.get(result["taskId"]) is not None


# 5 opted in via capabilities only
async def test_05_opt_in_through_client_capabilities():
    jobs = manager(FakeOllama([]), FakeClock())
    ext = TasksExtension(jobs)
    sid = new_session()
    args = {"session_id": sid, "question": "Q?", "answer_tool": "decide"}

    async def call_next(c):
        raise AssertionError("must not be called")

    result = await ext.intercept_tool_call(
        tool_params("navigator_ask", args), ctx(caps={EXT_ID: {}}, arguments=args), call_next
    )
    assert set(result) == TASK_KEYS


# 6 not opted in
async def test_06_not_opted_in_falls_through_and_returns_queued():
    jobs = manager(FakeOllama([]), FakeClock())
    ext = TasksExtension(jobs)
    sid = new_session()
    calls = []

    async def call_next(c):
        calls.append(c)
        return jobs.enqueue_queued(sid, "ask", ask_args())

    result = await ext.intercept_tool_call(
        tool_params("navigator_ask", {"session_id": sid, "question": "Q?", "answer_tool": "decide"}),
        ctx(arguments={"session_id": sid}),
        call_next,
    )
    assert len(calls) == 1
    assert result["status"] == "queued"
    assert "taskId" not in result


# 7 other tool opted in
async def test_07_other_tool_is_not_intercepted():
    jobs = manager(FakeOllama([]), FakeClock())
    ext = TasksExtension(jobs)
    calls = []

    async def call_next(c):
        calls.append(c)
        return {"ok": True}

    await ext.intercept_tool_call(tool_params("navigator_status", {"session_id": "x"}), ctx(meta=OPT_IN, name="navigator_status"), call_next)
    assert len(calls) == 1
    assert jobs.active() is None


# 8 tasks/get completed
async def test_08_tasks_get_completed_carries_the_outcome_and_is_fetched_once():
    fake = FakeOllama([decide(decision="x", carry_forward=[])])
    jobs = manager(fake, FakeClock())
    ext = TasksExtension(jobs)
    job = jobs.enqueue(new_session(), "ask", ask_args())
    async with workers(jobs):
        await job.settled.wait()
    got = await ext._get(ctx(meta=OPT_IN), GetTaskParams(taskId=job.job_id))
    assert got["resultType"] == "complete"
    assert got["status"] == "completed"
    assert got["statusMessage"] == "completed"
    assert got["result"]["isError"] is False
    assert json.loads(got["result"]["content"][0]["text"]) == job.outcome.model_dump(mode="json")
    with pytest.raises(MCPError) as info:
        await ext._get(ctx(meta=OPT_IN), GetTaskParams(taskId=job.job_id))
    assert info.value.code == -32602


# 9 completed with an error outcome
async def test_09_two_empty_replies_complete_with_is_error():
    fake = FakeOllama([empty(), empty()])
    jobs = manager(fake, FakeClock())
    ext = TasksExtension(jobs)
    job = jobs.enqueue(new_session(), "ask", ask_args())
    async with workers(jobs):
        await job.settled.wait()
    got = await ext._get(ctx(meta=OPT_IN), GetTaskParams(taskId=job.job_id))
    assert got["status"] == "completed"
    assert got["result"]["isError"] is True


# 10 failed job
async def test_10_worker_exception_fails_the_task(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(ollama, "ask", boom)
    jobs = manager(FakeOllama([]), FakeClock())
    ext = TasksExtension(jobs)
    job = jobs.enqueue(new_session(), "ask", ask_args())
    async with workers(jobs):
        await job.settled.wait()
    got = await ext._get(ctx(meta=OPT_IN), GetTaskParams(taskId=job.job_id))
    assert got["status"] == "failed"
    assert got["error"] == {"code": -32603, "message": "boom"}
    assert "result" not in got


# 11 unknown task id
async def test_11_unknown_task_id_is_minus_32602():
    ext = TasksExtension(manager(FakeOllama([]), FakeClock()))
    for handler, params in [
        (ext._get, GetTaskParams(taskId="nope")),
        (ext._cancel, CancelTaskParams(taskId="nope")),
        (ext._update, UpdateTaskParams(taskId="nope", inputResponses={})),
    ]:
        with pytest.raises(MCPError) as info:
            await handler(ctx(), params)
        assert info.value.code == -32602


# 12 tasks/update
async def test_12_tasks_update_on_known_task_is_an_empty_ack():
    jobs = manager(FakeOllama([]), FakeClock())
    ext = TasksExtension(jobs)
    job = jobs.enqueue(new_session(), "ask", ask_args())
    assert await ext._update(ctx(), UpdateTaskParams(taskId=job.job_id, inputResponses={"k": {}})) == {}
    assert job.state == "queued"


# 13 non-declaring client on tasks/get
async def test_13_tasks_get_without_opt_in_still_answers():
    jobs = manager(FakeOllama([]), FakeClock())
    ext = TasksExtension(jobs)
    job = jobs.enqueue(new_session(), "ask", ask_args())
    got = await ext._get(ctx(), GetTaskParams(taskId=job.job_id))
    assert got["taskId"] == job.job_id


# 14 fallback queued
async def test_14_fallback_returns_queued_with_absolute_result_path():
    fake = FakeOllama([decide(decision="x", carry_forward=[])])
    fake.hold()
    jobs = manager(fake, FakeClock())
    sid = new_session()
    async with workers(jobs):
        queued = jobs.enqueue_queued(sid, "ask", ask_args())
        await until(lambda: jobs.get(queued["job_id"]).state == "running")
        assert queued["status"] == "queued"
        assert Path(queued["result_path"]).is_absolute()
        assert "navigator-wait" in queued["wait_command"]
        jobs_dir = S.state_root() / "jobs"
        assert (jobs_dir / f"{queued['job_id']}.pending.json").exists()
        assert not Path(queued["result_path"]).exists()
        fake.release()
        await jobs.get(queued["job_id"]).settled.wait()


# 15 result file
async def test_15_result_file_matches_the_task_path_outcome():
    fake = FakeOllama([decide(decision="x", carry_forward=[]), decide(decision="x", carry_forward=[])])
    jobs = manager(fake, FakeClock())
    async with workers(jobs):
        queued = jobs.enqueue_queued(new_session(), "ask", ask_args())
        fallback_job = jobs.get(queued["job_id"])
        await fallback_job.settled.wait()
        task_job = jobs.enqueue(new_session(), "ask", ask_args())
        await task_job.settled.wait()
    result = json.loads(Path(queued["result_path"]).read_text())
    assert result["state"] == "completed"
    assert result["outcome"] == task_job.outcome.model_dump(mode="json")
    assert not (S.state_root() / "jobs" / f"{queued['job_id']}.pending.json").exists()


# 16 cancel queued
async def test_16_cancel_queued_job_leaves_session_untouched_and_is_skipped():
    fake = FakeOllama([decide(decision="x", carry_forward=[])])
    jobs = manager(fake, FakeClock())
    ext = TasksExtension(jobs)
    sid = new_session()
    before = S.load_state(sid).model_dump()
    job = jobs.enqueue(sid, "ask", ask_args())
    assert await ext._cancel(ctx(), CancelTaskParams(taskId=job.job_id)) == {}
    assert job.state == "cancelled"
    async with workers(jobs):
        await anyio.sleep(0)
        await anyio.sleep(0)
    assert fake.requests == []
    assert S.load_state(sid).model_dump() == before


# 17 cancel running
async def test_17_cancel_running_job_restores_session_and_moves_audit_files():
    fake = FakeOllama([decide(decision="x", carry_forward=[])])
    fake.hold()
    jobs = manager(fake, FakeClock())
    ext = TasksExtension(jobs)
    sid = new_session()
    before = S.load_state(sid).model_dump()
    job = jobs.enqueue(sid, "ask", ask_args())
    async with workers(jobs):
        await until(lambda: job.state == "running" and fake.requests)
        assert await ext._cancel(ctx(), CancelTaskParams(taskId=job.job_id)) == {}
        await job.settled.wait()
    assert fake.cancel_seen
    assert job.state == "cancelled"
    assert S.load_state(sid).model_dump() == before
    session_dir = S.session_dir(sid)
    assert (session_dir / f"cancelled-{job.job_id}" / "request-1.json").exists()
    assert not (session_dir / "request-1.json").exists()

    # the next ask writes request-1.json again, because the counter was restored
    async with workers(jobs):
        nxt = jobs.enqueue(sid, "ask", ask_args())
        await nxt.settled.wait()
    assert (session_dir / "request-1.json").exists()


# 18 navigator_cancel
async def test_18_navigator_cancel_returns_the_state_after_cancel(monkeypatch):
    fake = FakeOllama([decide(decision="x", carry_forward=[])])
    fake.hold()
    jobs = manager(fake, FakeClock())
    monkeypatch.setattr(srv, "jobs", jobs)
    job = jobs.enqueue(new_session(), "ask", ask_args())
    async with workers(jobs):
        await until(lambda: job.state == "running" and fake.requests)
        reply = await srv.navigator_cancel(job.job_id)
    assert reply == {"job_id": job.job_id, "state": "cancelled"}


# 19 cancel terminal
async def test_19_cancel_of_a_completed_job_is_a_no_op():
    fake = FakeOllama([decide(decision="x", carry_forward=[])])
    jobs = manager(fake, FakeClock())
    ext = TasksExtension(jobs)
    job = jobs.enqueue(new_session(), "ask", ask_args())
    async with workers(jobs):
        await job.settled.wait()
    assert await ext._cancel(ctx(), CancelTaskParams(taskId=job.job_id)) == {}
    assert job.state == "completed"


# 21 timeout unset
async def test_21_no_deadline_means_no_watcher_and_no_timeout():
    fake = FakeOllama([decide(decision="x", carry_forward=[])])
    fake.hold()
    clock = FakeClock()
    jobs = manager(fake, clock)
    job = jobs.enqueue(new_session(), "ask", ask_args())
    async with workers(jobs):
        await until(lambda: job.state == "running" and fake.requests)
        clock.advance(10**7)
        await anyio.sleep(0)
        assert job.state == "running"
        assert job.deadline is None
        assert clock.waiters == []
        assert fake.timeouts[0] == {"connect": None, "read": None, "write": None, "pool": None}
        fake.release()
        await job.settled.wait()


# 22 timeout set
async def test_22_deadline_expiry_cancels_and_restores_the_session():
    fake = FakeOllama([decide(decision="x", carry_forward=[])])
    fake.hold()
    clock = FakeClock()
    jobs = manager(fake, clock, job_timeout=30)
    sid = new_session()
    before = S.load_state(sid).model_dump()
    job = jobs.enqueue(sid, "ask", ask_args())
    async with workers(jobs):
        await until(lambda: job.state == "running" and fake.requests and clock.waiters)
        clock.advance(31)
        await job.settled.wait()
    assert job.outcome == Error(status="error", reason="timeout", stage="waiting on navigator (request-1)")
    assert fake.cancel_seen
    assert S.load_state(sid).model_dump() == before


# 23 derived httpx timeout
async def test_23_request_timeout_is_the_remaining_deadline():
    fake = FakeOllama([decide(decision="x", carry_forward=[])])
    fake.hold()
    clock = FakeClock()
    jobs = manager(fake, clock, job_timeout=30)
    job = jobs.enqueue(new_session(), "ask", ask_args())
    clock.advance(10)
    async with workers(jobs):
        await until(lambda: job.state == "running" and fake.requests)
        assert fake.timeouts[0] == {"connect": 20.0, "read": 20.0, "write": 20.0, "pool": 20.0}
        fake.release()
        await job.settled.wait()


# 24 timeout while queued
async def test_24_timeout_while_queued_sends_nothing():
    fake = FakeOllama([decide(decision="x", carry_forward=[])])
    clock = FakeClock()
    jobs = manager(fake, clock, job_timeout=30)
    job = jobs.enqueue(new_session(), "ask", ask_args())
    clock.advance(31)
    async with workers(jobs):
        await job.settled.wait()
    assert job.outcome == Error(status="error", reason="timeout", stage="queued")
    assert fake.requests == []


# 25 503 retry
async def test_25_503_is_retried_with_identical_bytes_and_backoff():
    fake = FakeOllama([decide(decision="x", carry_forward=[])])
    fake.script_status(503, 3)
    clock = FakeClock()
    sleeps: list[float] = []

    async def record(dt):
        sleeps.append(dt)
        clock.advance(dt)

    jobs = manager(fake, clock, backoff=record)
    sid = new_session()
    job = jobs.enqueue(sid, "ask", ask_args())
    async with workers(jobs):
        await job.settled.wait()
    assert len(fake.requests) == 4
    assert len(set(fake.requests)) == 1
    assert sleeps == [1, 2, 4]
    assert isinstance(job.outcome, Answered)
    assert (S.session_dir(sid) / "request-1.json").exists()


# 26 503 cap
async def test_26_503_retry_cap_ends_the_job_with_busy_error():
    fake = FakeOllama([decide(decision="x", carry_forward=[])])
    fake.script_status(503, 9)
    clock = FakeClock()
    sleeps: list[float] = []

    async def record(dt):
        sleeps.append(dt)
        clock.advance(dt)

    jobs = manager(fake, clock, backoff=record, retries=8)
    job = jobs.enqueue(new_session(), "ask", ask_args())
    async with workers(jobs):
        await job.settled.wait()
    assert sleeps == [1, 2, 4, 8, 16, 30, 30, 30]
    assert job.outcome.status == "error"
    assert job.outcome.reason.startswith("ollama busy (503)")
    assert "maximum pending requests exceeded" in job.outcome.reason
    assert len(fake.requests) == 9


# 27 503 with deadline
async def test_27_503_backoff_never_sends_after_the_deadline():
    fake = FakeOllama([decide(decision="x", carry_forward=[])])
    fake.script_status(503, 10)
    clock = FakeClock()
    jobs = manager(fake, clock, job_timeout=5)
    job = jobs.enqueue(new_session(), "ask", ask_args())
    async with workers(jobs):
        await job.settled.wait()
    assert job.outcome.reason == "timeout"
    assert job.outcome.stage.startswith("waiting on navigator")
    assert len(fake.requests) == 3


# 28 503 cancelled during backoff
async def test_28_cancel_during_backoff_restores_the_session():
    fake = FakeOllama([decide(decision="x", carry_forward=[])])
    fake.script_status(503, 1)
    entered = anyio.Event()

    async def blocked(dt):
        entered.set()
        await anyio.sleep_forever()

    jobs = manager(fake, FakeClock(), backoff=blocked)
    ext = TasksExtension(jobs)
    sid = new_session()
    before = S.load_state(sid).model_dump()
    job = jobs.enqueue(sid, "ask", ask_args())
    async with workers(jobs):
        await entered.wait()
        await ext._cancel(ctx(), CancelTaskParams(taskId=job.job_id))
        await job.settled.wait()
    assert job.state == "cancelled"
    assert S.load_state(sid).model_dump() == before


# 29 second ask while active
async def test_29_second_ask_or_submit_while_active_is_a_normal_error_result():
    fake = FakeOllama([decide(decision="x", carry_forward=[])])
    fake.hold()
    jobs = manager(fake, FakeClock())
    ext = TasksExtension(jobs)
    first_sid, other_sid = new_session(), new_session()
    args = {"session_id": first_sid, "question": "Q?", "answer_tool": "decide"}

    async def call_next(c):
        return jobs.enqueue_queued(c.params["arguments"]["session_id"], "ask", ask_args())

    async with workers(jobs):
        task = await ext.intercept_tool_call(tool_params("navigator_ask", args), ctx(meta=OPT_IN, arguments=args), call_next)
        job1 = jobs.get(task["taskId"])
        await until(lambda: job1.state == "running" and fake.requests)
        second_args = {"session_id": other_sid, "question": "Q2?", "answer_tool": "decide"}
        second = await ext.intercept_tool_call(
            tool_params("navigator_ask", second_args), ctx(meta=OPT_IN, arguments=second_args), call_next
        )
        assert second == {"status": "error", "reason": f"job {job1.job_id} active"}
        submit = jobs.enqueue_queued(other_sid, "submit", SubmitArgs(results=[Result(call_id="call-0001", status="ran", content="x")]))
        assert submit == {"status": "error", "reason": f"job {job1.job_id} active"}
        fake.release()
        await job1.settled.wait()
    assert jobs.active() is None
    assert isinstance(jobs.enqueue(other_sid, "ask", ask_args()), J.Job)


# 30 active slot before phase checks
async def test_30_active_slot_is_checked_before_session_phase():
    fake = FakeOllama([decide(decision="x", carry_forward=[])])
    fake.hold()
    jobs = manager(fake, FakeClock())
    busy_sid = new_session()
    other = new_session()
    state = S.load_state(other)
    state.phase = "AWAITING_SUBMIT"
    S.save_state(state)
    async with workers(jobs):
        job1 = jobs.enqueue(busy_sid, "ask", ask_args())
        await until(lambda: job1.state == "running" and fake.requests)
        result = jobs.enqueue_queued(other, "ask", ask_args())
        assert result["reason"] == f"job {job1.job_id} active"
        fake.release()
        await job1.settled.wait()


# 31 unfetched completed job is not active
async def test_31_completed_but_unfetched_job_does_not_block_a_new_ask():
    fake = FakeOllama([decide(decision="x", carry_forward=[]), decide(decision="x", carry_forward=[])])
    jobs = manager(fake, FakeClock())
    job1 = jobs.enqueue(new_session(), "ask", ask_args())
    async with workers(jobs):
        await job1.settled.wait()
    assert job1.state == "completed"
    job2 = jobs.enqueue(new_session(), "ask", ask_args())
    assert job2.state == "queued"


# 32 recovery
async def test_32_recovery_fails_stale_jobs_and_restores_phase():
    jobs = manager(FakeOllama([]), FakeClock())
    sid_a, sid_b = new_session(), new_session()
    state_a = S.load_state(sid_a)
    state_a.phase = "THINKING"
    S.save_state(state_a)
    for sid, job_id, state_name, phase in [(sid_a, "a" * 32, "running", "AWAITING_SUBMIT"), (sid_b, "b" * 32, "queued", "IDLE")]:
        payload = {
            "job_id": job_id, "kind": "ask", "state": state_name, "created_at": "2026-11-02T00:00:00Z",
            "finished_at": None, "phase_before": phase, "failure": None,
        }
        (S.session_dir(sid) / f"job-{job_id}.json").write_text(json.dumps(payload))
    jobs.recover()
    for sid, job_id, phase in [(sid_a, "a" * 32, "AWAITING_SUBMIT"), (sid_b, "b" * 32, "IDLE")]:
        data = json.loads((S.session_dir(sid) / f"job-{job_id}.json").read_text())
        assert data["state"] == "failed"
        assert data["failure"] == "server restarted"
        assert S.load_state(sid).phase == phase


# 33 lifespan
async def test_33_lifespan_starts_and_stops_the_worker_and_exposes_jobs():
    async with srv.lifespan(srv.mcp) as context:
        assert context["jobs"] is srv.jobs


# 34 registration
async def test_34_extension_registers_with_the_server_and_advertises_empty_settings():
    jobs = manager(FakeOllama([]), FakeClock())
    app = MCPServer("navigator", extensions=[TasksExtension(jobs)])
    assert TasksExtension.identifier == EXT_ID
    assert TasksExtension(jobs).settings() == {}
    for method in ["tasks/get", "tasks/cancel", "tasks/update"]:
        assert app._lowlevel_server.get_request_handler(method) is not None


# 17b. Session restore compares against a snapshot taken when the job started (section 2.4 phase 3 state)
async def test_session_restore_uses_the_job_start_snapshot():
    fake = FakeOllama([decide(decision="x", carry_forward=[])])
    fake.hold()
    jobs = manager(fake, FakeClock())
    sid = new_session()
    job = jobs.enqueue(sid, "ask", ask_args())
    async with workers(jobs):
        await until(lambda: job.state == "running" and fake.requests)
        assert job.snapshot is not None
        assert job.snapshot.phase == "IDLE"
        fake.release()
        await job.settled.wait()


# docs/fix-navigator-record.md sections 2, 4 and 6: a record during a job is refused with text the driver can act on,
# and status shows the job.
async def test_record_during_a_running_job_is_a_readable_tool_error(monkeypatch):
    fake = FakeOllama([decide(decision="x", carry_forward=[])])
    fake.hold()
    jobs = manager(fake, FakeClock())
    monkeypatch.setattr(srv, "jobs", jobs)
    sid = new_session()
    job = jobs.enqueue(sid, "ask", ask_args())
    async with workers(jobs):
        await until(lambda: job.state == "running" and fake.requests)
        with pytest.raises(ToolError) as info:
            srv.navigator_record(sid, "decided", {"text": "x"})
        assert f"job {job.job_id} is still running for this session" in str(info.value)
        assert "wait for its result, then record" in str(info.value)
        fake.release()
        await job.settled.wait()


async def test_status_shows_the_queued_job_of_the_session(monkeypatch):
    jobs = manager(FakeOllama([]), FakeClock())
    monkeypatch.setattr(srv, "jobs", jobs)
    sid = new_session()
    job = jobs.enqueue(sid, "ask", ask_args())  # no worker: the job stays queued
    status = srv.navigator_status(sid)
    assert f"JOB: {job.job_id} queued queued (position 1)" in status.splitlines()


async def test_status_has_no_job_line_when_the_session_has_no_job(monkeypatch):
    monkeypatch.setattr(srv, "jobs", manager(FakeOllama([]), FakeClock()))
    assert "JOB:" not in srv.navigator_status(new_session())
