"""Provider identities stay separate from the corpus identity."""

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import unquote


def normalize_doi(value: str | None) -> str:
    value = unquote((value or "").strip())
    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value, flags=re.I)
    return value.lower() if value.lower().startswith("10.") and "/" in value else ""


def normalize_openalex(value: str | None) -> str:
    value = re.sub(r"^openalex:", "", (value or "").strip(), flags=re.I).rstrip("/").split("/")[-1]
    return value.upper() if re.fullmatch(r"W\d+", value, re.I) else ""


def normalize_arxiv(value: str | None) -> str:
    value = re.sub(r"^arxiv:", "", (value or "").strip(), flags=re.I)
    value = re.sub(r"^https?://(?:export\.)?arxiv\.org/(?:abs|pdf)/", "", value)
    value = re.sub(r"(?:\.pdf)?$", "", value)
    value = re.sub(r"v\d+$", "", value)
    return value if re.fullmatch(r"(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})", value) else ""


@dataclass
class PaperIdentifiers:
    doi: str = ""
    semantic_scholar_id: str = ""
    openalex_id: str = ""
    arxiv_id: str = ""
    pmid: str = ""


def identifier_key(value: str, provider: str = "") -> str:
    if doi := normalize_doi(value):
        return doi
    if oa := normalize_openalex(value):
        return f"openalex:{oa}"
    if ax := normalize_arxiv(value):
        return f"arxiv:{ax}"
    if value.lower().startswith("s2:"):
        return value.lower()
    if provider == "semantic_scholar" or re.fullmatch(r"[a-fA-F0-9]{40}", value):
        return f"s2:{value.lower()}"
    return value.strip().lower()


def paper_aliases(paper) -> set[str]:
    ids = paper.identifiers
    aliases = {identifier_key(paper.id, paper.source), paper.canonical_id}
    for value, provider in (
        (paper.doi or ids.doi, ""), (ids.semantic_scholar_id, "semantic_scholar"),
        (ids.openalex_id, "openalex"), (ids.arxiv_id, "arxiv"),
    ):
        if value:
            aliases.add(identifier_key(value, provider))
    return aliases - {""}


def fallback_key(title: str, year: int | None) -> str:
    text = re.sub(r"\W+", "", title.casefold())
    return "title:" + hashlib.sha256(f"{text}|{year}".encode()).hexdigest()[:24]
