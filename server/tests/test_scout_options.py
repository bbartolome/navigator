"""Phase 5b: driver-supplied options on scout settle (docs/phases/phase-5b-scout-options.md).

This file collects the phase 5b tests. Task 1 (the data model) is covered here; later tasks add their own sections.
"""

import json

import pytest
from pydantic import ValidationError

from navigator_mcp import session as S
from navigator_mcp.ollama import AskArgs


def _old_settled_answer() -> dict:
    """A SettledAnswer as written before phase 5b: no options and no other."""
    return {
        "question_id": "Q1",
        "question": "JSON or SQLite?",
        "alternatives": [
            {"name": "JSON", "case_for": "no deps", "case_against": "no queries", "backwards_compatible": True},
        ],
        "decision": "JSON",
        "reasoning": "simplest",
        "complexity_justification": None,
    }


# Task 1: the data model. Nothing reads these fields yet, so the behaviour is unchanged.


def test_old_settled_answer_loads_with_empty_options_and_no_other():
    answer = S.SettledAnswer.model_validate(_old_settled_answer())
    assert answer.options == {}
    assert answer.other is None


def test_settled_answer_round_trips_options_and_other():
    answer = S.SettledAnswer(
        question_id="Q2",
        question="Where is state stored?",
        alternatives=[S.Alternative(name="A", case_for="simple", case_against="no concurrency")],
        decision="Other",
        reasoning="matches the state.json layout",
        options={"A": "one file", "B": "per-session dir"},
        other=S.OtherAnswer(
            summary="a shared directory outside the repo",
            case_for="shared",
            case_against="more files",
            backwards_compatible=True,
        ),
    )
    again = S.SettledAnswer.model_validate_json(answer.model_dump_json())
    assert again == answer


def test_other_answer_needs_every_field():
    with pytest.raises(ValidationError):
        S.OtherAnswer(summary="s", case_for="c", case_against="a")


def test_other_answer_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        S.OtherAnswer(summary="s", case_for="c", case_against="a", backwards_compatible=True, extra=1)


def test_session_state_written_before_ask_options_loads_with_none():
    state = S.create_session("scout", "Settle storage format", "Ticket T-1")
    data = json.loads(state.model_dump_json())
    data.pop("ask_options")
    loaded = S.SessionState.model_validate(data)
    assert loaded.ask_options is None


def test_ask_args_options_default_to_an_empty_list():
    assert AskArgs(question="Which?", answer_tool="agree_questions").options == []


def test_ask_args_keeps_the_options_it_is_given():
    args = AskArgs(question="Which?", answer_tool="settle", options=["JSON file", "SQLite database"])
    assert args.options == ["JSON file", "SQLite database"]


# Task 2: the SETTLED ANSWERS line (section 7). Option text is shown, so the summary reads without the legend.

from navigator_mcp.render import _settled_line  # noqa: E402


def _answer(**overrides) -> S.SettledAnswer:
    fields = dict(
        question_id="Q1",
        question="JSON or SQLite?",
        alternatives=[
            S.Alternative(name="A", case_for="no deps", case_against="no queries"),
            S.Alternative(name="B", case_for="queries", case_against="new dep"),
        ],
        decision="A",
        reasoning="simplest",
        options={"A": "JSON file", "B": "SQLite database"},
    )
    fields.update(overrides)
    return S.SettledAnswer(**fields)


def test_label_decision_prints_its_option_text_and_each_alternative_with_text():
    assert _settled_line(_answer()) == (
        "Q1: JSON or SQLite? | decision: A: JSON file | reasoning: simplest"
        " | alternatives: A: JSON file (for: no deps; against: no queries); B: SQLite database (for: queries; against: new dep)"
    )


def test_other_decision_prints_the_summary_and_not_the_found_alternative_case():
    answer = _answer(
        decision="Other",
        alternatives=[S.Alternative(name="A", case_for="no deps", case_against="no queries")],
        other=S.OtherAnswer(summary="a shared directory", case_for="one writer at a time", case_against="collisions", backwards_compatible=True),
    )
    line = _settled_line(answer)
    assert "| decision: Other: a shared directory |" in line
    assert "one writer at a time" not in line and "collisions" not in line  # the found alternative's case is not printed


def test_extra_alternative_prints_its_name_as_it_is():
    answer = _answer(alternatives=[
        S.Alternative(name="A", case_for="no deps", case_against="no queries"),
        S.Alternative(name="SQLite", case_for="queries", case_against="new dep"),
    ])
    assert "; SQLite (for: queries; against: new dep)" in _settled_line(answer)


def test_old_answer_with_no_options_prints_exactly_as_before():
    old = S.SettledAnswer(
        question_id="Q1",
        question="JSON or SQLite?",
        alternatives=[S.Alternative(name="JSON", case_for="no deps", case_against="no queries"), S.Alternative(name="SQLite", case_for="queries", case_against="new dep")],
        decision="JSON",
        reasoning="simplest, additive later",
    )
    assert _settled_line(old) == (
        "Q1: JSON or SQLite? | decision: JSON | reasoning: simplest, additive later"
        " | alternatives: JSON (for: no deps; against: no queries); SQLite (for: queries; against: new dep)"
    )
