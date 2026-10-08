"""Session schema validation regressions (spec H2/H3, contract §11).

The validator lives in ``litsearch/session_schema.py`` (a module this task owns)
so that ``litsearch/persistence.py`` can call it without a second owner editing
the same file.  Everything here runs offline.
"""

from __future__ import annotations

import itertools
import json
import re
import shutil
from pathlib import Path

import pytest

from litsearch.session_schema import (
    ERROR_PREFIX,
    MAX_PAPERS,
    SESSION_SCHEMA_VERSION,
    SessionSchemaError,
    backup_corrupt_session,
    load_session_file,
    schema_version_of,
    validate_session_dict,
)

_TMP_ROOT = Path(__file__).resolve().parent / "_workspace_tmp"
_COUNTER = itertools.count()


@pytest.fixture()
def workdir():
    path = _TMP_ROOT / f"session_{next(_COUNTER)}"
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def valid_session() -> dict:
    """The shape ``persistence.state_to_dict`` currently writes."""
    return {
        "version": SESSION_SCHEMA_VERSION,
        "saved_at": 1_700_000_000.0,
        "topic": "plant phenotyping",
        "research_direction": "deep learning for plant phenotyping",
        "phase": "scoping",
        "start_time": 1_699_999_000.0,
        "scoping_papers": [],
        "topic_clusters": {"0": ["a"]},
        "key_journals": [["Nature", 3]],
        "key_authors": [["Smith", 2]],
        "year_distribution": {"2020": 5},
        "search_papers": [],
        "similar_papers": [],
        "snowball_result": None,
        "prisma": {"records": [], "papers": {}, "duplicates_seen": 0,
                   "identification_counts": {}, "full_text_stage_enabled": False},
        "search_manifest": {},
        "calibration": None,
    }


def paper_dict(**kwargs) -> dict:
    base = {
        "id": "10.1/x", "title": "A paper", "abstract": None,
        "authors": [{"name": "Smith", "author_id": None}],
        "year": 2020, "venue": "Nature", "doi": "10.1/x", "citation_count": 5,
        "reference_count": 10, "citation_ids": [], "reference_ids": [],
        "source": "openalex", "url": None, "relevance_score": 0.5, "topics": [],
        "canonical_id": "10.1/x", "identifiers": {}, "discovery_traces": [],
        "score_breakdown": {},
    }
    base.update(kwargs)
    return base


def errors_of(data) -> list[dict]:
    with pytest.raises(SessionSchemaError) as info:
        validate_session_dict(data)
    return info.value.errors


def codes_for(errors, field):
    return [e["code"] for e in errors if e["field"] == field]


# ---------------------------------------------------------------------------
# version handling
# ---------------------------------------------------------------------------


def test_current_version_validates_and_is_returned_unchanged():
    data = valid_session()
    assert validate_session_dict(data)["version"] == SESSION_SCHEMA_VERSION
    assert schema_version_of(data) == SESSION_SCHEMA_VERSION


def test_unknown_future_version_is_rejected_with_a_field_error():
    data = valid_session()
    data["version"] = SESSION_SCHEMA_VERSION + 1
    errors = errors_of(data)
    assert codes_for(errors, "version") == ["future_version"]
    assert all("field" in e and "code" in e and "message" in e for e in errors)


@pytest.mark.parametrize("bad", ["3", 3.5, True, None, -1, 0])
def test_non_integer_or_out_of_range_versions_are_rejected(bad):
    data = valid_session()
    data["version"] = bad
    errors = errors_of(data)
    assert errors, f"{bad!r} must not be accepted as a schema version"
    assert errors[0]["field"] == "version"


def test_missing_version_is_treated_as_legacy_v1_and_migrated():
    data = valid_session()
    del data["version"]
    warnings: list[dict] = []
    migrated = validate_session_dict(data, warnings=warnings)
    assert migrated["version"] == SESSION_SCHEMA_VERSION
    assert any(w["code"] == "legacy_without_version" for w in warnings)


def test_a_future_version_is_never_silently_downgraded():
    data = valid_session()
    data["version"] = 99
    with pytest.raises(SessionSchemaError) as info:
        validate_session_dict(data)
    assert "future" in str(info.value)


# ---------------------------------------------------------------------------
# field-level validation
# ---------------------------------------------------------------------------


def test_errors_name_the_exact_field():
    data = valid_session()
    data["search_papers"] = [paper_dict(relevance_score="high", year=12345)]
    errors = errors_of(data)
    assert codes_for(errors, "search_papers[0].relevance_score") == ["not_a_number"]
    assert codes_for(errors, "search_papers[0].year") == ["out_of_range"]


def test_nan_and_infinity_are_rejected():
    data = valid_session()
    data["saved_at"] = float("nan")
    data["search_papers"] = [paper_dict(relevance_score=float("inf"),
                                        citation_count=float("nan"))]
    errors = errors_of(data)
    assert codes_for(errors, "saved_at") == ["not_finite"]
    assert codes_for(errors, "search_papers[0].relevance_score") == ["not_finite"]
    assert codes_for(errors, "search_papers[0].citation_count") == ["not_finite"]


def test_negative_counts_and_wrong_types_are_rejected():
    data = valid_session()
    data["search_papers"] = [paper_dict(citation_count=-3, reference_count="many",
                                        title=None, id="")]
    errors = errors_of(data)
    assert "out_of_range" in codes_for(errors, "search_papers[0].citation_count")
    assert "not_an_integer" in codes_for(errors, "search_papers[0].reference_count")
    assert "not_a_string" in codes_for(errors, "search_papers[0].title")
    assert "empty_identifier" in codes_for(errors, "search_papers[0].id")


def test_enum_and_shape_validation():
    data = valid_session()
    data["phase"] = "finished"
    data["similar_papers"] = [["not-a-paper", 0.5]]
    data["key_journals"] = [["only-one-element"]]
    data["year_distribution"] = {"2020": "five"}
    errors = errors_of(data)
    assert codes_for(errors, "phase") == ["invalid_enum"]
    assert "invalid_shape" in codes_for(errors, "similar_papers[0]")
    assert "invalid_shape" in codes_for(errors, "key_journals[0]")
    assert "not_an_integer" in codes_for(errors, "year_distribution.2020")


def test_container_type_errors():
    data = valid_session()
    data["search_papers"] = {"not": "a list"}
    data["prisma"] = "nope"
    data["topic_clusters"] = ["nope"]
    errors = errors_of(data)
    assert codes_for(errors, "search_papers") == ["not_a_list"]
    assert codes_for(errors, "prisma") == ["not_a_dict"]
    assert codes_for(errors, "topic_clusters") == ["not_a_dict"]


def test_a_non_object_document_is_rejected():
    errors = errors_of(["not", "an", "object"])
    assert errors[0]["code"] == "not_an_object"


def test_absurdly_large_lists_are_rejected_with_one_error():
    data = valid_session()
    data["search_papers"] = [{}] * (MAX_PAPERS + 1)
    errors = errors_of(data)
    assert codes_for(errors, "search_papers") == ["too_large"]


def test_the_error_is_recognisable_and_carries_every_field():
    data = valid_session()
    data["phase"] = "nope"
    with pytest.raises(SessionSchemaError) as info:
        validate_session_dict(data)
    assert str(info.value).startswith(ERROR_PREFIX)
    payload = info.value.to_dict()
    assert payload["error"] == ERROR_PREFIX
    assert payload["errors"][0]["field"] == "phase"


def test_valid_data_is_not_mutated_in_place():
    data = valid_session()
    snapshot = json.dumps(data, sort_keys=True)
    validate_session_dict(data)
    assert json.dumps(data, sort_keys=True) == snapshot


# ---------------------------------------------------------------------------
# migration of known older versions
# ---------------------------------------------------------------------------


def test_v1_session_is_migrated_to_the_current_shape():
    legacy = {
        "topic": "old topic",
        "research_direction": "old direction",
        "phase": "systematic_search",
        "similar_papers": [{"paper": paper_dict(), "score": 0.4, "method": "tfidf"}],
        "key_journals": [{"name": "Nature", "count": 3}],
        "key_authors": [{"name": "Smith", "count": 2}],
        "year_distribution": {"2020": 5},
    }
    warnings: list[dict] = []
    migrated = validate_session_dict(legacy, warnings=warnings)
    assert migrated["version"] == SESSION_SCHEMA_VERSION
    assert migrated["similar_papers"][0][1:] == [0.4, "tfidf"]
    assert migrated["key_journals"] == [["Nature", 3]]
    assert migrated["key_authors"] == [["Smith", 2]]
    assert migrated["search_manifest"] == {}
    assert any(w["code"] == "legacy_without_version" for w in warnings)


def test_v2_session_gains_the_v3_defaults():
    legacy = valid_session()
    legacy["version"] = 2
    legacy["prisma"] = {
        "records": [{"key": "10.1/x", "source": "database_search",
                     "screening_decision": "accept"}],
        "papers": {"10.1/x": paper_dict()},
    }
    legacy["snowball_result"] = {
        "saturated": False, "initial_ids": ["10.1/x"],
        "rounds": [{"round_number": 1, "new_papers": [paper_dict()]}],
        "all_papers": {},
    }
    warnings: list[dict] = []
    migrated = validate_session_dict(legacy, warnings=warnings)
    record = migrated["prisma"]["records"][0]
    assert record["full_text_retrieved"] is False
    assert record["full_text_decision"] == "pending"
    assert record["full_text_reason"] == ""
    assert record["relevance_score"] == 0.0
    round_ = migrated["snowball_result"]["rounds"][0]
    # Reconstructed from the data actually present: the round's new papers.
    assert round_["unique_count"] == 1
    assert round_["raw_count"] == 1
    # Counters that cannot be reconstructed stay 0 rather than being invented.
    assert round_["relevant_count"] == 0
    assert round_["cumulative_unique"] == 0
    assert any(w["code"] == "migrated" for w in warnings)


def test_unknown_keys_are_preserved_for_forward_compatibility():
    data = valid_session()
    data["future_field"] = {"keep": "me"}
    migrated = validate_session_dict(data)
    assert migrated["future_field"] == {"keep": "me"}


def test_v3_session_gains_the_v4_fields():
    legacy = valid_session()
    legacy["version"] = 3
    legacy.pop("calibration")
    legacy["prisma"]["records"] = [{"key": "10.1/x", "source": "database_search",
                                    "screening_decision": "accept",
                                    "screening_reason": "", "full_text_retrieved": False,
                                    "full_text_decision": "pending", "full_text_reason": "",
                                    "relevance_score": 0.0}]
    warnings: list[dict] = []
    migrated = validate_session_dict(legacy, warnings=warnings)
    assert migrated["calibration"] is None
    record = migrated["prisma"]["records"][0]
    assert record["requires_manual_review"] is False
    assert record["conflicts"] == []
    assert record["history"] == []
    assert record["score_context_id"] == ""
    assert any(w["code"] == "migrated" for w in warnings)


def test_calibration_is_validated_field_by_field():
    data = valid_session()
    data["calibration"] = {"labels": {"relevant": "two"}, "positive_count": -1,
                           "threshold": float("inf"), "status": "certain"}
    errors = errors_of(data)
    assert codes_for(errors, "calibration.labels.relevant") == ["not_an_integer"]
    assert codes_for(errors, "calibration.positive_count") == ["out_of_range"]
    assert codes_for(errors, "calibration.threshold") == ["not_finite"]
    assert codes_for(errors, "calibration.status") == ["invalid_enum"]


def test_a_real_calibration_record_round_trips_through_the_validator():
    from litsearch.filters import CalibrationRecord

    record = CalibrationRecord(labels={"relevant": 3, "irrelevant": 3}, positive_count=3,
                               negative_count=3, query="plant phenotyping", corpus_hash="abc",
                               threshold=0.3, evaluation={"separation": True, "margin": 0.2,
                                                          "needs_manual_review": 1},
                               status="calibrated")
    data = valid_session()
    data["calibration"] = record.to_dict()
    assert validate_session_dict(data)["calibration"]["status"] == "calibrated"


# ---------------------------------------------------------------------------
# H3: failed import keeps the original + autosave, backs up the corrupt file
# ---------------------------------------------------------------------------


def test_load_valid_file_returns_it(workdir):
    path = workdir / "session.json"
    path.write_text(json.dumps(valid_session()), encoding="utf-8")
    assert load_session_file(str(path))["topic"] == "plant phenotyping"


def test_corrupt_json_is_backed_up_and_reported(workdir):
    autosave = workdir / "autosave.json"
    autosave.write_text(json.dumps(valid_session()), encoding="utf-8")
    autosave_before = autosave.read_bytes()
    broken = workdir / "saved.json"
    broken.write_text('{"topic": "half a fi', encoding="utf-8")
    broken_before = broken.read_bytes()

    with pytest.raises(SessionSchemaError) as info:
        load_session_file(str(broken))
    assert str(info.value).startswith(ERROR_PREFIX)
    assert info.value.errors[0]["code"] == "invalid_json"

    assert broken.read_bytes() == broken_before, "the corrupt file must not be modified"
    backups = list(workdir.glob("saved.json.corrupt-*.bak"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == broken_before
    assert autosave.read_bytes() == autosave_before, "the autosave must stay intact"


def test_schema_invalid_file_is_backed_up_with_field_level_errors(workdir):
    path = workdir / "saved.json"
    data = valid_session()
    data["version"] = SESSION_SCHEMA_VERSION + 10
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(SessionSchemaError) as info:
        load_session_file(str(path))
    assert info.value.errors[0]["field"] == "version"
    assert info.value.errors[0]["code"] == "future_version"
    assert list(workdir.glob("saved.json.corrupt-*.bak"))


def test_backup_never_overwrites_an_existing_backup(workdir):
    path = workdir / "saved.json"
    path.write_text("{}", encoding="utf-8")
    first = backup_corrupt_session(str(path), reason="test")
    second = backup_corrupt_session(str(path), reason="test")
    assert first != second
    assert Path(first).exists() and Path(second).exists()


def test_a_failed_import_does_not_touch_the_autosave_or_other_sessions(workdir):
    autosave = workdir / "autosave.json"
    autosave.write_text(json.dumps(valid_session()), encoding="utf-8")
    other = workdir / "other.json"
    other.write_text(json.dumps(valid_session()), encoding="utf-8")
    broken = workdir / "broken.json"
    broken.write_text("not json at all", encoding="utf-8")
    before = {p.name: p.read_bytes() for p in workdir.glob("*.json")}
    with pytest.raises(SessionSchemaError):
        load_session_file(str(broken))
    after = {p.name: p.read_bytes() for p in workdir.glob("*.json")}
    assert after == before


# ---------------------------------------------------------------------------
# H4: project id, per-project path, write-conflict detection
# ---------------------------------------------------------------------------


def test_project_id_is_stable_and_project_specific():
    from litsearch.session_schema import project_id_for

    first = project_id_for("plant phenotyping", "deep learning")
    assert first == project_id_for("plant phenotyping", "deep learning")
    assert first != project_id_for("plant phenotyping", "other direction")
    assert first != project_id_for("another topic", "deep learning")
    assert re.fullmatch(r"[A-Za-z0-9._-]{1,64}", first)


def test_explicit_project_id_cannot_escape_the_session_directory():
    from litsearch.session_schema import project_id_for, project_session_path

    unsafe = project_id_for("t", "d", explicit="../../etc/passwd")
    assert "/" not in unsafe and "\\" not in unsafe and ".." not in unsafe
    path = project_session_path("C:/sessions", unsafe)
    assert ".." not in path.split("projects")[-1]


def test_each_project_gets_its_own_autosave_path():
    from litsearch.session_schema import project_session_path

    a = project_session_path("sessions", "alpha")
    b = project_session_path("sessions", "beta")
    assert a != b
    assert a.endswith("autosave.json") and b.endswith("autosave.json")
    assert "projects" in a and "alpha" in a
    named = project_session_path("sessions", "alpha", name="My Topic / 2026")
    assert named.endswith("my_topic_2026.json")
    assert ".." not in named


def test_no_conflict_when_nobody_else_wrote(workdir):
    from litsearch.session_schema import detect_write_conflict

    path = workdir / "autosave.json"
    assert detect_write_conflict(str(path), project_id="p1")["conflict"] is False
    path.write_text(json.dumps({"saved_at": 100.0, "project_id": "p1"}), encoding="utf-8")
    report = detect_write_conflict(str(path), project_id="p1", loaded_saved_at=100.0)
    assert report["conflict"] is False
    assert report["existing_saved_at"] == 100.0


def test_a_later_save_by_another_session_is_reported(workdir):
    from litsearch.session_schema import detect_write_conflict

    path = workdir / "autosave.json"
    path.write_text(json.dumps({"saved_at": 200.0, "project_id": "p1"}), encoding="utf-8")
    report = detect_write_conflict(str(path), project_id="p1", loaded_saved_at=100.0)
    assert report["conflict"] is True
    assert report["reason"] == "saved_later_by_another_session"


def test_a_different_project_in_the_same_file_is_reported(workdir):
    from litsearch.session_schema import detect_write_conflict

    path = workdir / "autosave.json"
    path.write_text(json.dumps({"saved_at": 100.0, "project_id": "other"}), encoding="utf-8")
    report = detect_write_conflict(str(path), project_id="mine", loaded_saved_at=100.0)
    assert report["conflict"] is True
    assert report["reason"] == "different_project"


def test_an_unreadable_existing_file_is_a_conflict_not_an_overwrite(workdir):
    from litsearch.session_schema import detect_write_conflict

    path = workdir / "autosave.json"
    path.write_text("{ half a file", encoding="utf-8")
    report = detect_write_conflict(str(path), project_id="mine")
    assert report["conflict"] is True
    assert report["reason"] == "unreadable_existing_session"
    assert path.read_text(encoding="utf-8") == "{ half a file", "detection must not modify it"


def test_conflict_copy_path_never_collides(workdir):
    from litsearch.session_schema import conflict_copy_path

    path = workdir / "autosave.json"
    path.write_text("{}", encoding="utf-8")
    first = conflict_copy_path(str(path))
    Path(first).write_text("{}", encoding="utf-8")
    second = conflict_copy_path(str(path))
    assert first != second
    assert Path(first).name.startswith("autosave.conflict-")


# ---------------------------------------------------------------------------
# integration with what persistence actually writes today
# ---------------------------------------------------------------------------


def test_persistence_writes_the_version_this_module_validates():
    from litsearch.persistence import state_to_dict
    from litsearch.search import ReviewState

    state = ReviewState(topic="t", research_direction="d")
    written = state_to_dict(state)
    assert written["version"] == SESSION_SCHEMA_VERSION
    assert validate_session_dict(written)["version"] == SESSION_SCHEMA_VERSION


def test_a_real_saved_session_round_trips_through_the_validator():
    from litsearch.models import Author, Paper
    from litsearch.persistence import state_to_dict
    from litsearch.search import ReviewState

    state = ReviewState(topic="plant phenotyping", research_direction="deep learning")
    state.search_papers = [Paper(id="10.1/x", title="A paper", year=2020,
                                 authors=[Author(name="Smith")], relevance_score=0.25)]
    state.year_distribution = {2020: 3}
    written = state_to_dict(state)
    reloaded = validate_session_dict(json.loads(json.dumps(written)))
    assert reloaded["topic"] == "plant phenotyping"
    assert reloaded["search_papers"][0]["title"] == "A paper"
