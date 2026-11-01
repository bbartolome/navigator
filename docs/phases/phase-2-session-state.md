# Phase 2: Session state, rendering, persistence

Milestone 2 of [navigator-plugin-plan.md](../navigator-plugin-plan.md) §10. Depends on phase 1 (scaffold: `server/` uv
project, `navigator_start` and `navigator_status` stubs). Context, goals and responsibilities: main doc §1, §5.
Compaction field list: §6. Summary ordering and determinism: §9a. Out of scope: Ollama loop (phase 3), pair/scout
schemas (phases 4-5), compression internals (phase 6).

Files touched: `server/src/navigator_mcp/session.py`, `render.py`; `server/tests/test_session.py`, `test_render.py`,
`test_record.py`, `server/tests/golden/*.txt`. Phase 1's `navigator_start`/`navigator_status` are rewired to these
modules; no new tools.

## 1. Models (`session.py`)

Pydantic v2. All models: `model_config = ConfigDict(extra="forbid", validate_assignment=True)`.
`extra="forbid"` rejects unknown keys with `ValidationError`; `validate_assignment=True` validates attribute
assignment (default `False`). In-place `list.append` is not validated by it.
Source: <https://pydantic.dev/docs/validation/latest/api/pydantic/config/>

```python
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

Mode = Literal["pair", "scout"]
CallStatus = Literal["ran", "denied", "bounced"]

class Step(_M):                      # set_plan step (to-llm/SKILL.md)
    id: str
    files: list[str] = []
    change: str
    verification: str

class Decided(_M):
    text: str
    rationale: str = ""              # prose; compressible (phase 6 hook)

class RejectedClaim(_M):
    claim: str
    evidence: str

class Clarification(_M):
    question: str                    # a need_info question
    answer: str                      # driver's evidence-backed answer; compressible

class FileRead(_M):
    path: str
    start: int = Field(ge=1)
    end: int = Field(ge=1)
    what: str                        # "what it shows"

class WebSearch(_M):
    query: str
    url: str
    what: str                        # "what it showed"; compressible

class Shot(_M):
    k: int = Field(ge=1)
    target: str
    what: str

class ActionLogEntry(_M):
    call_id: str                     # "call-0001"
    name: str                        # navigator tool name
    args_summary: str                # single line, produced by phase 3
    status: CallStatus
    result_summary: str              # single line

class Alternative(_M):
    name: str
    case_for: str
    case_against: str
    backwards_compatible: bool = True

class ScoutQuestion(_M):          # fields match phase 5 `agree_questions`
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
    session_id: str                  # 32 lowercase hex chars, never rendered
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
    settled_answers: list[SettledAnswer] = []        # scout only
    # volatile
    steps: list[Step] = []                           # pair
    done: list[str] = []                             # step ids, completion order
    current_step: str | None = None                  # step id
    scout_phase: Literal["agreeing", "settling", "done"] | None = None  # scout only; "agreeing" at start
    questions: list[ScoutQuestion] = []              # scout only; frozen once scout_phase != "agreeing"
    open_challenge: str | None = None
    carry_forward: list[str] = Field(default=[], max_length=5)
    # counters (all start at 1)
    next_call_seq: int = 1
    next_shot_k: int = 1
    next_request_n: int = 1
    ended: bool = False
```

Pydantic copies mutable defaults per instance (open question 5 covers confirming this).

### Mutators (in-place, called by phases 3-5 and by `navigator_record`)

Every mutator validates its inputs before changing state; on error it raises `ValueError` and leaves state unchanged.

| Function | Effect |
| --- | --- |
| `alloc_call_id(s) -> str` | returns `f"call-{s.next_call_seq:04d}"`, increments counter |
| `alloc_request_n(s) -> int` | returns `next_request_n`, increments |
| `log_action(s, entry)` | append to `action_log` |
| `add_file_read(s, FileRead)` | append unless an equal record exists |
| `add_web_search(s, WebSearch)` | append unless an equal record exists |
| `add_shot(s, target, what) -> int` | append `Shot(k=next_shot_k, ...)`, increment, return `k` |
| `set_plan(s, steps, carry_forward)` | replace `steps`; keep `done` ids that still exist; `current_step` = first step not in `done`, else `None`; set carry_forward |
| `set_carry_forward(s, lines)` | replace; `len(lines) <= 5` |
| `set_questions(s, questions)` | scout, only while `scout_phase == "agreeing"`: replace `questions` |
| `freeze_questions(s)` | scout: requires non-empty `questions`; sets `scout_phase = "settling"` |
| `current_question_id(s) -> str \| None` | derived, not stored: first `questions[].id` with no entry in `settled_answers`; `None` unless `settling` |
| `settle_question(s, SettledAnswer)` | scout: requires `question_id == current_question_id(s)`, or an id already settled (re-settle after a challenge, per phase 5 §5). Appends, or replaces the entry with the same `question_id`. Sets `scout_phase = "done"` when no unsettled id remains |

## 2. `navigator_record` (`session.py`)

Payload validation uses a discriminated union.
Sources: <https://pydantic.dev/docs/validation/latest/concepts/unions/> (`Annotated[A | B, Field(discriminator=...)]`,
`TypeAdapter(...).validate_python`).

```python
from typing import Annotated
from pydantic import TypeAdapter

class DecidedRec(_M):       kind: Literal["decided"];        text: str; rationale: str = ""
class RejectedRec(_M):      kind: Literal["rejected_claim"]; claim: str; evidence: str
class ClarifRec(_M):        kind: Literal["clarification"];  question: str; answer: str
class StepDoneRec(_M):      kind: Literal["step_done"];      step_id: str
class ChallengeRec(_M):     kind: Literal["challenge"];      text: str | None   # None clears
class VerdictRec(_M):       kind: Literal["carry_forward_verdict"]
                            line: str; verdict: Literal["kept", "wrong"]; evidence: str = ""

Record = Annotated[DecidedRec | RejectedRec | ClarifRec | StepDoneRec | ChallengeRec | VerdictRec,
                   Field(discriminator="kind")]
_record = TypeAdapter(Record)

def apply_record(state: SessionState, kind: str, payload: dict) -> SessionState:
    rec = _record.validate_python({**payload, "kind": kind})   # raises ValidationError
    ...                                                         # mutate in place, return state
```

| kind | Mutation | Errors |
| --- | --- | --- |
| `decided` | append `Decided(text, rationale)` to `decided` | none |
| `rejected_claim` | append `RejectedClaim` | none |
| `clarification` | append `Clarification` | none |
| `step_done` | append `step_id` to `done` if absent; `current_step` = first step not in `done`, else `None` | `step_id` not in `steps` |
| `challenge` | `open_challenge = text` (`None` or `""` stores `None`) | none |
| `carry_forward_verdict` | `kept`: no change. `wrong`: remove first equal line from `carry_forward`, append `RejectedClaim(claim=line, evidence=evidence)` | `line` not in `carry_forward`; `wrong` with empty `evidence` |

Unknown `kind` or extra payload keys raise `ValidationError`; `server.py` returns it as the tool error. The ack
returned by `navigator_record` is `{"ok": true, "counts": {<list field>: len, ...}}`.

## 3. Persistence (`session.py`)

Layout:

```
$NAVIGATOR_STATE_DIR/<session_id>/state.json      # this phase
$NAVIGATOR_STATE_DIR/<session_id>/request-N.json   # phase 3 (names reserved)
$NAVIGATOR_STATE_DIR/<session_id>/reply-N.json     # phase 3
```

```python
def state_root() -> Path:                 # NAVIGATOR_STATE_DIR, else $CLAUDE_PLUGIN_DATA/sessions (main doc §8)
def new_session_id() -> str:              # uuid.uuid4().hex
def session_dir(session_id: str) -> Path: # raises ValueError unless re.fullmatch(r"[0-9a-f]{32}", id)
def create_session(mode: Mode, goal: str, context: str = "") -> SessionState:  # mkdir + save_state
def load_state(session_id: str) -> SessionState
def save_state(state: SessionState) -> None
def session_lock(session_id: str) -> threading.Lock   # one lock per id; callers hold it across load/mutate/save
```

- `load_state`: read `state.json`, `SessionState.model_validate_json(text)`. Raises `SessionNotFound` (missing
  file) or `SessionCorrupt` (JSON or validation error).
- `save_state`: `session_dir.mkdir(parents=True, exist_ok=True)`; write `model_dump_json(indent=2)` to
  `state.json.tmp` in the same directory; `f.flush()`; `os.fsync(f.fileno())`; `os.replace(tmp, state.json)`.
  `os.replace` is atomic on success and may fail across filesystems, hence same-directory temp. Sources:
  <https://docs.python.org/3/library/os.html> (`os.replace`, `os.fsync`);
  <https://pydantic.dev/docs/validation/latest/api/pydantic/base_model/> (`model_validate_json`, `model_dump_json(indent=...)`).
- `navigator_end` sets `ended = True` and saves; files stay on disk.

## 4. Rendering (`render.py`)

```python
FieldKind = Literal["rationale", "clarification", "challenge", "web_result"]
TextTransform = Callable[[str, FieldKind], str]   # compression plug-in point (phase 6); default None = identity

def render_summary(state: SessionState, transform: TextTransform | None = None) -> str
def render_stable(state: SessionState, transform: TextTransform | None = None) -> str    # append-only sections
def render_volatile(state: SessionState, transform: TextTransform | None = None) -> str  # per-question sections
```

`render_summary == render_stable + "\n" + render_volatile`. Pure functions of `state`: no clock, no randomness, no
environment, no reads of earlier request files. The caller (phase 3) calls `render_summary` once when a new question
starts and reuses the string for tool rounds (main doc §9a; see open question 1).

### Transform hook (interface only)

`transform(text, kind)` is applied only to: `Decided.rationale` (`"rationale"`), `Clarification.answer`
(`"clarification"`), `open_challenge` (`"challenge"`), `WebSearch.what` (`"web_result"`). It is never called for
paths, line ranges, IDs, URLs, queries, goals, contexts, steps, file `what`, or carry-forward lines. Empty strings are
not passed. Memoization and model choice belong to phase 6.

### Text format

Rules:
- Each section is `NAME:` followed by `- ` items, or `NAME: (none)` when empty.
- Scalar fields are `KEY: value`; empty value renders `(none)`.
- Normalise `\r\n` to `\n`; strip trailing spaces per line. Continuation lines of a multi-line value are indented
  by 2 spaces.
- Sections are separated by a single `\n`; no blank lines; no trailing newline.
- Scout-only sections (`SETTLED ANSWERS`, `OPEN QUESTIONS`) render only when `mode == "scout"`; `STEPS` and
  `CURRENT STEP` render only when `mode == "pair"` (open question 3).

Section order (stable block, then volatile block; main doc §9a items 3 and 4):

```
SESSION SUMMARY
GOAL: {goal}
MODE: {mode}
CONTEXT: {context}
DECIDED:
- {text}                                  # " | why: {rationale}" appended when rationale != ""
REJECTED CLAIMS:
- {claim} | evidence: {evidence}
CLARIFICATIONS GIVEN:
- Q: {question} | A: {answer}
FILES READ:
- {path}:{start}-{end} ({what})
WEB SEARCHES:
- {query} -> {url} ({what})
SHOTS:
- shot-{k}: {target} ({what})
ACTION LOG:
- {call_id} {name} {args_summary} -> {status}: {result_summary}
SETTLED ANSWERS:                          # scout only; end of stable block
- {question_id}: {question} | decision: {decision} | reasoning: {reasoning} | alternatives: {alts}
STEPS:                                    # pair only; volatile block starts here
- [{x| }] {id} files: {files joined ", "} | change: {change} | verify: {verification}
CURRENT STEP: {current_step}
OPEN QUESTIONS:                           # scout only
- {id}: {text}
OPEN CHALLENGE: {open_challenge}
CARRY FORWARD:
- {line}
```

`{alts}` = `{name} (for: {case_for}; against: {case_against})` joined by `; `, or `(none)`. `[x]` when the step id is
in `done`, `[ ]` otherwise. `render_stable` ends after the last stable section; `render_volatile` starts at the first
volatile section (no header).

## 5. Golden-summary tests (`server/tests/test_render.py`)

Goldens live in `server/tests/golden/<case>.txt`; tests compare with `==`.

**G1: empty pair state.** `SessionState(session_id="0"*32, mode="pair", goal="Add retry to fetch_user")`. Expected:

```
SESSION SUMMARY
GOAL: Add retry to fetch_user
MODE: pair
CONTEXT: (none)
DECIDED: (none)
REJECTED CLAIMS: (none)
CLARIFICATIONS GIVEN: (none)
FILES READ: (none)
WEB SEARCHES: (none)
SHOTS: (none)
ACTION LOG: (none)
STEPS: (none)
CURRENT STEP: (none)
OPEN CHALLENGE: (none)
CARRY FORWARD: (none)
```

**G2: populated pair state.** Input: G1 plus `context="Repo: api/. Python 3.12."`; `decided=[Decided(text="Use
exponential backoff", rationale="Matches api/http.py")]`; `rejected_claims=[RejectedClaim(claim="fetch_user is
async", evidence="api/users.py:12 def fetch_user")]`; `clarifications=[Clarification(question="Which HTTP client?",
answer="httpx, api/http.py:3")]`; `files_read=[FileRead(path="api/users.py", start=1, end=40, what="fetch_user
definition")]`; `web_searches=[WebSearch(query="httpx retry", url="https://example.com/r", what="retries option")]`;
`shots=[Shot(k=1, target="http://localhost:3000/users", what="user list renders")]`;
`action_log=[ActionLogEntry(call_id="call-0001", name="read_file", args_summary="api/users.py:1-40", status="ran",
result_summary="40 lines")]`; `steps=[Step(id="1", files=["api/users.py"], change="add retry loop",
verification="pytest api"), Step(id="2", files=[], change="add timeout", verification="pytest api")]`;
`done=["1"]`; `current_step="2"`; `open_challenge="Step 2 skips timeout"`; `carry_forward=["Backoff base is 0.5s"]`.
Expected snippets (full file is the golden):

```
CONTEXT: Repo: api/. Python 3.12.
DECIDED:
- Use exponential backoff | why: Matches api/http.py
REJECTED CLAIMS:
- fetch_user is async | evidence: api/users.py:12 def fetch_user
CLARIFICATIONS GIVEN:
- Q: Which HTTP client? | A: httpx, api/http.py:3
FILES READ:
- api/users.py:1-40 (fetch_user definition)
WEB SEARCHES:
- httpx retry -> https://example.com/r (retries option)
SHOTS:
- shot-1: http://localhost:3000/users (user list renders)
ACTION LOG:
- call-0001 read_file api/users.py:1-40 -> ran: 40 lines
STEPS:
- [x] 1 files: api/users.py | change: add retry loop | verify: pytest api
- [ ] 2 files:  | change: add timeout | verify: pytest api
CURRENT STEP: 2
OPEN CHALLENGE: Step 2 skips timeout
CARRY FORWARD:
- Backoff base is 0.5s
```

(An empty `files` list renders as an empty string after `files: `.)

**G3: scout state.** `mode="scout"`, `goal="Settle storage format"`,
`settled_answers=[SettledAnswer(question_id="Q1", question="JSON or SQLite?", alternatives=[Alternative(name="JSON",
case_for="no deps", case_against="no queries"), Alternative(name="SQLite", case_for="queries",
case_against="new dep")], decision="JSON", reasoning="simplest, additive later")]`,
`scout_phase="settling"`, `questions=[ScoutQuestion(id="Q1", question="JSON or SQLite?"), ScoutQuestion(id="Q2", question="Where is state stored?")]`. Expected: no `STEPS:`/`CURRENT STEP:` lines, and

```
SETTLED ANSWERS:
- Q1: JSON or SQLite? | decision: JSON | reasoning: simplest, additive later | alternatives: JSON (for: no deps; against: no queries); SQLite (for: queries; against: new dep)
OPEN QUESTIONS:
- Q2: Where is state stored?
OPEN CHALLENGE: (none)
CARRY FORWARD: (none)
```

**G4: multi-line values.** `goal="a\r\nb  "` renders `GOAL: a` / `  b`.

**Property tests:**
- Render twice gives identical strings; `load_state(save_state(s)) == s` and renders identically.
- Mutating only volatile fields (`steps`, `done`, `current_step`, `scout_phase`, `questions`, `open_challenge`, `carry_forward`)
  leaves `render_stable` unchanged; appending to an append-only list changes `render_stable` and leaves
  `render_volatile` unchanged.
- Rendered text matches no ISO-8601 timestamp regex and contains no `session_id`.
- Transform: with `lambda t, k: f"<{k}>"`, G2 renders `- Use exponential backoff | why: <rationale>`,
  `A: <clarification>`, `(<web_result>)`, `OPEN CHALLENGE: <challenge>`, while `api/users.py:1-40`, `call-0001`,
  `https://example.com/r` and `Backoff base is 0.5s` are unchanged. `transform=None` equals identity transform.

## 6. Other tests

- `test_record.py`: one case per `kind` per §2 table, including every listed error; unknown kind and extra payload
  key raise `ValidationError`; failed record leaves state equal to its pre-call copy (`model_copy(deep=True)`,
  <https://pydantic.dev/docs/validation/latest/api/pydantic/base_model/>).
- `test_session.py`:
  - round trip, missing session gives `SessionNotFound`, truncated JSON gives `SessionCorrupt`.
  - `session_dir("../x")` raises `ValueError`.
  - With `os.replace` monkeypatched to raise, the prior `state.json` is unchanged and parseable.
  - `alloc_call_id` yields `call-0001`, `call-0002`, ...; `add_shot` yields `k=1, 2`.
  - `set_plan` keeps surviving `done` ids; `carry_forward` with 6 lines raises.
  - `state_root()` honours `NAVIGATOR_STATE_DIR`, then `CLAUDE_PLUGIN_DATA`.

## 7. Done criteria

- `uv run pytest` passes in `server/`, including G1-G4, property, record and persistence tests.
- `navigator_start` creates `state.json`; `navigator_status` renders the summary from the loaded state;
  `navigator_record` and `navigator_end` implemented per §2-§3.
- `render.py` imports nothing from `ollama.py`, `compress.py` or `modes/`.
- No timestamps or random values appear in rendered text (property test).
- `claude plugin validate .` still passes.

## 8. Open questions

1. Main doc §11, append-only tool-round layout: `render_summary` is specified as called once per question. If the
   layout is rejected, does phase 3 call it on every send, and does anything here change?
2. Does `navigator_status` return `render_summary` output verbatim, or with a header for metrics (phase 3 supplies
   the metrics)?
3. Are `STEPS`/`CURRENT STEP` pair-only, and does scout ever use `set_plan`? Phase 5 decides; the gating is one
   condition in `render.py`.
4. Is `CONTEXT` (from `navigator_start`) part of the summary, or only of the first question? Main doc §6 does not list
   it.
5. Does the installed pydantic v2 copy mutable defaults (`[]`) per instance? The docs fetched here do not state it;
   if unconfirmed, switch to `Field(default_factory=list)`.
6. Is `path:{start}-{end}` the intended rendering of to-llm's `path:L-L`?
7. Should `Clarification.question` also pass through `transform`? Main doc §7 lists "clarifications" without
   saying which half.
8. How is a scout settled answer recorded: a `decided` record with a `question_id`, or server-side from the `decide`
   outcome? `navigator_record.kind` (main doc §4) has no scout kind.
9. Can the MCP SDK dispatch tool calls concurrently? `session_lock` assumes thread-based concurrency; unverified.
10. Is a directory `fsync` after `os.replace` needed? Not checked against primary docs.
11. Fallback for `state_root()` when neither `NAVIGATOR_STATE_DIR` nor `CLAUDE_PLUGIN_DATA` is set (for example
    running tests): raise, or use a temp dir?
