"""Phase 1 smoke tests (docs/phases/phase-1-scaffold.md, "Smoke tests" 1-3).

Smoke test 4 (plugin load and /mcp) is manual and is recorded in the PR, not here.
"""

import json
import subprocess
import sys
from pathlib import Path

ENTRY = Path(sys.executable).with_name("navigator-mcp")

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "smoke", "version": "0"},
    },
}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}
LIST_TOOLS = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}


def _call(id_: int, name: str, arguments: dict) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": id_,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


def _run(messages: list[dict], expect_ids: list[int]) -> dict[int, dict]:
    """Send messages over stdio and return the replies keyed by id.

    stdin stays open until every expected reply has arrived, as a real MCP client keeps it open. A literal pipe
    that closes stdin right after the last request makes the SDK cancel in-flight requests (see phase 1 notes).
    """
    proc = subprocess.Popen(
        [str(ENTRY)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    lines = []
    try:
        for m in messages:
            proc.stdin.write(json.dumps(m) + "\n")
        proc.stdin.flush()
        seen: set[int] = set()
        while not set(expect_ids) <= seen:
            line = proc.stdout.readline()
            if not line:
                break
            lines.append(line)
            msg = json.loads(line)  # every stdout line must be JSON-RPC
            if "id" in msg:
                seen.add(msg["id"])
    finally:
        proc.stdin.close()
        rest = proc.stdout.read()
        err = proc.stderr.read()
        proc.wait(timeout=120)
    lines += rest.splitlines(keepends=True)
    assert proc.returncode == 0, err
    responses = {}
    for line in lines:
        msg = json.loads(line)
        if "id" in msg:
            responses[msg["id"]] = msg
    return responses


def test_entry_point_exits_cleanly_with_no_stdout():
    proc = subprocess.run(
        [str(ENTRY)], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""


def test_jsonrpc_over_stdio_lists_and_calls_stub_tools():
    responses = _run(
        [
            INITIALIZE,
            INITIALIZED,
            LIST_TOOLS,
            _call(3, "navigator_start", {"mode": "pair", "goal": "g", "context": "c"}),
            _call(4, "navigator_status", {"session_id": "s1"}),
        ],
        expect_ids=[1, 2, 3, 4],
    )

    assert responses[1]["result"]["serverInfo"]["name"] == "navigator"

    tools = {t["name"]: t for t in responses[2]["result"]["tools"]}
    assert set(tools) == {"navigator_start", "navigator_status"}
    mode_schema = tools["navigator_start"]["inputSchema"]["properties"]["mode"]
    assert set(_enum_values(mode_schema)) == {"pair", "scout"}

    assert responses[3]["result"]["content"][0]["text"] == "s1"
    assert (
        responses[4]["result"]["content"][0]["text"]
        == "session s1\nmode: pair\ngoal: g\nmetrics: none"
    )


def test_unknown_session_id_is_a_tool_error():
    responses = _run(
        [INITIALIZE, INITIALIZED, _call(2, "navigator_status", {"session_id": "nope"})], expect_ids=[1, 2]
    )
    assert responses[2]["result"]["isError"] is True


def _enum_values(schema: dict) -> list[str]:
    if "enum" in schema:
        return schema["enum"]
    # Pydantic emits Literal as anyOf of const/enum branches.
    values = []
    for branch in schema.get("anyOf", []):
        values.extend(branch.get("enum", []))
    return values
