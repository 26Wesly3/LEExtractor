"""A Chinese research direction must be translated before it is searched or ranked.

Before this, the v3.2 pipeline sent the raw Chinese phrase to OpenAlex/Crossref and
then scored the returned English records against that Chinese phrase.  The lexical
topic share was therefore ~0 for every paper, which switches the strongest signal
off entirely, so the list came back in an arbitrary order — the retrieval
"worked" while the product was useless.  Both halves are pinned here.
"""

from __future__ import annotations

import pytest

from litsearch.v32 import pipeline
from litsearch.v32 import contracts


@pytest.fixture()
def english_translator(monkeypatch):
    """Stand in for the pinned Marian model so the test needs no model on disk."""
    calls: list[str] = []

    def fake_prepare(topic, direction, manual="", translator=None):
        calls.append(direction)
        return {
            "original": direction,
            "translated": "mint drought stress transcriptome secondary metabolism",
            "engine": "Xenova/opus-mt-zh-en",
            "revision": "test-revision",
            "topic": topic,
            "direction": direction,
        }

    monkeypatch.setattr("litsearch.query_context.prepare_query", fake_prepare)
    return calls


def test_a_cjk_direction_is_translated_for_retrieval(english_translator):
    info = pipeline.retrieval_text("薄荷干旱胁迫的转录组响应")
    assert info["translated"] is True
    assert info["original"] == "薄荷干旱胁迫的转录组响应"
    assert info["text"] == "mint drought stress transcriptome secondary metabolism"
    assert info["engine"] == "Xenova/opus-mt-zh-en"
    assert info["revision"] == "test-revision"
    assert english_translator == ["薄荷干旱胁迫的转录组响应"]
    # 原文必须保留，不被译文覆盖。
    assert "薄荷" in info["original"]


def test_an_english_direction_is_not_sent_through_the_translator(english_translator):
    info = pipeline.retrieval_text("mint drought stress")
    assert info["translated"] is False
    assert info["text"] == "mint drought stress"
    assert info["engine"] == "identity"
    assert english_translator == [], "an English direction must not be translated again"


def test_an_empty_direction_is_left_alone(english_translator):
    info = pipeline.retrieval_text("   ")
    assert info["text"] == ""
    assert info["translated"] is False
    assert english_translator == []


def test_an_unavailable_translator_degrades_honestly(monkeypatch):
    def explode(*_args, **_kwargs):
        raise RuntimeError("model missing")

    monkeypatch.setattr("litsearch.query_context.prepare_query", explode)
    info = pipeline.retrieval_text("薄荷干旱胁迫")
    assert info["translated"] is False
    assert info["text"] == "薄荷干旱胁迫", "the original is used when translation fails"
    assert info["engine"] == "unavailable", "the run must not claim it translated"
    assert info["error"] == "RuntimeError"


def test_a_translation_that_keeps_cjk_is_rejected(monkeypatch):
    monkeypatch.setattr(
        "litsearch.query_context.prepare_query",
        lambda *a, **k: {"translated": "薄荷 drought", "engine": "x", "revision": "r"},
    )
    info = pipeline.retrieval_text("薄荷干旱胁迫")
    assert info["translated"] is False, "half-translated text is not sent to the sources"
    assert info["text"] == "薄荷干旱胁迫"


def test_translated_variants_record_the_model_version(english_translator):
    info = pipeline.retrieval_text("薄荷干旱胁迫")
    variants = pipeline.query_variants(
        info["text"], source="translation", generator_version=info["revision"]
    )
    assert variants, "at least one query variant must be recorded"
    first = variants[0]
    assert first["source"] == "translation"
    assert first["generator_version"] == "test-revision"
    assert first["language"] == "en"
    # doc 08: query_variants record the rule and the model version.
    assert set(first) == {"text", "language", "source", "generator_version", "confirmed_terms"}


def test_ranking_prefers_the_translated_query_over_the_chinese_one():
    """The half that actually broke the ordering."""

    class Project:
        doc = {
            "anchor": {"original_text": "薄荷干旱胁迫"},
            "query_translation": {"translated": "mint drought stress", "engine": "e", "revision": "r"},
        }

    assert pipeline.ranking_query_text(Project()) == "mint drought stress"

    class Untranslated:
        doc = {"anchor": {"original_text": "薄荷干旱胁迫"}}

    assert pipeline.ranking_query_text(Untranslated()) == "薄荷干旱胁迫"


def test_scoring_english_records_with_the_chinese_phrase_is_why_it_was_empty():
    """Documents the defect the translation removes, so a regression is obvious."""
    records = [
        {"paper_id": "a" * 32, "title": "Drought stress responses in mint", "abstract": "transcriptome"},
        {"paper_id": "b" * 32, "title": "Japanese literary criticism", "abstract": "essay"},
    ]
    chinese = pipeline.topic_scores("薄荷干旱胁迫", records)
    english = pipeline.topic_scores("mint drought stress transcriptome", records)
    # Chinese against English text: essentially no shared tokens -> the signal is
    # flat, and a flat signal is switched off.
    assert len(set(chinese.values())) <= 1
    assert english["a" * 32] > english["b" * 32]
    assert english["a" * 32] > 0


def test_the_contract_still_budgets_three_query_variants():
    assert contracts.discovery()["query_variants"] == 3
    variants = pipeline.query_variants("mint drought stress transcriptome response metabolism")
    assert 1 <= len(variants) <= 3
