"""Dependency-free JSON persistence helpers shared by both storage layers.

These two functions used to live in :mod:`litsearch.web.repository`.  They were
moved here because that module is reached through ``litsearch.web.__init__``,
which imports the FastAPI application, which imports the v3.2 router, which
imports the v3.2 store — so ``from litsearch.web.repository import atomic_json``
inside the v3.2 layer closed a cycle::

    litsearch.v32.store -> litsearch.web.repository
        -> litsearch.web (__init__) -> litsearch.web.api
        -> litsearch.v32.api -> litsearch.v32.jobs -> litsearch.v32.store

A twelve-line helper is not worth a cycle, and a *new* storage layer should not
depend on the legacy web package to write a file.  ``litsearch.web.repository``
now re-exports both names, so its own public API and every existing importer are
unchanged, and there is still exactly one implementation.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

__all__ = ["atomic_json", "now", "utc_now"]


def utc_now() -> datetime:
    """Timezone-aware UTC now.  The only clock these layers may read."""
    return datetime.now(timezone.utc)


def now() -> str:
    """RFC3339 UTC timestamp used by every persisted record."""
    return utc_now().isoformat()


def atomic_json(path: Path, data: dict) -> None:
    """Write ``data`` as JSON to ``path`` atomically.

    Serialises to a temporary file in the same directory, ``fsync``s it and then
    ``os.replace``\\ s it over the target, so a crash or a full disk can never
    leave a half-written project behind.  ``allow_nan=False`` is not incidental:
    it is the last line of defence for the rule that an unknown number is ``null``
    and never ``NaN``.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".write-", delete=False) as handle:
            name = handle.name
            json.dump(data, handle, ensure_ascii=False, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)
