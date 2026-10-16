"""No credential may ever be committed, ever.

The user supplied a live Semantic Scholar API key for real integration checks
and stated plainly that it must never reach git.  A promise is not a control, so
this test *is* the control: it fails the build if anything that looks like a
provider key appears in a tracked file, and it pins the rules about where
credentials may live.

It deliberately scans only tracked/committable content, and it never prints a
matched secret — a failing assertion that echoes the key would leak it into CI
logs.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Patterns for real provider credentials.  Kept narrow on purpose: a broad
#: "any long token" rule would flag hashes, base64 fixtures and lock files.
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("semantic_scholar_key", re.compile(r"\bs2k-[A-Za-z0-9]{20,}\b")),
    ("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{24,}\b")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("private_key_block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")),
)

#: A file we expect to be scanned.  Binary blobs are skipped by decoding errors.
_SKIP_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".rar", ".7z",
    ".ttf", ".otf", ".woff", ".woff2", ".bin", ".db", ".pyc", ".so", ".dll",
    ".exe", ".onnx", ".pt", ".safetensors",
}
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", ".web-data", ".sessions"}


def _tracked_files() -> list[Path]:
    """Every path git would commit, plus untracked-but-not-ignored files."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True, timeout=120,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover
        pytest.skip(f"git is unavailable: {exc}")
    files: list[Path] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        path = REPO_ROOT / line
        if not path.is_file():
            continue
        if path.suffix.lower() in _SKIP_SUFFIXES:
            continue
        if any(part in _SKIP_DIRS for part in path.relative_to(REPO_ROOT).parts):
            continue
        files.append(path)
    return files


#: Documented placeholders already in the repository.  They are obviously fake
#: (a lowercase alphabet run), they are used to prove that redaction works, and
#: they are not credentials.  Listing them explicitly keeps the scan strict
#: everywhere else instead of loosening the pattern for everyone.
KNOWN_PLACEHOLDERS = frozenset({"s2k-abcdefghijklmnopqrstuvwxyz012345"})

#: Where the live key for this machine is kept.  Deliberately OUTSIDE every
#: repository so it cannot be committed by accident.
LOCAL_SECRET_FILE = Path(r"C:\Users\PC\.leextractor\secrets.env")


def test_no_provider_credential_is_committable():
    offenders: list[str] = []
    for path in _tracked_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for name, pattern in SECRET_PATTERNS:
            for match in pattern.finditer(text):
                if match.group(0) in KNOWN_PLACEHOLDERS:
                    continue
                # Report the location and the pattern name ONLY — never the value.
                line = text[: match.start()].count("\n") + 1
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line} matches {name}")
    assert not offenders, "credential-shaped strings found in committable files:\n" + "\n".join(offenders)


def _live_key() -> str | None:
    """The real S2 key for this machine, if this machine has one."""
    from os import environ

    if value := environ.get("SEMANTIC_SCHOLAR_API_KEY"):
        return value.strip()
    try:
        raw = LOCAL_SECRET_FILE.read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError):
        return None
    for line in raw.splitlines():
        if line.startswith("SEMANTIC_SCHOLAR_API_KEY="):
            return line.split("=", 1)[1].strip()
    return None


def test_the_live_api_key_is_absent_from_every_committable_file():
    """The strongest possible control: search for the actual secret value.

    A pattern scan can only catch shapes; this catches *the* key.  It skips when
    no key is configured (a CI machine), and it never prints the key.
    """
    key = _live_key()
    if not key:
        pytest.skip("no live Semantic Scholar key is configured on this machine")
    assert len(key) > 20, "sanity: refusing to scan for a suspiciously short secret"
    offenders = []
    for path in _tracked_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if key in text:
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, "the live API key appears in committable files: " + ", ".join(offenders)


def test_the_local_secret_file_lives_outside_every_repository():
    if not LOCAL_SECRET_FILE.exists():
        pytest.skip("no local secret file on this machine")
    resolved = LOCAL_SECRET_FILE.resolve()
    for ancestor in resolved.parents:
        assert not (ancestor / ".git").exists(), f"secret file is inside a git repository at {ancestor}"


def test_a_credential_shaped_value_cannot_be_persisted_into_a_project_document(tmp_path):
    """Writing a key into a project document must fail, not silently succeed."""
    from litsearch.v32.errors import V32Error
    from litsearch.v32.store import V32Store

    store = V32Store(tmp_path / "v32")
    project = store.create_project(owner_id="o", name="p", entry_mode="topic")

    project.doc["zotero_map"]["x" * 32] = {"item_key": "K", "secret_value": "placeholder"}
    with pytest.raises(V32Error) as field_error:
        store.save(project)
    assert field_error.value.code == "secret_in_project_document"
    assert "placeholder" not in field_error.value.message
    del project.doc["zotero_map"]["x" * 32]

    project.doc["zotero_map"]["y" * 32] = {"item_key": "s2k-" + "A" * 30}
    with pytest.raises(V32Error) as value_error:
        store.save(project)
    assert value_error.value.code == "secret_in_project_document"
    assert "A" * 30 not in value_error.value.message


def test_an_ordinary_abstract_is_not_mistaken_for_a_secret(tmp_path):
    """The guard must not reject real bibliographic text."""
    from litsearch.v32.store import V32Store

    store = V32Store(tmp_path / "guard")
    project = store.create_project(owner_id="o", name="p", entry_mode="topic")
    store.upsert_paper(project, {
        "paper_id": "a" * 32,
        "title": "Scaling laws and secret tokens in transformer models",
        "abstract": "We compare api_key extraction and token-level attention across 12 corpora.",
    })
    store.save(project)
    assert store.get(project.project_id, owner_id="o").revision == 1


def test_credential_store_default_is_memory_only():
    """Public mode must not persist credentials to disk (doc 10)."""
    from litsearch.v32 import contracts

    assert contracts.credentials_defaults()["public_persistence"] == "memory_only"
    assert contracts.credentials_defaults()["return_secret"] is False
    assert "SECRET" not in repr(contracts.credentials_defaults())
