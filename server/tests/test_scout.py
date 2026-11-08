"""Phase 5 tests: scout mode, agree_questions and settle, and skills/scout (docs/phases/phase-5-scout.md section 8).

Numbers follow the phase doc table. Fixtures copy the phase 5 doc: scout_tools.json (the two JSON tool blocks, section
4) and scout_charter.txt (the section 3 charter).
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from fakes import FakeOllama, call, runtime
from navigator_mcp import session as S
from navigator_mcp.modes.scout import SCOUT, SCOUT_CHARTER
from navigator_mcp.ollama import Answered, AskArgs, Error, ask, submit, Result, SubmitArgs
from navigator_mcp.render import render_summary
from navigator_mcp.session import SessionState, apply_record

pytestmark = pytest.mark.anyio

FIX = Path(__file__).parent / "fixtures"
GOLDEN = Path(__file__).parent / "golden"
REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / "skills" / "scout" / "SKILL.md"
TOOLS = json.loads((FIX / "scout_tools.json").read_text())


def _start() -> SessionState:
    return S.create_session("scout", "Settle storage format", "Ticket T-1: resolution text")


def _questions(ids=("q1", "q2")):
    text = {"q1": "JSON or SQLite?", "q2": "Where is state stored?"}
    return [{"id": i, "question": text.get(i, f"{i}?"), "why_it_matters": "storage"} for i in ids]


def _alts():
    # phase 5b: options are named by their labels, A and B, which the legend shows for the two option texts
    return [
        {"name": "A", "case_for": "no deps", "case_against": "no queries", "backwards_compatible": True},
        {"name": "B", "case_for": "queries", "case_against": "new dep", "backwards_compatible": True},
    ]


def _settle(qid, decision="A", alternatives=None, complexity=None, reasoning="simplest"):
    args = {
        "question_id": qid,
        "alternatives": alternatives if alternatives is not None else _alts(),
        "decision": decision,
        "reasoning": reasoning,
        "carry_forward": [],
    }
    if complexity is not None:
        args["complexity_justification"] = complexity
    return call("settle", **args)


def _agree_reply(ids=("q1", "q2")):
    return call("agree_questions", questions=_questions(ids), carry_forward=[])


async def _agreed(st) -> None:
    """Agree the list, then record the decided scope that freezes it (section 5, steps 2-4)."""
    fake = FakeOllama([_agree_reply()])
    out = await ask(st, AskArgs(question="Which questions?", answer_tool="agree_questions"), runtime(fake, SCOUT))
    assert isinstance(out, Answered)
    apply_record(st, "decided", {"scope": "question_list"})


_OPTS = ["JSON file", "SQLite database"]  # phase 5b: every settle ask carries options


def _ask(tool, question="Settle which?") -> AskArgs:
    return AskArgs(question=question, answer_tool=tool, options=_OPTS if tool == "settle" else [])


def _tool_text(body: bytes) -> str:
    return json.loads(body)["messages"][-1]["content"]


# 1
def test_01_scout_start_is_agreeing_with_no_questions():
    st = _start()
    assert st.scout_phase == "agreeing"
    assert st.questions == []
    assert st.settled_answers == []


# 2
async def test_02_settle_while_agreeing_is_an_error_with_no_request():
    st = _start()
    fake = FakeOllama([])
    out = await ask(st, _ask("settle"), runtime(fake, SCOUT))
    assert isinstance(out, Error)
    assert fake.requests == []


# 3
async def test_03_set_plan_in_a_scout_session_is_an_error():
    fake = FakeOllama([])
    out = await ask(_start(), _ask("set_plan"), runtime(fake, SCOUT))
    assert isinstance(out, Error)
    assert fake.requests == []


# 4
async def test_04_request_for_agree_questions_uses_the_charter_and_the_tool_entry():
    fake = FakeOllama([_agree_reply()])
    await ask(_start(), _ask("agree_questions"), runtime(fake, SCOUT))
    body = json.loads(fake.requests[0])
    assert body["messages"][0]["content"] == SCOUT_CHARTER
    assert SCOUT_CHARTER == (FIX / "scout_charter.txt").read_text()
    assert body["tools"][-1] == TOOLS["agree_questions"]
    assert set(body) == {"model", "messages", "stream", "tools"}


# 5
async def test_05_agree_questions_answer_returns_the_list():
    fake = FakeOllama([_agree_reply()])
    out = await ask(_start(), _ask("agree_questions"), runtime(fake, SCOUT))
    assert isinstance(out, Answered)
    assert out.tool == "agree_questions"
    assert len(out.result["questions"]) == 2


# 6
async def test_06_agree_questions_missing_carry_forward_is_bounced():
    fake = FakeOllama([call("agree_questions", questions=_questions()), _agree_reply()])
    out = await ask(_start(), _ask("agree_questions"), runtime(fake, SCOUT))
    assert isinstance(out, Answered)
    assert "carry_forward" in _tool_text(fake.requests[1])


# 7
async def test_07_agree_questions_with_duplicate_ids_is_bounced_naming_the_id():
    fake = FakeOllama([_agree_reply(("q1", "q1")), _agree_reply(("q1", "q2"))])
    out = await ask(_start(), _ask("agree_questions"), runtime(fake, SCOUT))
    assert isinstance(out, Answered)
    assert "id" in _tool_text(fake.requests[1]) and "q1" in _tool_text(fake.requests[1])


# 8
async def test_08_recording_the_question_list_freezes_it_and_starts_settling():
    st = _start()
    await _agreed(st)
    assert st.scout_phase == "settling"
    assert S.current_question_id(st) == "q1"


# 9
async def test_09_settle_for_a_later_question_is_bounced_naming_question_id():
    st = _start()
    await _agreed(st)
    fake = FakeOllama([_settle("q2"), _settle("q1")])
    out = await ask(st, _ask("settle"), runtime(fake, SCOUT))
    assert isinstance(out, Answered)
    assert "question_id" in _tool_text(fake.requests[1])
    assert [a.question_id for a in st.settled_answers] == ["q1"]


# 10
async def test_10_settle_with_one_alternative_is_bounced_naming_alternatives():
    st = _start()
    await _agreed(st)
    fake = FakeOllama([_settle("q1", alternatives=[_alts()[0]]), _settle("q1")])
    out = await ask(st, _ask("settle"), runtime(fake, SCOUT))
    assert isinstance(out, Answered)
    assert "alternatives" in _tool_text(fake.requests[1])


# 11
async def test_11_decision_matching_no_alternative_is_bounced_naming_decision():
    st = _start()
    await _agreed(st)
    fake = FakeOllama([_settle("q1", decision="Postgres"), _settle("q1")])
    out = await ask(st, _ask("settle"), runtime(fake, SCOUT))
    assert isinstance(out, Answered)
    assert "decision" in _tool_text(fake.requests[1])


# 12
async def test_12_incompatible_choice_without_complexity_justification_is_bounced():
    incompatible = [dict(_alts()[0]), dict(_alts()[1], backwards_compatible=False)]
    st = _start()
    await _agreed(st)
    fake = FakeOllama(
        [_settle("q1", decision="B", alternatives=incompatible), _settle("q1", decision="B", alternatives=incompatible, complexity="needs queries")]
    )
    out = await ask(st, _ask("settle"), runtime(fake, SCOUT))
    assert isinstance(out, Answered)
    assert "complexity_justification" in _tool_text(fake.requests[1])


# 13
async def test_13_valid_settle_records_the_answer_and_moves_on():
    st = _start()
    await _agreed(st)
    fake = FakeOllama([_settle("q1")])
    out = await ask(st, _ask("settle"), runtime(fake, SCOUT))
    assert isinstance(out, Answered)
    assert [a.question_id for a in st.settled_answers] == ["q1"]
    assert S.current_question_id(st) == "q2"
    assert out.result["next_question_id"] == "q2"


# 14
async def test_14_settling_the_last_question_finishes_and_further_asks_are_errors():
    st = _start()
    await _agreed(st)
    fake = FakeOllama([_settle("q1"), _settle("q2")])
    await ask(st, _ask("settle"), runtime(fake, SCOUT))
    last = await ask(st, _ask("settle"), runtime(fake, SCOUT))
    assert isinstance(last, Answered) and st.scout_phase == "done"
    out = await ask(st, _ask("settle"), runtime(fake, SCOUT))
    assert isinstance(out, Error)
    assert len(fake.requests) == 2


# 15
async def test_15_resettling_after_a_challenge_replaces_the_entry():
    st = _start()
    await _agreed(st)
    fake = FakeOllama([_settle("q1", reasoning="first"), _settle("q1", reasoning="after challenge")])
    await ask(st, _ask("settle"), runtime(fake, SCOUT))
    apply_record(st, "challenge", {"text": "alternatives not surveyed"})
    out = await ask(st, _ask("settle"), runtime(fake, SCOUT))
    assert isinstance(out, Answered)
    assert len(st.settled_answers) == 1
    assert st.settled_answers[0].reasoning == "after challenge"


# 16
def _messages_slice(body: bytes) -> bytes:
    start = body.index(b'"messages":[')
    return body[start : body.index(b'],"model":')]


async def test_16_prefix_is_byte_stable_across_a_tool_round_in_settling():
    st = _start()
    await _agreed(st)
    fake = FakeOllama([call("read_file", path="a.py", justification="what the store looks like"), _settle("q1")])
    rt = runtime(fake, SCOUT)
    first = await ask(st, _ask("settle"), rt)
    assert isinstance(first, Answered) or first.status == "tool_calls"
    if first.status == "tool_calls":
        await submit(st, SubmitArgs(results=[Result(call_id=first.calls[0].call_id, status="ran", content="class Store")]), rt)
    assert len(fake.requests) == 2
    assert _messages_slice(fake.requests[1]).startswith(_messages_slice(fake.requests[0]))


# 17
def test_17_summary_with_two_settled_answers_and_one_pending_matches_golden():
    st = S.create_session("scout", "Settle storage format")
    st.scout_phase = "settling"
    st.questions = [S.ScoutQuestion(id="Q1", question="JSON or SQLite?"), S.ScoutQuestion(id="Q2", question="Where is state stored?"), S.ScoutQuestion(id="Q3", question="Is the format versioned?")]
    st.settled_answers = [
        S.SettledAnswer(question_id="Q1", question="JSON or SQLite?", options={"A": "JSON file", "B": "SQLite database"}, alternatives=[S.Alternative(name="A", case_for="no deps", case_against="no queries"), S.Alternative(name="B", case_for="queries", case_against="new dep")], decision="A", reasoning="simplest, additive later"),
        S.SettledAnswer(question_id="Q2", question="Where is state stored?", options={"A": "one file", "B": "per-session dir"}, alternatives=[S.Alternative(name="A", case_for="simple", case_against="no concurrency"), S.Alternative(name="B", case_for="isolation", case_against="more files")], decision="Other", reasoning="matches the state.json layout", other=S.OtherAnswer(summary="a shared directory outside the repo", case_for="shared", case_against="more files", backwards_compatible=True)),
    ]
    text = render_summary(st)
    assert text == (GOLDEN / "g4_scout_two_settled.txt").read_text()
    assert text.index("SETTLED ANSWERS:") < text.index("OPEN QUESTIONS:")


# 18
@pytest.mark.skipif(shutil.which("claude") is None, reason="claude CLI not on PATH")
def test_18_claude_plugin_validate_passes_with_the_scout_skill():
    assert SKILL.exists()
    proc = subprocess.run(["claude", "plugin", "validate", str(REPO)], capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr


# Extra checks for the decisions above


def test_skill_frontmatter_matches_the_phase_doc():
    text = SKILL.read_text()
    assert text.startswith("---\nname: scout\n")
    assert "disable-model-invocation: true" in text
    assert "allowed-tools: mcp__plugin_navigator_navigator__*" in text


# Fix plan section 6: the skill quotes the exact payload key lines that the server accepts and describes.
def test_scout_skill_quotes_each_record_kinds_keys():
    text = SKILL.read_text()
    for line in S.RECORD_CONTRACT.values():
        assert line in text, line


async def test_agree_questions_is_refused_once_the_list_is_frozen():
    st = _start()
    await _agreed(st)
    fake = FakeOllama([])
    out = await ask(st, _ask("agree_questions"), runtime(fake, SCOUT))
    assert isinstance(out, Error)
    assert fake.requests == []


# Justification form in scout mode (decided 2026-11-02): a tool parameter on every exploration tool, as in pair mode


async def test_19_scout_request_offers_justification_as_a_required_tool_parameter():
    fake = FakeOllama([_agree_reply()])
    await ask(_start(), _ask("agree_questions"), runtime(fake, SCOUT))
    tools = json.loads(fake.requests[0])["tools"]
    by_name = {t["function"]["name"]: t["function"]["parameters"] for t in tools}
    for name in ["read_file", "list_dir", "grep", "screenshot", "web_search"]:
        assert "justification" in by_name[name]["properties"], name
        assert "justification" in by_name[name]["required"], name
    assert "justification" not in by_name["need_info"]["properties"]  # need_info takes questions, not a justification
    assert "justification" not in by_name["agree_questions"]["properties"]  # answer tools carry no justification


async def test_20_scout_exploration_call_without_justification_is_bounced():
    st = _start()
    await _agreed(st)
    fake = FakeOllama([call("read_file", path="a.py"), _settle("q1")])
    out = await ask(st, _ask("settle"), runtime(fake, SCOUT))
    assert isinstance(out, Answered)
    assert "justification is required" in _tool_text(fake.requests[1])
