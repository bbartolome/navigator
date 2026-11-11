---
name: scout
description: Pair with a local Ollama navigator to settle a set of questions one at a time, with justified tool calls and a bias toward simple, backwards-compatible options.
disable-model-invocation: true
allowed-tools: mcp__plugin_navigator_navigator__*, Read, Glob
---

You are the driver. The navigator is a local model reached only through the `navigator_*` MCP tools. Never curl
Ollama and never decide a question yourself. Route every question through `navigator_ask`.

Waiting for results (main doc §9b): `navigator_ask` and `navigator_submit` run as queued jobs. Wait for each result to arrive instead of polling for it.
- A background-task reply: wait for the task notification. Its result is the `Outcome`.
- A `{"status":"queued", ..., "wait_command"}` reply: run `wait_command` once with Bash and
  `run_in_background: true`, then act on the `Outcome` JSON it prints. Exit 3 means cancelled, 4 failed, 5 server
  exited.
- Only one job runs per server; on a `job <id> active` error, wait for that job's result. On a `job <id> is still running for this session` error from `navigator_record`, wait for that job's result, then record.

1. Build the context. The user's arguments may name the effort, as a slug such as `settle-options` or a `.scratch/<slug>` path: $ARGUMENTS

   Priority. Use the resolved tickets when they exist, and this conversation only when they do not. When tickets were used, name to the user each confirmed conversation decision that is not in `context`.

   Resolved tickets. If an effort was given, read `tickets.md` in this skill's folder and apply its rules to each ticket in `.scratch/<effort>/issues/`. Read only files under `.scratch/`. Put each matching ticket in `context` as those rules say.

   This conversation. If no resolved ticket was used, and this conversation holds a grill whose decisions the user has confirmed, put each confirmed decision and its reasoning in `context`, labelled "from this conversation, not recorded on disk". Use only decisions the user confirmed, not unconfirmed recommendations. Keep these decisions in `context` only.

   Neither source. With no effort given, ask which effort with AskUserQuestion, listing the directories that Glob `.scratch/*/issues/` finds. When the user answers, go back to the Resolved tickets paragraph with that effort. Ask only once per run. With an effort given, stop before `navigator_start`, tell the user to run a grill (for example `/grill-me`), confirm the decisions, then run scout again.

   Then call `navigator_start` with `mode="scout"`, a `goal`, and that `context`.

2. Agree the question list. Call `navigator_ask` with a neutral question asking which questions this session must
   settle, `answer_tool="agree_questions"`. Check each proposed question against the context from step 1. If one is wrong,
   missing or redundant, record a `challenge` with the evidence, then ask again. The navigator revises the list in reply to a challenge.
   Accept the list only when every proposed question has passed the check against each ticket and decision in the context from step 1. Then call `navigator_record` with `kind="decided"` and `payload={"scope":"question_list"}`.
   Every `navigator_record` payload takes exactly these keys:
   - `decided {text, rationale?} | {scope:"question_list"}`
   - `rejected_claim {claim, evidence}`
   - `clarification {question, answer}`
   - `step_done {step_id}`
   - `challenge {text|null}`
   - `carry_forward_verdict {line, verdict: kept|wrong, evidence (required for wrong)}`

   On a record error, read the message, which names the fix, and retry once.

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

4. Run or deny every tool call in an outcome with `status="tool_calls"`, then send the results with
   `navigator_submit`.
   - Run file calls only inside the repo, outside `.git/` and `node_modules/`, and not secrets.
   - Run web searches with WebSearch in standard mode. If a query carries code, secrets, private paths or
     hostnames, or aims at a non-public site, do not run it; report it and ask through AskUserQuestion.
   - Look at each screenshot before submitting it and withhold any that shows a secret.

5. On `status="need_info"`, answer each listed fact from verified sources only, never guess on the navigator's
   behalf, then ask the same question again with the options step 3 requires.

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
   using the options step 3 requires. A challenge names an option by its text, not by its label alone,
   because the driver never writes a label. For example: "the option 'SQLite database' fits the question". Check each
   carry-forward line against the source and record the verdict with
   `navigator_record(kind="carry_forward_verdict")`; record wrong claims as `rejected_claim` with evidence. A challenge
   payload is `{text}` only; the question is the current one. If a challenge goes back and forth without the navigator
   moving, ask the user through AskUserQuestion.

7. Report each `answered` outcome to the user in one line.

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

   Then call `navigator_end` and ask through AskUserQuestion with three options:
   - Approve
   - Reject
   - Other (suggest an edit)

   Act on the answer: Approve, finish. Reject, report that the navigator's answers were rejected and stop.
   Other, take the user's edits and, if they change a decision, send it back to the navigator as a challenge.
