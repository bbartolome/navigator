# Phase 7: Retire to-llm and scout-ahead

Milestone 7 of [navigator-plugin-plan.md](../navigator-plugin-plan.md) (§10). Depends on phases 4 (pair mode) and 5
(scout mode). Phase 6 (compression) is not required; run with `NAVIGATOR_COMPRESSION=off` if it is not done.

## 1. Facts found (read-only inspection, 2026-11-01)

- Old skills: `<synced-skills>/{to-llm,scout-ahead}/SKILL.md`
  (32 and 11 lines). Each dir holds only `SKILL.md`.
- Sibling files in that dir: `manifest.json`, `.last-complete-round` (`<uuid> sdk-cli,cli`),
  and a bucket marker `../.bucket-6bf666ac-...` (empty).
- `manifest.json` entries for both skills have `"source": "plugin"` and a `backingPluginId`:
  - `to-llm`: `skill_01BSYPp8X8Nz22kdeH3bFrFR`, plugin `plugin_0114eQZcHK2osZD5cjftu6dj`, updated 2026-11-01
  - `scout-ahead`: `skill_01Mkg7Gnwa7pYksAuvCkFikq`, plugin `plugin_01RjoXDijttPp66F7dzNQ3iB`, updated 2026-10-25
- Docs: terminal sessions signed in via `/login` download the claude.ai account's skills into `<claude-config>/skills/synced/`,
  re-check about every 10 min active / 40 min idle, downloads are one-way, local edits are overwritten, names run as
  `/anthropic-skills:<name>`. Opt-out is `"syncClaudeAiSkills": false` (user settings), which disables all synced skills.
  — https://code.claude.com/docs/en/skills (section "Synced skills from claude.ai")
- Docs: for claude.ai-synced plugins, "Remove: turn the plugin off on claude.ai."
  — https://code.claude.com/docs/en/discover-plugins (section "Manage plugins synced from claude.ai")
- `<trash>/navigator-driver/SKILL.md` exists (trashed 2026-10-26). Who
  trashed it is not known.
- Repo tracking: `skills-lock.json` lists only `mattpocock/skills` entries (github source, `skillPath`, `computedHash`).
  It has no `to-llm`, `scout-ahead` or `navigator` entry. `.gitignore` contains only `.claude/settings.local.json`.
  `.claude/skills/` is tracked. `docs/` is currently untracked.

## 2. Install and enable the navigator plugin for normal use

Docs used:
- https://code.claude.com/docs/en/plugin-marketplaces (local marketplace, `claude plugin marketplace add ./dir`,
  `claude plugin install <name>@<marketplace>`)
- https://code.claude.com/docs/en/discover-plugins (scopes, enable/disable/uninstall, `/reload-plugins`)
- https://code.claude.com/docs/en/skills (plugin skills load as `/plugin-name:skill-name`)
- `--plugin-dir` (one-session load) is for development only; it is not the everyday route.

Precondition: milestones 1-5 done, §12 verification items pass, `uv` available (§11).

1. Add `.claude-plugin/marketplace.json` at the repo root (not listed in §3; added in this phase). Fields: `name`,
   `owner`, `plugins: [{ "name": "navigator", "source": "./" }]`. The entry `name` must equal the `plugin.json` `name`.
   `source` of `"./"` (plugin at marketplace root) is unverified; if validation rejects it, see Q1.
2. `claude plugin validate .`  (checks marketplace file and `plugin.json`)
3. `claude plugin marketplace add <repo>`
4. `claude plugin install navigator@<marketplace-name> --scope user`
5. In a new session (or `/reload-plugins`): `claude plugin list` shows `navigator@<marketplace-name>` enabled;
   `claude plugin details navigator` lists skills `pair`, `scout`; `/mcp` lists the `navigator` server; `/` shows
   `/navigator:pair` and `/navigator:scout`.
6. If MCP calls prompt after the skill's invoking turn (§11), add `mcp__plugin_navigator_navigator__*` to the user
   `permissions.allow`.
7. Run the §12 smoke test (`/navigator:pair` on a small task) from the installed plugin, not `--plugin-dir`.

Notes from the docs:
- A local relative-path marketplace is read in place; edits apply at next session start or `/reload-plugins`.
  Auto-update is off by default for local marketplaces.
- Cloud sessions do not load plugins installed on this machine.
- Scope can be `project` instead; that writes `.claude/settings.json` and each collaborator must also run
  `claude plugin install ... --scope project`.

## 3. Parity checklist

Legend: S = server module, K = skill text, C = Claude per §5, X = gap/open.
Phase 4 copies the pair charter into `modes/pair.py` (§2); phase 5 does the scout charter into `modes/scout.py`.

### to-llm

| Behaviour | Now lives in |
| --- | --- |
| Endpoint `host.docker.internal:11434`, model `qwen-agent:latest` | S `ollama.py`, §8 config |
| Roles: Claude is driver, navigator decides | K `pair` (§9) |
| Every failure, mismatch, scope question goes to navigator, one question per request | K `pair` |
| Neutral questions with options; challenges in a separate turn | K `pair` (§9) |
| Replies treated as claims; check against source, cite evidence | C; verdict via `navigator_record` (`rejected_claim`, `challenge`) |
| First question: task, plan/code excerpts, mismatch; asks `set_plan` | K `pair`; `navigator_ask(answer_tool=set_plan)` |
| Later questions end with "Answer by calling <tool>." | S (`modes/pair.py`, question suffix) — verify in phase 4 |
| Per-question compaction; summary built at send time | S `session.py`, `render.py` (§6); tool-round exception in §9a (open Q4) |
| Summary fields (goal, steps, done, decided, challenge, rejected, clarifications, files, searches) | S `SessionState` (§6) |
| Carry-forward checked against source; wrong ones to rejected claims | C; `carry_forward_verdict` record |
| Code sent as `path:start-end` excerpts | `navigator_ask.excerpts`; C selects them |
| Screenshots listed as `shot-K: target (what it shows)`, re-attached only when needed | S state (§6); `images?[path]` on ask |
| `request-N.json` has exactly `model, messages, tools, stream:false` | S `ollama.py` (§5, §2) |
| Tools offered with required `justification` (`need_info` takes `questions`) | S `schemas.py` |
| Answer tools `set_plan`, `decide` and their required fields | S `schemas.py`, `modes/pair.py` |
| Charter verbatim as system prompt | S `modes/pair.py` |
| Run `read_file`, `list_dir`, `grep` only inside repo, outside `.git/`, `node_modules/`, secrets | C (§5) |
| Repeat read allowed only if justification says what changed | S bounce (§5); C judges the rest |
| Call with no justification goes back for one | S bounce (§5) |
| `web_search` via WebSearch standard mode; suspicious query to AskUserQuestion | C (§5) |
| Screenshot capture with headless Playwright, viewport default 1280x800 | X: not assigned in §5 (see Q2) |
| Look at screenshot first; withhold if it shows a secret | C (§5) |
| Image base64 in `images`, under about 1 MB, lower viewport to fit | S enforces limit (§5); viewport reduction: X (Q2) |
| Write `request-N.json` / `reply-N.json`, check with jq | S writes them (§5); jq check replaced by server-side validation |
| curl in background with `timeout: 7200000`, no polling | S job queue + MCP Tasks / auto-background fallback; no timeout by default, optional `NAVIGATOR_JOB_TIMEOUT` (§9b) |
| One resend for malformed or plain-text reply ("Call <tool> with that answer; don't reply in text.") | S (§2, §5) |
| Empty reply: one retry with ±5-line excerpts; then AskUserQuestion with sizes | S retry; `Outcome.error` carries sizes; C/K asks user |
| `need_info`: Claude answers with evidence, then asks the same question again | `Outcome.need_info`; K `pair`; C |
| `set_plan`/`decide` ends exchange once required fields valid; missing fields returned | S validation (§5) |
| Report outcome to the user in one line | K `pair` (§9) |
| AskUserQuestion for approvals and stalled challenges | C (§5) |
| Implement and commit | C (§5) |

### scout-ahead

| Behaviour | Now lives in |
| --- | --- |
| Payload only `model, messages, stream, tools`; no `options` | S `ollama.py` |
| Navigator may ask clarification, listing missing facts and why each matters | S `modes/scout.py` charter; `need_info` |
| Claude answers from verified sources, no guessing | C; K `scout` |
| Discrepancy is reported to navigator, plan not changed unilaterally | K `scout`; `navigator_record(challenge)` |
| Verify claims against source before accepting or challenging | C |
| Explore alternatives, strongest case for and against each; incomplete decision gets challenged | charter in `modes/scout.py`; K `scout` |
| Prefer simplest backwards-compatible option; justify complexity | charter in `modes/scout.py`; K `scout` |
| Tools enabled on every turn | S (§9a fixed `tools`) |
| Each tool-call reply carries a justification per call | S bounce (§5); form (param vs content) see Q3 |
| Rolling summary instead of chat history, one deliverable per request | S `render.py`; `answer_tool` per ask |
| Full context behind each fact, not gists | S state fields; C supplies via `context`/`excerpts` |
| Past actions as one-line results; repeat call must say what is new | S action log + bounce (§5, §6) |
| Raw tool results kept only within the turn | S (§9a tool rounds; summary re-rendered per question) |
| Context built from closed tickets' resolution comments | K `scout` (§9); C gathers into `navigator_start.context` |
| Agree question list first, then one question per turn | K `scout`; scout-only state (§6) |
| Record each settled answer | S state (settled answers); `navigator_record` |
| Final per-question summary (alternatives, decision, reasoning) | K `scout` (§9) |
| AskUserQuestion: Approve / Reject / Other (Suggest Edit) | K `scout`; C |
| curl via background Bash, no polling/Monitor | Replaced by a queued MCP job; the result arrives as a task result or notification; no polling (§9b, phase 3b) |

Before removal, tick every row against the shipped `server/` and `skills/` (phases 4-5 tests and the §12 smoke test).
Any row left X or unverified blocks section 4.

## 4. Disable and remove the old skills

Mechanism per section 1: the two skills are claude.ai account skills with `backingPluginId`s, mirrored one-way into
`<claude-config>/skills/synced/`. Deleting the local dirs is not a removal (overwritten on sync). The documented removal for a
synced plugin is to turn it off on claude.ai.

1. Back up (outside the synced dir, outside git-tracked skill dirs):
   `mkdir -p ~/navigator-retired && cp -r <synced>/to-llm <synced>/scout-ahead ~/navigator-retired/ && cp <synced>/manifest.json ~/navigator-retired/manifest.json.bak`
2. Check the backing plugins are not shared with other skills:
   `jq '.skills[] | select(.backingPluginId=="plugin_0114eQZcHK2osZD5cjftu6dj" or .backingPluginId=="plugin_01RjoXDijttPp66F7dzNQ3iB") | {name,backingPluginId}' <synced>/manifest.json`
   Expect exactly `to-llm` and `scout-ahead`. If any other skill appears, stop (Q5).
3. On claude.ai, in the account's plugin/skill settings, turn off (or delete) the plugin behind `to-llm` and the plugin
   behind `scout-ahead`. The exact UI path is not verified (Q5). This is a manual step for the user.
4. In a terminal Claude Code session signed in with that account, wait up to about 10 min, or restart. Expect
   `Plugins changed. Run /reload-plugins to activate.`; run `/reload-plugins`.
5. Verify: `ls <synced>/` has no `to-llm` or `scout-ahead`; `jq -r '.skills[].name' <synced>/manifest.json` omits both;
   `anthropic-skills:to-llm` and `anthropic-skills:scout-ahead` are absent from the session skill list.
6. Interim suppression if step 3 is delayed: add `Skill(anthropic-skills:to-llm)` and
   `Skill(anthropic-skills:scout-ahead)` to `permissions.deny` in user settings
   (https://code.claude.com/docs/en/skills, "Disabling skills"). Whether the short names `/to-llm`, `/scout-ahead` are
   covered by these rules is unverified. Do not set `syncClaudeAiSkills: false` (disables every synced skill).
7. `skills-lock.json`: no change (neither skill is listed). Do not add a navigator entry; it is a plugin, not a
   `mattpocock/skills` skill.

## 5. Rollback

- Re-enable the two plugins on claude.ai; the next sync (about 10 min or restart) re-downloads both skills. Verify as
  in section 4 step 5, inverted.
- If a skill dir is missing after re-enable, restore from `~/navigator-retired/` into a scratch location for reading
  only; do not write into `synced/` (overwritten on sync). Check `<claude-config>/skills/.trash/` for a moved copy
  (behaviour inferred from `navigator-driver`, unconfirmed).
- Remove any `Skill(anthropic-skills:...)` deny rules added in step 6.
- Disable the new plugin without uninstalling: `claude plugin disable navigator@<marketplace-name>`; remove entirely:
  `claude plugin uninstall navigator@<marketplace-name>` and `claude plugin marketplace remove <marketplace-name>`
  (https://code.claude.com/docs/en/discover-plugins, "Manage plugins from your shell").
- Charters in git (`modes/pair.py`, `modes/scout.py`) are unaffected by rollback.

## 6. Done criteria

- Section 2 steps 1-7 pass on the user's machine with the plugin installed at user scope (not `--plugin-dir`).
- Every row in section 3 maps to an existing, tested location; no X rows remain.
- `/navigator:pair` and `/navigator:scout` each complete a real session end to end with only `navigator_*` calls for
  navigator traffic and no curl to Ollama.
- Section 4 step 5 verification passes: neither old skill is in `synced/`, `manifest.json`, or the session skill list,
  after at least one sync cycle and a new session.
- Backup at `~/navigator-retired/` exists.
- `docs/navigator-plugin-plan.md` §11 item "How are the synced ... skills removed?" is answered with the method used.

## 7. Open questions affecting this phase (from §11, plus new)

- §11: Is `uv` available wherever the plugin runs, including the container behind `host.docker.internal`? Does the
  everyday session run on the host or in a container, given synced skills and `<claude-config>` sit on one filesystem?
- §11: Do MCP tool calls prompt for permission after the skill's invoking turn, requiring the section 2 step 6 allow rule?
- §11: How are the synced to-llm and scout-ahead skills removed? Is turning off the backing plugin on claude.ai
  sufficient, and does the sync delete the local dir or move it to `.trash`?
- §11: Is the append-only tool-round layout (§9a) accepted, so that "summary rewritten on every send" is dropped from
  parity?
- §11: Should `keep_alive` be sent? It breaks the "exactly four keys" parity row if yes.
- Q1: Does `source: "./"` (plugin at marketplace root) pass `claude plugin validate` and install?
- Q2: Who captures screenshots (Playwright, viewport reduction to stay under about 1 MB)? §5 assigns only viewing.
- Q3: Is `justification` a tool parameter (to-llm) or reply `content` (scout-ahead) in scout mode?
- Q5: Where in claude.ai are the backing plugins turned off, and do `plugin_0114eQ...` / `plugin_01RjoX...` back any
  other skill?

## 8. Unverified

- claude.ai UI path and sync deletion behaviour (sections 4-5).
- Short-name coverage of `Skill(...)` deny rules for synced skills.
- `source: "./"` marketplace entry.
- Meaning and writer of `.trash/navigator-driver`.
- Docs page for synced plugins (`plugins/loading.md#synced-plugins`) was not fetched; the skills page was read only to
  character 100000 of about 118800.
