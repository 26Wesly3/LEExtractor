"""Export search results in various formats: CSV, JSON, RIS, Mermaid."""

import csv
import io
import json

from litsearch.models import Paper, SearchResult


def to_csv(papers: list[Paper], include_abstract: bool = False) -> str:
    """Export papers as CSV string."""
    output = io.StringIO()
    writer = csv.writer(output)
    header = ["Title", "Authors", "Year", "Venue", "DOI", "Citations", "Relevance"]
    if include_abstract:
        header.append("Abstract")
    writer.writerow(header)
    for p in papers:
        authors = "; ".join(a.name for a in p.authors[:5])
        if len(p.authors) > 5:
            authors += " et al."
        row = [
            p.title, authors, p.year or "", p.venue or "",
            p.doi or "", p.citation_count, round(p.relevance_score, 4),
        ]
        if include_abstract:
            row.append((p.abstract or "")[:300])
        writer.writerow(row)
    return output.getvalue()


def to_json(result: SearchResult) -> str:
    """Export full SearchResult as formatted JSON."""
    papers_data = []
    for p in result.papers:
        papers_data.append({
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
        })
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
            for a in p.authors[:10]:
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
            parts.append(f"N2  - {(p.abstract or '')[:500]}")
        parts.append("ER  - ")
        entries.append("\n".join(parts))
    return "\n\n".join(entries)
