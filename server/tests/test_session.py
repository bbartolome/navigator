"""Phase 2 session tests (docs/phases/phase-2-session-state.md, sections 1, 3, 6)."""

import pytest
from pydantic import ValidationError

from navigator_mcp import session
from navigator_mcp.session import (
    ActionLogEntry,
    Alternative,
    FileRead,
    ScoutQuestion,
    SessionCorrupt,
    SessionNotFound,
    SessionState,
    SettledAnswer,
    Step,
    WebSearch,
    add_file_read,
    add_shot,
    add_web_search,
    alloc_call_id,
    create_session,
    current_question_id,
    freeze_questions,
    load_state,
    log_action,
    save_state,
    session_dir,
    session_lock,
    set_carry_forward,
    set_plan,
    set_questions,
    settle_question,
    state_root,
)

ZERO = "0" * 32


def _pair(**kw) -> SessionState:
    return SessionState(session_id=ZERO, mode="pair", goal="g", **kw)


def _scout(**kw) -> SessionState:
    # Matches create_session: a new scout session starts in "agreeing".
    kw.setdefault("scout_phase", "agreeing")
    return SessionState(session_id=ZERO, mode="scout", goal="g", **kw)


# Persistence (section 3)


def test_round_trip_through_disk(state_dir):
    s = create_session("pair", "Goal", "ctx")
    assert load_state(s.session_id) == s


def test_missing_session_raises_not_found(state_dir):
    with pytest.raises(SessionNotFound):
        load_state("f" * 32)


def test_truncated_json_raises_corrupt(state_dir):
    s = create_session("pair", "Goal")
    path = session_dir(s.session_id) / "state.json"
    path.write_text(path.read_text()[:20])
    with pytest.raises(SessionCorrupt):
        load_state(s.session_id)


@pytest.mark.parametrize("bad", ["../x", "ABCDEF" + "0" * 26, "0" * 31, "g" * 32])
def test_session_dir_rejects_bad_ids(bad):
    with pytest.raises(ValueError):
        session_dir(bad)


def test_failed_replace_keeps_prior_state_parseable(state_dir, monkeypatch):
    s = create_session("pair", "Goal")
    s.goal = "changed"

    def boom(src, dst):
        raise OSError("disk gone")

    monkeypatch.setattr(session.os, "replace", boom)
    with pytest.raises(OSError):
        save_state(s)
    assert load_state(s.session_id).goal == "Goal"  # read path does not use os.replace


def test_state_root_honours_env_then_plugin_data(monkeypatch, tmp_path):
    monkeypatch.setenv("NAVIGATOR_STATE_DIR", str(tmp_path / "a"))
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "data"))
    assert state_root() == tmp_path / "a"
    monkeypatch.delenv("NAVIGATOR_STATE_DIR")
    assert state_root() == tmp_path / "data" / "sessions"


def test_state_root_without_either_variable_raises(monkeypatch):
    monkeypatch.delenv("NAVIGATOR_STATE_DIR")
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    with pytest.raises(RuntimeError):
        state_root()


def test_session_lock_is_one_lock_per_id():
    assert session_lock(ZERO) is session_lock(ZERO)
    assert session_lock(ZERO) is not session_lock("1" * 32)


def test_create_session_sets_scout_phase_only_for_scout(state_dir):
    assert create_session("pair", "g").scout_phase is None
    assert create_session("scout", "g").scout_phase == "agreeing"


# Models (section 1)


def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        SessionState(session_id=ZERO, mode="pair", goal="g", bogus=1)


def test_assignment_is_validated():
    s = _pair()
    with pytest.raises(ValidationError):
        s.mode = "other"


def test_mutable_defaults_are_copied_per_instance():
    # Open question 5 in phase 2: confirm pydantic v2 copies [] defaults per instance.
    a, b = _pair(), _pair()
    a.carry_forward.append("x")
    a.decided.append(session.Decided(text="t"))
    assert b.carry_forward == []
    assert b.decided == []


def test_carry_forward_has_at_most_five_lines():
    with pytest.raises(ValidationError):
        _pair(carry_forward=["1", "2", "3", "4", "5", "6"])


# Mutators (section 1)


def test_alloc_call_id_is_sequential():
    s = _pair()
    assert [alloc_call_id(s) for _ in range(2)] == ["call-0001", "call-0002"]


def test_add_shot_numbers_from_one():
    s = _pair()
    assert add_shot(s, "http://a", "one") == 1
    assert add_shot(s, "http://b", "two") == 2
    assert [sh.k for sh in s.shots] == [1, 2]


def test_log_action_appends_and_rejects_bad_entry():
    s = _pair()
    log_action(s, ActionLogEntry(call_id="call-0001", name="read_file", args_summary="a", status="ran", result_summary="r"))
    assert len(s.action_log) == 1
    with pytest.raises(ValidationError):
        log_action(s, {"call_id": "x"})
    assert len(s.action_log) == 1


def test_add_file_read_and_web_search_skip_equal_records():
    s = _pair()
    fr = FileRead(path="a.py", start=1, end=2, what="w")
    add_file_read(s, fr)
    add_file_read(s, fr)
    add_web_search(s, WebSearch(query="q", url="u", what="w"))
    add_web_search(s, WebSearch(query="q", url="u", what="w"))
    assert len(s.files_read) == 1
    assert len(s.web_searches) == 1


def test_set_plan_keeps_surviving_done_ids_and_sets_current_step():
    s = _pair(done=["1", "9"])
    set_plan(s, [Step(id="1", change="a", verification="v"), Step(id="2", change="b", verification="v")], ["keep"])
    assert s.done == ["1"]
    assert s.current_step == "2"
    assert s.carry_forward == ["keep"]


def test_set_plan_with_all_done_sets_current_step_none():
    s = _pair()
    set_plan(s, [Step(id="1", change="a", verification="v")], [])
    s.done = ["1"]
    set_plan(s, [Step(id="1", change="a", verification="v")], [])
    assert s.current_step is None


def test_set_plan_rejects_six_carry_forward_lines_and_changes_nothing():
    s = _pair(carry_forward=["old"])
    before = s.model_copy(deep=True)
    with pytest.raises(ValueError):
        set_plan(s, [Step(id="1", change="a", verification="v")], list("abcdef"))
    assert s == before


def test_set_carry_forward_rejects_six_lines():
    with pytest.raises(ValueError):
        set_carry_forward(_pair(), list("abcdef"))


# Scout mutators (section 1)


def _scout_with_questions() -> SessionState:
    s = _scout()
    set_questions(s, [ScoutQuestion(id="Q1", question="one?"), ScoutQuestion(id="Q2", question="two?")])
    return s


def _answer(qid: str) -> SettledAnswer:
    return SettledAnswer(
        question_id=qid,
        question=f"{qid}?",
        alternatives=[Alternative(name="A", case_for="x", case_against="y"), Alternative(name="B", case_for="x", case_against="y")],
        decision="A",
        reasoning="r",
    )


def test_set_questions_only_while_agreeing():
    s = _scout_with_questions()
    freeze_questions(s)
    with pytest.raises(ValueError):
        set_questions(s, [ScoutQuestion(id="Q9", question="late?")])


def test_set_questions_rejects_duplicate_ids():
    with pytest.raises(ValueError):
        set_questions(_scout(), [ScoutQuestion(id="Q1", question="a"), ScoutQuestion(id="Q1", question="b")])


def test_set_questions_is_scout_only():
    with pytest.raises(ValueError):
        set_questions(_pair(), [ScoutQuestion(id="Q1", question="a")])


def test_freeze_requires_non_empty_questions():
    with pytest.raises(ValueError):
        freeze_questions(_scout())


def test_freeze_moves_to_settling_and_current_question_is_first():
    s = _scout_with_questions()
    assert current_question_id(s) is None  # not derived until settling
    freeze_questions(s)
    assert s.scout_phase == "settling"
    assert current_question_id(s) == "Q1"


def test_settle_in_order_then_done():
    s = _scout_with_questions()
    freeze_questions(s)
    with pytest.raises(ValueError):
        settle_question(s, _answer("Q2"))  # not the current question
    settle_question(s, _answer("Q1"))
    assert current_question_id(s) == "Q2"
    settle_question(s, _answer("Q2"))
    assert s.scout_phase == "done"
    assert current_question_id(s) is None


def test_resettle_replaces_entry_with_same_id():
    s = _scout_with_questions()
    freeze_questions(s)
    settle_question(s, _answer("Q1"))
    replacement = _answer("Q1")
    replacement.reasoning = "changed after challenge"
    settle_question(s, replacement)
    assert len(s.settled_answers) == 1
    assert s.settled_answers[0].reasoning == "changed after challenge"


def test_settle_rejected_while_agreeing():
    s = _scout_with_questions()
    with pytest.raises(ValueError):
        settle_question(s, _answer("Q1"))
    assert s.settled_answers == []


def test_settle_is_scout_only():
    with pytest.raises(ValueError):
        settle_question(_pair(), _answer("Q1"))
