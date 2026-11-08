Type: grilling
Status: resolved (agent-proposed in the Ralph loop on 2026-11-08; not yet confirmed with the user)
Blocked by:

# Scout skill changes: how the driver supplies options

## Question

How does `skills/scout/SKILL.md` change so the driver passes `options` and checks an `Other` answer?

- Step 3 says "offer options without a preference" in prose. What does the driver put in `options`, how many, and
  how does it keep them neutral and free of labels.
- Step 6 lists reasons to challenge a `settle` result. What changes when the decision is `Other`: what the driver
  checks in the found alternative, and whether an `Other` always needs a challenge.
- Step 8's summary format (`Alternatives: ... | Decision: ...`): how option labels and `Other` appear to the user.
- The `navigator_ask` description and the error the driver sees for a `settle` ask without options.

## Answer

Proposed by the agent, building on ticket 01 (decisions 4, 5, 6, 7) and ticket 02 (the legend, the labels and the
`other` object). Recommendations, not settled with the user.

**Step 3: what goes in `options`.** Two to eight plain strings, one per option the driver wants judged.

- Each string is a complete choice that reads without the question, for example `JSON file per session`, not
  `the first one` or `option 2`.
- No labels (the server adds `A` to `H`), no `Other` (the server supplies it, decision 4), and no marking such as
  "recommended" or "default".
- Order them the way the driver would list them with no preference. The driver never puts the option it leans toward
  first.
- Include up to eight options. If the driver has more, it merges near-duplicates first, then keeps the eight most
  plausible. The navigator still surveys the rest as extra alternatives, named in words (charter ALTERNATIVES).
- A question with one real option still gets two: the driver adds the status quo or the simplest alternative as the
  second.

New step 3 text: "Settle one question per `navigator_ask`, `answer_tool="settle"`, in list order. Pass the question, the
excerpts it needs, and `options`: 2 to 8 plain strings, ordered with no preference and free of labels. Do not list the
options or any labels in the question text; the server appends them. Every `settle` ask for the same question, including
a re-ask after `need_info` or a challenge, passes the same `options` in the same order. To change an option, stop and
ask the user through AskUserQuestion."

**Step 6: checks on a `settle` result.** The checks for a label decision stay as they are. The answered result carries
`options` (label to text, ticket 02), so the fit check below compares each option's text, not its label. For `Other`:

- Check `other.summary`, `other.case_for` and `other.case_against` against the source, with the same citation rule as
  any alternative.
- Check each option's `case_against`. An uncited or wrong one is a challenge.
- The server does not judge fit, so the driver does. If an option plainly fits the question as the ticket states it,
  challenge with that evidence.
- If `other.backwards_compatible` is false, the server already requires `complexity_justification`; the driver checks
  that the justification holds.

An extra alternative (named in words, not an option) gets the same citation check as any alternative. A wrong citation
in one is a challenge.

**Steps 5 and 6: re-asks and challenges.** The skill re-asks in two places: step 5 (after `need_info`) and step 6 (after a
challenge). Both re-asks pass the same `options`, in the same order, as the first ask (step 3). A challenge names an
option by its text, not by its label alone, because the driver never writes a label (decision 7). For example, "the
option 'SQLite database' fits the question", not "B fits the question".

`Other` is not challenged automatically. It gets the same review as a label decision, and a challenge follows only when
one of the checks above fails. New bullet in step 6: "the decision is `Other` and an option fits the question, or a
citation in `other` or in an option's `case_against` is wrong (the server does not judge fit)."

**Step 8: summary.** Option labels and `Other` appear with their text, so the user never sees a bare label. The driver
keeps each question's `options` from its answered result (step 7 already reads it) and prints the text from there:

```
## <question id>: <question>
Alternatives: A: <text> (for: ...; against: ...) | B: <text> (for: ...; against: ...) | <extra name> (for: ...; against: ...) | Other: <summary> (for: ...; against: ...)
Decision: <label>: <option text>        (or: Decision: Other: <summary>)
Reasoning: <reasoning>
```

The `Other` entry in `Alternatives` appears only when the decision is `Other`, and its `for` and `against` come from the
`other` object. Extra entries (alternatives the navigator surveyed that the driver did not offer) appear by name, as
they do in the `SETTLED ANSWERS` line in ticket 02. The option entries print as `<label>: <text>`, which matches that
line.

**`navigator_ask`.** The `options` parameter gets this description: "Required when `answer_tool` is `settle`: 2 to 8
plain strings, one per option. The server labels them A, B, and so on, and the navigator must judge each one. Not used
with other tools."

- The refusals run in `JobManager._enqueue_locked` (`jobs.py:162`) through `scout.check_options`, for every `ask`, so the
  queued fallback and the Tasks path both refuse it (ticket 02). `_ask` calls the same function, so a direct `ask()` call
  is refused too. A `settle` ask without `options` returns `{"status": "error", "reason": "settle needs options: 2 to 8 plain strings, one per option"}` and no job is queued.
- An `options` value on another tool: `{"status": "error", "reason": "options is only used with answer_tool settle"}`.
- The other refusals (count outside 2 to 8, an empty entry after whitespace collapse, two entries with the same text,
  and `options` with another tool) use the literal messages in ticket 02. Newlines inside an option are collapsed to
  spaces, not refused.
- The phase gate in `ask_error` is unchanged.
