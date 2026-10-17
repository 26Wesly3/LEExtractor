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
def _catalog() -> dict[str, dict[str, Any]]:
    """normalised name/acronym -> {rank, kind, edition}. Loaded once per process."""
    # 目录文件在包根（litsearch/ccf_catalog.json），不在 v32/ 下。
    path = Path(__file__).resolve().parent.parent / "ccf_catalog.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # pragma: no cover - shipped with the package
        return {}
    edition = str(document.get("edition") or "")
    table: dict[str, dict[str, Any]] = {}
    for entry in document.get("entries", []):
        row = {
            "rank": str(entry.get("rank") or ""),
            "kind": str(entry.get("kind") or ""),
            "edition": edition,
        }
        for key in (entry.get("full_name"), entry.get("acronym")):
            normalised = _normalise(key)
            if normalised:
                table.setdefault(normalised, row)
    return table


def lookup_rank(venue: str) -> dict[str, Any] | None:
    """Exact (normalised) match against the bundled CCF catalogue, or None."""
    key = _normalise(venue)
    if not key:
        return None
    row = _catalog().get(key)
    return dict(row) if row else None


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
        rows.append(
            {
                "name": entry.get("name") or "",
                "count": total,
                "project_count": len(projects),
                "ccf_rank": ccf.get("rank") or None,
                "ccf_kind": ccf.get("kind") or None,
                "ccf_edition": ccf.get("edition") or None,
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
