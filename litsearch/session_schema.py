"""Session-file schema validation, migration and corruption reporting.

Session JSON is user data that survives upgrades: a half-written file, a file
from an older release or a file produced by a newer one must never be loaded
blindly into :class:`~litsearch.search.ReviewState`.  This module is the single
place that decides whether a session document may be restored.

Contract (also the API ``litsearch/persistence.py`` calls):

* :data:`SESSION_SCHEMA_VERSION` — the schema this build writes and reads.
* :func:`validate_session_dict` — validate **and** migrate; returns a new dict at
  the current schema version.  Raises :class:`SessionSchemaError` with
  field-level errors (``{"field", "code", "message"}`` per problem).
* :func:`migrate_session_dict` — migration only, without validation.
* :func:`load_session_file` — read + validate a file; a corrupt or invalid file
  is backed up first and the original is left untouched.
* :func:`backup_corrupt_session` — copy a file aside
  (``<name>.corrupt-<utc>.bak``); never overwrites an existing backup.
* :exc:`SessionSchemaError` — ``str()`` always starts with
  :data:`ERROR_PREFIX` so a log line is recognisable.

A future schema version is **rejected**, not downgraded: silently dropping
fields a newer build wrote is how a user loses work.

Migration notes: versions 1 and 2 differ from 3 only in fields the current
reader already treats as optional (papers without identifier/discovery blocks,
PRISMA records without the stage-3 fields, snowball rounds without counters).
The migration fills exactly those defaults and normalises the two legacy
container shapes (``similar_papers`` entries and key-journal/author entries
written as objects instead of tuples).  Nothing is guessed: an unknown value
fails validation instead of being invented.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

#: Schema version written by ``persistence.state_to_dict`` (v4 adds the
#: calibration, the per-decision history/conflict trail and the count ledger).
SESSION_SCHEMA_VERSION = 4

#: Versions this build can migrate from.
SUPPORTED_PAST_VERSIONS = (1, 2, 3)

#: Prefix of every raised error message.
ERROR_PREFIX = "SESSION_SCHEMA_INVALID"

#: Documents larger than this are refused outright instead of being validated
#: entry by entry (a 10^7-record list is not a session, it is a disk bomb).
MAX_PAPERS = 50_000
MAX_RECORDS = 50_000
MAX_TEXT = 4096
MIN_YEAR = 1000
MAX_YEAR = 2200

_FALLBACK_PHASES = ("scoping", "systematic_search", "snowballing", "screening", "complete")
_DECISIONS = ("pending", "accept", "reject", "maybe")
_CALIBRATION_STATUSES = ("calibrated", "insufficient_labels")


def _phase_values() -> frozenset[str]:
    """Valid ``phase`` values, taken from the enum when it is importable."""
    try:  # pragma: no cover - the import works in the normal environment
        from litsearch.search import ReviewPhase

        return frozenset(phase.value for phase in ReviewPhase)
    except Exception:  # pragma: no cover - keep the module importable alone
        return frozenset(_FALLBACK_PHASES)


class SessionSchemaError(ValueError):
    """A session document that must not be loaded."""

    def __init__(self, errors: list[dict], *, path: str = "", message: str = ""):
        self.errors = list(errors)
        self.path = path
        summary = ERROR_PREFIX
        if path:
            summary += f": {path}"
        if message:
            summary += f": {message}"
        if self.errors:
            first = self.errors[0]
            summary += (f": {first.get('field') or '<document>'}: "
                        f"{first.get('code')}: {first.get('message')}")
            if len(self.errors) > 1:
                summary += f" (+{len(self.errors) - 1} more)"
        super().__init__(summary)

    def to_dict(self) -> dict:
        return {"error": ERROR_PREFIX, "path": self.path, "errors": self.errors}


def _error(errors: list[dict], field: str, code: str, message: str) -> None:
    errors.append({"field": field, "code": code, "message": message})


def _ok_text(errors: list[dict], value, field: str, *, allow_empty: bool = True) -> None:
    if not isinstance(value, str):
        _error(errors, field, "not_a_string", f"expected a string, found {type(value).__name__}")
    elif not allow_empty and not value.strip():
        _error(errors, field, "empty_identifier", "expected a non-empty identifier")
    elif len(value) > MAX_TEXT:
        _error(errors, field, "too_large", f"text longer than {MAX_TEXT} characters")


def _ok_number(errors: list[dict], value, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _error(errors, field, "not_a_number", f"expected a number, found {type(value).__name__}")
    elif not math.isfinite(value):
        _error(errors, field, "not_finite", "NaN and Infinity cannot be stored in a session")


def _ok_int(errors: list[dict], value, field: str, *, minimum: int | None = None) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        _error(errors, field, "not_finite", "NaN and Infinity cannot be stored in a session")
    elif isinstance(value, bool) or not isinstance(value, int):
        _error(errors, field, "not_an_integer", f"expected an integer, found {type(value).__name__}")
    elif minimum is not None and value < minimum:
        _error(errors, field, "out_of_range", f"must be >= {minimum}, found {value}")


def _ok_list(errors: list[dict], value, field: str, *, limit: int = MAX_PAPERS) -> bool:
    if not isinstance(value, list):
        _error(errors, field, "not_a_list", f"expected a list, found {type(value).__name__}")
        return False
    if len(value) > limit:
        _error(errors, field, "too_large", f"{len(value)} entries exceeds the limit of {limit}")
        return False
    return True


def _ok_dict(errors: list[dict], value, field: str) -> bool:
    if not isinstance(value, dict):
        _error(errors, field, "not_a_dict", f"expected an object, found {type(value).__name__}")
        return False
    return True


def _ok_str_list(errors: list[dict], value, field: str, *, limit: int = MAX_PAPERS) -> None:
    if not _ok_list(errors, value, field, limit=limit):
        return
    for index, item in enumerate(value):
        _ok_text(errors, item, f"{field}[{index}]")


def _validate_paper(errors: list[dict], paper, field: str) -> None:
    if not _ok_dict(errors, paper, field):
        return
    if "id" in paper:
        _ok_text(errors, paper["id"], f"{field}.id", allow_empty=False)
    if "canonical_id" in paper:
        _ok_text(errors, paper["canonical_id"], f"{field}.canonical_id")
    if "title" in paper:
        _ok_text(errors, paper["title"], f"{field}.title")
    if "abstract" in paper and paper["abstract"] is not None:
        _ok_text(errors, paper["abstract"], f"{field}.abstract")
    if paper.get("year") is not None:
        _ok_int(errors, paper["year"], f"{field}.year")
        plain_int = isinstance(paper["year"], int) and not isinstance(paper["year"], bool)
        if plain_int and not MIN_YEAR <= paper["year"] <= MAX_YEAR:
            _error(errors, f"{field}.year", "out_of_range",
                   f"year {paper['year']} is outside {MIN_YEAR}-{MAX_YEAR}")
    for name in ("citation_count", "reference_count"):
        if name in paper:
            _ok_int(errors, paper[name], f"{field}.{name}", minimum=0)
    if "relevance_score" in paper:
        _ok_number(errors, paper["relevance_score"], f"{field}.relevance_score")
    for name in ("citation_ids", "reference_ids", "topics"):
        if name in paper:
            _ok_str_list(errors, paper[name], f"{field}.{name}")
    if "authors" in paper and _ok_list(errors, paper["authors"], f"{field}.authors"):
        for index, author in enumerate(paper["authors"]):
            author_field = f"{field}.authors[{index}]"
            if not _ok_dict(errors, author, author_field):
                continue
            _ok_text(errors, author.get("name"), f"{author_field}.name")
    for name in ("identifiers", "score_breakdown", "topics_index"):
        if name in paper and _ok_dict(errors, paper[name], f"{field}.{name}"):
            if name == "score_breakdown":
                for key, value in paper[name].items():
                    _ok_number(errors, value, f"{field}.{name}.{key}")
            elif name == "identifiers":
                for key, value in paper[name].items():
                    if value is not None:
                        _ok_text(errors, value, f"{field}.{name}.{key}")
    if "discovery_traces" in paper and _ok_list(errors, paper["discovery_traces"],
                                                f"{field}.discovery_traces"):
        for index, trace in enumerate(paper["discovery_traces"]):
            trace_field = f"{field}.discovery_traces[{index}]"
            if _ok_dict(errors, trace, trace_field):
                _ok_text(errors, trace.get("method"), f"{trace_field}.method")


def _validate_pair_list(errors: list[dict], value, field: str, second: str) -> None:
    if not _ok_list(errors, value, field):
        return
    for index, entry in enumerate(value):
        entry_field = f"{field}[{index}]"
        if not isinstance(entry, (list, tuple)):
            _error(errors, entry_field, "invalid_shape", "expected a [name, value] pair")
            continue
        if len(entry) != 2:
            _error(errors, entry_field, "invalid_shape",
                   f"expected 2 elements, found {len(entry)}")
            continue
        _ok_text(errors, entry[0], f"{entry_field}[0]")
        if second == "int":
            _ok_int(errors, entry[1], f"{entry_field}[1]", minimum=0)
        else:
            _ok_number(errors, entry[1], f"{entry_field}[1]")


def _validate_similar_papers(errors: list[dict], value, field: str) -> None:
    if not _ok_list(errors, value, field):
        return
    for index, entry in enumerate(value):
        entry_field = f"{field}[{index}]"
        if not isinstance(entry, (list, tuple)):
            _error(errors, entry_field, "invalid_shape", "expected a [paper, score, method] triple")
            continue
        if len(entry) != 3:
            _error(errors, entry_field, "invalid_shape",
                   f"expected 3 elements, found {len(entry)}")
            continue
        _validate_paper(errors, entry[0], f"{entry_field}[0]")
        _ok_number(errors, entry[1], f"{entry_field}[1]")
        _ok_text(errors, entry[2], f"{entry_field}[2]")


def _validate_prisma(errors: list[dict], prisma, field: str) -> None:
    if not _ok_dict(errors, prisma, field):
        return
    for name in ("duplicates_seen",):
        if name in prisma:
            _ok_int(errors, prisma[name], f"{field}.{name}", minimum=0)
    if "identification_counts" in prisma and _ok_dict(
            errors, prisma["identification_counts"], f"{field}.identification_counts"):
        for key, value in prisma["identification_counts"].items():
            _ok_int(errors, value, f"{field}.identification_counts.{key}", minimum=0)
    if "papers" in prisma and _ok_dict(errors, prisma["papers"], f"{field}.papers"):
        if len(prisma["papers"]) > MAX_PAPERS:
            _error(errors, f"{field}.papers", "too_large",
                   f"{len(prisma['papers'])} entries exceeds the limit of {MAX_PAPERS}")
        else:
            for key, paper in prisma["papers"].items():
                _validate_paper(errors, paper, f"{field}.papers.{key}")
    if "records" in prisma and _ok_list(errors, prisma["records"], f"{field}.records",
                                        limit=MAX_RECORDS):
        for index, record in enumerate(prisma["records"]):
            record_field = f"{field}.records[{index}]"
            if not _ok_dict(errors, record, record_field):
                continue
            if "key" in record:
                _ok_text(errors, record["key"], f"{record_field}.key", allow_empty=False)
            for name in ("screening_decision", "full_text_decision"):
                if name in record and record[name] not in _DECISIONS:
                    _error(errors, f"{record_field}.{name}", "invalid_enum",
                           f"expected one of {', '.join(_DECISIONS)}")
            for name in ("full_text_retrieved",):
                if name in record and not isinstance(record[name], bool):
                    _error(errors, f"{record_field}.{name}", "not_a_boolean",
                           f"expected a boolean, found {type(record[name]).__name__}")
            if "relevance_score" in record:
                _ok_number(errors, record["relevance_score"], f"{record_field}.relevance_score")
            for name in ("source", "screening_reason", "full_text_reason", "score_context_id"):
                if name in record:
                    _ok_text(errors, record[name], f"{record_field}.{name}")
            for name in ("requires_manual_review",):
                if name in record and not isinstance(record[name], bool):
                    _error(errors, f"{record_field}.{name}", "not_a_boolean",
                           f"expected a boolean, found {type(record[name]).__name__}")
            for name in ("conflicts", "history"):
                if name in record and _ok_list(errors, record[name], f"{record_field}.{name}",
                                               limit=MAX_RECORDS):
                    for item_index, item in enumerate(record[name]):
                        item_field = f"{record_field}.{name}[{item_index}]"
                        if _ok_dict(errors, item, item_field):
                            for key, value in item.items():
                                if isinstance(value, str):
                                    _ok_text(errors, value, f"{item_field}.{key}")
                                elif isinstance(value, bool):
                                    continue
                                elif isinstance(value, (int, float)):
                                    _ok_number(errors, value, f"{item_field}.{key}")
    if "ledger" in prisma and _ok_dict(errors, prisma["ledger"], f"{field}.ledger"):
        for key, value in prisma["ledger"].items():
            if isinstance(value, bool):
                continue
            if isinstance(value, int):
                _ok_int(errors, value, f"{field}.ledger.{key}", minimum=0)
            elif isinstance(value, str):
                _ok_text(errors, value, f"{field}.ledger.{key}")
            elif isinstance(value, dict):
                _ok_dict(errors, value, f"{field}.ledger.{key}")
            elif isinstance(value, list):
                _ok_list(errors, value, f"{field}.ledger.{key}", limit=MAX_RECORDS)
            else:
                _ok_number(errors, value, f"{field}.ledger.{key}")


def _validate_calibration(errors: list[dict], calibration, field: str) -> None:
    if calibration is None:
        return
    if not _ok_dict(errors, calibration, field):
        return
    if "labels" in calibration and _ok_dict(errors, calibration["labels"], f"{field}.labels"):
        for key, value in calibration["labels"].items():
            _ok_int(errors, value, f"{field}.labels.{key}", minimum=0)
    for name in ("positive_count", "negative_count"):
        if name in calibration:
            _ok_int(errors, calibration[name], f"{field}.{name}", minimum=0)
    for name in ("query", "corpus_hash", "algorithm_version", "score_version", "created_at"):
        if name in calibration:
            _ok_text(errors, calibration[name], f"{field}.{name}")
    if calibration.get("threshold") is not None:
        _ok_number(errors, calibration["threshold"], f"{field}.threshold")
    if "evaluation" in calibration and _ok_dict(errors, calibration["evaluation"],
                                               f"{field}.evaluation"):
        for key, value in calibration["evaluation"].items():
            if isinstance(value, bool):
                continue
            if isinstance(value, str):
                _ok_text(errors, value, f"{field}.evaluation.{key}")
            else:
                _ok_number(errors, value, f"{field}.evaluation.{key}")
    if "status" in calibration and calibration["status"] not in _CALIBRATION_STATUSES:
        _error(errors, f"{field}.status", "invalid_enum",
               f"expected one of {', '.join(_CALIBRATION_STATUSES)}")


def _validate_snowball(errors: list[dict], snowball, field: str) -> None:
    if snowball is None:
        return
    if not _ok_dict(errors, snowball, field):
        return
    for name in ("initial_ids", "next_seed_ids"):
        if name in snowball:
            _ok_str_list(errors, snowball[name], f"{field}.{name}")
    for name in ("saturated", "completed"):
        if name in snowball and not isinstance(snowball[name], bool):
            _error(errors, f"{field}.{name}", "not_a_boolean",
                   f"expected a boolean, found {type(snowball[name]).__name__}")
    if "parameters" in snowball:
        _ok_dict(errors, snowball["parameters"], f"{field}.parameters")
    if "all_papers" in snowball and _ok_dict(errors, snowball["all_papers"], f"{field}.all_papers"):
        for key, paper in snowball["all_papers"].items():
            _validate_paper(errors, paper, f"{field}.all_papers.{key}")
    if "rounds" in snowball and _ok_list(errors, snowball["rounds"], f"{field}.rounds"):
        for index, round_ in enumerate(snowball["rounds"]):
            round_field = f"{field}.rounds[{index}]"
            if not _ok_dict(errors, round_, round_field):
                continue
            _ok_int(errors, round_.get("round_number"), f"{round_field}.round_number", minimum=1)
            _ok_text(errors, round_.get("direction"), f"{round_field}.direction")
            for name in ("raw_count", "unique_count", "relevant_count", "cumulative_unique"):
                if name in round_:
                    _ok_int(errors, round_[name], f"{round_field}.{name}", minimum=0)
            if "new_papers" in round_ and _ok_list(errors, round_["new_papers"],
                                                   f"{round_field}.new_papers"):
                for paper_index, paper in enumerate(round_["new_papers"]):
                    _validate_paper(errors, paper, f"{round_field}.new_papers[{paper_index}]")
            if "source_papers" in round_:
                _ok_str_list(errors, round_["source_papers"], f"{round_field}.source_papers")


def _validate_document(data) -> list[dict]:
    errors: list[dict] = []
    if not isinstance(data, dict):
        _error(errors, "", "not_an_object", f"expected a JSON object, found {type(data).__name__}")
        return errors
    # The schema version is checked before migration (see `_check_version`); by
    # the time a document reaches this function it is at the current version.
    for name in ("topic", "research_direction"):
        if name in data:
            _ok_text(errors, data[name], name)
    if "phase" in data and data["phase"] not in _phase_values():
        _error(errors, "phase", "invalid_enum",
               f"expected one of {', '.join(sorted(_phase_values()))}")
    for name in ("saved_at", "start_time"):
        if name in data:
            _ok_number(errors, data[name], name)
    if "saved_at" in data and isinstance(data["saved_at"], (int, float)) \
            and not isinstance(data["saved_at"], bool) and math.isfinite(data["saved_at"]) \
            and data["saved_at"] < 0:
        _error(errors, "saved_at", "out_of_range", "saved_at must not be negative")
    for name in ("scoping_papers", "search_papers"):
        if name in data and _ok_list(errors, data[name], name):
            for index, paper in enumerate(data[name]):
                _validate_paper(errors, paper, f"{name}[{index}]")
    if "similar_papers" in data:
        _validate_similar_papers(errors, data["similar_papers"], "similar_papers")
    for name in ("key_journals", "key_authors"):
        if name in data:
            _validate_pair_list(errors, data[name], name, "int")
    if "topic_clusters" in data and _ok_dict(errors, data["topic_clusters"], "topic_clusters"):
        for key, value in data["topic_clusters"].items():
            _ok_str_list(errors, value, f"topic_clusters.{key}")
    if "year_distribution" in data and _ok_dict(errors, data["year_distribution"],
                                                "year_distribution"):
        for key, value in data["year_distribution"].items():
            if not (isinstance(key, str) and key.isdigit()):
                _error(errors, f"year_distribution.{key}", "invalid_key",
                       "expected a four-digit year as the key")
            _ok_int(errors, value, f"year_distribution.{key}", minimum=0)
    if "search_manifest" in data:
        _ok_dict(errors, data["search_manifest"], "search_manifest")
    if "calibration" in data:
        _validate_calibration(errors, data["calibration"], "calibration")
    if "prisma" in data:
        _validate_prisma(errors, data["prisma"], "prisma")
    if "snowball_result" in data:
        _validate_snowball(errors, data["snowball_result"], "snowball_result")
    return errors


# ---------------------------------------------------------------------------
# migration
# ---------------------------------------------------------------------------


def _migrate_similar_papers(data: dict, warnings: list[dict]) -> None:
    entries = data.get("similar_papers")
    if not isinstance(entries, list):
        return
    for index, entry in enumerate(entries):
        if isinstance(entry, dict) and "paper" in entry:
            entries[index] = [entry["paper"], entry.get("score", 0.0),
                              entry.get("method", "unknown")]
            warnings.append({"code": "migrated", "field": f"similar_papers[{index}]",
                             "message": "object entry converted to a [paper, score, method] triple"})


def _migrate_pairs(data: dict, name: str, warnings: list[dict]) -> None:
    entries = data.get(name)
    if not isinstance(entries, list):
        return
    for index, entry in enumerate(entries):
        if isinstance(entry, dict) and ("name" in entry or "count" in entry):
            entries[index] = [entry.get("name", ""), entry.get("count", 0)]
            warnings.append({"code": "migrated", "field": f"{name}[{index}]",
                             "message": "object entry converted to a [name, count] pair"})


def _migrate_v1_to_v2(data: dict, warnings: list[dict]) -> None:
    _migrate_similar_papers(data, warnings)
    _migrate_pairs(data, "key_journals", warnings)
    _migrate_pairs(data, "key_authors", warnings)
    data.setdefault("search_manifest", {})
    data.setdefault("saved_at", 0.0)
    data.setdefault("start_time", 0.0)
    data.setdefault("topic_clusters", {})
    data.setdefault("year_distribution", {})


def _migrate_v2_to_v3(data: dict, warnings: list[dict]) -> None:
    """Fill the fields v3 readers expect but v2 writers did not emit."""
    prisma = data.get("prisma")
    if isinstance(prisma, dict):
        for index, record in enumerate(prisma.get("records") or []):
            if not isinstance(record, dict):
                continue
            for name, default in (("full_text_retrieved", False),
                                  ("full_text_decision", "pending"),
                                  ("full_text_reason", ""),
                                  ("screening_reason", ""),
                                  ("relevance_score", 0.0),
                                  ("source", "")):
                if name not in record:
                    record[name] = default
                    warnings.append({"code": "migrated", "field": f"prisma.records[{index}].{name}",
                                     "message": f"defaulted to {default!r}"})
        prisma.setdefault("duplicates_seen", 0)
        prisma.setdefault("identification_counts", {})
        prisma.setdefault("full_text_stage_enabled", False)
        prisma.setdefault("papers", {})
    snowball = data.get("snowball_result")
    if isinstance(snowball, dict):
        snowball.setdefault("parameters", {})
        snowball.setdefault("initial_ids", [])
        snowball.setdefault("next_seed_ids", [])
        snowball.setdefault("completed", True)
        for index, round_ in enumerate(snowball.get("rounds") or []):
            if not isinstance(round_, dict):
                continue
            new_papers = round_.get("new_papers") or []
            for name, default in (("round_number", index + 1),
                                  ("direction", "both"),
                                  ("raw_count", len(new_papers)),
                                  ("unique_count", len(new_papers)),
                                  ("relevant_count", 0),
                                  ("cumulative_unique", 0),
                                  ("source_papers", [])):
                if name not in round_:
                    round_[name] = default
                    warnings.append({"code": "migrated",
                                     "field": f"snowball_result.rounds[{index}].{name}",
                                     "message": f"defaulted to {default!r}"})
    for name in ("scoping_papers", "search_papers"):
        for index, paper in enumerate(data.get(name) or []):
            _migrate_paper(paper, f"{name}[{index}]", warnings)
    for index, entry in enumerate(data.get("similar_papers") or []):
        if isinstance(entry, (list, tuple)) and entry:
            _migrate_paper(entry[0], f"similar_papers[{index}][0]", warnings)


def _migrate_paper(paper, field: str, warnings: list[dict]) -> None:
    if not isinstance(paper, dict):
        return
    for name, default in (("identifiers", {}), ("discovery_traces", []), ("score_breakdown", {})):
        if name not in paper:
            paper[name] = default
            warnings.append({"code": "migrated", "field": f"{field}.{name}",
                             "message": f"defaulted to {default!r}"})


def _migrate_v3_to_v4(data: dict, warnings: list[dict]) -> None:
    """Fill the v4 fields: calibration placeholder + per-decision audit trail.

    The count ledger's internal shape is owned by ``prisma.py``; a missing ledger
    is left to that module's own defaults instead of being guessed here.
    """
    if "calibration" not in data:
        data["calibration"] = None
        warnings.append({"code": "migrated", "field": "calibration",
                         "message": "defaulted to None (no calibration was recorded)"})
    prisma = data.get("prisma")
    if isinstance(prisma, dict):
        for index, record in enumerate(prisma.get("records") or []):
            if not isinstance(record, dict):
                continue
            for name, default in (("score_context_id", ""),
                                  ("requires_manual_review", False),
                                  ("conflicts", []),
                                  ("history", [])):
                if name not in record:
                    record[name] = default
                    warnings.append({"code": "migrated",
                                     "field": f"prisma.records[{index}].{name}",
                                     "message": f"defaulted to {default!r}"})


_MIGRATIONS = {1: _migrate_v1_to_v2, 2: _migrate_v2_to_v3, 3: _migrate_v3_to_v4}


def schema_version_of(data) -> int:
    """The schema version a document declares (missing ⇒ 1, the legacy shape)."""
    if not isinstance(data, dict):
        raise SessionSchemaError([{"field": "", "code": "not_an_object",
                                   "message": f"expected a JSON object, found {type(data).__name__}"}])
    version = data.get("version")
    if version is None:
        return 1
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise SessionSchemaError([{"field": "version", "code": "invalid_version",
                                   "message": f"invalid schema version {version!r}"}])
    return version


def migrate_session_dict(data, *, warnings: list[dict] | None = None) -> dict:
    """Return a copy of ``data`` at :data:`SESSION_SCHEMA_VERSION`.

    Only known older versions are migrated.  A future version raises instead of
    being downgraded.
    """
    notes = warnings if warnings is not None else []
    version = schema_version_of(data)
    if version > SESSION_SCHEMA_VERSION:
        raise SessionSchemaError([{
            "field": "version", "code": "future_version",
            "message": (f"session schema version {version} was written by a newer release "
                        f"(this build supports up to {SESSION_SCHEMA_VERSION})")}])
    migrated = deepcopy(data)
    if version == 1 and "version" not in data:
        notes.append({"code": "legacy_without_version", "field": "version",
                      "message": "no version field: treated as the legacy v1 schema"})
    for step in range(version, SESSION_SCHEMA_VERSION):
        _MIGRATIONS[step](migrated, notes)
    migrated["version"] = SESSION_SCHEMA_VERSION
    return migrated


def _check_version(errors: list[dict], data: dict) -> int | None:
    """Validate the declared schema version; returns it (missing ⇒ legacy v1)."""
    if "version" not in data:
        return 1
    version = data["version"]
    if version is None or isinstance(version, bool) or not isinstance(version, int):
        _error(errors, "version", "invalid_version",
               f"expected an integer schema version, found {version!r}")
        return None
    if version < 1:
        _error(errors, "version", "invalid_version", f"schema version {version} is not valid")
        return None
    if version > SESSION_SCHEMA_VERSION:
        _error(errors, "version", "future_version",
               f"session schema version {version} was written by a newer release "
               f"(this build supports up to {SESSION_SCHEMA_VERSION}); refusing to "
               f"load and silently drop fields")
        return None
    return version


def validate_session_dict(data, *, warnings: list[dict] | None = None) -> dict:
    """Validate **and** migrate a session document.

    The version is checked first — a future version is refused, never
    downgraded — then known older versions are migrated to the current shape and
    the migrated document is validated field by field.  Returns a new dict at
    :data:`SESSION_SCHEMA_VERSION`; raises :class:`SessionSchemaError` carrying
    one entry per field-level problem.
    """
    notes = warnings if warnings is not None else []
    if not isinstance(data, dict):
        raise SessionSchemaError([{"field": "", "code": "not_an_object",
                                   "message": f"expected a JSON object, found {type(data).__name__}"}])
    version_errors: list[dict] = []
    _check_version(version_errors, data)
    if version_errors:
        raise SessionSchemaError(version_errors)
    migrated = migrate_session_dict(data, warnings=notes)
    errors = _validate_document(migrated)
    if errors:
        raise SessionSchemaError(errors)
    return migrated


# ---------------------------------------------------------------------------
# file access
# ---------------------------------------------------------------------------


def backup_corrupt_session(path: str, *, reason: str = "") -> str:
    """Copy a corrupt session aside; the original is left untouched.

    Returns the backup path.  A backup is never overwritten: an existing name is
    extended with a counter.
    """
    source = Path(path)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    target = source.with_name(f"{source.name}.corrupt-{stamp}.bak")
    counter = 1
    while target.exists():
        target = source.with_name(f"{source.name}.corrupt-{stamp}-{counter}.bak")
        counter += 1
    shutil.copy2(source, target)
    return str(target)


def load_session_file(path: str, *, warnings: list[dict] | None = None) -> dict:
    """Read, validate and migrate a session file.

    On a corrupt or schema-invalid file the original is backed up, the file
    itself is never modified, and :class:`SessionSchemaError` is raised so the
    caller can keep the current session and its autosave intact.
    """
    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise SessionSchemaError([{"field": "", "code": "unreadable", "message": str(exc)}],
                                 path=str(source)) from exc
    try:
        data = json.loads(text)
    except ValueError as exc:
        backup_corrupt_session(str(source), reason="invalid_json")
        raise SessionSchemaError([{"field": "", "code": "invalid_json",
                                   "message": f"not valid JSON: {exc}"}],
                                 path=str(source)) from exc
    try:
        return validate_session_dict(data, warnings=warnings)
    except SessionSchemaError:
        backup_corrupt_session(str(source), reason="schema_invalid")
        raise


# ---------------------------------------------------------------------------
# Project isolation (spec H4)
# ---------------------------------------------------------------------------
#
# Several browser tabs share one process and, before this, one autosave file:
# the last writer silently won.  A project id, a per-project path and a write
# conflict check make that visible instead of destructive.

PROJECTS_DIRNAME = "projects"
AUTOSAVE_NAME = "autosave.json"


def project_id_for(topic: str, research_direction: str = "", *, explicit: str = "") -> str:
    """A stable, filename-safe id for one review project.

    An explicit id is sanitised (so it can never escape the session directory);
    otherwise the id is derived from the topic + direction, which keeps the same
    project in the same place across restarts.
    """
    if explicit:
        cleaned = sanitize_project_id(explicit)
        if cleaned:
            return cleaned
    seed = f"{(topic or '').strip()}\n{(research_direction or '').strip()}".encode()
    return hashlib.sha256(seed).hexdigest()[:12]


def sanitize_project_id(value: str) -> str:
    """Keep a project id to ``[A-Za-z0-9._-]`` and bound its length.

    ``..`` / separators would otherwise let a caller pick an arbitrary path.
    """
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip())
    cleaned = cleaned.strip("-.")
    return cleaned[:64]


def project_session_dir(session_dir: str, project_id: str) -> str:
    """``<session_dir>/projects/<project_id>`` — one directory per project."""
    return os.path.join(session_dir, PROJECTS_DIRNAME, sanitize_project_id(project_id) or "default")


def project_session_path(session_dir: str, project_id: str, name: str = "") -> str:
    """Where a project's session lives: its own directory, its own autosave.

    A named session becomes ``<project>/<slug>.json``; without a name it is the
    project's ``autosave.json``.  Two projects can therefore never overwrite
    each other's autosave.
    """
    directory = project_session_dir(session_dir, project_id)
    if not name:
        return os.path.join(directory, AUTOSAVE_NAME)
    slug = re.sub(r"[^\w]+", "_", str(name).strip().lower()).strip("_")[:40] or "session"
    return os.path.join(directory, f"{slug}.json")


def _session_header(path: str) -> tuple[float | None, str]:
    """``(saved_at, project_id)`` of an existing file, or ``(None, "")``."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None, ""
    if not isinstance(data, dict):
        return None, ""
    saved_at = data.get("saved_at")
    if isinstance(saved_at, bool) or not isinstance(saved_at, (int, float)):
        saved_at = None
    return saved_at, str(data.get("project_id") or "")


def detect_write_conflict(path: str, *, project_id: str = "",
                          loaded_saved_at: float | None = None,
                          loaded_project_id: str = "") -> dict:
    """Report whether writing ``path`` would clobber another session's work.

    A conflict is reported when the file on disk belongs to a different project,
    when it is unreadable, or when it was saved after the copy this process
    loaded.  The caller decides what to do (usually: keep both, do not
    overwrite) — this function never writes.
    """
    result = {"path": str(path), "conflict": False, "reason": "",
              "existing_saved_at": None, "existing_project_id": ""}
    if not path or not os.path.exists(str(path)):
        return result
    existing_saved_at, existing_project_id = _session_header(str(path))
    result["existing_saved_at"] = existing_saved_at
    result["existing_project_id"] = existing_project_id
    if existing_saved_at is None and not existing_project_id:
        result["conflict"] = True
        result["reason"] = "unreadable_existing_session"
        return result
    if project_id and existing_project_id and existing_project_id != project_id:
        result["conflict"] = True
        result["reason"] = "different_project"
        return result
    if loaded_project_id and existing_project_id and existing_project_id != loaded_project_id:
        result["conflict"] = True
        result["reason"] = "different_project"
        return result
    if loaded_saved_at is not None and existing_saved_at is not None \
            and existing_saved_at > float(loaded_saved_at) + 1e-6:
        result["conflict"] = True
        result["reason"] = "saved_later_by_another_session"
    return result


def conflict_copy_path(path: str) -> str:
    """A side path to write our version to when a conflict was detected."""
    source = Path(path)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    target = source.with_name(f"{source.stem}.conflict-{stamp}{source.suffix}")
    counter = 1
    while target.exists():
        target = source.with_name(f"{source.stem}.conflict-{stamp}-{counter}{source.suffix}")
        counter += 1
    return str(target)
