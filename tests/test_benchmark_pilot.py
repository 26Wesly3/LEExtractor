import math
import sys
from pathlib import Path

import numpy as np
import pytest

# The pytest console entry point does not add the repository root to sys.path.
# Scripts are repository utilities, outside the installed litsearch package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from litsearch.benchmark import (
    CORE_RELEVANT,
    SEED,
    BenchmarkCase,
    BenchmarkDataset,
    CaseResult,
    LabelledPaper,
    compare_systems,
    precision_at_k,
    recall_at_k,
)
from litsearch.benchmark_reports import paired_bootstrap, write_exports
from litsearch.local_models import LocalEmbedding
from litsearch.models import Paper
from scripts.run_benchmark import build_results, report_payload, shared_candidates
from scripts.run_public_benchmark import BM25, document_vectors, normalize, ranked_ids


def dataset(policy="require_judged"):
    return BenchmarkDataset(name="test", frozen_on="2026-10-10", cases=[
        BenchmarkCase(case_id="q", domain="test", query="query", search_date="2026-10-10",
                      labelled_by="reviewer", judgment_policy=policy,
                      papers=[LabelledPaper("10.1234/a", CORE_RELEVANT), LabelledPaper("seed", SEED)])])


def test_duplicate_doi_aliases_cannot_inflate_recall_or_precision():
    ids = ["https://doi.org/10.1234/A", "10.1234/a"]
    assert recall_at_k(ids, {"10.1234/a"}, 20) == 1
    assert precision_at_k(ids, {"10.1234/a"}, 2) == 0.5


def test_unjudged_custom_topic_scores_are_null_not_irrelevant():
    row = CaseResult("q", "s", ranked_ids=["unknown", "10.1234/a"]).metrics(dataset().cases[0])
    assert row["precision@20"] is None and row["ndcg@20"] is None
    assert row["mrr"] is None
    assert row["recall@50"] == 1
    assert row["judged_fraction@20"] == 0.5


def test_public_qrels_policy_is_explicit_and_roundtrips():
    data = dataset("unjudged_as_nonrelevant")
    data = BenchmarkDataset.from_dict(data.to_dict())
    row = CaseResult("q", "s", ranked_ids=["unknown", "10.1234/a"]).metrics(data.cases[0])
    assert row["precision@20"] == 0.05
    assert row["ndcg@20"] == pytest.approx(1 / math.log2(3), abs=0.0001)


def test_seed_exclusion_and_system_macro_averages():
    data = dataset()
    comparison = compare_systems([
        CaseResult("q", "good", ranked_ids=["seed", "10.1234/a", "10.1234/a"]),
        CaseResult("q", "empty", ranked_ids=[]),
    ], data)
    report = comparison.to_dict()["aggregate_by_system"]
    assert report["good"]["ndcg@20"] == 1
    assert report["empty"]["ndcg@20"] == 0
    assert report["good"]["mrr"] == 1


def test_empty_snapshot_rankings_and_blocked_competitors_are_not_fake_results():
    data = dataset()
    snapshot = {"cases": {"q": {"empty": {"ranked_ids": []}, "scholar": {
        "ranked_ids": [], "status": "missing_credentials", "notes": "No SerpApi key"}}}}
    results = build_results(data, snapshot, False, 50)
    assert results[0].ranked_ids == []
    report = report_payload(data, results, False)
    assert report["per_case"]["q"]["scholar"]["status"] == "missing_credentials"
    assert report["aggregate_by_system"]["scholar"]["cases_scored"] == 0
    assert "ndcg@20" not in report["aggregate_by_system"]["scholar"]


def test_candidate_pool_must_be_known_and_equal():
    assert not shared_candidates([CaseResult("q", "s")])
    assert shared_candidates([CaseResult("q", "s", candidate_ids={"a"}), CaseResult("q", "t", candidate_ids={"a"})])
    assert not shared_candidates([CaseResult("q", "s", candidate_ids={"a"}), CaseResult("q", "t", candidate_ids={"b"})])


def test_duplicate_labels_and_runs_are_rejected():
    with pytest.raises(ValueError, match="duplicate labels"):
        BenchmarkCase("q", "d", "query", "date", papers=[LabelledPaper("10.1234/a", CORE_RELEVANT), LabelledPaper("https://doi.org/10.1234/a", CORE_RELEVANT)])
    with pytest.raises(ValueError, match="duplicate case/system"):
        compare_systems([CaseResult("q", "s"), CaseResult("q", "s")], dataset())


def test_bm25_matches_hand_computed_okapi_weights():
    model = BM25(["cat cat", "dog"])
    expected = math.log(2) * 2 * 2.2 / (2 + 1.2 * (0.25 + 0.75 * 2 / 1.5))
    assert model.scores("cat")[0] == pytest.approx(expected)
    assert model.scores("cat")[1] == 0
    assert model.scores("cat cat").tolist() == model.scores("cat").tolist()


def test_frozen_document_vectors_match_production_chunk_mean():
    model = LocalEmbedding()
    model._chunks = lambda text: [text[:3], text[3:]]
    model.vectors = lambda texts: np.array([[len(t) + 1, sum(map(ord, t)) % 11 + 1] for t in texts], dtype=np.float32)
    papers = [Paper("a", "Title", "Abstract"), Paper("b", "Other", "Summary")]
    frozen = document_vectors(model, papers)
    query = "text"
    q = normalize(model.vectors(model._chunks(query)).mean(axis=0))
    assert np.clip(frozen @ q, 0, 1).tolist() == pytest.approx(model.scores(papers, query))
    assert ranked_ids(["a", "b"], [0, 0]) == ["a", "b"]


def test_bootstrap_resamples_paired_questions_and_exports(tmp_path):
    per_case = {str(i): {"a": {"ndcg@20": 0.2}, "b": {"ndcg@20": 0.5}} for i in range(3)}
    stats = paired_bootstrap(per_case, "a", "b")
    assert stats["paired_cases"] == 3 and stats["ci95"] == [0.3, 0.3]
    per_case["none"] = {"a": {"ndcg@20": None}, "b": {"ndcg@20": 0.5}}
    assert paired_bootstrap(per_case, "a", "b")["paired_cases"] == 3
    payload = report_payload(dataset(), [CaseResult("q", "a", ranked_ids=["10.1234/a"])], False)
    write_exports(payload, tmp_path / "report.json")
    assert (tmp_path / "report.csv").read_text(encoding="utf-8-sig").startswith("system,")
    assert "a" in (tmp_path / "report.md").read_text(encoding="utf-8")


def test_public_runner_roundtrip_without_network_or_model_download(tmp_path, monkeypatch):
    import scripts.run_public_benchmark as runner

    corpus = [{"_id": "1", "title": "cat research", "text": "cat evidence"},
              {"_id": "2", "title": "dog research", "text": "dog evidence"}]
    monkeypatch.setattr(runner, "load_data", lambda path: (corpus, {"a": "cat", "b": "dog"}, {"a": ["1"], "b": ["2"]}))

    class Model:
        def ensure_ready(self):
            pass

        def _chunks(self, text):
            return [text]

        def vectors(self, texts):
            return np.array([[float("cat" in text), float("dog" in text)] for text in texts])

    monkeypatch.setattr(runner, "LocalEmbedding", Model)
    monkeypatch.setenv("LEEXTRACTOR_MODEL_DIR", str(tmp_path / "initial-cache"))
    report = runner.run("unused.zip", tmp_path / "run", count=0)
    assert report["shared_candidate_set"]
    assert set(report["systems"]) == set(runner.SYSTEMS)
    assert report["aggregate_by_system"]["current_hybrid"]["ndcg@20"] == 1
    assert report["protocol"]["evaluated_queries"] == ["a", "b"]
    replay = report_payload(BenchmarkDataset.load(tmp_path / "run/dataset.json"),
                            build_results(BenchmarkDataset.load(tmp_path / "run/dataset.json"),
                                          __import__("json").loads((tmp_path / "run/systems.json").read_text()), False, 50), False)
    assert replay["aggregate_by_system"] == report["aggregate_by_system"]


def test_annotation_import_refuses_blank_and_conflicting_labels(tmp_path):
    import json

    from scripts.import_benchmark_labels import main

    data_path = tmp_path / "dataset.json"
    data_path.write_text(json.dumps(dataset().to_dict()), encoding="utf-8")
    sheet = tmp_path / "labels.csv"
    sheet.write_text("case_id,paper_id,label\nq,new,\n", encoding="utf-8")
    args = ["--dataset", str(data_path), "--annotations", str(sheet), "--reviewer", "human", "--out", str(tmp_path / "labelled.json")]
    with pytest.raises(ValueError, match="no human labels"):
        main(args)
    sheet.write_text("case_id,paper_id,label\nq,10.1234/a,known_irrelevant\n", encoding="utf-8")
    with pytest.raises(ValueError, match="conflicting"):
        main(args)
