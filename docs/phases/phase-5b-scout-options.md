# Phase 5b: driver-supplied options for scout `settle`

Refines phase 5 (`phase-5-scout.md`): the `settle` schema (§4), the `settle` flow (§5, step 5), the driver skill (§6,
steps 3, 5, 6 and 8), and the scout tests (§8, tests 9 to 16 and 20). Where this doc and phase 5 disagree on `settle`,
this doc wins.

Source: the charting round and tickets 02 and 03 in `.scratch/settle-options/`. Tickets 02 and 03 were proposed by the
agent and reviewed by a read-only Opus subagent. They are not yet confirmed with the user; the decisions below carry
that caveat until they are.

Breaking change, scout `settle` only. Pair mode, `agree_questions`, and the scout charter are unchanged.

## 1. Scope

- `server/src/navigator_mcp/modes/scout.py`: `SETTLE_TOOL`, `check_options`, `option_texts`, `_check_settle`,
  `apply_answer`.
- `server/src/navigator_mcp/ollama.py`: `AskArgs.options: list[str] = []`. `_ask` calls `check_options` (imported inside
  `_ask`, to avoid a circular import), catches its `ValueError`, builds the legend, and sets `ask_options`.
- `server/src/navigator_mcp/jobs.py`: `JobManager._enqueue_locked` calls `check_options` for `kind == "ask"`.
- `server/src/navigator_mcp/session.py`: `SessionState.ask_options`, `SettledAnswer.options`, `SettledAnswer.other`,
  `OtherAnswer`.
- `server/src/navigator_mcp/render.py`: the `SETTLED ANSWERS` line.
- `server/src/navigator_mcp/server.py`: the `options` parameter of `navigator_ask`, its description, and
  `options or []` when it builds `AskArgs`. `navigator_ask` does not check options itself.
- `skills/scout/SKILL.md`: steps 3, 5, 6 and 8 (§8).
- `server/tests/fixtures/scout_tools.json`: its `settle` block is a copy of §4 and is updated with it.
- Out of scope: the charter, pair mode, `agree_questions`, archiving or migrating old sessions, `oneOf` for `decision`,
  `decide` with options (deferred until scout works).

Vocabulary is in `GLOSSARY.md`: an **option** is the driver's choice; an **alternative** is an entry in the navigator's
survey; **Other** is the decision when no option fits.

## 2. Driver interface: where options are checked

Two functions in `modes/scout.py`:

- `check_options(answer_tool: str, options: list[str] | None) -> None`. It raises `ValueError(message)` and returns
  nothing when the options are valid. `None` and `[]` both mean "no options given".
- `option_texts(options: list[str]) -> dict[str, str]`. It returns label to collapsed text (`" ".join(s.split())`),
  labels `A` upwards in the order given. Call it only after `check_options` has passed.

`check_options` rules:

| Case | Message |
| --- | --- |
| `answer_tool` is `settle`, and `options` is `None` or `[]` | `settle needs options: 2 to 8 plain strings, one per option` |
| `answer_tool` is `settle`, and there are 1 or more than 8 entries | `options: 2 to 8 entries; got N` |
| `answer_tool` is `settle`, and an entry is empty after collapse | `options: entry K is empty` (K is 1-based) |
| `answer_tool` is `settle`, and two entries have the same collapsed text | `options: entries I and J have the same text` |
| `answer_tool` is not `settle`, and `options` is non-empty | `options is only used with answer_tool settle` |

Newlines inside an entry are collapsed to spaces, not refused.

Where it runs:

- `JobManager._enqueue_locked` (`jobs.py:162`), for `kind == "ask"`, after the mode check and before the job is built.
  The check raises `ValueError`. The queued fallback, `enqueue_queued` (`jobs.py:188-189`), already returns a
  `ValueError` as `{"status": "error", "reason": ...}`, so the driver gets that shape and no job is queued.
- The Tasks path: `TasksExtension.intercept_tool_call` calls `enqueue`, which reaches `_enqueue_locked`. On `ValueError`
  it falls through to the tool body (`jobs.py:528-529`), which reaches `enqueue_queued` and returns the same error dict.
- `_ask` (`ollama.py`): the check runs again for direct `ask()` calls, such as the tests make. The `ValueError` is caught
  and returned as `_err(str(exc))`, before any state change, next to the phase gates (`ollama.py:607-611`).
  `ask()` (`ollama.py:589-597`) catches only `TransportFailure` and `ImageError`, so without this catch the error would
  raise rather than return an outcome.

`navigator_ask` (`server.py:116-125`) takes `options: list[str] | None = None`, passes `options or []` into `AskArgs`,
and does not check. Its description: "Required when `answer_tool` is `settle`: 2 to 8 plain strings, one per option.
The server labels them A, B, and so on, and the navigator must judge each one. Not used with other tools."

## 3. The question the navigator reads

For `settle` asks only, `_ask` builds the question in this order:

1. Remove a trailing `Answer by calling settle.` line from the driver's question, if the driver wrote one.
2. Append the legend.
3. Add the suffix as the last line with `_with_suffix`, unchanged.

Two options, `JSON file` and `SQLite database`:

```
<question text from the ask>

Options:
A: JSON file
B: SQLite database
Answer by calling settle.
```

Three options, `JSON file`, `SQLite database` and `one file per session`:

```
<question text from the ask>

Options:
A: JSON file
B: SQLite database
C: one file per session
Answer by calling settle.
```

The legend names no `Other`. The `Other` rule lives in the field descriptions (§4).

The legend is in the newest user message, after the summary and excerpts. The tools block and everything before the
question are unchanged by it.

## 4. The `settle` schema

One constant schema, identical on every ask. The per-ask label set is not in the schema. The server checks it (§5),
and the legend carries it.

```python
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
```

`name` is a string, not an enum, because extra alternatives (§5) must be possible. An enum would forbid them. The schema
is still constant across asks.

Ollama does not enforce `enum`, `required` or `minItems` on tool arguments, so the server checks all of them (§5).

## 5. Server checks on `settle`

`check_options` has already run, in `_enqueue_locked` and in `_ask` (§2), so the options are valid by the time an answer
arrives. The checks below run in `_check_settle` on each `settle` reply, and use `state.ask_options`. Each reply that
fails returns `settle: missing or invalid fields: <list>. Call settle again with them.` (the existing loop text), with
the entries shown literally below.

**Matching.** `decision` and an entry's `name` are compared after `strip()`, case-sensitively for labels. "Non-empty"
means non-empty after `strip()`.

**Reserved names.** A name is reserved when, after `strip()` and ignoring case, it equals `A` to `H` or `Other`, or
starts with one of them followed by `:`. `a`, `other` and `A: JSON file` are all reserved. A reserved name is never an
extra entry.

**Field checks.**

| Field | Failing case | Message |
| --- | --- | --- |
| `question_id` | unknown, or not the current question | unchanged from phase 5 |
| `decision` | not one of the ask's labels, or `Other` | `decision (must be one of: A, B, Other; got 'E')`, listing the ask's labels |
| `other` | `decision` is `Other`, and `other` is not an object, or `summary`, `case_for` or `case_against` is empty, or `backwards_compatible` is not a boolean | `other (required when decision is Other, with non-empty summary, case_for and case_against, and a boolean backwards_compatible)` |
| `other` | `decision` is a label, and `other` is present and not null | `other (must be omitted unless decision is Other)` |
| `alternatives` | shape: an entry is not an object, or lacks `name`, `case_for`, `case_against` or `backwards_compatible` | `alternatives (each entry needs name, case_for, case_against and backwards_compatible)` |
| `alternatives` | a reserved name | `alternatives (name '<name>' is reserved: name an option by its label alone, such as 'A', and name other alternatives without A to H or Other)` |
| `alternatives` | an empty `case_against` on an option or extra entry | `alternatives (case_against is empty for '<name>')` |
| `alternatives` | an option with no entry | `alternatives (missing options: B, C)`, listing every missing label |
| `alternatives` | an option with two entries | `alternatives (duplicate entry for option: A)` |
| `complexity_justification` | the chosen alternative (the option entry, or `other`) has `backwards_compatible` false, and no justification | `complexity_justification (required when the chosen alternative is not backwards compatible)` |

For `alternatives`, only the first failing check is sent, in this order: shape, reserved name, empty `case_against`,
missing option, duplicate option. A reserved name usually explains a missing option, so the navigator should not get
both. `decision`, `other` and `complexity_justification` have their own messages and can be sent with it.

The `case_against` of `other` is covered by the `other` message, not by the `alternatives` message.

**Null.** An absent `other` and `other: null` both count as omitted.

**Extra alternatives.** Entries whose name is not an option label are accepted when they are not reserved. They must
have a non-empty `case_against`. They cannot be chosen, because `decision` is a label or `Other`. The charter's
ALTERNATIVES paragraph requires surveying plausible alternatives the driver did not raise, and this is where they go.

**`Other`.** The found alternative is in `other`, not in `alternatives`. Every option still needs its entry, so the
navigator surveys all options and the found alternative. When `other.backwards_compatible` is false, the complexity
rule applies to `other` in the same way as to a chosen option.

## 6. State

- `AskArgs.options: list[str] = []`, set from `navigator_ask`'s `options or []`.
- `SessionState.ask_options: dict[str, str] | None = None`. `_ask` sets it to `option_texts(args.options)` for a `settle`
  ask, and to `None` otherwise. It is set once per ask, so bounces and resends check against the same labels. It is not
  rendered.
- `_ask` appends the legend to the question and stores the question in `ask_parts` (`ask_parts["question"]`). The
  empty-reply retry replays that stored question, so the retry sees the same labels.
- `SettledAnswer` gains two optional fields, both defaulting, so old state files load:
  - `options: dict[str, str] = {}`: a copy of `ask_options` for the ask that settled the question.
  - `other: OtherAnswer | None = None`.
- `OtherAnswer` is `{summary, case_for, case_against, backwards_compatible}`.
- `apply_answer` copies `ask_options` into `options`, stores `other` when the decision is `Other`, and returns
  `{**args, "next_question_id": ..., "options": ask_options}`. The answered `result` carries `options` (label to text),
  so the driver has each label's text when the answer arrives. The labels alone do not carry it.

## 7. Rendering: the `SETTLED ANSWERS` line

The line shows option text, so the summary reads without the legend. Example, with both answers in the new form:

```
- Q1: JSON or SQLite? | decision: A: JSON file | reasoning: simplest, additive later | alternatives: A: JSON file (for: no deps; against: no queries); B: SQLite database (for: queries; against: new dep)
- Q2: Where is state stored? | decision: Other: a shared directory outside the repo | reasoning: matches the state.json layout | alternatives: A: one file (for: simple; against: no concurrency); B: per-session dir (for: isolation; against: more files)
```

- A label decision prints `<label>: <option text>`.
- `Other` prints `Other: <other.summary>`. The found alternative's case for and against stay in the stored `other`, not in
  the line.
- An option entry prints `<label>: <option text> (for: ...; against: ...)`. An extra entry prints its name as it is.
- An answer with no `options` (an old answer) prints exactly as today: the decision string and the names as they are.

## 8. Driver skill: `skills/scout/SKILL.md`

Steps 1, 2, 4 and 7 are unchanged. Steps 3, 5, 6 and 8 change. The text below replaces each changed step in full. Step 3,
step 6 and step 8 are the ticket 03 text, verbatim where ticket 03 gives it.

**Step 3 (replace):**

```
3. Settle one question per `navigator_ask`, `answer_tool="settle"`, in list order. Pass the question, the excerpts it
   needs, and `options`: 2 to 8 plain strings, ordered with no preference and free of labels. Do not list the options or
   any labels in the question text; the server appends them. Every `settle` ask for the same question, including a
   re-ask after `need_info` or a challenge, passes the same `options` in the same order. To change an option, stop and
   ask the user through AskUserQuestion.
   - Each string is a complete choice that reads without the question, not "the first one" or "option 2".
   - No labels, no `Other`, and no "recommended" or "default" marking. Never put the option you lean toward first.
   - Include up to eight options. If you have more, merge near-duplicates first, then keep the eight most plausible.
     The navigator still surveys the rest as extra alternatives, named in words.
   - A question with one real option gets two: add the status quo or the simplest alternative as the second.
```

**Step 5 (replace the last sentence of the current step 5):**

```
5. On `status="need_info"`, answer each listed fact from verified sources only, never guess on the navigator's behalf,
   then ask the same question again with the same `options`, in the same order (step 3).
```

**Step 6 (replace):**

```
6. Check each `settle` result against the source before accepting it. Challenge, citing the evidence, if
   - alternatives were not surveyed or a plausible one is missing,
   - a case for or against is uncited or wrong,
   - the decision is not the simplest backwards-compatible option and no `complexity_justification` explains why,
   - the decision is `Other` and an option fits the question, or a citation in `other` or in an option's `case_against`
     is wrong (the server does not judge fit).
   The answered result carries `options` (label to text), so the fit check compares each option's text, not its label.
   `Other` is not challenged automatically: it gets the same review as a label decision. An extra alternative gets the
   same citation check as any alternative.
   Send a challenge in its own turn: record it with `navigator_record(kind="challenge")`, then ask again with `settle`,
   with the same `options` in the same order (step 3). A challenge names an option by its text, not by its label alone,
   because the driver never writes a label. For example: "the option 'SQLite database' fits the question". Check each
   carry-forward line against the source and record the verdict with `navigator_record(kind="carry_forward_verdict")`;
   record wrong claims as `rejected_claim` with evidence. If a challenge goes back and forth without the navigator
   moving, ask the user through AskUserQuestion.
```

**Step 8 (replace the template; the rest of step 8 is unchanged):**

````
8. When every question is settled, show the user a summary per question in this form. Option labels and `Other` appear
   with their text, so the user never sees a bare label. Take each question's option text from the `options` field of
   its answered result. Keep each answered result from step 7 until this step.

   ```
   ## <question id>: <question>
   Alternatives: A: <text> (for: ...; against: ...) | B: <text> (for: ...; against: ...) | <extra name> (for: ...; against: ...) | Other: <summary> (for: ...; against: ...)
   Decision: <label>: <option text>        (or: Decision: Other: <summary>)
   Reasoning: <reasoning>
   ```

   The `Other` entry in `Alternatives` appears only when the decision is `Other`, and its `for` and `against` come from
   the `other` object. Extra entries appear by name.
````

**Step 8, unchanged after the template:** "Then call `navigator_end` and ask through AskUserQuestion with three options:
Approve, Reject, Other (suggest an edit)", and the rest of the current step 8.

## 9. Tests

`server/tests/test_scout.py` keeps the phase 5 numbering. The helpers change first, and the tests that use them change
with them:

- `_ask(tool, question)` passes two option texts for a `settle` ask, so no `settle` test sends an ask without options.
- `_alts()` returns entries named `A` and `B`, each with a non-empty `case_against`.
- `_settle(qid, decision="A", ...)` defaults the decision to a label.

Changed tests:

| # | Change |
| --- | --- |
| 9 | a `settle` for a later question bounces naming `question_id`, as before, with options passed |
| 10 | one alternative bounces naming `alternatives` (now `missing options`) |
| 11 | a decision that is not a label bounces naming `decision`, with the labels in the message |
| 12 | an incompatible choice with no justification bounces naming `complexity_justification`; labels used |
| 13, 14, 15 | answers use labels and pass options |
| 16 | prefix test across a tool round in `settling`; the legend is in the newest user message |
| 17 | renders `g4_scout_two_settled.txt`, rewritten to the new form (§7) |
| 20 | an exploration call without justification is bounced; labels used |

Unchanged: tests 1 to 8, 18 and 19, and the `agree_questions` tests. `g3_scout.txt` does not change: it renders an
old-form answer, and it proves old state files still load and print.

New tests:

| Name | Expected |
| --- | --- |
| `settle` with no `options`, through `navigator_ask`'s job path (`_enqueue_locked`) and through `ask()` | refused with `settle needs options: ...`; nothing sent to the fake; no job queued |
| `settle` with no `options`, through the Tasks path (`intercept_tool_call`, as in `test_stdio_tasks.py`) | the error dict, no job queued |
| count 1, count 9 | refused with the count message |
| an entry that is empty after collapse; two equal entries | refused with the messages in §2 |
| `options` with `agree_questions`, or with `set_plan` | refused with `options is only used with answer_tool settle` |
| `agree_questions` and pair asks with `options` `[]` or `None` | not refused |
| the legend for 2 options and for 3 options | the exact blocks in §3 |
| a question already ending with the suffix | the suffix appears once, as the last line, after the legend |
| `decision` `E` with 2 options | bounce naming `decision`, listing `A, B, Other` |
| `Other` with no `other`, or with an empty `summary` | bounce naming `other` |
| `Other` with `other.backwards_compatible` false, no justification | bounce naming `complexity_justification` |
| `other` with a label decision; `other: null` with a label decision | the first bounces; the second is accepted |
| `[A, A]` for options A and B | bounce `missing options: B` (missing is checked before duplicate) |
| `[A]` only, options A and B | bounce `missing options: B` |
| an entry named `A: JSON file` | bounce with the reserved-name message; no missing-options message in the same reply |
| an entry named `a` | bounce with the reserved-name message |
| an extra entry named `SQLite` with a non-empty `case_against` | accepted; printed in the summary; not choosable |
| `Other` with a valid `other` | accepted; `summary` in the line; the found alternative stored in `other` |
| the `settle` tool block across two asks with different options | byte-identical |
| prefix stability across a tool round in settling, with the legend | the bytes before the question are identical |
| old-form state (`g3_state()`) | `g3_scout.txt` matches |

`tests/fixtures/scout_tools.json` gets its `settle` block updated to §4, because it is a copy of the block.

**Replay (manual, not a pytest).** Ticket 01's audit found 27 `settle` replies whose `decision` matched no alternative
name. Those asks carried no options, so the replay supplies them:

1. For each of the 27 questions, write its options by hand: 2 to 8 plain strings, each a complete choice, with any labels
   removed from the original text. Save them to `server/tests/fixtures/replay_options.json`, keyed by session and
   question id. The file is committed with the implementation.
2. Build a session for each question as a new `SessionState`, not by editing a copy of the original file:
   - a new `session_id`, and `mode="scout"` (`SessionState.mode` has no default);
   - `goal`, `context`, `questions`, `files_read` and `clarifications` copied from the original session, and
     `settled_answers` limited to the answers that come before this question;
   - `scout_phase="settling"`;
   - every other field takes its default. That gives `ended=False`, `action_log=[]`, `carry_forward=[]`, `phase="IDLE"`,
     `pending=None`, `messages=[]`, `bounces=0` and `ask_parts=None`.
   The current question is derived as the first unsettled question (`session.py:253-257`), so keeping only the earlier
   answers makes this question current. Save each copy under a scratch `NAVIGATOR_STATE_DIR` (`session.py:424-432`),
   never the plugin data directory, so the audit data is not overwritten. Each question runs in its own copy, so the
   questions do not contaminate each other.
3. Set the question text to the original request's question, with its option list removed (the legend will add the
   options back), and keep its excerpts. Then run `navigator_ask(answer_tool="settle", options=...)` against
   `qwen-agent:latest` with the saved options. The `context` and earlier answers in the copied state are what the
   navigator sees, so the numbers depend on them and are reported with the copy's source session id.
4. Record two numbers per question:
   - **First-try acceptance:** the first `settle` reply passes `_check_settle` with no bounce.
   - **Final acceptance:** the question ends as an accepted settled answer within the ask, after bounces.

The baseline in ticket 01 (48 of 101 `settle` attempts ended as accepted settled answers) counts attempts, not questions,
and includes bounces. It is not the same measure as either number above. For the 27 questions, the baseline is 0 of 27 by
construction, since they were the failing ones. The replay reports its two numbers against 0 of 27, and does not compare
them to 48 of 101.

## 10. Done criteria

- `uv run pytest` passes in `server/`, with the changed and new tests in §9.
- The `settle` tool block is byte-identical across asks (§9).
- `claude plugin validate .` passes with the changed `skills/scout/SKILL.md` (phase 5 test 18).
- The 27-question replay is run, and both numbers are recorded per §9. The pass threshold is open (§11).
- `/navigator:scout` still appears in a session started with `claude --plugin-dir .`.

## 11. Open questions

1. Tickets 02 and 03 are agent-proposed. Confirm them with the user, or change them, before this spec is final.
2. The pass threshold for the replay's first-try and final acceptance numbers.
3. If the replay shows many reserved-name bounces (`A: JSON file`), relax the reserved-name rule to a normalisation.
   Not decided here: the spec bounces, because a silent normalisation would hide the mistake from the navigator.
4. `decide` in pair mode with options: deferred until scout works (`map.md`).
5. The probe files `/tmp/enum-probe/` and `/tmp/oneof-probe/` may be gone. The decisions do not depend on them, but the
   evidence in ticket 01 does.
