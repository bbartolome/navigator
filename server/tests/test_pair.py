"""Phase 4 tests: pair mode, answer-tool validation, and skills/pair (docs/phases/phase-4-pair.md, section 6).

Table numbers follow the phase doc. Test 20 runs `claude plugin validate` on the repo.
"""

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

from fakes import FakeOllama, call, decide, runtime
from navigator_mcp import session as S
from navigator_mcp.modes import pair
from navigator_mcp.modes.pair import CHARTER, PAIR
from navigator_mcp.ollama import Answered, AskArgs, Error, NeedInfo, canonical_json, ask
from navigator_mcp.schemas import NAVIGATOR_TOOLS, SET_PLAN_TOOL, DECIDE_TOOL

pytestmark = pytest.mark.anyio

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / "skills" / "pair" / "SKILL.md"

NAV_NAMES = ["read_file", "list_dir", "grep", "screenshot", "web_search", "need_info"]
REQUIRED = {
    "read_file": ["path", "justification"],
    "list_dir": ["path", "justification"],
    "grep": ["pattern", "justification"],
    "screenshot": ["target", "justification"],
    "web_search": ["query", "justification"],
    "need_info": ["questions"],
    "set_plan": ["steps", "carry_forward"],
    "decide": ["decision", "carry_forward"],
}

VALID_STEP = {"id": "1", "files": ["api/users.py"], "change": "add --version", "verification": "pytest"}


def _state():
    return S.create_session("pair", "Add a --version flag", "Repo: cli/.")


def _ask(question="Which?", tool="decide") -> AskArgs:
    return AskArgs(question=question, answer_tool=tool)


def _user_text(body: bytes) -> str:
    return json.loads(body)["messages"][1]["content"]


# 1 charter byte-equal to the to-llm charter. The skill file is not in the repo; the test checks a digest of its charter.
CHARTER_SHA256 = "1e93e56b08586f0bede6fd78356abb01c1db7942bdaf267fe1156799f6887280"


def test_01_charter_matches_the_to_llm_charter_digest():
    assert hashlib.sha256(CHARTER.encode("utf-8")).hexdigest() == CHARTER_SHA256


# 2 schemas
def _tool_fn(tool):
    return tool["function"]


def test_02_schemas_are_valid_json_schema_with_the_required_fields():
    tools = {t["function"]["name"]: t["function"] for t in [*NAVIGATOR_TOOLS, SET_PLAN_TOOL, DECIDE_TOOL]}
    assert list(tools) == NAV_NAMES + ["set_plan", "decide"]
    for name, fn in tools.items():
        Draft202012Validator.check_schema(fn["parameters"])
        assert fn["parameters"]["required"] == REQUIRED[name], name
    assert tools["screenshot"]["parameters"]["required"] == ["target", "justification"]
    assert tools["need_info"]["parameters"]["required"] == ["questions"]


# 3 tool list with decide
def test_03_tool_order_with_decide_and_stable_bytes():
    def build():
        return [*NAVIGATOR_TOOLS, DECIDE_TOOL]

    first, second = build(), build()
    assert [t["function"]["name"] for t in first] == NAV_NAMES + ["decide"]
    assert canonical_json(first) == canonical_json(second)


# 4 set_plan suffix
async def test_04_set_plan_question_ends_with_the_answer_line():
    fake = FakeOllama([call("set_plan", steps=[VALID_STEP], carry_forward=[])])
    await ask(_state(), _ask("First question?", "set_plan"), runtime(fake, PAIR))
    assert _user_text(fake.requests[0]).splitlines()[-1] == "Answer by calling set_plan."


# 5 decide suffix, question once
async def test_05_decide_question_ends_with_the_answer_line_and_question_appears_once():
    fake = FakeOllama([decide(decision="x", carry_forward=[])])
    await ask(_state(), _ask("Which module owns retries?", "decide"), runtime(fake, PAIR))
    text = _user_text(fake.requests[0])
    assert text.splitlines()[-1] == "Answer by calling decide."
    assert text.count("Which module owns retries?") == 1


# 6 question already ending in the suffix
async def test_06_question_already_ending_with_suffix_is_not_doubled():
    fake = FakeOllama([decide(decision="x", carry_forward=[])])
    await ask(_state(), _ask("Which?\nAnswer by calling decide.", "decide"), runtime(fake, PAIR))
    assert _user_text(fake.requests[0]).count("Answer by calling decide.") == 1


# 7 read_file is not an answer tool
async def test_07_navigator_ask_with_a_navigator_tool_as_answer_tool_is_an_error_and_sends_nothing():
    fake = FakeOllama([])
    st = _state()
    out = await ask(st, _ask("Q?", "read_file"), runtime(fake, PAIR))
    assert isinstance(out, Error)
    assert len(fake.requests) == 0


# 8-15 missing_fields
def test_08_valid_set_plan_has_no_missing_fields():
    assert PAIR.missing_fields("set_plan", {"steps": [VALID_STEP], "carry_forward": []}) == []


def test_09_set_plan_without_carry_forward_names_it():
    assert any("carry_forward" in f for f in PAIR.missing_fields("set_plan", {"steps": [VALID_STEP]}))


def test_10_set_plan_with_no_steps_is_invalid():
    assert PAIR.missing_fields("set_plan", {"steps": [], "carry_forward": []}) != []


def test_11_set_plan_step_missing_verification_names_it():
    step = {k: v for k, v in VALID_STEP.items() if k != "verification"}
    assert PAIR.missing_fields("set_plan", {"steps": [step], "carry_forward": []}) == ["steps[0].verification"]


def test_12_decide_without_findings_is_valid():
    assert PAIR.missing_fields("decide", {"decision": "x", "carry_forward": []}) == []


def test_13_decide_with_six_carry_forward_lines_is_invalid():
    assert PAIR.missing_fields("decide", {"decision": "x", "carry_forward": list("abcdef")}) != []


def test_14_decide_with_six_findings_is_invalid():
    finding = {"severity": "S1", "cite": "a.py:1", "issue": "i", "fix": "f"}
    args = {"decision": "x", "carry_forward": [], "findings": [finding] * 6}
    assert PAIR.missing_fields("decide", args) != []


def test_15_decide_finding_with_severity_s4_is_invalid():
    finding = {"severity": "S4", "cite": "a.py:1", "issue": "i", "fix": "f"}
    args = {"decision": "x", "carry_forward": [], "findings": [finding]}
    assert PAIR.missing_fields("decide", args) != []


# 16-18 loop behaviour with pair mode
async def test_16_missing_decision_is_returned_to_the_navigator_then_answered():
    fake = FakeOllama([decide(carry_forward=[]), decide(decision="x", carry_forward=[])])
    out = await ask(_state(), _ask(), runtime(fake, PAIR))
    assert isinstance(out, Answered)
    assert "decision" in json.loads(fake.requests[1])["messages"][-1]["content"]


async def test_17_set_plan_when_decide_was_asked_is_malformed_and_resent():
    fake = FakeOllama([call("set_plan", steps=[VALID_STEP], carry_forward=[]), decide(decision="x", carry_forward=[])])
    out = await ask(_state(), _ask(), runtime(fake, PAIR))
    assert isinstance(out, Answered)
    assert json.loads(fake.requests[1])["messages"][-1]["content"].startswith("Your reply was malformed:")


async def test_18_need_info_reply_is_need_info_not_answered():
    fake = FakeOllama([call("need_info", questions=["Which HTTP client? -- matters because retries differ"])])
    out = await ask(_state(), _ask(), runtime(fake, PAIR))
    assert isinstance(out, NeedInfo)


# 19 skill frontmatter and body
def _frontmatter() -> tuple[dict, str]:
    text = SKILL.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    head, body = text[4:].split("\n---\n", 1)
    return yaml.safe_load(head), body


def test_19_skill_frontmatter_and_body():
    meta, body = _frontmatter()
    assert meta["disable-model-invocation"] is True
    assert meta["allowed-tools"] == "mcp__plugin_navigator_navigator__*"
    assert meta["name"] == "pair"
    for needle in ["navigator_ask", "navigator_submit", "navigator_record", "WebSearch", "AskUserQuestion", "Playwright"]:
        assert needle in body, needle
    outside = body.replace("Never curl Ollama", "")
    assert "curl" not in outside


# Fix plan section 6: the skill quotes the exact payload key lines that the server accepts and describes.
def test_pair_skill_quotes_each_record_kinds_keys():
    _, body = _frontmatter()
    for line in S.RECORD_CONTRACT.values():
        assert line in body, line


# 20 plugin validation
@pytest.mark.skipif(shutil.which("claude") is None, reason="claude CLI not on PATH")
def test_20_claude_plugin_validate_passes():
    proc = subprocess.run(["claude", "plugin", "validate", str(REPO)], capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Validation passed" in proc.stdout


# Added with phase 5: the set_plan answer now fills the summary's STEPS, and scope records are scout-only


async def test_21_set_plan_answer_stores_the_steps_in_the_summary():
    from navigator_mcp.render import render_summary

    fake = FakeOllama([call("set_plan", steps=[VALID_STEP], carry_forward=[])])
    st = _state()
    out = await ask(st, _ask("First question?", "set_plan"), runtime(fake, PAIR))
    assert isinstance(out, Answered)
    assert len(st.steps) == 1
    assert "- [ ] 1 files: api/users.py | change: add --version | verify: pytest" in render_summary(st)


def test_22_question_list_scope_is_refused_in_a_pair_session():
    st = _state()
    with pytest.raises(ValueError):
        S.apply_record(st, "decided", {"scope": "question_list"})
