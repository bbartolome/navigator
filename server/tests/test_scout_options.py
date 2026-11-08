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


# Task 3: check_options, option_texts, and the question the navigator reads (sections 2 and 3)

from fakes import FakeOllama, call, runtime  # noqa: E402
from navigator_mcp.modes.scout import SCOUT, check_options, option_texts  # noqa: E402
from navigator_mcp.ollama import Answered, Error, ask  # noqa: E402

SETTLE_OPTIONS = ["JSON file", "SQLite database"]


def test_settle_without_options_is_refused():
    with pytest.raises(ValueError, match="^settle needs options: 2 to 8 plain strings, one per option$"):
        check_options("settle", None)
    with pytest.raises(ValueError, match="^settle needs options"):
        check_options("settle", [])


@pytest.mark.parametrize("options, got", [(["JSON file"], 1), ([f"option {i}" for i in range(9)], 9)])
def test_settle_needs_two_to_eight_options(options, got):
    with pytest.raises(ValueError, match=rf"^options: 2 to 8 entries; got {got}$"):
        check_options("settle", options)


def test_an_entry_that_is_empty_after_collapse_is_refused():
    with pytest.raises(ValueError, match=r"^options: entry 2 is empty$"):
        check_options("settle", ["JSON file", "   \n  "])


def test_entries_with_the_same_collapsed_text_are_refused():
    with pytest.raises(ValueError, match=r"^options: entries 1 and 2 have the same text$"):
        check_options("settle", ["JSON  file", "JSON\nfile"])


def test_valid_settle_options_pass():
    assert check_options("settle", SETTLE_OPTIONS) is None


def test_options_with_another_tool_are_refused():
    with pytest.raises(ValueError, match="^options is only used with answer_tool settle$"):
        check_options("agree_questions", ["x", "y"])


@pytest.mark.parametrize("options", [None, []])
def test_no_options_is_fine_for_other_tools(options):
    assert check_options("agree_questions", options) is None
    assert check_options("set_plan", options) is None


def test_option_texts_collapse_whitespace_and_label_in_order():
    assert option_texts(["JSON\nfile", "  SQLite   database "]) == {"A": "JSON file", "B": "SQLite database"}


def _settling_state() -> S.SessionState:
    st = S.create_session("scout", "Settle storage format", "Ticket T-1")
    st.scout_phase = "settling"
    st.questions = [S.ScoutQuestion(id="q1", question="JSON or SQLite?")]
    return st


def _settle_reply(labels=("A", "B")):
    alternatives = [{"name": x, "case_for": "no deps", "case_against": "no queries", "backwards_compatible": True} for x in labels]
    return call("settle", question_id="q1", alternatives=alternatives, decision="A", reasoning="simplest", carry_forward=[])


def _user_content(fake) -> str:
    return json.loads(fake.requests[0])["messages"][-1]["content"]


@pytest.mark.anyio
async def test_settle_without_options_is_refused_before_any_request():
    fake = FakeOllama([])
    out = await ask(_settling_state(), AskArgs(question="Which?", answer_tool="settle"), runtime(fake, SCOUT))
    assert isinstance(out, Error)
    assert out.reason == "settle needs options: 2 to 8 plain strings, one per option"
    assert fake.requests == []


@pytest.mark.anyio
async def test_question_ends_with_the_options_legend_then_the_suffix():
    st = _settling_state()
    fake = FakeOllama([_settle_reply()])
    out = await ask(st, AskArgs(question="Which one?", answer_tool="settle", options=SETTLE_OPTIONS), runtime(fake, SCOUT))
    assert isinstance(out, Answered)
    assert _user_content(fake).endswith("Which one?\n\nOptions:\nA: JSON file\nB: SQLite database\nAnswer by calling settle.")
    assert st.ask_options == {"A": "JSON file", "B": "SQLite database"}


@pytest.mark.anyio
async def test_three_options_make_a_three_line_legend():
    fake = FakeOllama([_settle_reply(labels=("A", "B", "C"))])
    out = await ask(
        _settling_state(),
        AskArgs(question="Which one?", answer_tool="settle", options=["JSON file", "SQLite database", "one file per session"]),
        runtime(fake, SCOUT),
    )
    assert isinstance(out, Answered)
    assert _user_content(fake).endswith(
        "Which one?\n\nOptions:\nA: JSON file\nB: SQLite database\nC: one file per session\nAnswer by calling settle."
    )


@pytest.mark.anyio
async def test_a_driver_suffix_is_not_doubled_and_the_legend_comes_before_it():
    fake = FakeOllama([_settle_reply()])
    await ask(
        _settling_state(),
        AskArgs(question="Which one?\nAnswer by calling settle.", answer_tool="settle", options=SETTLE_OPTIONS),
        runtime(fake, SCOUT),
    )
    content = _user_content(fake)
    assert content.count("Answer by calling settle.") == 1
    assert content.endswith("Which one?\n\nOptions:\nA: JSON file\nB: SQLite database\nAnswer by calling settle.")


@pytest.mark.anyio
async def test_a_non_settle_ask_clears_ask_options():
    st = S.create_session("scout", "Settle storage format", "Ticket T-1")
    st.ask_options = {"A": "left over from a settle ask"}
    fake = FakeOllama([call("agree_questions", questions=[{"id": "q1", "question": "JSON or SQLite?", "why_it_matters": "storage"}], carry_forward=[])])
    out = await ask(st, AskArgs(question="Which questions?", answer_tool="agree_questions"), runtime(fake, SCOUT))
    assert isinstance(out, Answered)
    assert st.ask_options is None


# Task 4: the settle schema and the server checks on a settle reply (sections 4, 5 and 6)

from navigator_mcp.modes.scout import SETTLE_TOOL  # noqa: E402


def _entry(name, against="no queries", compat=True):
    return {"name": name, "case_for": "no deps", "case_against": against, "backwards_compatible": compat}


def _other(**over):
    o = {"summary": "a shared directory", "case_for": "one place", "case_against": "collisions", "backwards_compatible": True}
    o.update(over)
    return o


def _args(decision="A", alternatives=None, other=None, **extra):
    args = {
        "question_id": "q1",
        "alternatives": alternatives if alternatives is not None else [_entry("A"), _entry("B", "new dep")],
        "decision": decision,
        "reasoning": "simplest",
        "carry_forward": [],
    }
    if other is not None:
        args["other"] = other
    args.update(extra)
    return args


def _checks(args, labels=None):
    st = _settling_state()
    st.ask_options = option_texts(labels or SETTLE_OPTIONS)
    return SCOUT.missing_fields("settle", args, st)


OTHER_REQUIRED = (
    "other (required when decision is Other, with non-empty summary, case_for and case_against, "
    "and a boolean backwards_compatible)"
)
COMPLEXITY = "complexity_justification (required when the chosen alternative is not backwards compatible)"


def test_a_valid_label_decision_passes():
    assert _checks(_args()) == []


def test_a_decision_that_is_not_a_label_is_refused_naming_the_labels():
    assert "decision (must be one of: A, B, Other; got 'E')" in _checks(_args(decision="E"))


def test_decision_is_case_sensitive_so_lowercase_other_is_refused():
    assert "decision (must be one of: A, B, Other; got 'other')" in _checks(_args(decision="other"), labels=None)


def test_a_missing_decision_is_refused_with_no_value():
    args = _args()
    del args["decision"]
    assert "decision (must be one of: A, B, Other; got no value)" in _checks(args)


def test_decision_is_compared_after_strip():
    assert _checks(_args(decision=" A ")) == []


def test_the_labels_in_the_decision_message_follow_the_ask():
    three = [_entry("A"), _entry("B"), _entry("C")]
    assert _checks(_args(decision="D", alternatives=three), labels=["A", "B", "C"]) == [
        "decision (must be one of: A, B, C, Other; got 'D')"
    ]


def test_other_without_an_other_object_is_refused():
    assert OTHER_REQUIRED in _checks(_args(decision="Other", alternatives=[_entry("A"), _entry("B")]))


@pytest.mark.parametrize("field, value", [("summary", ""), ("case_for", "   "), ("case_against", ""), ("backwards_compatible", "yes")])
def test_other_with_an_empty_or_mistyped_field_is_refused(field, value):
    problems = _checks(_args(decision="Other", alternatives=[_entry("A"), _entry("B")], other=_other(**{field: value})))
    assert OTHER_REQUIRED in problems


def test_other_with_a_label_decision_is_refused():
    assert "other (must be omitted unless decision is Other)" in _checks(_args(other=_other()))


def test_other_null_counts_as_omitted_for_a_label_decision():
    assert _checks(_args(other=None)) == []
    args = _args()
    args["other"] = None
    assert _checks(args) == []


def test_a_valid_other_decision_passes_and_needs_an_entry_for_every_option():
    assert _checks(_args(decision="Other", alternatives=[_entry("A"), _entry("B")], other=_other())) == []
    assert "alternatives (missing options: B)" in _checks(_args(decision="Other", alternatives=[_entry("A")], other=_other()))


def test_a_missing_option_is_named():
    assert "alternatives (missing options: B)" in _checks(_args(alternatives=[_entry("A")]))


def test_two_missing_options_are_both_named():
    assert "alternatives (missing options: B, C)" in _checks(_args(alternatives=[_entry("A")]), labels=["A", "B", "C"])


def test_a_duplicate_option_is_refused_and_missing_is_checked_first():
    assert _checks(_args(alternatives=[_entry("A"), _entry("A")])) == ["alternatives (missing options: B)"]
    assert "alternatives (duplicate entry for option: A)" in _checks(
        _args(alternatives=[_entry("A"), _entry("A"), _entry("B")])
    )


def test_an_option_named_with_its_label_and_text_is_refused_as_reserved_with_no_missing_message():
    problems = _checks(_args(alternatives=[_entry("A: JSON file"), _entry("B")]))
    assert problems == [
        "alternatives (name 'A: JSON file' is reserved: name an option by its label alone, such as 'A', "
        "and name other alternatives without A to H or Other)"
    ]


def test_a_lowercase_label_is_reserved():
    problems = _checks(_args(alternatives=[_entry("a"), _entry("B")]))
    assert [p for p in problems if p.startswith("alternatives")] == [
        "alternatives (name 'a' is reserved: name an option by its label alone, such as 'A', "
        "and name other alternatives without A to H or Other)"
    ]


def test_an_extra_alternative_named_in_words_is_accepted():
    assert _checks(_args(alternatives=[_entry("A"), _entry("B"), _entry("SQLite")])) == []


def test_an_extra_named_with_a_reserved_label_is_refused():
    assert any("is reserved" in p for p in _checks(_args(alternatives=[_entry("A"), _entry("B"), _entry("E")])))


def test_an_empty_case_against_is_refused_for_an_option_and_for_an_extra():
    assert "alternatives (case_against is empty for 'A')" in _checks(_args(alternatives=[_entry("A", against="   "), _entry("B")]))
    assert "alternatives (case_against is empty for 'SQLite')" in _checks(
        _args(alternatives=[_entry("A"), _entry("B"), _entry("SQLite", against="")])
    )


def test_an_entry_missing_a_field_is_the_shape_message():
    bad = {"name": "A", "case_for": "x", "backwards_compatible": True}
    assert "alternatives (each entry needs name, case_for, case_against and backwards_compatible)" in _checks(
        _args(alternatives=[bad, _entry("B")])
    )


def test_an_incompatible_label_choice_needs_a_justification():
    alternatives = [_entry("A"), _entry("B", compat=False)]
    assert COMPLEXITY in _checks(_args(decision="B", alternatives=alternatives))
    assert _checks(_args(decision="B", alternatives=alternatives, complexity_justification="needs queries")) == []


def test_an_incompatible_other_needs_a_justification_too():
    other = _other(backwards_compatible=False)
    assert COMPLEXITY in _checks(_args(decision="Other", alternatives=[_entry("A"), _entry("B")], other=other))
    assert _checks(
        _args(decision="Other", alternatives=[_entry("A"), _entry("B")], other=other, complexity_justification="a second store")
    ) == []


def _settle_for(qid, labels=("A", "B"), decision="A", other=None):
    alternatives = [{"name": x, "case_for": "no deps", "case_against": "no queries", "backwards_compatible": True} for x in labels]
    args = dict(question_id=qid, alternatives=alternatives, decision=decision, reasoning="simplest", carry_forward=[])
    if other is not None:
        args["other"] = other
    return call("settle", **args)


@pytest.mark.anyio
async def test_an_other_answer_is_stored_with_its_options_and_returned_with_them():
    st = _settling_state()
    fake = FakeOllama([_settle_for("q1", decision="Other", other=_other())])
    out = await ask(st, AskArgs(question="Which?", answer_tool="settle", options=SETTLE_OPTIONS), runtime(fake, SCOUT))
    assert isinstance(out, Answered)
    stored = st.settled_answers[0]
    assert stored.decision == "Other"
    assert stored.other.summary == "a shared directory"
    assert stored.options == {"A": "JSON file", "B": "SQLite database"}
    assert out.result["options"] == {"A": "JSON file", "B": "SQLite database"}


@pytest.mark.anyio
async def test_the_settle_tool_block_is_identical_across_asks_with_different_options():
    st = _settling_state()
    st.questions.append(S.ScoutQuestion(id="q2", question="Where is state stored?"))
    fake = FakeOllama([_settle_for("q1"), _settle_for("q2", labels=("A", "B", "C"))])
    await ask(st, AskArgs(question="Which?", answer_tool="settle", options=SETTLE_OPTIONS), runtime(fake, SCOUT))
    await ask(st, AskArgs(question="Where?", answer_tool="settle", options=["one file", "per-session dir", "a database"]), runtime(fake, SCOUT))

    def settle_block(body):
        tools = json.loads(body)["tools"]
        return [t for t in tools if t["function"]["name"] == "settle"][0]

    first, second = settle_block(fake.requests[0]), settle_block(fake.requests[1])
    assert first == second == SETTLE_TOOL
    assert json.dumps(first) == json.dumps(second)


# Task 4 (review fixes): the fixture copies the §4 schema, and the apply_answer model fields

import json as _json  # noqa: E402
from pathlib import Path as _Path  # noqa: E402

_FIX = _Path(__file__).parent / "fixtures"


def test_scout_tools_fixture_settle_block_is_the_settle_tool_schema():
    tools = _json.loads((_FIX / "scout_tools.json").read_text())
    assert tools["settle"] == SETTLE_TOOL


def test_an_extra_key_on_other_or_an_alternative_is_dropped_not_bounced():
    st = _settling_state()
    st.ask_options = option_texts(SETTLE_OPTIONS)
    args = _args(
        decision="Other",
        alternatives=[dict(_entry("A"), note="extra"), _entry("B")],
        other=_other(name="extra"),
    )
    answer = SCOUT.apply_answer(st, "settle", args)
    assert answer["next_question_id"] is None
    stored = st.settled_answers[0]
    assert stored.other.summary == "a shared directory"
    assert all(set(a.model_dump()) == {"name", "case_for", "case_against", "backwards_compatible"} for a in stored.alternatives)


def test_a_name_with_spaces_is_stored_stripped():
    st = _settling_state()
    st.ask_options = option_texts(SETTLE_OPTIONS)
    SCOUT.apply_answer(st, "settle", _args(alternatives=[_entry(" A "), _entry("B")], decision="A"))
    assert [a.name for a in st.settled_answers[0].alternatives] == ["A", "B"]


def test_a_decision_with_spaces_is_stored_stripped():
    st = _settling_state()
    st.ask_options = option_texts(SETTLE_OPTIONS)
    SCOUT.apply_answer(st, "settle", _args(decision=" A "))
    assert st.settled_answers[0].decision == "A"
    assert st.settled_answers[0].options == {"A": "JSON file", "B": "SQLite database"}
