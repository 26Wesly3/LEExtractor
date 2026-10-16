"""M1: per-file PDF import, text/metadata parsing and identity matching.

Specification: ``docs/v3.2/16_PDF_ZOTERO_INTEGRATIONS.md`` ("PDF 解析与匹配",
"每文件记录", "批次边界"), ``contracts/defaults.json`` ``pdf`` section and
``contracts/domain.schema.json`` ``$defs/ImportItem`` / ``$defs/MatchCandidate``.

Design rules this module exists to enforce:

* **The third-party PDF library is wrapped here and nowhere else.**  PyMuPDF
  (``import fitz``) is imported lazily inside :func:`_read_pdf`; no other module
  in the package may import it, and every failure it raises is converted into a
  contract status plus an error code rather than escaping as a library traceback.
* **AC-27 — a DOI in the references section is not this paper's DOI.**  The
  candidate zone is bounded by the "References" heading; anything at or after it
  is collected into ``reference_dois`` and can never become ``parsed.doi``.
* **No hard-coded contract value.**  Every limit, weight and threshold is read
  from :mod:`litsearch.v32.contracts` at call time.
* **Batch limits reject the whole request before enqueue.**  :func:`plan_batch`
  is a pure pre-flight check; it never enqueues a partial batch.
* **Identity is reused, not re-derived.**  DOI normalisation goes through
  :func:`litsearch.identifiers.normalize_doi`; this module never grows a second
  identity resolver.
* **No network.**  Nothing here opens a socket; the parser is untrusted input and
  embedded scripts/actions/URIs are never executed.

Physical paths are deliberately absent from every returned object: an
:class:`ImportItem` record can only carry an opaque ``attachment_id`` and an
opaque storage key derived from the content identity, so the DTO cannot expose
where a file lives on disk.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from litsearch.identifiers import normalize_doi as _normalize_doi_value

from . import contracts as _contracts
from .errors import BAD_REQUEST, V32Error
from .types import IMPORT_RETRYABLE, ImportStatus, round_tie, safe_float

__all__ = [
    "ParsedPaper",
    "ParseOutcome",
    "MatchCandidate",
    "MatchOutcome",
    "CandidatePaper",
    "Attachment",
    "AttachmentRegistry",
    "PdfImportError",
    "PdfTooLargeError",
    "TooManyFilesError",
    "BatchTooLargeError",
    "TooManyPagesError",
    "PdfParseTimeoutError",
    "InvalidFileNameError",
    "ERROR_EMPTY_FILE",
    "ERROR_NOT_A_PDF",
    "ERROR_MALFORMED_PDF",
    "ERROR_ENCRYPTED_PDF",
    "ERROR_NEEDS_OCR",
    "ERROR_PARSE_TIMEOUT",
    "ERROR_RESOURCE_LIMIT",
    "ERROR_PAGE_LIMIT",
    "ERROR_INSUFFICIENT_METADATA",
    "ERROR_IDENTIFIER_CONFLICT",
    "normalize_title",
    "title_similarity",
    "author_overlap",
    "year_agreement",
    "match_score",
    "extract_dois",
    "sanitize_display_name",
    "content_hash",
    "stored_path_key",
    "pdf_limits",
    "plan_batch",
    "parse_pdf",
    "parse_pdf_bytes",
    "match_parsed",
    "build_import_item",
    "describe_outcome",
]


# --------------------------------------------------------------------------
# limits and error codes
# --------------------------------------------------------------------------

#: Import-level error codes (``$defs/ImportItem.error_code`` is a bounded
#: string, so these are the vocabulary the API layer switches on).
ERROR_EMPTY_FILE = "empty_file"
ERROR_NOT_A_PDF = "not_a_pdf"
ERROR_MALFORMED_PDF = "malformed_pdf"
ERROR_ENCRYPTED_PDF = "encrypted_pdf"
ERROR_NEEDS_OCR = "needs_ocr"
ERROR_PARSE_TIMEOUT = "parser_timeout"
ERROR_RESOURCE_LIMIT = "parser_resource_limit"
ERROR_PAGE_LIMIT = "page_limit_exceeded"
ERROR_INSUFFICIENT_METADATA = "insufficient_metadata"
ERROR_IDENTIFIER_CONFLICT = "identifier_conflict"
ERROR_AMBIGUOUS_MATCH = "ambiguous_match"

#: Fallback values, used only if a contract file is unreadable.  They mirror
#: ``contracts/defaults.json`` so the module degrades instead of crashing.
_DEFAULTS: dict[str, Any] = {
    "batch_max_files": 10,
    "file_max_mib": 20,
    "batch_max_mib": 100,
    "max_pages": 200,
    "parser_timeout_seconds": 15,
    "parser_memory_mib": 256,
    "minimum_seed_chars": 100,
    "match_weights": {"title": 0.75, "authors": 0.15, "year": 0.1},
    "auto_match_score": 0.9,
    "auto_title_similarity": 0.9,
    "candidate_score_min": 0.7,
    "minimum_match_margin": 0.1,
    "doi_title_similarity": 0.8,
    "ocr_enabled": False,
}


def pdf_limits(overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The PDF contract section, with optional per-call overrides.

    The contract is the source of truth (``contracts/defaults.json`` ``pdf``);
    ``overrides`` exist so a caller can tighten a budget for one call, never so
    a caller can substitute a different one.  An override may only touch a key
    the contract already declares.
    """
    limits: dict[str, Any] = dict(_DEFAULTS)
    try:
        section = _contracts.pdf_defaults()
    except Exception:  # pragma: no cover - a missing contract must not crash import
        section = {}
    for key, value in section.items():
        limits[key] = value
    for key in _DEFAULTS:
        limits.setdefault(key, _DEFAULTS[key])
    if overrides:
        for key, value in overrides.items():
            if key in _DEFAULTS:
                limits[key] = value
    limits["match_weights"] = dict(limits.get("match_weights") or _DEFAULTS["match_weights"])
    return limits


class PdfImportError(V32Error):
    """Base class for every rejection this module raises."""

    code = "pdf_import_error"

    def __init__(
        self,
        message: str,
        *,
        status: int = 400,
        code: str | None = None,
        retryable: bool | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(
            status=status,
            code=code or self.code,
            message=message,
            retryable=retryable,
            details=dict(details or {}),
        )


class PdfTooLargeError(PdfImportError):
    code = "file_too_large"

    def __init__(self, message: str = "单个文件超过大小上限", **kwargs: Any) -> None:
        super().__init__(message, status=413, **kwargs)


class TooManyFilesError(PdfImportError):
    code = "too_many_files"

    def __init__(self, message: str = "文件数量超过批次上限", **kwargs: Any) -> None:
        super().__init__(message, status=400, **kwargs)


class BatchTooLargeError(PdfImportError):
    code = "batch_too_large"

    def __init__(self, message: str = "批次总大小超过上限", **kwargs: Any) -> None:
        super().__init__(message, status=413, **kwargs)


class TooManyPagesError(PdfImportError):
    code = "too_many_pages"

    def __init__(
        self, message: str = "页数超过上限", *, status: int = 422, **kwargs: Any
    ) -> None:
        super().__init__(message, status=status, **kwargs)


class PdfParseTimeoutError(PdfImportError):
    code = "parser_timeout"

    def __init__(self, message: str = "解析超时", **kwargs: Any) -> None:
        super().__init__(message, status=504, retryable=True, **kwargs)


class InvalidFileNameError(PdfImportError):
    code = BAD_REQUEST

    def __init__(self, message: str = "文件条目不合法", **kwargs: Any) -> None:
        super().__init__(message, status=400, retryable=False, **kwargs)


# --------------------------------------------------------------------------
# domain objects
# --------------------------------------------------------------------------


@dataclass
class ParsedPaper:
    """What a single PDF yielded, with page/section evidence per field."""

    title: str | None = None
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    abstract: str | None = None
    doi: str | None = None
    doi_candidates: list[str] = field(default_factory=list)
    reference_dois: list[str] = field(default_factory=list)
    text_chars: int = 0
    page_count: int = 0
    evidence: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe view; no NaN, no path, no library object."""
        return {
            "title": self.title,
            "authors": list(self.authors),
            "year": self.year,
            "abstract": self.abstract,
            "doi": self.doi,
            "doi_candidates": list(self.doi_candidates),
            "reference_dois": list(self.reference_dois),
            "text_chars": int(self.text_chars),
            "page_count": int(self.page_count),
            "evidence": {k: dict(v) for k, v in self.evidence.items()},
        }


@dataclass
class ParseOutcome:
    status: ImportStatus
    parsed: ParsedPaper | None
    error_code: str | None
    retryable: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": str(self.status),
            "parsed": self.parsed.to_dict() if self.parsed else None,
            "error_code": self.error_code,
            "retryable": bool(self.retryable),
        }


@dataclass
class MatchCandidate:
    candidate_id: str
    title: str
    doi: str | None
    year: int | None
    match_score: float
    source: str
    conflicts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "title": self.title,
            "doi": self.doi,
            "year": self.year,
            "match_score": self.match_score,
            "source": self.source,
            "conflicts": list(self.conflicts),
        }


@dataclass
class MatchOutcome:
    status: ImportStatus
    paper_id: str | None
    candidates: list[MatchCandidate]
    match_score: float | None
    conflicts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": str(self.status),
            "paper_id": self.paper_id,
            "candidates": [c.to_dict() for c in self.candidates],
            "match_score": self.match_score,
            "conflicts": list(self.conflicts),
        }


@dataclass
class CandidatePaper:
    """The explicit candidate shape accepted by :func:`match_parsed`.

    Any object *or* mapping with ``candidate_id``/``id``, ``title``, ``doi``,
    ``year``, ``authors`` and ``source`` is also accepted, so a provider payload
    can be matched without being copied into this dataclass first.
    """

    candidate_id: str
    title: str = ""
    doi: str | None = None
    year: int | None = None
    authors: list[str] = field(default_factory=list)
    source: str = "local"
    identifiers: dict[str, str] = field(default_factory=dict)


@dataclass
class Attachment:
    """The content-addressed attachment identity (AC-04)."""

    attachment_id: str
    content_hash: str
    display_name: str
    stored_path_key: str
    size_bytes: int
    reused: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "attachment_id": self.attachment_id,
            "content_hash": self.content_hash,
            "display_name": self.display_name,
            "stored_path_key": self.stored_path_key,
            "size_bytes": int(self.size_bytes),
            "reused": bool(self.reused),
        }


# --------------------------------------------------------------------------
# text normalisation and similarity
# --------------------------------------------------------------------------

_CJK_RANGES = (
    (0x3040, 0x30FF),  # Hiragana / Katakana
    (0x3400, 0x4DBF),  # CJK extension A
    (0x4E00, 0x9FFF),  # CJK unified ideographs
    (0xF900, 0xFAFF),  # CJK compatibility ideographs
    (0xAC00, 0xD7AF),  # Hangul syllables
)


def _is_cjk(ch: str) -> bool:
    code = ord(ch)
    return any(low <= code <= high for low, high in _CJK_RANGES)


def normalize_title(text: str) -> str:
    """NFKC, casefold, whitespace- and punctuation-normalised title.

    Latin text keeps word boundaries; every run of punctuation, symbols and
    whitespace collapses to a single space.  CJK characters survive because the
    "alphanumeric" test accepts them, so a Chinese title keeps its characters.
    """
    if not text:
        return ""
    folded = unicodedata.normalize("NFKC", str(text)).casefold()
    out: list[str] = []
    pending_space = False
    for ch in folded:
        if ch.isalnum():
            if pending_space and out:
                out.append(" ")
            pending_space = False
            out.append(ch)
        else:
            pending_space = True
    return "".join(out).strip()


def _title_tokens(text: str) -> list[str]:
    """Deterministic token stream: latin by word, CJK by character bigram.

    A CJK run of length >= 2 yields overlapping character bigrams, which is what
    makes a one-character-shuffled Chinese title score below 1.0 while an exact
    match scores exactly 1.0.
    """
    normalised = normalize_title(text)
    if not normalised:
        return []
    tokens: list[str] = []
    index = 0
    length = len(normalised)
    while index < length:
        ch = normalised[index]
        if _is_cjk(ch):
            start = index
            while index < length and _is_cjk(normalised[index]):
                index += 1
            run = normalised[start:index]
            if len(run) == 1:
                tokens.append(run)
            else:
                tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
        elif ch.isalnum():
            start = index
            while (
                index < length
                and normalised[index].isalnum()
                and not _is_cjk(normalised[index])
            ):
                index += 1
            tokens.append(normalised[start:index])
        else:  # pragma: no cover - normalize_title already removed these
            index += 1
    return tokens


def title_similarity(a: str, b: str) -> float:
    """Deterministic token Jaccard; latin by word, Chinese by character bigram."""
    left = set(_title_tokens(a or ""))
    right = set(_title_tokens(b or ""))
    if not left or not right:
        return 0.0
    union = left | right
    if not union:  # pragma: no cover - guarded by the emptiness check above
        return 0.0
    return len(left & right) / len(union)


def _author_tokens(name: str) -> set[str]:
    """Normalised tokens of one author name (surname/given-name letters)."""
    if not name:
        return set()
    folded = unicodedata.normalize("NFKC", str(name)).casefold()
    tokens: set[str] = set()
    for run in re.findall(r"[^\W\d_]+", folded, flags=re.UNICODE):
        if _is_cjk(run[0]):
            if len(run) >= 2:
                tokens.add(run)
        elif len(run) >= 3:
            tokens.add(run)
    if not tokens:
        compact = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", folded)
        if len(compact) >= 2:
            tokens.add(compact)
    return tokens


def author_overlap(a: Sequence[str], b: Sequence[str]) -> float:
    """Jaccard over normalised author tokens; a missing side scores 0."""
    left: set[str] = set()
    right: set[str] = set()
    for name in a or ():
        left |= _author_tokens(str(name))
    for name in b or ():
        right |= _author_tokens(str(name))
    if not left or not right:
        return 0.0
    union = left | right
    if not union:  # pragma: no cover - guarded above
        return 0.0
    return len(left & right) / len(union)


def year_agreement(a: int | None, b: int | None) -> float:
    """Same year -> 1.0, one year apart -> 0.5, otherwise 0.0."""
    if a is None or b is None:
        return 0.0
    try:
        left, right = int(a), int(b)
    except (TypeError, ValueError):
        return 0.0
    if left == right:
        return 1.0
    if abs(left - right) == 1:
        return 0.5
    return 0.0


# --------------------------------------------------------------------------
# candidate access helpers (dict or object, both accepted)
# --------------------------------------------------------------------------


def _pick(candidate: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(candidate, Mapping):
            if name in candidate and candidate[name] is not None:
                return candidate[name]
        else:
            value = getattr(candidate, name, None)
            if value is not None:
                return value
    return default


def _candidate_id(candidate: Any) -> str:
    return str(_pick(candidate, "candidate_id", "id", "paper_id", default="") or "")


def _candidate_title(candidate: Any) -> str:
    return str(_pick(candidate, "title", default="") or "")


def _candidate_doi(candidate: Any) -> str | None:
    raw = _pick(candidate, "doi", default=None)
    if raw is None:
        identifiers = _pick(candidate, "identifiers", default=None)
        if isinstance(identifiers, Mapping):
            raw = identifiers.get("doi")
    if raw is None:
        return None
    return _normalize_doi_value(str(raw)) or None


def _candidate_year(candidate: Any) -> int | None:
    raw = _pick(candidate, "year", "publication_year")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _candidate_authors(candidate: Any) -> list[str]:
    raw = _pick(candidate, "authors", "author_names", default=None)
    if raw is None or isinstance(raw, (str, bytes)):
        if isinstance(raw, str):
            return [part.strip() for part in re.split(r";|\band\b", raw) if part.strip()]
        return []
    if isinstance(raw, Iterable):
        out: list[str] = []
        for entry in raw:
            if isinstance(entry, str) and entry.strip():
                out.append(entry)
            elif isinstance(entry, Mapping) and entry.get("name"):
                out.append(str(entry["name"]))
        return out
    return []


def _candidate_source(candidate: Any) -> str:
    return str(_pick(candidate, "source", "provider", default="local") or "local")


def _candidate_conflicts(parsed: ParsedPaper, candidate: Any) -> list[str]:
    """Strong-identifier and title conflicts, in a stable order."""
    conflicts: list[str] = []
    parsed_doi = _normalize_doi_value(parsed.doi) if parsed.doi else ""
    candidate_doi = _candidate_doi(candidate) or ""
    if parsed_doi and candidate_doi and parsed_doi != candidate_doi:
        conflicts.append(ERROR_IDENTIFIER_CONFLICT)
    title_a = parsed.title or ""
    title_b = _candidate_title(candidate)
    if title_a and title_b and title_similarity(title_a, title_b) < 0.5:
        conflicts.append("title")
    return conflicts


def match_score(parsed: ParsedPaper, candidate: Any) -> float:
    """``0.75*title + 0.15*authors + 0.10*year``, weights never re-normalised.

    A missing sub-term contributes 0 and the weights stay exactly as the
    contract declares them, so a candidate that only agrees on the title cannot
    reach the auto-match threshold by dropping the other two terms.  The score is
    an engineering similarity, never a probability.
    """
    weights = pdf_limits().get("match_weights") or _DEFAULTS["match_weights"]
    title_term = title_similarity(parsed.title or "", _candidate_title(candidate))
    author_term = author_overlap(parsed.authors or [], _candidate_authors(candidate))
    year_term = year_agreement(parsed.year, _candidate_year(candidate))
    total = (
        float(weights.get("title", 0.0)) * title_term
        + float(weights.get("authors", 0.0)) * author_term
        + float(weights.get("year", 0.0)) * year_term
    )
    finite = safe_float(total)
    if finite is None:
        return 0.0
    return round_tie(min(max(finite, 0.0), 1.0)) or 0.0


# --------------------------------------------------------------------------
# identifiers / display names
# --------------------------------------------------------------------------

_DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>()\[\]{}]+", re.I)
_DOI_TRAILING = ".,;:!?)]}>"
_CJK_TRAILING = "\u3002\uff0c\uff1b\uff1a\uff09\u300b\u3011\u3001"


def _clean_doi(raw: str) -> str:
    """Strip prefixes/whitespace/trailing punctuation, then apply the shared rule."""
    value = (raw or "").strip().strip("\u200b\ufeff")
    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi\s*:\s*)", "", value, flags=re.I)
    value = re.sub(r"\s+", "", value)
    while value and (value[-1] in _DOI_TRAILING or value[-1] in _CJK_TRAILING):
        value = value[:-1]
    return _normalize_doi_value(value)


def extract_dois(text: str) -> list[str]:
    """Every DOI-shaped string in ``text``, normalised, in first-seen order."""
    found: list[str] = []
    for match in _DOI_RE.finditer(text or ""):
        cleaned = _clean_doi(match.group(0))
        if cleaned and cleaned not in found:
            found.append(cleaned)
    return found


def sanitize_display_name(name: str, *, fallback: str = "document.pdf") -> str:
    """A display-only name: no directories, no control characters, bounded.

    The result is ever only a label.  Storage keys are derived from the content
    identity, so a hostile name cannot influence where anything is written and
    the adapter never raises because of one.
    """
    raw = unicodedata.normalize("NFKC", str(name or ""))
    base = raw.replace("\\", "/").rsplit("/", 1)[-1]
    base = "".join(ch for ch in base if ch.isprintable()).strip()
    base = base.strip(". ")
    if not base or base in {".", ".."}:
        return fallback
    if len(base) > 128:
        stem, dot, suffix = base.rpartition(".")
        if dot and 0 < len(suffix) <= 10:
            base = stem[: 128 - len(suffix) - 1] + "." + suffix
        else:
            base = base[:128].strip(". ")
    if not base or base in {".", ".."}:
        return fallback
    return base


def content_hash(data: bytes | bytearray | memoryview) -> str:
    """SHA-256 of the file bytes — the content identity used by AC-04."""
    return hashlib.sha256(bytes(data)).hexdigest()


def stored_path_key(content_digest: str) -> str:
    """The only path-shaped value that may leave this module.

    It is content-addressed, so identical bytes always map to the same opaque
    key and no caller can learn a real filesystem path from it.
    """
    digest = str(content_digest or "")
    return f"blobs/{digest[:2]}/{digest}"


# --------------------------------------------------------------------------
# attachment registry (content de-duplication, AC-04)
# --------------------------------------------------------------------------


class AttachmentRegistry:
    """Project-scoped content-addressed attachments.

    Content de-duplication is deliberately separate from command idempotency:
    the same bytes uploaded twice in one project reuse the same
    ``attachment_id`` (this class); repeating the same *command* reuses the same
    response (the caller's concern).
    """

    def __init__(self) -> None:
        self._by_hash: dict[tuple[str, str], Attachment] = {}

    def register(self, project_id: str, data: bytes, display_name: str) -> Attachment:
        """Register bytes; a second identical upload reuses the identity."""
        digest = content_hash(data)
        key = (str(project_id), digest)
        existing = self._by_hash.get(key)
        if existing is not None:
            record = Attachment(**existing.to_dict())
            record.reused = True
            return record
        record = Attachment(
            attachment_id="att_" + digest[:32],
            content_hash=digest,
            display_name=sanitize_display_name(display_name),
            stored_path_key=stored_path_key(digest),
            size_bytes=len(data),
            reused=False,
        )
        self._by_hash[key] = record
        return Attachment(**record.to_dict())

    def lookup(self, project_id: str, digest: str) -> Attachment | None:
        record = self._by_hash.get((str(project_id), str(digest)))
        return Attachment(**record.to_dict()) if record is not None else None

    def __len__(self) -> int:
        return len(self._by_hash)


# --------------------------------------------------------------------------
# batch pre-flight check
# --------------------------------------------------------------------------


def plan_batch(files: Sequence[tuple[str, int]]) -> None:
    """Reject the whole request *before* enqueue when a batch limit is exceeded.

    ``files`` is ``[(display_name, size_bytes), ...]``.  A breach raises a
    :class:`V32Error` — 413 for size, 400 for count — and enqueues nothing, so a
    half-written batch can never be left behind.  Returns ``None`` when the
    request is admissible.
    """
    limits = pdf_limits()
    entries = list(files or [])
    file_max_bytes = int(limits["file_max_mib"]) * 1024 * 1024
    batch_max_bytes = int(limits["batch_max_mib"]) * 1024 * 1024
    batch_max_files = int(limits["batch_max_files"])

    # Count first: it is the cheapest check and the one a caller can fix without
    # re-uploading anything.
    if len(entries) > batch_max_files:
        raise TooManyFilesError(
            f"一次最多导入 {batch_max_files} 个文件，本次 {len(entries)} 个",
            details={"capability": "pdf_batch_files"},
        )

    sizes: list[tuple[str, int]] = []
    for entry in entries:
        try:
            name, size = str(entry[0]), int(entry[1])
        except (TypeError, ValueError, IndexError, KeyError):
            raise InvalidFileNameError("文件条目缺少名称或大小") from None
        if size < 0:
            raise InvalidFileNameError("文件大小不能为负数")
        sizes.append((name, size))

    for _name, size in sizes:
        if size > file_max_bytes:
            raise PdfTooLargeError(
                f"单个文件超过 {limits['file_max_mib']} MiB 上限",
                details={"capability": "pdf_file_size"},
            )

    total = sum(size for _name, size in sizes)
    if total > batch_max_bytes:
        raise BatchTooLargeError(
            f"批次总大小超过 {limits['batch_max_mib']} MiB 上限",
            details={"capability": "pdf_batch_size"},
        )
    return None


# --------------------------------------------------------------------------
# PDF reading (the only place the third-party library is touched)
# --------------------------------------------------------------------------

_SECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "references",
        re.compile(
            r"(?:references|reference list|bibliography|works cited|literature cited|"
            r"参考文献|引用文献)",
            re.I,
        ),
    ),
    (
        "abstract",
        re.compile(
            r"(?:abstract|a\s*b\s*s\s*t\s*r\s*a\s*c\s*t|摘要|内容摘要)[\s:.\-\u2014]*(.*)$",
            re.I,
        ),
    ),
    ("keywords", re.compile(r"(?:keywords|key words|index terms|关键词)[\s:.\-]*", re.I)),
    (
        "introduction",
        re.compile(r"(?:1\.?\s*)?(?:introduction|引言|绪论)[\s:.\-]*", re.I),
    ),
    (
        "acknowledgement",
        re.compile(r"(?:acknowledg\w*|funding|致谢)[\s:.\-]*", re.I),
    ),
)

#: Section headings that terminate an abstract.
_ABSTRACT_STOP = frozenset({"references", "keywords", "introduction", "acknowledgement"})

_MONTHS = "january|february|march|april|may|june|july|august|september|october|november|december"
_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
#: A "Name, Name and Name" byline.  The capitalisation requirement is load
#: bearing: it is what separates a byline from a title that merely contains a
#: comma ("Deep Learning for Astronomy, Survey and Applications").
_AUTHOR_LINE_RE = re.compile(
    r"^\s*(?:[A-Z\u00c0-\u024f][\w'\u00c0-\u024f\-]*\.?\s+){1,3}"
    r"[A-Z\u00c0-\u024f][\w'\u00c0-\u024f\-]*\.?\s*"
    r"(?:,|;|\u3001|\uff1b|\band\b)\s*"
    r"(?:(?:[A-Z\u00c0-\u024f][\w'\u00c0-\u024f\-]*\.?\s*){1,3}"
    r"(?:,|;|\u3001|\uff1b|\band\b|$))+",
)
#: CJK bylines are separated by 、 or ；; capitalisation does not apply.
_AUTHOR_LINE_CJK_RE = re.compile(r"^\s*[\u3400-\u9fff]{2,5}\s*(?:,|\u3001|\uff1b|;)\s*[\u3400-\u9fff]{2,5}")
_TRAILING_GLUE = "\u2013\u2014-\u00b7"


def _plain(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _looks_like_byline(line: str) -> bool:
    """True for an author byline, never for a paper title."""
    plain = _plain(line)
    if not plain or len(plain) > 400:
        return False
    return bool(_AUTHOR_LINE_RE.match(plain) or _AUTHOR_LINE_CJK_RE.match(plain))


def _looks_like_journal_header(line: str) -> bool:
    plain = _plain(line)
    if len(plain) > 160:
        return True
    if re.search(r"\b(?:vol\.?|volume|no\.?|issue|pp\.?|doi|issn)\b", plain, re.I):
        return True
    if re.search(r"https?://|www\.", plain, re.I):
        return True
    if re.search(r"received|accepted|published|\u00a9|copyright", plain, re.I):
        return True
    return bool(re.search(r"\b20\d{2}\b", plain) and len(plain) < 90)


def _merge_wrapped(fragments: Sequence[tuple[str, float]], width: float) -> list[str]:
    """Re-assemble visual lines that one logical line was wrapped into.

    Only a fragment ending on a hyphen, a comma or a wide-but-unterminated line
    followed by a fragment that starts mid-sentence is absorbed; a genuine
    paragraph break (a short, terminated line) is preserved.
    """
    out: list[str] = []
    for text, line_width in fragments:
        if out:
            previous = out[-1]
            if previous.endswith("-") and text[:1].islower():
                out[-1] = previous[:-1] + text
                continue
            if (
                previous.endswith(_TRAILING_GLUE)
                or (width > 0 and line_width >= 0.98 * width and not previous.endswith((".", "?", "!")))
            ) and (text[:1].islower() or text[:1].isdigit()):
                out[-1] = previous + text
                continue
        out.append(text)
    return out


def _page_layout(page: Any) -> dict[str, Any]:
    """Blocks, lines and font sizes of one page, tolerant of a corrupt page."""
    width = 0.0
    try:
        width = float(page.rect.width)
    except Exception:  # noqa: BLE001
        width = 0.0

    blocks: list[dict[str, Any]] = []
    try:
        payload = page.get_text("dict", sort=True)
    except Exception:  # noqa: BLE001 - a corrupt page yields no text
        try:
            plain = page.get_text() or ""
        except Exception:  # noqa: BLE001
            plain = ""
        for line in plain.splitlines():
            text = line.strip()
            if text:
                blocks.append({"text": text, "font_size": 0.0, "lines": [text]})
        return {"blocks": blocks, "width": width}

    for block in payload.get("blocks", []) or []:
        raw_lines = block.get("lines", []) or []
        fragments: list[tuple[str, float]] = []
        sizes: list[float] = []
        for line in raw_lines:
            text = "".join(span.get("text", "") for span in (line.get("spans", []) or [])).strip()
            if not text:
                continue
            try:
                box = line.get("bbox") or (0, 0, 0, 0)
                line_width = float(box[2]) - float(box[0])
            except Exception:  # noqa: BLE001
                line_width = 0.0
            for span in line.get("spans", []) or []:
                try:
                    sizes.append(float(span.get("size") or 0.0))
                except Exception:  # noqa: BLE001
                    continue
            fragments.append((text, line_width))
        if not fragments:
            continue
        lines = _merge_wrapped(fragments, width)
        blocks.append(
            {
                "text": "\n".join(lines),
                "font_size": max(sizes) if sizes else 0.0,
                "lines": lines,
            }
        )
    return {"blocks": blocks, "width": width}


def _read_pdf(data: bytes, limits: Mapping[str, Any]) -> dict[str, Any]:
    """Read the PDF with PyMuPDF; the sole importer of ``fitz``.

    Everything is returned as plain data so the timeout wrapper never holds a
    library object across threads, and no library exception escapes.
    """
    import fitz  # local import: the third-party dependency stays inside the adapter

    if not data[:1024].lstrip().startswith(b"%PDF-"):
        raise PdfImportError(
            "文件头不是 PDF",
            status=422,
            code=ERROR_NOT_A_PDF,
            retryable=False,
        )

    max_pages = int(limits["max_pages"])
    text_budget = int(limits["parser_memory_mib"]) * 1024 * 1024
    try:
        doc = fitz.open(stream=bytes(data), filetype="pdf")
    except Exception as exc:  # noqa: BLE001 - mapped to a contract status
        raise PdfImportError(
            f"无法打开 PDF：{type(exc).__name__}",
            status=422,
            code=ERROR_MALFORMED_PDF,
            retryable=False,
        ) from None

    with doc:
        if doc.needs_pass:
            return {"encrypted": True}
        page_count = int(doc.page_count)
        if page_count > max_pages:
            raise TooManyPagesError(
                f"页数 {page_count} 超过上限 {max_pages}",
                status=413,
            )
        pages: list[dict[str, Any]] = []
        extracted = 0
        for page in doc:
            layout = _page_layout(page)
            extracted += sum(len(block["text"]) for block in layout["blocks"])
            if extracted > text_budget:
                raise PdfImportError(
                    "解析文本超过内存预算",
                    status=413,
                    code=ERROR_RESOURCE_LIMIT,
                    retryable=True,
                )
            pages.append(layout)
        try:
            metadata = dict(doc.metadata or {})
        except Exception:  # noqa: BLE001
            metadata = {}

    return {
        "encrypted": False,
        "pages": pages,
        "metadata": metadata,
        "page_count": page_count,
    }


def _run_with_timeout(data: bytes, limits: Mapping[str, Any]) -> dict[str, Any]:
    """Run :func:`_read_pdf` under ``pdf.parser_timeout_seconds``.

    The deadline is enforced from the caller's side: the wrapper stops waiting
    and reports ``parser_timeout`` (retryable) instead of letting an untrusted
    document hang the request.  The worker is a daemon thread with its own
    interpreter state, so nothing is shared with the request thread.
    """
    timeout = float(limits.get("parser_timeout_seconds", 15))
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(_read_pdf, data, limits)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise PdfParseTimeoutError(
                f"解析超过 {timeout:g} 秒上限",
                details={"retry_after_seconds": 0},
            ) from None
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


# --------------------------------------------------------------------------
# field extraction
# --------------------------------------------------------------------------


def _section_header(line: str, *, heading: bool) -> str | None:
    """Classify a line as a section heading (keyword, or an enlarged line)."""
    plain = _plain(line)
    if not plain or len(plain) > 300:
        return None
    for name, pattern in _SECTION_PATTERNS:
        if pattern.match(plain):
            return name
        if heading and pattern.match(plain.casefold()):
            return name
    return None


def _page_lines(page: Mapping[str, Any]) -> list[str]:
    lines: list[str] = []
    for block in page.get("blocks") or []:
        for line in block.get("lines") or []:
            plain = _plain(line)
            if plain:
                lines.append(plain)
    return lines


def _title_from_metadata(metadata: Mapping[str, Any]) -> str | None:
    raw = str(metadata.get("title") or "").strip()
    if not raw:
        return None
    plain = _plain(raw)
    if len(plain) < 8 or len(plain) > 400:
        return None
    if not any(ch.isalpha() for ch in plain):
        return None
    if re.match(r"^[\w\-. ]+\.(?:pdf|docx?|tex)$", plain, re.I):
        return None
    if _looks_like_byline(plain):
        return None
    if _looks_like_journal_header(plain) and len(plain) < 25:
        return None
    return plain


def _title_from_page(page: Mapping[str, Any]) -> str | None:
    """The title near the top of the first page.

    A line set in a clearly larger body of type than the page's median size is
    the title; otherwise the longest title-like line of the top of the page
    wins.  Author bylines, journal headers and the abstract sentence are never
    candidates.
    """
    lines: list[tuple[str, float]] = []
    for block in (page.get("blocks") or [])[:12]:
        for line in block.get("lines") or []:
            plain = _plain(line)
            if plain:
                lines.append((plain, float(block.get("font_size") or 0.0)))
    if not lines:
        return None

    sizes = sorted(size for _text, size in lines if size > 0)
    median = sizes[len(sizes) // 2] if sizes else 0.0
    first_text, first_size = lines[0]
    if first_size > 0 and median > 0 and first_size >= 1.15 * median and len(first_text) >= 8:
        return first_text[:400].rstrip()
    if first_size > 0.0 and sizes and first_size == sizes[-1] and len(first_text) >= 15:
        return first_text[:400].rstrip()

    candidates: list[str] = []
    for plain, _size in lines[:14]:
        if len(plain) < 8 or len(plain) > 400:
            continue
        if _looks_like_journal_header(plain) or _looks_like_byline(plain):
            continue
        if _SECTION_PATTERNS[1][1].match(plain):
            continue
        candidates.append(plain)
    if not candidates:
        return None
    return max(candidates, key=len)[:400].rstrip()


def _authors_from_page(page: Mapping[str, Any]) -> list[str]:
    """Author names from the byline lines of the first page."""
    names: list[str] = []
    for line in _page_lines(page)[:20]:
        if _looks_like_journal_header(line) or not _looks_like_byline(line):
            continue
        if re.search(
            r"@|\b(?:university|institute|department|college|laborator|academy)\b",
            line,
            re.I,
        ):
            continue
        body = re.sub(r"[\d\*†‡§¶#]+", "", line)
        for part in re.split(r",|;|\band\b|\u3001|\uff1b", body):
            name = _plain(re.sub(r"\(.*?\)", "", part)).strip(" .,;:\u00b7")
            if 2 <= len(name) <= 80 and any(ch.isalpha() for ch in name) and name not in names:
                names.append(name)
    return names


def _year_from_pages(pages: Sequence[Mapping[str, Any]], page_count: int) -> int | None:
    """The earliest plausible publication year in the front matter.

    Only text *before* a "References" heading is considered, so a citation year
    (or a DOI's embedded year) can never be mistaken for the paper's year.
    """
    found: list[int] = []
    for page in list(pages)[: min(2, page_count)]:
        lines = _page_lines(page)
        front: list[str] = []
        for line in lines:
            if _section_header(line, heading=True) == "references":
                break
            front.append(line)
        text = "\n".join(front)
        for match in _YEAR_RE.finditer(text):
            found.append(int(match.group(1)))
        for match in re.finditer(
            r"(?:" + _MONTHS + r")\s+\d{1,2},?\s+((?:19|20)\d{2})",
            text,
            re.I,
        ):
            found.append(int(match.group(1)))
    return min(found) if found else None


def _abstract_from_pages(
    pages: Sequence[Mapping[str, Any]], page_count: int
) -> tuple[str | None, int | None]:
    """The abstract and the 0-based page it starts on."""
    for page_index in range(min(2, page_count)):
        lines = _page_lines(pages[page_index])
        for index, line in enumerate(lines):
            match = _SECTION_PATTERNS[1][1].match(line)
            if not match:
                continue
            tail = (match.group(1) or "").strip(" :.\u3002\u2014-")
            collected: list[str] = [tail] if tail else []
            for following in lines[index + 1 :]:
                plain = _plain(following)
                if not plain:
                    continue
                if _section_header(plain, heading=True) in _ABSTRACT_STOP:
                    break
                collected.append(plain)
                if len(" ".join(collected)) > 4000:
                    break
            abstract = _plain(" ".join(collected))
            if len(abstract) >= 40:
                return abstract[:4000], page_index
    return None, None


def _extract_fields(read: Mapping[str, Any]) -> ParsedPaper:
    pages: list[Mapping[str, Any]] = list(read.get("pages") or [])
    page_count = int(read.get("page_count") or len(pages))
    metadata: Mapping[str, Any] = read.get("metadata") or {}

    # --- DOI zones.  A DOI at or after the "References" heading is a citation
    # (AC-27) and may never become this paper's DOI; only the front matter does.
    reference_dois: list[str] = []
    candidate_dois: list[str] = []
    candidate_evidence: dict[str, dict[str, Any]] = {}
    for page_index, page in enumerate(pages):
        lines = _page_lines(page)
        ref_at: int | None = None
        for index, line in enumerate(lines):
            if _section_header(line, heading=True) == "references":
                ref_at = index
                break
        front = lines[:ref_at] if ref_at is not None else lines
        tail = lines[ref_at:] if ref_at is not None else []
        if page_index < 2:
            for doi in extract_dois("\n".join(front)):
                if doi not in candidate_dois:
                    candidate_dois.append(doi)
                    candidate_evidence[doi] = {
                        "page": page_index + 1,
                        "section": "title" if page_index == 0 else "front_matter",
                    }
        for doi in extract_dois("\n".join(tail)):
            if doi not in reference_dois:
                reference_dois.append(doi)

    evidence: dict[str, dict[str, Any]] = {}

    # --- title: document metadata first, then the top of page 1.
    title = _title_from_metadata(metadata)
    if title:
        evidence["title"] = {"page": 1, "section": "metadata"}
    elif pages:
        title = _title_from_page(pages[0])
        if title:
            evidence["title"] = {"page": 1, "section": "title"}

    # --- abstract
    abstract, abstract_page = _abstract_from_pages(pages, page_count)
    if abstract:
        evidence["abstract"] = {"page": (abstract_page or 0) + 1, "section": "abstract"}

    # --- authors
    authors = _authors_from_page(pages[0]) if pages else []
    if authors:
        evidence["authors"] = {"page": 1, "section": "title"}

    # --- year
    year = _year_from_pages(pages, page_count)
    if year is None:
        for key in ("creationDate", "modDate"):
            match = _YEAR_RE.search(str(metadata.get(key) or ""))
            if match:
                year = int(match.group(1))
                break
    if year is not None:
        evidence["year"] = {"page": 1, "section": "front_matter"}

    doi: str | None = candidate_dois[0] if candidate_dois else None
    if doi:
        evidence["doi"] = dict(candidate_evidence.get(doi, {"page": 1, "section": "title"}))
    if reference_dois:
        evidence["reference_dois"] = {"page": 1, "section": "references"}

    return ParsedPaper(
        title=title,
        authors=authors,
        year=year,
        abstract=abstract,
        doi=doi,
        doi_candidates=candidate_dois,
        reference_dois=reference_dois,
        text_chars=sum(sum(len(block.get("text", "")) for block in page.get("blocks") or [])
                       for page in pages),
        page_count=page_count,
        evidence=evidence,
    )


# --------------------------------------------------------------------------
# parse entry points
# --------------------------------------------------------------------------


def _status_outcome(
    status: ImportStatus,
    *,
    parsed: ParsedPaper | None = None,
    error_code: str | None = None,
    retryable: bool = False,
) -> ParseOutcome:
    return ParseOutcome(
        status=status,
        parsed=parsed,
        error_code=error_code,
        retryable=bool(retryable),
    )


def _outcome_from_exception(exc: Exception) -> ParseOutcome:
    """Translate any failure into a contract status; nothing escapes a traceback."""
    if isinstance(exc, TooManyPagesError):
        return _status_outcome(
            ImportStatus.FAILED, error_code=ERROR_PAGE_LIMIT, retryable=False
        )
    if isinstance(exc, PdfParseTimeoutError):
        return _status_outcome(
            ImportStatus.FAILED, error_code=ERROR_PARSE_TIMEOUT, retryable=True
        )
    if isinstance(exc, V32Error):
        status, retryable = {
            ERROR_NOT_A_PDF: (ImportStatus.INVALID_PDF, False),
            ERROR_MALFORMED_PDF: (ImportStatus.INVALID_PDF, False),
            # A password is a user action, not a repeat of the same request.
            ERROR_ENCRYPTED_PDF: (ImportStatus.ENCRYPTED_PDF, False),
            ERROR_RESOURCE_LIMIT: (ImportStatus.FAILED, True),
        }.get(exc.code, (ImportStatus.FAILED, bool(exc.retryable)))
        return _status_outcome(status, error_code=exc.code, retryable=retryable)
    return _status_outcome(
        ImportStatus.FAILED, error_code=ERROR_MALFORMED_PDF, retryable=False
    )


def parse_pdf_bytes(
    data: bytes,
    display_name: str,
    *,
    limits: Mapping[str, Any] | None = None,
) -> ParseOutcome:
    """Parse one PDF from bytes.  Never raises; every failure is a status.

    ``needs_ocr`` (a readable PDF without a text layer), ``encrypted_pdf`` (a
    password-protected document) and ``invalid_pdf`` (bytes that are not a PDF)
    are distinct outcomes with distinct retryability, and a failure is never
    reported as a successful local seed.
    """
    try:
        blob = bytes(data or b"")
    except (TypeError, ValueError):
        return _status_outcome(
            ImportStatus.INVALID_PDF, error_code=ERROR_NOT_A_PDF, retryable=False
        )
    effective = pdf_limits(limits)

    if not blob:
        return _status_outcome(
            ImportStatus.INVALID_PDF, error_code=ERROR_EMPTY_FILE, retryable=False
        )
    if len(blob) > int(effective["file_max_mib"]) * 1024 * 1024:
        return _status_outcome(
            ImportStatus.FAILED, error_code="file_too_large", retryable=False
        )

    try:
        read = _run_with_timeout(blob, effective)
    except Exception as exc:  # noqa: BLE001 - the adapter boundary
        return _outcome_from_exception(exc)

    if read.get("encrypted"):
        return _status_outcome(
            ImportStatus.ENCRYPTED_PDF, error_code=ERROR_ENCRYPTED_PDF, retryable=False
        )

    try:
        parsed = _extract_fields(read)
    except Exception:  # noqa: BLE001 - a pathological document must not escape
        return _status_outcome(
            ImportStatus.FAILED, error_code=ERROR_MALFORMED_PDF, retryable=False
        )

    if parsed.text_chars < 1:
        # OCR is not enabled this round (``pdf.ocr_enabled`` is false), so a
        # document without a text layer is an explicit, retryable outcome —
        # never a successful local seed.
        return _status_outcome(
            ImportStatus.NEEDS_OCR,
            parsed=parsed,
            error_code=ERROR_NEEDS_OCR,
            retryable=True,
        )

    if not parsed.title and parsed.text_chars < int(effective["minimum_seed_chars"]):
        return _status_outcome(
            ImportStatus.FAILED,
            parsed=parsed,
            error_code=ERROR_INSUFFICIENT_METADATA,
            retryable=False,
        )

    return _status_outcome(ImportStatus.PARSING, parsed=parsed, retryable=False)


def parse_pdf(path: Any, *, limits: Mapping[str, Any] | None = None) -> ParseOutcome:
    """Parse one PDF from a filesystem path.

    The path is consumed here and never travels any further: no returned object
    carries a path field.  A missing or unreadable file becomes ``invalid_pdf``
    rather than an exception.
    """
    try:
        target = Path(path)
        data = target.read_bytes()
    except (OSError, TypeError, ValueError):
        return _status_outcome(
            ImportStatus.INVALID_PDF, error_code="unreadable_file", retryable=True
        )
    return parse_pdf_bytes(data, target.name, limits=limits)


# --------------------------------------------------------------------------
# identity matching
# --------------------------------------------------------------------------


def match_parsed(
    parsed: ParsedPaper,
    candidates: Sequence[Any],
    *,
    config: Mapping[str, Any] | None = None,
) -> MatchOutcome:
    """Decide whether a parsed PDF is an existing paper or a new local seed.

    The decision uses the contract thresholds only: an auto-match needs
    ``score >= auto_match_score`` **and** ``title_similarity >= auto_title_similarity``
    **and** a margin of at least ``minimum_match_margin`` **and** no strong
    identifier conflict; a DOI that agrees with a title similarity of at least
    ``doi_title_similarity`` may auto-match on its own.  ``0.70 <= score`` or an
    insufficient margin goes to ``needs_confirmation``; everything else is
    ``local_seed``.  ``match_score`` is an engineering similarity and is never
    presented as a probability.
    """
    limits = pdf_limits(config)
    auto_score = float(limits["auto_match_score"])
    auto_title = float(limits["auto_title_similarity"])
    candidate_min = float(limits["candidate_score_min"])
    min_margin = float(limits["minimum_match_margin"])
    doi_title_min = float(limits["doi_title_similarity"])
    minimum_chars = int(limits["minimum_seed_chars"])

    scored: list[tuple[float, float, Any]] = []
    for candidate in list(candidates or []):
        scored.append(
            (
                match_score(parsed, candidate),
                title_similarity(parsed.title or "", _candidate_title(candidate)),
                candidate,
            )
        )
    # Order never depends on input order: score, then title, then candidate id.
    scored.sort(key=lambda row: (-row[0], -row[1], _candidate_id(row[2])))

    top_score = scored[0][0] if scored else None
    top_title = scored[0][1] if scored else 0.0
    second_score = scored[1][0] if len(scored) > 1 else 0.0
    margin = (top_score - second_score) if top_score is not None else 0.0

    parsed_doi = _normalize_doi_value(parsed.doi) if parsed.doi else ""
    top_conflicts = _candidate_conflicts(parsed, scored[0][2]) if scored else []
    doi_agrees = bool(parsed_doi) and any(
        parsed_doi == (_candidate_doi(row[2]) or "") for row in scored
    )

    public_candidates = [
        MatchCandidate(
            candidate_id=_candidate_id(row[2]) or f"cand_{index}",
            title=_candidate_title(row[2]),
            doi=_candidate_doi(row[2]),
            year=_candidate_year(row[2]),
            match_score=row[0],
            source=_candidate_source(row[2]),
            conflicts=_candidate_conflicts(parsed, row[2]),
        )
        for index, row in enumerate(scored)
    ]

    enough_title = top_score is not None and top_title >= auto_title
    strong_score = top_score is not None and top_score >= auto_score
    strong_margin = margin >= min_margin

    auto_score_ok = (
        strong_score
        and enough_title
        and strong_margin
        and not top_conflicts
    )
    auto_doi_ok = (
        doi_agrees
        and top_score is not None
        and top_score >= candidate_min
        and top_title >= doi_title_min
        and not top_conflicts
    )

    if scored and (auto_score_ok or auto_doi_ok):
        return MatchOutcome(
            status=ImportStatus.MATCHED,
            paper_id=_candidate_id(scored[0][2]) or None,
            candidates=public_candidates,
            match_score=top_score,
            conflicts=[],
        )

    if top_score is not None and top_score >= candidate_min:
        # A conflicting strong identifier, a DOI whose title disagrees, an
        # ambiguous margin or a merely similar title: the user decides.
        if top_conflicts:
            conflicts = list(top_conflicts)
        elif doi_agrees and top_title < doi_title_min:
            conflicts = ["title"]
        else:
            conflicts = [ERROR_AMBIGUOUS_MATCH]
        return MatchOutcome(
            status=ImportStatus.NEEDS_CONFIRMATION,
            paper_id=None,
            candidates=public_candidates,
            match_score=top_score,
            conflicts=conflicts,
        )

    if not parsed.title and parsed.text_chars < minimum_chars:
        return MatchOutcome(
            status=ImportStatus.FAILED,
            paper_id=None,
            candidates=public_candidates,
            match_score=top_score,
            conflicts=[ERROR_INSUFFICIENT_METADATA],
        )

    return MatchOutcome(
        status=ImportStatus.LOCAL_SEED,
        paper_id=None,
        candidates=public_candidates,
        match_score=top_score,
        conflicts=[],
    )


# --------------------------------------------------------------------------
# DTO helper
# --------------------------------------------------------------------------


def build_import_item(
    *,
    item_id: str,
    attachment: Attachment,
    display_name: str,
    status: ImportStatus,
    parsed: ParsedPaper | None = None,
    match: MatchOutcome | None = None,
    error_code: str | None = None,
    retryable: bool | None = None,
) -> dict[str, Any]:
    """One ``$defs/ImportItem``-shaped record.

    The record is closed over exactly the schema's properties, which is what
    keeps the physical path out of the API: there is no field to put it in.
    """
    effective_status = status
    if match is not None and match.status not in {
        ImportStatus.UPLOADED,
        ImportStatus.PARSING,
        ImportStatus.MATCHING,
    }:
        # A pipeline stage is only replaced by a real decision, never by the
        # "still working" statuses of the matching step itself.
        effective_status = match.status
    if retryable is None:
        retryable = effective_status in IMPORT_RETRYABLE
    score = match.match_score if match is not None else None
    return {
        "item_id": str(item_id),
        "attachment_id": attachment.attachment_id,
        "display_name": sanitize_display_name(display_name),
        "content_hash": attachment.content_hash,
        "status": str(effective_status),
        "paper_id": match.paper_id if match is not None else None,
        "match_candidates": [c.to_dict() for c in (match.candidates if match else [])],
        "match_score": round_tie(safe_float(score)),
        "error_code": error_code,
        "retryable": bool(retryable),
    }


def describe_outcome(parsed: ParseOutcome | None, match: MatchOutcome | None) -> dict[str, Any]:
    """A small JSON-safe summary for logging and tests (no path, no NaN)."""
    return {
        "parse": parsed.to_dict() if parsed is not None else None,
        "match": match.to_dict() if match is not None else None,
    }
