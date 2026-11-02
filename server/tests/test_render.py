"""Phase 2 rendering tests (docs/phases/phase-2-session-state.md, sections 4-5)."""

import re
import uuid
from pathlib import Path

from navigator_mcp import session
from navigator_mcp.render import render_stable, render_summary, render_volatile
from navigator_mcp.session import (
    Alternative,
    Clarification,
    Decided,
    FileRead,
    RejectedClaim,
    SessionState,
    SettledAnswer,
    ScoutQuestion,
    Shot,
    Step,
    ActionLogEntry,
    WebSearch,
)

GOLDEN = Path(__file__).parent / "golden"
ZERO = "0" * 32


def g1_state() -> SessionState:
    return SessionState(session_id=ZERO, mode="pair", goal="Add retry to fetch_user")


def g2_state() -> SessionState:
    s = g1_state()
    s.context = "Repo: api/. Python 3.12."
    s.decided = [Decided(text="Use exponential backoff", rationale="Matches api/http.py")]
    s.rejected_claims = [RejectedClaim(claim="fetch_user is async", evidence="api/users.py:12 def fetch_user")]
    s.clarifications = [Clarification(question="Which HTTP client?", answer="httpx, api/http.py:3")]
    s.files_read = [FileRead(path="api/users.py", start=1, end=40, what="fetch_user definition")]
    s.web_searches = [WebSearch(query="httpx retry", url="https://example.com/r", what="retries option")]
    s.shots = [Shot(k=1, target="http://localhost:3000/users", what="user list renders")]
    s.action_log = [
        ActionLogEntry(
            call_id="call-0001",
            name="read_file",
            args_summary="api/users.py:1-40",
            status="ran",
            result_summary="40 lines",
        )
    ]
    s.steps = [
        Step(id="1", files=["api/users.py"], change="add retry loop", verification="pytest api"),
        Step(id="2", files=[], change="add timeout", verification="pytest api"),
    ]
    s.done = ["1"]
    s.current_step = "2"
    s.open_challenge = "Step 2 skips timeout"
    s.carry_forward = ["Backoff base is 0.5s"]
    return s


def g3_state() -> SessionState:
    return SessionState(
        session_id=ZERO,
        mode="scout",
        goal="Settle storage format",
        settled_answers=[
            SettledAnswer(
                question_id="Q1",
                question="JSON or SQLite?",
                alternatives=[
                    Alternative(name="JSON", case_for="no deps", case_against="no queries"),
                    Alternative(name="SQLite", case_for="queries", case_against="new dep"),
                ],
                decision="JSON",
                reasoning="simplest, additive later",
            )
        ],
        scout_phase="settling",
        questions=[
            ScoutQuestion(id="Q1", question="JSON or SQLite?"),
            ScoutQuestion(id="Q2", question="Where is state stored?"),
        ],
    )


# Golden summaries (section 5, G1-G3)


def test_g1_empty_pair_state_matches_golden():
    assert render_summary(g1_state()) == (GOLDEN / "g1_empty_pair.txt").read_text()


def test_g2_populated_pair_state_matches_golden():
    assert render_summary(g2_state()) == (GOLDEN / "g2_populated_pair.txt").read_text()


def test_g3_scout_state_matches_golden_and_omits_pair_sections():
    text = render_summary(g3_state())
    assert text == (GOLDEN / "g3_scout.txt").read_text()
    assert "STEPS:" not in text
    assert "CURRENT STEP:" not in text


def test_g4_multiline_values_are_indented_and_trailing_spaces_stripped():
    s = g1_state()
    s.goal = "a\r\nb  "
    assert "GOAL: a\n  b\nMODE: pair" in render_summary(s)


# Property tests (section 5)


def test_render_twice_is_identical():
    assert render_summary(g2_state()) == render_summary(g2_state())


def test_save_and_load_round_trip_renders_identically(state_dir):
    s = g2_state()
    session.save_state(s)
    loaded = session.load_state(s.session_id)
    assert loaded == s
    assert render_summary(loaded) == render_summary(s)


def test_volatile_mutations_leave_stable_block_unchanged():
    base = g2_state()
    for mutate in [
        lambda s: s.steps.append(Step(id="3", change="x", verification="y")),
        lambda s: s.done.append("2"),
        lambda s: setattr(s, "current_step", None),
        lambda s: setattr(s, "open_challenge", "a different challenge"),
        lambda s: setattr(s, "carry_forward", ["another line"]),
    ]:
        s = base.model_copy(deep=True)
        before_stable = render_stable(s)
        mutate(s)
        assert render_stable(s) == before_stable


def test_append_only_mutation_changes_stable_and_not_volatile():
    s = g2_state()
    before_volatile = render_volatile(s)
    before_stable = render_stable(s)
    s.decided.append(Decided(text="Second decision"))
    assert render_stable(s) != before_stable
    assert render_volatile(s) == before_volatile


def test_no_timestamps_and_no_session_id_in_render():
    s = g2_state()
    s.session_id = uuid.uuid4().hex
    text = render_summary(s)
    assert not re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", text)
    assert s.session_id not in text


def test_summary_is_stable_block_then_volatile_block():
    s = g2_state()
    assert render_summary(s) == render_stable(s) + "\n" + render_volatile(s)


# Transform hook (section 4)


def test_transform_applies_only_to_the_four_prose_fields():
    text = render_summary(g2_state(), lambda t, k: f"<{k}>")
    assert "- Use exponential backoff | why: <rationale>" in text
    assert "A: <clarification>" in text
    assert "(<web_result>)" in text
    assert "OPEN CHALLENGE: <challenge>" in text
    for protected in ["api/users.py:1-40", "call-0001", "https://example.com/r", "Backoff base is 0.5s"]:
        assert protected in text
        assert f"<{protected}>" not in text


def test_transform_none_equals_identity():
    s = g2_state()
    assert render_summary(s, None) == render_summary(s, lambda t, k: t)


def test_transform_is_never_called_with_empty_text():
    calls = []

    def record(text, kind):
        calls.append((text, kind))
        return text

    render_summary(g1_state(), record)
    assert calls == []
    assert all(text != "" for text, _ in calls)
