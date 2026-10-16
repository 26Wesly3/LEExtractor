"""Repository integrity: no mangled encodings, no stubs, no scratch files committed.

This file exists because of a real incident on this working tree. A source file
was round-tripped through ``Get-Content … | Set-Content`` in PowerShell, which
decoded the UTF-8 source with the console code page and wrote it back lossy: every
Chinese string literal was destroyed and the file became invalid UTF-8. The file
was then replaced with a one-line placeholder to get past the read-before-write
guard, and a 54 KB implementation briefly existed only as a placeholder.

Three cheap checks make that class of damage impossible to commit silently:

1. every source file must decode as UTF-8 (the mangling above breaks this);
2. no source file may be a content-free stub (the placeholder);
3. no ``.tmp_*`` scratch file may be tracked (ad-hoc probes belong outside the
   repository — an untracked working tree is shared by every agent here).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

SOURCE_SUFFIXES = {".py", ".js", ".vue", ".css", ".json", ".md", ".html", ".yml", ".yaml", ".bat", ".sh"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".web-data", ".sessions", ".venv",
             "dist", "build", "tests/_workspace_tmp"}

#: Mojibake markers. A UTF-8 file read as cp1252/latin-1 and written back as
#: UTF-8 leaves these, and they are never legitimate in this repository.
#:
#: Written as escapes rather than literal characters on purpose: a literal copy
#: would make this very file match its own scan (which it did until the escapes
#: went in), and exempting the detector from itself would also exempt a real
#: regression that mangled this file.
MOJIBAKE = (
    "\u00ef\u00bf\u00bd",  # the UTF-8 replacement character, double-encoded
    "\u00c3\u00a4",        # a-umlaut read as latin-1
    "\u00c3\u00a5",        # a-ring
    "\u00c3\u00a6",        # ae-ligature
    "\u00e2\u20ac",        # the classic mangled smart quote / em dash
    "\u00e3\u20ac",        # a mangled CJK bracket
    "\u00ef\u00bc",        # a mangled CJK comma
    "\ufffd",              # the replacement character itself
)


def _tracked_source_files() -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "--cached"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True, timeout=120,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover
        pytest.skip(f"git is unavailable: {exc}")
    files = []
    for line in out.splitlines():
        path = REPO_ROOT / line.strip()
        if not path.is_file() or path.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        relative = path.relative_to(REPO_ROOT)
        if any(part in SKIP_DIRS for part in relative.parts):
            continue
        files.append(path)
    return files


def test_every_tracked_source_file_is_valid_utf8():
    broken: list[str] = []
    for path in _tracked_source_files():
        try:
            path.read_bytes().decode("utf-8")
        except UnicodeDecodeError as exc:
            broken.append(f"{path.relative_to(REPO_ROOT)} (byte {exc.start}: {exc.reason})")
    assert not broken, (
        "these files are not valid UTF-8, which is what a PowerShell "
        "Get-Content/Set-Content text round-trip does to a UTF-8 source file; "
        "repair them with the read/edit/write tools:\n" + "\n".join(broken)
    )


def test_no_tracked_source_file_contains_mojibake():
    offenders: list[str] = []
    for path in _tracked_source_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for marker in MOJIBAKE:
            if marker in text:
                line = text[: text.index(marker)].count("\n") + 1
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line} contains {marker!r}")
                break
    assert not offenders, "mojibake found in tracked files:\n" + "\n".join(offenders)


def test_no_tracked_python_module_is_a_content_free_stub():
    """A module that is only a comment is a placeholder, not an implementation."""
    offenders: list[str] = []
    for path in _tracked_source_files():
        if path.suffix != ".py":
            continue
        relative = path.relative_to(REPO_ROOT)
        if relative.parts[0] not in {"litsearch", "scripts"}:
            continue
        meaningful = [
            line for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        min_lines = 3 if relative.parts[0] == "litsearch" else 1
        if len(meaningful) < min_lines:
            offenders.append(f"{relative} has only {len(meaningful)} non-comment line(s)")
    assert not offenders, "placeholder/stub modules found:\n" + "\n".join(offenders)


def test_no_scratch_file_is_tracked():
    tracked = subprocess.run(
        ["git", "ls-files", "--cached"], cwd=REPO_ROOT,
        capture_output=True, text=True, check=True, timeout=120,
    ).stdout.splitlines()
    offenders = [
        name for name in tracked
        if PurePosixName(name).name.startswith(".tmp_") or name.endswith((".orig", ".rej", ".bak"))
    ]
    assert not offenders, "scratch files must not be committed: " + ", ".join(offenders)


def PurePosixName(name: str):
    import posixpath

    class _N:
        def __init__(self, value: str) -> None:
            self.name = posixpath.basename(value)

    return _N(name)


def test_the_critical_modules_are_not_stubs_and_import_cleanly():
    """The modules the whole v3.2 layer depends on must be real and importable."""
    critical = [
        "litsearch/v32/contracts.py",
        "litsearch/v32/errors.py",
        "litsearch/v32/types.py",
        "litsearch/v32/sessions.py",
        "litsearch/v32/store.py",
        "litsearch/v32/jobs.py",
    ]
    import importlib

    for name in critical:
        path = REPO_ROOT / name
        assert path.is_file(), f"{name} is missing"
        assert path.stat().st_size > 1000, f"{name} is suspiciously small ({path.stat().st_size} bytes)"
        module = name.removesuffix(".py").replace("/", ".")
        assert importlib.import_module(module), module
