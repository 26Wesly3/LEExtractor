"""Single source of truth for the released version.

Everything that names a version — ``pyproject.toml``, ``litsearch/__init__.py``,
the README's current-version line, the CHANGES entry, the UI footer, the release
ZIP name and the validation document — must derive from this module.

Note for packaging: ``scripts/package_release.py`` reads this file by parsing
the source with :mod:`ast` instead of importing it. Importing ``litsearch``
would drag in streamlit, scikit-learn and requests, so a packaging step that
only needs a version string must not depend on the runtime environment.
"""

from __future__ import annotations

__version__ = "0.9.9"

APP_NAME = "LEExtractor"

#: Schema/format version of exported evidence bundles and session files.
BUNDLE_SCHEMA_VERSION = 1


def version_tag() -> str:
    """``"v0.9.4"`` — the form used in release artefacts and the UI footer."""
    return f"v{__version__}"


def package_name() -> str:
    """``"LEExtractor_v0.9.4"`` — base name for release archives."""
    return f"{APP_NAME}_{version_tag()}"
