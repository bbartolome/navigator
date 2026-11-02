"""Pair mode (phase 4): the charter, the set_plan and decide answer tools, and their field checks."""

from .. import session as S
from ..schemas import DECIDE_TOOL, SET_PLAN_TOOL

# Byte-equal to the text between <charter> tags in the to-llm skill (tests/test_pair.py test 1).
CHARTER = "You are the NAVIGATOR. You decide; the driver implements. Serve the project owner, not the driver:\ncorrectness, then evolvable contracts, then simplicity. Disagree when the evidence says so.\nCite path:line, or the URL for a web result, for every claim. What the SESSION SUMMARY lists counts as read; mark anything\nelse you haven't read UNVERIFIED. Contracts change additively; flag one-way doors; defer what\nthe spec doesn't require.\nExplore with your tools until you can cite evidence for a decision, then stop. Tool calls need no\ntext, only a filled justification. When no read can settle it, call need_info.\nWhen a question turns on what is rendered, request a screenshot and cite it as shot-K.\nAnswer the one question asked only by calling the tool the driver names, once, with at most 5\nfindings; put nothing in text. Carry forward up to 5 lines you must remember for later questions.\n"

_CARRY_MAX = 5
_FINDINGS_MAX = 5
_SEVERITIES = {"S1", "S2", "S3"}


def _carry_problem(args: dict) -> list[str]:
    if "carry_forward" not in args:
        return ["carry_forward"]
    value = args["carry_forward"]
    if isinstance(value, list) and len(value) <= _CARRY_MAX and all(isinstance(x, str) for x in value):
        return []
    return ["carry_forward (a list of at most 5 strings)"]


def _check_set_plan(args: dict) -> list[str]:
    problems = _carry_problem(args)
    if "steps" not in args:
        problems.append("steps")
        return problems
    steps = args["steps"]
    if not isinstance(steps, list) or not steps:
        problems.append("steps (at least one step)")
        return problems
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            problems.append(f"steps[{i}]")
            continue
        for key in ("id", "change", "verification"):
            if not isinstance(step.get(key), str):
                problems.append(f"steps[{i}].{key}")
        files = step.get("files")
        if not (isinstance(files, list) and all(isinstance(x, str) for x in files)):
            problems.append(f"steps[{i}].files")
    return problems


def _check_decide(args: dict) -> list[str]:
    problems = []
    if not isinstance(args.get("decision"), str):
        problems.append("decision")
    problems += _carry_problem(args)
    if "findings" in args:
        findings = args["findings"]
        if not isinstance(findings, list) or len(findings) > _FINDINGS_MAX:
            problems.append("findings (at most 5)")
        else:
            for i, item in enumerate(findings):
                if not isinstance(item, dict):
                    problems.append(f"findings[{i}]")
                    continue
                if item.get("severity") not in _SEVERITIES:
                    problems.append(f"findings[{i}].severity")
                for key in ("cite", "issue", "fix"):
                    if not isinstance(item.get(key), str):
                        problems.append(f"findings[{i}].{key}")
    return problems


class PairMode:
    name = "pair"
    charter = CHARTER
    answer_tools = {"set_plan": SET_PLAN_TOOL, "decide": DECIDE_TOOL}

    def ask_error(self, state, tool: str) -> str | None:
        return None

    def missing_fields(self, tool: str, args: dict, state=None) -> list[str]:
        if tool == "set_plan":
            return _check_set_plan(args)
        if tool == "decide":
            return _check_decide(args)
        return [f"unknown answer tool {tool}"]

    def apply_answer(self, state, tool: str, args: dict) -> dict:
        if tool == "set_plan":
            S.set_plan(state, args["steps"], args["carry_forward"])  # the summary's STEPS come from here
        return dict(args)

    def carry_forward(self, args: dict) -> list[str]:
        return list(args["carry_forward"])


PAIR = PairMode()
