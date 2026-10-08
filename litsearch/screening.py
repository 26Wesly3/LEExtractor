"""Screening / review-status domain logic — one implementation, no MCP layer.

This module owns the *meaning* of a review's screening state:

* :data:`SCREENING_STATUS_VALUES`, :data:`SCREENING_FIELD_DEFINITIONS` and
  :data:`SCREENING_COMPATIBILITY_NOTE` — the published vocabulary;
* :func:`calibration_record` / :func:`calibration_usable` /
  :func:`threshold_calibrated` / :func:`calibration_summary` — when a
  threshold may be applied at all;
* :func:`paper_screening_status` and :func:`screening_facts` — the state
  machine (and the counts that keep a coarse status honest);
* :func:`screening_section` — the Markdown block used by AGENT_HANDOFF.md and
  the Evidence Pack;
* :func:`apply_calibrated_screening` — calibrated assistance, applied through
  the core API so the refusals live in one place.

It deliberately has **no dependency on the MCP adapter** (``litsearch.server``
imports ``fastmcp`` at module level) and none on ``fastmcp`` itself: the
Streamlit app, the export bundle and the MCP tools all have to be able to read
the same status without starting a protocol server.

``litsearch/server.py`` re-exports every public name here, so
``from litsearch.server import screening_facts`` keeps working.
"""

# ---------------------------------------------------------------------------
# Screening status (single implementation — contract §13)
# ---------------------------------------------------------------------------

#: Allowed values of ``screening_status``.
SCREENING_STATUS_VALUES = (
    "not_started",
    "preliminary_included",
    "final_included",
    "excluded",
)

#: What each exposed field means, so callers do not have to guess.
SCREENING_FIELD_DEFINITIONS = {
    "screening_status": (
        "Session-level screening state. 'not_started' = no decision recorded yet, or "
        "decisions were recorded but nothing is included while assessments are still open "
        "(an unfinished review is never reported as 'excluded'). "
        "'preliminary_included' = at least one paper passed title/abstract screening and the "
        "full-text stage is not complete; inclusion is preliminary. "
        "'final_included' = the full-text stage is complete and at least one paper was "
        "accepted there (explicit human assessment). "
        "'excluded' = the review is complete at its last stage and no paper is included."
    ),
    "threshold_calibrated": (
        "True only when a calibration record for this session exists and reports "
        "status == 'calibrated' (labelled relevant + irrelevant papers, separated by the "
        "score). Uncalibrated relevance scores rank results and nothing else: they never "
        "justify an automatic reject."
    ),
    "requires_manual_review": (
        "True while any paper is still pending/maybe at either stage, or while the full-text "
        "stage has not been completed. Automation never marks full text as reviewed."
    ),
    "full_text_review_completed": (
        "True only when the full-text stage has been run and every paper accepted at "
        "title/abstract screening has an explicit full-text decision (accept or reject)."
    ),
}

#: Backwards-compatibility note published next to the fields.
SCREENING_COMPATIBILITY_NOTE = (
    "v0.9.0 exposed only 'included_papers' plus PRISMA totals, and "
    "'literature_review_search' auto-screened by relevance >= 0.15 by default. "
    "v0.9.1 keeps 'included_papers'/'studies_included' working but adds "
    "screening_status / threshold_calibrated / requires_manual_review / "
    "full_text_review_completed, defaults auto_screen to False, and returns "
    "candidate papers plus a restorable session so a caller can continue manually. "
    "'included_papers' now means 'passed the last completed stage' and is empty "
    "when screening has not started — read 'candidates' in that case."
)


def calibration_record(state):
    """The session's calibration record (frozen contract §8: ``ReviewState.calibration``).

    ``None`` means the session was never calibrated, which is the normal state
    for a fresh run and the reason automatic screening is off by default.
    """
    return getattr(state, "calibration", None)


def calibration_usable(record) -> bool:
    """Whether an automatic threshold may be applied at all (contract §8).

    ``True`` only for a record whose ``status`` is ``"calibrated"`` — a
    single-class calibration is an ``insufficient_labels`` ranking aid, never a
    boundary. ``CalibrationRecord.usable`` (which also requires a threshold and
    no invalidation) is the authoritative check when the record provides it.
    """
    if record is None:
        return False
    status = getattr(record, "status", None)
    if status is not None and status != "calibrated":
        return False
    usable = getattr(record, "usable", None)
    if isinstance(usable, bool):
        return usable
    return status == "calibrated" and getattr(record, "threshold", None) is not None


def threshold_calibrated(state) -> bool:
    """Whether this session has a usable calibration (contract §13)."""
    return calibration_usable(calibration_record(state))


def calibration_summary(state) -> dict:
    """The calibration behind ``threshold_calibrated``, for display and audit."""
    record = calibration_record(state)
    if record is None:
        return {
            "present": False,
            "status": "",
            "threshold": None,
            "reliable": False,
            "note": "本会话没有标定记录：分数只用于排序。No calibration record: scores only rank.",
        }
    summary = {
        "present": True,
        "status": str(getattr(record, "status", "") or ""),
        "threshold": getattr(record, "threshold", None),
        "reliable": bool(getattr(record, "reliable", False)),
        "query": str(getattr(record, "query", "") or ""),
        "created_at": str(getattr(record, "created_at", "") or ""),
        "labels": dict(getattr(record, "labels", {}) or {}),
        "note": str(getattr(record, "note", "") or ""),
    }
    to_dict = getattr(record, "to_dict", None)
    if callable(to_dict):
        # Round-trippable: pass this back as `literature_review_search(calibration=...)`.
        summary["record"] = to_dict()
    return summary


def _prisma_records(state) -> list:
    prisma = getattr(state, "prisma", None)
    return list(getattr(prisma, "records", {}).values())


def paper_screening_status(record) -> str:
    """Per-paper status using the same four values as ``screening_status``."""
    from litsearch.prisma import ScreeningDecision

    title = record.screening_decision
    full = record.full_text_decision
    if title == ScreeningDecision.REJECT or full == ScreeningDecision.REJECT:
        return "excluded"
    if record.passed_screening and full == ScreeningDecision.ACCEPT:
        return "final_included"
    if record.passed_screening:
        return "preliminary_included"
    return "not_started"


def screening_facts(state) -> dict:
    """Screening status for a session: the four contract fields plus counts.

    See :data:`SCREENING_FIELD_DEFINITIONS` for the exact meaning of each
    value. The counts are included because a coarse status can hide an
    unfinished review, and the caller should be able to see that.

    The state machine is deliberately monotonic, because v0.9.1 could report
    ``screening_status = "final_included"`` together with
    ``full_text_review_completed = True`` while two papers were still
    undecided at title/abstract. Every rule below follows from one invariant:

        **A stage is finished only when nothing at or before it is still
        actionable.** ``PENDING`` and ``MAYBE`` are both *actionable* — MAYBE
        means "a human must look again", so it can never count as decided.

    That single rule is what stops "completed" and "still pending" from being
    true at the same time.
    """
    from litsearch.prisma import ScreeningDecision

    actionable = (ScreeningDecision.PENDING, ScreeningDecision.MAYBE)
    records = _prisma_records(state)

    title_actionable = [r for r in records if r.screening_decision in actionable]
    title_accepted = [r for r in records if r.passed_screening]
    title_decided = [r for r in records if r.screening_decision
                     in (ScreeningDecision.ACCEPT, ScreeningDecision.REJECT)]

    # Only papers that actually cleared title/abstract can have a full-text
    # task; anything else has no eligibility state yet.
    full_actionable = [r for r in title_accepted if r.full_text_decision in actionable]
    full_decided = [r for r in title_accepted
                    if r.full_text_decision in (ScreeningDecision.ACCEPT, ScreeningDecision.REJECT)]
    included = [r for r in title_accepted
                if r.full_text_decision == ScreeningDecision.ACCEPT]

    prisma = getattr(state, "prisma", None)
    stage_enabled = bool(getattr(prisma, "full_text_stage_enabled", False))
    flagged = [r for r in records if getattr(r, "requires_manual_review", False)]

    # Finishing title/abstract is a precondition for the full-text stage being
    # complete, and the full-text stage must have actually been worked on.
    title_stage_complete = bool(records) and not title_actionable
    full_text_review_completed = bool(
        stage_enabled and title_stage_complete and full_decided and not full_actionable
    )

    if not records or not (title_decided or full_decided):
        status = "not_started"
    elif not title_stage_complete or full_actionable:
        # Something is still actionable, so the review is not finished — at
        # most it is "preliminary", and only if work has actually progressed.
        status = "preliminary_included" if (title_accepted or title_decided) else "not_started"
    elif full_text_review_completed:
        status = "final_included" if included else "excluded"
    elif title_accepted:
        # Title/abstract is fully decided but the eligibility stage has not
        # been run at all.
        status = "preliminary_included"
    else:
        status = "excluded"

    return {
        "screening_status": status,
        "threshold_calibrated": threshold_calibrated(state),
        "requires_manual_review": bool(
            not full_text_review_completed or title_actionable or full_actionable or flagged
        ),
        "full_text_review_completed": full_text_review_completed,
        # Counts, so the coarse status cannot hide an unfinished review.
        "records_total": len(records),
        "title_abstract_decided": sum(
            1 for r in records if r.screening_decision != ScreeningDecision.PENDING
        ),
        "title_abstract_accepted": len(title_accepted),
        "title_abstract_pending": len(title_actionable),
        "full_text_decided": len(full_decided),
        "full_text_pending": len(full_actionable),
        "title_stage_complete": title_stage_complete,
        "included": len(included),
        "flagged_for_manual_review": len(flagged),
        "calibration": calibration_summary(state),
        "definitions": dict(SCREENING_FIELD_DEFINITIONS),
        "compatibility": SCREENING_COMPATIBILITY_NOTE,
    }


def screening_section(state) -> str:
    """Markdown block for AGENT_HANDOFF.md / the Evidence Pack."""
    facts = screening_facts(state)
    lines = [
        "## Screening status",
        "",
        f"- screening_status: {facts['screening_status']} — "
        f"{SCREENING_FIELD_DEFINITIONS['screening_status']}",
        f"- threshold_calibrated: {facts['threshold_calibrated']} — "
        f"{SCREENING_FIELD_DEFINITIONS['threshold_calibrated']}",
        f"- requires_manual_review: {facts['requires_manual_review']} — "
        f"{SCREENING_FIELD_DEFINITIONS['requires_manual_review']}",
        f"- full_text_review_completed: {facts['full_text_review_completed']} — "
        f"{SCREENING_FIELD_DEFINITIONS['full_text_review_completed']}",
        "",
        f"Records: {facts['records_total']}; title/abstract decided: "
        f"{facts['title_abstract_decided']} (accepted {facts['title_abstract_accepted']}, "
        f"pending {facts['title_abstract_pending']}); full-text decided: "
        f"{facts['full_text_decided']} (pending {facts['full_text_pending']}); "
        f"included at full text: {facts['included']}.",
        "",
        ("Inclusion in this pack is **preliminary**: it reflects recorded screening "
         "decisions only. Full-text inclusion requires explicit human assessment, and "
         "uncalibrated relevance scores rank results rather than judging them."
         if not facts["full_text_review_completed"] else
         "The full-text stage is complete for every paper that passed title/abstract "
         "screening; inclusion still reflects human decisions, not an automatic rule."),
        "",
        f"Backwards compatibility: {SCREENING_COMPATIBILITY_NOTE}",
    ]
    return "\n".join(lines)


def apply_calibrated_screening(state, record) -> str:
    """Apply a calibrated threshold as *assistance*, and say what happened.

    Goes through the core API (``PRISMATracker.screen_by_calibrated_threshold``)
    so the refusals live in one place: no calibration, an ``insufficient_labels``
    record, a stale record, a queue mixing scoring batches, or the full-text
    stage all end without a single paper being rejected. Returns a
    human-readable note for the caller — never raises for a missing
    calibration, because "nothing was screened" is a valid outcome that has to
    be reported rather than an error.
    """
    from litsearch.filters import corpus_hash as _corpus_hash
    from litsearch.prisma import AutoScreeningRefused
    from litsearch.search import current_scored_corpus, ranking_query

    if not calibration_usable(record):
        return (
            "请求了 auto_screen=True，但本会话没有可用的标定阈值（CalibrationRecord "
            "status != 'calibrated'），因此没有自动排除任何文献，分数只用于排序。"
            "auto_screen was requested but no usable calibration exists for this session: "
            "nothing was rejected, scores only rank the candidates. "
            "Calibrate from labelled papers first, or keep screening manually."
        )
    try:
        changed = state.prisma.screen_by_calibrated_threshold(
            record,
            query=ranking_query(state),
            corpus_hash=_corpus_hash(current_scored_corpus(state)),
        )
    except AutoScreeningRefused as exc:
        return (
            f"标定阈值不可用于本次候选集，未做任何自动筛选：{exc} "
            f"Calibration refused by the core API, nothing was screened: {exc}"
        )
    note = (
        f"已按标定阈值 {float(record.threshold):.3f} 对标题/摘要阶段给出 {len(changed)} 条"
        "自动建议（可人工复核与撤销）。全文纳入仍需人工明确评估。"
        "Automatic title/abstract assistance applied with the calibrated threshold "
        f"({len(changed)} decisions, reversible by a reviewer); full-text inclusion still "
        "requires explicit human assessment."
    )
    if not getattr(record, "reliable", False):
        note += (
            " 标定两类样本有重叠，边界附近的判定已标记为需要人工复核。"
            " The calibration's classes overlap, so boundary decisions are flagged for review."
        )
    return note
