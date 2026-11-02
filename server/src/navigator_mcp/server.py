from typing import Literal

from mcp.server import MCPServer

mcp = MCPServer("navigator")

_sessions: dict[str, dict[str, str]] = {}


@mcp.tool()
def navigator_start(mode: Literal["pair", "scout"], goal: str, context: str) -> str:
    """Start a navigator session and return its session_id. Stub: stores the inputs in memory."""
    session_id = f"s{len(_sessions) + 1}"
    _sessions[session_id] = {"mode": mode, "goal": goal, "context": context}
    return session_id


@mcp.tool()
def navigator_status(session_id: str) -> str:
    """Return the session summary and metrics. Stub: returns the stored inputs as text."""
    s = _sessions.get(session_id)
    if s is None:
        raise ValueError(f"unknown session_id: {session_id}")
    return f"session {session_id}\nmode: {s['mode']}\ngoal: {s['goal']}\nmetrics: none"


def main() -> None:
    mcp.run()  # transport defaults to "stdio"
