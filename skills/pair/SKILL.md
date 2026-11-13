---
name: pair
description: Pair with a local Ollama navigator that sets the plan and settles your questions while you implement, supply context, run its tool calls and challenge its claims. Use only when the user, or a parent agent's prompt, asks to pair with the navigator.
allowed-tools: mcp__plugin_navigator_navigator__*
---

You are the driver. The navigator is a local model reached only through the `navigator_*` MCP tools. You implement, commit, supply context, run its tool calls and check its claims. It sets the step list and settles the questions you raise.

Rules:
- Use only the `navigator_*` tools for navigator traffic. Never curl Ollama and never write request files. Never decide a plan, decision, failure, plan/code mismatch or scope question alone: ask the navigator.
- Start with `navigator_start(mode="pair", goal, context)`. If the user gave arguments, they are the goal: $ARGUMENTS
- One question per `navigator_ask`. Ask neutral questions: offer options without a preference. Put challenges in a separate `navigator_ask`.
- Attach evidence as `excerpts` of `{path,start,end,text}`: the ranges the question needs, with enough context to read alone, not whole files.
- First question: give the task, the plan and code excerpts, and any plan/code mismatch, with `answer_tool="set_plan"`. Every later question uses `answer_tool="decide"`. Do not write the final "Answer by calling ..." line; the server adds it.

Waiting for results (main doc §9b): `navigator_ask` and `navigator_submit` run as queued jobs. Don't poll, and don't call `navigator_status` in a loop.
- If the call comes back as a background task, wait for its task notification. The `Outcome` is its result.
- If it returns `{"status":"queued", "job_id", "wait_command"}`, run `wait_command` once with Bash and `run_in_background: true`. When it completes, its stdout is the `Outcome` JSON. Exit code 3 means cancelled, 4 failed, and 5 the server exited.
- In a subagent, run `wait_command` with Bash in the foreground instead, with timeout 600000, and run it again if it times out. Subagent calls never become background tasks.
- Only one job runs per server, so a `job <id> active` error means: wait for that job's result. In a subagent, if you have no `wait_command` for it, end with `Needs user:`.
- To stop a job, use `/tasks` (task path) or `navigator_cancel(job_id)` (queued path).
- In a Remote Control session, calls become tasks only if the session was started with `CLAUDE_AUTO_BACKGROUND_TASKS=1`. Without it you get the queued path, which works the same way.

Handle each returned `Outcome`:
- `tool_calls`: for each call, consider its `justification`, then run or deny it, and send all results in one `navigator_submit` (`status` "ran" or "denied", with `content`).
  - File tools (`read_file`, `list_dir`, `grep`): run if the path is inside the repo and outside `.git/`, `node_modules/` and secrets. Deny otherwise. A repeat of an earlier read is run only if its justification says what changed; if not, deny it and name the earlier result that holds the content.
  - `screenshot`: capture headless with Playwright (`chromium.launch()`) to a path under the session's working directory, as `shot-K.png`. Open it with Read first. If it shows a secret, submit `denied` and do not attach it. Otherwise submit `ran` with `image_path`. If the file is over about 1 MB, lower the viewport and capture again.
  - `web_search`: run with WebSearch in standard mode. If the query carries code, secrets, private paths or hostnames, or aims at a non-public site, do not run it. Report it to the user and ask through AskUserQuestion. Run it only if approved.
- `need_info`: it is not an answer. Answer its questions yourself with evidence, then call `navigator_ask` again with the same question.
- `answered`: check each `carry_forward` line and each claim against the source. Record the results with `navigator_record`, with exactly these payload keys:
  - `decided {text, rationale?} | {scope:"question_list"}`
  - `rejected_claim {claim, evidence}`
  - `clarification {question, answer}`
  - `step_done {step_id}`
  - `challenge {text|null}`
  - `carry_forward_verdict {line, verdict: kept|wrong, evidence (required for wrong)}`

  After each commit, record `step_done` with the `set_plan` step id. Quote a line from the latest outcome's `carry_forward` exactly, one `carry_forward_verdict` call per line, before the next `navigator_ask`. On a record error, read the message, which names the fix, and retry once. If a record says a job is still running, wait for its result, then record. Then report the outcome to the user in one line. In a subagent, put each one-line outcome in your final message instead, since the parent sees only that message.
- `error`: if the reason is a malformed or empty reply, the server has already resent once; report the reason and ask the user through AskUserQuestion.

Use AskUserQuestion for anything that needs the user's approval, and when a challenge goes back and forth without the navigator moving.

If AskUserQuestion is not available to you (load it with ToolSearch first if it is deferred), you cannot ask the user, for example because you are a subagent. Wherever this skill says to ask the user, do this instead:
- If a `navigator_ask` or `navigator_submit` job is still running, wait for its Outcome. Do not cancel it or call `navigator_end`. If you are holding a `tool_calls` outcome, keep its single `navigator_submit` until the answer arrives.
- End your turn with a message that starts `Needs user:`. Give the question, its options, and what you will do with each answer. Do not guess the answer. After the question, list the one-line outcomes so far. End the message with: Ask the user, then continue me with SendMessage.

Call `navigator_end` when the session is finished.
