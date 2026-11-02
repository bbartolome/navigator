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
