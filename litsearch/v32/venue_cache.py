"""本地期刊缓存（S10）：把见过的期刊、CCF 分区和每本刊在各项目里的篇数存在本机。

为什么要它：影响因子/分区没有本地授权数据源，但**期刊名本身**是检索记录里真实
存在的字段。把它们缓存下来，同一项目刷新、换一个项目、甚至离线开会时，都能
直接回答"这本刊我见过多少篇、CCF 是几区"，不必依赖在线服务，也不会因为刷新
而把计数越加越多（计数按项目存，重复构建是幂等的）。

文件：``{data_dir}/cache/venues.json``，原子替换，不含任何凭据。
CCF 分区来自仓库自带的 ``litsearch/ccf_catalog.json``（2026 第七版，681 条）。
"""
from __future__ import annotations

import json
import re
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping

__all__ = [
    "cache_path",
    "clear",
    "import_csv",
    "lookup_rank",
    "record",
    "summary",
]

_LOCK = threading.Lock()
_VERSION = 1


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def cache_path(root: Path | str) -> Path:
    return Path(root) / "cache" / "venues.json"


@lru_cache(maxsize=1)
def _catalog_entries() -> tuple[dict[str, dict[str, Any]], list[tuple[str, str, dict[str, Any]]]]:
    """(exact index, acronym list) from the bundled catalogue, loaded once."""
    path = Path(__file__).resolve().parent.parent / "ccf_catalog.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # pragma: no cover - shipped with the package
        return {}, []
    edition = str(document.get("edition") or "")
    by_name: dict[str, dict[str, Any]] = {}
    acronyms: list[tuple[str, str, dict[str, Any]]] = []
    for entry in document.get("entries", []):
        row = {
            "rank": str(entry.get("rank") or ""),
            "kind": str(entry.get("kind") or ""),
            "edition": edition,
        }
        full = _normalise(str(entry.get("full_name") or ""))
        acronym = str(entry.get("acronym") or "").strip()
        if full:
            by_name.setdefault(full, row)
        if acronym:
            by_name.setdefault(_normalise(acronym), row)
            acronyms.append((acronym.lower(), full, row))
    return by_name, acronyms


def _catalog() -> dict[str, dict[str, Any]]:
    return _catalog_entries()[0]


def _acronym_hit(venue: str) -> dict[str, Any] | None:
    """A catalogue acronym that appears as a standalone word in the venue string."""
    text = f" {str(venue or '').lower()} "
    best: tuple[int, dict[str, Any]] | None = None
    for acronym, _full, row in _catalog_entries()[1]:
        if len(acronym) < 3:
            continue
        marker = re.compile(r"(?<![a-z0-9])" + re.escape(acronym) + r"(?![a-z0-9])")
        if marker.search(text) and (best is None or len(acronym) > best[0]):
            best = (len(acronym), row)
    return dict(best[1]) if best else None


def _fuzzy_hit(venue: str) -> dict[str, Any] | None:
    """Fuzzy fallback for spelled-out names (IEEE/CVF …, Proceedings of …)."""
    from difflib import SequenceMatcher

    key = _normalise(venue)
    if len(key) < 12:
        return None
    best_ratio = 0.0
    best_row: dict[str, Any] | None = None
    matcher = SequenceMatcher()
    matcher.set_seq2(key)
    for candidate, row in _catalog().items():
        if len(candidate) < 12:
            continue
        matcher.set_seq1(candidate)
        if matcher.quick_ratio() < 0.9:
            continue
        ratio = matcher.ratio()
        if ratio > best_ratio:
            best_ratio, best_row = ratio, row
    return dict(best_row) if best_ratio >= 0.92 else None


#: 出版社/系列前缀在检索记录里经常出现，但 CCF 目录里没有它们。
_NOISE = ("ieee/cvf", "ieee", "acm", "proceedings of the", "proceedings of", "springer", "elsevier")


def _variants(venue: str) -> list[str]:
    text = str(venue or "").lower()
    for noise in _NOISE:
        text = text.replace(noise, " ")
    variants = [_normalise(text)]
    # 去掉年份与会议序号（"2023 IEEE/CVF …"）
    stripped = re.sub(r"\b(19|20)\d{2}\b", " ", text)
    variants.append(_normalise(stripped))
    return [item for item in dict.fromkeys(variants) if item]


def lookup_rank(venue: str) -> dict[str, Any] | None:
    """Resolve a venue to its CCF rank: exact, cleaned-exact, acronym, fuzzy.

    目录只覆盖计算机领域（CCF 2026 第七版），其它学科的期刊不会命中，
    这是目录边界，不是匹配失败。影响因子请用 ``import_csv`` 导入本地数据。
    """
    if not _normalise(venue):
        return None
    for variant in _variants(venue):
        row = _catalog().get(variant)
        if row:
            return dict(row)
    # 缩写与近似匹配也跑一遍清洗后的写法（"2023 IEEE/CVF …" 这类前缀会压低相似度）。
    for text in [str(venue), *[venue.replace(noise, " ") for noise in _NOISE]]:
        row = _acronym_hit(text) or _fuzzy_hit(text)
        if row:
            return row
    return None


def _load(root: Path | str) -> dict[str, Any]:
    path = cache_path(root)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        document = {}
    if not isinstance(document, dict) or document.get("version") != _VERSION:
        return {"version": _VERSION, "updated_at": None, "venues": {}}
    document.setdefault("venues", {})
    return document


def _save(root: Path | str, document: dict[str, Any]) -> None:
    path = cache_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    document["version"] = _VERSION
    document["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def record(root: Path | str, project_id: str, records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Record every venue seen in one project's corpus. Idempotent per project."""
    project = str(project_id or "")
    if not project:
        return _load(root)
    counts: dict[str, int] = {}
    display: dict[str, str] = {}
    years: dict[str, list[int]] = {}
    for record in records:
        venue = str(record.get("venue") or "").strip()
        if not venue:
            continue
        key = _normalise(venue)
        if not key:
            continue
        counts[key] = counts.get(key, 0) + 1
        display.setdefault(key, venue)
        year = record.get("year")
        if isinstance(year, int):
            span = years.setdefault(key, [year, year])
            span[0] = min(span[0], year)
            span[1] = max(span[1], year)
    with _LOCK:
        document = _load(root)
        venues = document["venues"]
        for key, count in counts.items():
            entry = venues.setdefault(
                key,
                {
                    "name": display[key],
                    "ccf": lookup_rank(display[key]),
                    "projects": {},
                    "years": years.get(key),
                },
            )
            entry["name"] = display[key]
            entry.setdefault("ccf", lookup_rank(display[key]))
            entry.setdefault("projects", {})[project] = count
            if years.get(key):
                entry["years"] = years[key]
        _save(root, document)
        return document


def summary(root: Path | str, limit: int = 50) -> list[dict[str, Any]]:
    """Venues ordered by total records, with their CCF rank when known."""
    document = _load(root)
    rows: list[dict[str, Any]] = []
    for entry in document["venues"].values():
        projects = entry.get("projects") or {}
        total = sum(int(value) for value in projects.values())
        ccf = entry.get("ccf") or lookup_rank(str(entry.get("name") or "")) or {}
        years = entry.get("years") or [None, None]
        journal = entry.get("journal") or {}
        rows.append(
            {
                "name": entry.get("name") or "",
                "count": total,
                "project_count": len(projects),
                "ccf_rank": ccf.get("rank") or None,
                "ccf_kind": ccf.get("kind") or None,
                "ccf_edition": ccf.get("edition") or None,
                "impact_factor": journal.get("impact_factor"),
                "quartile": journal.get("quartile"),
                "journal_year": journal.get("year"),
                "journal_source": journal.get("source"),
                "year_from": years[0],
                "year_to": years[1],
            }
        )
    rows.sort(key=lambda row: (-int(row["count"]), str(row["name"]).lower()))
    return rows[: max(1, int(limit))]


def clear(root: Path | str) -> bool:
    try:
        cache_path(root).unlink()
        return True
    except OSError:
        return False


#: 导入 CSV 时可接受的列名（大小写与空格无关）。
_IMPORT_COLUMNS = {
    "name": ("name", "venue", "journal", "title", "期刊", "刊名"),
    "impact_factor": ("impact_factor", "if", "jif", "影响因子"),
    "quartile": ("quartile", "分区", "jcr", "q"),
    "issn": ("issn",),
    "year": ("year", "年份"),
}


def import_csv(root: Path | str, path: Path | str) -> dict[str, Any]:
    """Merge a local journal table (CSV/TSV) into the cache.

    Recognised columns (any order, case-insensitive): name/venue/journal,
    impact_factor/IF, quartile, issn, year. This is how a user supplies an
    impact factor offline — the cache never downloads or invents one.
    """
    import csv

    source = Path(path)
    delimiter = "\t" if source.suffix.lower() == ".tsv" else ","
    merged = 0
    skipped = 0
    with _LOCK:
        document = _load(root)
        venues = document["venues"]
        with source.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=delimiter)
            for raw in reader:
                row = {str(key or "").strip().lower().replace(" ", "_"): value for key, value in raw.items()}
                name = ""
                for column in _IMPORT_COLUMNS["name"]:
                    if row.get(column):
                        name = str(row[column]).strip()
                        break
                if not name:
                    skipped += 1
                    continue
                key = _normalise(name)
                if not key:
                    skipped += 1
                    continue
                entry = venues.setdefault(
                    key,
                    {"name": name, "ccf": lookup_rank(name), "projects": {}, "years": None},
                )
                meta = dict(entry.get("journal") or {})
                for field_name, columns in _IMPORT_COLUMNS.items():
                    if field_name == "name":
                        continue
                    for column in columns:
                        value = row.get(column)
                        if value not in (None, ""):
                            meta[field_name] = str(value).strip()
                            break
                meta["source"] = source.name
                entry["journal"] = meta
                merged += 1
        _save(root, document)
    return {"merged": merged, "skipped": skipped, "file": source.name}
