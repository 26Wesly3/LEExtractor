"""PDF downloader with open-access fallback: arXiv → Unpaywall → OpenAlex OA.

Only legitimate, publisher-sanctioned sources are used.  The previous
implementation fell back to scraping Sci-Hub for paywalled articles; that
tier has been removed (it is copyright infringement in most jurisdictions,
and the mirrors are unreliable).  Everything below resolves to an
author/publisher-provided open-access copy, or reports "not available".
"""

import logging
import re
import time
from pathlib import Path

import requests

from litsearch.config import default_download_dir, unpaywall_email
from litsearch.models import Paper

logger = logging.getLogger(__name__)

UNPAYWALL_BASE = "https://api.unpaywall.org/v2"


class PaperDownloader:
    """Download PDFs from multiple sources with automatic fallback."""

    def __init__(self, download_dir: str = ""):
        self._download_dir = Path(download_dir or default_download_dir())
        self._download_dir.mkdir(parents=True, exist_ok=True)
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
        # 1. Direct OA URL from paper metadata
        if paper.url and self._is_pdf_url(paper.url):
            return paper.url

        # 2. arXiv preprint
        arxiv_id = self._extract_arxiv_id(paper)
        if arxiv_id:
            return f"https://arxiv.org/pdf/{arxiv_id}.pdf"

        # 3. OpenAlex: best open-access location
        oa_url = self._check_openalex_oa(paper)
        if oa_url:
            return oa_url

        # 4. Unpaywall (aggregates publisher-hosted OA copies)
        return self._unpaywall_url(paper.doi) if paper.doi else None

    def download_pdf(
        self, paper: Paper, filename: str | None = None, skip_existing: bool = True
    ) -> str | None:
        """Download a paper PDF. Returns the file path or None on failure.

        Args:
            paper: Paper to download.
            filename: Custom filename (without extension). Auto-generated if None.
            skip_existing: Skip if file already exists.
        """
        if filename is None:
            filename = self._sanitize_filename(paper)

        filepath = self._download_dir / f"{filename}.pdf"
        if skip_existing and filepath.exists():
            logger.info("Already downloaded: %s", filepath)
            return str(filepath)

        url = self.get_download_url(paper)
        if url is None:
            logger.warning("No download URL for: %s", paper.title[:60])
            return None

        try:
            resp = self._session.get(url, timeout=60, stream=True)
            content_type = resp.headers.get("content-type", "").lower()
            body = resp.content
            if resp.status_code != 200:
                logger.warning(
                    "Download failed for %s: HTTP %d", paper.doi, resp.status_code
                )
                return None
            # Guard against HTML "not found"/captcha pages being saved as PDFs.
            if "pdf" not in content_type and body[:5] != b"%PDF-":
                logger.warning(
                    "Not a PDF for %s (content-type=%s, %d bytes)",
                    paper.doi, content_type, len(body),
                )
                return None
            if len(body) < 10000:
                logger.warning(
                    "Download too small for %s: %d bytes", paper.doi, len(body)
                )
                return None
            with open(filepath, "wb") as f:
                f.write(body)
            logger.info("Downloaded: %s (%d KB)", filename, len(body) // 1024)
            return str(filepath)
        except Exception as e:
            logger.warning("Download error for %s: %s", paper.doi or paper.title[:40], e)
            return None

    def batch_download(
        self, papers: list[Paper], delay_seconds: float = 1.0
    ) -> list[tuple[Paper, str | None]]:
        """Download multiple papers sequentially. Returns (paper, path_or_None)."""
        results = []
        for paper in papers:
            path = self.download_paper(paper)
            results.append((paper, path))
            if delay_seconds:
                time.sleep(delay_seconds)  # Be polite to OA hosts
        return results

    def get_download_dir(self) -> str:
        return str(self._download_dir)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

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
        if not paper.doi:
            return None
        try:
            resp = self._session.get(
                f"https://api.openalex.org/works/https://doi.org/{paper.doi}",
                params={"select": "open_access,primary_location,best_oa_location"},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                oa = data.get("open_access", {})
                if oa.get("is_oa") and oa.get("oa_url"):
                    return oa["oa_url"]
                for loc_key in ("best_oa_location", "primary_location"):
                    loc = data.get(loc_key) or {}
                    for url_key in ("pdf_url", "landing_page_url"):
                        url = loc.get(url_key)
                        if url and self._looks_like_pdf(url):
                            return url
        except Exception:
            pass
        return None

    @staticmethod
    def _extract_arxiv_id(paper: Paper) -> str | None:
        """Extract arXiv ID from paper metadata."""
        # Check abstract and URL for arXiv IDs
        text_sources = [paper.abstract or "", paper.url or "", paper.id]
        for text in text_sources:
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
        """Generate a clean filename: Year_FirstAuthor_ShortTitle."""
        year = paper.year or "XXXX"
        author = paper.authors[0].name.split()[-1] if paper.authors else "Unknown"
        title_words = re.sub(r"[^\w\s]", "", paper.title)[:60]
        title_words = re.sub(r"\s+", "_", title_words.strip())
        return f"{year}_{author}_{title_words}"[:200]
