Type: grilling
Status: resolved
Blocked by:

# Charting round: why `decision` fails today and the shape of the fix

## Question

Why do `settle` decisions fail the name check, and what is the smallest change that removes the cause?

## Answer

Settled with the user on 2026-11-08, with evidence from two probes.

**Cause.** `settle.alternatives[].name` has no description and `decision` is "the `name` of the chosen alternative".
In the audit of 34 session directories (`~/.claude/plugins/data/navigator-navigator/sessions`), 248 of 371 names
carried a label and text (`A: ...`), and 27 `settle` replies had a `decision` that matched no name, usually the bare
label. Only 48 of 101 `settle` attempts ended as accepted settled answers.

**Decisions.**

1. **Destination.** A phase spec in `docs/phases/`, no code.
2. **Scope.** Scout `settle` only. Pair `decide` and `agree_questions` unchanged.
3. **Vocabulary.** Option (driver's choice), Alternative (navigator's surveyed entry), Other (the decision when no
   option fits, carrying the best alternative found). Added to `GLOSSARY.md`.
4. **Other.** Allowed at any time. The server requires a non-empty `case_against` for every option. It does not judge
   fit or backwards compatibility. The existing `complexity_justification` rule still applies to the chosen alternative.
5. **Breaking change.** `options` is required on `navigator_ask` when `answer_tool` is `settle`; an ask without it is
   refused. No compatibility fallback.
6. **Where options attach.** At each `settle` ask, supplied by the driver, as 2 to 8 plain strings.
7. **Labels.** The server assigns `A` to `H` and appends a legend (`A: <text>`) to the question text. The driver
   never writes a label.
8. **Constant schema.** The `settle` schema has a fixed enum `A`..`H` plus `Other`, identical on every ask. Reason:
   Ollama renders the tools block before the messages, so a per-ask schema change invalidated the whole prompt
   cache in a probe (31 of 3,754 tokens reused, against 3,749 on an identical repeat). The server enforces the
   per-ask set of labels, since Ollama did not enforce the schema (a `oneOf` probe returned `Z` in 4 of 4 pressure runs).
9. **Shape.** Flat: `decision` is the enum, `other` is an object (`summary`, `case_for`, `case_against`,
   `backwards_compatible`) required exactly when `decision` is `Other`. `oneOf` rejected: in the probe it produced
   0 of 8 valid replies when the object branch was needed, against 8 of 8 valid with the flat shape (one run made no call).
10. **Stored answer.** `SettledAnswer` keeps `decision` as a string (label or `Other`) and gains an optional `other`
    object, so old state files load. The `SETTLED ANSWERS` summary line prints the label, and for `Other` its summary.
11. **Rule location.** The options and Other rule go in the `decision` and `other` field descriptions. The charter and
    `tests/test_scout.py` test 4 stay as they are.
12. **Old sessions.** No archive or migration step.

Probe caveats: 4 samples per cell, one model, temperature 0.7. The direction is clear, the rates are not measured.
