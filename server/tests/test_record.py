"""Phase 2 navigator_record tests (docs/phases/phase-2-session-state.md, section 2)."""

import pytest
from pydantic import ValidationError

from navigator_mcp.session import RejectedClaim, SessionState, apply_record


def _state(**kw) -> SessionState:
    base = dict(session_id="0" * 32, mode="pair", goal="g")
    base.update(kw)
    return SessionState(**base)


def test_decided_appends_with_rationale_defaulting_empty():
    s = apply_record(_state(), "decided", {"text": "Use X", "rationale": "because"})
    assert [(d.text, d.rationale) for d in s.decided] == [("Use X", "because")]
    s = apply_record(s, "decided", {"text": "Use Y"})
    assert s.decided[1].rationale == ""


def test_rejected_claim_appends():
    s = apply_record(_state(), "rejected_claim", {"claim": "c", "evidence": "e"})
    assert s.rejected_claims == [RejectedClaim(claim="c", evidence="e")]


def test_clarification_appends():
    s = apply_record(_state(), "clarification", {"question": "q", "answer": "a"})
    assert [(c.question, c.answer) for c in s.clarifications] == [("q", "a")]


def test_step_done_marks_step_and_advances_current_step():
    s = _state(steps=[{"id": "1", "change": "a", "verification": "v"}, {"id": "2", "change": "b", "verification": "v"}],
               current_step="1")
    s = apply_record(s, "step_done", {"step_id": "1"})
    assert s.done == ["1"]
    assert s.current_step == "2"
    s = apply_record(s, "step_done", {"step_id": "1"})  # already done: no duplicate
    assert s.done == ["1"]
    s = apply_record(s, "step_done", {"step_id": "2"})
    assert s.current_step is None


def test_step_done_unknown_step_raises_and_changes_nothing():
    s = _state(steps=[{"id": "1", "change": "a", "verification": "v"}], current_step="1")
    before = s.model_copy(deep=True)
    with pytest.raises(ValueError):
        apply_record(s, "step_done", {"step_id": "9"})
    assert s == before


@pytest.mark.parametrize("text", ["challenge text", "x"])
def test_challenge_sets_open_challenge(text):
    assert apply_record(_state(), "challenge", {"text": text}).open_challenge == text


@pytest.mark.parametrize("text", [None, ""])
def test_challenge_none_or_empty_clears(text):
    s = apply_record(_state(open_challenge="old"), "challenge", {"text": text})
    assert s.open_challenge is None


def test_carry_forward_kept_changes_nothing():
    s = _state(carry_forward=["keep me"])
    after = apply_record(s, "carry_forward_verdict", {"line": "keep me", "verdict": "kept"})
    assert after == s


def test_carry_forward_kept_with_unknown_line_raises():
    s = _state(carry_forward=["a"])
    before = s.model_copy(deep=True)
    with pytest.raises(ValueError):
        apply_record(s, "carry_forward_verdict", {"line": "zzz", "verdict": "kept"})
    assert s == before


def test_carry_forward_wrong_removes_line_and_records_rejected_claim():
    s = _state(carry_forward=["a", "b", "a"])
    s = apply_record(s, "carry_forward_verdict", {"line": "a", "verdict": "wrong", "evidence": "api/x.py:3"})
    assert s.carry_forward == ["b", "a"]  # first equal line removed
    assert s.rejected_claims == [RejectedClaim(claim="a", evidence="api/x.py:3")]


def test_carry_forward_wrong_without_evidence_raises_and_changes_nothing():
    s = _state(carry_forward=["a"])
    before = s.model_copy(deep=True)
    with pytest.raises(ValueError):
        apply_record(s, "carry_forward_verdict", {"line": "a", "verdict": "wrong", "evidence": ""})
    assert s == before


def test_carry_forward_wrong_with_unknown_line_raises():
    s = _state(carry_forward=["a"])
    with pytest.raises(ValueError):
        apply_record(s, "carry_forward_verdict", {"line": "zzz", "verdict": "wrong", "evidence": "e"})


def test_unknown_kind_raises_validation_error():
    with pytest.raises(ValidationError):
        apply_record(_state(), "nonsense", {})


def test_extra_payload_key_raises_validation_error():
    with pytest.raises(ValidationError):
        apply_record(_state(), "decided", {"text": "t", "bogus": 1})


def test_failed_record_leaves_state_equal_to_pre_call_copy():
    s = _state(decided=[], carry_forward=["a"])
    before = s.model_copy(deep=True)
    with pytest.raises(ValueError):
        apply_record(s, "carry_forward_verdict", {"line": "a", "verdict": "wrong", "evidence": ""})
    assert s == before
