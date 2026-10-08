"""Candidate research questions, grounded in the evidence actually collected.

The report is explicit that these are *not* claims of novelty: every question is
marked as a hypothesis, carries the papers that support it, names the nearest
existing work, and says what would make it a bad bet. A question the evidence
cannot support is not produced at all.

Three generators, all offline and all deliberately shallow:

* **coverage_gap** — a discovered topic holds far less of the corpus than the
  others. That usually means under-retrieval, and the honest question is
  "is this genuinely thin, or did my search miss it?".
* **combination_gap** — two concepts each appear in several papers, but almost
  never together. A classic cross-product gap; also a classic false positive
  when the two words simply never co-occur in prose.
* **unfollowed_result** — a recent paper is textually unlike earlier work in the
  corpus and no paper here cites it. It may be unexplored, or the corpus may
  just be incomplete.

Every item is labelled ``status: hypothesis`` because none of the three can be
settled from metadata alone.

Two honesty rules run through the whole module:

* **One citation-fact source.** Citation counts come from
  :func:`litsearch.evidence.citation_facts`, which is also what the evidence
  graph builds its edges from. If the graph draws "citer → cited", this module
  counts it; if this module counts zero, the graph has no such edge. A
  ``forward_citation`` discovery trace therefore suppresses the "nobody
  followed up" question exactly as a resolved reference does.
* **Missing evidence is not evidence of absence.** ``coverage_unknown`` and
  ``needs_verification`` are set from the observed coverage of the *citing
  side* of the corpus, and ``zero_citation_confirmed`` is only True when every
  paper's references were actually observed. "No observed citation" and
  "confirmed zero in-corpus citations" are different statements and are
  reported differently.

Every item carries ``computation_basis`` (the counts and thresholds behind it)
and ``suggested_next_search`` (the entry point for checking it against a real
database).
"""

from __future__ import annotations

from collections import Counter
from itertools import combinations

from sklearn.feature_extraction.text import TfidfVectorizer

from litsearch.evidence import citation_facts
from litsearch.filters import RelevanceFilter
from litsearch.landscape import MIN_PAPERS_FOR_TOPICS, _paper_text

# Same floor as topic discovery: below this, co-occurrence counts are noise.
MIN_PAPERS_FOR_QUESTIONS = MIN_PAPERS_FOR_TOPICS
MAX_QUESTIONS = 6
# A concept must appear in at least this many papers to be worth combining.
MIN_TERM_SUPPORT = 3
# How many high-weight terms are considered when looking for missing pairs.
TERM_POOL = 40
# Distinctiveness a paper needs before calling its result "unfollowed".
NOVELTY_FLOOR = 0.75

_KINDS = ("combination_gap", "coverage_gap", "unfollowed_result")


def _brief(paper) -> dict:
    return {"paper_id": paper.canonical_id, "title": paper.title, "year": paper.year}


def _term_index(papers: list) -> dict[str, dict]:
    """Terms that appear in enough papers to reason about, with their papers."""
    docs = [_paper_text(p) for p in papers]
    try:
        vectorizer = TfidfVectorizer(max_features=4000, stop_words="english",
                                     ngram_range=(1, 2), sublinear_tf=True, min_df=2)
        matrix = vectorizer.fit_transform(docs)
    except ValueError:
        return {}
    terms = vectorizer.get_feature_names_out()
    dense = matrix.toarray()
    weights = dense.sum(axis=0)
    index: dict[str, dict] = {}
    for position, term in enumerate(terms):
        rows = [i for i in range(len(papers)) if dense[i][position] > 0]
        if len(rows) >= MIN_TERM_SUPPORT:
            index[term] = {"weight": float(weights[position]), "papers": rows}
    return index


def citation_coverage(papers: list) -> dict:
    """How much of the citing side of the corpus was actually observed.

    A paper's outgoing citations are *observed* when its reference list was
    collected (``reference_ids``) or when a ``backward_citation`` trace proves
    the reference side was fetched. ``reference_count`` above the number of
    collected references means the provider reported more references than we
    hold, which is also incomplete coverage.

    Anything else — the common case after a keyword-only search — is *not
    observed*: nothing can be said about whether the paper cites something.
    """
    papers = list(papers)
    unobserved = 0
    truncated = 0
    for paper in papers:
        observed = bool(paper.reference_ids) or any(
            trace.method == "backward_citation" for trace in paper.discovery_traces
        )
        if not observed:
            unobserved += 1
        elif paper.reference_count and paper.reference_count > len(paper.reference_ids):
            truncated += 1
    complete = bool(papers) and unobserved == 0 and truncated == 0
    return {
        "complete": complete,
        "papers_total": len(papers),
        "papers_with_observed_references": len(papers) - unobserved,
        "papers_without_reference_data": unobserved,
        "papers_truncated": truncated,
        "observed_citation_pairs": len(citation_facts(papers)),
        "note": (
            "所有论文的引文侧都已观察，语料内零被引可作为观测结论。"
            "Every reference list in the corpus was observed: a zero in-degree is a finding."
            if complete else
            f"{unobserved} 篇论文的参考文献未观察到、{truncated} 篇被截断，"
            "因此「无人引用」只能表述为未观察到，不能当作确认的零结果。"
            f"{unobserved} papers have no observed reference data and {truncated} are "
            "truncated: a zero in-degree is unobserved evidence, not a confirmed zero."
        ),
    }


def _in_corpus_citations(papers: list) -> Counter:
    """How many *other papers in this corpus* cite each paper.

    Counts :func:`litsearch.evidence.citation_facts` — the same observed
    citation facts the evidence graph builds its directed edges from — so the
    two can never disagree about whether a citation was seen.
    """
    papers = list(papers)
    in_corpus = {paper.canonical_id for paper in papers}
    counts: Counter = Counter()
    for citing, cited in citation_facts(papers):
        if cited in in_corpus and citing in in_corpus and cited != citing:
            counts[cited] += 1
    return counts


def _combination_gaps(papers: list, index: dict, intent_terms: set[str], limit: int = 2) -> list[dict]:
    """Concept pairs that are individually common but几乎没有同时出现."""
    ordered = sorted(index, key=lambda term: -index[term]["weight"])[:TERM_POOL]
    found: list[tuple[float, str, str, int, int, list[int]]] = []
    for left, right in combinations(ordered, 2):
        # "wheat" and "wheat yield" are one concept, not a combination.
        if left in right or right in left:
            continue
        rows_left = set(index[left]["papers"])
        rows_right = set(index[right]["papers"])
        overlap = sorted(rows_left & rows_right)
        if len(overlap) > 1:
            continue
        score = min(len(rows_left), len(rows_right)) * (
            index[left]["weight"] + index[right]["weight"]
        )
        if intent_terms & {left, right}:
            score *= 1.5
        found.append((score, left, right, len(rows_left), len(rows_right), overlap))
    found.sort(key=lambda row: -row[0])

    questions = []
    for _score, left, right, count_left, count_right, overlap in found[:limit]:
        left_papers = [papers[i] for i in index[left]["papers"][:2]]
        right_papers = [papers[i] for i in index[right]["papers"][:2]]
        questions.append({
            "kind": "combination_gap",
            "question": (
                f"「{left}」与「{right}」在证据集中分别有 {count_left} 篇和 {count_right} 篇文献，"
                f"但同时涉及两者的只有 {len(overlap)} 篇 —— 这个组合是尚未被研究，还是被检索式割裂开了？ "
                f"\"{left}\" appears in {count_left} papers and \"{right}\" in {count_right}, "
                f"but only {len(overlap)} covers both: unexplored combination, or a search artefact?"
            ),
            "rationale": (
                "两个概念各自有足够的文献支撑，却几乎没有交集，这是典型的组合空白信号。"
                "Both concepts are individually well supported yet barely co-occur."
            ),
            "supporting_papers": [_brief(p) for p in left_papers + right_papers],
            "nearest_existing_work": [
                {**_brief(p), "note": f"覆盖 {left} / covers {left}"} for p in left_papers
            ] + [
                {**_brief(p), "note": f"覆盖 {right} / covers {right}"} for p in right_papers
            ],
            "coverage_gap": f"{left} ∩ {right} = {len(overlap)} 篇 / papers",
            "risks": (
                "词形共现不等于概念关联：也可能是两种写法从不同时出现，或检索词把两个方向分开检索了。"
                "Co-occurrence is lexical: the terms may simply never appear together in prose, "
                "or the query split them into separate searches."
            ),
            "suggested_next_search": f"{left} {right}",
            "status": "hypothesis",
            "evidence_strength": "moderate" if min(count_left, count_right) >= 5 else "weak",
            # The claim is bounded by this corpus; every term pair in it was
            # enumerated, so no citation-coverage caveat applies.
            "coverage_unknown": False,
            "needs_verification": False,
            "computation_basis": (
                f"TF-IDF(n-gram 1-2) over {len(papers)} retrieved papers: "
                f"\"{left}\" in {count_left} papers, \"{right}\" in {count_right}, "
                f"co-occur in {len(overlap)} (≤1 is the gap condition; "
                f"term weights {index[left]['weight']:.2f} / {index[right]['weight']:.2f}). "
                "术语共现在当前语料内被完整枚举，但语料本身是检索结果。"
            ),
        })
    return questions


def _coverage_gaps(landscape: dict | None, papers: list, limit: int = 2) -> list[dict]:
    """Topics that hold much less of the corpus than the rest."""
    if not landscape:
        return []
    coverage = landscape.get("coverage") or {}
    topics = {row["topic_id"]: row for row in coverage.get("topics", [])}
    papers_by_topic = {topic["topic_id"]: topic.get("papers", [])
                       for topic in (landscape.get("topics") or {}).get("topics", [])}
    questions = []
    for gap in coverage.get("gaps", [])[:limit]:
        row = topics.get(gap["topic_id"], {})
        terms = ", ".join((row.get("terms") or [])[:3]) or gap["topic_id"]
        examples = papers_by_topic.get(gap["topic_id"], [])[:3]
        questions.append({
            "kind": "coverage_gap",
            "question": (
                f"「{terms}」只占当前证据集的 {gap['share']:.0%}（{gap['size']} 篇）—— "
                f"它确实是冷门方向，还是现有检索式没有覆盖到？ "
                f"\"{terms}\" holds only {gap['share']:.0%} of the corpus ({gap['size']} papers): "
                f"genuinely thin, or missed by the current search?"
            ),
            "rationale": (
                "该主题占比不到平均水平的二分之一；在样本内，低占比更常见的解释是检索不足。"
                "The topic holds less than half of an even share; under-retrieval is the "
                "usual explanation inside a sample."
            ),
            "supporting_papers": examples,
            "nearest_existing_work": examples,
            "coverage_gap": f"{gap['topic_id']} share {gap['share']:.0%}, size {gap['size']}",
            "risks": (
                "主题来自当前语料的文本聚类，术语可能只是词形相近；占比低说明检索不足，不代表领域不重要。"
                "Topics come from clustering this corpus; low share means under-retrieval, "
                "not unimportance."
            ),
            "suggested_next_search": terms,
            "status": "hypothesis",
            "evidence_strength": "weak" if gap["size"] <= 2 else "moderate",
            # The share is computed over every retrieved paper, so it is
            # complete *for this corpus* — the caveat is retrieval, not counting.
            "coverage_unknown": False,
            "needs_verification": False,
            "computation_basis": (
                f"clustering of {len(papers)} retrieved papers: cluster {gap['topic_id']} "
                f"holds {gap['size']} papers = {gap['share']:.0%} of the corpus, "
                f"below half of an even share; terms: {terms}. "
                "该占比是对已检索语料的完整统计，不代表领域占比。"
            ),
        })
    return questions


def _unfollowed_results(papers: list, landscape: dict | None, limit: int = 2) -> list[dict]:
    """Recent, distinctive papers that nothing else in the corpus builds on.

    An observed in-corpus citation suppresses the item outright: the graph has
    the edge, so "nobody followed up" is simply false. When no citation was
    observed, the wording and the ``zero_citation_confirmed`` flag depend on
    whether the citing side of the corpus was actually observed — an
    unobserved reference list is not a confirmed zero.
    """
    if not landscape:
        return []
    novelty = landscape.get("novelty") or {}
    rows = [row for row in novelty.get("rows", []) if row.get("novelty") is not None]
    if not rows:
        return []
    years = [p.year for p in papers if p.year]
    if not years:
        return []
    recent_cut = max(years) - 1
    citations = _in_corpus_citations(papers)
    coverage = citation_coverage(papers)
    by_id = {p.canonical_id: p for p in papers}

    questions = []
    for row in rows:
        if len(questions) >= limit:
            break
        if row["novelty"] < NOVELTY_FLOOR or (row.get("year") or 0) < recent_cut:
            continue
        observed = citations.get(row["paper_id"], 0)
        if observed > 0:
            # The evidence graph already has this edge; the premise is false.
            continue
        confirmed_zero = coverage["complete"]
        nearest = row.get("most_similar") or {}
        nearest_paper = by_id.get(nearest.get("paper_id"))
        nearest_work = [{
            "paper_id": nearest.get("paper_id"),
            "title": nearest.get("title"),
            "year": nearest_paper.year if nearest_paper else None,
            "similarity": nearest.get("similarity"),
            "note": "样本内最相近的更早工作 / closest earlier work in sample",
        }] if nearest else []
        citation_claim = (
            f"但在已观察到的引文中没有观察到其他论文引用它 —— 引文覆盖不完整，"
            f"这只是「尚未观察到」，不是已确认的零被引。 "
            f"\"{row['title'][:80]}\" is the most distinctive recent paper here "
            f"({row['novelty']:.2f}) with no observed in-corpus citation — "
            f"citation coverage is incomplete, so this is 'not observed yet', "
            f"not a confirmed zero: has it been taken up?"
            if not confirmed_zero else
            f"但语料中没有其他论文引用它 —— 它的结论是否被后续工作验证或沿用？ "
            f"\"{row['title'][:80]}\" is the most distinctive recent paper here "
            f"({row['novelty']:.2f}) yet nothing in the corpus cites it: has it been taken up?"
        )
        questions.append({
            "kind": "unfollowed_result",
            "question": (
                f"《{row['title'][:80]}》在证据集内与更早工作差异最大（{row['novelty']:.2f}），"
                + citation_claim
            ),
            "rationale": (
                "高文本差异 + 近两年 + 语料内零被引，通常意味着该结果尚未进入后续工作。"
                "High textual distinctiveness, recent, and uncited inside the corpus."
            ),
            "supporting_papers": [{**_brief(by_id[row["paper_id"]]),
                                   "novelty": row["novelty"]}] if row["paper_id"] in by_id else [],
            "nearest_existing_work": nearest_work,
            "coverage_gap": (
                f"in-corpus citations: {observed} observed"
                + ("" if confirmed_zero else " (citation coverage incomplete)")
            ),
            "risks": (
                "语料不完整时会把「还没被收录」误判成「没人跟进」；被引数据依赖已观察到的参考文献。"
                "An incomplete corpus turns 'not collected yet' into 'not followed up'; "
                "citations rely on observed references."
            ),
            "suggested_next_search": row["title"][:120],
            "status": "hypothesis",
            "evidence_strength": "weak",
            # A missing reference list must never be read as zero citations.
            "coverage_unknown": not confirmed_zero,
            "needs_verification": not confirmed_zero,
            "zero_citation_confirmed": confirmed_zero,
            "citation_evidence": {
                "observed_in_corpus_citations": observed,
                "coverage": "complete" if confirmed_zero else "unknown",
                "papers_without_reference_data": coverage["papers_without_reference_data"],
                "papers_truncated": coverage["papers_truncated"],
                "basis": "reference_ids + forward/backward discovery traces "
                         "(litsearch.evidence.citation_facts)",
            },
            "computation_basis": (
                f"novelty {row['novelty']:.2f} ≥ {NOVELTY_FLOOR} (TF-IDF distance to earlier work "
                f"in this corpus), year {row.get('year')} ≥ {recent_cut}, observed in-corpus "
                f"citations {observed}; citation coverage "
                f"{'complete' if confirmed_zero else 'incomplete'} — "
                f"{coverage['papers_without_reference_data']} of {coverage['papers_total']} papers "
                f"have no observed reference data. "
                "该结论只基于已观察到的引文事实。"
            ),
        })
    return questions


def candidate_questions(
    papers: list,
    landscape: dict | None = None,
    intent: dict | None = None,
    max_questions: int = MAX_QUESTIONS,
) -> dict:
    """Generate candidate questions from the corpus, landscape and intent.

    Returns ``{"questions": [...], "usable": bool, "note": str, "counts": {...}}``.
    ``usable`` is False below ``MIN_PAPERS_FOR_QUESTIONS`` papers, because
    co-occurrence below that size is indistinguishable from chance.
    """
    papers = RelevanceFilter().deduplicate_by_doi(list(papers))
    if len(papers) < MIN_PAPERS_FOR_QUESTIONS:
        return {
            "questions": [], "usable": False, "counts": {},
            "citation_coverage": citation_coverage(papers),
            "note": (
                f"文献少于 {MIN_PAPERS_FOR_QUESTIONS} 篇时不生成候选问题：共现关系无法与偶然区分。"
                f"Fewer than {MIN_PAPERS_FOR_QUESTIONS} papers: co-occurrence is not "
                f"distinguishable from chance."
            ),
        }

    slots = (intent or {}).get("slots") or {}
    intent_terms: set[str] = set()
    for values in slots.values():
        for value in values or []:
            intent_terms.add(str(value).lower())

    index = _term_index(papers)
    questions: list[dict] = []
    questions += _coverage_gaps(landscape, papers)
    questions += _combination_gaps(papers, index, intent_terms)
    questions += _unfollowed_results(papers, landscape)

    # Keep the order stable and the three kinds interleaved by strength.
    order = {kind: position for position, kind in enumerate(_KINDS)}
    questions.sort(key=lambda item: (order.get(item["kind"], 9),
                                     0 if item["evidence_strength"] == "moderate" else 1))
    questions = questions[:max_questions]

    counts = dict(Counter(item["kind"] for item in questions))
    note = ""
    if not questions:
        note = (
            "当前证据集没有出现明显的组合空白、覆盖不足或无人跟进的结果。"
            "没有发现问题本身也是一个结果，但更可能说明检索范围还不够宽。"
            "No gaps surfaced: either the corpus is well covered, or it is too narrow to show gaps."
        )
    return {
        "questions": questions,
        "usable": True,
        "counts": counts,
        # Corpus-level citation coverage, so a consumer can tell "no observed
        # citation" from "confirmed zero citations" without re-deriving it.
        "citation_coverage": citation_coverage(papers),
        "note": note or "以下均为待验证假设，不是创新性结论。All items are hypotheses, not novelty verdicts.",
    }
