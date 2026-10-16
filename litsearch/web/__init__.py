"""Local web application; importing this package starts no server or jobs.

``create_app`` is re-exported lazily (PEP 562).  Eagerly importing
``litsearch.web.api`` here meant that *any* import of a submodule — including
``litsearch.web.repository`` — pulled in the FastAPI application, which imports
the v3.2 router, which imports the v3.2 store, which imports
``litsearch.web.repository``: a cycle that only appeared once the v3.2 router was
wired in.  Deferring the import keeps ``from litsearch import web`` working while
letting a low-level submodule be imported without the application.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from litsearch.web.api import create_app as create_app

__all__ = ["create_app"]

_LAZY = {"create_app": "litsearch.web.api"}


def __getattr__(name: str) -> Any:
    """Resolve ``create_app`` on first access instead of at package import."""
    module_name = _LAZY.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(module_name), name)


def __dir__() -> list[str]:
    return sorted(__all__)

