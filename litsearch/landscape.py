"""Research Landscape: topics, temporal profile, novelty and coverage.

Everything here is computed from the papers already collected — no new network
calls. That makes the numbers cheap and reproducible, but it also bounds what
they can mean, so each result carries an explicit scope note:

* **Topics** are clusters of the current corpus (TF-IDF + K-means). They
  describe *what you found*, not what the field contains. A missing topic means
  "not retrieved", never "does not exist".
* **Temporal** trends are counts per year inside this sample. A rising line can
  be real growth or just a retrieval artefact.
* **Novelty** is *relative distinctiveness within this corpus*: how unlike a
  paper is compared with earlier work in the same sample. It is not peer
  judgement of originality, and it silently degrades when the sample is small.
* **Coverage** measures how evenly the corpus spans the discovered topics, so
  an over-concentrated result set is visible instead of being read as thorough.

K-means runs with a fixed seed so repeated calls on the same corpus are stable.
"""

from collections import Counter, defaultdict

from sklearn.cluster import KMeans
from sklearn.feature_extraction.text import TfidfVectorizer

from litsearch.filters import RelevanceFilter

# Below this many papers, clustering produces arbitrary partitions.
MIN_PAPERS_FOR_TOPICS = 6
# Cap on clusters: more topics than this is unreadable and over-fits small sets.
MAX_TOPICS = 10


def _paper_text(paper) -> str:
    """Title weighted by repetition, plus abstract — same trick as scoring."""
    parts = [paper.title or "", paper.title or ""]
    if paper.abstract:
        parts.append(paper.abstract)
    return " ".join(parts).strip()


def _term_counts(vectorizer, matrix, labels, n_clusters) -> dict[int, list[str]]:
    """Top terms per cluster, from the highest-weight centroid components."""
    terms = vectorizer.get_feature_names_out()
    out: dict[int, list[str]] = {}
    order = matrix.toarray()
    for cluster in range(n_clusters):
        members = [i for i, label in enumerate(labels) if label == cluster]
        if not members:
            out[cluster] = []
            continue
        mean = order[members].mean(axis=0)
        top = mean.argsort()[::-1][:8]
        out[cluster] = [terms[i] for i in top if mean[i] > 0]
    return out


def discover_topics(papers, n_clusters: int | None = None) -> dict:
    """Cluster the corpus into topics and label each with its top terms.

    Returns ``{"topics": [...], "n_clusters": int, "usable": bool, "note": str}``.
    ``usable`` is False when the corpus is too small to cluster meaningfully —
    in that case no topics are returned rather than a confident-looking guess.
    """
    papers = RelevanceFilter().deduplicate_by_doi(list(papers))
    docs = [_paper_text(p) for p in papers]
    usable_docs = [d for d in docs if d.strip()]
    if len(papers) < MIN_PAPERS_FOR_TOPICS or len(usable_docs) < MIN_PAPERS_FOR_TOPICS:
        return {
            "topics": [], "n_clusters": 0, "usable": False,
            "note": (
                f"文献少于 {MIN_PAPERS_FOR_TOPICS} 篇时聚类结果不可靠，因此不给出主题划分。"
                "Fewer than %d papers: topic clustering is not reliable." % MIN_PAPERS_FOR_TOPICS
            ),
        }

    k = n_clusters or min(MAX_TOPICS, max(2, len(papers) // 4))
    k = max(2, min(k, len(papers) - 1, MAX_TOPICS))
    try:
        vectorizer = TfidfVectorizer(max_features=6000, stop_words="english",
                                     ngram_range=(1, 2), sublinear_tf=True)
        matrix = vectorizer.fit_transform(docs)
        labels = KMeans(n_clusters=k, random_state=0, n_init=10).fit_predict(matrix)
    except ValueError:
        return {"topics": [], "n_clusters": 0, "usable": False,
                "note": "文本词汇不足以聚类。Not enough vocabulary to cluster."}

    term_map = _term_counts(vectorizer, matrix, labels, k)
    grouped: dict[int, list] = defaultdict(list)
    for paper, label in zip(papers, labels, strict=False):
        grouped[int(label)].append(paper)

    topics = []
    for cluster in sorted(grouped, key=lambda c: -len(grouped[c])):
        members = sorted(grouped[cluster], key=lambda p: -p.citation_count)
        years = [p.year for p in members if p.year]
        topics.append({
            "topic_id": f"T{cluster + 1}",
            "terms": term_map.get(cluster, [])[:6],
            "size": len(members),
            "median_year": sorted(years)[len(years) // 2] if years else None,
            "papers": [{"paper_id": p.canonical_id, "title": p.title,
                        "year": p.year, "citations": p.citation_count} for p in members[:8]],
        })
    return {
        "topics": topics, "n_clusters": k, "usable": True,
        "note": "主题来自当前证据集的文本聚类，只描述已检索到的文献。Topics describe retrieved papers, not the whole field.",
    }


def temporal_profile(papers, topics: dict | None = None) -> dict:
    """Yearly counts plus, when topics exist, which topics grew or faded.

    ``recent_share`` is the fraction published in the last three observed years;
    ``emerging`` / ``declining`` compare each topic's recent share against the
    corpus average. With sparse samples these are indicative only.
    """
    papers = RelevanceFilter().deduplicate_by_doi(list(papers))
    years = [p.year for p in papers if p.year]
    if not years:
        return {"timeline": {}, "recent_share": 0.0, "emerging": [], "declining": [],
                "note": "没有年份信息。No year metadata available."}

    timeline = dict(sorted(Counter(years).items()))
    latest = max(years)
    recent_cut = latest - 2
    recent = sum(1 for y in years if y >= recent_cut)
    recent_share = round(recent / len(years), 4)

    emerging, declining = [], []
    if topics and topics.get("usable"):
        topic_of: dict[str, str] = {}
        for topic in topics["topics"]:
            for row in topic["papers"]:
                topic_of[row["paper_id"]] = topic["topic_id"]
        per_topic: dict[str, list[int]] = defaultdict(list)
        for p in papers:
            if p.year and p.canonical_id in topic_of:
                per_topic[topic_of[p.canonical_id]].append(p.year)
        for topic_id, topic_years in sorted(per_topic.items()):
            if len(topic_years) < 3:
                continue
            share = sum(1 for y in topic_years if y >= recent_cut) / len(topic_years)
            delta = round(share - recent_share, 4)
            row = {"topic_id": topic_id, "size": len(topic_years), "recent_share": round(share, 4),
                   "delta": delta}
            if delta > 0.10:
                emerging.append(row)
            elif delta < -0.10:
                declining.append(row)
        emerging.sort(key=lambda r: -r["delta"])
        declining.sort(key=lambda r: r["delta"])

    return {
        "timeline": timeline, "recent_share": recent_share,
        "latest_year": latest, "emerging": emerging, "declining": declining,
        "note": "趋势只反映当前证据集的年度分布，不等于领域真实增长。Trends reflect this sample, not field-wide growth.",
    }


def novelty_scores(papers) -> dict:
    """How unlike each paper is, compared with *earlier* work in this corpus.

    For every paper, cosine similarity is computed against papers published in
    earlier years; novelty is ``1 - max(similarity)``. A paper with no earlier
    comparator is reported as ``None`` rather than being given a flattering 1.0.
    """
    papers = RelevanceFilter().deduplicate_by_doi(list(papers))
    dated = [p for p in papers if p.year]
    if len(dated) < 3:
        return {"rows": [], "note": "至少需要 3 篇带年份的文献。Needs at least 3 dated papers."}

    docs = [_paper_text(p) for p in dated]
    try:
        matrix = TfidfVectorizer(max_features=6000, stop_words="english",
                                 ngram_range=(1, 2), sublinear_tf=True).fit_transform(docs)
        sims = (matrix @ matrix.T).toarray()
    except ValueError:
        return {"rows": [], "note": "文本词汇不足以计算相似度。Not enough vocabulary."}

    rows = []
    for i, p in enumerate(dated):
        earlier = [j for j, q in enumerate(dated) if j != i and q.year < p.year]
        if not earlier:
            rows.append({"paper_id": p.canonical_id, "title": p.title, "year": p.year,
                         "novelty": None, "most_similar": None,
                         "note": "样本中没有更早的文献可比较。No earlier paper in sample."})
            continue
        best = max(earlier, key=lambda j: sims[i][j])
        rows.append({
            "paper_id": p.canonical_id, "title": p.title, "year": p.year,
            "novelty": round(float(1.0 - sims[i][best]), 4),
            "most_similar": {"paper_id": dated[best].canonical_id, "title": dated[best].title,
                             "similarity": round(float(sims[i][best]), 4)},
        })
    scored = [r for r in rows if r["novelty"] is not None]
    scored.sort(key=lambda r: -r["novelty"])
    return {
        "rows": scored + [r for r in rows if r["novelty"] is None],
        "note": "新颖度只表示在当前证据集内与更早文献的文本差异，不是同行评议的原创性评价。"
                "Relative textual distinctiveness within this sample, not peer review of originality.",
    }


def coverage_report(papers, topics: dict) -> dict:
    """How evenly the corpus spans its own discovered topics.

    Reports each topic's share plus a ``gaps`` list of thinly covered topics.
    Under-coverage here means "the search returned little on this", which is
    actionable; it does not mean the topic is unimportant in the field.
    """
    papers = RelevanceFilter().deduplicate_by_doi(list(papers))
    if not topics or not topics.get("usable"):
        return {"topics": [], "gaps": [], "balance": 0.0,
                "note": "没有可用主题划分。No usable topic partition."}

    total = sum(t["size"] for t in topics["topics"]) or 1
    rows = []
    for topic in topics["topics"]:
        share = round(topic["size"] / total, 4)
        rows.append({"topic_id": topic["topic_id"], "terms": topic["terms"],
                     "size": topic["size"], "share": share})
    shares = [r["share"] for r in rows] or [0.0]
    # balance = 1 - normalized spread; 1.0 means perfectly even coverage.
    spread = (max(shares) - min(shares)) if len(shares) > 1 else 1.0
    balance = round(1.0 - spread, 4)
    expected = 1.0 / len(rows) if rows else 0.0
    gaps = [r for r in rows if len(rows) > 1 and r["share"] < expected / 2]
    return {
        "topics": rows, "gaps": gaps, "balance": balance,
        "note": "覆盖度描述检索结果在各主题上的分布；占比低说明该方向检索不足，不代表领域的真实重要性。"
                "Coverage describes retrieved distribution, not field importance.",
    }


def build_landscape(papers, n_clusters: int | None = None) -> dict:
    """Run the full landscape analysis and return every section at once."""
    papers = RelevanceFilter().deduplicate_by_doi(list(papers))
    topics = discover_topics(papers, n_clusters=n_clusters)
    return {
        "paper_count": len(papers),
        "topics": topics,
        "temporal": temporal_profile(papers, topics),
        "novelty": novelty_scores(papers),
        "coverage": coverage_report(papers, topics),
        "scope": "computed from the current corpus only; no external field baseline",
    }
