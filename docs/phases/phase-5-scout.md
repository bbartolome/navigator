# Phase 5: Scout mode and `skills/scout`

Milestone 5 of `../navigator-plugin-plan.md` (the main doc). Depends on phases 1 to 3. Reuses the navigator tool
schemas (`read_file`, `list_dir`, `grep`, `screenshot`, `web_search`, `need_info`) from phase 4 by reference; they are
not redefined here. Loop mechanics (ask/submit, bounce, resend/retry) are phase 3. `SessionState` is phase 2.

Replaces: `scout-ahead/SKILL.md` (path in main doc §1).

## 1. Scope

- `server/src/navigator_mcp/modes/scout.py`: scout charter, the two answer-tool schemas, answer validation.
- `skills/scout/SKILL.md`: the driver instructions (full text in §6).
- Server changes outside `modes/scout.py`: only the hooks in §4 and §5, in `session.py` and `server.py`.
- Out of scope: pair mode (phase 4), compression (phase 6).

## 2. Scout state (fields defined in phase 2 §1)

This phase uses the phase 2 scout fields and mutators:
- `scout_phase`
- `questions` (`ScoutQuestion{id, question, why_it_matters}`)
- `settled_answers` (`SettledAnswer`, including `complexity_justification`; `Alternative` includes `backwards_compatible`)
- the derived `current_question_id(s)`
- `set_questions`, `freeze_questions`, `settle_question`

Rendering order follows phase 2 §4.

## 3. Scout charter (`modes/scout.py`, constant `SCOUT_CHARTER`)

Derived from the `to-llm` charter (which phase 4 copies verbatim for pair) plus the scout-ahead instructions.
Sent verbatim as the system prompt (main doc §2, §9a).

```
You are the NAVIGATOR. You decide; the driver implements. Serve the project owner, not the driver:
correctness, then evolvable contracts, then simplicity. Disagree when the evidence says so.
Cite path:line, or the URL for a web result, for every claim. What the SESSION SUMMARY lists counts as read; mark
anything else you haven't read UNVERIFIED. Contracts change additively; flag one-way doors; defer what the spec
doesn't require.
Explore with your tools until you can cite evidence for a decision, then stop. Tool calls need no text, only a filled
justification: for each call, what you expect to learn and how it bears on the current decision. When no read can
settle it, call need_info.
CLARIFICATION. Before you decide you may call need_info. Each entry names one missing fact or intent and says why it
matters to the decision, in the form "<fact> -- matters because <reason>". The driver answers from verified sources
only and will not guess for you.
ALTERNATIVES. Before choosing, survey every plausible alternative, including ones the driver has not raised. For each,
state the strongest case for it and the strongest case against it. A decision without a surveyed set of alternatives
is incomplete and the driver will challenge it.
BIAS. Favour the simplest backwards-compatible option that maximizes flexibility for future changes. If you choose
anything more complex or compatibility-breaking, say why it is needed; otherwise the driver will challenge it.
PHASES. The SESSION SUMMARY names the scout phase. While agreeing, propose the question list by calling
agree_questions. While settling, answer only the current question by calling settle, once.
Answer the one question asked only by calling the tool the driver names, once; put nothing in text. Carry forward up
to 5 lines you must remember for later questions.
```

Differences from the pair charter: the `CLARIFICATION`, `ALTERNATIVES`, `BIAS` and `PHASES` paragraphs, and no
"at most 5 findings" (the scout answer tools have no `findings`).

## 4. Answer-tool schemas (`modes/scout.py`)

Navigator tool schemas: phase 4. Both tools below use the Ollama `tools` entry format
(`{"type":"function","function":{name,description,parameters}}`, `parameters` a JSON Schema object) and are returned as
`message.tool_calls[].function.{name,arguments}` with `arguments` a JSON object. Source:
https://github.com/ollama/ollama/blob/main/docs/api.md

`navigator_ask.answer_tool` (main doc §4) accepts `"agree_questions"` and `"settle"` when the session mode is
`"scout"`, and `"set_plan"` and `"decide"` when it is `"pair"`; any other combination returns
`Outcome{status:"error", reason}` without calling Ollama.

### `agree_questions`

```json
{
  "type": "function",
  "function": {
    "name": "agree_questions",
    "description": "Propose the ordered list of questions this session will settle, one per turn.",
    "parameters": {
      "type": "object",
      "properties": {
        "questions": {
          "type": "array", "minItems": 1, "maxItems": 12,
          "items": {
            "type": "object",
            "properties": {
              "id": {"type": "string", "description": "Short unique id, e.g. q1."},
              "question": {"type": "string", "description": "The decision to be settled, as a question."},
              "why_it_matters": {"type": "string", "description": "What depends on the answer."}
            },
            "required": ["id", "question", "why_it_matters"]
          }
        },
        "carry_forward": {
          "type": "array", "maxItems": 5, "items": {"type": "string"},
          "description": "Up to 5 lines to remember for later questions."
        }
      },
      "required": ["questions", "carry_forward"]
    }
  }
}
```

### `settle`

```json
{
  "type": "function",
  "function": {
    "name": "settle",
    "description": "Settle the current question: alternatives surveyed, the decision, the reasoning.",
    "parameters": {
      "type": "object",
      "properties": {
        "question_id": {"type": "string", "description": "Id of the current question."},
        "alternatives": {
          "type": "array", "minItems": 2,
          "items": {
            "type": "object",
            "properties": {
              "name": {"type": "string"},
              "case_for": {"type": "string", "description": "Strongest case for, citing path:line or URL."},
              "case_against": {"type": "string", "description": "Strongest case against, citing path:line or URL."},
              "backwards_compatible": {"type": "boolean"}
            },
            "required": ["name", "case_for", "case_against", "backwards_compatible"]
          }
        },
        "decision": {"type": "string", "description": "The `name` of the chosen alternative."},
        "reasoning": {"type": "string"},
        "complexity_justification": {
          "type": "string",
          "description": "Required when the decision is not the simplest backwards-compatible alternative."
        },
        "carry_forward": {
          "type": "array", "maxItems": 5, "items": {"type": "string"},
          "description": "Up to 5 lines to remember for later questions."
        }
      },
      "required": ["question_id", "alternatives", "decision", "reasoning", "carry_forward"]
    }
  }
}
```

Ollama does not enforce `required`, `minItems` or conditionals, so the server validates (main doc §5 "validates the
required fields on answer tools"). Checks beyond schema shape, each failing with a bounce naming the field:

- `agree_questions`: ids unique.
- `settle`: `question_id == current_question_id`; `decision` equals an `alternatives[].name`; if the chosen
  alternative has `backwards_compatible == false`, `complexity_justification` is non-empty (open question 5 covers
  the wider rule).

`SettledAnswer` (stored in `settled_answers`) holds `question_id`, `alternatives`, `decision`, `reasoning`,
`complexity_justification | None`, and the driver's verdict once recorded.

## 5. From agreeing the list to settling one question per ask

1. `navigator_start(mode="scout", goal, context)`. `scout_phase = "agreeing"`. `context` is built per §7.
2. First `navigator_ask` uses `answer_tool="agree_questions"`. Outcome `answered` returns the proposed list.
   While `agreeing`, `answer_tool="settle"` returns `Outcome{status:"error"}` (no Ollama call).
3. Claude checks the list against the source. To dispute, it sends `navigator_record(kind="challenge", ...)` and a new
   `navigator_ask` with `answer_tool="agree_questions"` (challenges go in their own turn, as in pair; main doc §9).
4. When Claude accepts the list it calls `navigator_record(kind="decided", payload={"scope":"question_list"})`.
   The server freezes `questions`, sets `scout_phase = "settling"` and `current_question_id` to the first id.
   Open question 4 covers reusing `decided` for this.
5. Each following `navigator_ask` uses `answer_tool="settle"`; its `question` text is the current question.
   An `answered` outcome appends to `settled_answers`, advances `current_question_id`, and returns the next id in
   `Outcome.result`. Claude verifies claims against the source and records verdicts with `navigator_record`
   (`rejected_claim`, `carry_forward_verdict`, `challenge`). A challenge on a settled answer re-asks with
   `settle` for the same id; the new answer replaces the earlier one.
6. After the last id is settled, `scout_phase = "done"`. Further `navigator_ask` returns `Outcome{status:"error"}`.
   The skill then runs the summary and AskUserQuestion step (§6).

`need_info` handling is phase 3 unchanged: it is not an answer; Claude answers it with evidence and asks the same
question again.

## 6. `skills/scout/SKILL.md` (full proposed text)

Frontmatter per main doc §9. `name` replaces the directory name in the command, so the command is
`/navigator:scout`. Source: https://code.claude.com/docs/en/skills

````markdown
---
name: scout
description: Pair with a local Ollama navigator to settle the questions of a grilling session one at a time, with justified tool calls and a bias toward simple, backwards-compatible options. Use for navigator/driver scouting.
disable-model-invocation: true
allowed-tools: mcp__plugin_navigator_navigator__*
---

You are the driver. The navigator is a local model reached only through the `navigator_*` MCP tools. Never curl
Ollama and never decide a question yourself. Route every question through `navigator_ask`.

Waiting for results (main doc §9b): `navigator_ask` and `navigator_submit` run as queued jobs. Don't poll, and don't
call `navigator_status` in a loop.
- A background-task reply: wait for the task notification. Its result is the `Outcome`.
- A `{"status":"queued", ..., "wait_command"}` reply: run `wait_command` once with Bash and
  `run_in_background: true`, then act on the `Outcome` JSON it prints. Exit 3 means cancelled, 4 failed, 5 server
  exited.
- Only one job runs per server; on a `job <id> active` error, wait for that job's result.

1. Build the context. For each closed ticket of the grilling session, read its resolution comment in full, not the
   map's one-line gist. Put each ticket id and its full comment in the `context` argument. Call
   `navigator_start` with `mode="scout"`, a `goal`, and that `context`.

2. Agree the question list. Call `navigator_ask` with a neutral question asking which questions this session must
   settle, `answer_tool="agree_questions"`. Check each proposed question against the tickets. If one is wrong,
   missing or redundant, record a `challenge` with the evidence, then ask again. Do not change the list yourself.
   When you accept the list, call `navigator_record` with `kind="decided"` and `payload={"scope":"question_list"}`.

3. Settle one question per `navigator_ask`, `answer_tool="settle"`, in list order. Give the question and the
   excerpts it needs. Offer options without a preference.

4. Run or deny every tool call in an outcome with `status="tool_calls"`, then send the results with
   `navigator_submit`.
   - Run file calls only inside the repo, outside `.git/` and `node_modules/`, and not secrets.
   - Run web searches with WebSearch in standard mode. If a query carries code, secrets, private paths or
     hostnames, or aims at a non-public site, do not run it; report it and ask through AskUserQuestion.
   - Look at each screenshot before submitting it and withhold any that shows a secret.

5. On `status="need_info"`, answer each listed fact from verified sources only, never guess on the navigator's
   behalf, then ask the same question again.

6. Check each `settle` result against the source before accepting it. Challenge, citing the evidence, if
   - alternatives were not surveyed or a plausible one is missing,
   - a case for or against is uncited or wrong,
   - the decision is not the simplest backwards-compatible option and no `complexity_justification` explains why.
   Send a challenge in its own turn: record it with `navigator_record(kind="challenge")`, then ask again with
   `settle`. Check each carry-forward line against the source and record the verdict with
   `navigator_record(kind="carry_forward_verdict")`; record wrong claims as `rejected_claim` with evidence. If a
   challenge goes back and forth without the navigator moving, ask the user through AskUserQuestion.

7. Report each `answered` outcome to the user in one line.

8. When every question is settled, show the user a summary per question in this form:

   ```
   ## <question id>: <question>
   Alternatives: <name> (for: ...; against: ...) | <name> (...) | ...
   Decision: <decision>
   Reasoning: <reasoning>
   ```

   Then call `navigator_end` and ask through AskUserQuestion with three options:
   - Approve
   - Reject
   - Other (suggest an edit)

   Act on the answer: Approve, finish. Reject, report that the navigator's answers were rejected and stop.
   Other, take the user's edits and, if they change a decision, send it back to the navigator as a challenge.
````

Point 8 calls `navigator_end` before the user answers, so a post-Other challenge cannot reach the same session. Open
question 6.

## 7. Context from closed tickets

- Input per ticket: ticket id, title, and the full text of its resolution comment (the last comment that records the
  outcome). Not a one-line gist.
- Claude reads the comments and passes them in `navigator_start.context`; the server stores `context` unmodified in
  `SessionState` (phase 2) and renders it under its own heading in the stable block (§9a, grow-only).
- The server does not fetch tickets; it has no tracker access (main doc §1 non-goals and §5). Which tool Claude uses to
  read tickets is open question 1.

## 8. Tests (`server/tests/test_scout.py`, fake Ollama from phase 3)

| # | Input | Expected |
| --- | --- | --- |
| 1 | `navigator_start(mode="scout", ...)` | state has `scout_phase="agreeing"`, `questions=[]`, `settled_answers=[]` |
| 2 | `navigator_ask(answer_tool="settle")` while `agreeing` | `Outcome{status:"error"}`; fake Ollama receives 0 requests |
| 3 | `navigator_ask(answer_tool="set_plan")` in a scout session | `Outcome{status:"error"}` |
| 4 | request built for `agree_questions` | `messages[0].content == SCOUT_CHARTER`; `tools` contains the `agree_questions` entry exactly as in §4; payload keys are exactly `model, messages, tools, stream` |
| 5 | fake replies `agree_questions` with 2 questions | `Outcome{status:"answered", tool:"agree_questions"}` and `result.questions` has the 2 entries |
| 6 | fake replies `agree_questions` missing `carry_forward` | bounce to the fake, as a missing required field (phase 3 bounce path) |
| 7 | `agree_questions` with duplicate ids | bounce naming `id` |
| 8 | `navigator_record(kind="decided", payload={"scope":"question_list"})` | `scout_phase="settling"`, `current_question_id="q1"` |
| 9 | `settle` for `q2` while current is `q1` | bounce naming `question_id`; `settled_answers` unchanged |
| 10 | `settle` with 1 alternative | bounce naming `alternatives` |
| 11 | `settle` whose `decision` matches no alternative `name` | bounce naming `decision` |
| 12 | `settle` choosing an alternative with `backwards_compatible=false`, no `complexity_justification` | bounce naming `complexity_justification` |
| 13 | valid `settle` for `q1` | `answered`; `settled_answers` has 1 entry; `current_question_id="q2"` |
| 14 | valid `settle` for the last question | `scout_phase="done"`; the next `navigator_ask` returns `error` |
| 15 | re-`settle` `q1` after a `challenge` | the `q1` entry is replaced, `settled_answers` length unchanged |
| 16 | prefix test (main doc §9a) across a tool round in `settling` | request bytes through the summary are identical before and after the appended tool results |
| 17 | rendering with 2 settled answers and 1 pending | golden SESSION SUMMARY (`tests/golden/g4_scout_two_settled.txt`); SETTLED ANSWERS come before OPEN QUESTIONS. Phase 2 has no current-question line for scout, so the order is checked on those two sections |
| 18 | `claude plugin validate .` on a tree containing `skills/scout/SKILL.md` | passes |

## 9. Done criteria

- Tests 1 to 18 pass under `uv run pytest` in `server/`.
- `/navigator:scout` appears in a session started with `claude --plugin-dir .`.
- A scripted run against the fake Ollama goes agree, settle x N, summary, AskUserQuestion with Approve / Reject /
  Other, with Claude issuing only `navigator_*` calls for navigator traffic and no curl.
- Every `request-N.json` has exactly four top-level keys (main doc §12).

## 10. Platform details checked

| Detail | Source | Result |
| --- | --- | --- |
| Ollama `tools` entry shape and `tool_calls[].function.arguments` is an object; works with `stream:false`; no call id in the response | https://github.com/ollama/ollama/blob/main/docs/api.md | verified |
| Plugin skill command is `/<plugin>:<skill>`; frontmatter `name` replaces the directory name in the last segment | https://code.claude.com/docs/en/skills | verified |
| AskUserQuestion: 1-4 questions, 2-4 options each, `header` max 12 chars, `multiSelect` | https://code.claude.com/docs/en/agent-sdk/user-input | verified (SDK page) |
| AskUserQuestion is unavailable in subagents spawned through the Agent tool, so the driver must call it from the main session | same page | verified |
| Whether the Claude Code CLI adds its own "Other" free-text choice to AskUserQuestion | that page describes the SDK host adding "Other"; the CLI behaviour is not stated | unverified, open question 2 |

## 11. Open questions

1. Which tool or CLI does Claude use to read the closed tickets' resolution comments, and where do the grilling
   session's ticket ids come from (skill argument, or a map file)? The scout-ahead source names tickets and a "map" but
   no tracker.
2. Does the Claude Code CLI add an "Other" choice to AskUserQuestion by itself? If it does, the skill's explicit third
   option duplicates it, and the option list should be Approve / Reject only.
3. Main doc §9a lists the step list under per-question fields. Should the scout question list render in the grow-only
   block (it is frozen after step 4 of §5) or in the per-question block?
4. Is reusing `navigator_record(kind="decided")` with `payload.scope="question_list"` acceptable, or does main doc §4
   need a new kind such as `questions_agreed`?
5. What exactly triggers `complexity_justification`: the chosen alternative being non-backwards-compatible only, or also
   any case where a simpler backwards-compatible alternative exists and was not chosen? Only the first is checkable
   from the schema fields; the second needs a `simplicity_rank` field.
6. Should the summary and AskUserQuestion step happen before `navigator_end`, so an "Other" edit can go back to the
   same session as a challenge?
7. Main doc §9a says `tools` has a fixed order. Does a scout session always send both answer tools, or only the one
   named in `answer_tool` (which changes the prefix between the agreeing and settling phases)?
8. The `need_info` format `"<fact> -- matters because <reason>"` is enforced by the charter only. Should the server
   bounce entries lacking the `matters because` clause?
9. Does qwen-agent honour `minItems` and the conditional requirement on `complexity_justification` well enough that
   the server bounce path stays rare? (Main doc §11 asks the related question for compressed summaries.)
10. Main doc §11: do MCP tool calls prompt for permission after the skill's invoking turn? If they do, the multi-turn
    scout loop prompts on each `navigator_*` call, and a settings allow rule is needed.
