"""AI reading of the research intent before ranking (doc 02 / 10 / 17).

Opt-in: runs only when ``LEEXTRACTOR_AI_INTENT=1``, the job is in ``ai_enhanced``
mode and a model adapter is configured.  The reading is produced by its own
``intent`` job after the local snapshot is committed; ranking only reads the
cache (:func:`cached`).
Only the research direction text is sent (never PDFs, titles or judgements). The
reply must match a strict shape; anything else returns ``None`` and the caller keeps
the rule-based query. The model may organise the user's own words. It may not add
research objects, results or claims.
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Any

ENV_SWITCH = "LEEXTRACTOR_AI_INTENT"
MAX_TEXT_CHARS = 1000
MAX_TERMS = 8
MAX_TERM_CHARS = 80
MAX_FIELD_CHARS = 200

_SYSTEM = (
    "You rewrite a researcher's research direction into a search plan. "
    "Use ONLY information present in the user's text. Do not add research objects, "
    "results, claims or papers. Respond with ONE JSON object and nothing else, with keys: "
    '"english_query" (a concise English search query, string), '
    '"object" (string or ""), "task" (string or ""), "methods" (array of strings), '
    '"synonyms" (array of English synonyms of the query terms, may be empty).'
)

_CACHE: dict[str, dict[str, Any]] = {}


class IntentError(ValueError):
    pass


def switch_on() -> bool:
    return os.environ.get(ENV_SWITCH, "").strip() in {"1", "true", "yes", "on"}


def _text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        raise IntentError("field is not a string")
    text = re.sub(r"\s+", " ", value).strip()
    if len(text) > limit:
        raise IntentError("field too long")
    if re.search(r"[\x00-\x1f\x7f]", text):
        raise IntentError("control characters")
    return text


def _terms(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_TERMS:
        raise IntentError("list shape")
    out: list[str] = []
    for item in value:
        term = _text(item, MAX_TERM_CHARS)
        if term and term.casefold() not in {x.casefold() for x in out}:
            out.append(term)
    return out


def validate(raw: Any) -> dict[str, Any]:
    """Strict shape check. The English query must contain Latin words."""
    if not isinstance(raw, dict):
        raise IntentError("not an object")
    if set(raw) - {"english_query", "object", "task", "methods", "synonyms"}:
        raise IntentError("unexpected keys")
    query = _text(raw.get("english_query", ""), MAX_FIELD_CHARS)
    if not re.findall(r"[A-Za-z]{2,}", query):
        raise IntentError("english query has no latin words")
    return {
        "english_query": query,
        "object": _text(raw.get("object", ""), MAX_FIELD_CHARS),
        "task": _text(raw.get("task", ""), MAX_FIELD_CHARS),
        "methods": _terms(raw.get("methods")),
        "synonyms": _terms(raw.get("synonyms")),
    }


def topic_text(fields: dict[str, Any]) -> str:
    parts = [fields["english_query"], fields["object"], fields["task"], *fields["methods"]]
    return " ".join(part for part in parts if part)


def _key(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def cached(text: str) -> dict[str, Any] | None:
    """An already-produced reading of ``text``, or ``None``. Never calls a model.

    Local ranking uses only this (doc 17: the local snapshot never waits on a
    model); a new reading is produced by the separate ``intent`` job.
    """
    source = (text or "").strip()
    if not source or len(source) > MAX_TEXT_CHARS or not switch_on():
        return None
    return _CACHE.get(_key(source))


def interpret(text: str, adapter: Any) -> dict[str, Any] | None:
    """Validated reading of ``text``, or ``None``. Never raises; cached per text."""
    source = (text or "").strip()
    if not source or len(source) > MAX_TEXT_CHARS or adapter is None or not switch_on():
        return None
    key = _key(source)
    if key in _CACHE:
        return _CACHE[key]
    try:
        reply = adapter.intent({"system_prompt": _SYSTEM, "user_text": source})
        fields = validate(reply)
    except Exception:  # provider errors, malformed JSON, bad shape: fall back silently
        return None
    result = {**fields, "source_hash": key[:16]}
    _CACHE[key] = result
    return result
