"""Scout mode (phase 5): the charter, agree_questions and settle answer tools, and their checks."""

from .. import session as S
from ..ollama import Mode  # noqa: F401  (documents the protocol this class satisfies)

# Byte-equal to the section 3 charter in docs/phases/phase-5-scout.md (tests/test_scout.py test 4).
SCOUT_CHARTER = 'You are the NAVIGATOR. You decide; the driver implements. Serve the project owner, not the driver:\ncorrectness, then evolvable contracts, then simplicity. Disagree when the evidence says so.\nCite path:line, or the URL for a web result, for every claim. What the SESSION SUMMARY lists counts as read; mark\nanything else you haven\'t read UNVERIFIED. Contracts change additively; flag one-way doors; defer what the spec\ndoesn\'t require.\nExplore with your tools until you can cite evidence for a decision, then stop. Tool calls need no text, only a filled\njustification: for each call, what you expect to learn and how it bears on the current decision. When no read can\nsettle it, call need_info.\nCLARIFICATION. Before you decide you may call need_info. Each entry names one missing fact or intent and says why it\nmatters to the decision, in the form "<fact> -- matters because <reason>". The driver answers from verified sources\nonly and will not guess for you.\nALTERNATIVES. Before choosing, survey every plausible alternative, including ones the driver has not raised. For each,\nstate the strongest case for it and the strongest case against it. A decision without a surveyed set of alternatives\nis incomplete and the driver will challenge it.\nBIAS. Favour the simplest backwards-compatible option that maximizes flexibility for future changes. If you choose\nanything more complex or compatibility-breaking, say why it is needed; otherwise the driver will challenge it.\nPHASES. The SESSION SUMMARY names the scout phase. While agreeing, propose the question list by calling\nagree_questions. While settling, answer only the current question by calling settle, once.\nAnswer the one question asked only by calling the tool the driver names, once; put nothing in text. Carry forward up\nto 5 lines you must remember for later questions.'

# The two answer-tool entries of phase 5 section 4, in the Ollama tools-entry shape.
AGREE_QUESTIONS_TOOL = {'type': 'function', 'function': {'name': 'agree_questions', 'description': 'Propose the ordered list of questions this session will settle, one per turn.', 'parameters': {'type': 'object', 'properties': {'questions': {'type': 'array', 'minItems': 1, 'maxItems': 12, 'items': {'type': 'object', 'properties': {'id': {'type': 'string', 'description': 'Short unique id, e.g. q1.'}, 'question': {'type': 'string', 'description': 'The decision to be settled, as a question.'}, 'why_it_matters': {'type': 'string', 'description': 'What depends on the answer.'}}, 'required': ['id', 'question', 'why_it_matters']}}, 'carry_forward': {'type': 'array', 'maxItems': 5, 'items': {'type': 'string'}, 'description': 'Up to 5 lines to remember for later questions.'}}, 'required': ['questions', 'carry_forward']}}}

# Phase 5b section 4: one constant schema, identical on every ask. The per-ask label set is checked by the server (section 5).
SETTLE_TOOL = {"type": "function", "function": {"name": "settle",
  "description": "Settle the current question: the decision, the alternatives surveyed, the reasoning.",
  "parameters": {"type": "object", "properties": {
    "question_id": {"type": "string", "description": "Id of the current question."},
    "alternatives": {"type": "array", "minItems": 2,
      "description": "One entry for each option the question offers, named by its label, each with a case for and a case against. Also list any other plausible alternative you surveyed, named in words.",
      "items": {"type": "object", "properties": {
        "name": {"type": "string", "description": "An option's label (A, B, and so on), or a short name in words for another alternative you surveyed. A to H and Other are reserved for options."},
        "case_for": {"type": "string", "description": "Strongest case for, citing path:line or URL."},
        "case_against": {"type": "string", "description": "Strongest case against, citing path:line or URL. Non-empty."},
        "backwards_compatible": {"type": "boolean"}},
        "required": ["name", "case_for", "case_against", "backwards_compatible"]}},
    "decision": {"type": "string", "enum": ["A", "B", "C", "D", "E", "F", "G", "H", "Other"],
      "description": "The label of the chosen option (A, B, and so on), or Other when no option fits. Do not name an alternative in words here."},
    "other": {"type": "object", "description": "Required only when decision is Other: the best alternative you found yourself, which is not one of the options. Omit it, or send null, for any other decision.",
      "properties": {"summary": {"type": "string"}, "case_for": {"type": "string"}, "case_against": {"type": "string", "description": "Strongest case against, citing path:line or URL. Non-empty."}, "backwards_compatible": {"type": "boolean"}},
      "required": ["summary", "case_for", "case_against", "backwards_compatible"]},
    "reasoning": {"type": "string"},
    "complexity_justification": {"type": "string", "description": "Required when the chosen alternative is not backwards compatible."},
    "carry_forward": {"type": "array", "maxItems": 5, "items": {"type": "string"}, "description": "Up to 5 lines to remember for later questions."}},
  "required": ["question_id", "alternatives", "decision", "reasoning", "carry_forward"]}}}

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


# Phase 5b section 5: A to H and Other are reserved for options, checked ignoring case; a label followed by ':' is reserved too.
_RESERVED = ("A", "B", "C", "D", "E", "F", "G", "H", "Other")


def _reserved(name: str, labels: list[str]) -> bool:
    text = name.strip()
    if text in labels:  # an option's entry is named by its label, so the label itself is never reserved against that option
        return False
    low = text.lower()
    return any(low == r.lower() or low.startswith(r.lower() + ":") for r in _RESERVED)


def _valid_other(o) -> bool:
    return (
        isinstance(o, dict)
        and all(isinstance(o.get(k), str) and o[k].strip() != "" for k in ("summary", "case_for", "case_against"))
        and isinstance(o.get("backwards_compatible"), bool)
    )


def _alternatives_problem(alternatives: list[dict], labels: list[str]) -> str | None:
    """The first failing alternatives check, in the order of section 5. Only one message is sent for the field."""
    names = [a["name"].strip() for a in alternatives]
    for name in names:
        if _reserved(name, labels):
            return (
                f"alternatives (name '{name}' is reserved: name an option by its label alone, such as 'A', "
                "and name other alternatives without A to H or Other)"
            )
    for a in alternatives:
        if a["case_against"].strip() == "":
            return f"alternatives (case_against is empty for '{a['name'].strip()}')"
    missing = [label for label in labels if label not in names]
    if missing:
        return f"alternatives (missing options: {', '.join(missing)})"
    for label in labels:
        if names.count(label) > 1:
            return f"alternatives (duplicate entry for option: {label})"
    return None


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
    labels = list((state.ask_options or {}).keys()) if state else []
    decision = args.get("decision")
    chosen_label = decision.strip() if isinstance(decision, str) else None
    allowed = labels + ["Other"]
    if chosen_label not in allowed:
        got = f"'{decision}'" if isinstance(decision, str) else "no value"
        problems.append(f"decision (must be one of: {', '.join(allowed)}; got {got})")
    other = args.get("other")
    if chosen_label in labels and other is not None:  # an absent other and other: null both count as omitted
        problems.append("other (must be omitted unless decision is Other)")
    if chosen_label == "Other" and not _valid_other(other):
        problems.append(
            "other (required when decision is Other, with non-empty summary, case_for and case_against, "
            "and a boolean backwards_compatible)"
        )
    alternatives = args.get("alternatives")
    shape_ok = isinstance(alternatives, list) and all(_valid_alternative(a) for a in alternatives)
    if not shape_ok:
        problems.append("alternatives (each entry needs name, case_for, case_against and backwards_compatible)")
    else:
        problem = _alternatives_problem(alternatives, labels)
        if problem is not None:
            problems.append(problem)
    compatible = None
    if chosen_label == "Other" and _valid_other(other):
        compatible = other["backwards_compatible"]
    elif chosen_label in labels and shape_ok:
        entry = next((a for a in alternatives if a["name"].strip() == chosen_label), None)
        compatible = entry["backwards_compatible"] if entry is not None else None
    justification = args.get("complexity_justification")
    if compatible is False and not (isinstance(justification, str) and justification.strip()):
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
        # Built from the four fields only, so an extra key the model adds is dropped rather than bounced (section 5).
        # Names are stored stripped, as the checks compare them.
        alternatives = [
            S.Alternative(
                name=a["name"].strip(),
                case_for=a["case_for"],
                case_against=a["case_against"],
                backwards_compatible=a["backwards_compatible"],
            )
            for a in args["alternatives"]
        ]
        decision = args["decision"].strip()
        options = dict(state.ask_options or {})  # phase 5b section 6: the labels and text the navigator was shown
        other = None
        if decision == "Other":
            o = args["other"]
            other = S.OtherAnswer(
                summary=o["summary"],
                case_for=o["case_for"],
                case_against=o["case_against"],
                backwards_compatible=o["backwards_compatible"],
            )
        S.settle_question(
            state,
            S.SettledAnswer(
                question_id=qid,
                question=question,
                alternatives=alternatives,
                decision=decision,
                reasoning=args["reasoning"],
                complexity_justification=args.get("complexity_justification") or None,
                options=options,
                other=other,
            ),
        )
        return {**args, "next_question_id": S.current_question_id(state), "options": options}

    def carry_forward(self, args: dict) -> list[str]:
        return list(args["carry_forward"])


SCOUT = ScoutMode()
