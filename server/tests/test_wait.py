"""Phase 3b test 20: bin/navigator-wait exit codes and output (docs/phases/phase-3b-async-queue.md, section 7).

The script runs as a real subprocess with its shebang, as the Bash tool would run it.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "bin" / "navigator-wait"
OUTCOME = {"status": "answered", "tool": "decide", "result": {"decision": "x"}, "carry_forward": []}


def _dead_pid() -> int:
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()  # reaped: the pid no longer exists
    return child.pid


def _write_result(jobs: Path, job_id: str, state: str, outcome=None, failure=None) -> Path:
    path = jobs / f"{job_id}.result.json"
    path.write_text(json.dumps({"job_id": job_id, "state": state, "outcome": outcome, "failure": failure, "server_pid": 1}))
    return path


def _run(path: Path):
    return subprocess.run([str(SCRIPT), str(path)], capture_output=True, text=True, timeout=30)


@pytest.fixture
def jobs(tmp_path) -> Path:
    d = tmp_path / "jobs"
    d.mkdir()
    return d


def test_script_is_executable_with_a_python3_shebang():
    assert SCRIPT.exists()
    assert SCRIPT.read_text().startswith("#!/usr/bin/env python3")
    assert SCRIPT.stat().st_mode & 0o111


def test_completed_prints_the_outcome_and_exits_zero(jobs):
    proc = _run(_write_result(jobs, "j1", "completed", outcome=OUTCOME))
    assert proc.returncode == 0
    lines = proc.stdout.splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == OUTCOME


def test_cancelled_exits_three(jobs):
    proc = _run(_write_result(jobs, "j2", "cancelled"))
    assert proc.returncode == 3
    assert json.loads(proc.stdout) == {"status": "error", "reason": "cancelled"}


def test_failed_exits_four_with_the_failure_text(jobs):
    proc = _run(_write_result(jobs, "j3", "failed", failure="server restarted"))
    assert proc.returncode == 4
    assert json.loads(proc.stdout) == {"status": "error", "reason": "server restarted"}


def test_server_gone_before_a_result_exits_five(jobs):
    (jobs / "j4.pending.json").write_text(json.dumps({"job_id": "j4", "server_pid": _dead_pid()}))
    proc = _run(jobs / "j4.result.json")
    assert proc.returncode == 5
    assert json.loads(proc.stdout) == {"status": "error", "reason": "server exited"}


def test_unknown_job_exits_two(jobs):
    proc = _run(jobs / "missing.result.json")
    assert proc.returncode == 2
