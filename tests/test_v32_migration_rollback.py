"""AC-12 / doc 14 §持久化升级: a failed migration leaves the original readable.

Two failure points are covered: the migration function itself raising (an
unexpected exception, not only a ``V32Error``), and the write of the migrated
document failing half-way after the file was already touched.  In both cases
the store must not crash, must report the refusal, and the project file must be
byte-identical to the original (restored from the backup when it was touched).
"""

from __future__ import annotations

import json
from pathlib import Path

from litsearch.v32 import store as S

PID = "c" * 32


def _legacy(root: Path) -> tuple[Path, str]:
    projects = root / "projects"
    projects.mkdir(parents=True)
    legacy = {
        "project_id": PID,
        "name": "旧会话",
        "records": [{"canonical_id": "d" * 32, "title": "Legacy", "doi": "10.1/legacy"}],
    }
    path = projects / f"{PID}.json"
    original = json.dumps(legacy, ensure_ascii=False)
    path.write_text(original, encoding="utf-8")
    return path, original


def test_a_raising_migration_keeps_the_original_file_and_does_not_crash(tmp_path, monkeypatch):
    root = tmp_path / "v32"
    path, original = _legacy(root)

    def broken(_doc):
        raise KeyError("records")

    monkeypatch.setitem(S._MIGRATIONS, 0, broken)
    store = S.V32Store(root)
    assert PID not in store.projects
    assert store.load_errors and "migration" in store.load_errors[0]
    assert path.read_text(encoding="utf-8") == original
    assert json.loads(path.read_text(encoding="utf-8"))["name"] == "旧会话"


def test_a_failed_write_restores_the_backup(tmp_path, monkeypatch):
    root = tmp_path / "v32"
    path, original = _legacy(root)

    def torn_write(target, payload, *args, **kwargs):
        # Simulate a non-atomic failure: the file is truncated, then the write dies.
        Path(target).write_text('{"schema_version": 1, "proj', encoding="utf-8")
        raise OSError("disk full")

    monkeypatch.setattr(S, "atomic_json", torn_write)
    store = S.V32Store(root)
    assert PID not in store.projects
    assert store.load_errors and "migration" in store.load_errors[0]
    # The original is back and still parses.
    assert path.read_text(encoding="utf-8") == original
    assert json.loads(path.read_text(encoding="utf-8"))["project_id"] == PID
    assert list((root / "backups").glob("*.json"))

    # The next start (with a working writer) migrates it normally.
    monkeypatch.undo()
    again = S.V32Store(root)
    assert PID in again.projects and again.load_errors == []
