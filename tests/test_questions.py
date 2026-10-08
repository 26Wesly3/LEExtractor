"""Offline tests for candidate research questions.

The failure mode to guard is not "too few questions" but "a confident question
the evidence does not support". So these tests check the gates as much as the
output: below the paper floor nothing is produced, every item is marked a
hypothesis, and every item names the papers behind it.
"""

import pytest

from litsearch.questions import (
    MIN_PAPERS_FOR_QUESTIONS,
    candidate_questions,
)


def _paper(title, abstract="", year=2020, citations=0, paper_id=None, references=()):
    from litsearch.models import Paper

    key = paper_id or title
    paper = Paper(id=f"10.1/{key}", doi=f"10.1/{key}", title=title,
                  abstract=abstract or title, year=year, citation_count=citations)
    paper.reference_ids = list(references)
    return paper


def _corpus():
    """Two concepts that never meet: remote sensing vs root architecture."""
    return [
        _paper("Remote sensing of wheat canopy", "satellite imagery canopy reflectance", 2019),
        _paper("Remote sensing of maize fields", "satellite imagery maize reflectance", 2020),
        _paper("Remote sensing of rice paddies", "satellite imagery rice reflectance", 2021),
        _paper("Root architecture under drought", "root traits drought tolerance", 2018),
        _paper("Root architecture of wheat", "root traits wheat seedlings", 2020),
        _paper("Root architecture and nitrogen", "root traits nitrogen uptake", 2022),
        _paper("Root architecture imaging", "root traits imaging method", 2023),
    ]


def test_below_the_paper_floor_nothing_is_produced():
    payload = candidate_questions(_corpus()[:3])
    assert payload["usable"] is False
    assert payload["questions"] == []


def test_combination_gap_is_found_and_carries_evidence():
    payload = candidate_questions(_corpus())
    assert payload["usable"] is True
    gaps = [q for q in payload["questions"] if q["kind"] == "combination_gap"]
    assert gaps, payload["questions"]
    gap = gaps[0]
    assert gap["status"] == "hypothesis"
    assert gap["supporting_papers"]
    assert gap["suggested_next_search"].strip()
    assert gap["risks"]


def test_corpus_without_gaps_stays_quiet():
    """Every paper shares the same vocabulary, so no combination is missing."""
    papers = [_paper(f"Wheat phenotyping study {i}", "wheat phenotyping deep learning imaging", 2019 + i)
              for i in range(8)]
    payload = candidate_questions(papers)
    gaps = [q for q in payload["questions"] if q["kind"] == "combination_gap"]
    assert gaps == []


def test_every_question_is_marked_a_hypothesis_and_has_the_required_fields():
    required = {"kind", "question", "rationale", "supporting_papers",
                "nearest_existing_work", "coverage_gap", "risks",
                "suggested_next_search", "status", "evidence_strength"}
    for item in candidate_questions(_corpus())["questions"]:
        assert required <= set(item)
        assert item["status"] == "hypothesis"


def test_unfollowed_result_needs_a_distinctive_recent_uncited_paper():
    papers = _corpus()
    # A recent paper on a topic nothing else covers, cited by nobody here.
    papers.append(_paper("Volatile sensors for greenhouse mint", "electronic nose mint volatiles", 2024))
    from litsearch.landscape import build_landscape

    payload = candidate_questions(papers, landscape=build_landscape(papers))
    kinds = {q["kind"] for q in payload["questions"]}
    assert "unfollowed_result" in kinds


def test_in_corpus_citation_suppresses_the_unfollowed_question():
    from litsearch.landscape import build_landscape

    papers = _corpus()
    novel = _paper("Volatile sensors for greenhouse mint", "electronic nose mint volatiles", 2024,
                   paper_id="novel")
    citer = _paper("Follow-up on volatile sensors", "electronic nose mint volatiles follow up", 2024,
                   paper_id="citer")
    citer.reference_ids = ["10.1/novel"]
    payload = candidate_questions(papers + [novel, citer], landscape=build_landscape(papers + [novel, citer]))
    unfollowed = [q for q in payload["questions"] if q["kind"] == "unfollowed_result"]
    assert [q for q in unfollowed if "Volatile sensors for greenhouse mint" in q["question"]] == []


def test_output_is_deterministic():
    first = candidate_questions(_corpus())["questions"]
    second = candidate_questions(_corpus())["questions"]
    assert [q["question"] for q in first] == [q["question"] for q in second]


def test_max_questions_is_respected():
    payload = candidate_questions(_corpus(), max_questions=1)
    assert len(payload["questions"]) <= 1


@pytest.mark.parametrize("size", [MIN_PAPERS_FOR_QUESTIONS - 1, MIN_PAPERS_FOR_QUESTIONS])
def test_floor_boundary(size):
    payload = candidate_questions(_corpus()[:size])
    assert payload["usable"] is (size >= MIN_PAPERS_FOR_QUESTIONS)
