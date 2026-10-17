"""Features merged from the LEExtractor 0.9.15 line into v3.2: offline tests only."""

import json

import numpy as np
import pytest

from litsearch.v32 import external_match, intent_ai, semantic
from litsearch.v32.ai_providers import create_adapter


class FakeEmbedder:
    VOCAB = ["transformer", "attention", "soil", "mint"]

    def vectors(self, texts):
        rows = []
        for text in texts:
            words = text.lower().replace(".", " ").split()
            rows.append([float(sum(w == term for w in words)) for term in self.VOCAB])
        return np.array(rows)


@pytest.fixture
def embedder_on(monkeypatch):
    monkeypatch.setattr(semantic, "model_reason", lambda: None)
    monkeypatch.setattr(semantic, "_embedder", lambda: FakeEmbedder())


@pytest.fixture
def embedder_off(monkeypatch):
    monkeypatch.setattr(semantic, "model_reason", lambda: "model_not_on_disk")


def test_semantic_scores_are_clipped_cosines():
    scores = semantic.semantic_topic_scores(np.array([1.0, 0.0]),
                                            {"a": np.array([1.0, 0.0]), "b": np.array([-1.0, 0.0])})
    assert scores == {"a": pytest.approx(1.0), "b": 0.0}


def test_no_vectors_when_model_is_missing(embedder_off):
    records = [{"paper_id": "p1", "title": "transformer attention"}]
    assert semantic.paper_and_query_vectors("transformer", records) is None


def test_query_and_papers_share_one_vector_space(embedder_on):
    records = [{"paper_id": "p1", "title": "transformer attention"},
               {"paper_id": "p2", "title": "soil mint"}]
    pair = semantic.paper_and_query_vectors("transformer", records)
    assert pair is not None
    query_vector, vectors = pair
    scores = semantic.semantic_topic_scores(query_vector, vectors)
    assert scores["p1"] > scores["p2"]


def test_embedder_failure_degrades_to_none(monkeypatch):
    class Broken:
        def vectors(self, texts):
            raise RuntimeError("boom")

    monkeypatch.setattr(semantic, "model_reason", lambda: None)
    monkeypatch.setattr(semantic, "_embedder", lambda: Broken())
    assert semantic.embed(["x"]) is None


GOOD = {
    "english_query": "mint drought stress transcriptome",
    "object": "Mentha haplocalyx",
    "task": "drought stress response",
    "methods": ["transcriptome sequencing"],
    "synonyms": ["Mentha"],
}


@pytest.fixture
def intent_on(monkeypatch):
    monkeypatch.setenv(intent_ai.ENV_SWITCH, "1")
    intent_ai._CACHE.clear()


class FakeAdapter:
    def __init__(self, reply):
        self.reply = reply
        self.requests = []

    def intent(self, request):
        self.requests.append(request)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def test_intent_reading_is_used_only_with_the_switch_and_valid_shape(intent_on):
    adapter = FakeAdapter(GOOD)
    reading = intent_ai.interpret("薄荷干旱胁迫", adapter)
    assert reading["english_query"] == GOOD["english_query"]
    assert "Mentha haplocalyx" in intent_ai.topic_text(reading)
    assert adapter.requests[0]["user_text"] == "薄荷干旱胁迫"  # only the direction text


def test_switch_off_sends_nothing(monkeypatch):
    monkeypatch.delenv(intent_ai.ENV_SWITCH, raising=False)
    adapter = FakeAdapter(GOOD)
    assert intent_ai.interpret("薄荷", adapter) is None
    assert adapter.requests == []


@pytest.mark.parametrize("bad", [
    {**GOOD, "extra": 1},
    {**GOOD, "english_query": "薄荷干旱"},
    {**GOOD, "object": "x" * 300},
    {**GOOD, "methods": "not a list"},
    ["not", "an", "object"],
])
def test_invalid_reading_falls_back_to_none(intent_on, bad):
    assert intent_ai.interpret("薄荷", FakeAdapter(bad)) is None


def test_provider_error_falls_back_to_none(intent_on):
    assert intent_ai.interpret("薄荷", FakeAdapter(RuntimeError("timeout"))) is None


def test_model_adapter_intent_call_sends_its_own_prompt_and_parses_json():
    seen = {}

    def transport(url, headers, body, timeout):
        seen["body"] = body
        return {"choices": [{"message": {"content": json.dumps(GOOD)}}]}

    adapter = create_adapter("deepseek", "k", transport=transport)
    reply = adapter.intent({"system_prompt": intent_ai._SYSTEM, "user_text": "薄荷"})
    assert reply == GOOD
    serialised = json.dumps(seen["body"], ensure_ascii=False)
    assert "research direction" in serialised
    assert "薄荷" in serialised


def test_external_lookup_prefers_doi_then_title_and_maps_to_matcher_fields():
    calls = []

    def transport(url):
        calls.append(url)
        if "doi:" in url:
            return {"id": "https://openalex.org/W1", "display_name": "Mint paper",
                    "publication_year": 2024, "doi": "https://doi.org/10.1000/ABC",
                    "authorships": [{"author": {"display_name": "Zhang Wei"}}]}
        return {"results": []}

    class Parsed:
        doi = "10.1000/abc"
        title = "Mint paper"

    rows = external_match.lookup(Parsed(), transport)
    assert len(rows) == 1 and "doi:" in calls[0] and len(calls) == 1
    row = rows[0]
    assert row["candidate_id"] == row["paper_id"] == "10.1000/abc"
    assert row["title"] == "Mint paper" and row["year"] == 2024 and row["source"] == "openalex"


def test_external_lookup_failure_returns_no_candidates():
    def transport(url):
        raise TimeoutError("slow")

    class Parsed:
        doi = None
        title = "Mint paper"

    assert external_match.lookup(Parsed(), transport) == []


def test_external_matching_is_off_by_default(monkeypatch):
    monkeypatch.delenv(external_match.ENV_SWITCH, raising=False)
    assert external_match.switch_on() is False
