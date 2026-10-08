"""The shipped documentation must be self-contained.

A release whose own files cite missing files is not self-contained: the reader
either cannot follow the instruction or assumes the package is incomplete. This
module asserts the invariant instead of trusting the whitelist to stay in sync
with the prose.
"""

import re
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
_TMP_ROOT = Path(__file__).resolve().parent / "_workspace_tmp"

sys.path.insert(0, str(SCRIPTS))

import package_release  # noqa: E402


@pytest.fixture()
def outdir():
    """A wiped packaging directory under the workspace.

    Same shape as ``test_release_version.py``: the sandbox cannot remove files
    outside the workspace, so the system temp dir is not an option.
    """
    path = _TMP_ROOT / "release_self_contained"
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)

#: Documents whose cross-references a reader is expected to follow.
SHIPPED_PROSE = (
    "ONBOARDING.md",
    "README.md",
    "ARCHITECTURE.md",
    "CHANGES.md",
    "ROADMAP.md",
)

#: Files a markdown document may name without shipping them: generated output,
#: local artefacts, or things the reader is told to create.
IGNORED_REFERENCES: set[str] = {
    # README.md names it only inside a maintainer note ("add a real
    # CONTRIBUTING.md once the repository is confirmed"); nothing to ship yet.
    "CONTRIBUTING.md",
}

#: Repository working name -> the name the release ships it under. Documents
#: mention either, so both have to resolve.
WORKING_TO_SHIPPED = {
    "启动LEExtractor_收到后改回bat.bat": "启动LEExtractor.bat",
}
SHIPPED_TO_WORKING = {shipped: working for working, shipped in WORKING_TO_SHIPPED.items()}


def _markdown_filenames(text: str) -> set[str]:
    """Every filename-looking reference in a markdown document.

    Covers inline ``code`` spans, link targets and bare mentions of a name with
    a known document extension.
    """
    found = set()
    for match in re.finditer(
        r"[「\"'(（]?([^\s「」\"'()（）\[\]，,。；;：:<>|*?]+"
        r"\.(?:md|json|py|txt|toml|bat|yml|yaml|sha256))[」\"')）]?",
        text,
    ):
        name = match.group(1).strip()
        if name:
            found.add(name)
    # Drop anything that is clearly a path fragment or a URL piece.
    return {name for name in found if not name.startswith(("http", "www", "./", "../"))}


def _packaged_names(outdir) -> set[str]:
    summary = package_release.package(REPO, outdir)
    with zipfile.ZipFile(summary["path"]) as archive:
        return {name.split("/", 1)[1] for name in archive.namelist() if "/" in name}


def test_every_document_reference_resolves_inside_the_release(outdir):
    """A shipped document must not point at a file the release omits."""
    packaged = _packaged_names(outdir)
    missing = {}
    for document in SHIPPED_PROSE:
        path = REPO / document
        if not path.exists():
            continue
        for referenced in sorted(_markdown_filenames(path.read_text(encoding="utf-8"))):
            if referenced in IGNORED_REFERENCES:
                continue
            # A document naming itself is not a reference.
            if referenced == document:
                continue
            # Windows-flavoured paths in the prose ("scripts\launch_gui.py").
            normalised = referenced.replace("\\", "/")
            local_name = SHIPPED_TO_WORKING.get(normalised, normalised)
            shipped_name = WORKING_TO_SHIPPED.get(normalised, normalised)
            if (REPO / local_name).exists() and shipped_name not in packaged:
                missing.setdefault(document, set()).add(shipped_name)
    assert not missing, (
        "these documents are shipped but cite files the package omits: "
        + "; ".join(f"{doc} -> {sorted(refs)}" for doc, refs in sorted(missing.items()))
    )


def test_the_launcher_ships_under_the_documented_name(outdir):
    """README/ONBOARDING tell the user to double-click a name that must exist."""
    packaged = _packaged_names(outdir)
    assert "启动LEExtractor.bat" in packaged, (
        "the launcher is documented as 启动LEExtractor.bat but ships under "
        f"another name; packaged bat files: "
        f"{sorted(n for n in packaged if n.endswith('.bat'))}"
    )


def test_the_manifest_lists_the_shipped_names(outdir):
    """The manifest must agree with the archive about where a file lives."""
    summary = package_release.package(REPO, outdir)
    with zipfile.ZipFile(summary["path"]) as archive:
        names = set(archive.namelist())
        manifest = archive.read("MANIFEST.sha256").decode("utf-8")
    for line in manifest.strip().splitlines():
        entry = line.split("  ", 1)[1]
        assert entry in names, f"MANIFEST lists {entry}, which the archive does not contain"


def test_onboarding_does_not_send_a_new_reader_to_a_historical_document_first():
    """The first instruction must be followable and current."""
    text = (REPO / "ONBOARDING.md").read_text(encoding="utf-8")
    first_instruction = text.split("\n## ")[1] if "\n## " in text else text
    assert "优化说明" not in first_instruction.split("\n\n")[0], (
        "ONBOARDING still tells the reader to read the historical v0.6.0 note first"
    )
    assert "README" in text, "ONBOARDING must still point at README"
