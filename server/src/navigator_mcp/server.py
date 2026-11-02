import os
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from mcp.server import MCPServer

from . import ollama, session
from .render import render_summary

mcp = MCPServer("navigator")

# Modes are registered by phases 4 and 5. Until then navigator_ask reports the mode as unavailable.
_MODES: dict[str, ollama.Mode] = {}

# Phase 3 has no job queue yet (phase 3b). This guard stops two asks on one session in the same process.
_busy: set[str] = set()


def _load(session_id: str) -> session.SessionState:
    try:
        return session.load_state(session_id)
    except session.SessionNotFound:
        raise ValueError(f"unknown session_id: {session_id}") from None


def _client() -> ollama.OllamaClient:
    return ollama.OllamaClient(
        os.environ.get("NAVIGATOR_OLLAMA_URL", "http://host.docker.internal:11434"),
        os.environ.get("NAVIGATOR_MODEL", "qwen-agent:latest"),
    )


async def _drive(
    session_id: str, run: Callable[[session.SessionState, ollama.Runtime], Awaitable[ollama.Outcome]]
) -> dict[str, Any]:
    with session.session_lock(session_id):
        state = _load(session_id)
        if session_id in _busy:
            return ollama.Error(status="error", reason="busy").model_dump(mode="json")
        mode = _MODES.get(state.mode)
        if mode is None:
            reason = f"no mode registered for {state.mode!r} yet"
            return ollama.Error(status="error", reason=reason).model_dump(mode="json")
        _busy.add(session_id)
    client = _client()
    try:
        outcome = await run(state, ollama.Runtime(client=client, mode=mode))
        return outcome.model_dump(mode="json")
    finally:
        await client.aclose()
        with session.session_lock(session_id):
            session.save_state(state)
        _busy.discard(session_id)


@mcp.tool()
def navigator_start(mode: Literal["pair", "scout"], goal: str, context: str) -> str:
    """Start a navigator session and return its session_id."""
    return session.create_session(mode, goal, context).session_id


@mcp.tool()
def navigator_status(session_id: str) -> str:
    """Return the rendered SESSION SUMMARY and the session's Ollama metrics."""
    with session.session_lock(session_id):
        state = _load(session_id)
    m = state.metrics
    metrics = (
        f"METRICS: sends={m.sends} prompt_eval_count={m.prompt_eval_count}"
        f" prompt_eval_cached_count={m.prompt_eval_cached_count} prompt_eval_duration={m.prompt_eval_duration}"
    )
    return render_summary(state) + "\n" + metrics


@mcp.tool()
def navigator_record(session_id: str, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Record a verdict, decision, clarification, step or challenge on the session."""
    with session.session_lock(session_id):
        state = _load(session_id)
        session.apply_record(state, kind, payload)
        session.save_state(state)
        return {"ok": True, "counts": _counts(state)}


@mcp.tool()
def navigator_end(session_id: str) -> dict[str, Any]:
    """Mark the session finished. Its files stay on disk."""
    with session.session_lock(session_id):
        state = _load(session_id)
        state.ended = True
        session.save_state(state)
        return {"ok": True}


@mcp.tool()
async def navigator_ask(
    session_id: str,
    question: str,
    answer_tool: str,
    excerpts: list[ollama.Excerpt] | None = None,
    images: list[str] | None = None,
) -> dict[str, Any]:
    """Ask the navigator one question. Returns an Outcome: tool_calls, need_info, answered or error."""
    args = ollama.AskArgs(question=question, answer_tool=answer_tool, excerpts=excerpts or [], images=images or [])
    return await _drive(session_id, lambda st, rt: ollama.ask(st, args, rt))


@mcp.tool()
async def navigator_submit(session_id: str, results: list[ollama.Result]) -> dict[str, Any]:
    """Send the results of the navigator's tool calls. Returns the next Outcome."""
    args = ollama.SubmitArgs(results=results)
    return await _drive(session_id, lambda st, rt: ollama.submit(st, args, rt))


def _counts(state: session.SessionState) -> dict[str, int]:
    return {name: len(value) for name, value in state if isinstance(value, list)}


def main() -> None:
    mcp.run()  # transport defaults to "stdio"
