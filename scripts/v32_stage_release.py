"""Stage release: bump the version everywhere, gate on tests, commit and push.

The LEExtractor v3.2 programme ships one stage at a time (M1 -> M2 -> ... -> M5),
and the user asked for the version number to advance and be published
automatically at each stage.  ``litsearch/version.py`` is the single source of
truth for the version (asserted by ``tests/test_release_version.py``), but the
number is *also* embedded in ``pyproject.toml``, the two changelogs and three
README badges/links — so "bump the version" is a five-file edit that is easy to
do half-way.  :func:`bump` does all of it or none of it.

The gate is deliberately conservative: nothing is committed or pushed unless
every named test target exits 0, and the script never force-pushes.

Usage::

    python scripts/v32_stage_release.py --stage M1 --version 0.9.15 \\
        --note "PDF 导入、身份归一、快速列表" --test tests/test_v32_pdf_import.py \\
        --test tests/test_v32_api.py --push

    python scripts/v32_stage_release.py --stage M1 --version 0.9.15 --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

VERSION_FILE = "litsearch/version.py"
PYPROJECT = "pyproject.toml"
CHANGES = "CHANGES.md"
CHANGELOG = "CHANGELOG.md"
READMES = ("README.md", "README.en.md", "README.zh-CN.md")

#: Badge and portable-download link carry the tag form (``v0.9.14``).
TAG_RE = re.compile(r"v\d+\.\d+\.\d+")
VERSION_RE = re.compile(r"\d+\.\d+\.\d+")


class ReleaseError(RuntimeError):
    """The bump could not be applied cleanly."""


# ---------------------------------------------------------------------------
# the bump itself
# ---------------------------------------------------------------------------


def current_version(root: Path = ROOT) -> str:
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', (root / VERSION_FILE).read_text(encoding="utf-8"), re.M)
    if not match:
        raise ReleaseError(f"{VERSION_FILE} does not declare __version__")
    return match.group(1)


def _replace_once(text: str, pattern: str, replacement: str, *, what: str) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.M)
    if count != 1:
        raise ReleaseError(f"could not locate {what} to update")
    return updated


def _set_version_module(root: Path, version: str) -> None:
    path = root / VERSION_FILE
    text = path.read_text(encoding="utf-8")
    path.write_text(_replace_once(text, r'^__version__\s*=\s*"[^"]+"',
                                  f'__version__ = "{version}"', what="__version__"),
                    encoding="utf-8")


def _set_pyproject(root: Path, version: str) -> None:
    path = root / PYPROJECT
    text = path.read_text(encoding="utf-8")
    path.write_text(_replace_once(text, r'^version\s*=\s*"[^"]+"',
                                  f'version = "{version}"', what="[project] version"),
                    encoding="utf-8")


def _prepend_changelog(root: Path, version: str, *, stage: str, date: str, notes: list[str]) -> None:
    tag = f"v{version}"
    entry = [f"## {tag}（{date}）{stage}：LEExtractor v3.2 分阶段实施", ""]
    entry += [f"- {note}" for note in notes]
    entry += [
        "",
        "本阶段按 `docs/v3.2/` 规范实施；验收状态见 `handoff/IMPLEMENTATION_STATUS.md`。",
        "",
    ]
    body = "\n".join(entry)

    for name in (CHANGES, CHANGELOG):
        path = root / name
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)
        # Insert directly above the newest existing "## vX.Y.Z" heading, keeping
        # the file's preamble (title + notes) at the top.
        index = next((i for i, line in enumerate(lines) if line.startswith("## v")), 1)
        lines[index:index] = [body]
        path.write_text("".join(lines), encoding="utf-8")


def _set_readme_version(root: Path, version: str, previous: str) -> None:
    tag, old_tag = f"v{version}", f"v{previous}"
    for name in READMES:
        path = root / name
        text = path.read_text(encoding="utf-8")
        if old_tag not in text:
            raise ReleaseError(f"{name} does not mention {old_tag}; refusing a partial bump")
        path.write_text(text.replace(old_tag, tag), encoding="utf-8")


def _portable_artifacts(root: Path, version: str) -> dict[str, bool]:
    """Report which optional release artefacts exist for this version.

    The portable ZIP is built and published separately; its absence is reported
    rather than fabricated, because the README links to it by exact name.
    """
    download = root / "download"
    names = [
        f"LEExtractor_v{version}_Windows_portable.zip",
        f"LEExtractor_v{version}.zip",
    ]
    return {name: (download / name).is_file() for name in names}


def bump(
    version: str,
    *,
    stage: str,
    notes: list[str] | None = None,
    root: Path = ROOT,
    date: str | None = None,
) -> dict:
    """Apply the whole version bump.  Idempotent for an already-bumped tree."""
    if not VERSION_RE.fullmatch(version):
        raise ReleaseError(f"version must look like X.Y.Z, got {version!r}")
    previous = current_version(root)
    if previous == version:
        return {"changed": [], "version": version, "previous": previous, "already": True}

    stamp = date or datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    _set_version_module(root, version)
    _set_pyproject(root, version)
    _prepend_changelog(root, version, stage=stage, date=stamp, notes=notes or [])
    _set_readme_version(root, version, previous)

    return {
        "changed": [VERSION_FILE, PYPROJECT, CHANGES, CHANGELOG, *READMES],
        "version": version,
        "previous": previous,
        "artifacts": _portable_artifacts(root, version),
        "already": False,
    }


# ---------------------------------------------------------------------------
# git and test gating
# ---------------------------------------------------------------------------


def run(args: list[str], *, root: Path = ROOT, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(args, cwd=root, capture_output=True, text=True, encoding="utf-8")
    if check and result.returncode != 0:
        raise ReleaseError(f"{' '.join(args)} failed ({result.returncode}):\n{result.stderr[-2000:]}")
    return result


def run_tests(targets: list[str], *, root: Path = ROOT) -> dict:
    """Run each pytest target.  Every one must pass before anything is committed.

    A failing target is *recorded*, never raised: the whole point of the gate is
    to report which target failed and then refuse to publish, so raising here
    would hide the other targets' outcomes.
    """
    results = []
    for target in targets:
        result = run([sys.executable, "-m", "pytest", target, "-q", "-p", "no:cacheprovider"],
                     root=root, check=False)
        stdout = (result.stdout or "").strip()
        tail = stdout.splitlines()[-1] if stdout else (result.stderr or "").strip().splitlines()[-1]
        results.append({"target": target, "returncode": result.returncode, "tail": tail})
    failed = [item for item in results if item["returncode"] != 0]
    return {"results": results, "passed": not failed, "failed": failed}


def commit_and_push(message: str, *, root: Path = ROOT, branch: str | None = None, push: bool = False) -> dict:
    if branch:
        run(["git", "checkout", "-B", branch], root=root)
    run(["git", "add", "-A"], root=root)
    run(["git", "commit", "-m", message], root=root)
    head = run(["git", "rev-parse", "HEAD"], root=root).stdout.strip()
    pushed = False
    if push:
        # Never force-push. A rejected push is reported, not worked around.
        run(["git", "push", "-u", "origin", "HEAD"], root=root)
        pushed = True
    return {"head": head, "pushed": pushed}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True, help="milestone label, e.g. M1")
    parser.add_argument("--version", required=True, help="new version, e.g. 0.9.15")
    parser.add_argument("--note", action="append", default=[], help="changelog bullet (repeatable)")
    parser.add_argument("--test", action="append", default=[], help="pytest target that must pass (repeatable)")
    parser.add_argument("--message", default=None, help="commit subject; default derives from stage/version")
    parser.add_argument("--branch", default=None, help="switch to this branch before committing")
    parser.add_argument("--push", action="store_true", help="push to origin after committing")
    parser.add_argument("--dry-run", action="store_true", help="bump nothing; report what would change")
    parser.add_argument("--json", action="store_true", help="machine-readable summary on stdout")
    args = parser.parse_args(argv)

    try:
        if args.dry_run:
            report = {
                "dry_run": True,
                "stage": args.stage,
                "current": current_version(),
                "target": args.version,
                "artifacts": _portable_artifacts(ROOT, args.version),
            }
        else:
            if args.test:
                gate = run_tests(args.test)
                if not gate["passed"]:
                    print(json.dumps({"stage": args.stage, "gate": gate}, ensure_ascii=False, indent=2))
                    print("gate failed; nothing was committed or pushed", file=sys.stderr)
                    return 1
            else:
                print("warning: no --test target given; the gate is not enforced", file=sys.stderr)

            report = bump(args.version, stage=args.stage, notes=args.note)
            subject = args.message or f"release: v{args.version} — {args.stage} LEExtractor v3.2"
            report["git"] = commit_and_push(subject, branch=args.branch, push=args.push)
    except ReleaseError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"stage {args.stage}: {report.get('previous', '?')} -> {args.version}")
        for path in report.get("changed", []):
            print(f"  updated {path}")
        for name, present in (report.get("artifacts") or {}).items():
            print(f"  artifact {name}: {'present' if present else 'NOT PRESENT'}")
        if "git" in report:
            print(f"  commit {report['git']['head'][:12]} pushed={report['git']['pushed']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
