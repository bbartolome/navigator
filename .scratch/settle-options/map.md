Label: wayfinder:map

# Driver-supplied options for scout settle

## Destination

An implementation-ready phase spec in `docs/phases/` for driver-supplied options on scout `settle`: the new
`options` argument of `navigator_ask`, the constant `settle` schema, the server checks, the stored and rendered
`Other` answer, and the test plan. Planning only: the spec, not the code.

## Notes

- Domain: the navigator MCP server (`server/src/navigator_mcp`: `modes/scout.py`, `ollama.py`, `render.py`,
  `session.py`) and its phase docs. Read `docs/phases/phase-5-scout.md` and `docs/navigator-plugin-plan.md` §9a (prompt
  caching) before deciding anything that touches the schema or the summary. `GLOSSARY.md` holds option, alternative
  and Other.
- Skills: `grilling` and `domain-modeling` for grilling tickets; `research` for research tickets.
- Planning only: tickets produce decisions, not code. When the frontier is empty, write the phase doc.
- Evidence behind the design (audit of 34 real sessions, probes against `qwen-agent:latest`) is in the charting
  round ticket. Probe files: `/tmp/enum-probe/`, `/tmp/oneof-probe/` (may be gone).

## Decisions so far

- [Charting round: why `decision` fails today and the shape of the fix](issues/01-charting-round.md): the driver
  supplies options, the server labels them A to H, `decision` is a flat enum plus Other with the found alternative
  inline; breaking change, scout and `settle` only; rule lives in field descriptions, charter untouched.
- [Stored and rendered form of a settled answer, and the legend text](issues/02-stored-and-rendered-other-answer.md)
  (agent-proposed, not yet confirmed with the user): the legend under an `Options:` heading before the suffix; the
  `SETTLED ANSWERS` line shows option text; `SettledAnswer` gains optional `options` and `other`; check messages
  listed; `alternatives[].name` is the label for options and words for extras.

## Not yet specified

- Test plan and done criteria: shaped by tickets 02 and 03 (both agent-proposed, not yet confirmed with the user). Should include a replay of the 27 audited failing
  `settle` requests and a check that the `settle` schema bytes are identical across asks.
- A forced-violation test of whether Ollama enforces enums on tool arguments (current evidence: it does not enforce
  `oneOf`; the flat enum held in small runs). Only needed if the server check is ever relaxed.
- `decide` in pair mode with options: deferred until scout works.

## Out of scope

- Changing the scout charter or its pinned test ([charting round](issues/01-charting-round.md)): the rule lives in
  the schema's field descriptions.
- Archiving or migrating old sessions: `SettledAnswer` gains two optional fields (`options`, `other`), both defaulting, so old files still load.
- `oneOf` for `decision`: the probe showed the model does not use the object branch (8 of 8 invalid) and ignores the
  schema when none fits; the flat shape replaces it.
- Proposing options inside `agree_questions`: options come from the driver at each `settle` ask.
