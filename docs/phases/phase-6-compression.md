# Phase 6: LLMLingua-2 compression

Milestone 6 of `docs/navigator-plugin-plan.md` (the "main doc"). Depends on phase 2 (`SessionState`, `render.py`) and phase 3
(Ollama loop, §9a payload layout, cache metrics). Plugs into the rendering hook defined in phase 2. This doc adds only
`compress.py`, its wiring, tests and the evaluation. Behaviour already fixed in main doc §7 (what is compressed, config
names, lazy-load fallback) and §9a (memoization, deterministic rendering) is not restated.

## 1. Library facts (verified)

Sources: `llmlingua/prompt_compressor.py` at
[github.com/microsoft/LLMLingua](https://github.com/microsoft/LLMLingua/blob/main/llmlingua/prompt_compressor.py) and its
[README](https://github.com/microsoft/LLMLingua/blob/main/README.md). Main doc §2 already covers install requires, model
names and the basic usage call.

- `PromptCompressor.__init__(model_name="NousResearch/Llama-2-7b-hf", device_map="cuda", model_config={}, open_api_config={}, use_llmlingua2=False, use_slingua=False, llmlingua2_config={})`.
  The `device_map` default is `"cuda"`, so the wrapper passes `device_map` explicitly (§2 below). The README does not
  mention `device_map`.
- `compress_prompt_llmlingua2(context: List[str], rate=0.5, target_token=-1, use_context_level_filter=False, use_token_level_filter=True, ..., return_word_label=False, token_to_word="mean", force_tokens=[], force_reserve_digit=False, drop_consecutive=False, chunk_end_tokens=[".", "\n"])`.
- `compress_prompt(context: List[str], instruction="", question="", rate=0.5, ..., force_tokens=[], force_reserve_digit=False, drop_consecutive=False, chunk_end_tokens=[".", "\n"], strict_preserve_uncompressed=True)`
  accepts the same three parameters. The README example uses only `rate` and `force_tokens`, and does not document
  `force_reserve_digit` or `drop_consecutive`.
- Return dict of the LLMLingua-2 path: `compressed_prompt`, `compressed_prompt_list`, `origin_tokens`,
  `compressed_tokens`, `ratio`, `rate`, `saving` (plus `fn_labeled_original_prompt` if `return_word_label=True`).
  The `compress_prompt` docstring promises `compressed_prompt_list` too, but the source shown does not return it
  on the original-LLMLingua path.

## 2. `compress.py`

Public surface: `compress_text(text: str) -> CompressResult` and `compressor_status() -> CompressorStatus`.
`CompressResult = {text, origin_tokens, compressed_tokens, memo_hit: bool}`.

- **Config** (names from §7): `NAVIGATOR_COMPRESSION` (`on|off`), `NAVIGATOR_COMPRESSION_RATE` (float),
  `NAVIGATOR_LLMLINGUA_MODEL` (HF model id). Default values are open questions (§8 below); until answered, the
  code default for `NAVIGATOR_COMPRESSION` is `off`.
  Invalid values (non-float rate, rate outside `(0, 1]`, unknown on/off) are treated as a load failure: uncompressed,
  with the reason in status.
- **Lazy loading.** No `import llmlingua` or `torch` at module import. The first `compress_text` call with
  compression on builds `PromptCompressor(model_name=<env>, use_llmlingua2=True, device_map="cpu")` once, under a
  lock, and caches the instance or the failure. `device_map="cpu"` is the plan's choice because the container has no
  stated GPU (open question: is CPU the right target).
- **Call.** `c.compress_prompt([segment], rate=rate, force_tokens=FORCE, drop_consecutive=True, force_reserve_digit=True)`.
  - `context` is passed as a one-element list because the signature types it `List[str]` (the README passes a bare
    string; both-forms behaviour is unverified, so use the typed form).
  - `FORCE = ["\n", "?", ".", ",", ":", ";", "-"]`, extending the README's `['\n', '?']`. §7 asks that newlines and
    punctuation be kept. The effect of `force_tokens` on multi-character tokens is not documented. Whether
    `drop_consecutive=True` helps with `force_tokens` is only inferred from the parameter name and not documented, so
    the A/B run (§7) tests it on/off.
  - `force_reserve_digit=True` is kept so digits in prose (counts, versions) survive. Documented only as a signature
    parameter.
  - Read `["compressed_prompt"]`, `["origin_tokens"]`, `["compressed_tokens"]`.
- **Dispatch.** Check in the source that `compress_prompt` forwards to `compress_prompt_llmlingua2` when
  `use_llmlingua2=True`. If it does not, call `compress_prompt_llmlingua2` directly (same keyword names).
- **Fallback.** On `ImportError` (torch/llmlingua missing), any exception from model download or construction, or any
  exception from the call: log once, return the input text unchanged with `origin_tokens == compressed_tokens`, and
  set `compressor_status().state = "unavailable"` with `reason`. The failure is cached so the load is not retried on
  every render. No exception reaches the MCP tool boundary.
- **Memoization** (§9a). Key `sha256(text ‖ model ‖ repr(rate))` over the segment text, resolved model id and rate.
  Value is the full `CompressResult`. In-memory dict, plus persistence to the session state dir so a server restart
  does not change rendered bytes (open question: is cross-restart stability needed). The memo is consulted before the
  lazy load, so a hit never loads the model. Fallback output (uncompressed) is not memoized.

## 3. What is compressed

Rendering hook: the `transform(text, kind)` interface (`TextTransform`) from phase 2 §4 "Transform hook". Phase 2 decides
which fields it is called on. This phase supplies `make_transform() -> TextTransform`, which returns
`lambda text, kind: compress_text(text).text`. With `NAVIGATOR_COMPRESSION=off` it returns `None`, and phase 2 renders
untransformed. The table below matches phase 2's field list.

| SessionState field (§6) | Treatment |
| --- | --- |
| decided (rationale text), clarifications, open challenge text, web-search descriptions ("what it showed") | Compressed (the §7 list) |
| rejected claims | Open question: claim text and evidence are not in the §7 list; protected until answered |
| goal, mode, step list, current step, done | Protected |
| files read (`path:L-L (what it shows)`), screenshots (`shot-K: ...`), action log | Protected (not in §7 list) |
| carry_forward, scout question list and settled answers | Protected (not in §7 list) |
| excerpts, question, charter, tool schemas, JSON | Protected |

**Byte-for-byte protection inside compressible fields.** Free text can still contain paths, `path:line` refs, IDs
(`call_id`, `shot-K`, request ids), inline code and fenced code. `split_protected(text) -> list[Span(kind, text)]`
tokenizes the field into protected and free spans by regex (backtick code and fences, `path:line[-line]`, bare relative
or absolute paths, `shot-\d+`, call ids, URLs). Only free spans go to the compressor; protected spans are re-inserted
verbatim. Invariant: concatenating the output spans, with free spans replaced, preserves every protected span in
order. Enforced by a post-check: if any protected span is missing or reordered, discard the compressed text for that
field and use the original. Free spans are compressed independently, so cross-span context is lost (open question:
quality cost, see §7). Free spans under a minimum length (config constant, set from the A/B run) are passed through.
The regex set is a design choice with no external source.

## 4. Packaging (`server/pyproject.toml`)

Move `llmlingua` out of base `dependencies` (main doc §3 lists it there) into an extra:

```toml
[project.optional-dependencies]
compression = ["llmlingua"]
```

Syntax per [uv: project dependencies](https://docs.astral.sh/uv/concepts/projects/dependencies/)
(`[project.optional-dependencies]`, extras requested as `package[extra]`). Per the
[uv CLI reference](https://docs.astral.sh/uv/reference/cli/), `uv run --extra <name>` ("Include optional dependencies
from the specified extra name", repeatable) and `--all-extras` exist, project-only. So the `.mcp.json` entry from §3
becomes `uv run --project ${CLAUDE_PLUGIN_ROOT}/server --extra compression navigator-mcp` when compression is wanted.
Open questions: (a) whether a plain `.mcp.json` entry can be switched by env var, or two server entries/configs are
needed so base users do not download torch; (b) whether `--project` and `--extra` combine in that order as written
(each flag is documented separately; the combination is untested); (c) installed size of `llmlingua` + `torch` + the
HF model download and where HF caches the model in the container (`HF_HOME` under `${CLAUDE_PLUGIN_DATA}` is
unverified); (d) a torch CPU-only index configuration, not researched.

Without the extra, `import llmlingua` fails and the §2 fallback applies; tests cover this path.

## 5. Token counting and `navigator_status`

- Per compressed field, record `origin_tokens` and `compressed_tokens` from the compressor result. Which tokenizer
  those counts use is not verified (the source returns them, the doc does not say); treat them as relative
  indicators only.
- Authoritative counts: Ollama's `prompt_eval_count` per request (phase 3 already captures it in the audit files,
  §9a). Compare it with compression on vs off in the A/B run.
- `navigator_status` adds a `compression` object: `{enabled, state: "active"|"unavailable"|"off", reason?, model,
  rate, fields_compressed, memo_hits, memo_misses, origin_tokens_total, compressed_tokens_total}`. When unavailable, the
  summary is uncompressed and `reason` says why (§7 requirement).

## 6. Tests (`server/tests/test_compress.py`, fake compressor injected)

The model is not loaded in unit tests. A fake `PromptCompressor` returns the input with every third whitespace token
removed and deterministic counts. One separate `@pytest.mark.slow` test uses the real model.

1. **Protected-span preservation.** Input field:
   `"Chose approach B because the retry in server/src/navigator_mcp/ollama.py:120-140 hides errors. See call c-0007 and `render(state)`. Counts: 3 files."`
   Expected: output contains `server/src/navigator_mcp/ollama.py:120-140`, `c-0007`, and `` `render(state)` `` byte-identical
   (assert `bytes` containment); `3` retained.
   Also property test: random interleavings of protected spans and words, fake compressor dropping up to all free
   words; every protected span present, in order.
2. **Post-check fallback.** A fake compressor that mangles a span: output equals the original field.
3. **Code fence.** A field containing a fenced block: block bytes unchanged, including trailing whitespace.
4. **Memo-hit determinism.** Call `compress_text(t)` twice with a fake compressor whose output varies per call
   (counter suffix): second result equals the first, `memo_hit` is `True`, fake call count is 1. Changing `rate` or
   model changes the key (miss). Render a full state twice: summaries byte-identical (§9a).
5. **Memo before load.** With the memo primed and loader patched to raise, `compress_text` returns the memo value and
   does not call the loader.
6. **Fallback, torch missing.** Make `import llmlingua` raise `ImportError`: `compress_text(t).text == t`,
   `compressor_status().state == "unavailable"` with a reason, loader attempted once across 3 calls.
7. **Fallback, model load fails.** Constructor raises `OSError`: same expectations.
8. **Off.** `NAVIGATOR_COMPRESSION=off`: identity, loader never touched, no `llmlingua` import
   (`"llmlingua" not in sys.modules`).
9. **Bad config.** `NAVIGATOR_COMPRESSION_RATE=abc` and `=0`: unavailable, uncompressed.
10. **Prefix stability.** Phase 3's prefix-byte test run with compression on: the prefix is byte-identical across tool
    rounds.
11. **Status.** `navigator_status` after a render shows non-zero `memo_misses`, then `memo_hits` on re-render.
12. **Slow/real model.** With the extra installed, on a fixed paragraph: call args are accepted, the return dict has
    the three keys read in §2, and protected spans survive. This also settles the dispatch question in §2.

## 7. A/B evaluation on a real session

1. Pick one finished real `/navigator:pair` session with at least one `request-N.json` per step (audit files, §5). Copy
   the final `SessionState`.
2. Re-render the summary with compression off and on (rate from a small sweep, for example 0.5, 0.33). Record
   `origin_tokens`/`compressed_tokens` and, by replaying the same requests to Ollama, `prompt_eval_count`,
   `prompt_eval_cached_count` (if returned) and `prompt_eval_duration`.
3. Check every protected span from the off render appears byte-identically in the on render (script, not eyeball).
4. Replay the same questions with each configuration; compare the navigator's tool choices and answers for the same
   state. Blind-label the outputs and score them against the off run. Repeat over 2 or more steps and at least 3 samples
   per arm, since sampling variance is not controlled (the payload has no `options`, §2).
5. Run `drop_consecutive` on/off and the model pair (§2 of main doc) on the same state; record CPU latency of
   `compress_text` per field in the container (cold and warm).
6. Record the results in the PR description. Conclusions about speed or quality come only from these numbers.

## 8. Open questions (from §11 affecting this phase)

- What should the default compression rate and model be, and what are the install size, download size and CPU latency
  in the container?
- Does qwen-agent's answer quality change with compressed summaries?
- Is `uv` available wherever the plugin runs, and can the extra be requested from `.mcp.json` as in §4?
- Does `/api/chat` return `prompt_eval_cached_count`, and how much of the prefix does Ollama reuse? (Needed to read the A/B
  prompt-cache numbers.)
- Do rejected-claim evidence, carry_forward and file/shot descriptions need compression, given they are outside §7's list?
- Is memo persistence across server restarts required, or is in-process enough for §9a?
- Does per-span compression lose too much context compared with compressing whole fields?

## 9. Done criteria

- `uv run pytest` passes in `server/` without the extra installed (tests 1-11); test 12 passes with it.
- With the extra missing or the model unloadable, rendering works uncompressed and `navigator_status` reports the reason.
- Every protected span is byte-identical in the on/off comparison of §7 step 3.
- Same state rendered twice with compression on yields identical bytes; prefix test (10) passes.
- `navigator_status` reports the §5 fields; A/B results of §7 recorded; open questions above are either answered with
  numbers or left listed.
- `llmlingua` is in the `compression` extra, not base dependencies; `.mcp.json` matches the §4 decision.
