"""PDF downloader with open-access fallback: arXiv → Unpaywall → OpenAlex OA.

Only legitimate, publisher-sanctioned sources are used.  The previous
implementation fell back to scraping Sci-Hub for paywalled articles; that
tier has been removed (it is copyright infringement in most jurisdictions,
and the mirrors are unreliable).  Everything below resolves to an
author/publisher-provided open-access copy, or reports "not available".

Two decisions are deliberately kept *separate* (spec G4):

* **Is the host allowed?** — protocol + resolved address of the initial URL and
  of every redirect hop.  Localhost, private, link-local, shared, multicast and
  otherwise non-global addresses are refused *before* a connection is made.
* **Is this a legitimate open-access copy?** — that is the resolver order above,
  not an address check.

A download that does not happen is reported as ``not_retrieved`` and never
turns into a full-text exclusion by itself: acquisition status and PRISMA
eligibility are different facts.

Downloads are streamed to a ``.part`` file next to the destination, size-capped
on the *decoded* bytes actually received, validated by head/tail, then moved
into place atomically.  A failure removes the partial file and closes the
response; the previous good file, if any, is left untouched.
"""

from __future__ import annotations

import contextlib
import hashlib
import ipaddress
import logging
import os
import re
import socket
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

from litsearch.config import (
    default_download_dir,
    max_pdf_size,
    openalex_api_key,
    unpaywall_email,
)
from litsearch.identifiers import normalize_arxiv, normalize_doi
from litsearch.models import Paper

logger = logging.getLogger(__name__)

UNPAYWALL_BASE = "https://api.unpaywall.org/v2"

#: Exact provenance record required by the interface contract (§10).
PROVENANCE_KEYS = (
    "resolver",
    "source",
    "final_url",
    "bytes",
    "sha256",
    "fetched_at",
    "failure_reason",
)

# Acquisition status. ``not_retrieved`` means "we could not obtain a copy" and
# is explicitly *not* a screening decision.
STATUS_DOWNLOADED = "downloaded"
STATUS_EXISTING = "existing"
STATUS_NOT_RETRIEVED = "not_retrieved"

# Failure reasons (stable tokens, safe to persist and to assert on).
FAIL_NONE = ""
FAIL_SCHEME = "unsupported_scheme"
FAIL_DNS = "dns_failure"
FAIL_ADDRESS = "non_public_address"
FAIL_REDIRECTS = "too_many_redirects"
FAIL_HTTP = "http_error"
FAIL_SIZE = "size_limit_exceeded"
FAIL_NOT_PDF = "not_a_pdf"
FAIL_EMPTY = "empty_body"
FAIL_TIMEOUT = "timeout"
FAIL_NETWORK = "network_error"
FAIL_TRUNCATED = "truncated_stream"
FAIL_TARGET = "target_unusable"
FAIL_NO_URL = "no_candidate_url"

MAX_REDIRECTS = 5
CHUNK_SIZE = 64 * 1024
DEFAULT_TOTAL_TIMEOUT = 120.0
REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})

#: Windows filename limits. 260 is the classic MAX_PATH; stay below it because
#: the download directory itself is part of the budget. MAX_FILENAME counts the
#: whole file name, extension included.
PDF_SUFFIX = ".pdf"
MAX_NAME_PART = 100
MAX_FILENAME = 120
MAX_PATH = 250

_ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul",
     *(f"com{i}" for i in range(10)), *(f"lpt{i}" for i in range(10))}
)


# ---------------------------------------------------------------------------
# Network-address safety (spec G3)
# ---------------------------------------------------------------------------


def resolve_host(host: str, port: int | None = None) -> list[str]:
    """Every address ``host`` resolves to.

    Raises :class:`socket.gaierror` (an :class:`OSError`) when DNS fails.  Tests
    monkeypatch this function so no test ever performs a real lookup.
    """
    infos = socket.getaddrinfo(host, port or 443, proto=socket.IPPROTO_TCP)
    addresses: list[str] = []
    for info in infos:
        sockaddr = info[4] if len(info) > 4 else None
        if sockaddr and sockaddr[0] not in addresses:
            addresses.append(sockaddr[0])
    return addresses


def is_public_address(value: str) -> bool:
    """True only for a globally routable address.

    Every non-global category is rejected explicitly — ``is_global`` alone is
    not enough because Python treats some multicast ranges as global.
    """
    try:
        address = ipaddress.ip_address(str(value).split("%")[0])
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if (address.is_private or address.is_loopback or address.is_link_local
            or address.is_multicast or address.is_reserved or address.is_unspecified):
        return False
    return bool(address.is_global)


def check_url_safety(url: str) -> str:
    """Return ``""`` when a URL may be fetched, else the refusal reason.

    Applies to the *initial* URL and to every redirect hop, before the request
    is sent — checking only the final URL is the defect this replaces.
    """
    parsed = urlparse(url or "")
    if parsed.scheme not in ("http", "https"):
        return FAIL_SCHEME
    if parsed.username or parsed.password:
        return FAIL_ADDRESS
    host = parsed.hostname
    if not host:
        return FAIL_ADDRESS
    if host.lower().rstrip(".") in {"localhost"} or host.lower().endswith(".localhost"):
        return FAIL_ADDRESS
    try:
        port = parsed.port
    except ValueError:
        return FAIL_ADDRESS
    try:
        literal = ipaddress.ip_address(host.split("%")[0])
    except ValueError:
        literal = None
    if literal is not None:
        return FAIL_NONE if is_public_address(str(literal)) else FAIL_ADDRESS
    try:
        addresses = resolve_host(host, port or (443 if parsed.scheme == "https" else 80))
    except (OSError, UnicodeError) as exc:
        logger.debug("Refusing %s: DNS lookup failed (%s)", host, exc)
        return FAIL_DNS
    if not addresses or any(not is_public_address(address) for address in addresses):
        return FAIL_ADDRESS if addresses else FAIL_DNS
    return FAIL_NONE


# ---------------------------------------------------------------------------
# Filenames (spec G5/G6)
# ---------------------------------------------------------------------------


def sanitize_filename_part(text: str, max_length: int = MAX_NAME_PART) -> str:
    """Make one filename component safe on Windows.

    Handles illegal characters, control characters, trailing dots/spaces,
    over-long components and the reserved device names (CON, PRN, AUX, NUL,
    COM0-9, LPT0-9) which cannot be used even with an extension.
    """
    cleaned = _ILLEGAL_CHARS.sub("_", str(text or ""))
    cleaned = re.sub(r"\s+", "_", cleaned.strip())
    cleaned = re.sub(r"_{2,}", "_", cleaned).strip(" .")
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].strip(" ._")
    if not cleaned:
        return "untitled"
    if cleaned.split(".")[0].strip(" .").lower() in _RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned


def paper_filename_stem(paper: Paper) -> str:
    """``Year_FirstAuthor_ShortTitle_<hash>`` — deterministic and collision-free.

    The hash of the canonical id is what keeps two papers with the same year,
    author and title from overwriting each other.
    """
    year = sanitize_filename_part(str(paper.year or "XXXX"), 8)
    author = "Unknown"
    if paper.authors:
        name = (paper.authors[0].name or "").strip()
        if name:
            author = name.split()[-1]
    author = sanitize_filename_part(author, 40)
    title = sanitize_filename_part(paper.title or "", 80)
    digest = hashlib.sha256(paper.canonical_id.encode("utf-8")).hexdigest()[:10]
    budget = MAX_FILENAME - len(PDF_SUFFIX)
    stem = f"{year}_{author}_{title}_{digest}"[:budget].strip(" ._")
    return stem or f"paper_{digest}"


# ---------------------------------------------------------------------------
# Outcome / provenance
# ---------------------------------------------------------------------------


def _new_provenance(resolver: str = "", source: str = "", final_url: str = "",
                    size: int = 0, sha256: str = "", failure_reason: str = "") -> dict:
    return {
        "resolver": resolver,
        "source": source,
        "final_url": final_url,
        "bytes": int(size),
        "sha256": sha256,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "failure_reason": failure_reason,
    }


@dataclass
class DownloadOutcome:
    """What happened to one acquisition attempt.

    ``status`` is the acquisition state (``downloaded`` / ``existing`` /
    ``not_retrieved``); it is never a screening decision.
    """

    path: str | None = None
    status: str = STATUS_NOT_RETRIEVED
    provenance: dict = field(default_factory=lambda: _new_provenance())

    @property
    def ok(self) -> bool:
        return self.path is not None

    def to_dict(self) -> dict:
        return {"status": self.status, "path": self.path, **self.provenance}


class PaperDownloader:
    """Download PDFs from multiple sources with automatic fallback."""

    def __init__(self, download_dir: str = "", total_timeout: float = DEFAULT_TOTAL_TIMEOUT):
        self._download_dir = Path(download_dir or default_download_dir())
        self._download_dir.mkdir(parents=True, exist_ok=True)
        self._total_timeout = float(total_timeout)
        self._request_timeout = (10.0, 30.0)
        self._last_provenance: dict = _new_provenance(failure_reason=FAIL_NO_URL)
        self._attempts: list[dict] = []
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36"
        })

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_download_url(self, paper: Paper) -> str | None:
        """Find a downloadable PDF URL for a paper.

        Priority: arXiv preprint → OpenAlex/Unpaywall open-access copy.
        Returns None if no legitimate open-access source is available.
        """
        return next(self._candidate_urls(paper), None)

    def download_pdf(
        self, paper: Paper, filename: str | None = None, skip_existing: bool = True
    ) -> str | None:
        """Download a paper PDF. Returns the file path or None on failure.

        Args:
            paper: Paper to download.
            filename: Custom filename (without extension). Auto-generated if None.
            skip_existing: Reuse a valid local file instead of re-downloading.
        """
        return self.download_pdf_with_provenance(paper, filename, skip_existing).path

    def download_pdf_with_provenance(
        self, paper: Paper, filename: str | None = None, skip_existing: bool = True
    ) -> DownloadOutcome:
        """Download and return the full :class:`DownloadOutcome`.

        Returns ``status="not_retrieved"`` with a machine-readable
        ``failure_reason`` when no copy could be obtained; the caller decides
        what that means for screening — acquisition is not eligibility.
        """
        filepath = self._target_path(paper, filename)
        if filepath.exists() and filepath.is_dir():
            return self._finish(None, STATUS_NOT_RETRIEVED, _new_provenance(
                failure_reason=FAIL_TARGET))
        if skip_existing and filepath.is_file() and self.pdf_is_valid(filepath):
            provenance = _new_provenance(
                resolver="local",
                source="local",
                final_url=filepath.as_uri(),
                size=filepath.stat().st_size,
                sha256=self._sha256_file(filepath),
            )
            logger.info("Already downloaded: %s", filepath)
            return self._finish(str(filepath), STATUS_EXISTING, provenance)

        attempted: set[str] = set()
        outcome: DownloadOutcome | None = None
        for url in self._candidate_urls(paper):
            if not url or url in attempted:
                continue
            attempted.add(url)
            outcome = self._download_url(url, filepath, paper)
            if outcome.path:
                return outcome
        if outcome is None:
            outcome = self._finish(None, STATUS_NOT_RETRIEVED, _new_provenance(
                failure_reason=FAIL_NO_URL))
        logger.warning("No working PDF URL for: %s", (paper.title or "")[:60])
        return outcome

    def batch_download(
        self, papers: list[Paper], delay_seconds: float = 1.0
    ) -> list[tuple[Paper, str | None]]:
        """Download multiple papers sequentially. Returns (paper, path_or_None)."""
        results = []
        for paper in papers:
            path = self.download_pdf(paper)
            results.append((paper, path))
            if delay_seconds:
                time.sleep(delay_seconds)  # Be polite to OA hosts
        return results

    def get_download_dir(self) -> str:
        return str(self._download_dir)

    @property
    def last_provenance(self) -> dict:
        """Provenance of the most recent attempt (success or failure)."""
        return dict(self._last_provenance)

    @property
    def attempts(self) -> list[dict]:
        """Every provenance record produced by this downloader, in order."""
        return [dict(row) for row in self._attempts]

    def safe_filename(self, paper: Paper) -> str:
        """The deterministic file stem used for ``paper``."""
        return paper_filename_stem(paper)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _finish(self, path: str | None, status: str, provenance: dict) -> DownloadOutcome:
        self._last_provenance = dict(provenance)
        self._attempts.append(dict(provenance))
        return DownloadOutcome(path=path, status=status, provenance=dict(provenance))

    def _target_path(self, paper: Paper, filename: str | None) -> Path:
        name_budget = MAX_FILENAME - len(PDF_SUFFIX)
        if filename is None:
            stem = paper_filename_stem(paper)
        else:
            if Path(filename).name != filename or any(c in filename for c in ("/", "\\", ":")):
                raise ValueError("Filename must be a file name, without a directory")
            stem = sanitize_filename_part(Path(filename).stem, name_budget) or "paper"
        filepath = self._download_dir / f"{stem}{PDF_SUFFIX}"
        overflow = len(str(filepath)) - MAX_PATH
        if overflow > 0:
            stem = stem[: max(16, len(stem) - overflow)].strip(" ._") or "paper"
            filepath = self._download_dir / f"{stem}{PDF_SUFFIX}"
        return filepath

    @staticmethod
    def _valid_pdf(body: bytes) -> bool:
        """Head/tail check on an in-memory body (kept for compatibility)."""
        return body[:1024].lstrip().startswith(b"%PDF-") and b"%%EOF" in body[-2048:]

    @staticmethod
    def pdf_is_valid(path: Path) -> bool:
        """Validate a PDF on disk without reading the whole file into memory."""
        try:
            size = os.path.getsize(path)
            if size <= 0:
                return False
            with open(path, "rb") as handle:
                if not handle.read(1024).lstrip().startswith(b"%PDF-"):
                    return False
                handle.seek(max(0, size - 2048))
                tail = handle.read(2048)
            return b"%%EOF" in tail
        except OSError:
            return False

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _declared_length(response) -> int | None:
        headers = getattr(response, "headers", None) or {}
        raw = headers.get("content-length") if hasattr(headers, "get") else None
        if raw is None:
            return None
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError):
            return None            # forged/garbage header: fall back to counting
        return value if value >= 0 else None

    def _open_stream(self, url: str, deadline: float):
        """Follow redirects one hop at a time, validating each hop first.

        Returns ``(response_or_None, final_url, failure_reason)``.
        """
        current = url
        for _hop in range(MAX_REDIRECTS + 1):
            if time.monotonic() > deadline:
                return None, current, FAIL_TIMEOUT
            refusal = check_url_safety(current)
            if refusal:
                logger.warning("Refusing %s: %s", current, refusal)
                return None, current, refusal
            try:
                response = self._session.get(
                    current, timeout=self._request_timeout, stream=True, allow_redirects=False
                )
            except Exception as exc:
                logger.warning("Request failed for %s: %s", current, exc)
                return None, current, FAIL_NETWORK
            headers = getattr(response, "headers", None) or {}
            location = headers.get("location") if response.status_code in REDIRECT_CODES else None
            if location:
                response.close()
                current = urljoin(current, location)
                continue
            return response, current, FAIL_NONE
        return None, current, FAIL_REDIRECTS

    def _download_url(self, url: str, filepath: Path, paper: Paper) -> DownloadOutcome:
        resolver = self._classify_resolver(url, paper)
        deadline = time.monotonic() + self._total_timeout
        cap = max_pdf_size()
        response, final_url, failure = self._open_stream(url, deadline)
        source = urlparse(final_url).netloc
        if response is None:
            return self._finish(None, STATUS_NOT_RETRIEVED, _new_provenance(
                resolver=resolver, source=source, final_url=final_url, failure_reason=failure))
        try:
            if response.status_code != 200:
                logger.warning("Download failed for %s: HTTP %d", paper.doi, response.status_code)
                return self._failure(resolver, source, final_url, f"{FAIL_HTTP}_{response.status_code}")
            declared = self._declared_length(response)
            if declared is not None and declared > cap:
                logger.warning("Refusing %s: declared Content-Length %d exceeds the %d byte cap",
                               final_url, declared, cap)
                return self._failure(resolver, source, final_url, FAIL_SIZE)
            return self._stream_to_file(response, filepath, resolver, source, final_url,
                                        declared, deadline, cap)
        except Exception as exc:  # defensive: never let one paper kill the batch
            logger.warning("Download error for %s: %s", paper.doi or (paper.title or "")[:40], exc)
            return self._failure(resolver, source, final_url, FAIL_NETWORK)
        finally:
            with contextlib.suppress(Exception):
                response.close()

    def _stream_to_file(self, response, filepath: Path, resolver: str, source: str,
                        final_url: str, declared: int | None, deadline: float,
                        cap: int) -> DownloadOutcome:
        part_path = ""
        digest = hashlib.sha256()
        total = 0
        failure = FAIL_NONE
        try:
            with tempfile.NamedTemporaryFile(
                    mode="wb", dir=str(filepath.parent), prefix=filepath.name + ".",
                    suffix=".part", delete=False) as handle:
                part_path = handle.name
                for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                    if not chunk:
                        continue
                    if time.monotonic() > deadline:
                        failure = FAIL_TIMEOUT
                        break
                    total += len(chunk)
                    if total > cap:
                        failure = FAIL_SIZE
                        break
                    digest.update(chunk)
                    handle.write(chunk)
            if failure == FAIL_NONE and total == 0:
                failure = FAIL_EMPTY
            if failure == FAIL_NONE and declared is not None and declared > total:
                # A body shorter than its own declared length is a truncated stream.
                failure = FAIL_TRUNCATED
            if failure == FAIL_NONE and not self.pdf_is_valid(Path(part_path)):
                failure = FAIL_NOT_PDF
            if failure:
                if failure == FAIL_SIZE:
                    logger.warning("Refusing %s: body exceeded the %d byte cap", final_url, cap)
                return self._failure(resolver, source, final_url, failure)
            os.replace(part_path, filepath)
            part_path = ""
            logger.info("Downloaded: %s (%d KB)", filepath.name, total // 1024)
            return self._finish(str(filepath), STATUS_DOWNLOADED, _new_provenance(
                resolver=resolver, source=source, final_url=final_url,
                size=total, sha256=digest.hexdigest()))
        except Exception as exc:
            logger.warning("Stream failed for %s: %s", final_url, exc)
            reason = FAIL_TRUNCATED if total else FAIL_NETWORK
            return self._failure(resolver, source, final_url, reason)
        finally:
            if part_path and os.path.exists(part_path):
                with contextlib.suppress(OSError):
                    os.remove(part_path)

    def _failure(self, resolver: str, source: str, final_url: str, reason: str) -> DownloadOutcome:
        return self._finish(None, STATUS_NOT_RETRIEVED, _new_provenance(
            resolver=resolver, source=source, final_url=final_url, failure_reason=reason))

    def _unpaywall_url(self, doi: str) -> str | None:
        """Ask Unpaywall for a publisher-hosted open-access PDF.

        Requires an e-mail (Unpaywall ToS): set LEEXTRACTOR_UNPAYWALL_EMAIL.
        """
        email = unpaywall_email()
        if not email:
            return None
        try:
            resp = self._session.get(
                f"{UNPAYWALL_BASE}/{doi}", params={"email": email}, timeout=15
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            if not data.get("is_oa"):
                return None
            for loc in [data.get("best_oa_location")] + (data.get("oa_locations") or []):
                if not loc:
                    continue
                for key in ("url_for_pdf", "url"):
                    url = loc.get(key)
                    if url and self._looks_like_pdf(url):
                        return url
        except Exception as e:
            logger.warning("Unpaywall lookup failed for %s: %s", doi, e)
        return None

    @staticmethod
    def _looks_like_pdf(url: str) -> bool:
        lowered = url.lower()
        return lowered.endswith(".pdf") or "/pdf" in lowered or "pdf" in lowered

    def _check_openalex_oa(self, paper: Paper) -> str | None:
        """Check OpenAlex for an open-access PDF URL."""
        identifier = normalize_doi(paper.doi) or paper.identifiers.openalex_id
        if not identifier:
            return None
        try:
            resp = self._session.get(
                "https://api.openalex.org/works/" + (f"https://doi.org/{identifier}" if normalize_doi(identifier) else identifier),
                params={"select": "open_access,primary_location,best_oa_location"},
                headers={"Authorization": f"Bearer {openalex_api_key()}"} if openalex_api_key() else {},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                oa = data.get("open_access", {})
                if oa.get("is_oa") and oa.get("oa_url") and self._looks_like_pdf(oa["oa_url"]):
                    return oa["oa_url"]
                for loc_key in ("best_oa_location", "primary_location"):
                    loc = data.get(loc_key) or {}
                    for url_key in ("pdf_url", "landing_page_url"):
                        url = loc.get(url_key)
                        if url and self._looks_like_pdf(url):
                            return url
        except Exception as exc:
            logger.debug("OpenAlex OA lookup failed for %s: %s", identifier, exc)
        return None

    def _candidate_urls(self, paper: Paper):
        """Resolve lazily so a working direct PDF does not need extra APIs."""
        if paper.url and self._is_pdf_url(paper.url):
            yield paper.url

        # 2. arXiv preprint
        arxiv_id = self._extract_arxiv_id(paper)
        if arxiv_id:
            yield f"https://arxiv.org/pdf/{arxiv_id}.pdf"

        # 3. OpenAlex: best open-access location
        oa_url = self._check_openalex_oa(paper)
        if oa_url:
            yield oa_url

        # 4. Unpaywall (aggregates publisher-hosted OA copies)
        if paper.doi and (url := self._unpaywall_url(paper.doi)):
            yield url

    @staticmethod
    def _classify_resolver(url: str, paper: Paper) -> str:
        """Which resolution rule produced ``url`` (provenance field ``resolver``)."""
        host = (urlparse(url).netloc or "").lower()
        if "arxiv.org" in host:
            return "arxiv"
        if "unpaywall.org" in host:
            return "unpaywall"
        if "openalex.org" in host:
            return "openalex"
        if paper.url and url == paper.url:
            return "paper_url"
        return "direct"

    @staticmethod
    def _extract_arxiv_id(paper: Paper) -> str | None:
        """Extract arXiv ID from paper metadata."""
        if value := normalize_arxiv(paper.identifiers.arxiv_id):
            return value
        text_sources = [paper.url or "", paper.id]
        for text in text_sources:
            if value := normalize_arxiv(text):
                return value
            m = re.search(r"arxiv[:\s]*(\d{4}\.\d{4,5})", text, re.IGNORECASE)
            if m:
                return m.group(1)
            # Old arXiv format
            m = re.search(
                r"arxiv[:\s]*([a-z\-]+/\d{7})", text, re.IGNORECASE
            )
            if m:
                return m.group(1)
        return None

    @staticmethod
    def _is_pdf_url(url: str) -> bool:
        return url.lower().endswith(".pdf") or "/pdf/" in url.lower()

    @staticmethod
    def _sanitize_filename(paper: Paper) -> str:
        """Generate a clean filename: Year_FirstAuthor_ShortTitle_<hash>."""
        return paper_filename_stem(paper)
