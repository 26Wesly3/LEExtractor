"""Release pipeline regressions (spec H5/H6/H7, contract §1).

Covers: one version source, a packaging script that never imports the runtime
package, whitelist-only packaging, the SHA-256 manifest, and the declared
dependency/Python bounds.
"""

from __future__ import annotations

import ast
import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
import tomllib

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
_TMP_ROOT = Path(__file__).resolve().parent / "_workspace_tmp"

sys.path.insert(0, str(SCRIPTS))

import package_release  # noqa: E402


def version_values() -> dict:
    tree = ast.parse((REPO / "litsearch" / "version.py").read_text(encoding="utf-8"))
    values: dict = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    with contextlib.suppress(ValueError):
                        values[target.id] = ast.literal_eval(node.value)
    return values


def pyproject() -> dict:
    with open(REPO / "pyproject.toml", "rb") as handle:
        return tomllib.load(handle)


@pytest.fixture()
def outdir():
    path = _TMP_ROOT / "release"
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


# ---------------------------------------------------------------------------
# H5: one version source
# ---------------------------------------------------------------------------


def test_version_module_is_the_single_source():
    values = version_values()
    assert values["APP_NAME"] == "LEExtractor"
    assert pyproject()["project"]["version"] == values["__version__"]
    # Compare against the module rather than a literal: a hardcoded expected
    # version turns every release bump into a false failure and teaches people
    # to edit the assertion instead of the version.
    from litsearch.version import __version__ as runtime_version

    assert values["__version__"] == runtime_version
    assert re.fullmatch(r"\d+\.\d+\.\d+", values["__version__"]), values["__version__"]


def test_version_is_not_left_at_an_older_release():
    def parts(text):
        return tuple(int(piece) for piece in text.split("."))

    assert parts(version_values()["__version__"]) >= (0, 9, 1)


def test_litsearch_reexports_the_version():
    import litsearch
    from litsearch import version

    assert litsearch.__version__ == version.__version__
    assert version.version_tag() == f"v{version.__version__}"
    assert version.package_name() == f"LEExtractor_v{version.__version__}"


def test_pyproject_version_is_asserted_not_read_dynamically():
    """The version must be a literal so a wheel can be built without running code."""
    raw = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'^version\s*=\s*"\d+\.\d+\.\d+"', raw, re.MULTILINE)
    assert "dynamic" not in raw.split("[project]")[1].split("[", 1)[0]


def test_launcher_reports_the_same_version(monkeypatch):
    import launch_gui

    assert launch_gui.read_version() == version_values()["__version__"]
    # A launcher must still name the version before the dependencies exist.
    monkeypatch.setattr(launch_gui, "ROOT", REPO / "does-not-exist")
    assert launch_gui.read_version() == "unknown"


def test_launcher_version_flag_prints_the_single_source(capsys):
    import launch_gui

    with pytest.raises(SystemExit) as info:
        launch_gui.main(["--version"])
    assert info.value.code == 0
    assert version_values()["__version__"] in capsys.readouterr().out


# ---------------------------------------------------------------------------
# H6: packaging reads the version without importing the runtime package
# ---------------------------------------------------------------------------


def test_read_version_matches_the_module_value():
    assert package_release.read_version(REPO / "litsearch" / "version.py") == \
        version_values()["__version__"]


def test_package_release_has_no_litsearch_import():
    tree = ast.parse((SCRIPTS / "package_release.py").read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert not [name for name in imported if name == "litsearch" or name.startswith("litsearch.")], \
        f"packaging must not import the runtime package: {imported}"


def test_packaging_run_survives_a_poisoned_litsearch_import(outdir):
    """Run the script in a process where importing litsearch raises."""
    guard = outdir / "guard"
    guard.mkdir()
    (guard / "sitecustomize.py").write_text(
        "import sys\n"
        "class _Blocker:\n"
        "    def find_spec(self, fullname, path=None, target=None):\n"
        "        if fullname == 'litsearch' or fullname.startswith('litsearch.'):\n"
        "            raise ImportError('packaging must not import litsearch')\n"
        "        return None\n"
        "sys.meta_path.insert(0, _Blocker())\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(guard), str(REPO)])

    blocked = subprocess.run([sys.executable, "-c", "import litsearch"],
                             capture_output=True, text=True, env=env, cwd=str(REPO))
    assert blocked.returncode != 0, "the import guard is not active"

    target = outdir / "guarded"
    target.mkdir()
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "package_release.py"), "--root", str(REPO),
         "--output", str(target)],
        capture_output=True, text=True, env=env, cwd=str(REPO))
    assert result.returncode == 0, result.stderr
    assert list(target.glob("*.zip"))


# ---------------------------------------------------------------------------
# H6: whitelist-only packaging + manifest
# ---------------------------------------------------------------------------


def build_fake_project(root: Path) -> None:
    """A project tree that contains everything a release must NOT ship."""
    (root / "litsearch").mkdir(parents=True)
    shutil.copy2(REPO / "litsearch" / "version.py", root / "litsearch" / "version.py")
    (root / "litsearch" / "__init__.py").write_text("", encoding="utf-8")
    (root / "litsearch" / "__pycache__").mkdir()
    (root / "litsearch" / "__pycache__" / "version.cpython-313.pyc").write_bytes(b"\x00")
    (root / "scripts").mkdir()
    (root / "scripts" / "launch_gui.py").write_text("", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_x.py").write_text("", encoding="utf-8")
    # Scratch space created by the test run itself (conftest redirects tmp_path
    # here) must never be packaged, even though it lives under tests/.
    (root / "tests" / "_workspace_tmp" / "tools" / "ruffpkg").mkdir(parents=True)
    (root / "tests" / "_workspace_tmp" / "tools" / "fetch_ruff.py").write_text("", encoding="utf-8")
    (root / "tests" / "_workspace_tmp" / "tools" / "ruffpkg" / "__init__.py").write_text(
        "", encoding="utf-8")
    (root / "tests" / "_workspace_tmp" / "probe_apptest.py").write_text("", encoding="utf-8")
    (root / "tests" / "pytest-cache-files-abc123").mkdir()
    (root / "tests" / "pytest-cache-files-abc123" / "cached.py").write_text("", encoding="utf-8")
    for name in ("app.py", "README.md", "CHANGES.md", "pyproject.toml"):
        (root / name).write_text("x", encoding="utf-8")
    # …and the things that must never be packaged:
    (root / ".env.local").write_text("S2_API_KEY=secret", encoding="utf-8")
    (root / ".env").write_text("S2_API_KEY=secret", encoding="utf-8")
    (root / "litsearch_cache.db").write_bytes(b"sqlite")
    (root / "litsearch_cache.db-wal").write_bytes(b"wal")
    (root / "debug.log").write_text("traceback", encoding="utf-8")
    (root / "sessions.bak").write_text("backup", encoding="utf-8")
    (root / "notes.txt").write_text("scratch", encoding="utf-8")
    (root / ".sessions").mkdir()
    (root / ".sessions" / "autosave.json").write_text("{}", encoding="utf-8")
    (root / "deliverables").mkdir()
    (root / "deliverables" / "LEExtractor_v0.6.0_优化版.zip").write_bytes(b"PK")
    (root / "repro_defects.py").write_text("", encoding="utf-8")


def test_whitelist_excludes_secrets_backups_logs_and_old_zips(tmp_path_factory, outdir):
    root = outdir / "fake_project"
    build_fake_project(root)
    summary = package_release.package(root, outdir / "dist")
    with zipfile.ZipFile(summary["path"]) as archive:
        names = archive.namelist()
    joined = "\n".join(names)
    for forbidden in (".env", "litsearch_cache.db", ".sessions", "deliverables",
                      "__pycache__", ".pyc", ".log", ".bak", "notes.txt",
                      "repro_defects.py", "v0.6.0",
                      "_workspace_tmp", "fetch_ruff", "ruffpkg", "probe_apptest",
                      "pytest-cache-files"):
        assert forbidden not in joined, f"{forbidden} must not be packaged"
    for required in ("litsearch/version.py", "scripts/launch_gui.py", "tests/test_x.py",
                     "app.py", "README.md", "pyproject.toml"):
        assert any(name.endswith(required) for name in names), required


def test_zip_name_uses_the_release_version(outdir):
    summary = package_release.package(REPO, outdir)
    name = Path(summary["path"]).name
    assert name == f"LEExtractor_v{version_values()['__version__']}.zip"
    assert "0.6.0" not in name
    assert summary["version"] == version_values()["__version__"]


def test_release_script_has_no_hardcoded_release_number():
    source = (SCRIPTS / "package_release.py").read_text(encoding="utf-8")
    assert "0.6.0" not in source
    assert not re.search(r"LEExtractor_v\d", source)


def test_manifest_lists_every_file_with_its_sha256(outdir):
    summary = package_release.package(REPO, outdir)
    with zipfile.ZipFile(summary["path"]) as archive:
        manifest_text = archive.read("MANIFEST.sha256").decode("utf-8")
        entries = {}
        for line in manifest_text.strip().splitlines():
            digest, _, name = line.partition("  ")
            entries[name] = digest
        payload = {name: hashlib.sha256(archive.read(name)).hexdigest()
                   for name in archive.namelist() if name != "MANIFEST.sha256"}
    assert entries == payload
    assert all(name.startswith("LEExtractor/") for name in entries)
    sidecar = Path(summary["path"] + ".sha256")
    assert sidecar.exists()
    digest_in_sidecar = sidecar.read_text(encoding="utf-8").split()[0]
    assert digest_in_sidecar == hashlib.sha256(Path(summary["path"]).read_bytes()).hexdigest()
    assert summary["sha256"] == digest_in_sidecar


def test_the_packaged_tree_contains_the_release_documents(outdir):
    from litsearch.version import __version__

    summary = package_release.package(REPO, outdir)
    with zipfile.ZipFile(summary["path"]) as archive:
        names = {name.split("/", 1)[1] for name in archive.namelist() if "/" in name}
    for document in ("README.md", "CHANGES.md", "ARCHITECTURE.md", "ROADMAP.md"):
        assert document in names, f"{document} is missing from the release"


def test_the_archive_is_intact(outdir):
    summary = package_release.package(REPO, outdir)
    with zipfile.ZipFile(summary["path"]) as archive:
        assert archive.testzip() is None


def test_main_prints_a_json_summary(outdir, capsys):
    code = package_release.main(["--root", str(REPO), "--output", str(outdir)])
    assert code == 0
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["version"] == version_values()["__version__"]
    assert payload["files"] > 0


# ---------------------------------------------------------------------------
# H7: declared dependency constraints and Python support range
# ---------------------------------------------------------------------------


def runtime_dependencies() -> dict:
    deps = {}
    for spec in pyproject()["project"]["dependencies"]:
        name = re.split(r"[<>=!~ ]", spec, maxsplit=1)[0].strip()
        deps[name.lower()] = spec
    return deps


def constraints() -> dict:
    text = (REPO / "constraints.txt").read_text(encoding="utf-8")
    pins = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        assert "==" in line, f"constraints must pin exact versions: {line}"
        name, _, pinned = line.partition("==")
        pins[name.strip().lower()] = pinned.strip()
    return pins


def test_constraints_pin_every_runtime_dependency():
    pins = constraints()
    for name in runtime_dependencies():
        assert name in pins, f"{name} is not pinned in constraints.txt"


def test_declared_python_range_is_what_ci_actually_tests():
    pyproject_data = pyproject()
    assert pyproject_data["project"]["requires-python"] == ">=3.10"
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert '"3.10"' in ci and '"3.13"' in ci
    classifiers = pyproject_data["project"].get("classifiers", [])
    assert any("3.10" in item for item in classifiers)


def test_installed_versions_satisfy_the_constraints():
    """The pins are the versions this suite actually ran against."""
    from importlib import metadata

    mismatches = []
    missing = []
    for name, pinned in constraints().items():
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError:
            missing.append(name)
            continue
        if installed != pinned:
            mismatches.append(f"{name}: pinned {pinned}, installed {installed}")
    assert not mismatches, mismatches
    assert not missing, f"declared constraints are not installed here: {missing}"
