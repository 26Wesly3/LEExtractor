"""Real-network M1 smoke check: does the Semantic Scholar adapter actually work?

Run this ONLY with a live key available. It reports counts and titles, never the
key, and it writes nothing.

    $env:PYTHONUTF8="1"; python scripts/v32_provider_probe.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

SECRET_FILE = Path(r"C:\Users\PC\.leextractor\secrets.env")


def load_key() -> bool:
    if os.environ.get("SEMANTIC_SCHOLAR_API_KEY"):
        os.environ.setdefault("S2_API_KEY", os.environ["SEMANTIC_SCHOLAR_API_KEY"])
    try:
        raw = SECRET_FILE.read_text(encoding="ascii")
    except OSError:
        return bool(os.environ.get("S2_API_KEY"))
    for line in raw.splitlines():
        if line.startswith("SEMANTIC_SCHOLAR_API_KEY="):
            value = line.split("=", 1)[1].strip()
            os.environ["S2_API_KEY"] = value
            os.environ["SEMANTIC_SCHOLAR_API_KEY"] = value
            return True
    return bool(os.environ.get("S2_API_KEY"))


def main() -> int:
    if not load_key():
        print(json.dumps({"status": "external_blocked", "reason": "no Semantic Scholar key configured"}))
        return 2

    from litsearch.sources import SemanticScholarSource

    source = SemanticScholarSource()
    report: dict = {"provider": "semantic_scholar", "configured": True}

    started = time.perf_counter()
    try:
        papers = source.search_papers("mint drought stress transcriptome", 5, 2015, 2026)
    except Exception as exc:  # noqa: BLE001 - the probe reports, never raises
        report.update({"status": "failed", "error": type(exc).__name__,
                       "elapsed_seconds": round(time.perf_counter() - started, 3)})
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1

    report.update({
        "status": "ok",
        "returned": len(papers),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "sample": [
            {"title": (p.title or "")[:90], "year": p.year,
             "citation_count": p.citation_count, "has_abstract": bool(p.abstract),
             "has_doi": bool(p.doi or p.identifiers.doi)}
            for p in papers[:3]
        ],
    })

    if papers:
        first = papers[0]
        try:
            references = source.get_references(first.id, 3)
            report["references_probe"] = len(references)
            citations = source.get_citations(first.id, 3)
            report["citations_probe"] = len(citations)
        except Exception as exc:  # noqa: BLE001
            report["graph_probe_error"] = type(exc).__name__

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
