# Phase 4: Pair mode + `skills/pair`

Milestone 4 of [navigator-plugin-plan.md](../navigator-plugin-plan.md). Depends on phase 1 (scaffold), phase 2 (`SessionState`, `render.py`, state model in §6) and phase 3 (Ollama loop, bounce, resend/retry, §9a payload layout). This phase redefines none of them. It adds the pair charter, the pair tool schemas, answer-tool validation and the `pair` skill.

Source of truth for text and parameters: `to-llm/SKILL.md` (path in main doc §1). Fields marked **[proposed]** are not specified there.

## 1. Scope

| File | Change |
| --- | --- |
| `server/src/navigator_mcp/modes/pair.py` | a `Mode` implementation (phase 3 §1): `name="pair"`, `charter`, `answer_tools`, `missing_fields()`, `carry_forward()` |
| `server/src/navigator_mcp/schemas.py` | the six navigator tools plus `set_plan` / `decide` as JSON-schema dicts |
| `skills/pair/SKILL.md` | new (§5) |
| `server/tests/test_pair.py` | new (§6) |

`modes/pair.py` implements the `Mode` protocol from phase 3 §1. Below, `CHARTER` is the value of `charter` and `ANSWER_TOOLS` the value of `answer_tools`.

## 2. `modes/pair.py`

### 2.1 Charter

`CHARTER` is the text between `<charter>` and `</charter>` in to-llm/SKILL.md, copied byte for byte (line breaks included, tags excluded). Sent as the system message per main doc §9a.

```
You are the NAVIGATOR. You decide; the driver implements. Serve the project owner, not the driver:
correctness, then evolvable contracts, then simplicity. Disagree when the evidence says so.
Cite path:line, or the URL for a web result, for every claim. What the SESSION SUMMARY lists counts as read; mark anything
else you haven't read UNVERIFIED. Contracts change additively; flag one-way doors; defer what
the spec doesn't require.
Explore with your tools until you can cite evidence for a decision, then stop. Tool calls need no
text, only a filled justification. When no read can settle it, call need_info.
When a question turns on what is rendered, request a screenshot and cite it as shot-K.
Answer the one question asked only by calling the tool the driver names, once, with at most 5
findings; put nothing in text. Carry forward up to 5 lines you must remember for later questions.
```

### 2.2 Tool schemas

Tool order in the request is fixed (main doc §9a): `read_file, list_dir, grep, screenshot, web_search, need_info`, then the one answer tool named by `answer_tool`. Wrapping in the Ollama `tools` entry format is a phase 3 concern (see open question 1).

Shared constants:
- `J_FILE` = `"what you expect to find and which question it settles"` (to-llm, file tools)
- `J_WEB` = `"what you expect the results to say and which question it settles"` (to-llm, web_search)

Navigator tools. `read_file`, `list_dir` and `grep` take `justification` per to-llm; their other parameters are **[proposed]**.

```json
{"name":"read_file","parameters":{"type":"object","required":["path","justification"],"properties":{
  "path":{"type":"string"},
  "start":{"type":"integer"},
  "end":{"type":"integer"},
  "justification":{"type":"string","description":"<J_FILE>"}}}}

{"name":"list_dir","parameters":{"type":"object","required":["path","justification"],"properties":{
  "path":{"type":"string"},
  "justification":{"type":"string","description":"<J_FILE>"}}}}

{"name":"grep","parameters":{"type":"object","required":["pattern","justification"],"properties":{
  "pattern":{"type":"string"},
  "path":{"type":"string"},
  "justification":{"type":"string","description":"<J_FILE>"}}}}

{"name":"screenshot","parameters":{"type":"object","required":["target","justification"],"properties":{
  "target":{"type":"string","description":"a URL or the page the task serves"},
  "viewport":{"type":"string","description":"default 1280x800"},
  "justification":{"type":"string","description":"<J_FILE>"}}}}

{"name":"web_search","parameters":{"type":"object","required":["query","justification"],"properties":{
  "query":{"type":"string"},
  "justification":{"type":"string","description":"<J_WEB>"}}}}

{"name":"need_info","parameters":{"type":"object","required":["questions"],"properties":{
  "questions":{"type":"array","items":{"type":"string"},"description":"each saying what it settles"}}}}
```

Notes:
- to-llm gives the `screenshot` justification no description of its own. `J_FILE` is assumed **[proposed]**.
- Tool-level `description` strings are not given in to-llm. Phase 4 leaves them out. If phase 3 requires one, use the name and the justification sentence only.
- `viewport` format (`"1280x800"` string) is **[proposed]**; to-llm says only "default 1280x800".
- `path` is repo-relative **[proposed]**; the driver enforces repo containment (main doc §5).

Answer tools (names, required fields and limits from to-llm; field types **[proposed]** where noted):

```json
{"name":"set_plan","parameters":{"type":"object","required":["steps","carry_forward"],"properties":{
  "steps":{"type":"array","items":{"type":"object",
    "required":["id","files","change","verification"],
    "properties":{
      "id":{"type":"string"},
      "files":{"type":"array","items":{"type":"string"}},
      "change":{"type":"string"},
      "verification":{"type":"string"}}}},
  "carry_forward":{"type":"array","maxItems":5,"items":{"type":"string"}}}}}

{"name":"decide","parameters":{"type":"object","required":["decision","carry_forward"],"properties":{
  "decision":{"type":"string"},
  "findings":{"type":"array","maxItems":5,"items":{"type":"object",
    "required":["severity","cite","issue","fix"],
    "properties":{
      "severity":{"type":"string","enum":["S1","S2","S3"]},
      "cite":{"type":"string","description":"path:line"},
      "issue":{"type":"string"},
      "fix":{"type":"string"}}}},
  "carry_forward":{"type":"array","maxItems":5,"items":{"type":"string"}}}}}
```

Types of `id`, `files` and the finding key name `cite` are **[proposed]**; to-llm lists "id, files, change, verification" and "severity S1|S2|S3, cite path:line, issue, fix".

### 2.3 Final line of the question

The phase 3 loop appends `Answer by calling <answer_tool>.` (phase 3 §4, step 3). Pair mode adds nothing. `answer_tool` must be a key of `ANSWER_TOOLS` (`set_plan` or `decide`); otherwise phase 3 returns `{status:"error"}`.

### 2.4 Validation of answer-tool fields

`missing_fields(name, args)` (phase 3 `Mode` protocol) runs on an answer-tool call before the loop returns `answered`. It checks against the schema in §2.2:
- Required keys are present: `set_plan` needs `steps`, `carry_forward`; `decide` needs `decision`, `carry_forward`.
- Type checks for each field as declared.
- `carry_forward` has at most 5 entries; `findings` at most 5.
- Each `steps[]` item has `id`, `files`, `change`, `verification`; each `findings[]` item has `severity` (S1|S2|S3), `cite`, `issue`, `fix`.
- Empty `steps` is invalid.

On failure, the exchange does not end. The failure list goes back to the navigator as a tool-result message naming each missing or invalid field, via the phase 3 resend path. This matches to-llm ("return it as a tool result naming any that are missing"). Whether this uses the one resend or is separate is an open question (3). On pass the outcome is `{status:"answered", tool, result, carry_forward}` (main doc §4).

An answer tool not matching the requested `answer_tool` is treated as a malformed reply (phase 3 rules). `need_info` is not an answer (main doc §4 `Outcome`).

## 3. Server wiring

- `navigator_start(mode="pair")` selects `modes.pair`.
- `navigator_ask` takes `answer_tool` from the caller and uses §2.4. Excerpts, images and compaction follow phases 2–3.
- The first question's content (task, plan, excerpts, mismatch; `answer_tool=set_plan`) is skill behaviour (§5), not server enforcement.

## 4. Platform facts used

Checked at https://code.claude.com/docs/en/skills (fetched; the last ~19k characters of the page were not read):
- Frontmatter fields are all optional. Unrecognised keys are ignored without error.
- `disable-model-invocation: true` prevents automatic loading.
- `allowed-tools` accepts a space- or comma-separated string or a YAML list, pre-approves for the invoking turn only (cleared on the next user message) and does not restrict other tools.
- `name` overrides the directory name for the command; plugin skills are `/plugin:skill`, so `skills/pair` gives `/navigator:pair`.
- Trailing `$ARGUMENTS`: if the body has no placeholder, `ARGUMENTS: <value>` is appended.
- Outside Claude Code (claude.ai/API upload) only `name`, `description`, `license`, `compatibility`, `metadata` and `allowed-tools` are allowed; `disable-model-invocation` would error there. Not relevant to plugin loading.

Main doc §2 facts (tool names `mcp__plugin_navigator_navigator__*`, `allowed-tools` semantics) are used as written.

## 5. `skills/pair/SKILL.md` (full proposed text)

Frontmatter per main doc §9. `description` is the to-llm description.

````markdown
---
name: pair
description: Pair with a local Ollama navigator that sets the plan and settles your questions while you implement, supply context, run its tool calls and challenge its claims. Use for navigator/driver pairing.
disable-model-invocation: true
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
- Only one job runs per server, so a `job <id> active` error means: wait for that job's result.
- To stop a job, use `/tasks` (task path) or `navigator_cancel(job_id)` (queued path).
- In a Remote Control session, calls become tasks only if the session was started with `CLAUDE_AUTO_BACKGROUND_TASKS=1`. Without it you get the queued path, which works the same way.

Handle each returned `Outcome`:
- `tool_calls`: for each call, consider its `justification`, then run or deny it, and send all results in one `navigator_submit` (`status` "ran" or "denied", with `content`).
  - File tools (`read_file`, `list_dir`, `grep`): run if the path is inside the repo and outside `.git/`, `node_modules/` and secrets. Deny otherwise. A repeat of an earlier read is run only if its justification says what changed; if not, deny it and name the earlier result that holds the content.
  - `screenshot`: capture headless with Playwright (`chromium.launch()`) to a path under the session's working directory, as `shot-K.png`. Open it with Read first. If it shows a secret, submit `denied` and do not attach it. Otherwise submit `ran` with `image_path`. If the file is over about 1 MB, lower the viewport and capture again.
  - `web_search`: run with WebSearch in standard mode. If the query carries code, secrets, private paths or hostnames, or aims at a non-public site, do not run it. Report it to the user and ask through AskUserQuestion. Run it only if approved.
- `need_info`: it is not an answer. Answer its questions yourself with evidence, then call `navigator_ask` again with the same question.
- `answered`: check each `carry_forward` line and each claim against the source. Record verdicts with `navigator_record` (`carry_forward_verdict`, `rejected_claim` with the evidence, `decided`, `step_done`, `clarification`, `challenge`). Then report the outcome to the user in one line.
- `error`: if the reason is a malformed or empty reply, the server has already resent once; report the reason and ask the user through AskUserQuestion.

Use AskUserQuestion for anything that needs the user's approval, and when a challenge goes back and forth without the navigator moving.

Call `navigator_end` when the session is finished.
````

Notes on the text:
- `$ARGUMENTS` behaviour per §4. Whether the skill should use it at all is open question 5.
- `image_path` and screenshot location: the `/tmp/navigator/<id>/` convention from to-llm is replaced by "a path"; the exact directory is open question 4.
- `AskUserQuestion` and `WebSearch` are not in `allowed-tools`; whether they prompt is open question 2.

## 6. Tests (`server/tests/test_pair.py`)

Against the phase 3 fake Ollama where a loop is needed.

| # | Input | Expected |
| --- | --- | --- |
| 1 | `pair.CHARTER` vs text between `<charter>` tags in to-llm/SKILL.md (a fixture copy of that file, so the test doesn't need `<claude-config>`) | byte-equal |
| 2 | Schemas for the 8 tools | each is valid JSON Schema; names and `required` match §2.2 exactly; `screenshot.required == ["target","justification"]`; `need_info.required == ["questions"]` |
| 3 | Tool list built with `answer_tool="decide"` | order `read_file,list_dir,grep,screenshot,web_search,need_info,decide`; canonical-JSON bytes identical across two builds |
| 4 | request built by the phase 3 loop with `answer_tool="set_plan"` | last line of the user message is `Answer by calling set_plan.` |
| 5 | Sent user message for question `"Q?"`, `answer_tool="decide"` | last line is `Answer by calling decide.`; question appears once |
| 6 | Question already ending in the suffix | suffix appears once |
| 7 | `navigator_ask(answer_tool="read_file")` | `{status:"error"}`; fake Ollama received 0 requests |
| 8 | `missing_fields("set_plan", {"steps":[...valid], "carry_forward":[]})` | `[]` |
| 9 | `set_plan` without `carry_forward` | list names `carry_forward` |
| 10 | `set_plan` with `steps:[]` | invalid |
| 11 | `set_plan` step missing `verification` | names `steps[0].verification` |
| 12 | `decide` with `{"decision":"x","carry_forward":[]}` | valid (findings optional) |
| 13 | `decide` with 6 `carry_forward` strings | invalid |
| 14 | `decide` with 6 findings | invalid |
| 15 | `decide` finding `severity:"S4"` | invalid |
| 16 | Fake Ollama replies to `decide` with missing `decision`, then a valid call | first reply returned to navigator as a tool result naming `decision`; final outcome `answered` |
| 17 | Fake Ollama replies with `set_plan` when `decide` was asked | handled as a malformed reply per phase 3 |
| 18 | `need_info` reply | outcome `need_info`, not `answered` |
| 19 | Frontmatter of `skills/pair/SKILL.md` parsed as YAML | `disable-model-invocation is True`; `allowed-tools == "mcp__plugin_navigator_navigator__*"`; `name == "pair"`; body contains `navigator_ask`, `navigator_submit`, `navigator_record`, `WebSearch`, `AskUserQuestion`, `Playwright`, and not `curl` outside the "Never curl" sentence |
| 20 | `claude plugin validate .` | exit 0 |

## 7. End-to-end manual check

1. Ollama reachable at `NAVIGATOR_OLLAMA_URL` with `qwen-agent:latest` loaded.
2. `claude --plugin-dir .` in a small scratch repo; `/mcp` lists `navigator`.
3. Run `/navigator:pair add a --version flag to the CLI`. Expected:
   - Claude calls `navigator_start`, then `navigator_ask` with `answer_tool="set_plan"`.
   - The `Outcome` is `tool_calls` or `answered`. Any `tool_calls` are run or denied and submitted with `navigator_submit`.
   - A `set_plan` outcome yields a one-line report.
   - A follow-up `decide` question yields a one-line report.
4. Force a denial: look in the session dir for the request that follows a navigator call to `read_file` on `.git/config` (or a `.env`). That call must be submitted `denied`.
5. If the navigator asks for a screenshot, the PNG exists at the chosen path and was opened with Read before `navigator_submit`.
6. Check the session dir under `${CLAUDE_PLUGIN_DATA}/sessions`:
   - every `request-N.json` has exactly `model, messages, tools, stream`;
   - the last line of the user message in each question's first request is `Answer by calling <tool>.`;
   - the system message equals the charter.
7. `navigator_status` shows the recorded verdicts.
8. The transcript contains no Bash call to curl.
9. After the first `/navigator:pair` turn, note whether a later turn's `navigator_*` calls prompt for permission (open question 2).

## 8. Done criteria

- `uv run pytest` passes in `server/`, including §6 tests 1–19.
- `claude plugin validate .` passes.
- §7 steps 1–8 completed, with the result of step 9 written down.
- `CHARTER` byte-equals the to-llm charter.
- Every **[proposed]** field in §2.2 is either confirmed or recorded as changed in this file.

## 9. Open questions

1. Does Ollama `/api/chat` accept the `tools` entry shape `{"type":"function","function":{name,description,parameters}}` with the schemas above? Not checked in this phase. Source to check: https://github.com/ollama/ollama/blob/main/docs/api.md.
2. (main doc §11) Do MCP tool calls, `WebSearch` and `AskUserQuestion` prompt for permission after the invoking turn, given that `allowed-tools` applies to that turn only? If they do, does a settings allow rule for `mcp__plugin_navigator_navigator__*` need to ship?
3. Does answer-tool validation failure use the same single resend as a malformed reply, or a separate attempt? What happens if the second reply is also invalid?
4. Which directory should screenshots be written to (`/tmp/navigator/<id>/` as in to-llm, or the session state dir), and must `navigator_submit` accept paths outside `${CLAUDE_PLUGIN_DATA}`?
5. Should the skill use `$ARGUMENTS` as the goal, or ask the user when none is given? The `/navigator:pair` argument behaviour in a plugin skill was not tested.
6. (main doc §11) Is the append-only tool-round layout acceptable in place of to-llm's per-send summary rewrite? It affects what the navigator sees on tool rounds.
7. The to-llm text does not give the parameters of `read_file`, `list_dir`, `grep`, the item types of `steps`, or the finding key names. Are the proposed ones in §2.2 acceptable?
8. How is `AskUserQuestion` reached when a skill runs inside a subagent or non-interactive session? Not checked.
