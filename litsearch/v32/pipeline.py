"""The v3.2 job pipeline: discovery, PDF import, rerank, expansion and Zotero.

Everything long-running lives here rather than in the request thread, because
doc 15 requires the first usable list to appear without waiting for a cold
semantic model or a model call: a request validates, enqueues, and answers
``202``; this module is what the queue then runs.

Three rules shape the implementation:

* **Retrieval is injectable.**  The one provider client is
  :class:`litsearch.sources.SourceManager` (doc 06 "不另建 provider 客户端");
  :class:`V32State` hands it out through :meth:`V32State.retrieval`, and a test
  substitutes its own object with the same three methods.  Nothing here opens a
  socket on import.
* **A failure is recorded, never laundered.**  A provider that raises becomes a
  name in ``coverage.failed_sources`` and a ``partial`` status; the candidates
  already saved stay saved (AC-10).  A missing capability is reported as a
  missing capability instead of being faked.
* **A stale computation never overwrites a newer one.**  A snapshot is committed
  with the ``context_version``/``corpus_version``/``config_hash`` the job started
  with, so :meth:`litsearch.v32.store.V32Store.commit_snapshot` can answer
  ``superseded`` (AC-09).

The teammate modules (``core_rank``, ``pdf_import``, ``ai_rerank``, ``mapping``,
``overview``, ``zotero``) are imported lazily inside the handler that needs
them, so an import-time failure in one of them can never take down the HTTP
surface or the legacy routes.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from litsearch.v32 import contracts, intent_ai, semantic
from litsearch.v32.errors import (
    BAD_REQUEST,
    CAPABILITY_UNAVAILABLE,
    NOT_FOUND,
    UNPROCESSABLE,
    V32Error,
)
from litsearch.v32.store import V32Project, V32Store, now
from litsearch.v32.types import (
    CoverageStatus,
    Eligibility,
    ImportStatus,
    JobStage,
    JudgementValue,
    iso_now,
    round_tie,
    safe_float,
    utc_now,
)

__all__ = [
    "V32State",
    "coverage_dto",
    "ranking_config",
    "config_hash",
    "query_variants",
    "topic_scores",
    "citation_edges",
    "rank_project",
    "build_snapshot",
    "register_handlers",
    "import_batch_dto",
    "overview_payload",
    "load_current_snapshot",
    "auto_expansion_seeds",
    "seed_external_id",
    "schedule_ai_rerank",
    "run_ai_rerank",
    "ai_window_ids",
    "retrieve_candidates",
    "retrieve_neighbours",
    "paper_record",
    "record_identity",
]

#: Which retrieval providers cannot work without a user credential.  This is a
#: fact about the providers, not a contract value: ``defaults.json`` names
#: ``discovery.provider_priority`` but says nothing about authentication.
_KEYED_PROVIDERS = frozenset({"google_scholar"})

#: Provider id -> the environment variable that carries its optional key.
_PROVIDER_KEY_ENV = {
    "semantic_scholar": "S2_API_KEY",
    "openalex": "OPENALEX_API_KEY",
    "google_scholar": "SERPAPI_API_KEY",
}

_TOKEN_RE = re.compile(r"[0-9a-z]+|[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


# ---------------------------------------------------------------------------
# the wired-up state
# ---------------------------------------------------------------------------


@dataclass
class V32State:
    """Everything the v3.2 HTTP surface needs, wired in one place.

    ``install_v32`` builds it and hangs it on ``app.state.v32``.  The optional
    attributes are the seams a test (or a future provider) injects through:
    ``source_manager``/``workflow_factory`` for retrieval, ``ai_adapter`` for the
    model stage, ``zotero_adapter`` for the Zotero stage and ``vectors`` for the
    semantic shares of the ranking.
    """

    store: V32Store
    jobs: Any
    sessions: Any
    credentials: Any
    data_dir: Path
    static_dir: Path | None = None
    source_manager: Any = None
    workflow_factory: Callable[[bool], Any] | None = None
    ai_adapter: Any = None
    ai_model_revision: str | None = None
    zotero_adapter: Any = None
    zotero_settings: Mapping[str, Any] | None = None
    #: ``paper_id -> unit vector``, when a semantic model is actually available.
    vectors: Mapping[str, Sequence[float]] | None = None
    #: ``(parsed) -> [candidate records]`` from an external metadata source (doc 16).
    #: Off unless configured; a failed lookup contributes no candidates.
    external_candidates: Any = None
    #: ``paper_id -> vector`` for the *query/intent* anchor, when available.
    query_vector: Sequence[float] | None = None
    previews: Any = None
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    # -- retrieval ---------------------------------------------------------

    def retrieval(self) -> Any:
        """The one provider client, or ``None`` when none can be built.

        ``SourceManager`` is constructed lazily and only here, so importing the
        application never opens a cache file, and a test that injects
        ``source_manager`` never touches the network.
        """
        if self.source_manager is not None:
            return self.source_manager
        with self.lock:
            if self.source_manager is not None:
                return self.source_manager
            if self.workflow_factory is None:
                from litsearch.cache import Cache
                from litsearch.sources import SourceManager

                self.source_manager = SourceManager(Cache(str(self.data_dir / "cache.db")))
            else:
                workflow = self.workflow_factory(False)
                self.source_manager = getattr(workflow, "sources", None)
            return self.source_manager

    def match_candidates(self, project: V32Project, parsed: Any) -> list[dict[str, Any]]:
        """Candidate records an uploaded PDF is matched against.

        The default is the project's own corpus: it is real, offline evidence.
        A deployment with a metadata provider injects a wider candidate set here
        instead of this module inventing a second provider client.
        """
        candidates = [dict(record) for record in project.doc.get("papers", {}).values()]
        if self.external_candidates is not None:
            candidates.extend(dict(row) for row in (self.external_candidates(parsed) or []))
        return candidates

    # -- optional stages ---------------------------------------------------

    def ai(self) -> Any:
        return self.ai_adapter

    def zotero(self) -> Any:
        with self.lock:
            if self.zotero_adapter is None and self.zotero_settings is not None:
                from litsearch.v32.zotero import create_adapter

                self.zotero_adapter = create_adapter(self.zotero_settings)
            return self.zotero_adapter

    def zotero_ready(self) -> bool:
        adapter = self.zotero()
        return bool(adapter is not None and getattr(adapter, "configured", False))


# ---------------------------------------------------------------------------
# small shared helpers
# ---------------------------------------------------------------------------


def coverage_dto(
    *,
    status: str,
    candidate_count: int,
    failed_sources: Iterable[str] = (),
    stop_reasons: Iterable[str] = (),
    scope: str = "current_retrieval_set",
) -> dict[str, Any]:
    """``$defs/Coverage`` with the enum and the counts kept honest."""
    valid = {str(value) for value in CoverageStatus}
    resolved = str(status)
    if resolved not in valid:
        resolved = "no_results" if not candidate_count else "partial"
    return {
        "status": resolved,
        "candidate_count": max(0, int(candidate_count)),
        "failed_sources": sorted({str(name) for name in failed_sources if str(name)}),
        "stop_reasons": sorted({str(reason) for reason in stop_reasons if str(reason)}),
        "scope": str(scope) or "current_retrieval_set",
    }


def ranking_config(as_of_year: int | None = None) -> dict[str, Any]:
    """The CoreRank configuration, read from the contract, never re-typed."""
    config = contracts.ranking()
    config["weights"] = contracts.weights()
    if as_of_year is not None:
        config["as_of_year"] = int(as_of_year)
    return config


def config_hash(as_of_year: int | None, model_revision: str | None) -> str:
    """A stable identity for the ranking configuration (``RankingSnapshot``)."""
    payload = {
        "document_version": contracts.document_version(),
        "algorithm_version": contracts.algorithm_version(),
        "ranking": ranking_config(as_of_year),
        "model_revision": model_revision,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def as_of_year() -> int:
    """The current UTC year: a real observation, never a fixture value."""
    return int(utc_now().year)


def query_variants(
    text: str,
    *,
    limit: int | None = None,
    source: str = "original",
    generator_version: str | None = None,
) -> list[dict[str, Any]]:
    """The query strings this run will actually send, as ``QueryVariant`` rows.

    Only rule-generated variants are produced (the submitted text plus a
    keyword-only form); nothing is invented, so the recorded list is a truthful
    account of the search.  When a research direction had to be translated, the
    caller passes ``source="translation"`` and the translation model's revision,
    because doc 08 requires ``query_variants`` to record the generation rule and
    the model version.
    """
    budget = int(contracts.discovery()["query_variants"] if limit is None else limit)
    original = str(text or "").strip()
    variants: list[dict[str, Any]] = []

    def _row(value: str, origin: str) -> dict[str, Any]:
        return {
            "text": value,
            "language": "zh" if _CJK_RE.search(value) else "en",
            "source": origin,
            "generator_version": generator_version or contracts.document_version(),
            "confirmed_terms": [],
        }

    if original:
        variants.append(_row(original, source))
    tokens = _tokens(original)
    if len(tokens) > 3:
        keyword_form = " ".join(dict.fromkeys(tokens))
        if keyword_form != original:
            variants.append(_row(keyword_form, "rule"))
    return variants[: max(1, budget)]


def retrieval_text(original: str) -> dict[str, Any]:
    """The text actually sent to the sources, plus how it was produced.

    A Chinese research direction cannot be sent to OpenAlex/Crossref/arXiv as-is:
    they answer with unrelated English records, and the lexical topic signal then
    scores ~0 for everything, so the list becomes effectively unordered.  The
    legacy pinned translator is reused here (``litsearch.query_context``); the
    ORIGINAL stays untouched in the anchor and the translation is recorded with
    its engine and model revision.  Translation is best effort: when it is
    unavailable the original text is used and the engine says so, rather than the
    run silently pretending to have translated.
    """
    text = str(original or "").strip()
    info: dict[str, Any] = {
        "original": text,
        "text": text,
        "engine": "identity",
        "revision": "",
        "translated": False,
    }
    if not text or not _CJK_RE.search(text):
        return info
    try:
        from litsearch.query_context import prepare_query

        context = prepare_query("", text)
        translated = str(context.get("translated") or "").strip()
        if translated and not _CJK_RE.search(translated):
            info.update(
                text=translated,
                engine=str(context.get("engine") or "translation"),
                revision=str(context.get("revision") or ""),
                translated=True,
            )
    except Exception as exc:  # noqa: BLE001 - translation is best effort, never fatal
        info["engine"] = "unavailable"
        info["error"] = type(exc).__name__
    return info


def _tokens(text: Any) -> list[str]:
    return _TOKEN_RE.findall(str(text or "").casefold())


def topic_scores(query: str, records: Iterable[Mapping[str, Any]]) -> dict[str, float]:
    """A lexical topic score per paper, in ``[0, 1]``.

    ``queries`` recall over the query's token set: the share of the request's
    tokens the paper's title+abstract actually contains.  Lexical evidence is
    real evidence, so it is used as the topic share and the shortfall is
    recorded in the snapshot; no semantic score is invented when no model is
    loaded (doc 04: a missing signal is ``null``, never a fabricated 0).
    """
    wanted = set(_tokens(query))
    if not wanted:
        return {}
    scores: dict[str, float] = {}
    for record in records:
        paper_id = str(record.get("paper_id") or "")
        if not paper_id:
            continue
        haystack = _tokens(f"{record.get('title') or ''} {record.get('abstract') or ''}")
        if not haystack:
            scores[paper_id] = 0.0
            continue
        overlap = wanted & set(haystack)
        scores[paper_id] = round_tie(len(overlap) / len(wanted)) or 0.0
    return scores


def ranking_query_text(project: V32Project) -> str:
    """The text the topic signal scores against: the translated query when there is one.

    Scoring an English corpus against a Chinese phrase gives every paper a topic
    share of roughly zero, which disables the strongest signal (a signal with
    fewer than two distinct values is switched off entirely) and leaves the list
    effectively unordered. The translation recorded at discovery time is reused
    here so retrieval and ranking see the same query.
    """
    translation = project.doc.get("query_translation") or {}
    translated = str(translation.get("translated") or "").strip()
    if translated:
        return translated
    return str((project.doc.get("anchor") or {}).get("original_text") or "").strip()


def citation_edges(store: V32Store, project: V32Project) -> list[dict[str, Any]]:
    """Observed citation edges (citing -> cited) from the project's records.

    Only an edge the record itself witnesses is produced: a reference id that
    resolves to a paper in this project.  Text similarity is never a citation
    edge (doc 17 rule 7).
    """
    edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for paper_id, record in sorted(project.doc.get("papers", {}).items()):
        for raw in record.get("reference_ids") or []:
            target = store.resolve_paper_id(project, str(raw))
            if not target or target == paper_id:
                continue
            key = (str(paper_id), str(target))
            if key in seen:
                continue
            seen.add(key)
            edges.append(
                {
                    "edge_id": str(uuid.uuid4().hex),
                    "from_paper_id": key[0],
                    "to_paper_id": key[1],
                    "relation": "citation",
                    "directed": True,
                    "weight": 1.0,
                    "witness_paper_ids": [key[0]],
                    "source_records": [],
                    "created_at": now(),
                }
            )
    return edges


# ---------------------------------------------------------------------------
# ranking
# ---------------------------------------------------------------------------


def rank_project(
    state: V32State,
    project: V32Project,
    *,
    year: int | None = None,
    config: Mapping[str, Any] | None = None,
) -> Any:
    """Rank the project's current candidate set with CoreRank.

    Every signal is fed from evidence that actually exists: the lexical topic
    share, the age-adjusted citation count, the observed citation-graph
    PageRank, the seed similarity and the feedback projection (the last two only
    when vectors are available).  A signal with no evidence stays ``None`` and
    CoreRank disables it — it is never silently replaced by 0.
    """
    from litsearch.v32.core_rank import CandidateSignals, age_adjusted_impact, core_rank
    from litsearch.v32.core_rank import pagerank_signal

    resolved_year = as_of_year() if year is None else int(year)
    cfg = dict(config or ranking_config(resolved_year))
    cfg.setdefault("as_of_year", resolved_year)

    documents = project.doc
    records = [dict(record) for record in documents.get("papers", {}).values()]
    records.sort(key=lambda row: str(row.get("paper_id")))
    paper_ids = [str(row["paper_id"]) for row in records]

    query_text = ranking_query_text(project)
    reading = intent_ai.interpret(query_text, state.ai_adapter) if state.ai_adapter is not None else None
    if reading:
        query_text = intent_ai.topic_text(reading)
    scores = topic_scores(query_text, records)
    pair = None
    if state.vectors is None or state.query_vector is None:
        pair = semantic.paper_and_query_vectors(query_text, records)
    if pair is not None:
        # Semantic topic when the pinned model is on disk; lexical share otherwise.
        scores = semantic.semantic_topic_scores(pair[0], pair[1])
    graph = pagerank_signal(citation_edges(state.store, project), paper_ids, config=cfg)

    seed_scores: dict[str, float] = {}
    feedback_values: dict[str, float] = {}
    vectors = state.vectors if state.vectors is not None else (pair[1] if pair is not None else None)
    anchor_vector = state.query_vector if state.query_vector is not None else (pair[0] if pair is not None else None)
    if vectors:
        vectors = {str(key): value for key, value in vectors.items()}
        seed_ids = [
            str(pid) for pid in (documents["anchor"].get("seed_paper_ids") or []) if str(pid) in vectors
        ]
        seed_scores = _max_cosine(vectors, seed_ids, paper_ids)
        try:
            from litsearch.v32.feedback import query_vector, feedback_scores

            anchor = anchor_vector
            if anchor is not None:
                q_new = query_vector(anchor, documents.get("judgements") or {}, vectors, config=cfg)
                feedback_values = {
                    str(pid): float(value)
                    for pid, value in feedback_scores(q_new, vectors).items()
                    if str(pid) in set(paper_ids)
                }
        except (ImportError, ValueError, TypeError, ZeroDivisionError):
            feedback_values = {}

    anchors = {
        str(pid)
        for pid in list(documents["anchor"].get("seed_paper_ids") or []) + list(documents.get("seeds") or [])
        if pid
    }
    excluded = {
        str(pid)
        for pid, judgement in (documents.get("judgements") or {}).items()
        if str(judgement.get("value")) == str(JudgementValue.EXCLUDED)
    }

    candidates = [
        CandidateSignals(
            paper_id=paper_id,
            topic=safe_float(scores.get(paper_id)),
            impact=age_adjusted_impact(
                record.get("citation_count"), record.get("year"), resolved_year
            ),
            graph=safe_float(graph.get(paper_id)),
            seed=safe_float(seed_scores.get(paper_id)),
            feedback=safe_float(feedback_values.get(paper_id)),
            excluded=paper_id in excluded,
            user_anchor=paper_id in anchors,
        )
        for paper_id, record in zip(paper_ids, records)
    ]
    return core_rank(
        candidates,
        judgements=documents.get("judgements") or {},
        seeds=sorted(anchors),
        as_of_year=resolved_year,
        config=cfg,
    )


def _max_cosine(
    vectors: Mapping[str, Sequence[float]], seed_ids: Sequence[str], paper_ids: Sequence[str]
) -> dict[str, float]:
    """max cosine similarity to any seed vector; a missing vector stays absent."""
    out: dict[str, float] = {}
    seeds = [vectors[pid] for pid in seed_ids if pid in vectors]
    if not seeds:
        return out
    for paper_id in paper_ids:
        vector = vectors.get(paper_id)
        if not vector:
            continue
        best = None
        for seed in seeds:
            value = _cosine(vector, seed)
            if value is None:
                continue
            best = value if best is None else max(best, value)
        if best is not None:
            out[str(paper_id)] = best
    return out


def _cosine(left: Sequence[float], right: Sequence[float]) -> float | None:
    if not left or not right or len(left) != len(right):
        return None
    dot = sum(float(a) * float(b) for a, b in zip(left, right))
    left_norm = sum(float(a) ** 2 for a in left) ** 0.5
    right_norm = sum(float(b) ** 2 for b in right) ** 0.5
    if left_norm <= 0 or right_norm <= 0:
        return None
    value = dot / (left_norm * right_norm)
    return value if value == value and abs(value) != float("inf") else None


def _sanitise_ranked(item: Mapping[str, Any]) -> dict[str, Any]:
    """A ``$defs/RankedPaper`` with its evidence filtered to the closed shape."""
    evidence: list[dict[str, Any]] = []
    for entry in item.get("recommendation_evidence") or []:
        if not isinstance(entry, Mapping):
            continue
        code = str(entry.get("code") or "").strip()
        text = entry.get("text")
        if not code or not isinstance(text, str):
            continue
        evidence.append(
            {
                "code": code[:128],
                "text": text,
                "paper_ids": [str(pid) for pid in (entry.get("paper_ids") or [])],
                "evidence_ids": [str(eid) for eid in (entry.get("evidence_ids") or [])],
            }
        )
    return {
        "paper_id": str(item["paper_id"]),
        "rank": int(item["rank"]),
        "core_score_raw": safe_float(item.get("core_score_raw")),
        "core_score": safe_float(item.get("core_score")),
        "relevance_score": safe_float(item.get("relevance_score")),
        "core_role": str(item.get("core_role") or "unknown"),
        "role_origin": str(item.get("role_origin") or "unknown"),
        "eligibility": str(item.get("eligibility") or Eligibility.EXPLORATION),
        "breakdown": [dict(row) for row in (item.get("breakdown") or [])],
        "recommendation_evidence": evidence,
    }


def build_snapshot(
    state: V32State,
    project: V32Project,
    ranking: Any,
    *,
    frozen: Mapping[str, Any],
    coverage: Mapping[str, Any] | None = None,
    ranking_stage: str | None = None,
) -> dict[str, Any]:
    """A ``$defs/RankingSnapshot`` for a completed ranking.

    Only schema-declared keys are written: diagnostics belong to the job result,
    not to the snapshot a user could export.
    """
    stage = str(ranking_stage or ranking.snapshot_fields.get("ranking_stage") or "fast")
    stages = set(contracts.domain_schema()["$defs"]["RankingSnapshot"]["properties"]["ranking_stage"]["enum"])
    if stage not in stages:
        stage = "fast"
    snapshot_coverage = coverage or ranking.coverage
    return {
        "snapshot_id": uuid.uuid4().hex,
        "parent_snapshot_id": project.doc.get("current_snapshot_id"),
        "project_id": project.project_id,
        "context_version": int(frozen["context_version"]),
        "corpus_version": int(frozen["corpus_version"]),
        "algorithm_version": contracts.algorithm_version(),
        "config_hash": str(frozen["config_hash"]),
        "model_revision": frozen.get("model_revision"),
        "as_of_date": datetime.now(timezone.utc).date().isoformat(),
        "ranking_stage": stage,
        "items": [_sanitise_ranked(item.to_dict()) for item in ranking.items],
        "starter_paper_ids": [str(pid) for pid in ranking.starter_paper_ids],
        "coverage": dict(snapshot_coverage),
    }


def _commit(state: V32State, project: V32Project, job: Any, snapshot: dict[str, Any]) -> str:
    frozen = _frozen_versions(job)
    return state.store.commit_snapshot(project, snapshot, frozen=frozen)


def _frozen_versions(job: Any) -> dict[str, Any]:
    """The versions the job started with — the supersede precondition (AC-09)."""
    return {
        "context_version": int(job.doc.get("context_version", 0)),
        "corpus_version": int(job.doc.get("corpus_version", 0)),
        "config_hash": job.doc.get("config_hash"),
        "model_revision": job.doc.get("model_revision"),
    }


# ---------------------------------------------------------------------------
# retrieval
# ---------------------------------------------------------------------------


@dataclass
class RetrievalOutcome:
    """What one retrieval round produced, including its honest coverage."""

    papers: list[dict[str, Any]] = field(default_factory=list)
    failed_sources: list[str] = field(default_factory=list)
    stop_reasons: list[str] = field(default_factory=list)
    raised: str | None = None
    #: True only when the run could not be attempted at all (no provider client).
    unavailable: bool = False
    #: ``provider -> {"returned", "kept", "cursor"}`` for the round log (doc 02).
    provider_stats: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: The monotonic deadline this round ran against (shared by the auto round).
    deadline: float | None = None
    #: Seeds actually sent, as ``{"paper_id", "external_id"}`` rows.
    used_seeds: list[dict[str, str]] = field(default_factory=list)
    #: Seeds that carry no external identifier a provider could accept.
    skipped_seeds: list[str] = field(default_factory=list)
    #: Candidates this round added that the project did not already hold.
    new_count: int = 0

    @property
    def status(self) -> str:
        """``within_budget`` / ``partial`` / ``no_results`` / ``failed`` (AC-10).

        A provider that *failed* makes the run incomplete, not broken: the
        candidates other providers returned are still evidence, so the status is
        ``partial`` and ``failed_sources`` names what went wrong.  ``failed`` is
        reserved for a run that could not be attempted at all.
        """
        if self.unavailable:
            return str(CoverageStatus.FAILED)
        if self.failed_sources or self.raised:
            return str(CoverageStatus.PARTIAL)
        if not self.papers:
            return str(CoverageStatus.NO_RESULTS)
        return str(CoverageStatus.WITHIN_BUDGET)


def paper_record(paper: Any) -> dict[str, Any]:
    """Map one provider record onto the store's paper shape (no invented fields).

    The provider's own canonical id is an **alias**, never the internal
    ``paper_id``: an external id such as ``10.1/a`` contains a slash and would
    be unusable as a path segment, while the store's contract is an opaque
    internal identity with every external identifier held as an alias
    (doc 14/AC-13).
    """
    canonical = getattr(paper, "canonical_id", None) or getattr(paper, "id", None)
    authors = []
    for author in getattr(paper, "authors", None) or []:
        name = getattr(author, "name", None) or (author if isinstance(author, str) else None)
        if name:
            authors.append(str(name))
    doi = getattr(paper, "doi", None)
    url = getattr(paper, "url", None)
    source = str(getattr(paper, "source", "") or "unknown")
    external_id = getattr(paper, "id", None)
    aliases = set()
    if canonical:
        aliases.add(str(canonical))
    if external_id:
        aliases.add(str(external_id))
    identifiers = getattr(paper, "identifiers", None)
    for key in ("doi", "openalex", "semantic_scholar", "arxiv", "crossref", "openreview", "dblp"):
        value = getattr(identifiers, key, None) if identifiers is not None else None
        if value:
            aliases.add(str(value))
    return {
        "paper_id": None,
        "title": str(getattr(paper, "title", "") or ""),
        "authors": authors,
        "year": getattr(paper, "year", None),
        "abstract": getattr(paper, "abstract", None),
        "doi": str(doi) if doi else None,
        "aliases": sorted(alias for alias in aliases if alias),
        "citation_count": getattr(paper, "citation_count", None),
        "venue": getattr(paper, "venue", None),
        "doc_type": getattr(paper, "publication_status", None),
        "landing_url": url if isinstance(url, str) and url.startswith("http") else None,
        "reference_ids": [str(rid) for rid in (getattr(paper, "reference_ids", None) or [])],
        "source_records": [
            {
                "provider": source[:128],
                "external_id": str(external_id) if external_id else None,
                "source_url": url if isinstance(url, str) and url.startswith("http") else None,
                "retrieved_at": now(),
            }
        ],
    }


def _result_papers(result: Any) -> list[Any]:
    papers = getattr(result, "papers", None)
    if papers is None and isinstance(result, (list, tuple)):
        return list(result)
    return list(papers or [])


def _result_failed(result: Any) -> bool:
    ok = getattr(result, "ok", None)
    if isinstance(ok, bool):
        return not ok
    status = str(getattr(result, "status", "") or "")
    return status not in {"success", "success_empty", "truncated"}


def record_identity(record: Mapping[str, Any]) -> str:
    """A stable de-duplication key for one retrieved record.

    Two providers describing the same work must collapse into one candidate, and
    two different works must never collapse: so the key is the strongest
    identifier the record actually carries, in order, and only when there is
    none at all does the title stand in.
    """
    doi = record.get("doi")
    if doi:
        return "doi:" + str(doi).strip().casefold()
    for alias in sorted(str(value) for value in (record.get("aliases") or []) if value):
        return "alias:" + alias.casefold()
    for row in record.get("source_records") or []:
        external = row.get("external_id") if isinstance(row, Mapping) else None
        if external:
            return f"external:{row.get('provider')}:{external}"
    title = re.sub(r"\s+", " ", str(record.get("title") or "")).strip().casefold()
    return "title:" + title


class _BudgetExceeded(Exception):
    """The round budget ran out before (or while) a provider call finished."""


def _accepts_keyword(call: Callable[..., Any], name: str) -> bool:
    try:
        parameters = inspect.signature(call).parameters
    except (TypeError, ValueError):
        return False
    return name in parameters


def _bounded_call(call: Callable[..., Any], args: Sequence[Any], kwargs: Mapping[str, Any], remaining: float) -> Any:
    """Run one provider call with at most ``remaining`` seconds of wall clock.

    The remaining round budget is handed to the call as ``timeout`` when it
    accepts one, and the round stops waiting when the budget is spent either
    way, so a stalled provider cannot hold the round past its 45 s (doc 02).
    """
    if remaining <= 0:
        raise _BudgetExceeded()
    arguments = dict(kwargs)
    if _accepts_keyword(call, "timeout"):
        arguments["timeout"] = remaining
    box: dict[str, Any] = {}

    def _target() -> None:
        try:
            box["value"] = call(*args, **arguments)
        except BaseException as exc:  # noqa: BLE001 - re-raised in the caller's thread
            box["error"] = exc

    worker = threading.Thread(target=_target, name="v32-provider-call", daemon=True)
    worker.start()
    worker.join(remaining)
    if worker.is_alive():
        raise _BudgetExceeded()
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _provider_order(names: Iterable[str]) -> list[str]:
    """``provider_priority`` first, then any other provider by name: a fixed order."""
    priority = [str(name) for name in contracts.discovery()["provider_priority"]]
    present = list(dict.fromkeys(str(name) for name in names))
    return [name for name in priority if name in present] + sorted(
        name for name in present if name not in priority
    )


def _round_robin(per_provider: Mapping[str, Sequence[Any]]) -> list[tuple[str, Any]]:
    """Interleave each provider's rows in the fixed provider order (doc 02 截断轮询)."""
    order = _provider_order(per_provider)
    out: list[tuple[str, Any]] = []
    depth = max((len(per_provider[name]) for name in order), default=0)
    for index in range(depth):
        for name in order:
            rows = per_provider[name]
            if index < len(rows):
                out.append((name, rows[index]))
    return out


def _note_provider(outcome: RetrievalOutcome, provider: str, result: Any, returned: int) -> None:
    stats = outcome.provider_stats.setdefault(provider, {"returned": 0, "kept": 0, "cursor": None})
    stats["returned"] += int(returned)
    cursor = getattr(result, "next_cursor", None)
    if cursor:
        stats["cursor"] = str(cursor)


def _select_round(
    outcome: RetrievalOutcome,
    per_provider: Mapping[str, Sequence[dict[str, Any]]],
    cap: int,
    *,
    known: Iterable[str] = (),
) -> None:
    """De-duplicate the interleaved rows and keep at most ``cap`` new candidates.

    Only candidates the project does not already hold count against the cap; a
    record of an already-known work is kept so it can still enrich that record.
    """
    existing = set(known)
    by_identity: dict[str, dict[str, Any]] = {}
    new_count = 0
    truncated = False
    for provider, record in _round_robin(per_provider):
        identity = record_identity(record)
        if not identity or identity in by_identity:
            continue
        is_new = identity not in existing
        if is_new and new_count >= cap:
            truncated = True
            continue
        by_identity[identity] = record
        outcome.provider_stats.setdefault(provider, {"returned": 0, "kept": 0, "cursor": None})["kept"] += 1
        if is_new:
            new_count += 1
    if truncated:
        outcome.stop_reasons.append("candidate_cap_reached")
    outcome.papers = list(by_identity.values())
    outcome.new_count = new_count


def retrieve_candidates(
    state: V32State, project: V32Project, job: Any, *, text: str, variants: Sequence[Mapping[str, Any]]
) -> RetrievalOutcome:
    """Search every configured provider once, recording each failure by name.

    The candidate cap, the round budget and the per-seed limits all come from
    ``contracts.discovery``.  The single provider client is asked once per round;
    a provider that raises is reported in ``failed_sources`` and never rolls back
    what other providers already returned.  Over the cap, the providers' rows are
    interleaved in ``provider_priority`` order before truncation (doc 02).
    """
    outcome = RetrievalOutcome()
    if not text.strip():
        outcome.stop_reasons.append("no_research_text")
        return outcome
    client = state.retrieval()
    if client is None:
        outcome.unavailable = True
        outcome.raised = "retrieval_unavailable"
        outcome.stop_reasons.append("no_retrieval_client")
        return outcome

    budget = contracts.discovery()
    limit = int(budget["candidate_cap"])
    deadline = time.monotonic() + float(budget["round_budget_seconds"])
    outcome.deadline = deadline
    # Each provider receives the variant chosen for it (the original text plus
    # any rule variants), which is what ``discovery.query_variants`` budgets.
    texts = [str(variant.get("text") or "") for variant in variants if str(variant.get("text") or "").strip()]
    providers = [str(name) for name in budget["provider_priority"]]
    query_plan = {
        provider: {"query": texts[index % len(texts)]} for index, provider in enumerate(providers)
    } if texts else None
    try:
        results = _bounded_call(
            client.search_all_sources_result,
            (text,),
            {"limit": limit, "query_plan": query_plan},
            deadline - time.monotonic(),
        )
    except _BudgetExceeded:
        outcome.raised = "round_budget_exceeded"
        outcome.failed_sources.append("timeout")
        outcome.stop_reasons.append("round_budget_exceeded")
        return outcome
    except Exception as exc:  # noqa: BLE001 - a provider failure is coverage, not a crash
        outcome.raised = type(exc).__name__
        outcome.failed_sources.append(type(exc).__name__)
        outcome.stop_reasons.append("provider_error")
        return outcome

    per_provider: dict[str, list[dict[str, Any]]] = {}
    for result in results or []:
        provider = str(getattr(result, "provider", "") or "unknown")
        if _result_failed(result):
            outcome.failed_sources.append(provider)
        papers = _result_papers(result)
        _note_provider(outcome, provider, result, len(papers))
        rows = per_provider.setdefault(provider, [])
        for paper in papers:
            record = paper_record(paper)
            if not record["title"] and not record["doi"]:
                continue
            rows.append(record)
    if time.monotonic() > deadline:
        outcome.stop_reasons.append("round_budget_exceeded")
    _select_round(outcome, per_provider, limit)
    return outcome


_S2_ID_RE = re.compile(r"^[0-9a-f]{40}$")
_OPENALEX_ID_RE = re.compile(r"^(?:https?://openalex\.org/|openalex:)?(W\d+)$", re.IGNORECASE)
_S2_PROVIDERS = frozenset({"semantic_scholar", "s2", "semanticscholar"})


def seed_external_id(project: V32Project, paper_id: str) -> str | None:
    """The best external identifier for an internal paper: DOI, then S2, then OpenAlex.

    The internal ``paper_id`` is an opaque uuid that no provider knows, so it is
    never sent; a seed without any external identifier yields ``None``.
    """
    record = (project.doc.get("papers") or {}).get(str(paper_id))
    if not record:
        return None
    aliases = [str(alias) for alias in (record.get("aliases") or []) if alias]
    sources = [row for row in (record.get("source_records") or []) if isinstance(row, Mapping)]
    doi = str(record.get("doi") or "").strip()
    if not doi:
        doi = next((alias for alias in aliases if alias.casefold().startswith("10.")), "")
    if doi:
        return doi
    for row in sources:
        if str(row.get("provider") or "").casefold() in _S2_PROVIDERS and row.get("external_id"):
            return str(row["external_id"])
    for alias in aliases:
        if alias.casefold().startswith("s2:"):
            return alias[3:]
        if _S2_ID_RE.match(alias):
            return alias
    for row in sources:
        if str(row.get("provider") or "").casefold() == "openalex" and row.get("external_id"):
            return str(row["external_id"])
    for alias in aliases:
        match = _OPENALEX_ID_RE.match(alias)
        if match:
            return match.group(1)
    return None


def retrieve_neighbours(
    state: V32State,
    project: V32Project,
    job: Any,
    *,
    seeds: Sequence[str],
    cap: int | None = None,
    deadline: float | None = None,
) -> RetrievalOutcome:
    """One snowball round over the given seeds: citations and references.

    Each seed is resolved to its record and sent by its external identifier
    (never the internal uuid).  At most ``cap`` new candidates are kept, the
    providers' rows interleaved in the fixed provider order, and every call gets
    only what is left of the round budget.
    """
    outcome = RetrievalOutcome()
    client = state.retrieval()
    if client is None:
        outcome.unavailable = True
        outcome.raised = "retrieval_unavailable"
        outcome.stop_reasons.append("no_retrieval_client")
        return outcome
    budget = contracts.discovery()
    per_seed = int(budget["citations_per_seed"])
    ref_limit = int(budget["references_per_seed"])
    remaining_cap = int(budget["candidate_cap"]) if cap is None else max(0, int(cap))
    if deadline is None:
        deadline = time.monotonic() + float(budget["round_budget_seconds"])
    outcome.deadline = deadline
    if remaining_cap <= 0:
        outcome.stop_reasons.append("candidate_cap_reached")
        return outcome
    per_provider: dict[str, list[dict[str, Any]]] = {}
    stopped = False
    for seed in seeds:
        if job.event.is_set():
            outcome.stop_reasons.append("cancel_requested")
            break
        if time.monotonic() > deadline:
            outcome.stop_reasons.append("round_budget_exceeded")
            break
        external = seed_external_id(project, str(seed))
        if not external:
            outcome.skipped_seeds.append(str(seed))
            continue
        outcome.used_seeds.append({"paper_id": str(seed), "external_id": external})
        for method, limit in (("get_citations_result", per_seed), ("get_references_result", ref_limit)):
            call = getattr(client, method, None)
            if call is None:
                outcome.failed_sources.append(f"missing:{method}")
                continue
            try:
                result = _bounded_call(
                    call, (external,), {"limit": min(limit, remaining_cap)}, deadline - time.monotonic()
                )
            except _BudgetExceeded:
                outcome.failed_sources.append("timeout")
                outcome.stop_reasons.append("round_budget_exceeded")
                stopped = True
                break
            except Exception as exc:  # noqa: BLE001
                outcome.failed_sources.append(type(exc).__name__)
                continue
            provider = str(getattr(result, "provider", "") or method)
            if _result_failed(result):
                outcome.failed_sources.append(provider)
            papers = _result_papers(result)
            _note_provider(outcome, provider, result, len(papers))
            rows = per_provider.setdefault(provider, [])
            for paper in papers:
                record = paper_record(paper)
                if not record["title"] and not record["doi"]:
                    continue
                rows.append(record)
        if stopped:
            break
    if outcome.skipped_seeds:
        outcome.stop_reasons.append("seed_without_external_id")
    known = {record_identity(record) for record in (project.doc.get("papers") or {}).values()}
    _select_round(outcome, per_provider, remaining_cap, known=known)
    if not outcome.new_count and not outcome.failed_sources:
        outcome.stop_reasons.append("no_new_results")
    return outcome


def _upsert_candidates(state: V32State, project: V32Project, papers: Iterable[Mapping[str, Any]]) -> list[str]:
    saved: list[str] = []
    for record in papers:
        saved.append(state.store.upsert_paper(project, dict(record)))
    return saved


def _seed_paper_ids(project: V32Project) -> list[str]:
    anchor = project.doc.get("anchor") or {}
    ids = [str(pid) for pid in (anchor.get("seed_paper_ids") or []) if pid]
    if ids:
        return ids
    ids = [str(pid) for pid in (project.doc.get("seeds") or []) if pid]
    if ids:
        return ids
    return []


def load_current_snapshot(
    state: V32State, project: V32Project, snapshot_id: str | None = None
) -> dict[str, Any] | None:
    """The named snapshot, or the project's current one, or ``None``."""
    resolved = snapshot_id or project.doc.get("current_snapshot_id")
    if not resolved:
        return None
    return state.store.load_snapshot(project, resolved)


# ---------------------------------------------------------------------------
# handlers
# ---------------------------------------------------------------------------


def run_discover(state: V32State, project: V32Project, job: Any) -> dict[str, Any]:
    """Retrieve, save candidates, rank locally, commit a snapshot."""
    state.jobs.set_stage(job, str(JobStage.RETRIEVING), progress=0.1)
    anchor = project.doc.get("anchor") or {}
    original_text = str(anchor.get("original_text") or "").strip()
    # 中文研究方向必须先译成英文再检索：直接把中文发给 OpenAlex/Crossref 只会
    # 拿回无关的英文记录，随后词法主题信号又近乎全 0，列表等于无序。原文保留在
    # anchor 里不被覆盖，译文与所用模型版本一并记录。
    translation = retrieval_text(original_text)
    text = translation["text"]
    variants = query_variants(
        text,
        source="translation" if translation["translated"] else "original",
        generator_version=translation["revision"] or None,
    )
    if translation["translated"]:
        # 派生检索文本，不属于用户意图，因此不改变 context_version；
        # 后续由判断触发的重排也要用它，否则重排会再次退回中文打分。
        project.doc["query_translation"] = {
            "original": translation["original"],
            "translated": translation["text"],
            "engine": translation["engine"],
            "revision": translation["revision"],
            "recorded_at": now(),
        }
        state.store.save(project)
    outcome = retrieve_candidates(state, project, job, text=text, variants=variants)

    saved = _upsert_candidates(state, project, outcome.papers)
    if saved:
        state.store.save(project, touch_corpus=True)
    # The job itself owns these candidate writes, so its corpus version is the
    # one it just produced; a foreign write makes the commit answer superseded.
    with job.lock:
        job.doc["corpus_version"] = project.corpus_version

    state.jobs.set_stage(job, str(JobStage.FAST_RANKING), progress=0.6)
    ranking = rank_project(state, project)
    coverage = coverage_dto(
        status=outcome.status,
        candidate_count=len(project.doc.get("papers", {})),
        failed_sources=outcome.failed_sources,
        stop_reasons=outcome.stop_reasons,
    )
    if outcome.unavailable:
        # Nothing could be attempted at all: report it and leave the previously
        # committed snapshot alone (a failed run must not overwrite evidence).
        return {
            "outcome": "failed",
            "coverage": coverage,
            "error_codes": ["retrieval_unavailable"],
        }
    snapshot = build_snapshot(state, project, ranking, frozen=_frozen_versions(job), coverage=coverage)
    status = _commit(state, project, job, snapshot)
    if status == "superseded":
        return {"superseded": True}

    provider_stats = {name: dict(stats) for name, stats in outcome.provider_stats.items()}
    auto = _auto_expansion_round(state, project, job, ranking, first=outcome, coverage=coverage)
    if auto.get("snapshot") is not None:
        snapshot = auto["snapshot"]
        ranking = auto["ranking"]
        coverage = auto["coverage"]
        saved = saved + list(auto["saved"])
    else:
        coverage = coverage_dto(
            status=coverage["status"],
            candidate_count=coverage["candidate_count"],
            failed_sources=coverage["failed_sources"],
            stop_reasons=list(coverage["stop_reasons"]) + list(auto["stop_reasons"]),
        )

    result = {
        "outcome": _outcome_for(coverage["status"]),
        "snapshot_id": snapshot["snapshot_id"],
        "coverage": coverage,
        "candidate_count": coverage["candidate_count"],
        "new_papers": len(saved),
        "recommended_count": len(ranking.recommended_paper_ids),
        "recommended_paper_ids": list(ranking.recommended_paper_ids),
        "starter_paper_ids": list(ranking.starter_paper_ids),
        "query_variants": [variant["text"] for variant in variants],
        "query_translation": project.doc.get("query_translation"),
        "ranking_query": ranking_query_text(project),
        "ranking_stage": snapshot["ranking_stage"],
        "provider_counts": {name: {"returned": s["returned"], "kept": s["kept"]} for name, s in provider_stats.items()},
        "provider_cursors": {name: s["cursor"] for name, s in provider_stats.items()},
        "auto_expansion": auto["report"],
        "error_codes": [] if coverage["status"] == str(CoverageStatus.WITHIN_BUDGET) else ["source_failure"],
    }
    mode = str(job.doc.get("mode") or "basic")
    if mode == "ai_enhanced":
        result["ai_status"] = "skipped"
        result["ai_detail"] = "本轮无可用模型适配器：本地排序已提交，未被改写。"
    return result


def _branch_of(state: V32State, project: V32Project, paper_ids: Sequence[str]) -> dict[str, str]:
    """``paper_id -> community id`` when the relation data supports a map, else ``{}``."""
    try:
        from litsearch.v32 import mapping as mapping_mod

        records = project.doc.get("papers", {})
        node_ids = sorted(str(pid) for pid in records)
        texts = {
            str(pid): f"{record.get('title') or ''} {record.get('abstract') or ''}"
            for pid, record in records.items()
        }
        community = mapping_mod.build_map(citation_edges(state.store, project), node_ids, texts=texts)
    except Exception:  # noqa: BLE001 - branch diversity is a preference, never a failure
        return {}
    if getattr(community, "degraded", False):
        return {}
    wanted = set(paper_ids)
    out: dict[str, str] = {}
    for entry in getattr(community, "communities", None) or []:
        for pid in entry.paper_ids:
            if str(pid) in wanted:
                out[str(pid)] = str(entry.community_id)
    return out


def auto_expansion_seeds(state: V32State, project: V32Project, ranking: Any) -> list[str]:
    """At most ``seeds_per_round`` recommended papers, preferring different branches.

    Only ``recommended`` items qualify: never an excluded one and never an
    exploration-only one.  When the relation data yields branches the seeds are
    taken round-robin across them; otherwise the starter list (an MMR diversity
    selection) leads, then the rest of the recommended order.  These are system
    choices and are never written into the user's anchor or seeds (doc 02).
    """
    per_round = int(contracts.discovery()["seeds_per_round"])
    excluded = {
        str(pid)
        for pid, judgement in (project.doc.get("judgements") or {}).items()
        if str(judgement.get("value")) == str(JudgementValue.EXCLUDED)
    }
    recommended = [
        str(item.paper_id)
        for item in ranking.items
        if str(item.eligibility) == str(Eligibility.RECOMMENDED) and str(item.paper_id) not in excluded
    ]
    if per_round <= 0 or not recommended:
        return []
    branches = _branch_of(state, project, recommended)
    if branches:
        groups: dict[str, list[str]] = {}
        for pid in recommended:
            groups.setdefault(branches.get(pid, f"unassigned:{pid}"), []).append(pid)
        ordered: list[str] = []
        depth = max(len(rows) for rows in groups.values())
        for index in range(depth):
            for rows in groups.values():
                if index < len(rows):
                    ordered.append(rows[index])
    else:
        wanted = set(recommended)
        starters = [str(pid) for pid in ranking.starter_paper_ids if str(pid) in wanted]
        ordered = list(dict.fromkeys(starters + recommended))
    return ordered[:per_round]


def _auto_expansion_round(
    state: V32State,
    project: V32Project,
    job: Any,
    ranking: Any,
    *,
    first: RetrievalOutcome,
    coverage: Mapping[str, Any],
) -> dict[str, Any]:
    """Exactly one automatic neighbour round after a committed discovery (AC-01/AC-32).

    It shares the discovery round's candidate cap and budget, commits its own
    snapshot (or reports why it did not), and never starts a second round.
    """
    report: dict[str, Any] = {"rounds": 0, "seeds": [], "skipped_seeds": [], "stop_reasons": []}
    out: dict[str, Any] = {"snapshot": None, "stop_reasons": [], "report": report, "saved": []}

    def _stop(*reasons: str) -> dict[str, Any]:
        report["stop_reasons"] = sorted(set(report["stop_reasons"]) | set(reasons))
        out["stop_reasons"] = list(report["stop_reasons"])
        return out

    discovery = contracts.discovery()
    if int(discovery["auto_rounds"]) < 1:
        return _stop("auto_rounds_disabled")
    if job.event.is_set():
        return _stop("cancel_requested")
    seeds = auto_expansion_seeds(state, project, ranking)
    if not seeds:
        return _stop("auto_expansion_no_seeds")

    state.jobs.set_stage(job, str(JobStage.RETRIEVING), progress=0.7)
    cap = int(discovery["candidate_cap"]) - len(first.papers)
    neighbours = retrieve_neighbours(
        state, project, job, seeds=seeds, cap=cap, deadline=first.deadline
    )
    report["rounds"] = 1
    report["seeds"] = [{**row, "origin": "system"} for row in neighbours.used_seeds]
    report["skipped_seeds"] = list(neighbours.skipped_seeds)
    report["provider_counts"] = {
        name: {"returned": s["returned"], "kept": s["kept"]} for name, s in neighbours.provider_stats.items()
    }
    report["provider_cursors"] = {name: s["cursor"] for name, s in neighbours.provider_stats.items()}
    report["new_papers"] = int(neighbours.new_count)
    _stop("auto_round_limit_reached", *neighbours.stop_reasons)

    saved = _upsert_candidates(state, project, neighbours.papers)
    if saved:
        state.store.save(project, touch_corpus=True)
    with job.lock:
        job.doc["corpus_version"] = project.corpus_version

    state.jobs.set_stage(job, str(JobStage.FAST_RANKING), progress=0.85)
    expanded = rank_project(state, project)
    statuses = {str(coverage["status"]), neighbours.status}
    if str(CoverageStatus.PARTIAL) in statuses:
        status = str(CoverageStatus.PARTIAL)
    else:
        status = str(coverage["status"])
    merged = coverage_dto(
        status=status,
        candidate_count=len(project.doc.get("papers", {})),
        failed_sources=list(coverage["failed_sources"]) + list(neighbours.failed_sources),
        stop_reasons=list(coverage["stop_reasons"]) + list(report["stop_reasons"]),
    )
    snapshot = build_snapshot(state, project, expanded, frozen=_frozen_versions(job), coverage=merged)
    if _commit(state, project, job, snapshot) == "superseded":
        # The first-round snapshot stands; the newer context owns what follows.
        return _stop("superseded")
    out.update(snapshot=snapshot, ranking=expanded, coverage=merged, saved=saved)
    return out


def _outcome_for(coverage_status: str) -> str:
    """A job outcome from a coverage status: incomplete is ``partial``.

    ``no_results`` is a *completed* run that found nothing, so it is a success
    with a ``no_results`` coverage — never a failure and never a fabricated
    result.
    """
    if coverage_status == str(CoverageStatus.FAILED):
        return "failed"
    if coverage_status == str(CoverageStatus.PARTIAL):
        return "partial"
    return "succeeded"


def run_expand(state: V32State, project: V32Project, job: Any) -> dict[str, Any]:
    """One bounded snowball round from the current seeds, then re-rank."""
    state.jobs.set_stage(job, str(JobStage.RETRIEVING), progress=0.1)
    seeds = _seed_paper_ids(project)
    if not seeds:
        snapshot = load_current_snapshot(state, project)
        if snapshot:
            seeds = [
                str(item["paper_id"])
                for item in snapshot.get("items", [])
                if str(item.get("eligibility")) == str(Eligibility.RECOMMENDED)
            ]
    per_round = int(contracts.discovery()["seeds_per_round"])
    resolved = [state.store.resolve_paper_id(project, pid) for pid in seeds]
    seeds = list(dict.fromkeys(str(pid) for pid in resolved if pid))[:per_round]
    if not seeds:
        outcome = RetrievalOutcome(stop_reasons=["no_seeds"])
    else:
        outcome = retrieve_neighbours(state, project, job, seeds=seeds)

    saved = _upsert_candidates(state, project, outcome.papers)
    if saved:
        state.store.save(project, touch_corpus=True)
    with job.lock:
        job.doc["corpus_version"] = project.corpus_version

    state.jobs.set_stage(job, str(JobStage.FAST_RANKING), progress=0.6)
    ranking = rank_project(state, project)
    coverage = coverage_dto(
        status=outcome.status,
        candidate_count=len(project.doc.get("papers", {})),
        failed_sources=outcome.failed_sources,
        stop_reasons=outcome.stop_reasons,
        scope="citation_expansion",
    )
    snapshot = build_snapshot(state, project, ranking, frozen=_frozen_versions(job), coverage=coverage)
    if _commit(state, project, job, snapshot) == "superseded":
        return {"superseded": True}
    result = {
        "outcome": _outcome_for(coverage["status"]),
        "snapshot_id": snapshot["snapshot_id"],
        "coverage": coverage,
        "seeds": seeds,
        "skipped_seeds": list(outcome.skipped_seeds),
        "provider_counts": {name: {"returned": s["returned"], "kept": s["kept"]} for name, s in outcome.provider_stats.items()},
        "provider_cursors": {name: s["cursor"] for name, s in outcome.provider_stats.items()},
        "new_papers": len(saved),
        "candidate_count": coverage["candidate_count"],
        "error_codes": [] if coverage["status"] == str(CoverageStatus.WITHIN_BUDGET) else ["source_failure"],
    }
    return result


def run_rerank(state: V32State, project: V32Project, job: Any) -> dict[str, Any]:
    """Recompute the local ranking and commit it; the AI pass is a separate job.

    Doc 17: the local snapshot is committed first and never waits on a model.
    When an adapter is configured, an ``ai_rerank`` job based on that snapshot
    (``parent_snapshot_id``) is queued behind ``jobs.ai_idle_delay_ms``.
    """
    state.jobs.set_stage(job, str(JobStage.FAST_RANKING), progress=0.2)
    ranking = rank_project(state, project)
    local_order = [str(item.paper_id) for item in ranking.items]

    coverage = coverage_dto(
        status=ranking.coverage.get("status") or str(CoverageStatus.WITHIN_BUDGET),
        candidate_count=int(ranking.coverage.get("candidate_count") or len(local_order)),
        failed_sources=ranking.coverage.get("failed_sources") or (),
        stop_reasons=ranking.coverage.get("stop_reasons") or (),
        scope="local_rerank",
    )
    snapshot = build_snapshot(state, project, ranking, frozen=_frozen_versions(job), coverage=coverage)
    if _commit(state, project, job, snapshot) == "superseded":
        return {"superseded": True}

    ai_status, ai_error, ai_job_id = "skipped", "ai_not_configured", None
    if state.ai() is not None and local_order:
        try:
            ai_job = schedule_ai_rerank(state, project, job, snapshot)
            ai_status, ai_error, ai_job_id = "queued", None, ai_job.job_id
        except V32Error as exc:
            ai_error = str(getattr(exc, "code", "") or "ai_queue_unavailable")
    result = {
        "outcome": "succeeded",
        "snapshot_id": snapshot["snapshot_id"],
        "coverage": coverage,
        "ranking_stage": snapshot["ranking_stage"],
        "ai_status": ai_status,
        "ai_error_code": ai_error,
        "ai_job_id": ai_job_id,
        "recommended_count": len(ranking.recommended_paper_ids),
        "error_codes": [],
    }
    return result


def schedule_ai_rerank(state: V32State, project: V32Project, job: Any, snapshot: Mapping[str, Any]) -> Any:
    """Queue the AI pass over a committed local snapshot (doc 17 staging).

    The delay is ``jobs.ai_idle_delay_ms`` from the contract.  A still-queued AI
    pass of the same project is superseded: only the newest local snapshot is
    worth refining.
    """
    from litsearch.v32.types import JobState

    delay_ms = int(contracts.job_defaults()["ai_idle_delay_ms"])
    jobs = state.jobs
    with jobs.lock:
        pending = [
            other
            for other in jobs.jobs.values()
            if other.project_id == project.project_id
            and other.doc.get("kind") == "ai_rerank"
            and other.state == str(JobState.QUEUED)
            and not other.thread
        ]
    for other in pending:
        jobs.transition(
            other,
            str(JobState.SUPERSEDED),
            error_codes=sorted(set(other.doc.get("error_codes") or []) | {"superseded_by_newer_context"}),
        )
        detach = getattr(jobs, "_detach", None)
        if callable(detach):
            detach(other)
    return jobs.submit(
        project,
        "ai_rerank",
        parent_job_id=getattr(job, "job_id", None),
        parent_snapshot_id=str(snapshot["snapshot_id"]),
        debounce_until=time.monotonic() + delay_ms / 1000.0,
        context_version=int(snapshot["context_version"]),
        corpus_version=int(snapshot["corpus_version"]),
        config_hash=job.doc.get("config_hash"),
        model_revision=job.doc.get("model_revision"),
        mode=job.doc.get("mode"),
    )


def run_ai_rerank(state: V32State, project: V32Project, job: Any) -> dict[str, Any]:
    """Refine one committed local snapshot with the model, or keep it unchanged."""
    parent_id = str(job.doc.get("parent_snapshot_id") or "")
    if not parent_id or project.doc.get("current_snapshot_id") != parent_id:
        return {"superseded": True}
    parent = load_current_snapshot(state, project, parent_id)
    if parent is None:
        return {"superseded": True}
    coverage = dict(parent.get("coverage") or {})
    result: dict[str, Any] = {
        "outcome": "succeeded",
        "snapshot_id": parent_id,
        "parent_snapshot_id": parent_id,
        "coverage": coverage,
        "ranking_stage": parent.get("ranking_stage"),
        "error_codes": [],
    }
    if state.ai() is None:
        result.update(ai_status="skipped", ai_error_code="ai_not_configured")
        return result
    state.jobs.set_stage(job, str(JobStage.ENHANCING), progress=0.4)
    items = [dict(item) for item in parent.get("items") or []]
    report = _apply_ai(state, project, job, items, parent_id)
    result.update(ai_status=report.get("status"), ai_error_code=report.get("error_code"))
    order = report.get("applied_order")
    if not order:
        return result
    by_id = {str(item["paper_id"]): item for item in items}
    reordered = []
    for index, paper_id in enumerate(order):
        item = by_id.get(str(paper_id))
        if item is None:
            continue
        reordered.append({**item, "rank": index + 1})  # scores untouched (AC-17)
    snapshot = {
        **parent,
        "snapshot_id": uuid.uuid4().hex,
        "parent_snapshot_id": parent_id,
        "ranking_stage": "ai_refined",
        "items": reordered,
    }
    if _commit(state, project, job, snapshot) == "superseded":
        return {"superseded": True}
    result.update(snapshot_id=snapshot["snapshot_id"], ranking_stage="ai_refined")
    return result


def ai_window_ids(items: Sequence[Mapping[str, Any]]) -> list[str]:
    """The AI window: the top ``ai.top_n`` of the *recommended* list only (doc 17)."""
    top_n = int(contracts.ai_defaults()["top_n"])
    recommended = [
        str(item["paper_id"])
        for item in sorted(items, key=lambda row: int(row.get("rank") or 0))
        if str(item.get("eligibility")) == str(Eligibility.RECOMMENDED)
    ]
    return recommended[:top_n]


def _apply_ai(
    state: V32State, project: V32Project, job: Any, items: Sequence[Mapping[str, Any]], parent: str
) -> dict[str, Any]:
    """Run the AI stage over a committed snapshot's order; any failure keeps it."""
    try:
        from litsearch.v32.ai_rerank import RerankInput, apply_window, build_window, rerank
    except ImportError as exc:  # pragma: no cover - module ships with the package
        return {"status": "failed", "error_code": "ai_module_unavailable", "detail": str(exc)}

    ai_cfg = contracts.ai_defaults()
    minimum_n = int(ai_cfg["minimum_n"])
    local_order = [str(item["paper_id"]) for item in sorted(items, key=lambda row: int(row.get("rank") or 0))]
    window_ids = ai_window_ids(items)
    if len(window_ids) < minimum_n:
        return {"status": "skipped", "error_code": "window_too_small"}

    records = project.doc.get("papers", {})
    inputs = []
    for paper_id in window_ids:
        record = records.get(paper_id) or {}
        inputs.append(
            RerankInput(
                paper_id=paper_id,
                title=str(record.get("title") or ""),
                abstract=record.get("abstract"),
                year=record.get("year"),
                doc_type=record.get("doc_type"),
            )
        )
    context_hash = hashlib.sha256(
        json.dumps(
            {
                "ids": window_ids,
                "context_version": job.doc.get("context_version"),
                "corpus_version": job.doc.get("corpus_version"),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    judgements = project.doc.get("judgements") or {}
    judgements_summary = json.dumps(
        {
            "core": sum(1 for j in judgements.values() if j.get("value") == "core"),
            "relevant": sum(1 for j in judgements.values() if j.get("value") == "relevant"),
            "excluded": sum(1 for j in judgements.values() if j.get("value") == "excluded"),
        },
        ensure_ascii=False,
    )
    window = build_window(
        inputs,
        context_hash=context_hash,
        intent=str((project.doc.get("anchor") or {}).get("original_text") or ""),
        judgements_summary=judgements_summary,
        parent_snapshot_id=parent,
        config=dict(ai_cfg),
    )
    outcome = rerank(window, state.ai(), parent_snapshot_id=parent, config=dict(ai_cfg))
    report = {
        "status": str(outcome.status),
        "error_code": outcome.error_code,
        "detail": outcome.detail,
        "model_revision": outcome.model_revision,
    }
    if str(outcome.status) != "applied" or not outcome.ordered_paper_ids:
        return report
    merged = apply_window(
        list(local_order), list(outcome.ordered_paper_ids), parent_snapshot_id=parent
    )
    report["applied_order"] = [str(pid) for pid in merged]
    return report


def run_import(state: V32State, project: V32Project, job: Any) -> dict[str, Any]:
    """Parse and match the items of one queued import batch."""
    import_id = str(job.doc.get("import_id") or "")
    record = (project.doc.get("imports") or {}).get(import_id)
    if record is None:
        raise V32Error(NOT_FOUND, "import_not_found", "导入批次不存在")
    try:
        from litsearch.v32.pdf_import import (
            Attachment,
            ImportStatus as PdfImportStatus,
            build_import_item,
            match_parsed,
            parse_pdf_bytes,
        )
    except ImportError as exc:  # pragma: no cover
        raise V32Error(CAPABILITY_UNAVAILABLE, "pdf_parser_unavailable", "PDF 解析模块不可用") from exc

    state.jobs.set_stage(job, str(JobStage.PARSING), progress=0.1)
    item_ids = [str(value) for value in record.get("item_ids") or []]
    succeeded = 0
    failed = 0
    added: list[str] = []
    for index, item_id in enumerate(item_ids):
        item = (record.get("items") or {}).get(item_id) or {}
        if str(item.get("status") or ImportStatus.UPLOADED) != str(ImportStatus.UPLOADED):
            # Doc 08: a retry reprocesses only the items it named (reset to
            # ``uploaded``); every other row is left exactly as it was.
            continue
        attachment_id = str(item.get("attachment_id") or "")
        display_name = str(item.get("display_name") or "document.pdf")
        try:
            data = state.store.attachment_bytes(project, attachment_id)
        except V32Error:
            record["items"][item_id] = _item_failure(item, "attachment_missing")
            failed += 1
            continue
        outcome = parse_pdf_bytes(data, display_name)
        if outcome.parsed is None:
            record["items"][item_id] = {
                **item,
                "status": str(outcome.status),
                "error_code": outcome.error_code,
                "retryable": bool(outcome.retryable),
                "paper_id": None,
            }
            failed += 1
            continue
        candidates = state.match_candidates(project, outcome.parsed)
        match = match_parsed(outcome.parsed, candidates)
        paper_id = state.store.resolve_paper_id(project, str(match.paper_id or ""))
        if str(match.status) == str(PdfImportStatus.LOCAL_SEED) or (
            str(match.status) == str(PdfImportStatus.MATCHED) and not paper_id
        ):
            paper_id = state.store.upsert_paper(project, _parsed_record(outcome.parsed))
            added.append(paper_id)
        row = build_import_item(
            item_id=item_id,
            attachment=Attachment(
                attachment_id=attachment_id,
                content_hash=str(item.get("content_hash") or ""),
                display_name=display_name,
                stored_path_key=str(item.get("content_hash") or ""),
                size_bytes=int(item.get("size_bytes") or 0),
                reused=bool(item.get("reused")),
            ),
            display_name=display_name,
            status=PdfImportStatus(match.status),
            match=match,
            error_code=None if paper_id else (match.conflicts[0] if match.conflicts else None),
        )
        # ``match.paper_id`` is only the candidate's id for a *matched* item;
        # a local seed's identity is the record this import just created.
        if paper_id:
            row["paper_id"] = paper_id
        if outcome.parsed is not None:
            row["parsed"] = outcome.parsed.to_dict()
        record["items"][item_id] = row
        if str(match.status) == str(PdfImportStatus.FAILED):
            failed += 1
        else:
            succeeded += 1
        state.jobs.set_stage(job, str(JobStage.MATCHING), progress=min(0.9, 0.1 + 0.8 * (index + 1) / max(1, len(item_ids))))
    if added:
        state.store.save(project, touch_corpus=True)
    record["updated_at"] = now()
    state.store.save(project)
    with job.lock:
        job.doc["import_id"] = import_id
    coverage = coverage_dto(
        status=str(CoverageStatus.WITHIN_BUDGET if not failed else CoverageStatus.PARTIAL),
        candidate_count=len(project.doc.get("papers", {})),
        stop_reasons=[] if not failed else ["import_items_failed"],
        scope="pdf_import",
    )
    return {
        "outcome": "succeeded" if not failed else "partial",
        "coverage": coverage,
        "import_id": import_id,
        "items_ok": succeeded,
        "items_failed": failed,
        "paper_ids": added,
        "error_codes": [] if not failed else ["import_items_failed"],
    }


def _item_failure(item: Mapping[str, Any], code: str) -> dict[str, Any]:
    return {
        **dict(item),
        "status": str(ImportStatus.FAILED),
        "error_code": code,
        "retryable": True,
        "paper_id": None,
    }


def _parsed_record(parsed: Any) -> dict[str, Any]:
    """A local seed built from parsed PDF metadata (identifiers stay aliases)."""
    aliases = {str(value) for value in [parsed.doi, *(parsed.doi_candidates or [])] if value}
    return {
        "paper_id": None,
        "title": parsed.title or "",
        "authors": list(parsed.authors or []),
        "year": parsed.year,
        "abstract": parsed.abstract,
        "doi": parsed.doi,
        "aliases": sorted(aliases),
    }


def run_zotero_import(state: V32State, project: V32Project, job: Any) -> dict[str, Any]:
    """Fetch the selected Zotero items and merge them into the project."""
    request = dict(job.doc.get("zotero_request") or {})
    adapter = state.zotero()
    if adapter is None or not getattr(adapter, "configured", False):
        return {
            "outcome": "failed",
            "error_codes": ["zotero_not_configured"],
            "coverage": coverage_dto(
                status=str(CoverageStatus.FAILED),
                candidate_count=len(project.doc.get("papers", {})),
                stop_reasons=["zotero_not_configured"],
                scope="zotero_import",
            ),
        }
    try:
        from litsearch.v32.zotero import apply_import, plan_import
    except ImportError as exc:  # pragma: no cover
        raise V32Error(503, CAPABILITY_UNAVAILABLE, "Zotero 模块不可用") from exc

    state.jobs.set_stage(job, str(JobStage.RETRIEVING), progress=0.2)
    library_type = str(request.get("library_type") or "user")
    library_id = str(request.get("library_id") or "")
    item_keys = [str(key) for key in request.get("item_keys") or []]
    # ``plan_import`` normalises each remote item itself, so the raw Zotero
    # response is what it wants — normalising twice would empty the item keys.
    remote = adapter.fetch_items(library_type, library_id, item_keys)
    existing = [dict(record) for record in project.doc.get("papers", {}).values()]
    plan = plan_import(
        existing,
        list(remote or []),
        library_type=library_type,
        library_id=library_id,
        selected_keys=item_keys,
        include_attachments=bool(request.get("include_selected_attachments")),
    )
    applied = apply_import(existing, plan)
    created_ids: dict[str, str] = {}
    for record in applied.get("papers") or []:
        if str(record.get("import_action") or "") == "create":
            created_ids[str(record.get("paper_id") or "")] = state.store.upsert_paper(
                project, dict(record)
            )
        elif str(record.get("import_action") or "") == "update":
            target = project.doc["papers"].get(str(record.get("paper_id") or ""))
            if target is not None and record.get("zotero"):
                target["zotero"] = dict(record["zotero"])
    if created_ids:
        state.store.save(project, touch_corpus=True)
        with job.lock:
            job.doc["corpus_version"] = project.corpus_version

    mapping = dict(project.doc.get("zotero_map") or {})
    results: list[dict[str, Any]] = []
    skipped = 0
    for row in plan.get("items") or []:
        item_key = str(row.get("item_key") or "")
        paper_id = str(created_ids.get(str(row.get("paper_id") or ""), row.get("paper_id") or ""))
        if item_key:
            mapping[item_key] = paper_id
        action = str(row.get("action") or "")
        if action in {"skipped"}:
            skipped += 1
        if not paper_id:
            continue
        results.append(
            {
                "paper_id": paper_id,
                "status": {
                    "create": "successful",
                    "update": "successful",
                    "unchanged": "unchanged",
                    "conflict": "conflict",
                }.get(action, "unknown_outcome"),
                "item_key": item_key or None,
                "item_version": row.get("item_version"),
                "error_code": None if action in {"create", "update", "unchanged"} else (row.get("reason") or None),
            }
        )
    project.doc["zotero_map"] = mapping
    state.store.save(project)
    with job.lock:
        job.doc["zotero_results"] = results
    coverage = coverage_dto(
        status=str(CoverageStatus.PARTIAL if skipped else CoverageStatus.WITHIN_BUDGET),
        candidate_count=len(project.doc.get("papers", {})),
        stop_reasons=["unsupported_items_skipped"] if skipped else [],
        scope="zotero_import",
    )
    return {
        "outcome": "partial" if skipped else "succeeded",
        "coverage": coverage,
        "imported": len(created_ids),
        "skipped": skipped,
        "error_codes": [] if not skipped else ["unsupported_items_skipped"],
    }


def run_zotero_export(state: V32State, project: V32Project, job: Any) -> dict[str, Any]:
    """Write a consumed export preview, one honest outcome per item."""
    adapter = state.zotero()
    if adapter is None or not getattr(adapter, "configured", False):
        return {
            "outcome": "failed",
            "error_codes": ["zotero_not_configured"],
            "coverage": coverage_dto(
                status=str(CoverageStatus.FAILED),
                candidate_count=len(project.doc.get("papers", {})),
                stop_reasons=["zotero_not_configured"],
                scope="zotero_export",
            ),
        }
    try:
        from litsearch.v32.zotero import run_export
    except ImportError as exc:  # pragma: no cover
        raise V32Error(CAPABILITY_UNAVAILABLE, "zotero_module_unavailable", "Zotero 模块不可用") from exc
    state.jobs.set_stage(job, str(JobStage.EXPORTING), progress=0.3)
    preview = job.doc.get("zotero_preview") or {}
    try:
        results = run_export(preview, adapter, confirm=True)
    except V32Error as exc:
        with job.lock:
            job.doc["zotero_results"] = []
        return {
            "outcome": "failed",
            "error_codes": [str(exc.code)],
            "coverage": coverage_dto(
                status=str(CoverageStatus.FAILED),
                candidate_count=len(project.doc.get("papers", {})),
                stop_reasons=[str(exc.code)],
                scope="zotero_export",
            ),
        }
    results = [dict(row) for row in results or []]
    with job.lock:
        job.doc["zotero_results"] = results
    failed = [row for row in results if str(row.get("status")) == "failed"]
    conflict = [row for row in results if str(row.get("status")) == "conflict"]
    coverage = coverage_dto(
        status=str(CoverageStatus.PARTIAL if failed or conflict else CoverageStatus.WITHIN_BUDGET),
        candidate_count=len(results),
        stop_reasons=sorted({str(row.get("error_code")) for row in failed + conflict if row.get("error_code")}),
        scope="zotero_export",
    )
    return {
        "outcome": "partial" if failed or conflict else "succeeded",
        "coverage": coverage,
        "written": len([row for row in results if str(row.get("status")) == "successful"]),
        "error_codes": [] if not failed and not conflict else ["zotero_partial"],
    }


def import_batch_dto(project: V32Project, import_id: str) -> dict[str, Any]:
    """``$defs/ImportBatch`` for one recorded batch."""
    record = (project.doc.get("imports") or {}).get(str(import_id))
    if record is None:
        raise V32Error(NOT_FOUND, "import_not_found", "导入批次不存在")
    items = [
        dict((record.get("items") or {}).get(item_id) or {})
        for item_id in record.get("item_ids") or []
    ]
    return {
        "import_id": str(import_id),
        "job_id": str(record.get("job_id") or ""),
        "project_id": project.project_id,
        "revision": int(project.revision),
        "items": items,
    }


def overview_payload(state: V32State, project: V32Project) -> dict[str, Any]:
    """The ``$defs/Overview`` for a project, built with or without a model.

    Doc 17 and AC-01: with zero user marks the page is still real — it is built
    from the ranking snapshot and the corpus statistics.  A model, when one
    exists, may only polish text: it cannot add a branch or invent a statement,
    and ``overview.build_overview`` re-validates whatever it returns.
    """
    from litsearch.v32 import mapping as mapping_mod
    from litsearch.v32.overview import build_overview, derive_leads

    records = [dict(record) for record in project.doc.get("papers", {}).values()]
    records.sort(key=lambda row: str(row.get("paper_id")))
    snapshot = load_current_snapshot(state, project)
    coverage = dict(snapshot.get("coverage") or {}) if snapshot else coverage_dto(
        status=str(CoverageStatus.NO_RESULTS if not records else CoverageStatus.WITHIN_BUDGET),
        candidate_count=len(records),
    )
    view = {
        "snapshot_id": (snapshot or {}).get("snapshot_id"),
        "items": (snapshot or {}).get("items") or [],
        "papers": records,
        "coverage": coverage,
    }
    timeline = mapping_mod.timeline(records)
    trends = mapping_mod.trends(records)
    edges = citation_edges(state.store, project)
    node_ids = [str(record["paper_id"]) for record in records]
    texts = {
        str(record["paper_id"]): f"{record.get('title') or ''} {record.get('abstract') or ''}"
        for record in records
    }
    branch_list: list[dict[str, Any]] = []
    community = mapping_mod.build_map(edges, node_ids, texts=texts)
    if getattr(community, "degraded", False):
        # Doc 17 rule 6: too little relation data must not be dressed up as a
        # community conclusion.  Say so instead of printing empty branches.
        reason = str(getattr(community, "degraded_reason", "") or "relations_insufficient")
        coverage = {
            **coverage,
            "stop_reasons": sorted(set(coverage.get("stop_reasons") or []) | {f"mapping_degraded:{reason}"}),
        }
    else:
        branch_list = [
            {
                "branch_id": entry.community_id,
                "label": entry.label,
                "label_origin": entry.label_origin,
                "paper_ids": list(entry.paper_ids),
                "representative_paper_ids": list(entry.paper_ids)[:3],
                "evidence_ids": [],
                "method": f"{community.algorithm}@{community.algorithm_version}",
                "limitations": ["仅基于当前检索集的关系数据"],
            }
            for entry in community.communities
        ]
    leads: list[dict[str, Any]] = []
    if branch_list:
        leads = derive_leads(view, communities=branch_list, edges=edges, adapter=state.ai())
    return build_overview(
        view,
        branches=branch_list,
        timeline=timeline,
        trends=trends,
        leads=leads,
        coverage=coverage,
        adapter=state.ai(),
    )


def register_handlers(state: V32State) -> None:
    """Register every v3.2 job kind on the scheduler."""
    state.jobs.register("discover", lambda project, job: run_discover(state, project, job))
    state.jobs.register("expand", lambda project, job: run_expand(state, project, job))
    state.jobs.register("rerank", lambda project, job: run_rerank(state, project, job))
    state.jobs.register("ai_rerank", lambda project, job: run_ai_rerank(state, project, job))
    state.jobs.register("import", lambda project, job: run_import(state, project, job))
    state.jobs.register("zotero_import", lambda project, job: run_zotero_import(state, project, job))
    state.jobs.register("zotero_export", lambda project, job: run_zotero_export(state, project, job))
