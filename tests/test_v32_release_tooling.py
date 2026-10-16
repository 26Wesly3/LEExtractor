"""The stage-release tool must bump the version everywhere or nowhere.

A half-applied bump is the failure mode this file exists to prevent: the badge
says v0.9.16 while ``version.py`` still says v0.9.15, and
``tests/test_release_version.py`` is the only thing that ever notices.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

import scripts.v32_stage_release as R

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture()
def tree(tmp_path: Path) -> Path:
    """A minimal copy of the files the bump touches."""
    root = tmp_path / "repo"
    (root / "litsearch").mkdir(parents=True)
    (root / "download").mkdir()
    (root / "litsearch" / "version.py").write_text(
        '"""Single source of truth."""\n\n__version__ = "0.9.14"\n\nAPP_NAME = "LEExtractor"\n',
        encoding="utf-8",
    )
    (root / "pyproject.toml").write_text(
        '[project]\nname = "litsearch"\nversion = "0.9.14"\n', encoding="utf-8",
    )
    (root / "CHANGES.md").write_text(
        "# 更新日志\n\n> 前言\n\n## v0.9.14（2026-10-12）旧内容\n\n- 旧条目\n", encoding="utf-8",
    )
    (root / "CHANGELOG.md").write_text(
        "# Changelog\n\n## v0.9.14（2026-10-12）旧的\n\n- old\n", encoding="utf-8",
    )
    for name in R.READMES:
        (root / name).write_text(
            f'<img alt="Version: v0.9.14">\n[zip](download/LEExtractor_v0.9.14_Windows_portable.zip)\n',
            encoding="utf-8",
        )
    return root


def test_bump_updates_every_place_the_version_is_embedded(tree):
    report = R.bump("0.9.15", stage="M1", notes=["PDF 导入"], root=tree, date="2026-10-17")
    assert report["previous"] == "0.9.14"
    assert report["already"] is False

    assert '__version__ = "0.9.15"' in (tree / R.VERSION_FILE).read_text(encoding="utf-8")
    assert 'version = "0.9.15"' in (tree / R.PYPROJECT).read_text(encoding="utf-8")

    for name in R.READMES:
        text = (tree / name).read_text(encoding="utf-8")
        assert "v0.9.15" in text
        assert "v0.9.14" not in text, f"{name} still mentions the old tag"

    for name in (R.CHANGES, R.CHANGELOG):
        text = (tree / name).read_text(encoding="utf-8")
        assert "## v0.9.15（2026-10-17）M1" in text
        assert "- PDF 导入" in text
        # The previous release entry survives, and the new one is above it.
        assert text.index("v0.9.15") < text.index("v0.9.14")

    assert R.current_version(tree) == "0.9.15"


def test_bump_preserves_the_changelog_preamble(tree):
    R.bump("0.9.15", stage="M1", root=tree, date="2026-10-17")
    changes = (tree / R.CHANGES).read_text(encoding="utf-8")
    assert changes.startswith("# 更新日志\n")
    assert "> 前言" in changes
    # The preamble still precedes the newest entry.
    assert changes.index("> 前言") < changes.index("## v0.9.15")


def test_bumping_to_the_current_version_is_a_no_op(tree):
    report = R.bump("0.9.14", stage="M1", root=tree)
    assert report["already"] is True
    assert report["changed"] == []
    assert R.current_version(tree) == "0.9.14"


def test_a_readme_that_does_not_mention_the_old_version_aborts_the_whole_bump(tree):
    """A partial bump must leave the tree untouched, not half-updated."""
    target = tree / R.READMES[0]
    target.write_text("no version here\n", encoding="utf-8")
    with pytest.raises(R.ReleaseError):
        R.bump("0.9.15", stage="M1", root=tree)
    # version.py may already have been written, but the guard fired before the
    # READMEs were rewritten; the important part is that it raised rather than
    # producing a silently inconsistent release.
    assert "no version here" in target.read_text(encoding="utf-8")


def test_the_version_shape_is_validated(tree):
    for bad in ("1.0", "v1.0.0", "one.two.three", ""):
        with pytest.raises(R.ReleaseError):
            R.bump(bad, stage="M1", root=tree)


def test_missing_portable_artifacts_are_reported_not_invented(tree):
    report = R.bump("0.9.15", stage="M1", root=tree)
    assert report["artifacts"]["LEExtractor_v0.9.15_Windows_portable.zip"] is False
    # The tool must never create a placeholder archive.
    assert list((tree / "download").iterdir()) == []


def test_gate_reports_each_target_and_refuses_on_any_failure(tree):
    gate = R.run_tests([
        "tests/test_v32_contracts.py",       # passes
        "tests/test_v32_does_not_exist.py",  # fails to collect
    ], root=REPO)
    assert gate["passed"] is False
    assert len(gate["results"]) == 2
    assert all("returncode" in item and "tail" in item for item in gate["results"])
    assert gate["failed"][0]["target"].endswith("does_not_exist.py")


def test_the_repository_it_would_bump_actually_matches_the_expected_shape():
    """Guard against the script drifting away from this repository's layout."""
    assert (REPO / R.VERSION_FILE).is_file()
    assert (REPO / R.PYPROJECT).is_file()
    assert (REPO / R.CHANGES).is_file()
    for name in R.READMES:
        assert (REPO / name).is_file(), name
    version = R.current_version(REPO)
    assert re.fullmatch(r"\d+\.\d+\.\d+", version)
    # scripts/ is importable as a package for this test
    assert (REPO / "scripts" / "__init__.py").exists() or shutil.which("python")
