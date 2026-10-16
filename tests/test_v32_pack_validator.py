"""R22: the specification pack stays intact and hand-off-able inside this repo.

``docs/v3.2/START_HERE.md`` says a pack run of ``scripts/validate_pack.py`` must
report the pack is complete, and ``docs/v3.2/18_REQUIREMENTS_TRACEABILITY.md``
makes R22 exactly that check.  The validator was adapted to this repository's
path mapping (``contracts/`` ships inside the package, the specification lives
under ``docs/v3.2/``) and the acceptance pass found it could no longer reach
``status: pass`` — so this test pins the outcome instead of trusting it.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
VALIDATOR = REPO / "scripts" / "validate_v32_pack.py"


def _run() -> tuple[int, dict]:
    result = subprocess.run(
        [sys.executable, str(VALIDATOR)], cwd=REPO,
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        payload = {"_stdout": result.stdout[-2000:], "_stderr": result.stderr[-2000:]}
    return result.returncode, payload


def test_the_pack_validator_reports_pass_in_this_repository():
    code, report = _run()
    assert code == 0, report
    assert report.get("status") == "pass", report
    assert report["openapi_operations"] == 25
    assert report["ranking_fixtures"] == 5
    assert report["acceptance_specifications"] == 32
    assert report["requirements"] == 22
    # It must say which layout it validated, so a reader knows the mapping applied.
    assert report.get("layout") in {"pack-root", "repository-mapped"}


def test_the_validator_actually_exercises_the_fixed_algorithm_cases():
    """A validator that skipped the fixtures would still print pass."""
    _, report = _run()
    assert report["ranking_fixtures"] == 5
    assert report["acceptance_specifications"] == 32


def test_every_manifest_file_is_checksum_verified_or_explicitly_explained():
    """A file that legitimately differs must be named with its reason.

    Two files are expected to differ: the implementer-filled status record, which
    the pack ships blank, and the validator itself, which was adapted to the
    repository's path mapping. Everything else must match the pack byte for byte.
    """
    code, report = _run()
    assert code == 0, report
    differing = {item["path"]: item for item in report["manifest_files_content_differs_from_pack"]}
    assert set(differing) == {
        "handoff/IMPLEMENTATION_STATUS.md",
        "scripts/validate_pack.py",
    }, differing
    for path, item in differing.items():
        assert item["reason"], f"{path} differs without a stated reason"
        assert item["pack_sha256"] != item["local_sha256"], path
    assert report["manifest_files_checksum_verified"] >= 30


def test_the_implementer_status_record_is_actually_filled_in():
    """The pack ships a template; START_HERE.md requires the real record."""
    text = (REPO / "handoff" / "IMPLEMENTATION_STATUS.md").read_text(encoding="utf-8")
    assert "not_started" not in text, "the status record is still the empty template"
    # The pack's template names these fields; the filled record must answer them.
    for required in ("repository_url", "branch", "commit", "python", "pytest"):
        assert required in text, required
    # It must report the real baseline honestly.
    assert "873" not in text or "passed" in text


def test_the_validator_does_not_require_network_or_the_application():
    source = VALIDATOR.read_text(encoding="utf-8")
    for banned in ("requests", "urllib.request", "socket", "http.client"):
        assert banned not in source, f"the validator must not use {banned}"
    assert "levenshtein" not in source
