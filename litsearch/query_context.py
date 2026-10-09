"""Translate the user's focus once, then use it for retrieval AND ranking."""

import re
from urllib.parse import urlencode

from litsearch.intent import _plain_plan, parse_intent
from litsearch.local_models import TRANSLATION_MODEL, TRANSLATION_REVISION, TRANSLATOR


def prepare_query(topic, direction, manual="", translator=None):
    original = (direction or topic).strip()
    if manual.strip():
        translated, engine = manual.strip(), "manual"
    else:
        translated = (translator or TRANSLATOR.translate)(original)
        engine = TRANSLATION_MODEL if translated != original else "identity"
    if not translated or re.search(r"[\u3400-\u9fff]", translated):
        raise ValueError("请填写有效的英文检索词")
    return {"original": original, "translated": translated, "engine": engine,
            "revision": TRANSLATION_REVISION if engine == TRANSLATION_MODEL else "",
            "topic": topic, "direction": direction}


def focused_plan(context, year_from, year_to):
    text = context["translated"]
    intent = parse_intent("", text)
    # Keep the complete focus. Do not allow shallow slot parsing to discard it.
    stop = {"the", "a", "an", "in", "of", "for", "and", "on", "to", "with", "field", "fields", "problem", "problems", "research", "study", "using"}
    words = [w for w in re.findall(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*", text) if w.lower() not in stop]
    flat = " ".join(words) or text
    queries = _plain_plan(flat, year_from, year_to, "按英文研究方向检索；原文、译文和实际查询均保存。")
    queries["arxiv"]["query"] = " AND ".join(f'(ti:{word} OR abs:{word})' for word in words)
    queries["openreview"] = {"query": flat, "note": "公开投稿论坛，包含未录用记录；年份涉及 2024 年以前时补查 API v1，分别记录实际覆盖。"}
    queries["google_scholar"] = {"query": flat, "note": "通过 SerpApi 检索谷歌学术，需配置 API Key；短片段不等于完整摘要。", "manual_url": "https://scholar.google.com/scholar?" + urlencode({"q": flat, "as_ylo": year_from, "as_yhi": year_to, "hl": "en"})}
    if re.search(r"multi[- ]agent", text, re.I) and "computer vision" in text.lower():
        queries["arxiv"]["query"] = '(ti:"multi-agent" OR abs:"multi-agent" OR ti:multiagent OR abs:multiagent) AND (ti:vision OR abs:vision OR ti:visual OR abs:visual OR ti:perception OR abs:perception)'
    return {"topic": context["topic"], "direction": context["direction"], "translation": context,
            "ranking_query": text, "language": "en", "keywords": words,
            "confidence": intent.confidence, "slots": intent.slot_dict(), "warnings": [],
            "year_from": year_from, "year_to": year_to, "degraded": False, "queries": queries}
