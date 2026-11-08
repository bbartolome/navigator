"""SessionState -> SESSION SUMMARY text (phase 2 section 4).

Pure functions of the state: no clock, no randomness, no environment. This module imports nothing from ollama.py,
compress.py or modes/.
"""

from collections.abc import Callable
from typing import Literal

from .session import SessionState

FieldKind = Literal["rationale", "clarification", "challenge", "web_result"]
TextTransform = Callable[[str, FieldKind], str]


def _apply(transform: TextTransform | None, text: str, kind: FieldKind) -> str:
    if transform is None or text == "":
        return text
    return transform(text, kind)


def _value(text: str) -> str:
    """Normalise a value: CRLF to LF, trailing spaces stripped, continuation lines indented by two spaces."""
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    if not lines:
        return "(none)"
    head, *rest = lines
    return "\n".join([head] + [("  " + ln) if ln else "" for ln in rest])


def _block(header: str, items: list[str]) -> list[str]:
    if not items:
        return [f"{header}: (none)"]
    return [f"{header}:"] + [f"- {item}" for item in items]


def _stable_lines(s: SessionState, transform: TextTransform | None) -> list[str]:
    lines = [
        "SESSION SUMMARY",
        f"GOAL: {_value(s.goal)}",
        f"MODE: {s.mode}",
        f"CONTEXT: {_value(s.context)}",
    ]
    lines += _block(
        "DECIDED",
        [
            _value(d.text) + (f" | why: {_value(_apply(transform, d.rationale, 'rationale'))}" if d.rationale != "" else "")
            for d in s.decided
        ],
    )
    lines += _block("REJECTED CLAIMS", [f"{_value(r.claim)} | evidence: {_value(r.evidence)}" for r in s.rejected_claims])
    lines += _block(
        "CLARIFICATIONS GIVEN",
        [
            f"Q: {_value(c.question)} | A: {_value(_apply(transform, c.answer, 'clarification'))}"
            for c in s.clarifications
        ],
    )
    lines += _block("FILES READ", [f"{f.path}:{f.start}-{f.end} ({_value(f.what)})" for f in s.files_read])
    lines += _block(
        "WEB SEARCHES",
        [
            f"{_value(w.query)} -> {w.url} ({_value(_apply(transform, w.what, 'web_result'))})"
            for w in s.web_searches
        ],
    )
    lines += _block("SHOTS", [f"shot-{sh.k}: {_value(sh.target)} ({_value(sh.what)})" for sh in s.shots])
    lines += _block(
        "ACTION LOG",
        [
            f"{a.call_id} {a.name} {_value(a.args_summary)} -> {a.status}: {_value(a.result_summary)}"
            for a in s.action_log
        ],
    )
    if s.mode == "scout":
        lines += _block("SETTLED ANSWERS", [_settled_line(a) for a in s.settled_answers])
    return lines


def _decision_text(a) -> str:
    """Phase 5b section 7: a label prints with its option text, Other with its summary. An old answer prints as it is."""
    if a.decision == "Other" and a.other is not None:
        return f"Other: {_value(a.other.summary)}"
    if a.decision in a.options:
        return f"{a.decision}: {_value(a.options[a.decision])}"
    return _value(a.decision)


def _alternative_name(a, name: str) -> str:
    return f"{name}: {_value(a.options[name])}" if name in a.options else _value(name)


def _settled_line(a) -> str:
    alts = "; ".join(
        f"{_alternative_name(a, x.name)} (for: {_value(x.case_for)}; against: {_value(x.case_against)})"
        for x in a.alternatives
    ) or "(none)"
    return (
        f"{_value(a.question_id)}: {_value(a.question)} | decision: {_decision_text(a)}"
        f" | reasoning: {_value(a.reasoning)} | alternatives: {alts}"
    )


def _volatile_lines(s: SessionState, transform: TextTransform | None) -> list[str]:
    lines: list[str] = []
    if s.mode == "pair":
        done = set(s.done)
        lines += _block(
            "STEPS",
            [
                f"[{'x' if st.id in done else ' '}] {st.id} files: {', '.join(st.files)}"
                f" | change: {_value(st.change)} | verify: {_value(st.verification)}"
                for st in s.steps
            ],
        )
        lines.append(f"CURRENT STEP: {_value(s.current_step or '')}")
    if s.mode == "scout":
        settled = {a.question_id for a in s.settled_answers}
        lines += _block(
            "OPEN QUESTIONS",
            [f"{q.id}: {_value(q.question)}" for q in s.questions if q.id not in settled],
        )
    challenge = s.open_challenge or ""
    lines.append(f"OPEN CHALLENGE: {_value(_apply(transform, challenge, 'challenge'))}")
    lines += _block("CARRY FORWARD", [_value(line) for line in s.carry_forward])
    return lines


def render_stable(state: SessionState, transform: TextTransform | None = None) -> str:
    """The append-only sections (phase 2 section 4; main doc section 9a item 3)."""
    return "\n".join(_stable_lines(state, transform))


def render_volatile(state: SessionState, transform: TextTransform | None = None) -> str:
    """The per-question sections (main doc section 9a item 4). No header."""
    return "\n".join(_volatile_lines(state, transform))


def render_summary(state: SessionState, transform: TextTransform | None = None) -> str:
    return render_stable(state, transform) + "\n" + render_volatile(state, transform)
