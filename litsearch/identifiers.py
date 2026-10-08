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
    """Canonical identity key for an identifier string.

    Returns ``""`` for anything unusable rather than raising. Provider payloads
    legitimately contain ``null`` reference entries and blank strings, and a
    crash deep inside a graph build is a much worse outcome than a skipped
    reference — the caller can only treat "no identifier" as "no evidence",
    which is exactly right for a missing reference.
    """
    if not isinstance(value, str) or not value.strip():
        return ""
    if doi := normalize_doi(value):
        return doi
    if oa := normalize_openalex(value):
        return f"openalex:{oa}"
    if ax := normalize_arxiv(value):
        return f"arxiv:{ax}"
    # S2 sometimes hands out "S2CorpusId:99" / "CorpusId:99"; it is the same
    # identity as the bare S2 paper id and must bridge with it.
    stripped = value.strip()
    corpus = re.sub(r"^(?:s2:)?(?:s2)?corpusid:\s*", "", stripped, flags=re.I)
    if corpus and corpus != stripped:
        return f"s2:{corpus.lower()}"
    if value.lower().startswith("s2:"):
        return value.lower()
    if provider == "semantic_scholar" or re.fullmatch(r"[a-fA-F0-9]{40}", value):
        return f"s2:{value.lower()}"
    return stripped.lower()


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


def reference_witness(ref, provider: str = "", alias_index: dict[str, str] | None = None) -> str:
    """One canonical witness key for a reference, or ``""`` if it is unusable.

    This is the **single** rule for "is this reference real evidence?", shared by
    the evidence graph and by coupling/co-citation discovery. Two modules
    deriving reference identity differently is not a style question: it means
    one of them counts a blank entry as a shared reference while the other does
    not, and the two disagree about which papers are related.

    ``None``, ``""``, whitespace and non-strings return ``""``. **Callers must
    drop empty keys rather than storing them** — a set that keeps ``""`` makes
    every paper with a missing reference look like it shares one with every
    other such paper.

    Args:
        ref: The raw reference value from provider metadata.
        provider: The paper's source, used to disambiguate bare ids.
        alias_index: Optional ``alias -> canonical id`` map. When a reference
            resolves through it, the *canonical* id is returned so that a URL
            DOI, a bare DOI and a cross-source alias of one work collapse into
            one witness.
    """
    if not isinstance(ref, str) or not ref.strip():
        return ""
    for key in (identifier_key(ref, provider), identifier_key(ref)):
        if not key:
            continue
        if alias_index and key in alias_index:
            return alias_index[key]
    return identifier_key(ref) or ref.strip().lower()


def paper_reference_witnesses(paper, alias_index: dict[str, str] | None = None) -> set[str]:
    """Every usable reference witness of one paper, in canonical form.

    Empty keys are filtered here so no caller has to remember to do it.
    """
    keys = {
        reference_witness(ref, paper.source, alias_index)
        for ref in (paper.reference_ids or [])
    }
    return keys - {""}
