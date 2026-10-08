"""Package the release: whitelisted source and docs, plus a SHA-256 manifest.

Two rules this script exists to enforce:

* **The version comes from the source, not from the runtime.** ``litsearch``
  imports streamlit/scikit-learn/requests, so a packaging step must not import
  it: ``litsearch/version.py`` is parsed with :mod:`ast` instead.
* **Whitelist, never blacklist.** Only the directories and root files listed
  below are shipped. Local secrets (``.env.local``), session autosaves, logs,
  backups, cache databases and their sidecars, ``__pycache__`` trees and old
  release ZIPs are excluded by construction rather than by a pattern that has
  to anticipate every future artefact.

Usage::

    python scripts/package_release.py [--root DIR] [--output DIR]

The last line of stdout is a JSON summary (path, version, file count, bytes,
archive SHA-256).  ``MANIFEST.sha256`` inside the archive lists the SHA-256 of
every packaged file; ``<archive>.sha256`` next to it carries the archive hash.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_MODULE = Path("litsearch") / "version.py"

#: Directories that are shipped, with the file suffixes allowed inside them.
WHITELIST_DIRS: dict[str, tuple[str, ...]] = {
    "litsearch": (".py",),
    "scripts": (".py",),
    "tests": (".py",),
    ".github": (".yml", ".yaml"),
    # The benchmark harness is part of the deliverable: the spec asks for the
    # dataset format, label guide and runner to ship so a human can label cases
    # and reproduce the comparison. The recorded snapshot is included so the
    # sample report can be regenerated offline.
    "benchmarks": (".md", ".json"),
}

#: Root files that are shipped, by exact name (everything else at the root is
#: packaging output, scratch work or a local artefact).
WHITELIST_ROOT_FILES = (
    "app.py",
    "server.py",
    "pyproject.toml",
    "requirements.txt",
    "constraints.txt",
    "README.md",
    "CHANGES.md",
    "ARCHITECTURE.md",
    "ROADMAP.md",
    "VALIDATION_v0.9.0.md",
    "ONBOARDING.md",
    "V090_INTERFACE_CONTRACT.md",
    ".env.example",
    ".gitignore",
)

#: Directory names that are never traversed, even inside a whitelisted tree.
#: ``_workspace_tmp``/``_sys_tmp`` matter in particular: the test suite redirects
#: ``tmp_path`` there, so a plain "*.py under tests/" rule would otherwise ship
#: test scratch — including unpacked third-party tools.
FORBIDDEN_PARTS = frozenset({
    "__pycache__", ".sessions", "deliverables", "dist", "build", ".venv", "venv",
    ".git", ".pytest_cache", ".ruff_cache", ".pytest_bt", ".pytest_tmp",
    "_workspace_tmp", "_sys_tmp", "artifacts", "backups", "logs",
    "node_modules", ".mypy_cache", ".idea", ".vscode",
})

#: Directory-name prefixes that are never traversed (pytest's own scratch dirs).
FORBIDDEN_DIR_PREFIXES = ("pytest-cache-files-", "pytest-of-")
#: Directory-name suffixes that are never traversed.
FORBIDDEN_DIR_SUFFIXES = ("_tmp", ".tmp")

#: File names that are never shipped, wherever they appear.
FORBIDDEN_NAMES = frozenset({".env", ".env.local", "secrets.toml", "autosave.json"})
FORBIDDEN_NAME_PREFIXES = (".env.",)          # .env.local, .env.production …
FORBIDDEN_SUFFIXES = (".pyc", ".pyo", ".pyd", ".db", ".db-wal", ".db-shm", ".sqlite",
                      ".log", ".bak", ".tmp", ".part", ".zip", ".tar", ".gz", ".7z",
                      ".old", ".orig", ".swp", ".save")

#: The one template file that is allowed to look like an env file.
ALLOWED_ENV_TEMPLATE = ".env.example"


def read_constant(name: str, path: Path | None = None):
    """Read a module-level literal assignment out of a source file (no import)."""
    source_path = Path(path) if path else ROOT / VERSION_MODULE
    try:
        tree = ast.parse(Path(source_path).read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        raise SystemExit(f"cannot read {source_path}: {exc}") from exc
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == name for target in node.targets):
            try:
                return ast.literal_eval(node.value)
            except ValueError as exc:
                raise SystemExit(f"{name} in {source_path} is not a literal: {exc}") from exc
    raise SystemExit(f"no {name} assignment found in {source_path}")


def read_version(path: Path | None = None) -> str:
    """The release version, parsed from ``litsearch/version.py``."""
    version = read_constant("__version__", path)
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise SystemExit(f"unexpected version literal: {version!r}")
    return version


def read_app_name(path: Path | None = None) -> str:
    name = read_constant("APP_NAME", path)
    if not isinstance(name, str) or not name:
        raise SystemExit(f"unexpected APP_NAME literal: {name!r}")
    return name


def _is_scratch_dir(part: str) -> bool:
    return (part in FORBIDDEN_PARTS
            or part.startswith(FORBIDDEN_DIR_PREFIXES)
            or part.endswith(FORBIDDEN_DIR_SUFFIXES))


def _is_forbidden(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    if any(_is_scratch_dir(part) for part in relative.parts[:-1]):
        return True
    if path.name in FORBIDDEN_NAMES:
        return True
    if path.name.startswith(FORBIDDEN_NAME_PREFIXES) and path.name != ALLOWED_ENV_TEMPLATE:
        return True
    return path.name.endswith(FORBIDDEN_SUFFIXES)


def iter_release_files(root: Path) -> list[Path]:
    """Every file the release ships, in a deterministic order."""
    files: list[Path] = []
    for name in WHITELIST_ROOT_FILES:
        candidate = root / name
        if candidate.is_file() and not _is_forbidden(candidate, root):
            files.append(candidate)
    for directory, suffixes in WHITELIST_DIRS.items():
        base = root / directory
        if not base.is_dir():
            continue
        for candidate in sorted(base.rglob("*")):
            if not candidate.is_file():
                continue
            if candidate.suffix.lower() not in suffixes:
                continue
            if _is_forbidden(candidate, root):
                continue
            files.append(candidate)
    return sorted(set(files))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(256 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(root: Path, files: list[Path], prefix: str) -> str:
    lines = [f"{_sha256_file(path)}  {prefix}/{path.relative_to(root).as_posix()}"
             for path in files]
    return "\n".join(lines) + "\n"


def package(root: Path | str = ROOT, output_dir: Path | str | None = None,
            prefix: str | None = None) -> dict:
    """Build the release archive and return a JSON-serialisable summary."""
    root = Path(root).resolve()
    version = read_version(root / VERSION_MODULE)
    app_name = read_app_name(root / VERSION_MODULE)
    prefix = prefix or app_name
    archive_name = f"{app_name}_v{version}.zip"
    output = Path(output_dir) if output_dir else root / "deliverables"
    output.mkdir(parents=True, exist_ok=True)
    archive_path = output / archive_name

    files = iter_release_files(root)
    if not files:
        raise SystemExit(f"nothing to package under {root}")
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, f"{prefix}/{path.relative_to(root).as_posix()}")
        archive.writestr("MANIFEST.sha256", build_manifest(root, files, prefix))
    with zipfile.ZipFile(archive_path) as archive:
        if archive.testzip() is not None:
            raise SystemExit("the archive failed its integrity check")

    digest = _sha256_file(archive_path)
    (output / f"{archive_name}.sha256").write_text(f"{digest}  {archive_name}\n",
                                                   encoding="utf-8")
    return {
        "path": str(archive_path),
        "package": f"{app_name}_v{version}",
        "version": version,
        "files": len(files),
        "bytes": archive_path.stat().st_size,
        "sha256": digest,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Package the LEExtractor release")
    parser.add_argument("--root", default=str(ROOT), help="project root to package")
    parser.add_argument("--output", default="", help="output directory (default <root>/deliverables)")
    args = parser.parse_args(argv)
    summary = package(args.root, args.output or None)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
