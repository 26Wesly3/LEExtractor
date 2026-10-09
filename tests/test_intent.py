"""Offline tests for research-intent parsing and per-database query planning.

The parser is rule-based, so the tests care less about "did it find everything"
and more about the failure mode that matters: it must never emit a confident
looking term it cannot justify. A slot is either filled with something taken
verbatim from the input, or it is empty and listed in ``warnings``.
"""

import pytest

from litsearch.intent import (
    PLANNED_SOURCES,
    build_query_plan,
    parse_intent,
    plan_payload,
)


def test_chinese_slots_are_split_not_whole_sentence():
    intent = parse_intent("小麦表型", "使用深度学习预测小麦表型")
    assert intent.language == "zh"
    assert intent.method_terms == ["深度学习"]
    assert intent.task_terms == ["预测"]
    assert intent.object_terms == ["小麦表型"]
    # The regression this guards: the whole sentence used to land in every slot.
    assert "使用深度学习预测小麦表型" not in intent.object_terms
    assert "使用深度学习预测小麦表型" not in intent.method_terms


def test_chinese_object_is_not_swallowed_by_the_method_slot():
    intent = parse_intent("作物病害", "基于遥感影像的作物病害识别与监测")
    assert intent.method_terms == ["遥感影像"]
    assert intent.object_terms == ["作物病害"]
    assert intent.task_terms == ["识别", "监测"]


def test_english_method_is_a_phrase_not_a_letter():
    intent = parse_intent("wheat yield", "using convolutional networks to estimate wheat yield in field conditions")
    assert intent.method_terms == ["convolutional network"]
    assert intent.task_terms == ["estimate"]
    assert intent.object_terms == ["wheat yield"]
    assert "field conditions" in intent.scenario_terms
    # The regression this guards: method used to be "s" and task "estim".
    for term in intent.keywords:
        assert len(term) >= 3


def test_english_task_uses_the_lemma_not_a_stem():
    intent = parse_intent("yield", "deep learning for yield estimation using UAV imagery")
    assert intent.task_terms == ["estimate"]
    assert "uav imagery" in intent.method_terms
    assert intent.object_terms == ["yield"]


def test_introducer_phrase_stays_together():
    intent = parse_intent("biomass", "random forest for biomass estimation")
    assert "random forest" in intent.method_terms
    assert intent.task_terms == ["estimate"]


def test_vague_input_gets_no_invented_structure():
    intent = parse_intent("plant phenotyping", "plant phenotyping")
    assert intent.object_terms == ["plant phenotyping"]
    assert intent.method_terms == []
    assert intent.task_terms == []
    assert intent.confidence < 0.7
    assert any("method" in warning for warning in intent.warnings)


def test_empty_input_is_reported_not_guessed():
    intent = parse_intent("", "")
    assert intent.confidence == 0.0
    assert intent.keywords == []
    assert intent.warnings


def test_mixed_language_is_flagged():
    intent = parse_intent("wheat", "使用深度学习预测 wheat yield")
    assert intent.language == "mixed"
    assert any("混合" in warning for warning in intent.warnings)


def test_arxiv_expression_only_when_two_groups_survive():
    rich, plan = parse_intent("小麦表型", "使用深度学习预测小麦表型"), None
    plan = build_query_plan(rich, 2018, 2025)
    expression = plan["arxiv"]["query"]
    assert "abs:深度学习" in expression and "ti:小麦表型" in expression
    assert "ti:" not in plan["openalex"]["query"]

    thin = parse_intent("plant phenotyping", "plant phenotyping")
    plan = build_query_plan(thin, 2019, 2025)
    assert plan["arxiv"]["query"].startswith("all:(")
    assert "不足" in plan["arxiv"]["note"]


def test_plan_covers_every_planned_source_with_years():
    intent = parse_intent("小麦表型", "使用深度学习预测小麦表型")
    plan = build_query_plan(intent, 2018, 2025)
    assert set(plan) == set(PLANNED_SOURCES)
    assert plan["openalex"]["filter"] == "publication_year:2018-2025"
    assert "201801010000" in plan["arxiv"]["date_range"]
    for source in PLANNED_SOURCES:
        assert plan[source]["query"].strip()


def test_language_mismatch_keeps_the_search_keywords():
    """A Chinese research question must not rewrite English search keywords."""
    payload = plan_payload("wheat yield", "使用深度学习预测小麦表型", 2018, 2025)
    assert payload["degraded"] is True
    for source in PLANNED_SOURCES:
        query = payload["queries"][source]["query"]
        assert "wheat yield" in query
        assert "深度学习" not in query
    # The parse is still recorded, just not used to drive retrieval.
    assert payload["slots"]["method"] == ["深度学习"]


def test_matching_languages_are_not_degraded():
    payload = plan_payload("wheat yield", "using deep learning to estimate wheat yield", 2018, 2025)
    assert payload["degraded"] is False
    assert payload["queries"]["openalex"]["query"] != "wheat yield"


def test_confidence_stays_within_bounds():
    for topic, direction in (
        ("小麦表型", "使用深度学习预测小麦表型"),
        ("wheat", "using cnn to detect wheat disease in field conditions"),
        ("x", "x"),
        ("", ""),
    ):
        intent = parse_intent(topic, direction)
        assert 0.0 <= intent.confidence <= 0.95


def test_no_slot_ever_holds_a_single_character():
    for direction in (
        "using convolutional networks to estimate wheat yield in field conditions",
        "基于遥感影像的作物病害识别与监测",
        "a of the and",
    ):
        intent = parse_intent("topic", direction)
        for term in intent.keywords:
            assert len(term.strip()) >= 2


class _FakeSource:
    """Stands in for a provider: records the string it was actually given."""

    def __init__(self, name):
        self.name = name
        self.calls: list[str] = []

    def search_papers(self, query, limit=50, year_from=1900, year_to=None):
        self.calls.append(query)
        return []


def test_plan_reaches_each_provider(monkeypatch):
    """The seam that matters: per-provider strings must actually be sent."""
    from litsearch.sources import SourceManager

    manager = SourceManager.__new__(SourceManager)
    manager.set_search_providers(["semantic_scholar", "openalex", "arxiv"])
    manager.s2 = _FakeSource("semantic_scholar")
    manager.oa = _FakeSource("openalex")
    manager.arxiv = _FakeSource("arxiv")
    manager.cr = _FakeSource("crossref")

    intent = parse_intent("wheat yield", "using deep learning to estimate wheat yield")
    plan = build_query_plan(intent, 2018, 2025)
    manager.last_search_manifest = {}
    manager.search_all_sources("wheat yield", limit=8, year_from=2018, year_to=2025, query_plan=plan)

    assert manager.s2.calls == [plan["semantic_scholar"]["query"]]
    assert manager.oa.calls == [plan["openalex"]["query"]]
    assert manager.arxiv.calls == [plan["arxiv"]["query"]]
    assert manager.arxiv.calls[0].startswith("(abs:")
    assert manager.last_search_manifest["per_source_queries"]["arxiv"] == plan["arxiv"]["query"]
    # Without a plan every provider still gets the same string, as before.
    assert manager.last_search_manifest["query"] == "wheat yield"


def test_without_a_plan_behaviour_is_unchanged():
    from litsearch.sources import SourceManager

    manager = SourceManager.__new__(SourceManager)
    manager.set_search_providers(["semantic_scholar", "openalex", "arxiv"])
    manager.s2 = _FakeSource("semantic_scholar")
    manager.oa = _FakeSource("openalex")
    manager.arxiv = _FakeSource("arxiv")
    manager.cr = _FakeSource("crossref")

    manager.last_search_manifest = {}
    manager.search_all_sources("wheat yield", limit=8, year_from=2018, year_to=2025)
    for source in (manager.s2, manager.oa, manager.arxiv):
        assert source.calls == ["wheat yield"]
    assert manager.last_search_manifest["query_plan"] is None


@pytest.mark.parametrize("direction", ["使用深度学习预测小麦表型", "基于遥感影像的作物病害识别与监测"])
def test_unparsed_is_empty_when_everything_is_placed(direction):
    """Chinese inputs that the rules fully cover should leave no remainder."""
    assert parse_intent("作物", direction).unparsed == ""
