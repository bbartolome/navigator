import sys
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any, Literal

import anyio
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError

import json

from . import compress
from . import jobs as J
from . import ollama, session
from .render import render_summary

jobs = J.JobManager.from_env()


@asynccontextmanager
async def lifespan(app: MCPServer) -> AsyncIterator[dict[str, Any]]:
    """Startup recovery, then one worker for the life of the process (phase 3b section 2.5)."""
    jobs.recover()
    async with anyio.create_task_group() as tg:
        tg.start_soon(jobs.worker)
        yield {"jobs": jobs}
        tg.cancel_scope.cancel()


mcp = MCPServer("navigator", extensions=[J.TasksExtension(jobs)], lifespan=lifespan)


def _load(session_id: str) -> session.SessionState:
    try:
        return session.load_state(session_id)
    except session.SessionNotFound:
        raise ValueError(f"unknown session_id: {session_id}") from None


# The SDK replaces any exception other than ToolError with "Error executing tool <name>", so the driver would see no
# reason. These tools re-raise the failures it can act on as ToolError, whose text reaches the client
# (docs/fix-navigator-record.md section 1).
RECORD_DESCRIPTION = (
    "Record a verdict, decision, clarification, step or challenge on the session. Payload keys by kind; an unknown or "
    "missing key is refused and the error lists the allowed keys:\n" + "\n".join(session.RECORD_CONTRACT.values())
)


def _tool_error(exc: Exception) -> ToolError:
    if isinstance(exc, ValidationError):
        return ToolError(session.describe_record_error(exc))
    if isinstance(exc, session.SessionNotFound):
        return ToolError(f"unknown session_id: {exc}")
    return ToolError(str(exc))


@contextmanager
def _readable_errors() -> Iterator[None]:
    try:
        yield
    except (ValueError, session.SessionNotFound, session.SessionCorrupt) as exc:  # ValidationError is a ValueError
        raise _tool_error(exc) from exc


@mcp.tool()
def navigator_start(mode: Literal["pair", "scout"], goal: str, context: str) -> str:
    """Start a navigator session and return its session_id."""
    return session.create_session(mode, goal, context).session_id


@mcp.tool()
def navigator_status(session_id: str) -> str:
    """Return the rendered SESSION SUMMARY, the session's Ollama metrics and its current job, if any."""
    with _readable_errors():
        with session.session_lock(session_id):
            state = _load(session_id)
        job = jobs.session_job(session_id)
    m = state.metrics
    metrics = (
        f"METRICS: sends={m.sends} prompt_eval_count={m.prompt_eval_count}"
        f" prompt_eval_cached_count={m.prompt_eval_cached_count} prompt_eval_duration={m.prompt_eval_duration}"
    )
    job_line = f"\nJOB: {job.job_id} {job.state} {job.stage}" if job is not None else ""
    summary = render_summary(state, compress.make_transform())
    return summary + "\n" + metrics + job_line + "\nCOMPRESSION: " + json.dumps(compress.status(), sort_keys=True)


@mcp.tool(description=RECORD_DESCRIPTION)
def navigator_record(session_id: str, kind: session.RecordKind, payload: dict[str, Any]) -> dict[str, Any]:
    """Record a verdict, decision, clarification, step or challenge on the session."""
    with _readable_errors(), session.session_lock(session_id):
        jobs.check_no_active_job(session_id)  # inside the lock: no job can start between this check and the save
        state = _load(session_id)
        if state.ended:
            raise ValueError("session ended")
        session.apply_record(state, kind, payload)
        session.save_state(state)
        return {"ok": True, "counts": _counts(state)}


@mcp.tool()
def navigator_end(session_id: str) -> dict[str, Any]:
    """Mark the session finished. Its files stay on disk."""
    with _readable_errors():
        with session.session_lock(session_id):
            jobs.check_no_active_job(session_id)
            state = _load(session_id)
            state.ended = True
            session.save_state(state)
    jobs.drop_session(session_id)
    return {"ok": True}


@mcp.tool()
def navigator_ask(
    session_id: str,
    question: str,
    answer_tool: str,
    excerpts: list[ollama.Excerpt] | None = None,
    images: list[str] | None = None,
) -> dict[str, Any]:
    """Ask the navigator one question. Returns the queued reply; the Outcome arrives as the job result."""
    args = ollama.AskArgs(question=question, answer_tool=answer_tool, excerpts=excerpts or [], images=images or [])
    return jobs.enqueue_queued(session_id, "ask", args)


@mcp.tool()
def navigator_submit(session_id: str, results: list[ollama.Result]) -> dict[str, Any]:
    """Send the results of the navigator's tool calls. Returns the queued reply; the Outcome arrives as the job result."""
    args = ollama.SubmitArgs(results=results)
    return jobs.enqueue_queued(session_id, "submit", args)


@mcp.tool()
async def navigator_cancel(job_id: str) -> dict[str, Any]:
    """Cancel a queued or running job. Returns the job's state after the cancel."""
    try:
        state = await jobs.cancel(job_id)
    except KeyError:
        state = "unknown"
    return {"job_id": job_id, "state": state}


def _counts(state: session.SessionState) -> dict[str, int]:
    return {name: len(value) for name, value in state if isinstance(value, list)}


def main() -> None:
    argv = sys.argv[1:]
    if "--job-timeout" in argv:  # the CLI flag wins over NAVIGATOR_JOB_TIMEOUT (phase 3b section 8)
        jobs.configure(float(argv[argv.index("--job-timeout") + 1]))
    mcp.run()  # transport defaults to "stdio"
