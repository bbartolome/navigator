"""LLMLingua-2 compression behind NAVIGATOR_COMPRESSION (phase 6; docs/navigator-plugin-plan.md section 7).

Only free prose spans are compressed. Code, paths, ids, URLs and backtick spans are re-inserted byte for byte, and a
post-check falls back to the uncompressed field if any protected span is missing or out of order. The model loads
lazily, once, on the first compression; a failure is cached and the summary is sent uncompressed.
"""

import hashlib
import os
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

DEFAULT_MODEL = "microsoft/llmlingua-2-xlm-roberta-large-meetingbank"
DEFAULT_RATE = 0.33  # open question in phase 6 section 8; the A/B run will settle it
MIN_FREE_TOKENS = 0  # free spans shorter than this pass through; set from the A/B run
FORCE = ["\n", "?", ".", ",", ":", ";", "-"]

# Protected spans, in priority order at each position: fenced code, inline code, URLs, path:line refs, ids, bare paths.
PROTECTED = re.compile(
    r"""
    (```.*?```)
  | (`[^`\n]+`)
  | (https?://\S+)
  | ([\w./-]+\.\w+:\d+(?:-\d+)?)
  | (\b(?:call|shot)-\d+\b|\bc-\d+\b)
  | ((?<![\w.])(?:\.{1,2}/|/)?[\w.-]+(?:/[\w.-]+)+)
    """,
    re.S | re.X,
)


@dataclass
class CompressResult:
    text: str
    origin_tokens: int
    compressed_tokens: int
    memo_hit: bool = False


@dataclass
class Config:
    enabled: bool
    rate: float
    model: str
    error: str | None


def split_protected(text: str) -> list[tuple[str, str]]:
    """Split a field into ("protected" | "free", text) spans. Concatenating the spans gives back the field."""
    spans: list[tuple[str, str]] = []
    pos = 0
    for m in PROTECTED.finditer(text):
        if m.start() > pos:
            spans.append(("free", text[pos : m.start()]))
        spans.append(("protected", m.group(0)))
        pos = m.end()
    if pos < len(text):
        spans.append(("free", text[pos:]))
    return spans


def _config() -> Config:
    raw = os.environ.get("NAVIGATOR_COMPRESSION", "off").strip().lower()
    model = os.environ.get("NAVIGATOR_LLMLINGUA_MODEL", DEFAULT_MODEL)
    enabled = raw == "on"
    if raw not in ("on", "off"):
        return Config(False, DEFAULT_RATE, model, f"NAVIGATOR_COMPRESSION must be on or off, not {raw!r}")
    rate_text = os.environ.get("NAVIGATOR_COMPRESSION_RATE", str(DEFAULT_RATE))
    try:
        rate = float(rate_text)
    except ValueError:
        return Config(enabled, DEFAULT_RATE, model, f"NAVIGATOR_COMPRESSION_RATE is not a number: {rate_text!r}")
    if not 0 < rate <= 1:
        return Config(enabled, rate, model, f"NAVIGATOR_COMPRESSION_RATE must be in (0, 1], got {rate}")
    return Config(enabled, rate, model, None)


def _default_factory(model: str) -> Any:
    from llmlingua import PromptCompressor  # the compression extra; ImportError without it

    return PromptCompressor(model_name=model, use_llmlingua2=True, device_map="cpu")


_lock = threading.Lock()
_memo: dict[str, CompressResult] = {}
_loaded: tuple[str, Any] | None = None  # (model, compressor or None after a failed load)
_load_reason: str | None = None
_load_attempts = 0
_factory: Callable[[str], Any] | None = None
_stats = {"memo_hits": 0, "memo_misses": 0, "fields": 0, "origin": 0, "compressed": 0}


def set_factory(factory: Callable[[str], Any] | None) -> None:
    global _factory
    _factory = factory


def forget_loaded_model() -> None:
    global _loaded
    _loaded = None


def load_attempts() -> int:
    return _load_attempts


def reset() -> None:
    global _loaded, _load_reason, _load_attempts, _factory
    with _lock:
        _memo.clear()
        _loaded = None
        _load_reason = None
        _load_attempts = 0
        _factory = None
        for key in _stats:
            _stats[key] = 0


def _get_compressor(cfg: Config) -> Any | None:
    global _loaded, _load_reason, _load_attempts
    with _lock:
        if _loaded is not None and _loaded[0] == cfg.model:
            return _loaded[1]
        _load_attempts += 1
        try:
            compressor = (_factory or _default_factory)(cfg.model)
        except Exception as exc:  # ImportError, OSError from the model cache, anything else: cached as a failure
            _loaded = (cfg.model, None)
            _load_reason = f"model unavailable: {type(exc).__name__}: {exc}"
            return None
        _loaded = (cfg.model, compressor)
        _load_reason = None
        return compressor


def _reassemble(spans: list[tuple[str, str]], compressed_free: list[str]) -> str:
    out = []
    it = iter(compressed_free)
    for kind, text in spans:
        out.append(text if kind == "protected" else next(it))
    return "".join(out)


def _keeps_protected_in_order(output: str, protected: list[str]) -> bool:
    pos = 0
    for span in protected:
        idx = output.find(span, pos)
        if idx < 0:
            return False
        pos = idx + len(span)
    return True


def _compress_with(compressor: Any, text: str, rate: float) -> CompressResult:
    spans = split_protected(text)
    protected = [s for kind, s in spans if kind == "protected"]
    compressed_free: list[str] = []
    origin = compressed = 0
    for kind, s in spans:
        if kind == "protected":
            continue
        words = len(s.split())
        if not s.strip() or words < MIN_FREE_TOKENS:
            compressed_free.append(s)
            origin += words
            compressed += words
            continue
        out = compressor.compress_prompt(
            [s], rate=rate, force_tokens=FORCE, drop_consecutive=True, force_reserve_digit=True
        )
        compressed_free.append(out["compressed_prompt"])
        origin += int(out.get("origin_tokens", words))
        compressed += int(out.get("compressed_tokens", len(out["compressed_prompt"].split())))
    rebuilt = _reassemble(spans, compressed_free)
    if not _keeps_protected_in_order(rebuilt, protected):
        words = len(text.split())
        return CompressResult(text, words, words, False)  # discard the compressed text for this field
    return CompressResult(rebuilt, origin, compressed, False)


def compress_text(text: str) -> CompressResult:
    cfg = _config()
    if not cfg.enabled or cfg.error is not None:
        words = len(text.split())
        return CompressResult(text, words, words, False)
    key = hashlib.sha256(f"{text}\x00{cfg.model}\x00{cfg.rate!r}".encode("utf-8")).hexdigest()
    with _lock:
        hit = _memo.get(key)
        if hit is not None:
            _stats["memo_hits"] += 1
            _stats["fields"] += 1
            _stats["origin"] += hit.origin_tokens
            _stats["compressed"] += hit.compressed_tokens
            return CompressResult(hit.text, hit.origin_tokens, hit.compressed_tokens, memo_hit=True)
        _stats["memo_misses"] += 1
    compressor = _get_compressor(cfg)  # loads on first use only; the memo above never loads
    if compressor is None:
        words = len(text.split())
        return CompressResult(text, words, words, False)
    try:
        result = _compress_with(compressor, text, cfg.rate)
    except Exception as exc:
        global _load_reason
        _load_reason = f"compression failed: {type(exc).__name__}: {exc}"
        words = len(text.split())
        return CompressResult(text, words, words, False)
    if result.text != text:  # memoize only compressed output; the fallback is not memoized
        with _lock:
            _memo[key] = result
    with _lock:
        _stats["fields"] += 1
        _stats["origin"] += result.origin_tokens
        _stats["compressed"] += result.compressed_tokens
    return result


def make_transform() -> Callable[[str, str], str] | None:
    """The render transform: None when compression is off (identity), else compress_text."""
    if not _config().enabled:
        return None
    return lambda text, kind: compress_text(text).text


def status() -> dict[str, Any]:
    cfg = _config()
    if not cfg.enabled:
        state, reason = "off", None
    elif cfg.error is not None:
        state, reason = "unavailable", cfg.error
    elif _load_reason is not None:
        state, reason = "unavailable", _load_reason
    else:
        state, reason = "active", None
    return {
        "enabled": cfg.enabled,
        "state": state,
        "reason": reason,
        "model": cfg.model,
        "rate": cfg.rate,
        "fields_compressed": _stats["fields"],
        "memo_hits": _stats["memo_hits"],
        "memo_misses": _stats["memo_misses"],
        "origin_tokens_total": _stats["origin"],
        "compressed_tokens_total": _stats["compressed"],
    }
