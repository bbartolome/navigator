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
