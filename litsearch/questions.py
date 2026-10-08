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
"""

from __future__ import annotations

from collections import Counter
from itertools import combinations

from sklearn.feature_extraction.text import TfidfVectorizer

from litsearch.filters import RelevanceFilter
from litsearch.identifiers import identifier_key, paper_aliases
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


def _in_corpus_citations(papers: list) -> Counter:
    """How many *other papers in this corpus* cite each paper."""
    aliases = {alias: paper.canonical_id for paper in papers for alias in paper_aliases(paper)}
    counts: Counter = Counter()
    for paper in papers:
        seen: set[str] = set()
        for reference in paper.reference_ids:
            target = (aliases.get(identifier_key(reference, paper.source))
                      or aliases.get(identifier_key(reference)))
            if target and target != paper.canonical_id and target not in seen:
                seen.add(target)
                counts[target] += 1
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
        })
    return questions


def _coverage_gaps(landscape: dict | None, limit: int = 2) -> list[dict]:
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
        })
    return questions


def _unfollowed_results(papers: list, landscape: dict | None, limit: int = 2) -> list[dict]:
    """Recent, distinctive papers that nothing else in the corpus builds on."""
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
    by_id = {p.canonical_id: p for p in papers}

    questions = []
    for row in rows:
        if len(questions) >= limit:
            break
        if row["novelty"] < NOVELTY_FLOOR or (row.get("year") or 0) < recent_cut:
            continue
        if citations.get(row["paper_id"], 0) > 0:
            continue
        nearest = row.get("most_similar") or {}
        nearest_paper = by_id.get(nearest.get("paper_id"))
        nearest_work = [{
            "paper_id": nearest.get("paper_id"),
            "title": nearest.get("title"),
            "year": nearest_paper.year if nearest_paper else None,
            "similarity": nearest.get("similarity"),
            "note": "样本内最相近的更早工作 / closest earlier work in sample",
        }] if nearest else []
        questions.append({
            "kind": "unfollowed_result",
            "question": (
                f"《{row['title'][:80]}》在证据集内与更早工作差异最大（{row['novelty']:.2f}），"
                f"但语料中没有其他论文引用它 —— 它的结论是否被后续工作验证或沿用？ "
                f"\"{row['title'][:80]}\" is the most distinctive recent paper here "
                f"({row['novelty']:.2f}) yet nothing in the corpus cites it: has it been taken up?"
            ),
            "rationale": (
                "高文本差异 + 近两年 + 语料内零被引，通常意味着该结果尚未进入后续工作。"
                "High textual distinctiveness, recent, and uncited inside the corpus."
            ),
            "supporting_papers": [{**_brief(by_id[row["paper_id"]]),
                                   "novelty": row["novelty"]}] if row["paper_id"] in by_id else [],
            "nearest_existing_work": nearest_work,
            "coverage_gap": "in-corpus citations: 0",
            "risks": (
                "语料不完整时会把「还没被收录」误判成「没人跟进」；被引数据依赖已观察到的参考文献。"
                "An incomplete corpus turns 'not collected yet' into 'not followed up'; "
                "citations rely on observed references."
            ),
            "suggested_next_search": row["title"][:120],
            "status": "hypothesis",
            "evidence_strength": "weak",
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
    questions += _coverage_gaps(landscape)
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
        "note": note or "以下均为待验证假设，不是创新性结论。All items are hypotheses, not novelty verdicts.",
    }
