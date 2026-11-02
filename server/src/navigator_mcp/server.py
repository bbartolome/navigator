from typing import Any, Literal

from mcp.server import MCPServer

from . import session
from .render import render_summary

mcp = MCPServer("navigator")


def _load(session_id: str) -> session.SessionState:
    try:
        return session.load_state(session_id)
    except session.SessionNotFound:
        raise ValueError(f"unknown session_id: {session_id}") from None


@mcp.tool()
def navigator_start(mode: Literal["pair", "scout"], goal: str, context: str) -> str:
    """Start a navigator session and return its session_id."""
    return session.create_session(mode, goal, context).session_id


@mcp.tool()
def navigator_status(session_id: str) -> str:
    """Return the rendered SESSION SUMMARY for the session."""
    with session.session_lock(session_id):
        return render_summary(_load(session_id))


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


def _counts(state: session.SessionState) -> dict[str, int]:
    return {
        name: len(getattr(state, name))
        for name, value in state
        if isinstance(value, list)
    }


def main() -> None:
    mcp.run()  # transport defaults to "stdio"
