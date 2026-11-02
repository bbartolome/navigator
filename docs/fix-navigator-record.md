# Fix plan: navigator_record failures

Status: implemented in plugin 0.1.1 (sections 2-6). Section 8 checked over stdio against the installed copy. Scope: `server/src/navigator_mcp/{server,session,jobs}.py`, `skills/pair/SKILL.md`,
`skills/scout/SKILL.md`, and tests.

## 1. Problem and evidence

Two real driver sessions used the installed plugin (navigator@navigator):

- quizzes, pair mode, navigator session `1c9d9486…`. Transcript:
  `<transcript>`.
- logos, scout mode, navigator session `d67d60fd…`. Transcript:
  `<transcript>`.

Six of the seven `navigator_record` calls failed. The driver afterwards wrote: "The navigator_record calls failed
several times, so the navigator's record of step outcomes is incomplete. The commits are the reliable record."

| Kind | Payload sent | Expected | Server-side error (hidden from the client) |
|---|---|---|---|
| decided | `{plan, deferred, note}` | `{text, rationale?}` | `extra_forbidden` on plan, deferred, note |
| step_done | `{id:"E2", commits, tests, note}` | `{step_id:"E2"}` | `step_id` Field required; `extra_forbidden` on the rest |
| challenge | `{question_id, challenge}` | `{text}` | `text` Field required; `extra_forbidden` |
| challenge | `{question_id, text}` | `{text}` | `extra_forbidden` on question_id |
| challenge | `{scope, claim, evidence}` | `{text}` | `text` Field required; `extra_forbidden` |
| carry_forward_verdict | `{question_id, verdict:"accepted", note}` | `{line, verdict:"kept"\|"wrong", evidence}` | `line` required; verdict literal_error; `extra_forbidden` |

The one success was `decided {"scope":"question_list"}`, the only payload any skill spells out.

### Root causes

1. **The contract is invisible.**
   - tools/list advertises `kind` as a free string and `payload` as `{"type":"object","additionalProperties":true}`.
   - The description is "Record a verdict, decision, clarification, step or challenge on the session."
   - `skills/pair/SKILL.md` names the kinds but no keys. The key table exists only in
     `docs/phases/phase-2-session-state.md` §2, which the driver never sees.
   - The Rec models in `session.py` (`DecidedRec` … `VerdictRec`) use `extra="forbid"`.
2. **The SDK hides the error text.**
   - In mcp 2.3.0, `mcp/server/mcpserver/tools/base.py`, the final `except Exception` raises
     `UnexpectedToolError(f"Error executing tool {name}")` and keeps the cause on the server.
   - Only `ToolError`, `ResourceError` and `MCPError` pass their text through.
   - `navigator_record`, `navigator_end` and `_load` raise `ValueError` or pydantic `ValidationError`, so the
     client sees only "Error executing tool navigator_record".
   - This also hides "job <id> active", so the skills' "wait on job active" rule can never fire.
   - The tests assert `isError` but never the text, so they did not catch this.

### Ruled out

- **Active job.** Each record fell in a gap between jobs, according to the `job-*.json` `finished_at` and
  `created_at` times.
- **Stale install.** The installed `site-packages/navigator_mcp` is identical to `server/src`.
- **Missing steps.** `set_plan` stored the steps, and the driver used the correct id `E2`.

## 2. Fix 1: readable errors

- **server.py: a `_tool_error(exc) -> ToolError` helper** (`from mcp.server.mcpserver.exceptions import ToolError`).
  - Wrap the bodies of `navigator_record`, `navigator_end` and `navigator_status`, which also covers `_load`.
  - Convert `ValueError`, `pydantic.ValidationError`, `session.SessionNotFound` and `session.SessionCorrupt` with
    `raise _tool_error(exc) from exc`.
- **Format `ValidationError` per error.** Use `exc.errors()`:
  - `extra_forbidden` → "unknown key 'id'".
  - `missing` → "missing 'step_id'".
  - `literal_error` → "verdict must be one of: kept, wrong".
  - `union_tag_invalid` → "unknown kind 'x'; kinds: decided, rejected_claim, …".
  - Append "Allowed keys for <kind>: …", taken from that Rec model's `model_fields`, minus `kind`.
  - Example: `step_done: unknown key 'id'; missing 'step_id'. Allowed keys: step_id`.
- **session.py `apply_record`: add context to the existing ValueErrors.**
  - unknown step_id: "valid step ids: E2, E8, …", from `state.steps`. If there are no steps: "no plan yet (no
    set_plan answer)".
  - A line not in carry_forward: "current carry_forward lines:" followed by the numbered current lines.
  - Keep the existing scope and freeze messages, which are already specific.
- **jobs.py `check_no_active_job`.** Message: "job <id> is still running for this session; wait for its result,
  then record". `server.py` re-raises it as a `ToolError`.

## 3. Fix 2: the contract in the schema

- `navigator_record(session_id: str, kind: Literal["decided","rejected_claim","clarification","step_done","challenge","carry_forward_verdict"], payload: dict[str, Any])`.
  pydantic emits this as an enum in the inputSchema.
- Build the tool description from the Rec models, one line per kind with its exact keys:
  - `decided {text, rationale?} | {scope:"question_list"}`
  - `rejected_claim {claim, evidence}`
  - `clarification {question, answer}`
  - `step_done {step_id}`
  - `challenge {text|null}`
  - `carry_forward_verdict {line, verdict: kept|wrong, evidence (required for wrong)}`
- Keep `extra="forbid"` and add no aliases: the schema stays strict, and the errors are now readable.

## 4. Fix 3: payloads in the skills

- `skills/pair/SKILL.md`: replace the `answered` bullet's kind list with the per-kind key table above, plus these
  rules:
  - After each commit, record `step_done` with the `set_plan` step id.
  - Verdicts quote a line from the latest outcome's `carry_forward` exactly, one call per line, before the next
    `navigator_ask`.
  - On a record error, read the message, which names the fix, and retry once.
- `skills/scout/SKILL.md`: add the same table near steps 2 and 6. A challenge payload is `{text}` only; the
  question is the current one.

## 5. Fix 4: smaller items

- `navigator_record` on an ended session: reject with "session ended".
- Move `check_no_active_job` inside `session_lock`, closing the race between the check and the load/save.
- `navigator_status`: add `JOB: <id> <state> <stage>` when the session has a queued or running job (plan §4).

## 6. Tests (write first, see them fail)

- **`server/tests/test_smoke.py` (stdio).** Assert the result text, not just `isError`, for each of:
  - an unknown key (`step_done {"id":"1"}`) → contains "unknown key 'id'" and "step_id".
  - an unknown kind → lists the kinds.
  - an unknown step id → lists the valid ids.
  - a line not in carry_forward → lists the current lines.
  - an unknown session → "unknown session_id".
- **tools/list.** The `navigator_record` `kind` schema has the six-value enum, and the description names every
  kind's keys.
- **`server/tests/test_record.py`.** Replay the six real payloads from §1. Each error names its offending key and
  the allowed keys.
- **`server/tests/test_jobs.py`.** A record during a running job raises a ToolError whose text contains "still
  running".
- **`server/tests/test_pair.py` / `test_scout.py`.** Each kind's keys appear in the SKILL.md text.

## 7. Out of scope / open

- Stable ids for carry_forward lines, or verdicts by index.
- Auto-recording `decide` answers into `decided`.
- A default `NAVIGATOR_JOB_TIMEOUT` in `mcp/navigator.json`. Jobs ran up to 7 minutes, and a hung Ollama would
  block records until `navigator_cancel`.

## 8. Verification

- `uv run pytest` in `server/` passes, including the new text assertions.
- Restart the installed plugin. In a scratch session, a bad `step_done` returns the readable error, and the
  corrected payload succeeds.
