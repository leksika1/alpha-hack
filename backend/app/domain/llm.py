"""Pure rules of the LLM Gateway (ADR-028): masking, token/cost estimates, cache keys, budget day."""

import hashlib
import json
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

PREVIEW_CHARS = 500
CHARS_PER_TOKEN = 3.0  # Russian text with a BPE tokenizer: ~3 chars per token; errs on the expensive side

# Order matters: e-mails before phones/ids, long digit runs last.
MASKS: list[tuple[str, re.Pattern[str], str]] = [
    ("person_id", re.compile(r"\bF_\d+\b"), "[PERSON]"),
    ("email", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[EMAIL]"),
    ("phone", re.compile(r"(?<!\d)(?:\+7|8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}(?!\d)"), "[PHONE]"),
    # INN (10/12), OGRN (13/15), accounts (20): standalone digit runs; amounts are written with separators
    ("tax_id", re.compile(r"(?<!\d)(?<!\d[.,])\d{10,20}(?!\d|[.,]\d)"), "[ID]"),
]


@dataclass(frozen=True, slots=True)
class ModelPrice:
    rub_per_1m_in: float
    rub_per_1m_out: float


def mask_text(text: str, counts: Counter[str] | None = None) -> str:
    for name, pattern, repl in MASKS:
        text, n = pattern.subn(repl, text)
        if counts is not None and n:
            counts[name] += n
    return text


def _mask_content(content: Any, counts: Counter[str]) -> Any:
    if isinstance(content, str):
        return mask_text(content, counts)
    if isinstance(
        content, list
    ):  # OpenAI content parts: [{"type": "text", "text": ...}, {"type": "image_url"}]
        return [
            {**part, "text": mask_text(part["text"], counts)}
            if isinstance(part, dict) and isinstance(part.get("text"), str)
            else part
            for part in content
        ]
    return content


def mask_messages(messages: Sequence[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """PII never leaves the gateway: person ids, e-mails, phones and INN-like numbers are replaced."""
    counts: Counter[str] = Counter()
    masked = [{**m, "content": _mask_content(m.get("content"), counts)} for m in messages]
    return masked, dict(counts)


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(p["text"] for p in content if isinstance(p, dict) and isinstance(p.get("text"), str))
    return "" if content is None else json.dumps(content, ensure_ascii=False)


def estimate_tokens(text: str) -> int:
    return max(1, int(len(text) / CHARS_PER_TOKEN) + 1)


def estimate_input_tokens(messages: Sequence[dict[str, Any]], tools: Any = None) -> int:
    text = " ".join(_text_of(m.get("content")) for m in messages)
    if tools:
        text += json.dumps(tools, ensure_ascii=False)
    return estimate_tokens(text) + 4 * len(messages)  # per-message framing


def cost_rub(tokens_in: int, tokens_out: int, price: ModelPrice) -> float:
    return round(tokens_in * price.rub_per_1m_in / 1e6 + tokens_out * price.rub_per_1m_out / 1e6, 6)


CACHE_FIELDS = ("model", "messages", "temperature", "max_tokens", "tools", "tool_choice", "response_format")


def is_cacheable(request: dict[str, Any]) -> bool:
    """Only deterministic requests (temperature == 0) without streaming are served from the cache."""
    return request.get("temperature") == 0 and not request.get("stream")


def cache_key(request: dict[str, Any]) -> str:
    body = {k: request.get(k) for k in CACHE_FIELDS}
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def prompt_preview(messages: Sequence[dict[str, Any]], limit: int = PREVIEW_CHARS) -> str:
    """Masked preview for llm_usage: the last user message (that is what differs between calls)."""
    user = [m for m in messages if m.get("role") == "user"] or list(messages)
    return _text_of(user[-1].get("content") if user else "")[:limit]


def budget_day(now: datetime, utc_offset_h: int) -> date:
    return now.astimezone(timezone(timedelta(hours=utc_offset_h))).date()
