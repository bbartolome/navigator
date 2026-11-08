"""Phase 3b test 35: raw JSON-RPC over stdio, as the main doc section 2 probe does.

The server runs as a subprocess (the navigator-mcp entry point, pair mode). Ollama is a real HTTP server on localhost that
answers every /api/chat call with a decide tool call. The opted-in navigator_ask returns a task; tasks/get polls it
to completion. Without the opt-in, navigator_ask returns the queued reply at once (section 7 and test 14).
"""

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

META = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientInfo": {"name": "stdio-test", "version": "0"},
    "io.modelcontextprotocol/clientCapabilities": {},
}
OPT_IN = {
    **META,
    "io.modelcontextprotocol/clientCapabilities": {"extensions": {"io.modelcontextprotocol/tasks": {}}},
}
DECIDE = {
    "message": {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": "decide", "arguments": {"decision": "x", "carry_forward": []}}}],
    },
    "done": True,
}


class _Ollama(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - http.server naming
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        body = json.dumps(DECIDE).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class _Client:
    def __init__(self, env: dict):
        self.proc = subprocess.Popen(
            [str(Path(sys.executable).with_name("navigator-mcp"))],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
        )
        self._next = 0

    def call(self, method: str, params: dict) -> dict:
        self._next += 1
        msg = {"jsonrpc": "2.0", "id": self._next, "method": method, "params": params}
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        assert line, self.proc.stderr.read()
        reply = json.loads(line)  # every stdout line is JSON-RPC
        assert reply["id"] == self._next
        return reply["result"]

    def tool(self, name: str, arguments: dict, meta: dict) -> dict:
        return self.call("tools/call", {"name": name, "arguments": arguments, "_meta": meta})

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=60)
        assert self.proc.returncode == 0, self.proc.stderr.read()


@pytest.fixture
def ollama_url():
    server = HTTPServer(("127.0.0.1", 0), _Ollama)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture
def client(ollama_url, monkeypatch):
    monkeypatch.setenv("NAVIGATOR_OLLAMA_URL", ollama_url)
    c = _Client(dict(os.environ))
    yield c
    c.close()


def test_opted_in_ask_returns_a_task_and_tasks_get_completes_it(client):
    discover = client.call("server/discover", {"_meta": META})
    assert "io.modelcontextprotocol/tasks" in discover["capabilities"]["extensions"]

    session_id = client.tool("navigator_start", {"mode": "pair", "goal": "g", "context": ""}, META)["structuredContent"]["result"]
    raw = client.call(
        "tools/call",
        {"name": "navigator_ask", "arguments": {"session_id": session_id, "question": "Q?", "answer_tool": "decide"}, "_meta": OPT_IN},
    )
    assert raw["resultType"] == "task"
    task_id = raw["taskId"]
    assert raw["status"] == "working"
    assert raw["pollIntervalMs"] == 5000

    got = None
    for _ in range(60):
        got = client.call("tasks/get", {"taskId": task_id, "_meta": OPT_IN})
        if got.get("status") in ("completed", "failed", "cancelled"):
            break
        time.sleep(0.2)
    assert got["status"] == "completed"
    assert got["resultType"] == "complete"
    assert got["result"]["isError"] is False
    assert json.loads(got["result"]["content"][0]["text"])["status"] == "answered"


def test_without_opt_in_the_ask_returns_queued_at_once(client):
    session_id = client.tool("navigator_start", {"mode": "pair", "goal": "g", "context": ""}, META)["structuredContent"]["result"]
    reply = client.tool("navigator_ask", {"session_id": session_id, "question": "Q?", "answer_tool": "decide"}, META)
    queued = reply["structuredContent"]
    assert queued["status"] == "queued"
    assert Path(queued["result_path"]).is_absolute()
    assert queued["wait_command"].startswith("navigator-wait ")


# Phase 5b section 9: a settle ask with no options is refused at enqueue, on both the queued and the Tasks path

NO_OPTIONS_REASON = "settle needs options: 2 to 8 plain strings, one per option"


def _scout_session(client) -> str:
    return client.tool("navigator_start", {"mode": "scout", "goal": "g", "context": ""}, META)["structuredContent"]["result"]


def test_settle_without_options_is_refused_on_the_queued_path(client):
    session_id = _scout_session(client)
    reply = client.tool("navigator_ask", {"session_id": session_id, "question": "Which?", "answer_tool": "settle"}, META)
    assert reply["structuredContent"] == {"status": "error", "reason": NO_OPTIONS_REASON}


def test_settle_without_options_is_refused_on_the_tasks_path_with_no_task(client):
    session_id = _scout_session(client)
    raw = client.call(
        "tools/call",
        {"name": "navigator_ask", "arguments": {"session_id": session_id, "question": "Which?", "answer_tool": "settle"}, "_meta": OPT_IN},
    )
    assert "taskId" not in raw
    assert raw["structuredContent"] == {"status": "error", "reason": NO_OPTIONS_REASON}


def test_settle_with_options_is_queued_on_the_queued_path(client):
    session_id = _scout_session(client)
    reply = client.tool(
        "navigator_ask",
        {"session_id": session_id, "question": "Which?", "answer_tool": "settle", "options": ["JSON file", "SQLite database"]},
        META,
    )
    assert reply["structuredContent"]["status"] == "queued"
