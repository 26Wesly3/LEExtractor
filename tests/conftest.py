"""Shared test fixtures.

Two jobs:

1. **Offline by default.** Every test must fail immediately if it reaches the
   network, so a provider outage can never be mistaken for a passing suite.

2. **A temp directory that works.** ``tmp_path`` normally resolves under the
   system temp dir. On this machine that directory is not fully accessible
   (creating a subdirectory succeeds, then every later read/scan of it raises
   ``PermissionError: [WinError 5]``), and pytest's own ``tmp_path_factory``
   teardown then turns an otherwise green run into 24 collection/setup errors.
   The overrides below keep temp files inside the repository instead, so
   ``--basetemp`` is never created and that teardown path is never reached.

   The fixture therefore *reuses* one directory per test rather than minting a
   fresh unique name: pytest creates a uniquely-named directory per request
   through ``tmp_path_factory.mktemp``, and cleaning those up is not possible
   here. One directory per test, wiped before use, is equivalent for our
   purposes and leaves nothing behind to clean.
"""

import shutil
from pathlib import Path

import pytest
import requests

_TMP_ROOT = Path(__file__).resolve().parent / "_workspace_tmp"

#: Never collect inside the repo-local temp tree. It holds test fixtures,
#: build leftovers and (currently) an extracted ruff binary, and walking it
#: fails with WinError 5 rather than simply finding nothing.
collect_ignore_glob = ["_workspace_tmp/*"]


@pytest.fixture(autouse=True)
def no_external_network(monkeypatch):
    from litsearch.sources import reset_provider_cooldowns

    reset_provider_cooldowns()
    def blocked(*args, **kwargs):
        raise AssertionError("Unexpected network request in an offline test")
    monkeypatch.setattr(requests.Session, "request", blocked)


def _per_test_dir(name: str) -> Path:
    """A clean directory for one test, inside the repository."""
    path = _TMP_ROOT / name
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def tmp_path(request):
    """Per-test temporary directory that does not touch the system temp dir."""
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in request.node.name)
    return _per_test_dir(safe[:80] or "test")


@pytest.fixture
def tmpdir(request):
    """``py.path.local`` variant, for older-style callers."""
    import py.path

    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in request.node.name)
    return py.path.local(str(_per_test_dir(safe[:80] or "test")))
