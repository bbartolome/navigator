"""Smoke tests over stdio (docs/phases/phase-1-scaffold.md "Smoke tests" 1-3, updated for phase 2).

Smoke test 4 (plugin load and /mcp) is manual and is recorded in the PR, not here.

The harness works like a real MCP client: it keeps stdin open and reads each reply before sending the next request.
A literal pipe that closes stdin after the last request makes the SDK cancel in-flight requests, so the last reply
would never arrive (see the phase 1 commit).
"""

import json
import re
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
    return {"jsonrpc": "2.0", "id": id_, "method": "tools/call", "params": {"name": name, "arguments": arguments}}


class _Server:
    """One navigator-mcp process. Replies are matched by id; notifications are ignored."""

    def __enter__(self):
        self.proc = subprocess.Popen(
            [str(ENTRY)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        self.stdout_lines: list[str] = []
        return self

    def send(self, msg: dict) -> None:
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def request(self, msg: dict) -> dict:
        self.send(msg)
        while True:
            line = self.proc.stdout.readline()
            assert line, self.proc.stderr.read()
            self.stdout_lines.append(line)
            reply = json.loads(line)  # every stdout line must be JSON-RPC
            if reply.get("id") == msg["id"]:
                return reply

    def __exit__(self, *exc):
        self.proc.stdin.close()
        self.stdout_lines += self.proc.stdout.read().splitlines(keepends=True)
        err = self.proc.stderr.read()
        self.proc.wait(timeout=120)
        assert self.proc.returncode == 0, err
        for line in self.stdout_lines:
            json.loads(line)


def _text(reply: dict) -> str:
    return reply["result"]["content"][0]["text"]


def test_entry_point_exits_cleanly_with_no_stdout():
    proc = subprocess.run([str(ENTRY)], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""


def test_jsonrpc_over_stdio_lists_tools_and_starts_and_reads_a_session():
    with _Server() as srv:
        init = srv.request(INITIALIZE)
        srv.send(INITIALIZED)
        assert init["result"]["serverInfo"]["name"] == "navigator"

        tools = {t["name"]: t for t in srv.request(LIST_TOOLS)["result"]["tools"]}
        expected = {"navigator_start", "navigator_status", "navigator_record", "navigator_end", "navigator_ask", "navigator_submit"}
        assert expected <= set(tools)
        mode_schema = tools["navigator_start"]["inputSchema"]["properties"]["mode"]
        assert set(_enum_values(mode_schema)) == {"pair", "scout"}

        session_id = _text(srv.request(_call(3, "navigator_start", {"mode": "pair", "goal": "g", "context": "c"})))
        assert re.fullmatch(r"[0-9a-f]{32}", session_id)

        summary = _text(srv.request(_call(4, "navigator_status", {"session_id": session_id})))
        assert summary.startswith("SESSION SUMMARY\nGOAL: g\nMODE: pair\nCONTEXT: c\nDECIDED: (none)\n")


def test_record_then_end_over_stdio():
    with _Server() as srv:
        srv.request(INITIALIZE)
        srv.send(INITIALIZED)
        session_id = _text(srv.request(_call(2, "navigator_start", {"mode": "pair", "goal": "g", "context": ""})))

        ack = srv.request(
            _call(3, "navigator_record", {"session_id": session_id, "kind": "decided", "payload": {"text": "Use X"}})
        )
        assert ack["result"]["structuredContent"]["ok"] is True
        assert ack["result"]["structuredContent"]["counts"]["decided"] == 1

        summary = _text(srv.request(_call(4, "navigator_status", {"session_id": session_id})))
        assert "DECIDED:\n- Use X" in summary

        ended = srv.request(_call(5, "navigator_end", {"session_id": session_id}))
        assert ended["result"]["isError"] is False


def test_unknown_session_id_is_a_tool_error():
    with _Server() as srv:
        srv.request(INITIALIZE)
        srv.send(INITIALIZED)
        reply = srv.request(_call(2, "navigator_status", {"session_id": "nope"}))
        assert reply["result"]["isError"] is True


def _enum_values(schema: dict) -> list[str]:
    if "enum" in schema:
        return schema["enum"]
    # Pydantic emits Literal as anyOf of const/enum branches.
    values = []
    for branch in schema.get("anyOf", []):
        values.extend(branch.get("enum", []))
    return values
