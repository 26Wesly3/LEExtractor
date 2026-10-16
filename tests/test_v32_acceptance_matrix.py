"""The acceptance matrix must not drift away from reality.

This test parses ``docs/v3.2/qa/ACCEPTANCE_MATRIX.md`` instead of duplicating its
content, and fails when the matrix stops matching the fixed acceptance set:

* every AC-01..AC-32 from ``fixtures/acceptance_cases.json`` appears exactly once;
* every R01..R22 appears exactly once;
* AC-29 is the only ``deferred`` and the only ``required_this_release: false`` case,
  and the matrix agrees with the fixture about that;
* every test module or script the matrix cites as evidence actually exists on disk;
* no row claims an ``effectiveness_validated`` status (the 24-topic expert-labelled
  set and the 8-novice study in ``docs/v3.2/09_ACCEPTANCE_BENCHMARK.md`` were not
  run this round, so that status cannot be claimed by anyone).

No network, no product code: only the matrix file and the fixture.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = REPO_ROOT / "docs" / "v3.2" / "qa" / "ACCEPTANCE_MATRIX.md"
CASES_PATH = REPO_ROOT / "fixtures" / "acceptance_cases.json"

#: The exact column order the matrix must use, so a row can be parsed without
#: guessing which cell means what:
#: 0 '' | 1 id | 2 requirement | 3 spec | 4 status | 5 evidence | 6 command |
#: 7 exit code | 8 observed result | 9 ''
ROW_RE = re.compile(r"^\|\s*(?P<id>AC-\d{2}|R\d{2})\s*\|")
STATUS_INDEX = 4
EVIDENCE_INDEX = 5
PATH_RE = re.compile(r"(?P<path>(?:tests|scripts|litsearch|docs|fixtures|web)/[A-Za-z0-9_./\-]+\.(?:py|json|md))")

REQUIREMENT_IDS = [f"R{index:02d}" for index in range(1, 23)]
ALLOWED_STATUSES = {
    "implemented",
    "integration_verified",
    "effectiveness_validated",
    "external_blocked",
    "deferred",
}


def _matrix_text() -> str:
    assert MATRIX_PATH.exists(), f"missing acceptance matrix: {MATRIX_PATH}"
    return MATRIX_PATH.read_text(encoding="utf-8")


def _rows() -> dict[str, list[list[str]]]:
    """id -> every table row that starts with that id (order preserved)."""
    rows: dict[str, list[list[str]]] = {}
    for line in _matrix_text().splitlines():
        match = ROW_RE.match(line)
        if not match:
            continue
        cells = [cell.strip() for cell in line.split("|")]
        rows.setdefault(match.group("id"), []).append(cells)
    return rows


def _fixture_cases() -> list[dict]:
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]


def test_every_acceptance_case_appears_exactly_once() -> None:
    expected = {case["id"] for case in _fixture_cases()}
    assert expected == {f"AC-{index:02d}" for index in range(1, 33)}, (
        "the fixture is no longer the fixed AC-01..AC-32 set"
    )
    rows = _rows()
    missing = sorted(case_id for case_id in expected if case_id not in rows)
    duplicated = sorted(case_id for case_id, found in rows.items() if case_id.startswith("AC-") and len(found) != 1)
    assert not missing, f"matrix does not mention these AC cases: {missing}"
    assert not duplicated, f"matrix mentions these AC cases more than once: {duplicated}"


def test_every_requirement_appears_exactly_once() -> None:
    rows = _rows()
    missing = sorted(req for req in REQUIREMENT_IDS if req not in rows)
    duplicated = sorted(req for req, found in rows.items() if req.startswith("R") and len(found) != 1)
    assert not missing, f"matrix does not mention these requirements: {missing}"
    assert not duplicated, f"matrix mentions these requirements more than once: {duplicated}"


def test_ac_29_is_the_only_deferred_and_not_required_case() -> None:
    cases = _fixture_cases()
    deferred = sorted(case["id"] for case in cases if case.get("status") == "deferred")
    not_required = sorted(case["id"] for case in cases if not case.get("required_this_release", True))
    assert deferred == ["AC-29"], f"fixtures defer more than AC-29: {deferred}"
    assert not_required == ["AC-29"], f"fixtures exempt more than AC-29: {not_required}"

    rows = _rows()
    deferred_rows = sorted(
        case_id
        for case_id, found in rows.items()
        if case_id.startswith("AC-") and found[0][STATUS_INDEX] == "deferred"
    )
    assert deferred_rows == ["AC-29"], f"the matrix defers more than AC-29: {deferred_rows}"
    assert "deferred" in rows["AC-29"][0][STATUS_INDEX]


def test_statuses_are_from_the_closed_vocabulary() -> None:
    for case_id, found in _rows().items():
        status = found[0][STATUS_INDEX]
        assert status in ALLOWED_STATUSES, f"{case_id} has an unknown status: {status!r}"


def test_no_row_claims_effectiveness_validated() -> None:
    claimed = sorted(
        case_id
        for case_id, found in _rows().items()
        if found[0][STATUS_INDEX] == "effectiveness_validated"
    )
    assert not claimed, (
        "effectiveness_validated requires the 24-topic expert-labelled set and the "
        f"8-novice study in docs/v3.2/09_ACCEPTANCE_BENCHMARK.md, not run this round: {claimed}"
    )


def test_every_cited_evidence_path_exists_on_disk() -> None:
    rows = _rows()
    missing: list[str] = []
    cited: set[str] = set()
    for case_id, found in rows.items():
        evidence = found[0][EVIDENCE_INDEX]
        paths = set(PATH_RE.findall(evidence))
        assert paths, f"{case_id} cites no evidence path"
        cited |= paths
        for path in sorted(paths):
            if not (REPO_ROOT / path).exists():
                missing.append(f"{case_id} -> {path}")
    assert not missing, f"matrix cites evidence that does not exist: {missing}"
    # The evidence base must be the real v3.2 suite, not a handful of files.
    assert len(cited) >= 20, f"only {len(cited)} distinct evidence files cited"


def test_matrix_records_a_command_and_an_exit_code_per_row() -> None:
    """A row without a real command and exit code is not evidence."""
    empty: list[str] = []
    for case_id, found in _rows().items():
        cells = found[0]
        command = cells[6] if len(cells) > 7 else ""
        exit_code = cells[7] if len(cells) > 8 else ""
        if not command or not exit_code:
            empty.append(case_id)
    assert not empty, f"these rows record no command/exit code: {empty}"


if __name__ == "__main__":  # pragma: no cover - manual convenience
    raise SystemExit(pytest.main([__file__, "-q"]))
