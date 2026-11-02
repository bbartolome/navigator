"""Session state: models, mutators, navigator_record payloads, and persistence (phase 2).

Sources: docs/phases/phase-2-session-state.md sections 1-3; compaction fields in docs/navigator-plugin-plan.md section 6.
"""

import os
import re
import threading
import uuid
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

Mode = Literal["pair", "scout"]
CallStatus = Literal["ran", "denied", "bounced"]

CARRY_FORWARD_MAX = 5


class SessionNotFound(Exception):
    """No state.json for the session id."""


class SessionCorrupt(Exception):
    """state.json exists but cannot be parsed or validated."""


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Step(_M):  # set_plan step (to-llm/SKILL.md)
    id: str
    files: list[str] = []
    change: str
    verification: str


class Decided(_M):
    text: str
    rationale: str = ""  # prose; compressible


class RejectedClaim(_M):
    claim: str
    evidence: str


class Clarification(_M):
    question: str  # a need_info question
    answer: str  # driver's evidence-backed answer; compressible


class FileRead(_M):
    path: str
    start: int = Field(ge=1)
    end: int = Field(ge=1)
    what: str  # "what it shows"


class WebSearch(_M):
    query: str
    url: str
    what: str  # "what it showed"; compressible


class Shot(_M):
    k: int = Field(ge=1)
    target: str
    what: str


class ActionLogEntry(_M):
    call_id: str  # "call-0001"
    name: str  # navigator tool name
    args_summary: str  # single line
    status: CallStatus
    result_summary: str  # single line
    # phase 3 bounce rule 2: canonical JSON of the args without justification, and the justification as given
    args_key: str = ""
    justification: str = ""


class PendingCall(_M):
    """One exploration call of the last reply. bounce is set when the server bounced it (phase 3 section 5)."""

    call_id: str
    name: str
    args: dict[str, Any]
    args_key: str
    justification: str
    bounce: str | None = None


class MetricsTotals(_M):
    """Sums of the per-reply Ollama metrics for one session (phase 3 section 3; None values are skipped)."""

    sends: int = 0
    prompt_eval_count: int = 0
    prompt_eval_cached_count: int = 0
    prompt_eval_duration: int = 0


class Alternative(_M):
    name: str
    case_for: str
    case_against: str
    backwards_compatible: bool = True


class ScoutQuestion(_M):
    id: str
    question: str
    why_it_matters: str = ""


class SettledAnswer(_M):
    question_id: str
    question: str
    alternatives: list[Alternative] = []
    decision: str
    reasoning: str
    complexity_justification: str | None = None


class SessionState(_M):
    schema_version: Literal[1] = 1
    session_id: str  # 32 lowercase hex chars, never rendered
    mode: Mode
    goal: str
    context: str = ""
    # append-only, insertion order
    decided: list[Decided] = []
    rejected_claims: list[RejectedClaim] = []
    clarifications: list[Clarification] = []
    files_read: list[FileRead] = []
    web_searches: list[WebSearch] = []
    shots: list[Shot] = []
    action_log: list[ActionLogEntry] = []
    settled_answers: list[SettledAnswer] = []  # scout only
    # volatile
    steps: list[Step] = []  # pair
    done: list[str] = []  # step ids, completion order
    current_step: str | None = None  # step id
    scout_phase: Literal["agreeing", "settling", "done"] | None = None  # scout only
    questions: list[ScoutQuestion] = []  # scout only; frozen once scout_phase != "agreeing"
    open_challenge: str | None = None
    carry_forward: list[str] = Field(default=[], max_length=CARRY_FORWARD_MAX)
    # counters (all start at 1)
    next_call_seq: int = 1
    next_shot_k: int = 1
    ended: bool = False
    # loop state (phase 3 section 4); the append-only summary above is not affected by these
    phase: Literal["IDLE", "THINKING", "AWAITING_SUBMIT"] = "IDLE"
    ask_n: int = 0  # request-N numbering; incremented per ask
    rounds: int = 0  # sends made in the current ask
    bounces: int = 0
    resent: bool = False
    retried: bool = False
    pending: list[PendingCall] | None = None
    messages: list[dict[str, Any]] = []  # the current ask's Ollama messages, append-only within the ask
    ask_parts: dict[str, Any] | None = None  # summary, excerpts, question and answer tool, for the empty-reply retry
    metrics: MetricsTotals = Field(default_factory=MetricsTotals)


# Mutators. Each validates before changing state and raises ValueError on error (phase 2 section 1).


def alloc_call_id(s: SessionState) -> str:
    call_id = f"call-{s.next_call_seq:04d}"
    s.next_call_seq += 1
    return call_id


def log_action(s: SessionState, entry: ActionLogEntry) -> None:
    s.action_log.append(ActionLogEntry.model_validate(entry))


def add_file_read(s: SessionState, rec: FileRead) -> None:
    r = FileRead.model_validate(rec)
    if r not in s.files_read:
        s.files_read.append(r)


def add_web_search(s: SessionState, rec: WebSearch) -> None:
    r = WebSearch.model_validate(rec)
    if r not in s.web_searches:
        s.web_searches.append(r)


def add_shot(s: SessionState, target: str, what: str) -> int:
    k = s.next_shot_k
    s.shots.append(Shot(k=k, target=target, what=what))
    s.next_shot_k = k + 1
    return k


def _check_carry_forward(lines: list[str]) -> None:
    if len(lines) > CARRY_FORWARD_MAX:
        raise ValueError(f"carry_forward allows at most {CARRY_FORWARD_MAX} lines, got {len(lines)}")


def set_plan(s: SessionState, steps: list[Step], carry_forward: list[str]) -> None:
    new_steps = [Step.model_validate(x) for x in steps]
    lines = list(carry_forward)
    _check_carry_forward(lines)
    ids = [st.id for st in new_steps]
    if len(set(ids)) != len(ids):
        raise ValueError("step ids must be unique")
    done = [d for d in s.done if d in ids]
    s.steps = new_steps
    s.done = done
    s.current_step = next((i for i in ids if i not in done), None)
    s.carry_forward = lines


def set_carry_forward(s: SessionState, lines: list[str]) -> None:
    lines = list(lines)
    _check_carry_forward(lines)
    s.carry_forward = lines


def _require_scout(s: SessionState) -> None:
    if s.mode != "scout":
        raise ValueError("not a scout session")


def set_questions(s: SessionState, questions: list[ScoutQuestion]) -> None:
    _require_scout(s)
    if s.scout_phase != "agreeing":
        raise ValueError("questions are frozen once settling has started")
    qs = [ScoutQuestion.model_validate(q) for q in questions]
    ids = [q.id for q in qs]
    if len(set(ids)) != len(ids):
        raise ValueError("question ids must be unique")
    s.questions = qs


def freeze_questions(s: SessionState) -> None:
    _require_scout(s)
    if s.scout_phase != "agreeing":
        raise ValueError("questions are already frozen")
    if not s.questions:
        raise ValueError("cannot freeze an empty question list")
    s.scout_phase = "settling"


def _settled_ids(s: SessionState) -> set[str]:
    return {a.question_id for a in s.settled_answers}


def current_question_id(s: SessionState) -> str | None:
    if s.scout_phase != "settling":
        return None
    settled = _settled_ids(s)
    return next((q.id for q in s.questions if q.id not in settled), None)


def settle_question(s: SessionState, answer: SettledAnswer) -> None:
    _require_scout(s)
    ans = SettledAnswer.model_validate(answer)
    if ans.question_id not in {q.id for q in s.questions}:
        raise ValueError(f"unknown question_id: {ans.question_id}")
    if ans.question_id not in _settled_ids(s) and ans.question_id != current_question_id(s):
        raise ValueError(f"question_id {ans.question_id} is not the current question")
    idx = next((i for i, a in enumerate(s.settled_answers) if a.question_id == ans.question_id), None)
    if idx is None:
        s.settled_answers.append(ans)
    else:
        s.settled_answers[idx] = ans
    if all(q.id in _settled_ids(s) for q in s.questions):
        s.scout_phase = "done"


# navigator_record payloads (section 2)


class DecidedRec(_M):
    kind: Literal["decided"]
    text: str
    rationale: str = ""


class RejectedRec(_M):
    kind: Literal["rejected_claim"]
    claim: str
    evidence: str


class ClarifRec(_M):
    kind: Literal["clarification"]
    question: str
    answer: str


class StepDoneRec(_M):
    kind: Literal["step_done"]
    step_id: str


class ChallengeRec(_M):
    kind: Literal["challenge"]
    text: str | None  # None clears


class VerdictRec(_M):
    kind: Literal["carry_forward_verdict"]
    line: str
    verdict: Literal["kept", "wrong"]
    evidence: str = ""


Record = Annotated[
    DecidedRec | RejectedRec | ClarifRec | StepDoneRec | ChallengeRec | VerdictRec,
    Field(discriminator="kind"),
]
_record = TypeAdapter(Record)


def apply_record(state: SessionState, kind: str, payload: dict[str, Any]) -> SessionState:
    """Validate and apply one navigator_record. Validation and checks run before any mutation."""
    rec = _record.validate_python({**payload, "kind": kind})  # raises ValidationError

    if isinstance(rec, DecidedRec):
        state.decided.append(Decided(text=rec.text, rationale=rec.rationale))
    elif isinstance(rec, RejectedRec):
        state.rejected_claims.append(RejectedClaim(claim=rec.claim, evidence=rec.evidence))
    elif isinstance(rec, ClarifRec):
        state.clarifications.append(Clarification(question=rec.question, answer=rec.answer))
    elif isinstance(rec, StepDoneRec):
        if rec.step_id not in [st.id for st in state.steps]:
            raise ValueError(f"unknown step_id: {rec.step_id}")
        if rec.step_id not in state.done:
            state.done.append(rec.step_id)
        state.current_step = next((st.id for st in state.steps if st.id not in state.done), None)
    elif isinstance(rec, ChallengeRec):
        state.open_challenge = rec.text or None
    elif isinstance(rec, VerdictRec):
        if rec.line not in state.carry_forward:
            raise ValueError(f"line is not in carry_forward: {rec.line}")
        if rec.verdict == "wrong":
            if not rec.evidence.strip():
                raise ValueError("a wrong verdict needs evidence")
            state.carry_forward.remove(rec.line)  # removes the first equal line
            state.rejected_claims.append(RejectedClaim(claim=rec.line, evidence=rec.evidence))
    return state


# Persistence (section 3)

_SESSION_ID = re.compile(r"[0-9a-f]{32}")
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def state_root() -> Path:
    """NAVIGATOR_STATE_DIR, else $CLAUDE_PLUGIN_DATA/sessions. Raises if neither is set (phase 2 open question 11)."""
    explicit = os.environ.get("NAVIGATOR_STATE_DIR")
    if explicit:
        return Path(explicit)
    plugin_data = os.environ.get("CLAUDE_PLUGIN_DATA")
    if plugin_data:
        return Path(plugin_data) / "sessions"
    raise RuntimeError("neither NAVIGATOR_STATE_DIR nor CLAUDE_PLUGIN_DATA is set")


def new_session_id() -> str:
    return uuid.uuid4().hex


def session_dir(session_id: str) -> Path:
    if not _SESSION_ID.fullmatch(session_id):
        raise ValueError(f"invalid session_id: {session_id!r}")
    return state_root() / session_id


def session_lock(session_id: str) -> threading.Lock:
    with _locks_guard:
        lock = _locks.get(session_id)
        if lock is None:
            lock = _locks[session_id] = threading.Lock()
        return lock


def save_state(state: SessionState) -> None:
    directory = session_dir(state.session_id)
    directory.mkdir(parents=True, exist_ok=True)
    final = directory / "state.json"
    tmp = directory / "state.json.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(state.model_dump_json(indent=2))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, final)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def load_state(session_id: str) -> SessionState:
    path = session_dir(session_id) / "state.json"
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise SessionNotFound(session_id) from None
    try:
        return SessionState.model_validate_json(text)
    except ValueError as exc:  # pydantic ValidationError is a ValueError
        raise SessionCorrupt(f"{session_id}: {exc}") from exc


def create_session(mode: Mode, goal: str, context: str = "") -> SessionState:
    state = SessionState(
        session_id=new_session_id(),
        mode=mode,
        goal=goal,
        context=context,
        scout_phase="agreeing" if mode == "scout" else None,
    )
    save_state(state)
    return state
