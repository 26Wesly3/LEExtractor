"""M0 baseline — the three legacy session fixtures must load through the real loaders.

The v3.2 migration has no fixed starting point unless the legacy shapes are
frozen as files.  This module is that fixed point: it loads every document in
``fixtures/legacy_sessions/`` with the *product* code paths

* :func:`litsearch.session_schema.validate_session_dict` (validate + migrate), and
* :func:`litsearch.persistence.state_from_dict` (the restore path every import
  route, including Web and MCP, goes through),

so a change that silently breaks the ability to read an older session fails
here instead of at a user's first upgrade.

Evidence for the facts asserted below:
* ``litsearch/session_schema.py:50``   ``SESSION_SCHEMA_VERSION = 4``
* ``litsearch/session_schema.py:53``   ``SUPPORTED_PAST_VERSIONS = (1, 2, 3)``
* ``litsearch/persistence.py:47``      ``SCHEMA_VERSION = 4``
* ``litsearch/persistence.py:243-245`` ``state_from_dict`` calls
  ``validate_session_dict`` first, so both loaders agree on acceptance.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

from litsearch.persistence import state_from_dict
from litsearch.session_schema import (
    SESSION_SCHEMA_VERSION,
    validate_session_dict,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "fixtures" / "legacy_sessions"

#: file name -> the ``version`` the document declares (``None`` means the
#: legacy v1 shape, which had no version field at all).
FIXTURES = {
    "legacy_v1_no_version.json": None,
    "legacy_v2_prisma_snowball.json": 2,
    "current_v4_full.json": 4,
}

EMAIL_LIKE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
#: Environment variable names that carry a provider credential.  None of them may
#: appear in a session document.
SECRET_NAMES = ("S2_API_KEY", "OPENALEX_API_KEY", "SERPAPI_API_KEY",
                "LEEXTRACTOR_UNPAYWALL_EMAIL", "Authorization", "api_key")


def load_raw(name: str) -> dict:
    path = FIXTURE_DIR / name
    assert path.is_file(), f"missing M0 fixture {path}"
    return json.loads(path.read_text(encoding="utf-8"))


def test_all_three_fixtures_are_present():
    for name in FIXTURES:
        assert (FIXTURE_DIR / name).is_file(), f"missing M0 fixture {name}"


def test_the_fixtures_cover_three_different_declared_versions():
    declared = {load_raw(name).get("version") for name in FIXTURES}
    assert declared == set(FIXTURES.values())
    assert len(declared) == 3, declared


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_fixture_validates_and_migrates_to_the_current_schema(name):
    raw = load_raw(name)
    assert raw.get("version") == FIXTURES[name]

    warnings: list[dict] = []
    migrated = validate_session_dict(raw, warnings=warnings)

    assert migrated["version"] == SESSION_SCHEMA_VERSION
    # A fresh fixture at the current version must not need migration at all.
    if FIXTURES[name] == SESSION_SCHEMA_VERSION:
        assert warnings == []


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_fixture_loads_through_the_real_restore_path(name):
    raw = load_raw(name)

    # Pass an independent copy: state_from_dict must not need the caller's dict
    # to stay untouched, and a reload must not depend on mutation order.
    state = state_from_dict(copy.deepcopy(raw))

    assert state.topic == raw.get("topic")
    assert state.research_direction == raw.get("research_direction", "")
    assert state.phase.value in {"scoping", "systematic_search", "snowballing",
                                "screening", "complete"}
    # Legacy v1 carries no PRISMA block; the two newer shapes do.
    if FIXTURES[name] is None:
        assert state.prisma.records == {}
    else:
        assert state.prisma.records, f"{name} must restore at least one PRISMA record"


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_a_migrated_document_is_accepted_again(name):
    """Migration output is itself a valid session: re-reading it is idempotent."""
    migrated = validate_session_dict(load_raw(name))
    round_tripped = json.loads(json.dumps(migrated))
    again = validate_session_dict(round_tripped)
    assert again == round_tripped
    assert state_from_dict(again).topic == migrated["topic"]


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_fixtures_carry_no_credential_or_personal_data(name):
    text = (FIXTURE_DIR / name).read_text(encoding="utf-8")
    for secret_name in SECRET_NAMES:
        assert secret_name not in text, f"{name} mentions {secret_name}"
    assert not EMAIL_LIKE.search(text), f"{name} contains an email-like string"


def test_the_v4_fixture_restores_the_audit_facts_it_records():
    """The current-shape fixture must exercise the v4 audit fields, not just load."""
    state = state_from_dict(load_raw("current_v4_full.json"))

    assert state.stop_reason == "saturated"
    assert state.http_budget["requests"] == 7
    assert state.run_history
    assert state.score_context_id == "ctx-gamma-1"
    assert state.score_context_history
    assert state.calibration is not None
    assert state.calibration.query == "synthetic topic gamma"
    # One PRISMA record was accepted at title/abstract, the other left pending.
    decisions = {
        record.screening_decision.value for record in state.prisma.records.values()
    }
    assert decisions == {"accept", "pending"}


def test_the_two_legacy_shapes_are_really_different():
    """Guards against the three fixtures collapsing into one shape over time."""
    v1 = load_raw("legacy_v1_no_version.json")
    v2 = load_raw("legacy_v2_prisma_snowball.json")

    # v1: no version field, dict-shaped container entries, no PRISMA/snowball.
    assert "version" not in v1
    assert isinstance(v1["key_journals"][0], dict)
    assert "prisma" not in v1 and "snowball_result" not in v1
    # v2: declared version, already tuple-shaped pairs, keeps PRISMA + snowball
    #     but lacks the v3 fields the migrator has to fill in.
    assert v2["version"] == 2
    assert isinstance(v2["key_journals"][0], list)
    assert v2["prisma"]["records"]
    assert "full_text_retrieved" not in v2["prisma"]["records"][0]
    assert "round_number" not in v2["snowball_result"]["rounds"][0]
