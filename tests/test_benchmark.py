"""Tests for the benchmark harness: format validation, metric maths, honesty rules.

The metrics are the part of a benchmark that is cheap to get subtly wrong and
expensive to notice — an inflated recall silently justifies a method that does
not work. These tests pin the arithmetic and, just as importantly, pin the
refusals: an unlabelled case must be skipped, not scored.
"""

import json

import pytest

from litsearch.benchmark import (
    BASELINES,
    CORE_RELEVANT,
    KEY_REVIEW,
    KNOWN_IRRELEVANT,
    LABELS,
    PERIPHERAL_RELEVANT,
    RELEVANT_LABELS,
    SEED,
    BenchmarkCase,
    BenchmarkDataset,
    CaseResult,
    LabelledPaper,
    UnlabelledCaseError,
    compare_systems,
    keyword_missed_recovery,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)

SAMPLE = "benchmarks/dataset.sample.json"


def labelled_case(**overrides) -> BenchmarkCase:
    base = {
        "case_id": "case-a",
        "domain": "plant phenotyping",
        "query": "deep learning for plant phenotyping",
        "search_date": "2026-10-08",
        "providers": ["openalex"],
        "labelled_by": "human-reviewer",
        "papers": [
            LabelledPaper("10.1/core1", CORE_RELEVANT),
            LabelledPaper("10.1/core2", CORE_RELEVANT),
            LabelledPaper("10.1/periph", PERIPHERAL_RELEVANT),
            LabelledPaper("10.1/review", KEY_REVIEW),
            LabelledPaper("10.1/off", KNOWN_IRRELEVANT),
            LabelledPaper("10.1/seed", SEED),
        ],
    }
    base.update(overrides)
    return BenchmarkCase(**base)


# ---------------------------------------------------------------------------
# Format
# ---------------------------------------------------------------------------


def test_unknown_label_is_rejected():
    with pytest.raises(ValueError, match="unknown label"):
        LabelledPaper("10.1/x", "quite_relevant")


def test_label_vocabulary_is_the_documented_one():
    assert set(LABELS) == {
        "core_relevant", "peripheral_relevant", "known_irrelevant", "key_review", "seed"
    }
    assert {"core_relevant", "peripheral_relevant", "key_review"} == RELEVANT_LABELS
    assert SEED not in RELEVANT_LABELS, "seeds must not inflate the recall denominator"


def test_missing_required_dataset_keys_are_reported():
    for payload in ({}, {"name": "x"}, {"name": "x", "frozen_on": "2026-10-08"}):
        with pytest.raises(ValueError):
            BenchmarkDataset.from_dict(payload)


def test_dataset_round_trips_through_json():
    dataset = BenchmarkDataset(name="d", frozen_on="2026-10-08", cases=[labelled_case()])
    again = BenchmarkDataset.from_dict(json.loads(json.dumps(dataset.to_dict())))
    assert again.to_dict() == dataset.to_dict()


def test_sample_dataset_loads_and_declares_its_synthetic_nature():
    dataset = BenchmarkDataset.load(SAMPLE)
    assert dataset.cases
    assert any("SYNTHETIC" in case.labelled_by.upper() for case in dataset.cases), (
        "the sample dataset must be unmistakably marked as synthetic"
    )
    assert dataset.label_gaps(), "the sample must include an unlabelled case to exercise skipping"


def test_domain_coverage_is_reported():
    dataset = BenchmarkDataset.load(SAMPLE)
    assert len(dataset.domain_coverage) >= 3


# ---------------------------------------------------------------------------
# Labelling rules
# ---------------------------------------------------------------------------


def test_a_case_needs_relevance_labels_to_count_as_labelled():
    no_labeller = labelled_case(labelled_by="")
    assert no_labeller.is_labelled is False

    only_seeds = labelled_case(
        papers=[LabelledPaper("10.1/seed", SEED)],
        labelled_by="human",
    )
    assert only_seeds.is_labelled is False, (
        "a seed-only case has no denominator and must not be scored"
    )

    assert labelled_case().is_labelled is True


def test_relevant_ids_exclude_seeds_and_irrelevant():
    case = labelled_case()
    assert case.relevant_ids == {"10.1/core1", "10.1/core2", "10.1/periph", "10.1/review"}
    assert "10.1/seed" not in case.relevant_ids
    assert "10.1/off" not in case.relevant_ids


def test_label_gaps_explain_every_unscorable_case():
    dataset = BenchmarkDataset(
        name="d", frozen_on="2026-10-08",
        cases=[labelled_case(), labelled_case(case_id="b", labelled_by="")],
    )
    gaps = dataset.label_gaps()
    assert len(gaps) == 1 and "b" in gaps[0]
    assert "labeller" in gaps[0]


# ---------------------------------------------------------------------------
# Metric arithmetic
# ---------------------------------------------------------------------------


def test_precision_at_k_divides_by_k_not_by_returned_count():
    """A short result list must be penalised, not silently normalised."""
    assert precision_at_k(["a", "b"], {"a"}, 2) == 0.5
    assert precision_at_k(["a"], {"a"}, 4) == 0.25


def test_recall_at_k_divides_by_the_labelled_relevant_set():
    relevant = {"a", "b", "c", "d"}
    assert recall_at_k(["a", "x", "b"], relevant, 3) == 0.5
    assert recall_at_k(["a", "b", "c", "d"], relevant, 4) == 1.0
    assert recall_at_k(["a", "b", "c", "d"], relevant, 2) == 0.5


def test_recall_without_labels_raises_rather_than_inventing_zero():
    with pytest.raises(UnlabelledCaseError):
        recall_at_k(["a"], set(), 10)


def test_reciprocal_rank_uses_the_first_hit():
    assert reciprocal_rank(["x", "y", "a"], {"a"}) == pytest.approx(1 / 3)
    assert reciprocal_rank(["a", "y"], {"a"}) == 1.0
    assert reciprocal_rank(["x", "y"], {"a"}) == 0.0


def test_ndcg_rewards_putting_core_papers_first():
    case = labelled_case()
    good = ["10.1/core1", "10.1/core2", "10.1/off"]
    bad = ["10.1/off", "10.1/core2", "10.1/core1"]
    assert ndcg_at_k(good, case, 3) > ndcg_at_k(bad, case, 3)


def test_ndcg_of_the_ideal_order_is_one():
    case = labelled_case()
    ideal = ["10.1/core1", "10.1/core2", "10.1/periph", "10.1/review", "10.1/off"]
    assert ndcg_at_k(ideal, case, 5) == pytest.approx(1.0)


def test_ndcg_gives_reviews_and_peripheral_papers_equal_credit():
    from litsearch.benchmark import LABEL_GAIN
    assert LABEL_GAIN[CORE_RELEVANT] > LABEL_GAIN[KEY_REVIEW]
    assert LABEL_GAIN[KEY_REVIEW] == LABEL_GAIN[PERIPHERAL_RELEVANT]
    assert LABEL_GAIN[SEED] == 0.0
    assert LABEL_GAIN[KNOWN_IRRELEVANT] == 0.0


def test_keyword_missed_recovery_counts_only_newly_found_relevant_papers():
    case = labelled_case()
    lexical = {"10.1/core1"}
    expanded = ["10.1/core1", "10.1/core2", "10.1/off", "10.1/review"]
    recovered = keyword_missed_recovery(expanded, case, lexical, 10)
    assert set(recovered) == {"10.1/core2", "10.1/review"}


def test_metrics_reject_a_non_positive_cutoff():
    for function in (precision_at_k, recall_at_k):
        with pytest.raises(ValueError):
            function(["a"], {"a"}, 0)


# ---------------------------------------------------------------------------
# CaseResult / comparison
# ---------------------------------------------------------------------------


def test_case_result_reports_the_denominator_and_unlabelled_share():
    case = labelled_case()
    result = CaseResult(
        case_id="case-a", system="lexical",
        ranked_ids=["10.1/core1", "10.1/unlabelled", "10.1/off"],
    )
    metrics = result.metrics(case)
    assert metrics["recall_denominator"] == 4
    assert metrics["relevant_labelled"] == 4
    assert metrics["top_k_unlabelled"] == 1, (
        "a reader must be able to see how much of the top-k the denominator cannot judge"
    )


def test_unlabelled_case_is_skipped_not_scored():
    case = labelled_case(labelled_by="")
    result = CaseResult(case_id="case-a", system="lexical", ranked_ids=["10.1/core1"])
    with pytest.raises(UnlabelledCaseError):
        result.metrics(case)

    dataset = BenchmarkDataset(name="d", frozen_on="2026-10-08", cases=[case])
    comparison = compare_systems([result], dataset)
    assert comparison.per_case["case-a"]["lexical"].get("skipped")
    assert comparison.aggregate()["cases_scored"] == 0
    assert comparison.aggregate()["cases_skipped"] == 1


def test_aggregate_omits_denominators_that_cannot_be_averaged():
    case = labelled_case()
    dataset = BenchmarkDataset(name="d", frozen_on="2026-10-08", cases=[case])
    results = [
        CaseResult(case_id="case-a", system="lexical", ranked_ids=["10.1/core1"]),
        CaseResult(case_id="case-a", system="expanded",
                   ranked_ids=["10.1/core1", "10.1/core2"]),
    ]
    aggregate = compare_systems(results, dataset).aggregate()
    assert "recall_denominator" not in aggregate
    assert "relevant_labelled" not in aggregate
    assert aggregate["cases_scored"] == 1
    assert aggregate["recall@20"] == pytest.approx((0.25 + 0.5) / 2)


def test_comparison_states_whether_the_candidate_set_was_shared():
    case = labelled_case()
    dataset = BenchmarkDataset(name="d", frozen_on="2026-10-08", cases=[case])
    results = [CaseResult(case_id="case-a", system="lexical", ranked_ids=["10.1/core1"])]
    assert compare_systems(results, dataset, shared_candidate_set=True).shared_candidate_set is True
    assert compare_systems(results, dataset, shared_candidate_set=False).shared_candidate_set is False


def test_comparison_carries_label_coverage_and_gaps():
    dataset = BenchmarkDataset(
        name="d", frozen_on="2026-10-08",
        cases=[labelled_case(), labelled_case(case_id="b", labelled_by="")],
    )
    coverage = compare_systems([], dataset).label_coverage
    assert coverage["cases_total"] == 2
    assert coverage["cases_labelled"] == 1
    assert coverage["label_gaps"]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def test_runner_reports_missing_dataset_instead_of_inventing_one(tmp_path, capsys):
    from scripts.run_benchmark import main

    missing = tmp_path / "dataset.json"
    assert main(["--dataset", str(missing)]) == 2
    err = capsys.readouterr().err
    assert "human relevance" in err or "labels" in err


def test_runner_scores_the_sample_snapshot_and_warns_about_it():
    from pathlib import Path

    from scripts.run_benchmark import build_results, build_warnings, load_snapshot

    dataset = BenchmarkDataset.load(SAMPLE)
    snapshot = load_snapshot(Path("benchmarks/systems.json"))
    results = build_results(dataset, snapshot, live=False, limit=50)
    assert results
    warnings = " ".join(build_warnings(dataset, results, live=False))
    assert "SYNTHETIC" in warnings.upper(), "synthetic labels must be flagged in the report"
    assert "CANNOT" in warnings, "the shared-candidate-set limitation must be stated"
    assert "recorded snapshot" in warnings


def test_runner_declares_the_five_required_baselines():
    assert len(BASELINES) == 5
    assert "lexical_bc" in BASELINES and "lexical_cc" in BASELINES, (
        "BC and CC must stay separate arms"
    )
    assert "lexical_snowball_bc_cc" in BASELINES


def test_runner_reports_environment():
    from scripts.run_benchmark import environment_record

    record = environment_record()
    assert record["python"]
    assert record["generated_at"]
