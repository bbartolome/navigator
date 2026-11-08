Type: grilling
Status: resolved (agent-proposed in the Ralph loop on 2026-11-08; not yet confirmed with the user)
Blocked by:

# Stored and rendered form of a settled answer, and the legend text

## Question

What exactly does a settled answer look like on disk, in the SESSION SUMMARY and in the question the navigator
reads?

- The legend: exact wording and placement in the question text (`A: <text>` lines, a heading, where the
  `Answer by calling settle.` suffix goes), and how this changes the golden files `g3_scout.txt` and `g4_scout_two_settled.txt`.
- The `SETTLED ANSWERS` line: how a label decision and an `Other` decision print, and whether the driver's option
  text is shown or only the label (the summary must stay readable without the original question).
- The server's check messages, because they are what the navigator reads on a bounce: wording for an unknown label,
  a missing `other`, an `other` given with a label, and a missing `case_against` for an option.
- What `alternatives[].name` becomes (the label only, with the enum from decision 8), and how an option the
  navigator did not judge is handled.

## Answer

Proposed by the agent, building on decisions 1 to 12 of the charting round. These are recommendations, not settled
with the user; the user can redirect any of them. Revised once after an Opus read-only review (the changes are listed
at the end).

**Question assembly (`_with_suffix` order).** For `settle` asks only, `_ask` in `ollama.py` builds the question the
navigator reads in this order:

1. Remove a trailing `Answer by calling settle.` line from the driver's question, if the driver wrote one.
2. Append the legend.
3. Add the suffix as the last line with `_with_suffix`, unchanged.

**Legend.** The text the navigator reads after the question:

```
<question text from the ask>

Options:
A: <option text>
B: <option text>
Answer by calling settle.
```

- Heading `Options:`, then one line per option, `<label>: <text>`. Labels run from `A` up to the number of options.
  Each text is whitespace-collapsed (`" ".join(text.split())`). The collapsed text is what the legend shows and what is
  stored.
- The legend names no `Other`. The rule lives in the field descriptions (decision 11).
- Prompt cache: the legend adds bytes only after the summary and the excerpts, in the newest user message. The tools
  block and everything before the question are unchanged by it.

**Where the options live.** `AskArgs` gains `options: list[str] = []`. `_ask` sets a new `SessionState` field
`ask_options: dict[str, str] | None = None` (label to collapsed text; `None` for `agree_questions`), next to
`ask_parts`. It is set once per ask, so bounces and resends check against the same labels, and it is never rendered.
The empty-reply retry replays `ask_parts["question"]`, which already holds the legend, so the retry sees the same
labels.

**Refusals on `settle` asks.** Checked in `JobManager._enqueue_locked` (`jobs.py:162`) for `kind == "ask"`, by
`scout.check_options(answer_tool, options)`, which raises `ValueError(message)`. Both paths turn that into
`{"status": "error", "reason": ...}`:

- the queued fallback, through `enqueue_queued` (`jobs.py:188-189`);
- the Tasks path, where `TasksExtension.intercept_tool_call` calls `enqueue` directly and, on `ValueError`, falls through
  to the `navigator_ask` body (`jobs.py:528-529`), which reaches the same `enqueue_queued` path.

`navigator_ask` does not check on its own. `_ask` calls `check_options` as well, for direct `ask()` calls (as the tests
make). `check_options` raises `ValueError`, and `_ask` catches it and returns `_err(str(exc))`, an `Error` outcome. That
happens before any state change, next to the phase gates (`ollama.py:607-611`). `ask()` (`ollama.py:589-597`) catches only
`TransportFailure` and `ImageError`, so without this catch the error would raise rather than return an outcome. A
`settle` ask with no options never reaches the navigator. Not a pydantic validator on `AskArgs`: that would surface as a
raw tool error.

Import: `ollama.py` imports `check_options` inside `_ask`, not at module level. `modes/scout.py` and `modes/__init__.py`
both import `ollama`, so a top-level import would be circular. `jobs.py` already imports `modes`, so its call is fine.

The messages:

- `settle` with no `options`: `settle needs options: 2 to 8 plain strings, one per option`
- `settle` with 1 or more than 8 entries: `options: 2 to 8 entries; got 1`
- An entry that is empty after collapse: `options: entry 2 is empty`
- Two entries with the same collapsed text: `options: entries 1 and 3 have the same text`
- `options` with any tool other than `settle`: `options is only used with answer_tool settle`

**Answered result.** The `result` of an answered `settle` carries `options` (label to collapsed text) next to
`next_question_id`, so the driver has each label's text when the answer arrives. `apply_answer` returns
`{**args, "next_question_id": ..., "options": <ask_options>}`. The driver needs the text for the fit check in step 6 and
the summary in step 8, and the labels alone do not carry it.

**Stored and rendered answer.** `SettledAnswer` gains two optional fields, both defaulting so old state files load:

- `options: dict[str, str] = {}`: a copy of `ask_options` for the ask that settled the question.
- `other: OtherAnswer | None = None`, where `OtherAnswer` is `{summary, case_for, case_against, backwards_compatible}`.

Decision 10 named only `other`. `options` is a second optional field, added so the line can show option text without
the question.

**`SETTLED ANSWERS` line.** Shows the driver's option text, so the summary reads without the legend:

```
Q1: JSON or SQLite? | decision: A: JSON file | reasoning: simplest, additive later | alternatives: A: JSON file (for: no deps; against: no queries); B: SQLite database (for: queries; against: new dep)
Q2: Where is state stored? | decision: Other: a shared directory outside the repo | reasoning: matches the state.json layout | alternatives: A: one file (for: simple; against: no concurrency); B: per-session dir (for: isolation; against: more files)
```

- A label decision prints `<label>: <option text>`.
- `Other` prints `Other: <other.summary>`. The found alternative's case for and against stay in the stored `other`
  object, not in the line, as decision 10 says.
- An option entry prints `<label>: <option text> (for: ...; against: ...)`. An extra entry prints its name as it is.
- An answer with no `options` (an old answer) prints exactly as today: the decision string and the names as they are.

**Goldens.**

- `g3_scout.txt` does not change. It is the old-form check: a state with a pre-change answer (no `options`, no `other`)
  must render byte-identically, which proves old files still load and print. `g3_state()` in `test_render.py` stays.
- `g4_scout_two_settled.txt` is rewritten, because test 17 builds the answers in the new form. The fixture becomes:
  Q1 with `options` `{"A": "JSON file", "B": "SQLite database"}` and `decision="A"`; Q2 with
  `options` `{"A": "one file", "B": "per-session dir"}` and `decision="Other"`, with
  `other=OtherAnswer(summary="a shared directory outside the repo", case_for="shared", case_against="more files", backwards_compatible=True)`.
  The expected `SETTLED ANSWERS` block, exact, with the alternatives' `name` values `A` and `B` (the labels; the text
  comes from `options`):

  ```
  SETTLED ANSWERS:
  - Q1: JSON or SQLite? | decision: A: JSON file | reasoning: simplest, additive later | alternatives: A: JSON file (for: no deps; against: no queries); B: SQLite database (for: queries; against: new dep)
  - Q2: Where is state stored? | decision: Other: a shared directory outside the repo | reasoning: matches the state.json layout | alternatives: A: one file (for: simple; against: no concurrency); B: per-session dir (for: isolation; against: more files)
  ```

  The `SETTLED ANSWERS` line still comes before `OPEN QUESTIONS`.

**Alternatives: labels, extras, and `Other`.**

- `decision` carries the fixed enum `A` to `H` plus `Other` (decision 8). The server checks it against the current ask's
  labels, plus `Other`.
- `alternatives[].name` stays a plain string, not an enum. Reason: an entry for an alternative the driver did not offer
  must be possible (see below), and an enum would forbid it. The schema is still constant across asks.
- Each option the ask offers gets exactly one entry in `alternatives`, named by its label. Its `case_against` is
  non-empty. An option the navigator did not judge is a bounce, not a silent drop.
- The charter's ALTERNATIVES paragraph requires surveying plausible alternatives the driver did not raise. Those go in
  `alternatives` as extra entries, named in words. They cannot be chosen: `decision` is a label or `Other`. They print
  as they are, and they need a non-empty `case_against`.
- The found alternative of an `Other` decision is not in `alternatives`. It is in `other`.

**Name matching.** Compared after `strip()`, and case-sensitive for labels: `decision` and an option's `name` must equal
its label exactly.

- A name is **reserved** when, after `strip()` and ignoring case, it equals `A` to `H` or `Other`, or starts with one of
  them followed by `:`. `a`, `other` and `A: JSON file` are all reserved. A reserved name is never an extra entry.
- Reserved-name bounce: `alternatives (name '<name>' is reserved: name an option by its label alone, such as 'A', and
  name other alternatives without A to H or Other)`, with the offending name filled in.
- Precedence: for `alternatives`, only the first failing check is sent, in this order: entry shape, reserved name,
  empty `case_against`, missing option, duplicate option. Reason: a reserved name usually explains a missing option, so the
  navigator should not get both messages. `decision` and `other` have their own messages and can be sent with it.

**Field descriptions.** These are the wording the schema carries. They are constant across asks, so they do not touch
the prompt cache. They are the lever for decision 11, since the root cause in decision 1 was an undescribed `name`.

- `decision`: "The label of the chosen option (A, B, and so on), or Other when no option fits. Do not name an
  alternative in words here."
- `alternatives`: "One entry for each option the question offers, named by its label, each with a case for and a case
  against. Also list any other plausible alternative you surveyed, named in words."
- `alternatives[].name`: "An option's label (A, B, and so on), or a short name in words for another alternative you
  surveyed. A to H and Other are reserved for options."
- `case_against` (every entry, and `other`): "Strongest case against, citing path:line or URL. Non-empty."
- `other`: "Required only when decision is Other: the best alternative you found yourself, which is not one of the
  options. Omit it, or send null, for any other decision."

**Check messages.** A bounce reads `settle: missing or invalid fields: <list>. Call settle again with them.` (the
existing loop text). "Non-empty" means non-empty after `strip()`, as the complexity check already does. The entries are:

- Decision not among the ask's labels or `Other`: `decision (must be one of: A, B, Other; got 'E')`, listing the
  current ask's labels.
- `Other` without a valid `other` (not an object, or `summary`, `case_for` or `case_against` empty, or
  `backwards_compatible` not a boolean): `other (required when decision is Other, with non-empty summary, case_for and case_against, and a boolean backwards_compatible)`.
- `other` present and not null with a label decision: `other (must be omitted unless decision is Other)`. `other: null`
  and an absent `other` both count as omitted.
- An option with no entry: `alternatives (missing options: B)`, listing every missing label (`missing options: B, C`).
- An option with two entries: `alternatives (duplicate entry for option: A)`.
- A reserved name on an entry: the reserved-name message under "Name matching" above, the only message for that case.
- An empty `case_against` on an option or extra entry: `alternatives (case_against is empty for 'B')`, naming the entry.
  The `other` object is covered by its own message above, which lists `case_against`.
- An entry with a missing or wrongly typed field: `alternatives (each entry needs name, case_for, case_against and backwards_compatible)`.
- Complexity: the existing `complexity_justification (required when the chosen alternative is not backwards compatible)`,
  applied to the chosen option's entry, or to `other.backwards_compatible` when the decision is `Other`.

**Per-ask options and the rest.** The option set is checked only through `ask_options`, so there is no second copy to
drift. Ticket 03 uses the refusal messages above and does not restate them.

## Revision after the Opus review

Second round:

- Name matching is defined: exact after `strip()`, case-sensitive for labels. Reserved names (ignoring case, or a label
  followed by `:`) bounce with a message that shows the correct form. The reserved-name bounce suppresses the
  missing-option message.
- The `case_against` message is in one place, the `Other` message, so no two messages cover the same case.
- Superseded by the third round below: the refusal check does not live in `navigator_ask`.
- The Q2 golden line is written out in full.

Third round:

- The refusal check moved to `JobManager._enqueue_locked` (`jobs.py:162`), so the Tasks path is covered. The Tasks
  path calls `enqueue` and falls through to the tool body on `ValueError`, so both paths return the same error dict.
  `navigator_ask` does not check on its own.
- `_ask` catches the `ValueError` from `check_options` and returns `_err(str(exc))`, before any state change. This is
  specified, not left open.
- `ollama.py` imports `check_options` inside `_ask`, to avoid the circular import.
- The answered `result` carries `options`, so the driver reads each label's text (see "Answered result").

- Golden files: g3 is kept as the old-form check, and g4 is rewritten, with its exact lines.
- Extra non-option alternatives are allowed, named in words, and cannot be chosen. Reason: the charter asks for them, and
  the decision cannot name them.
- `name` stays a string, and its description is given.
- `_with_suffix` order is fixed: strip, then legend, then suffix.
- `other: null` counts as omitted. `other` must be non-empty to count as valid for `Other`.
- The option set lives in `ask_options`. The refusals and their messages are listed.
- The prompt-cache claim is narrowed to what the legend changes.
