"""The session validator must actually run on the real restore path.

``litsearch/session_schema.py`` had 40 passing tests while being called by
nothing but those tests: ``persistence.load_state`` still did a bare
``json.load`` inside ``except Exception: return None``. A validator that no
production path invokes is documentation, not protection — a session file from
a future release, or one with a corrupted field, would be read as if it were
current. These tests exercise the *real* entry point.
"""

import json
import os

import pytest

import litsearch.persistence as persistence
from litsearch.models import Paper
from litsearch.search import ReviewState


@pytest.fixture
def session_dir(tmp_path, monkeypatch):
    """Point the module's session directory at a private per-test location."""
    directory = tmp_path / "sessions"
    directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(persistence, "SESSION_DIR", str(directory))
    return directory


def _state(topic="plant phenotyping"):
    state = ReviewState(topic, "deep learning for plant phenotyping")
    state.search_papers = [
        Paper(id="10.1/a", title="A study", doi="10.1/a", year=2022),
    ]
    return state


def _backups(directory):
    return [name for name in os.listdir(directory) if name.endswith(".bak")]


# ---------------------------------------------------------------------------
# The happy path still works
# ---------------------------------------------------------------------------


def test_a_valid_session_round_trips_through_the_validated_path(session_dir):
    path = persistence.save_state(_state(), str(session_dir / "ok.json"))
    restored = persistence.load_state(path)
    assert restored is not None
    assert restored.topic == "plant phenotyping"
    assert [p.canonical_id for p in restored.search_papers] == ["10.1/a"]
    assert _backups(session_dir) == [], "a valid session must not be backed up"


def test_the_written_file_carries_the_version_the_validator_expects(session_dir):
    from litsearch.session_schema import SESSION_SCHEMA_VERSION

    path = persistence.save_state(_state(), str(session_dir / "ok.json"))
    with open(path, encoding="utf-8") as handle:
        written = json.load(handle)
    assert written["version"] == SESSION_SCHEMA_VERSION == persistence.SCHEMA_VERSION


# ---------------------------------------------------------------------------
# The validator is reached from load_state
# ---------------------------------------------------------------------------


def test_corrupt_json_is_refused_and_backed_up_by_the_real_loader(session_dir):
    path = session_dir / "corrupt.json"
    path.write_text("{not json", encoding="utf-8")

    assert persistence.load_state(str(path)) is None
    assert _backups(session_dir), "the corrupt file must be kept as a backup"
    assert path.exists(), "the original must not be deleted or overwritten"


def test_a_session_from_a_future_release_is_refused_not_guessed(session_dir):
    path = session_dir / "future.json"
    path.write_text(json.dumps({
        "version": 99,
        "topic": "plant phenotyping",
        "research_direction": "deep learning",
    }), encoding="utf-8")

    assert persistence.load_state(str(path)) is None, (
        "a newer schema was read as if this build understood it"
    )
    assert _backups(session_dir)


def test_a_schema_invalid_field_is_refused_with_a_field_name(session_dir, caplog):
    payload = persistence.state_to_dict(_state())
    payload["search_papers"] = "not-a-list"
    path = session_dir / "badfield.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with caplog.at_level("ERROR"):
        assert persistence.load_state(str(path)) is None
    assert any("search_papers" in record.getMessage()
               or "SESSION_SCHEMA_INVALID" in record.getMessage()
               for record in caplog.records), (
        "the refusal must name what was wrong, not just fail silently"
    )


def test_a_known_older_version_is_migrated_rather_than_refused(session_dir):
    """Legacy sessions must keep loading: refusing them would lose user data."""
    payload = persistence.state_to_dict(_state())
    payload["version"] = 1
    payload.pop("run_history", None)
    path = session_dir / "legacy.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    restored = persistence.load_state(str(path))
    assert restored is not None, "a v1 session was refused instead of migrated"
    assert restored.topic == "plant phenotyping"


def test_a_rejected_session_leaves_a_neighbouring_autosave_untouched(session_dir):
    autosave = session_dir / "autosave.json"
    autosave.write_text(
        json.dumps(persistence.state_to_dict(_state("keep me"))), encoding="utf-8"
    )
    before = autosave.read_bytes()

    corrupt = session_dir / "corrupt.json"
    corrupt.write_text("{broken", encoding="utf-8")
    assert persistence.load_state(str(corrupt)) is None

    assert autosave.read_bytes() == before, "a failed import damaged a good session"
    assert persistence.load_state(str(autosave)).topic == "keep me"


def test_missing_file_still_returns_none_without_creating_anything(session_dir):
    assert persistence.load_state(str(session_dir / "absent.json")) is None
    assert os.listdir(session_dir) == []
