"""Export search results in various formats: CSV, JSON, RIS, Mermaid.

Spreadsheet safety (spec H1): a CSV is opened in Excel/LibreOffice by default,
so any *text* cell that a spreadsheet would treat as a formula (``=``, ``+``,
``-``, ``@`` and the tab/CR variants) is prefixed with an apostrophe.  The raw
characters are preserved verbatim in the JSON export, which is the
machine-readable format.

Journal metrics (spec H8): an internal citation proxy and an official,
source-verified journal metric are different quantities and are therefore
different columns with different labels.  A hardcoded number that nobody has
verified against the current release is marked as needing verification and is
never rendered as the official Impact Factor.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from datetime import date

from litsearch.models import Paper, SearchResult

#: Characters that make a spreadsheet treat a text cell as a formula.
FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")

#: Excel's own "this is literal text" prefix.
FORMULA_PREFIX = "'"

# Column headers — the two metric families never share a column.
PROXY_HEADER = "Citation Proxy (internal; not JCR Impact Factor)"
OFFICIAL_IF_HEADER = "Official Journal Impact Factor (source-verified only)"

#: What the internal proxy actually is. Kept in one place so the definition
#: travels with every number derived from it.
CITATION_PROXY_LABEL = "Citation Proxy (internal metric, not an Impact Factor)"
CITATION_PROXY_SOURCE = ("OpenAlex source record (cited_by_count, works_count, "
                         "summary_stats.h_index) retrieved by ImpactFactorLookup")
CITATION_PROXY_FORMULA = ("round((cited_by_count / max(works_count, 1)) * 0.15, 2), "
                          "blended 0.7/0.3 with h_index / 100")
OFFICIAL_METRIC_SOURCE_NOTE = ("An official metric must name its release and be checked "
                               "against the publisher's own documentation before it is cited.")
UNVERIFIED_METRIC_NOTE = ("unverified hardcoded value; needs source verification and must not be "
                          "presented as the current official Impact Factor")


def _neutralise(value: str) -> str:
    """Prefix formula-looking text so a spreadsheet keeps it as text."""
    if value and value[0] in FORMULA_TRIGGERS:
        return FORMULA_PREFIX + value
    return value


def citation_proxy_record(value: float | None, year: int | None = None,
                          source: str = CITATION_PROXY_SOURCE,
                          formula: str = CITATION_PROXY_FORMULA,
                          display: str = "", note: str = "",
                          needs_source_verification: bool = True,
                          verified: bool = False) -> dict:
    """An explicitly labelled internal proxy record.

    Field names and semantics match ``ImpactFactorLookup.citation_proxy()`` in
    ``litsearch/sources.py`` so the two modules say the same thing about the
    same number: ``value`` / ``kind`` / ``source`` / ``year`` / ``formula`` /
    ``verified`` / ``needs_source_verification`` / ``display`` / ``note``.
    ``verified`` is always False here — a proxy is never an official metric —
    and ``label`` / ``is_official`` are added for exporters.
    """
    resolved_year = year if year is not None else date.today().year
    resolved_display = display or (
        f"Citation Proxy: {value}" if value is not None else "Citation Proxy: N/A")
    return {
        "value": value,
        "kind": "citation_proxy",
        "source": source,
        "year": resolved_year,
        "formula": formula,
        "verified": False,
        "needs_source_verification": bool(needs_source_verification or verified),
        "display": resolved_display,
        "note": note or CITATION_PROXY_LABEL,
        "label": CITATION_PROXY_LABEL,
        "is_official": False,
    }


def official_metric_record(value: float | None, source: str = "", year: int | None = None,
                           verified: bool = False, note: str = "",
                           display: str = "") -> dict:
    """An official journal metric record.

    Without a named source, a year *and* ``verified=True`` the record is marked
    unverified; consumers must show it as needing verification instead of as the
    official Impact Factor.
    """
    is_verified = bool(verified and source and year)
    resolved_display = display or (
        f"Official Impact Factor: {value} ({source} {year})".strip() if is_verified
        else f"needs source verification: {value}")
    return {
        "value": value,
        "kind": "official_metric",
        "source": source,
        "year": year,
        "formula": None,
        "verified": is_verified,
        "needs_source_verification": not is_verified,
        "display": resolved_display,
        "note": note or (OFFICIAL_METRIC_SOURCE_NOTE if is_verified else UNVERIFIED_METRIC_NOTE),
        "label": ("Official Journal Impact Factor (JCR)" if is_verified
                  else "Official metric (unverified)"),
        "is_official": True,
    }


def normalize_metric_record(record) -> dict | None:
    """Accept this module's records, a ``sources.citation_proxy()`` dict, or a
    bare number, and return one canonical record.

    A bare number is treated as a proxy value: an unlabelled number must never
    become an "official Impact Factor" by accident.
    """
    if record is None:
        return None
    if isinstance(record, bool) or not isinstance(record, dict):
        if isinstance(record, (int, float)) and not isinstance(record, bool):
            return citation_proxy_record(float(record))
        return None
    official = bool(record.get("is_official")) or record.get("kind") == "official_metric"
    merged = dict(record)
    if official:
        base = official_metric_record(
            merged.get("value"), source=str(merged.get("source") or ""),
            year=merged.get("year"), verified=bool(merged.get("verified")))
    else:
        base = citation_proxy_record(
            merged.get("value"), year=merged.get("year"),
            source=str(merged.get("source") or CITATION_PROXY_SOURCE),
            formula=str(merged.get("formula") or CITATION_PROXY_FORMULA),
            display=str(merged.get("display") or ""), note=str(merged.get("note") or ""),
            needs_source_verification=bool(merged.get("needs_source_verification", True)))
    # The caller's own wording wins; the canonical field set does not change.
    for key in ("display", "note", "label", "source", "year", "formula", "value"):
        if merged.get(key) not in (None, ""):
            base[key] = merged[key]
    base["verified"] = False if not official else base["verified"]
    if not official:
        base["needs_source_verification"] = True
    return base


def _proxy_cell(record) -> str:
    record = normalize_metric_record(record)
    if not record or record.get("value") is None:
        return ""
    return (f"{record['display']} [source: {record.get('source', '')}; "
            f"year: {record.get('year', '')}; formula: {record.get('formula', '')}; "
            f"{record.get('note', '')}]")


def _official_cell(record) -> str:
    record = normalize_metric_record(record)
    if not record or record.get("value") is None:
        return ""
    if record.get("verified"):
        return f"{record['value']} ({record.get('source', '')} {record.get('year', '')})".strip()
    # The warning comes first: a spreadsheet cell that starts with the number
    # reads as a verified value to anyone skimming the column.
    return f"NEEDS SOURCE VERIFICATION: {record['value']} " \
           f"({record.get('note') or UNVERIFIED_METRIC_NOTE})"


def to_csv(papers: list[Paper], include_abstract: bool = False,
           journal_metrics: dict[str, dict] | None = None) -> str:
    """Export papers as CSV string.

    Args:
        papers: papers to export.
        include_abstract: add the abstract column.
        journal_metrics: optional ``{venue: {"official": record, "proxy": record}}``.
            When given, the official metric and the internal citation proxy are
            written to two separate columns.
    """
    output = io.StringIO()
    writer = csv.writer(output)
    header = ["Title", "Authors", "Year", "Venue", "DOI", "Citations", "Relevance"]
    if include_abstract:
        header.append("Abstract")
    if journal_metrics is not None:
        header += [OFFICIAL_IF_HEADER, PROXY_HEADER]
    writer.writerow(header)
    for p in papers:
        authors = "; ".join(a.name for a in p.authors)
        row = [
            _neutralise(p.title or ""), _neutralise(authors), p.year or "",
            _neutralise(p.venue or ""), _neutralise(p.doi or ""),
            p.citation_count, round(p.relevance_score, 4),
        ]
        if include_abstract:
            row.append(_neutralise(p.abstract or ""))
        if journal_metrics is not None:
            entry = journal_metrics.get(p.venue or "") or {}
            row += [_official_cell(entry.get("official")), _proxy_cell(entry.get("proxy"))]
        writer.writerow(row)
    return output.getvalue()


def to_json(result: SearchResult, journal_metrics: dict[str, dict] | None = None) -> str:
    """Export full SearchResult as formatted JSON (raw text, no CSV escaping)."""
    papers_data = []
    for p in result.papers:
        row = {
            "id": p.id,
            "title": p.title,
            "abstract": p.abstract,
            "authors": [{"name": a.name, "id": a.author_id} for a in p.authors],
            "year": p.year,
            "venue": p.venue,
            "doi": p.doi,
            "citation_count": p.citation_count,
            "url": p.url,
            "relevance_score": round(p.relevance_score, 4),
            "topics": p.topics,
        }
        if journal_metrics is not None:
            entry = journal_metrics.get(p.venue or "") or {}
            row["journal_metrics"] = {
                "official": entry.get("official"),
                "proxy": entry.get("proxy"),
            }
        papers_data.append(row)
    out = {
        "total_results": result.total_available,
        "elapsed_seconds": result.elapsed_seconds,
        "papers": papers_data,
        "trend_metrics": result.trend_metrics,
    }
    if result.network:
        out["network"] = {
            "seed_paper_id": result.network.seed_paper_id,
            "node_count": result.network.node_count,
            "hop_depth": result.network.hop_depth,
            "central_papers": result.network.central_papers[:10],
            "graph": result.network.graph_data,
        }
    return json.dumps(out, ensure_ascii=False, indent=2)


def to_mermaid_markdown(result: SearchResult, max_nodes: int = 80) -> str:
    """Generate Mermaid markdown graph from a search result."""
    if result.network is None or result.network.graph_data is None:
        return "```mermaid\ngraph LR\n    A[No network data]\n```"

    g = result.network.graph_data
    nodes = g.get("nodes", [])[:max_nodes]
    links = g.get("links", [])
    node_map = {n["id"]: i for i, n in enumerate(nodes)}

    lines = ["```mermaid", "graph LR"]
    for i, node in enumerate(nodes):
        title = (node.get("title", node["id"][:8]) or "")[:60].replace('"', "'")
        year = node.get("year", "")
        label = f"{year}: {title}" if year else title
        lines.append(f'    n{i}["{label}"]')

    edge_count = 0
    for link in links:
        u, v = link.get("source"), link.get("target")
        if u in node_map and v in node_map and edge_count < 150:
            lines.append(f"    n{node_map[u]} --> n{node_map[v]}")
            edge_count += 1

    lines.append("```")
    return "\n".join(lines)


def to_ris(papers: list[Paper]) -> str:
    """Export papers as RIS format for reference managers (EndNote, Zotero)."""
    entries = []
    for p in papers:
        parts = []
        parts.append("TY  - JOUR")
        parts.append(f"TI  - {p.title}")
        if p.authors:
            for a in p.authors:
                parts.append(f"AU  - {a.name}")
        if p.year:
            parts.append(f"PY  - {p.year}")
        if p.venue:
            parts.append(f"JO  - {p.venue}")
        if p.doi:
            parts.append(f"DO  - {p.doi}")
        if p.url:
            parts.append(f"UR  - {p.url}")
        if p.abstract:
            # RIS has N2 for abstract
            parts.append(f"N2  - {' '.join((p.abstract or '').splitlines())}")
        parts.append("ER  - ")
        entries.append("\n".join(parts))
    return "\n\n".join(entries)


def to_bibtex(papers: list[Paper]) -> str:
    def escape(text):
        return str(text).replace("\\", r"\textbackslash{}").replace("{", r"\{").replace("}", r"\}").replace("%", r"\%").replace("&", r"\&").replace("_", r"\_").replace("#", r"\#")
    entries = []
    for p in papers:
        key = "paper_" + hashlib.sha256(p.canonical_id.encode()).hexdigest()[:12]
        fields = {"title": p.title, "author": " and ".join(a.name for a in p.authors),
                  "year": p.year, "journal": p.venue, "doi": p.doi, "url": p.url, "abstract": p.abstract}
        lines = [f"@article{{{key},"]
        lines += [f"  {name} = {{{escape(value)}}}," for name, value in fields.items() if value]
        entries.append("\n".join([*lines, "}"]))
    return "\n\n".join(entries)
