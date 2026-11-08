"""Scout mode (phase 5): the charter, agree_questions and settle answer tools, and their checks."""

from .. import session as S
from ..ollama import Mode  # noqa: F401  (documents the protocol this class satisfies)

# Byte-equal to the section 3 charter in docs/phases/phase-5-scout.md (tests/test_scout.py test 4).
SCOUT_CHARTER = 'You are the NAVIGATOR. You decide; the driver implements. Serve the project owner, not the driver:\ncorrectness, then evolvable contracts, then simplicity. Disagree when the evidence says so.\nCite path:line, or the URL for a web result, for every claim. What the SESSION SUMMARY lists counts as read; mark\nanything else you haven\'t read UNVERIFIED. Contracts change additively; flag one-way doors; defer what the spec\ndoesn\'t require.\nExplore with your tools until you can cite evidence for a decision, then stop. Tool calls need no text, only a filled\njustification: for each call, what you expect to learn and how it bears on the current decision. When no read can\nsettle it, call need_info.\nCLARIFICATION. Before you decide you may call need_info. Each entry names one missing fact or intent and says why it\nmatters to the decision, in the form "<fact> -- matters because <reason>". The driver answers from verified sources\nonly and will not guess for you.\nALTERNATIVES. Before choosing, survey every plausible alternative, including ones the driver has not raised. For each,\nstate the strongest case for it and the strongest case against it. A decision without a surveyed set of alternatives\nis incomplete and the driver will challenge it.\nBIAS. Favour the simplest backwards-compatible option that maximizes flexibility for future changes. If you choose\nanything more complex or compatibility-breaking, say why it is needed; otherwise the driver will challenge it.\nPHASES. The SESSION SUMMARY names the scout phase. While agreeing, propose the question list by calling\nagree_questions. While settling, answer only the current question by calling settle, once.\nAnswer the one question asked only by calling the tool the driver names, once; put nothing in text. Carry forward up\nto 5 lines you must remember for later questions.'

# The two answer-tool entries of phase 5 section 4, in the Ollama tools-entry shape.
AGREE_QUESTIONS_TOOL = {'type': 'function', 'function': {'name': 'agree_questions', 'description': 'Propose the ordered list of questions this session will settle, one per turn.', 'parameters': {'type': 'object', 'properties': {'questions': {'type': 'array', 'minItems': 1, 'maxItems': 12, 'items': {'type': 'object', 'properties': {'id': {'type': 'string', 'description': 'Short unique id, e.g. q1.'}, 'question': {'type': 'string', 'description': 'The decision to be settled, as a question.'}, 'why_it_matters': {'type': 'string', 'description': 'What depends on the answer.'}}, 'required': ['id', 'question', 'why_it_matters']}}, 'carry_forward': {'type': 'array', 'maxItems': 5, 'items': {'type': 'string'}, 'description': 'Up to 5 lines to remember for later questions.'}}, 'required': ['questions', 'carry_forward']}}}

SETTLE_TOOL = {'type': 'function', 'function': {'name': 'settle', 'description': 'Settle the current question: alternatives surveyed, the decision, the reasoning.', 'parameters': {'type': 'object', 'properties': {'question_id': {'type': 'string', 'description': 'Id of the current question.'}, 'alternatives': {'type': 'array', 'minItems': 2, 'items': {'type': 'object', 'properties': {'name': {'type': 'string'}, 'case_for': {'type': 'string', 'description': 'Strongest case for, citing path:line or URL.'}, 'case_against': {'type': 'string', 'description': 'Strongest case against, citing path:line or URL.'}, 'backwards_compatible': {'type': 'boolean'}}, 'required': ['name', 'case_for', 'case_against', 'backwards_compatible']}}, 'decision': {'type': 'string', 'description': 'The `name` of the chosen alternative.'}, 'reasoning': {'type': 'string'}, 'complexity_justification': {'type': 'string', 'description': 'Required when the decision is not the simplest backwards-compatible alternative.'}, 'carry_forward': {'type': 'array', 'maxItems': 5, 'items': {'type': 'string'}, 'description': 'Up to 5 lines to remember for later questions.'}}, 'required': ['question_id', 'alternatives', 'decision', 'reasoning', 'carry_forward']}}}

_CARRY_MAX = 5
_AGREE_MAX = 12
_OPTIONS_MIN = 2
_OPTIONS_MAX = 8


def check_options(answer_tool: str, options: list[str] | None) -> None:
    """Phase 5b section 2: raises ValueError with the driver-facing reason; returns None when the options are valid.

    None and [] both mean no options. ollama._ask and jobs._enqueue_locked call this.
    """
    given = options or []
    if answer_tool != "settle":
        if given:
            raise ValueError("options is only used with answer_tool settle")
        return
    if not given:
        raise ValueError("settle needs options: 2 to 8 plain strings, one per option")
    if not _OPTIONS_MIN <= len(given) <= _OPTIONS_MAX:
        raise ValueError(f"options: 2 to 8 entries; got {len(given)}")
    collapsed = [" ".join(text.split()) for text in given]
    for k, text in enumerate(collapsed, start=1):
        if text == "":
            raise ValueError(f"options: entry {k} is empty")
    first_seen: dict[str, int] = {}
    for k, text in enumerate(collapsed, start=1):
        if text in first_seen:
            raise ValueError(f"options: entries {first_seen[text]} and {k} have the same text")
        first_seen[text] = k


def option_texts(options: list[str]) -> dict[str, str]:
    """Label to collapsed text, labels A upwards in the order given. Call after check_options has passed."""
    return {chr(ord("A") + i): " ".join(text.split()) for i, text in enumerate(options)}


def _carry_problem(args: dict) -> list[str]:
    if "carry_forward" not in args:
        return ["carry_forward"]
    value = args["carry_forward"]
    if isinstance(value, list) and len(value) <= _CARRY_MAX and all(isinstance(x, str) for x in value):
        return []
    return ["carry_forward (a list of at most 5 strings)"]


def _check_agree(args: dict) -> list[str]:
    problems = []
    questions = args.get("questions")
    if not (isinstance(questions, list) and 1 <= len(questions) <= _AGREE_MAX):
        problems.append("questions (1 to 12 entries)")
    else:
        seen: set[str] = set()
        for i, q in enumerate(questions):
            if not isinstance(q, dict):
                problems.append(f"questions[{i}]")
                continue
            for key in ("id", "question", "why_it_matters"):
                if not isinstance(q.get(key), str):
                    problems.append(f"questions[{i}].{key}")
            qid = q.get("id")
            if isinstance(qid, str):
                if qid in seen:
                    problems.append(f"questions[{i}].id (duplicate {qid})")
                seen.add(qid)
    return problems + _carry_problem(args)


def _valid_alternative(a) -> bool:
    return (
        isinstance(a, dict)
        and isinstance(a.get("name"), str)
        and isinstance(a.get("case_for"), str)
        and isinstance(a.get("case_against"), str)
        and isinstance(a.get("backwards_compatible"), bool)
    )


def _check_settle(args: dict, state: S.SessionState | None) -> list[str]:
    problems = []
    qid = args.get("question_id")
    ids = [q.id for q in state.questions] if state else []
    settled = {a.question_id for a in state.settled_answers} if state else set()
    current = S.current_question_id(state) if state else None
    if not isinstance(qid, str) or qid not in ids:
        problems.append(f"question_id (unknown question {qid!r})")
    elif qid != current and qid not in settled:
        problems.append(f"question_id ({qid} is not the current question; the current question is {current})")
    alternatives = args.get("alternatives")
    if not (isinstance(alternatives, list) and len(alternatives) >= 2 and all(_valid_alternative(a) for a in alternatives)):
        problems.append("alternatives (at least two, each with name, case_for, case_against and backwards_compatible)")
        alternatives = []
    names = [a["name"] for a in alternatives]
    decision = args.get("decision")
    if not isinstance(decision, str) or decision not in names:
        problems.append("decision (must name one of the alternatives)")
    else:
        chosen = next(a for a in alternatives if a["name"] == decision)
        justification = args.get("complexity_justification")
        if chosen["backwards_compatible"] is False and not (isinstance(justification, str) and justification.strip()):
            problems.append("complexity_justification (required when the chosen alternative is not backwards compatible)")
    if not isinstance(args.get("reasoning"), str):
        problems.append("reasoning")
    return problems + _carry_problem(args)


class ScoutMode:
    name = "scout"
    charter = SCOUT_CHARTER
    answer_tools = {"agree_questions": AGREE_QUESTIONS_TOOL, "settle": SETTLE_TOOL}

    def ask_error(self, state: S.SessionState, tool: str) -> str | None:
        if tool == "agree_questions" and state.scout_phase != "agreeing":
            return "agree_questions is only offered while the question list is being agreed"
        if tool == "settle" and state.scout_phase != "settling":
            return "settle is only offered while a question is being settled"
        return None

    def missing_fields(self, tool: str, args: dict, state: S.SessionState | None = None) -> list[str]:
        if tool == "agree_questions":
            return _check_agree(args)
        if tool == "settle":
            return _check_settle(args, state)
        return [f"unknown answer tool {tool}"]

    def apply_answer(self, state: S.SessionState, tool: str, args: dict) -> dict:
        if tool == "agree_questions":
            S.set_questions(state, args["questions"])
            return dict(args)
        qid = args["question_id"]
        question = next(q.question for q in state.questions if q.id == qid)
        alternatives = [S.Alternative.model_validate(a) for a in args["alternatives"]]
        S.settle_question(
            state,
            S.SettledAnswer(
                question_id=qid,
                question=question,
                alternatives=alternatives,
                decision=args["decision"],
                reasoning=args["reasoning"],
                complexity_justification=args.get("complexity_justification") or None,
            ),
        )
        return {**args, "next_question_id": S.current_question_id(state)}

    def carry_forward(self, args: dict) -> list[str]:
        return list(args["carry_forward"])


SCOUT = ScoutMode()
