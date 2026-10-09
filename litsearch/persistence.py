"""Save / restore a review session to disk.

The GUI used to keep all progress in memory only, so a browser refresh (or a
crash) threw away a multi-minute run.  `ReviewState` is now serialised after
every phase and every screening decision, and restored automatically on the
next start.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import tempfile
import time
from copy import deepcopy
from dataclasses import asdict

from litsearch.config import BASE_DIR
from litsearch.filters import (
    ALGORITHM_VERSION,
    SCORE_VERSION,
    CalibrationRecord,
    RelevanceFilter,
    corpus_hash,
)
from litsearch.identifiers import PaperIdentifiers, paper_aliases
from litsearch.models import Author, DiscoveryTrace, Paper
from litsearch.prisma import (
    PRISMARecord,
    PRISMATracker,
    ScreeningDecision,
)
from litsearch.search import ReviewPhase, ReviewState, current_scored_corpus, ranking_query
from litsearch.snowball import SnowballResult, SnowballRound

logger = logging.getLogger(__name__)

SESSION_DIR = os.path.join(BASE_DIR, ".sessions")
AUTOSAVE_NAME = "autosave.json"
#: v4 adds the screening calibration, per-decision audit trail, merge conflicts
#: and the provider-raw count ledger. Older files simply lack those keys.
#: Runtime accounting, score provenance and retrieval attempt facts are also
#: optional v4 fields so historical v4 documents remain valid.
SCHEMA_VERSION = 4


# ---------------------------------------------------------------------------
# Paper <-> dict
# ---------------------------------------------------------------------------


def paper_to_dict(p: Paper) -> dict:
    return {
        "id": p.id,
        "title": p.title,
        "abstract": p.abstract,
        "authors": [{"name": a.name, "author_id": a.author_id} for a in p.authors],
        "year": p.year,
        "venue": p.venue,
        "doi": p.doi,
        "citation_count": p.citation_count,
        "reference_count": p.reference_count,
        "citation_ids": p.citation_ids,
        "reference_ids": p.reference_ids,
        "source": p.source,
        "url": p.url,
        "relevance_score": p.relevance_score,
        "score_context_id": getattr(p, "score_context_id", ""),
        "topics": p.topics,
        "canonical_id": p.canonical_id,
        "identifiers": asdict(p.identifiers),
        "discovery_traces": [asdict(t) for t in p.discovery_traces],
        "score_breakdown": p.score_breakdown,
    }


def paper_from_dict(d: dict) -> Paper:
    return Paper(
        id=d.get("id", ""),
        title=d.get("title", ""),
        abstract=d.get("abstract"),
        authors=[Author(name=a.get("name", ""), author_id=a.get("author_id"))
                 for a in (d.get("authors") or [])],
        year=d.get("year"),
        venue=d.get("venue"),
        doi=d.get("doi"),
        citation_count=d.get("citation_count", 0),
        reference_count=d.get("reference_count", 0),
        citation_ids=d.get("citation_ids", []) or [],
        reference_ids=d.get("reference_ids", []) or [],
        source=d.get("source", ""),
        url=d.get("url"),
        relevance_score=d.get("relevance_score", 0.0),
        score_context_id=d.get("score_context_id", ""),
        topics=d.get("topics", []) or [],
        identifiers=PaperIdentifiers(**{k: v for k, v in (d.get("identifiers") or {}).items() if k in PaperIdentifiers.__dataclass_fields__}),
        discovery_traces=[DiscoveryTrace(**t) for t in d.get("discovery_traces", [])],
        score_breakdown=d.get("score_breakdown", {}) or {},
    )


# ---------------------------------------------------------------------------
# ReviewState <-> dict
# ---------------------------------------------------------------------------


def state_to_dict(state: ReviewState) -> dict:
    prisma = state.prisma.to_dict()
    prisma["papers"] = {
        key: paper_to_dict(rec.paper) for key, rec in state.prisma.records.items()
    }

    snowball = None
    if state.snowball_result is not None:
        sn = state.snowball_result
        snowball = {
            "saturated": sn.saturated,
            "saturation_reason": sn.saturation_reason,
            "initial_ids": sn.initial_ids,
            "next_seed_ids": sn.next_seed_ids,
            "parameters": sn.parameters,
            "completed": sn.completed,
            "stop_reason": sn.stop_reason,
            "seed_ids": list(getattr(sn, "seed_ids", [])),
            "all_papers": {k: paper_to_dict(p) for k, p in sn.all_papers.items()},
            "rounds": [
                {
                    "round_number": r.round_number,
                    "source_papers": r.source_papers,
                    "direction": r.direction,
                    "new_papers": [paper_to_dict(p) for p in r.new_papers],
                    "raw_count": r.raw_count,
                    "unique_count": r.unique_count,
                    "relevant_count": r.relevant_count,
                    "cumulative_unique": r.cumulative_unique,
                    "failed_seeds": r.failed_seeds,
                    "seed_ids": list(r.seed_ids),
                    "pending_seeds": list(r.pending_seeds),
                    "canceled": r.canceled,
                }
                for r in sn.rounds
            ],
        }

    return {
        "version": SCHEMA_VERSION,
        "saved_at": time.time(),
        "topic": state.topic,
        "research_direction": state.research_direction,
        "phase": state.phase.value,
        "start_time": state.start_time,
        "scoping_papers": [paper_to_dict(p) for p in state.scoping_papers],
        "topic_clusters": state.topic_clusters,
        "key_journals": [list(x) for x in state.key_journals],
        "key_authors": [list(x) for x in state.key_authors],
        "year_distribution": {str(k): v for k, v in state.year_distribution.items()},
        "search_papers": [paper_to_dict(p) for p in state.search_papers],
        "similar_papers": [
            [paper_to_dict(p), score, method] for p, score, method in state.similar_papers
        ],
        "snowball_result": snowball,
        "prisma": prisma,
        "search_manifest": state.search_manifest,
        "stop_reason": state.stop_reason,
        "http_budget": deepcopy(state.http_budget),
        "run_history": deepcopy(state.run_history),
        "score_context_id": state.score_context_id,
        "score_context_stage": state.score_context_stage,
        "score_context_history": deepcopy(state.score_context_history),
        "calibration_context_id": state.calibration_context_id,
        # The calibration is screening state: it travels with the session so a
        # restored session knows whether its threshold may still be trusted.
        "calibration": (
            state.calibration.to_dict()
            if getattr(state, "calibration", None) is not None
            else None
        ),
    }


def load_calibration(state: ReviewState, data: dict | None) -> CalibrationRecord | None:
    """Restore a session's calibration, invalidating it if anything moved.

    A calibration is bound to the query it was derived for, the candidate set
    (`corpus_hash`) and the scoring algorithm/version. Restoring an older or
    different session must not silently make a stale threshold usable again.
    """
    raw = (data or {}).get("calibration")
    if not isinstance(raw, dict) or not raw:
        return None
    try:
        record = CalibrationRecord.from_dict(raw)
    except (TypeError, ValueError):
        return None
    if (record.algorithm_version != ALGORITHM_VERSION
            or record.score_version != SCORE_VERSION):
        record.mark_invalid(
            "scoring version changed: "
            f"{record.algorithm_version}/{record.score_version} -> "
            f"{ALGORITHM_VERSION}/{SCORE_VERSION}"
        )
        return record
    papers = current_scored_corpus(state)
    expected = corpus_hash(papers)
    if record.corpus_hash and record.corpus_hash != expected:
        record.mark_invalid(
            "corpus changed (corpus_hash mismatch): the restored candidate set "
            "is not the one this calibration was derived from"
        )
        return record
    if record.query:
        known_queries = {state.topic, state.research_direction, ranking_query(state)}
        if record.query not in known_queries:
            record.mark_invalid(
                f"query changed: calibrated for {record.query!r}, this session asks "
                f"{ranking_query(state)!r}"
            )
    current_context = str(getattr(state, "score_context_id", "") or "")
    if current_context:
        reason = record.invalidated_by(
            query=ranking_query(state),
            corpus_hash=expected,
            algorithm_version=ALGORITHM_VERSION,
            score_version=SCORE_VERSION,
            score_context_id=current_context,
        )
        if reason:
            record.mark_invalid(reason)
    return record


def state_from_dict(d: dict) -> ReviewState:
    # Every import route (including Web and MCP callers) shares the same
    # migration/version checks as a disk restore. Never silently downgrade a
    # future session merely because the caller already decoded its JSON.
    from litsearch.session_schema import validate_session_dict

    d = validate_session_dict(d)
    state = ReviewState(
        topic=d.get("topic", ""),
        research_direction=d.get("research_direction", ""),
        phase=ReviewPhase(d.get("phase", ReviewPhase.SCOPING.value)),
    )
    state.start_time = d.get("start_time", 0.0)
    state.search_manifest = d.get("search_manifest", {}) or {}
    state.stop_reason = d.get("stop_reason", "")
    state.http_budget = deepcopy(d.get("http_budget", {}))
    state.run_history = deepcopy(d.get("run_history", []))
    state.score_context_id = d.get("score_context_id", "")
    state.score_context_stage = d.get("score_context_stage", "")
    state.score_context_history = deepcopy(d.get("score_context_history", []))
    state.scoping_papers = [paper_from_dict(p) for p in d.get("scoping_papers", [])]
    state.topic_clusters = d.get("topic_clusters", {}) or {}
    state.key_journals = [tuple(x) for x in d.get("key_journals", [])]
    state.key_authors = [tuple(x) for x in d.get("key_authors", [])]
    state.year_distribution = {
        int(k): v for k, v in (d.get("year_distribution") or {}).items()
    }
    state.search_papers = [paper_from_dict(p) for p in d.get("search_papers", [])]
    state.similar_papers = [
        (paper_from_dict(x[0]), x[1], x[2]) for x in d.get("similar_papers", [])
    ]

    sn = d.get("snowball_result")
    if sn:
        result = SnowballResult(
            saturated=sn.get("saturated", False),
            saturation_reason=sn.get("saturation_reason", ""),
            all_papers={k: paper_from_dict(p)
                        for k, p in (sn.get("all_papers") or {}).items()},
            initial_ids=sn.get("initial_ids", []),
            next_seed_ids=sn.get("next_seed_ids", []),
            parameters=sn.get("parameters", {}),
            completed=sn.get("completed", True),
            stop_reason=sn.get("stop_reason", ""),
        )
        result.rounds = [
            SnowballRound(
                round_number=r.get("round_number", i + 1),
                source_papers=r.get("source_papers", []),
                direction=r.get("direction", "both"),
                new_papers=[paper_from_dict(p) for p in r.get("new_papers", [])],
                raw_count=r.get("raw_count", len(r.get("new_papers", []))),
                unique_count=r.get("unique_count", len(r.get("new_papers", []))),
                relevant_count=r.get("relevant_count", 0),
                cumulative_unique=r.get("cumulative_unique", 0),
                failed_seeds=r.get("failed_seeds", 0),
                seed_ids=r.get("seed_ids", []),
                pending_seeds=r.get("pending_seeds", []),
                canceled=r.get("canceled", False),
            )
            for i, r in enumerate(sn.get("rounds", []))
        ]
        if "seed_ids" in sn:
            result.seed_ids = list(sn["seed_ids"])
        state.snowball_result = result

    prisma_data = d.get("prisma")
    if prisma_data:
        papers = {k: paper_from_dict(p)
                  for k, p in (prisma_data.get("papers") or {}).items()}
        tracker = PRISMATracker()
        tracker.full_text_stage_enabled = prisma_data.get(
            "full_text_stage_enabled", False
        )
        tracker._duplicates_seen = prisma_data.get("duplicates_seen", 0)
        tracker._id_counter.update(prisma_data.get("identification_counts", {}))
        for rec in prisma_data.get("records", []):
            key = rec.get("key", "")
            paper = papers.get(key)
            if paper is None:
                continue
            record = PRISMARecord(
                paper=paper,
                source=rec.get("source", ""),
                screening_decision=ScreeningDecision(
                    rec.get("screening_decision", "pending")),
                screening_reason=rec.get("screening_reason", ""),
                full_text_retrieved=rec.get("full_text_retrieved", False),
                full_text_decision=ScreeningDecision(
                    rec.get("full_text_decision", "pending")),
                full_text_reason=rec.get("full_text_reason", ""),
                retrieval_attempted=rec.get("retrieval_attempted", False),
                retrieval_failure_reason=rec.get("retrieval_failure_reason", ""),
                relevance_score=rec.get("relevance_score", 0.0),
                score_context_id=rec.get("score_context_id", ""),
                requires_manual_review=bool(rec.get("requires_manual_review", False)),
                conflicts=[dict(c) for c in (rec.get("conflicts") or [])],
                history=[dict(h) for h in (rec.get("history") or [])],
            )
            record.rejection_reason = record.screening_reason
            tracker.records[key] = record
            for alt in paper_aliases(paper) | {paper.id.lower()}:
                if alt:
                    tracker._alt_keys[alt] = key
        ledger_data = prisma_data.get("ledger") or {}
        for entry in ledger_data.get("entries", []):
            tracker.ledger.add_entry(dict(entry))
        state.prisma = tracker

    # ReviewState-level context ids were not serialised by early v4 sessions,
    # while each Paper/PRISMA record already carried the context. Recover the
    # single comparable context from the restored corpus before attaching a
    # calibration, so the calibration property stamps the correct scale.
    contexts = RelevanceFilter.score_contexts_of(current_scored_corpus(state))
    contexts.discard("")
    if "score_context_id" not in d and len(contexts) == 1:
        state.score_context_id = next(iter(contexts))
    state.calibration = load_calibration(state, d)
    if "calibration_context_id" in d:
        # Assignment normally stamps the current context. An imported record
        # must retain the context in which it was actually fitted, however;
        # stamping it anew would make a stale calibration look current.
        state.calibration_context_id = d["calibration_context_id"]
        if (state.calibration is not None
                and state.calibration_context_id != state.score_context_id):
            state.calibration.mark_invalid(
                "scoring context changed: saved calibration context does not "
                "match the restored corpus context"
            )

    return state


# ---------------------------------------------------------------------------
# File IO
# ---------------------------------------------------------------------------


def ensure_session_dir() -> str:
    os.makedirs(SESSION_DIR, exist_ok=True)
    return SESSION_DIR


def _slug(text: str) -> str:
    s = re.sub(r"[^\w]+", "_", (text or "").strip().lower()).strip("_")
    return s[:40] or "session"


def save_state(state: ReviewState, path: str | None = None) -> str:
    """Serialise a ReviewState to disk. Returns the file path."""
    ensure_session_dir()
    path = path or os.path.join(SESSION_DIR, AUTOSAVE_NAME)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = ""
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=os.path.dirname(os.path.abspath(path)), suffix=".tmp", delete=False) as f:
            tmp = f.name
            json.dump(state_to_dict(state), f, ensure_ascii=False)
        os.replace(tmp, path)
    finally:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)
    return path


def load_state(path: str | None = None) -> ReviewState | None:
    """Restore a ReviewState, or None if the file is missing/corrupt.

    Validation is not optional here. v0.9.0 read the JSON and handed it to
    ``state_from_dict`` inside a bare ``except Exception: return None``, so a
    session from a future schema version, or one with a corrupted field, either
    failed silently or was partially applied. The file now goes through
    :mod:`litsearch.session_schema` first: a corrupt or schema-invalid file is
    backed up and reported by name, a known older version is migrated, and a
    future version is refused instead of being read as if it were current.

    A rejected file is never modified, and the autosave path is left untouched,
    so a bad import cannot destroy the session the user still has.
    """
    path = path or os.path.join(SESSION_DIR, AUTOSAVE_NAME)
    if not os.path.exists(path):
        return None

    try:
        from litsearch.session_schema import SessionSchemaError, load_session_file

        try:
            data = load_session_file(path)
        except SessionSchemaError as exc:
            logger.error(
                "session rejected (%s): %s — a backup was kept, the original was not modified",
                path, exc,
            )
            return None
        return state_from_dict(data)
    except ImportError:
        # The validator is part of this package; if it is somehow absent we
        # still restore, but we say so rather than pretending we validated.
        logger.warning("session schema validator unavailable; loading without validation")
        try:
            with open(path, encoding="utf-8") as f:
                return state_from_dict(json.load(f))
        except Exception:
            return None
    except Exception as exc:
        logger.error("session could not be restored from %s: %s", path, exc)
        return None


def save_state_as(state: ReviewState, name: str = "") -> str:
    """Save under a named session file (for keeping several topics)."""
    ensure_session_dir()
    fname = f"{_slug(name or state.topic)}.json"
    return save_state(state, os.path.join(SESSION_DIR, fname))


def list_sessions() -> list[dict]:
    """All saved sessions, newest first."""
    if not os.path.exists(SESSION_DIR):
        return []
    items = []
    for fname in os.listdir(SESSION_DIR):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(SESSION_DIR, fname)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            items.append({
                "path": path,
                "name": fname,
                "topic": data.get("topic", ""),
                "phase": data.get("phase", ""),
                "saved_at": data.get("saved_at", os.path.getmtime(path)),
                "papers": len(data.get("search_papers", [])),
            })
        except Exception:
            continue
    items.sort(key=lambda x: x["saved_at"], reverse=True)
    return items


def delete_session(path: str) -> None:
    """Remove a saved session. A missing file is already the desired state."""
    with contextlib.suppress(OSError):
        os.remove(path)


def session_age_text(saved_at: float) -> str:
    delta = max(0.0, time.time() - saved_at)
    if delta < 60:
        return f"{int(delta)} 秒前"
    if delta < 3600:
        return f"{int(delta // 60)} 分钟前"
    return f"{int(delta // 3600)} 小时前"
