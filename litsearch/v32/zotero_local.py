"""本机 Zotero 接入（S11）：只读本地 API，不写库、不需要 Web API Key。

Zotero 7+ 在本机 `127.0.0.1:23119` 提供一个只读 HTTP 接口（偏好设置 → 高级 →
「允许此计算机上的其他应用程序与 Zotero 通讯」）。这里只调用它，不碰
`zotero.sqlite`，因此 Zotero 开着也不会被锁、不会损坏。

设计边界：
* 只读：列出条目、按关键词/分类检索、读取元数据；
* 不写：不创建条目、不修改分类（写回需要 Zotero Web API，属于外部阻塞）；
* 不缓存用户的库内容：只把用户选中的条目按需取回并写入**项目**。
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Iterable, Mapping

__all__ = [
    "ZoteroLocalError",
    "base_url",
    "collections",
    "items",
    "status",
]

#: ``transport(url, timeout) -> (status, payload)``; tests inject their own.
Transport = Callable[[str, float], tuple[int, Any]]


class ZoteroLocalError(Exception):
    """A user-facing Zotero problem."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def base_url() -> str:
    return os.environ.get("LEEXTRACTOR_ZOTERO_LOCAL", "http://127.0.0.1:23119").rstrip("/")


def _url(path: str, params: Mapping[str, Any] | None = None) -> str:
    url = f"{base_url()}{path}"
    clean = {key: value for key, value in (params or {}).items() if value not in (None, "")}
    return f"{url}?{urllib.parse.urlencode(clean)}" if clean else url


def _http(url: str, timeout: float) -> tuple[int, Any]:
    request = urllib.request.Request(url, headers={"accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed local host
            body = response.read().decode("utf-8", "replace")
            return int(response.status), json.loads(body) if body.strip() else None
    except urllib.error.HTTPError as error:
        return int(error.code), None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise ZoteroLocalError(
            "zotero_unavailable",
            "连不上本机 Zotero。请确认 Zotero 正在运行，并在「偏好设置 → 高级」里允许其他应用通讯。",
        ) from error


def _call(path: str, params: Mapping[str, Any] | None = None, *, transport: Transport | None = None, timeout: float = 8.0):
    status_code, payload = (transport or _http)(_url(path, params), timeout)
    if status_code == 404:
        raise ZoteroLocalError("zotero_not_found", "本机 Zotero 没有这个对象。")
    if status_code >= 400:
        raise ZoteroLocalError("zotero_error", f"本机 Zotero 返回 {status_code}。")
    return payload


def _library(payload: Any) -> tuple[int | None, str]:
    if isinstance(payload, list) and payload:
        library = payload[0].get("library") or {}
        return library.get("id"), str(library.get("name") or "")
    return None, ""


def status(*, transport: Transport | None = None) -> dict[str, Any]:
    """Whether the local API answers, and how big the library is."""
    try:
        sample = _call("/api/users/0/items/top", {"limit": 1}, transport=transport)
        collections_sample = _call("/api/users/0/collections", {"limit": 1}, transport=transport)
    except ZoteroLocalError as error:
        return {"running": False, "code": error.code, "message": error.message}
    library_id, library_name = _library(sample)
    payload = {
        "running": True,
        "library_id": library_id,
        "library_name": library_name or "我的文库",
        "collections": len(collections_sample or []),
        "base_url": base_url(),
    }
    if isinstance(sample, list) and sample:
        payload["sample_item"] = _normalize(sample[0]).get("title")
    return payload


def collections(*, transport: Transport | None = None) -> list[dict[str, Any]]:
    rows = _call("/api/users/0/collections", {"limit": 100}, transport=transport) or []
    out: list[dict[str, Any]] = []
    for row in rows:
        data = row.get("data") or {}
        out.append(
            {
                "key": str(data.get("key") or row.get("key") or ""),
                "name": str(data.get("name") or ""),
                "parent": str(data.get("parentCollection") or "") or None,
            }
        )
    out.sort(key=lambda item: item["name"].lower())
    return out


def _normalize(entry: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(entry.get("data") or {})
    creators = []
    for creator in data.get("creators") or []:
        if not isinstance(creator, Mapping):
            continue
        name = str(creator.get("name") or "").strip()
        if not name:
            name = " ".join(
                part for part in (str(creator.get("firstName") or "").strip(), str(creator.get("lastName") or "").strip()) if part
            )
        if name:
            creators.append(name)
    year = None
    match = re.search(r"(19|20)\d{2}", str(data.get("date") or ""))
    if match:
        year = int(match.group(0))
    return {
        "item_key": str(data.get("key") or entry.get("key") or ""),
        "item_type": str(data.get("itemType") or ""),
        "title": str(data.get("title") or "").strip(),
        "creators": creators,
        "year": year,
        "doi": (str(data.get("DOI") or "").strip() or None),
        "venue": (str(data.get("publicationTitle") or data.get("proceedingsTitle") or "").strip() or None),
        "abstract": (str(data.get("abstractNote") or "").strip() or None),
        "url": (str(data.get("url") or "").strip() or None),
        "date": str(data.get("date") or "").strip() or None,
        "tags": [str(tag.get("tag")) for tag in (data.get("tags") or []) if isinstance(tag, Mapping) and tag.get("tag")],
    }


def items(
    *,
    query: str = "",
    collection: str = "",
    limit: int = 25,
    offset: int = 0,
    transport: Transport | None = None,
) -> list[dict[str, Any]]:
    """Top-level items, optionally filtered by keyword or collection."""
    rows = _call(
        "/api/users/0/items/top",
        {"q": query.strip(), "collection": collection, "limit": max(1, min(int(limit), 100)), "start": max(0, int(offset))},
        transport=transport,
    ) or []
    return [_normalize(row) for row in rows if isinstance(row, Mapping)]


def item(item_key: str, *, transport: Transport | None = None) -> dict[str, Any]:
    key = str(item_key or "").strip()
    if not key:
        raise ZoteroLocalError("item_key_required", "缺少 Zotero 条目键。")
    payload = _call(f"/api/users/0/items/{urllib.parse.quote(key)}", {}, transport=transport)
    if not isinstance(payload, Mapping):
        raise ZoteroLocalError("zotero_not_found", f"Zotero 里找不到条目 {key}。")
    return _normalize(payload)


def to_paper(row: Mapping[str, Any]) -> dict[str, Any]:
    """Zotero row -> the paper record ``V32Store.upsert_paper`` expects."""
    key = str(row.get("item_key") or "")
    return {
        "paper_id": f"zotero-{key}" if key else None,
        "title": row.get("title") or "",
        "authors": list(row.get("creators") or []),
        "year": row.get("year"),
        "abstract": row.get("abstract"),
        "doi": row.get("doi"),
        "venue": row.get("venue"),
        "doc_type": row.get("item_type") or None,
        "landing_url": row.get("url"),
        "source_records": [
            {
                "provider": "zotero_local",
                "external_id": key,
                "source_url": row.get("url"),
                "retrieved_at": None,
            }
        ],
        "aliases": [value for value in (row.get("doi"), f"zotero-{key}" if key else None) if value],
    }


def fetch_many(keys: Iterable[str], *, transport: Transport | None = None) -> list[dict[str, Any]]:
    rows = []
    for key in keys:
        rows.append(item(str(key), transport=transport))
    return rows
