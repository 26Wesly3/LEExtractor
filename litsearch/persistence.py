"""Save / restore a review session to disk.

The GUI used to keep all progress in memory only, so a browser refresh (or a
crash) threw away a multi-minute run.  `ReviewState` is now serialised after
every phase and every screening decision, and restored automatically on the
next start.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import time

from litsearch.config import BASE_DIR
from litsearch.models import Author, Paper
from litsearch.prisma import (
    PRISMARecord,
    PRISMATracker,
    ScreeningDecision,
)
from litsearch.search import ReviewPhase, ReviewState
from litsearch.snowball import SnowballResult, SnowballRound

SESSION_DIR = os.path.join(BASE_DIR, ".sessions")
AUTOSAVE_NAME = "autosave.json"


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
        "topics": p.topics,
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
        topics=d.get("topics", []) or [],
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
            "all_papers": {k: paper_to_dict(p) for k, p in sn.all_papers.items()},
            "rounds": [
                {
                    "round_number": r.round_number,
                    "source_papers": r.source_papers,
                    "direction": r.direction,
                    "new_papers": [paper_to_dict(p) for p in r.new_papers],
                }
                for r in sn.rounds
            ],
        }

    return {
        "version": 2,
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
    }


def state_from_dict(d: dict) -> ReviewState:
    state = ReviewState(
        topic=d.get("topic", ""),
        research_direction=d.get("research_direction", ""),
        phase=ReviewPhase(d.get("phase", ReviewPhase.SCOPING.value)),
    )
    state.start_time = d.get("start_time", 0.0)
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
        )
        result.rounds = [
            SnowballRound(
                round_number=r.get("round_number", i + 1),
                source_papers=r.get("source_papers", []),
                direction=r.get("direction", "both"),
                new_papers=[paper_from_dict(p) for p in r.get("new_papers", [])],
            )
            for i, r in enumerate(sn.get("rounds", []))
        ]
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
                relevance_score=rec.get("relevance_score", 0.0),
            )
            record.rejection_reason = record.screening_reason
            tracker.records[key] = record
            for alt in {paper.id, paper.doi}:
                if alt:
                    tracker._alt_keys[alt.lower()] = key
        state.prisma = tracker

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
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state_to_dict(state), f, ensure_ascii=False)
    os.replace(tmp, path)
    return path


def load_state(path: str | None = None) -> ReviewState | None:
    """Restore a ReviewState, or None if the file is missing/corrupt."""
    path = path or os.path.join(SESSION_DIR, AUTOSAVE_NAME)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return state_from_dict(json.load(f))
    except Exception:
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
