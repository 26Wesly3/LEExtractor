"""Run frozen SciFact ranking comparisons using public BEIR relevance labels.

Defaults to a reproducible 30-question pilot; --queries 0 uses all 300 test
questions. No provider credentials, LLM labels or training on test qrels.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import random
import sys
import time
import zipfile
from pathlib import Path

import numpy as np
import requests
from sklearn.feature_extraction.text import CountVectorizer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from litsearch.benchmark import (  # noqa: E402
    PERIPHERAL_RELEVANT,
    BenchmarkCase,
    BenchmarkDataset,
    LabelledPaper,
)
from litsearch.benchmark_reports import paired_bootstrap, write_exports  # noqa: E402
from litsearch.filters import RelevanceFilter  # noqa: E402
from litsearch.local_models import EMBEDDING_MODEL, EMBEDDING_REVISION, LocalEmbedding  # noqa: E402
from litsearch.models import Paper  # noqa: E402
from scripts.run_benchmark import build_results, report_payload  # noqa: E402

DATA_URL = "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/scifact.zip"
DATA_SHA256 = "536e14446a0ba56ed1398ab1055f39fe852686ecad24a6306c80c490fa8e0165"
SYSTEMS = ("bm25", "lexical", "minilm_title", "minilm_title_abstract", "current_hybrid")


def load_data(archive):
    archive = Path(archive)
    if not archive.exists():
        archive.parent.mkdir(parents=True, exist_ok=True)
        response = requests.get(DATA_URL, timeout=90)
        response.raise_for_status()
        content = response.content
        if hashlib.sha256(content).hexdigest() != DATA_SHA256:
            raise ValueError("SciFact download checksum differs from the frozen version")
        archive.write_bytes(content)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != DATA_SHA256:
        raise ValueError("SciFact archive checksum mismatch")
    with zipfile.ZipFile(archive) as zipped:
        corpus = [json.loads(line) for line in zipped.read("scifact/corpus.jsonl").decode().splitlines()]
        queries = {row["_id"]: row["text"] for row in
                   (json.loads(line) for line in zipped.read("scifact/queries.jsonl").decode().splitlines())}
        qrels = {}
        for row in csv.DictReader(io.StringIO(zipped.read("scifact/qrels/test.tsv").decode()), delimiter="\t"):
            if int(row["score"]) > 0:
                qrels.setdefault(row["query-id"], []).append(row["corpus-id"])
    corpus.sort(key=lambda row: row["_id"])
    return corpus, queries, qrels


class BM25:
    """Exact sparse Okapi BM25; fixed corpus IDF, k1=1.2, b=0.75."""

    def __init__(self, docs, k1=1.2, b=0.75):
        self.vectorizer = CountVectorizer(stop_words="english")
        matrix = self.vectorizer.fit_transform(docs).astype(float).tocsr()
        lengths = np.asarray(matrix.sum(axis=1)).ravel()
        df = np.asarray((matrix > 0).sum(axis=0)).ravel()
        idf = np.log(1 + (len(docs) - df + 0.5) / (df + 0.5))
        norm = k1 * (1 - b + b * lengths / max(lengths.mean(), 1e-12))
        rows = np.repeat(np.arange(len(docs)), np.diff(matrix.indptr))
        matrix.data = idf[matrix.indices] * matrix.data * (k1 + 1) / (matrix.data + norm[rows])
        self.matrix = matrix

    def scores(self, query):
        terms = self.vectorizer.transform([query])
        terms.data[:] = 1  # query-term presence, no repeated-term boost
        return (self.matrix @ terms.T).toarray().ravel()


def normalize(vectors):
    return vectors / np.maximum(np.linalg.norm(vectors, axis=-1, keepdims=True), 1e-12)


def document_vectors(model, papers):
    """Same 120-token recursive splitting and chunk mean as LocalEmbedding.scores."""
    texts, spans = [], []
    for paper in papers:
        start = len(texts)
        texts.extend(model._chunks(f"{paper.title}\n{paper.abstract or ''}"))
        spans.append((start, len(texts)))
    blocks = []
    for offset in range(0, len(texts), 256):
        blocks.append(model.vectors(texts[offset:offset + 256]))
        print(f"Encoded {min(offset + 256, len(texts))}/{len(texts)} chunks", flush=True)
    vectors = np.concatenate(blocks)
    return normalize(np.array([vectors[start:end].mean(axis=0) for start, end in spans]))


class FrozenScores:
    """Inject precomputed scores into the actual production hybrid filter."""

    def __init__(self, ids, values):
        self.values = dict(zip(ids, values, strict=True))

    def scores(self, papers, query):
        return [float(self.values[p.id]) for p in papers]


def ranked_ids(ids, scores, limit=50):
    # Corpus ID order breaks ties, never relevance labels.
    return [ids[index] for index in np.argsort(-np.asarray(scores), kind="stable")[:limit]]


def run(archive, output, count=30, seed=42):
    if count < 0:
        raise ValueError("queries must be zero (all) or positive")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    # Benchmark vector cache is separate from application/user project state.
    os.environ["LEEXTRACTOR_MODEL_DIR"] = str(output / "model-cache")
    corpus, queries, qrels = load_data(archive)
    all_queries = sorted(qrels)
    selected = sorted(random.Random(seed).sample(all_queries, min(count, len(all_queries)))) if count else all_queries
    papers = [Paper(id=row["_id"], title=row["title"], abstract=row["text"], source="scifact") for row in corpus]
    ids = [p.id for p in papers]
    pool_hash = hashlib.sha256("\n".join(ids).encode()).hexdigest()
    print(f"SciFact: {len(papers)} documents, {len(selected)}/{len(all_queries)} test queries", flush=True)
    setup_started = time.perf_counter()
    bm25 = BM25([RelevanceFilter._paper_to_text(p) for p in papers])
    model = LocalEmbedding()
    model.ensure_ready()
    print("Embedding title+abstract corpus on CPU...", flush=True)
    full_vectors = document_vectors(model, papers)
    print("Embedding title-only ablation...", flush=True)
    title_vectors = document_vectors(model, [Paper(id=p.id, title=p.title) for p in papers])
    setup_seconds = time.perf_counter() - setup_started
    cases, snapshot = [], {"candidate_ids": ids, "cases": {}}
    for number, query_id in enumerate(selected, 1):
        query = queries[query_id]
        cases.append(BenchmarkCase(case_id=query_id, domain="biomedical scientific claim retrieval", query=query,
                                   search_date="BEIR frozen test split", labelled_by="SciFact public qrels via BEIR",
                                   judgment_policy="unjudged_as_nonrelevant", papers=[
                                       LabelledPaper(pid, PERIPHERAL_RELEVANT, evidence_notes="BEIR positive qrel; binary relevance, no core/peripheral distinction")
                                       for pid in qrels[query_id]]))
        row = snapshot["cases"][query_id] = {}

        def record(system, ranking, seconds, row=row):
            row[system] = {"ranked_ids": ranking, "latency_seconds": seconds,
                           "notes": "offline ranking only; excludes shared corpus preparation and model initialization"}

        started = time.perf_counter()
        record("bm25", ranked_ids(ids, bm25.scores(query)), time.perf_counter() - started)
        started = time.perf_counter()
        lexical = RelevanceFilter().compute_relevance(list(papers), query)
        record("lexical", [p.id for p in lexical[:50]], time.perf_counter() - started)
        started = time.perf_counter()
        q = normalize(model.vectors(model._chunks(query)).mean(axis=0))
        dense = np.clip(full_vectors @ q, 0, 1)
        record("minilm_title_abstract", ranked_ids(ids, dense), time.perf_counter() - started)
        started = time.perf_counter()
        record("minilm_title", ranked_ids(ids, np.clip(title_vectors @ q, 0, 1)), time.perf_counter() - started)
        # Include the same shared query-vector preparation in both dense timings.
        row["minilm_title"]["latency_seconds"] += row["minilm_title_abstract"]["latency_seconds"]
        started = time.perf_counter()
        hybrid_filter = RelevanceFilter()
        hybrid_filter.embedding_model = FrozenScores(ids, dense)
        hybrid = hybrid_filter.compute_relevance(list(papers), query)
        record("current_hybrid", [p.id for p in hybrid[:50]], time.perf_counter() - started + row["minilm_title_abstract"]["latency_seconds"])
        print(f"Ranked {number}/{len(selected)} query {query_id}", flush=True)
        # Persist intermediate rankings so a failed long run is inspectable.
        (output / "systems.json").write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    dataset = BenchmarkDataset(name=f"BEIR SciFact test pilot ({len(selected)} queries)", frozen_on="BEIR scifact.zip sha256 pinned",
                               cases=cases, environment={"dataset_url": DATA_URL, "dataset_sha256": DATA_SHA256})
    (output / "dataset.json").write_text(json.dumps(dataset.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    results = build_results(dataset, snapshot, live=False, limit=50)
    payload = report_payload(dataset, results, live=False)
    payload["protocol"] = {"candidate_documents": len(ids), "pool_id_sha256": pool_hash,
                           "test_queries_total": len(all_queries), "evaluated_queries": selected,
                           "sample_seed": seed, "sampling": "sorted random.sample of test qrel query IDs; no relevance-dependent selection",
                           "systems": list(SYSTEMS), "embedding_model": EMBEDDING_MODEL, "embedding_revision": EMBEDDING_REVISION,
                           "pool_preparation_seconds": round(setup_seconds, 3), "hardware": "CPU, ONNX threads=4",
                           "bm25": "k1=1.2 b=0.75 sklearn English stopwords; title x3 + abstract",
                           "lexical": "actual RelevanceFilter word/char/coverage; query+corpus TF-IDF fit per question",
                           "hybrid": "actual RelevanceFilter 0.60 semantic + 0.15 lexical + 0.25 concept coverage",
                           "gain": "binary public qrels mapped to equal gain 2; equivalent binary nDCG",
                           "unjudged": "standard public-qrels evaluation: unjudged treated nonrelevant, not verified irrelevant",
                           "rank_cutoff": 50, "mrr_cutoff": 50, "training": "none; no parameter tuning on test qrels"}
    payload["paired_comparisons"] = [paired_bootstrap(payload["per_case"], "bm25", arm) for arm in SYSTEMS if arm != "bm25"]
    payload["paired_comparisons"].extend([
        paired_bootstrap(payload["per_case"], "lexical", "current_hybrid"),
        paired_bootstrap(payload["per_case"], "minilm_title", "minilm_title_abstract")])
    payload["warnings"].extend([
        "SciFact tests biomedical claim-to-abstract retrieval; it does not establish CCF conference coverage, Chinese translation quality or competitor superiority.",
        "This is a seeded subset pilot unless --queries 0 was used. P@20 is diluted because most questions have only one or a few positive qrels.",
        "Public qrels are sparse; unjudged_as_nonrelevant follows IR evaluation convention. Custom human topic benchmarks default to require_judged.",
        "MRR is truncated at the stored top 50. Shared setup cost is reported separately; timings are exploratory, unreplicated and do not include network retrieval.",
        "BM25 here is a local algorithm reference, not the Google Scholar/Semantic Scholar/Connected Papers product. No external product score has been measured.",
        "No citation edges are included in this corpus run, so snowball/BC/CC are not scored."])
    write_exports(payload, output / "report.json")
    print(json.dumps(payload["aggregate_by_system"], indent=2), flush=True)
    return payload


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", default="artifacts/benchmark-data/scifact.zip")
    parser.add_argument("--out", default="artifacts/benchmark-scifact")
    parser.add_argument("--queries", type=int, default=30, help="0 evaluates all 300 test questions")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)
    run(args.archive, args.out, args.queries, args.seed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
