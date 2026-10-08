"""Shared configuration and small helpers.

Central place for values that were previously hard-coded (notably the
"current year", which was baked in as 2026 across several modules and would
silently break the whole pipeline once the calendar moved past it).
"""

from __future__ import annotations

import os
import re
from datetime import date

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_FILE = os.path.join(BASE_DIR, ".env")


def load_env_file() -> bool:
    """Load BASE_DIR/.env into os.environ (if python-dotenv is available).

    Lets you drop a `.env` next to app.py instead of exporting variables in
    every shell.  Missing file / missing package is fine.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return False
    if not os.path.exists(ENV_FILE):
        return False
    load_dotenv(ENV_FILE, override=False)
    return True


def set_env_value(key: str, value: str) -> None:
    """Persist a setting into BASE_DIR/.env (creates the file if needed)."""
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or any(c in value for c in "\n\r\0"):
        raise ValueError("Invalid environment setting")
    lines: list[str] = []
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, encoding="utf-8") as f:
            lines = f.read().splitlines()
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    quoted = value.replace("\\", "\\\\").replace('"', '\\"')
    new_line = f'{key}="{quoted}"'
    replaced = False
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = new_line
            replaced = True
            break
    if not replaced:
        lines.append(new_line)
    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.environ[key] = value


load_env_file()

# Cache / download defaults -------------------------------------------------

def default_cache_path() -> str:
    return os.path.join(BASE_DIR, "litsearch_cache.db")


def default_download_dir() -> str:
    override = os.environ.get("LEEXTRACTOR_DOWNLOAD_DIR", "").strip()
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), "Downloads", "litsearch_pdfs")


# Year helpers --------------------------------------------------------------

def current_year() -> int:
    """Current calendar year (never hard-code this)."""
    override = os.environ.get("LEEXTRACTOR_YEAR", "").strip()
    if override.isdigit():
        return int(override)
    return date.today().year


def year_from_years_back(years_back: int, floor: int = 1900) -> int:
    """Start year for a 'last N years' window."""
    return max(floor, current_year() - int(years_back))


# Optional integrations -----------------------------------------------------

def unpaywall_email() -> str:
    """Contact e-mail for the Unpaywall API (required by their ToS).

    Set LEEXTRACTOR_UNPAYWALL_EMAIL to enable the Unpaywall OA lookup.
    """
    return os.environ.get("LEEXTRACTOR_UNPAYWALL_EMAIL", "").strip()


def s2_key_configured() -> bool:
    """True if a Semantic Scholar API key is available."""
    return bool(semantic_scholar_api_key())


def semantic_scholar_api_key() -> str:
    """Optional S2 API key — without one you share a very tight global rate limit."""
    return os.environ.get("S2_API_KEY", "").strip()


def openalex_api_key() -> str:
    return os.environ.get("OPENALEX_API_KEY", "").strip()


# Download limits -----------------------------------------------------------

#: Default cap for a single downloaded PDF. 100 MiB comfortably covers every
#: real article PDF (the largest publishers stay well under 50 MB) while
#: stopping a hostile or mis-configured host from filling the disk. Override
#: with LEEXTRACTOR_MAX_PDF_SIZE (bytes).
DEFAULT_MAX_PDF_SIZE = 100 * 1024 * 1024


def max_pdf_size() -> int:
    """Maximum accepted PDF size in **bytes**.

    Read per call rather than at import time so a test (or the GUI settings
    page) can change it without restarting the process.
    """
    raw = os.environ.get("LEEXTRACTOR_MAX_PDF_SIZE", "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return DEFAULT_MAX_PDF_SIZE


def max_pdf_size_mib() -> float:
    """The same limit in MiB, for display."""
    return max_pdf_size() / (1024 * 1024)

