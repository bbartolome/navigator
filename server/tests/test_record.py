"""Phase 2 navigator_record tests (docs/phases/phase-2-session-state.md, section 2).

Readable-error tests follow docs/fix-navigator-record.md sections 1, 2 and 6.
"""

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError

from navigator_mcp import server
from navigator_mcp import session as S
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


# Readable errors (fix plan sections 1, 2 and 6): each error names the offending key and the allowed keys.


def _describe(kind: str, payload: dict) -> str:
    with pytest.raises(ValidationError) as info:
        apply_record(_state(), kind, payload)
    return S.describe_record_error(info.value)


# The six payloads the driver sent in the two real sessions (fix plan section 1).
@pytest.mark.parametrize(
    ("kind", "payload", "expected"),
    [
        (
            "decided",
            {"plan": "p", "deferred": ["d"], "note": "n"},
            ["decided:", "unknown key 'plan'", "unknown key 'deferred'", "unknown key 'note'",
             "Allowed keys for decided: text, rationale, scope"],
        ),
        (
            "step_done",
            {"id": "E2", "commits": ["abc"], "tests": "pass", "note": "n"},
            ["step_done:", "unknown key 'id'", "missing 'step_id'", "Allowed keys for step_done: step_id"],
        ),
        (
            "challenge",
            {"question_id": "Q1", "challenge": "c"},
            ["challenge:", "missing 'text'", "unknown key 'question_id'", "unknown key 'challenge'",
             "Allowed keys for challenge: text"],
        ),
        (
            "challenge",
            {"question_id": "Q1", "text": "c"},
            ["challenge:", "unknown key 'question_id'", "Allowed keys for challenge: text"],
        ),
        (
            "challenge",
            {"scope": "s", "claim": "c", "evidence": "e"},
            ["challenge:", "missing 'text'", "unknown key 'scope'", "unknown key 'claim'", "unknown key 'evidence'"],
        ),
        (
            "carry_forward_verdict",
            {"question_id": "Q1", "verdict": "accepted", "note": "n"},
            ["carry_forward_verdict:", "missing 'line'", "verdict must be one of: kept, wrong",
             "unknown key 'question_id'", "unknown key 'note'",
             "Allowed keys for carry_forward_verdict: line, verdict, evidence"],
        ),
    ],
)
def test_real_bad_payloads_name_the_offending_keys_and_the_allowed_keys(kind, payload, expected):
    message = _describe(kind, payload)
    for fragment in expected:
        assert fragment in message, (fragment, message)


def test_unknown_kind_lists_the_kinds():
    message = _describe("nonsense", {})
    assert "unknown kind 'nonsense'" in message
    for kind in S.RECORD_CONTRACT:
        assert kind in message


def test_unknown_step_id_lists_the_valid_step_ids():
    steps = [{"id": "E1", "change": "a", "verification": "v"}, {"id": "E2", "change": "b", "verification": "v"}]
    s = _state(steps=steps, current_step="E1")
    with pytest.raises(ValueError) as info:
        apply_record(s, "step_done", {"step_id": "E9"})
    assert str(info.value) == "unknown step_id: E9; valid step ids: E1, E2"


def test_unknown_step_id_without_a_plan_says_there_is_no_plan():
    with pytest.raises(ValueError) as info:
        apply_record(_state(), "step_done", {"step_id": "E1"})
    assert "no plan yet (no set_plan answer)" in str(info.value)


def test_verdict_on_a_line_not_in_carry_forward_lists_the_current_lines():
    s = _state(carry_forward=["keep A", "keep B"])
    with pytest.raises(ValueError) as info:
        apply_record(s, "carry_forward_verdict", {"line": "zzz", "verdict": "kept"})
    assert "current carry_forward lines:\n1. keep A\n2. keep B" in str(info.value)


def test_verdict_with_an_empty_carry_forward_says_so():
    with pytest.raises(ValueError, match="carry_forward is empty"):
        apply_record(_state(), "carry_forward_verdict", {"line": "zzz", "verdict": "kept"})


# The tool surface: a record that fails leaves the client a readable ToolError (fix plan section 1).


def test_server_record_error_reaches_the_client_as_its_text():
    sid = S.create_session("pair", "g", "").session_id
    with pytest.raises(ToolError) as info:
        server.navigator_record(sid, "step_done", {"id": "E2"})
    assert "unknown key 'id'" in str(info.value)
    assert "missing 'step_id'" in str(info.value)


def test_server_record_on_an_ended_session_is_refused():
    sid = S.create_session("pair", "g", "").session_id
    server.navigator_end(sid)
    with pytest.raises(ToolError, match="session ended"):
        server.navigator_record(sid, "decided", {"text": "x"})


def test_server_unknown_session_is_a_readable_tool_error():
    with pytest.raises(ToolError, match="unknown session_id: " + "0" * 32):
        server.navigator_status("0" * 32)


def test_contract_lines_name_every_field_of_each_kind():
    # Guards the description and the skills against drift from the Rec models.
    assert set(S.RECORD_CONTRACT) == set(S.RECORD_MODELS)
    for kind, model in S.RECORD_MODELS.items():
        for name in model.model_fields:
            if name != "kind":
                assert name in S.RECORD_CONTRACT[kind], (kind, name)
