"""Job queue, MCP Tasks extension, and the navigator-wait fallback (phase 3b).

Rules: docs/phases/phase-3b-async-queue.md. Decided 2026-11-02: call ids call-NNNN, request-N by ask_n, and a fourth
bounced call ends the ask.
"""

import json
import os
import shlex
import time
import uuid
from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anyio
from mcp.server.extension import Extension, MethodBinding
from mcp.shared.exceptions import MCPError
from mcp.types import CLIENT_CAPABILITIES_META_KEY, RequestParams
from pydantic import ValidationError

from . import compress, modes
from . import ollama
from . import session as S
from .ollama import AskArgs, Error, Outcome, SubmitArgs

TASKS_ID = "io.modelcontextprotocol/tasks"
PROTOCOL_2026 = "2026-07-28"
POLL_INTERVAL_MS = 5000
JOB_TOOLS = {"navigator_ask": "ask", "navigator_submit": "submit"}
TERMINAL = ("completed", "failed", "cancelled")
TASK_NOT_FOUND = -32602
INTERNAL_ERROR = -32603


class ActiveJobError(Exception):
    def __init__(self, job_id: str):
        super().__init__(f"job {job_id} active")
        self.job_id = job_id


class GetTaskParams(RequestParams):
    taskId: str


class CancelTaskParams(RequestParams):
    taskId: str


class UpdateTaskParams(RequestParams):
    taskId: str
    inputResponses: dict[str, Any] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json(path: Path, obj: Any) -> None:
    """Atomic write: temp file in the same directory, then os.replace (phase 2 persistence rule)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(obj))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _jobs_dir() -> Path:
    return S.state_root() / "jobs"


def _result_path(job_id: str) -> Path:
    return Path(os.path.abspath(_jobs_dir() / f"{job_id}.result.json"))


class Job:
    """One navigator_ask or navigator_submit run. Not serialized to the client except through task_fields()."""

    def __init__(self, job_id: str, session_id: str, kind: str, args: Any, phase_before: str, deadline: float | None):
        self.job_id = job_id
        self.session_id = session_id
        self.kind = kind
        self.args = args
        self.phase_before = phase_before
        self.deadline = deadline
        self.state = "queued"
        self.stage = "queued"
        self.stage_history: list[str] = []
        self.created_at = _now()
        self.last_updated_at = self.created_at
        self.finished_at: datetime | None = None
        self.snapshot: S.SessionState | None = None
        self.outcome: Outcome | None = None
        self.failure: str | None = None
        self.scope: anyio.CancelScope | None = None
        self.settled = anyio.Event()
        self.timed_out = False
        self.cancel_requested = False
        self.fetched = False
        self.files_before: set[str] = set()


class JobManager:
    """One process-wide FIFO with one worker (phase 3b section 2). Injected clock, sleep and client keep tests virtual."""

    def __init__(
        self,
        *,
        job_timeout: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Any] = anyio.sleep,
        backoff_sleep: Callable[[float], Any] | None = None,
        client_factory: Callable[[], ollama.OllamaClient] | None = None,
        mode_for: Callable[[str], Any] | None = None,
        max_503_retries: int = 8,
    ):
        self._job_timeout = job_timeout
        self._clock = clock
        self._sleep = sleep
        self._backoff_sleep = backoff_sleep or sleep
        self._client_factory = client_factory or _env_client
        self._mode_for = mode_for or modes.get
        self._max_503 = max_503_retries
        self._jobs: dict[str, Job] = {}
        self._queue: deque[Job] = deque()
        self._signal: anyio.Event | None = None

    @classmethod
    def from_env(cls) -> "JobManager":
        timeout = os.environ.get("NAVIGATOR_JOB_TIMEOUT")
        retries = int(os.environ.get("NAVIGATOR_503_RETRIES", "8"))
        return cls(job_timeout=float(timeout) if timeout else None, max_503_retries=retries)

    def configure(self, job_timeout: float | None) -> None:
        self._job_timeout = job_timeout

    # Queue

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def forget(self, job_id: str) -> None:
        self._jobs.pop(job_id, None)

    def active(self) -> Job | None:
        return next((j for j in self._jobs.values() if j.state in ("queued", "running")), None)

    def enqueue(self, session_id: str, kind: str, args: Any) -> Job:
        # Under the session lock, so a navigator_record that has just passed its active-job check cannot save over
        # the state this job starts from.
        with S.session_lock(session_id):
            return self._enqueue_locked(session_id, kind, args)

    def _enqueue_locked(self, session_id: str, kind: str, args: Any) -> Job:
        active = self.active()  # the active-slot check runs before any session check (section 2.3)
        if active is not None:
            raise ActiveJobError(active.job_id)
        try:
            state = S.load_state(session_id)
        except S.SessionNotFound:
            raise ValueError(f"unknown session_id: {session_id}") from None
        if self._mode_for(state.mode) is None:
            raise ValueError(f"no mode registered for {state.mode!r} yet")
        deadline = None if self._job_timeout is None else self._clock() + self._job_timeout
        job = Job(uuid.uuid4().hex, session_id, kind, args, state.phase, deadline)
        self._jobs[job.job_id] = job
        self._queue.append(job)
        job.stage = self._queued_stage(job)
        self._write_job_file(job)
        _write_json(_jobs_dir() / f"{job.job_id}.pending.json", {"job_id": job.job_id, "server_pid": os.getpid()})
        if self._signal is not None:
            self._signal.set()
        return job

    def enqueue_queued(self, session_id: str, kind: str, args: Any) -> dict[str, Any]:
        """The fallback path (section 7): the Queued reply, or the active-job error."""
        try:
            job = self.enqueue(session_id, kind, args)
        except ActiveJobError as exc:
            return {"status": "error", "reason": str(exc)}
        except ValueError as exc:
            return {"status": "error", "reason": str(exc)}
        path = _result_path(job.job_id)
        return {
            "status": "queued",
            "job_id": job.job_id,
            "result_path": str(path),
            "wait_command": "navigator-wait " + shlex.quote(str(path)),
        }

    def _queued_stage(self, job: Job) -> str:
        return f"queued (position {list(self._queue).index(job) + 1})"

    async def worker(self) -> None:
        while True:
            while not self._queue:
                self._signal = anyio.Event()
                await self._signal.wait()
            job = self._queue.popleft()
            if job.state != "queued":
                continue
            await self._run(job)

    async def cancel(self, job_id: str) -> str:
        """Cancel a job and return its state afterwards (phase 3b section 5). Raises KeyError for an unknown id."""
        job = self._jobs[job_id]
        if job.state == "queued":
            self._queue.remove(job)
            job.cancel_requested = True
            self._finish(job, "cancelled")
        elif job.state == "running":
            job.cancel_requested = True
            if job.scope is not None:
                job.scope.cancel()
            await job.settled.wait()
        return job.state

    # Running one job

    async def _run(self, job: Job) -> None:
        if job.deadline is not None and self._clock() >= job.deadline:
            self._finish(job, "completed", Error(status="error", reason="timeout", stage="queued"))
            return
        try:
            state = S.load_state(job.session_id)
        except (S.SessionNotFound, S.SessionCorrupt) as exc:
            self._finish(job, "failed", failure=str(exc))
            return
        job.state = "running"
        job.snapshot = state.model_copy(deep=True)
        job.files_before = self._audit_names(job.session_id)
        self._write_job_file(job)
        rt = ollama.Runtime(
            client=self._client_factory(),
            mode=self._mode_for(state.mode),
            on_stage=lambda stage: self._on_stage(job, stage),
            deadline_remaining=lambda: self._remaining(job),
            sleep=self._backoff_sleep,
            max_503_retries=self._max_503,
            transform=compress.make_transform(),
        )
        outcome: Outcome | None = None
        failure: str | None = None
        try:
            outcome = await self._execute(job, state, rt)
        except Exception as exc:  # an unexpected error in the worker is a failed task
            if isinstance(exc, BaseExceptionGroup) and len(exc.exceptions) == 1:
                exc = exc.exceptions[0]  # anyio wraps a body error in a task group
            failure = str(exc) or type(exc).__name__

        with anyio.CancelScope(shield=True):
            await rt.client.aclose()
            timed_out = job.timed_out or (
                isinstance(outcome, Error)
                and (outcome.reason == "timeout" or (job.deadline is not None and self._clock() >= job.deadline))
            )
            if failure is not None:
                self._rollback(job)
                self._finish(job, "failed", failure=failure)
            elif job.cancel_requested:
                self._rollback(job)
                self._finish(job, "cancelled")
            elif timed_out:
                self._rollback(job)
                self._finish(job, "completed", Error(status="error", reason="timeout", stage=job.stage))
            elif outcome is None:
                self._rollback(job)
                self._finish(job, "failed", failure="the job ended without an outcome")
            else:
                S.save_state(state)
                self._finish(job, "completed", outcome)

    async def _execute(self, job: Job, state: S.SessionState, rt: ollama.Runtime) -> Outcome | None:
        if job.cancel_requested:
            return None
        async with anyio.create_task_group() as tg:
            if job.deadline is not None:
                tg.start_soon(self._watch, job)
            with anyio.CancelScope() as scope:
                job.scope = scope
                outcome = await self._drive(job, state, rt)
            tg.cancel_scope.cancel()
        return None if scope.cancelled_caught else outcome

    async def _watch(self, job: Job) -> None:
        """Deadline watcher: ends the job when the deadline passes (section 2.2)."""
        assert job.deadline is not None
        await self._sleep(max(job.deadline - self._clock(), 0))
        job.timed_out = True
        if job.scope is not None:
            job.scope.cancel()

    async def _drive(self, job: Job, state: S.SessionState, rt: ollama.Runtime) -> Outcome:
        if job.kind == "ask":
            return await ollama.ask(state, job.args, rt)
        return await ollama.submit(state, job.args, rt)

    def _on_stage(self, job: Job, stage: str) -> None:
        job.stage = stage
        job.stage_history.append(stage)
        job.last_updated_at = _now()

    def _remaining(self, job: Job) -> float | None:
        return None if job.deadline is None else job.deadline - self._clock()

    def _audit_names(self, session_id: str) -> set[str]:
        directory = S.session_dir(session_id)
        if not directory.exists():
            return set()
        return {
            p.name
            for p in directory.iterdir()
            if p.is_file() and not p.name.startswith("job-") and p.name not in ("state.json", "state.json.tmp")
        }

    def _rollback(self, job: Job) -> None:
        """Restore the session to its job-start snapshot and move this job's audit files aside (section 5)."""
        if job.snapshot is None:
            return
        S.save_state(job.snapshot)
        directory = S.session_dir(job.session_id)
        for name in sorted(self._audit_names(job.session_id) - job.files_before):
            moved = directory / f"cancelled-{job.job_id}"
            moved.mkdir(exist_ok=True)
            (directory / name).rename(moved / name)

    # Persistence and results

    def _write_job_file(self, job: Job) -> None:
        _write_json(
            S.session_dir(job.session_id) / f"job-{job.job_id}.json",
            {
                "job_id": job.job_id,
                "kind": job.kind,
                "state": job.state,
                "created_at": _iso(job.created_at),
                "finished_at": _iso(job.finished_at) if job.finished_at else None,
                "phase_before": job.phase_before,
                "failure": job.failure,
            },
        )

    def _finish(self, job: Job, state: str, outcome: Outcome | None = None, failure: str | None = None) -> None:
        job.state = state
        job.outcome = outcome
        job.failure = failure
        job.finished_at = _now()
        job.last_updated_at = job.finished_at
        if state == "failed":
            job.stage = failure or "failed"
        elif state == "cancelled":
            job.stage = "cancelled"
        elif state == "completed":
            job.stage = "completed"
        self._write_job_file(job)
        _write_result(job.job_id, state, outcome, failure)
        (_jobs_dir() / f"{job.job_id}.pending.json").unlink(missing_ok=True)
        job.settled.set()

    def recover(self) -> None:
        """Startup recovery (section 8): stale jobs become failed and their session phase goes back."""
        root = S.state_root()
        if not root.exists():
            return
        for session_dir in root.iterdir():
            if not session_dir.is_dir() or session_dir.name == "jobs":
                continue
            for path in session_dir.glob("job-*.json"):
                data = json.loads(path.read_text())
                if data.get("state") not in ("queued", "running"):
                    continue
                data["state"] = "failed"
                data["failure"] = "server restarted"
                data["finished_at"] = _iso(_now())
                _write_json(path, data)
                try:
                    state = S.load_state(session_dir.name)
                    state.phase = data["phase_before"]
                    S.save_state(state)
                except (S.SessionNotFound, S.SessionCorrupt):
                    pass
                _write_result(data["job_id"], "failed", None, "server restarted")
                (_jobs_dir() / f"{data['job_id']}.pending.json").unlink(missing_ok=True)

    def drop_session(self, session_id: str) -> None:
        """navigator_end: forget finished jobs of the session and delete their result files."""
        for job in list(self._jobs.values()):
            if job.session_id == session_id and job.state in TERMINAL:
                del self._jobs[job.job_id]
                _result_path(job.job_id).unlink(missing_ok=True)

    def session_job(self, session_id: str) -> Job | None:
        """The queued or running job of one session, if any (navigator_status shows it)."""
        return next(
            (j for j in self._jobs.values() if j.session_id == session_id and j.state in ("queued", "running")),
            None,
        )

    def check_no_active_job(self, session_id: str) -> None:
        active = self.active()
        if active is not None and active.session_id == session_id:
            raise ValueError(f"job {active.job_id} is still running for this session; wait for its result, then record")

    # Task object (section 4)

    def _status_message(self, job: Job) -> str:
        if job.state == "queued":
            return self._queued_stage(job)
        if job.state == "running":
            return job.stage
        if job.state == "failed":
            return job.failure or "failed"
        return job.state  # completed or cancelled

    def task_fields(self, job: Job) -> dict[str, Any]:
        status = {"queued": "working", "running": "working"}.get(job.state, job.state)
        return {
            "taskId": job.job_id,
            "status": status,
            "statusMessage": self._status_message(job),
            "createdAt": _iso(job.created_at),
            "lastUpdatedAt": _iso(job.last_updated_at),
            "ttlMs": None,
            "pollIntervalMs": POLL_INTERVAL_MS,
        }


def _write_result(job_id: str, state: str, outcome: Outcome | None, failure: str | None) -> None:
    _write_json(
        _result_path(job_id),
        {
            "job_id": job_id,
            "state": state,
            "outcome": outcome.model_dump(mode="json") if outcome is not None else None,
            "failure": failure,
            "server_pid": os.getpid(),
        },
    )


def _env_client() -> ollama.OllamaClient:
    return ollama.OllamaClient(
        os.environ.get("NAVIGATOR_OLLAMA_URL", "http://host.docker.internal:11434"),
        os.environ.get("NAVIGATOR_MODEL", "qwen-agent:latest"),
    )


def _opted_in(ctx: Any) -> bool:
    """Opt-in per request: the _meta clientCapabilities key, or the session's client capabilities (section 3)."""
    if getattr(ctx, "protocol_version", None) != PROTOCOL_2026:
        return False
    params = ctx.params or {}
    meta = params.get("_meta") or {}
    per_request = (meta.get(CLIENT_CAPABILITIES_META_KEY) or {}).get("extensions") or {}
    if TASKS_ID in per_request:
        return True
    session_caps = getattr(getattr(ctx.session, "client_capabilities", None), "extensions", None) or {}
    return TASKS_ID in session_caps


class TasksExtension(Extension):
    identifier = TASKS_ID

    def __init__(self, jobs: JobManager):
        self._jobs = jobs

    def methods(self):
        return [
            MethodBinding("tasks/get", GetTaskParams, self._get),
            MethodBinding("tasks/cancel", CancelTaskParams, self._cancel),
            MethodBinding("tasks/update", UpdateTaskParams, self._update),
        ]

    def _known(self, task_id: str) -> Job:
        job = self._jobs.get(task_id)
        if job is None:
            raise MCPError(code=TASK_NOT_FOUND, message="Failed to retrieve task: Task not found")
        return job

    async def _get(self, ctx, params: GetTaskParams):
        job = self._known(params.taskId)
        body = {"resultType": "complete", **self._jobs.task_fields(job)}
        if job.state == "completed" and job.outcome is not None:
            outcome = job.outcome.model_dump(mode="json")
            body["result"] = {
                "content": [{"type": "text", "text": json.dumps(outcome)}],
                "structuredContent": outcome,
                "isError": outcome["status"] == "error",
            }
        elif job.state == "failed":
            body["error"] = {"code": INTERNAL_ERROR, "message": job.failure or "failed"}
        if job.state in TERMINAL:
            job.fetched = True
            self._jobs.forget(job.job_id)  # fetched once; a later tasks/get is not found
        return body

    async def _cancel(self, ctx, params: CancelTaskParams):
        self._known(params.taskId)
        await self._jobs.cancel(params.taskId)
        return {}

    async def _update(self, ctx, params: UpdateTaskParams):
        self._known(params.taskId)
        return {}

    async def intercept_tool_call(self, params, ctx, call_next):
        kind = JOB_TOOLS.get(params.name)
        if kind is None or not _opted_in(ctx):
            return await call_next(ctx)
        arguments = dict(params.arguments or {})
        session_id = arguments.pop("session_id", None)
        if not isinstance(session_id, str):
            return await call_next(ctx)
        try:
            args = AskArgs.model_validate(arguments) if kind == "ask" else SubmitArgs.model_validate(arguments)
        except ValidationError:
            return await call_next(ctx)
        try:
            job = self._jobs.enqueue(session_id, kind, args)
        except (ActiveJobError, ValueError):
            return await call_next(ctx)  # the tool body returns the error as a normal result
        return {"resultType": "task", **self._jobs.task_fields(job)}
