"""External candidate records for PDF matching (doc 16), fed into the existing matcher.

``pdf_import.match_parsed`` already scores and decides. The only missing piece was the
candidate source: the default is the project's own corpus. This module supplies
OpenAlex records (DOI lookup first, then title search) in the shape the matcher reads.

Off by default: enable with ``LEEXTRACTOR_PDF_EXTERNAL_MATCH=1``. A failed or slow
lookup returns no candidates, so the file stays a local seed.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

ENV_SWITCH = "LEEXTRACTOR_PDF_EXTERNAL_MATCH"
OPENALEX = "https://api.openalex.org"
_SELECT = "id,display_name,publication_year,doi,authorships"
TIMEOUT_SECONDS = 15


def switch_on() -> bool:
    return os.environ.get(ENV_SWITCH, "").strip() in {"1", "true", "yes", "on"}


def _default_transport(url: str) -> dict[str, Any]:
    key = os.environ.get("OPENALEX_API_KEY", "").strip()
    if key:
        url = url + ("&" if "?" in url else "?") + "api_key=" + urllib.parse.quote(key)
    request = urllib.request.Request(url, headers={"User-Agent": "LEExtractor/pdf-match"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return json.load(response)


def _record(work: dict[str, Any]) -> dict[str, Any]:
    doi = (work.get("doi") or "").replace("https://doi.org/", "").strip().lower() or None
    openalex_id = (work.get("id") or "").rsplit("/", 1)[-1] or None
    authors = [((a or {}).get("author") or {}).get("display_name") or ""
               for a in (work.get("authorships") or [])][:20]
    return {
        "candidate_id": doi or (f"openalex:{openalex_id}" if openalex_id else ""),
        "paper_id": doi or (f"openalex:{openalex_id}" if openalex_id else ""),
        "title": work.get("display_name") or "",
        "year": work.get("publication_year"),
        "doi": doi,
        "authors": authors,
        "source": "openalex",
    }


def lookup(parsed: Any, transport: Callable[[str], dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Candidates for one parsed PDF. Returns [] on any failure (never raises)."""
    fetch = transport or _default_transport
    doi = getattr(parsed, "doi", None)
    title = (getattr(parsed, "title", None) or "").strip()
    try:
        if doi:
            work = fetch(f"{OPENALEX}/works/doi:{urllib.parse.quote(str(doi), safe='/')}?select={_SELECT}")
            if work and work.get("display_name"):
                return [_record(work)]
        if title:
            body = fetch(f"{OPENALEX}/works?search={urllib.parse.quote(title[:300])}&per-page=5&select={_SELECT}")
            return [_record(w) for w in (body or {}).get("results", []) if w.get("display_name")]
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError, TypeError):
        return []
    return []
