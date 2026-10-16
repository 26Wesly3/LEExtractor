"""Smoke-check the v3.2 store while the API layer is being written."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from litsearch.v32 import store as S


@pytest.fixture()
def store(tmp_path: Path) -> S.V32Store:
    return S.V32Store(tmp_path / "v32")


def test_create_and_fetch_are_owner_scoped(store):
    project = store.create_project(owner_id="owner-a", name="薄荷", entry_mode="topic")
    dto = store.project_dto(project)
    assert dto["revision"] == 1
    assert dto["contract_version"] == "3.2"
    assert "owner_id" not in dto
    assert store.get(project.project_id, owner_id="owner-a").project_id == project.project_id
    # Another owner sees a 404, not a 403 — project existence is not leaked.
    from litsearch.v32.errors import V32Error

    with pytest.raises(V32Error) as excinfo:
        store.get(project.project_id, owner_id="owner-b")
    assert excinfo.value.status == 404


def test_revision_conflict_reports_current_revision(store):
    project = store.create_project(owner_id="o", name="p", entry_mode="topic")
    from litsearch.v32.errors import V32Error

    with pytest.raises(V32Error) as excinfo:
        store.check_revision(project, 99)
    assert excinfo.value.status == 409
    assert excinfo.value.details == {"current_revision": 1}


def test_judgement_is_atomic_and_revoking_clears_feedback(store):
    project = store.create_project(owner_id="o", name="p", entry_mode="topic")
    pid = store.upsert_paper(project, {"paper_id": "a" * 32, "title": "T"})
    first = store.set_judgement(project, expected_revision=1, paper_id=pid, value="core")
    assert first["revision"] == 2
    assert first["context_version"] == 2
    assert first["judgement"]["value"] == "core"
    assert store.excluded_ids(project) == set()

    second = store.set_judgement(project, expected_revision=2, paper_id=pid, value="excluded",
                                 exclusion_reason="not_relevant")
    assert second["revision"] == 3
    assert store.excluded_ids(project) == {pid}

    third = store.set_judgement(project, expected_revision=3, paper_id=pid, value="unjudged")
    assert third["revision"] == 4
    assert third["judgement"]["value"] == "unjudged"
    # Revoking clears the judgement but keeps the audit trail.
    assert store.excluded_ids(project) == set()
    assert project.doc["judgements"] == {}
    assert [e["value"] for e in project.doc["judgement_events"]] == ["core", "excluded", "unjudged"]
    assert [e["sequence"] for e in project.doc["judgement_events"]] == [1, 2, 3]


def test_nonexcluded_judgement_rejects_a_stray_exclusion_reason(store):
    from litsearch.v32.errors import V32Error

    project = store.create_project(owner_id="o", name="p", entry_mode="topic")
    pid = store.upsert_paper(project, {"paper_id": "b" * 32, "title": "T"})
    with pytest.raises(V32Error) as excinfo:
        store.set_judgement(project, expected_revision=1, paper_id=pid, value="core",
                            exclusion_reason="read")
    assert excinfo.value.status == 422


def test_identity_merge_keeps_the_earlier_id_and_migrates_everything(store):
    project = store.create_project(owner_id="o", name="p", entry_mode="topic")
    older = store.upsert_paper(project, {"paper_id": "1" * 32, "title": "Same", "doi": "10.1/x"})
    # Force a deterministic created_at ordering.
    project.doc["papers"][older]["created_at"] = "2000-01-01T00:00:00+00:00"
    newer = store.upsert_paper(project, {"paper_id": "2" * 32, "title": "Same", "doi": None})
    project.doc["papers"][newer]["created_at"] = "2020-01-01T00:00:00+00:00"
    store.set_judgement(project, expected_revision=1, paper_id=newer, value="core")
    project.doc["edges"]["e1"] = {
        "edge_id": "e1", "from_paper_id": older, "to_paper_id": newer, "relation": "citation",
        "directed": True, "weight": 1.0, "witness_paper_ids": [older],
        "source_records": [], "created_at": S.now(),
    }

    keep = store.merge_identity(project, older, newer)

    assert keep == older
    assert newer not in project.doc["papers"]
    assert project.doc["redirects"][newer] == older
    assert project.doc["aliases"][newer] == older
    assert project.doc["judgements"][older]["paper_id"] == older
    # The merge removed the self-loop it created.
    assert project.doc["edges"] == {}
    assert store.resolve_paper_id(project, newer) == older
    store._verify_integrity(project.doc)


def test_merge_collapses_duplicate_edges_onto_their_witnesses(store):
    project = store.create_project(owner_id="o", name="p", entry_mode="topic")
    a = store.upsert_paper(project, {"paper_id": "a" * 32, "title": "A"})
    b = store.upsert_paper(project, {"paper_id": "b" * 32, "title": "B"})
    c = store.upsert_paper(project, {"paper_id": "c" * 32, "title": "C"})
    for edge_id, source, target, witness in (
        ("e1", a, c, a), ("e2", b, c, b),
    ):
        project.doc["edges"][edge_id] = {
            "edge_id": edge_id, "from_paper_id": source, "to_paper_id": target,
            "relation": "citation", "directed": True, "weight": 1.0,
            "witness_paper_ids": [witness], "source_records": [], "created_at": S.now(),
        }
    store.merge_identity(project, a, b)
    assert len(project.doc["edges"]) == 1
    only = next(iter(project.doc["edges"].values()))
    assert only["from_paper_id"] == a and only["to_paper_id"] == c
    assert only["witness_paper_ids"] == [a, b]


def test_attachment_deduplicates_by_content(store):
    project = store.create_project(owner_id="o", name="p", entry_mode="topic")
    payload = b"%PDF-1.7 hello"
    first = store.add_attachment(project, payload, "paper.pdf")
    second = store.add_attachment(project, payload, "../../evil.pdf")
    assert first["attachment_id"] == second["attachment_id"]
    assert S.content_hash(payload) == first["content_hash"]
    assert store.attachment_bytes(project, first["attachment_id"]) == payload


def test_display_name_can_never_become_a_path():
    assert S._safe_display_name("..\\..\\evil.pdf") == "evil.pdf"
    assert S._safe_display_name("a/b/c.pdf") == "c.pdf"
    assert S._safe_display_name("") == "document.pdf"
    assert len(S._safe_display_name("x" * 500)) == 180


def test_snapshot_commit_is_refused_once_superseded(store):
    project = store.create_project(owner_id="o", name="p", entry_mode="topic")
    store.set_config_hash(project, "h", None)
    frozen = {
        "context_version": project.context_version,
        "corpus_version": project.corpus_version,
        "config_hash": "h",
        "model_revision": None,
    }
    snapshot = {
        "snapshot_id": "f" * 32, "parent_snapshot_id": None, "project_id": project.project_id,
        "context_version": project.context_version, "corpus_version": project.corpus_version,
        "algorithm_version": "corerank-3.2.0",
        "config_hash": "h", "model_revision": None, "as_of_date": "2026-01-01",
        "ranking_stage": "fast", "items": [], "starter_paper_ids": [],
        "coverage": {"status": "within_budget", "candidate_count": 0, "failed_sources": [],
                     "stop_reasons": [], "scope": "test"},
    }
    revision_before = project.revision
    assert store.commit_snapshot(project, snapshot, frozen=frozen) == "committed"
    # A snapshot is not a content edit: revision is unchanged.
    assert project.revision == revision_before
    assert project.doc["current_snapshot_id"] == "f" * 32
    assert store.load_snapshot(project, "f" * 32)["snapshot_id"] == "f" * 32

    # Now the context moves on; the same frozen computation must be superseded.
    project.doc["context_version"] = project.context_version + 1
    assert store.commit_snapshot(project, snapshot, frozen=frozen) == "superseded"
    # The historical snapshot is still readable.
    assert store.load_snapshot(project, "f" * 32)["context_version"] == frozen["context_version"]


def test_idempotency_same_body_replays_and_different_body_conflicts(store):
    from litsearch.v32.errors import V32Error

    kwargs = dict(owner_id="o", project_id="p" * 32, endpoint="discover", key="k" * 16)
    assert store.idempotency_lookup(body_hash="h1", **kwargs) is None
    store.idempotency_store(body_hash="h1", response={"job_id": "j"}, **kwargs)
    assert store.idempotency_lookup(body_hash="h1", **kwargs) == {"job_id": "j"}
    with pytest.raises(V32Error) as excinfo:
        store.idempotency_lookup(body_hash="h2", **kwargs)
    assert excinfo.value.status == 409
    assert excinfo.value.code == "idempotency_conflict"


def test_cursors_are_opaque_and_stale_cursors_are_rejected(store):
    from litsearch.v32.errors import V32Error

    cursor = S.encode_cursor("f" * 32, 20)
    assert "f" * 32 not in cursor
    assert S.decode_cursor(cursor) == ("f" * 32, 20)
    assert S.decode_cursor(None) == (None, 0)
    with pytest.raises(V32Error) as excinfo:
        S.decode_cursor("!!!not-a-cursor!!!")
    assert excinfo.value.status == 422


def test_legacy_document_is_migrated_once_and_idempotently(tmp_path):
    root = tmp_path / "v32"
    projects = root / "projects"
    projects.mkdir(parents=True)
    legacy = {
        "project_id": "c" * 32,
        "name": "旧会话",
        "records": [{"canonical_id": "d" * 32, "title": "Legacy", "doi": "10.1/legacy",
                     "relevance_score": 0.42}],
    }
    path = projects / f"{'c' * 32}.json"
    path.write_text(json.dumps(legacy), encoding="utf-8")

    first = S.V32Store(root)
    assert first.migrations_applied and first.load_errors == []
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["schema_version"] == 1
    assert doc["contract_version"] == "3.2"
    # Legacy relevance_score is retained verbatim and never re-labelled core_score.
    stored = next(iter(doc["papers"].values()))
    assert stored["title"] == "Legacy"
    assert "core_score" not in stored
    assert doc["judgements"] == {}
    before = path.read_text(encoding="utf-8")

    # A second run must not change anything.
    second = S.V32Store(root)
    assert second.migrations_applied == []
    assert path.read_text(encoding="utf-8") == before
    assert second.load_errors == []
    # And a backup of the original was written.
    assert list((root / "backups").glob("*.json"))


def test_newer_schema_version_is_refused_and_the_file_is_preserved(tmp_path):
    root = tmp_path / "v32"
    projects = root / "projects"
    projects.mkdir(parents=True)
    future = {"schema_version": 99, "project_id": "e" * 32, "owner_id": "o", "papers": {}}
    path = projects / f"{'e' * 32}.json"
    original = json.dumps(future)
    path.write_text(original, encoding="utf-8")

    store = S.V32Store(root)
    # The project is not loaded at all, and the file on disk is untouched.
    assert store.load_errors and "migration refused" in store.load_errors[0]
    assert path.read_text(encoding="utf-8") == original


def test_integrity_check_rejects_a_dangling_reference(store):
    from litsearch.v32.errors import V32Error

    project = store.create_project(owner_id="o", name="p", entry_mode="topic")
    project.doc["aliases"]["ghost"] = "9" * 32
    with pytest.raises(V32Error):
        store._verify_integrity(project.doc)


def test_nan_can_never_be_persisted(store):
    project = store.create_project(owner_id="o", name="p", entry_mode="topic")
    pid = store.upsert_paper(project, {"paper_id": "a" * 32, "title": "T"})
    project.doc["papers"][pid]["citation_count"] = float("nan")
    with pytest.raises(ValueError):
        store.save(project)
