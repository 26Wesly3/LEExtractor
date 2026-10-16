"""Project-scoped persistence for the v3.2 incremental API.

This is the evolution of :mod:`litsearch.web.repository` required by
``docs/03_SYSTEM_ARCHITECTURE.md`` ("业务编排连接发现、规范身份、排序、反馈…") and
``docs/14_JOBS_IDENTITY_MIGRATION.md``.  It keeps the legacy repository intact
— the old routes still read it — and adds the three versioned counters the
specification depends on:

``revision``
    The persisted version of *project content*.  Bumped by a judgement, an
    intent change, an import-item confirmation or a candidate-batch commit.
    **Not** bumped by job progress or by committing a ranking snapshot.
``context_version``
    Bumped when the intent, the seeds, the judgements, the model selection or
    the ranking configuration change.  A computation reads a frozen value.
``corpus_version``
    Bumped when candidate facts or the evidence graph change.

Plus canonical identity: an immutable internal ``paper_id`` with external
identifiers held only as aliases, and an explicit merge that keeps the older
internal id and leaves a redirect alias behind (doc 14, AC-13).

Everything is written through :func:`atomic_json` on a temporary file plus
``os.replace``, under a per-project lock, so a crash cannot leave a half-written
project.  ``allow_nan=False`` is not incidental: it is the last line of defence
for AC-07 ("未知为 null，不产生 NaN 或虚构 0").
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import shutil
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from litsearch.jsonio import atomic_json

from .contracts import job_defaults
from .errors import (
    CURSOR_STALE,
    IDEMPOTENCY_CONFLICT,
    NOT_FOUND,
    REVISION_CONFLICT,
    UNPROCESSABLE,
    V32Error,
)
from .types import ExclusionReason, JudgementValue

__all__ = [
    "SCHEMA_VERSION",
    "CONTRACT_VERSION",
    "V32Project",
    "V32Store",
    "encode_cursor",
    "decode_cursor",
    "content_hash",
]

#: Persisted integer schema of a v3.2 project document.  Deliberately a small
#: integer and deliberately *not* the document version "3.2" (doc 14: "不能将
#: 文档版本 3.2 直接当成旧整数 schema+1").
SCHEMA_VERSION = 1

#: The contract this document conforms to; recorded next to the integer.
CONTRACT_VERSION = "3.2"

#: Longest id we accept in a path segment.
_ID_RE = re.compile(r"^[a-f0-9]{32}$")

#: Legacy session schema versions we can migrate from, in ascending order.
SUPPORTED_LEGACY_SCHEMA_VERSIONS = (0,)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def content_hash(payload: bytes) -> str:
    """SHA256 of file content — the de-duplication key of doc 14/AC-04."""
    return hashlib.sha256(payload).hexdigest()


#: A project document is a user-visible, exportable artefact.  These field names
#: and these value shapes must never appear in one (doc 10: "Key 不进入项目 JSON").
#: The key-name rule is the reliable half; the value patterns are narrow enough
#: that a paper abstract cannot trip them.
#:
#: The word must be *credential-qualified* ("api_key", "access_token",
#: "secret_value") and anchored on a word boundary, because Zotero's perfectly
#: legitimate ``item_key`` contains a bare "key" and must keep working.
_FORBIDDEN_FIELD_RE = re.compile(
    r"(?:^|[_\-.])("
    r"api[_-]?key|apikey|secret|"
    r"access[_-]?token|refresh[_-]?token|auth[_-]?token|auth[_-]?key|id[_-]?token|"
    r"bearer|authorization|password|passwd|credential|private[_-]?key"
    r")(?:$|[_\-.])",
    re.I,
)
_FORBIDDEN_VALUE_RES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("semantic_scholar_key", re.compile(r"\bs2k-[A-Za-z0-9]{20,}\b")),
    ("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{24,}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("private_key_block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")),
)


def _reject_secrets(node: Any, *, path: str = "") -> None:
    """Refuse to persist anything credential-shaped.

    Called from :meth:`V32Store._write`, so *every* write path is covered by
    construction rather than by remembering.  The error deliberately names the
    field location but never the value — an exception message ends up in logs.
    """
    from .errors import SECRET_IN_PROJECT_DOCUMENT

    if isinstance(node, dict):
        for key, value in node.items():
            location = f"{path}.{key}" if path else str(key)
            if isinstance(key, str) and _FORBIDDEN_FIELD_RE.search(key):
                raise V32Error(
                    500, SECRET_IN_PROJECT_DOCUMENT,
                    f"项目文档中禁止出现凭据字段（{path or '<root>'}），请改用会话级凭据存储",
                )
            _reject_secrets(value, path=location)
    elif isinstance(node, (list, tuple)):
        for index, value in enumerate(node):
            _reject_secrets(value, path=f"{path}[{index}]")
    elif isinstance(node, str):
        for name, pattern in _FORBIDDEN_VALUE_RES:
            if pattern.search(node):
                raise V32Error(
                    500, SECRET_IN_PROJECT_DOCUMENT,
                    f"项目文档中禁止出现凭据值（{path or '<root>'}，形如 {name}）",
                )


# ---------------------------------------------------------------------------
# opaque pagination cursors
# ---------------------------------------------------------------------------


def encode_cursor(snapshot_id: str | None, offset: int) -> str:
    """An opaque backend cursor.  The front end never assembles one."""
    raw = json.dumps({"s": snapshot_id, "o": int(offset)}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> tuple[str | None, int]:
    """Decode a cursor, or ``(None, 0)`` when absent.

    A malformed cursor is a 422, not a silent restart from the beginning: a
    silently-reset page is how a client ends up duplicating rows.
    """
    if not cursor:
        return None, 0
    padding = "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(cursor + padding)
        payload = json.loads(raw)
        snapshot_id = payload.get("s")
        offset = int(payload["o"])
    except (binascii.Error, ValueError, KeyError, TypeError, UnicodeDecodeError):
        raise V32Error(422, UNPROCESSABLE, "分页游标无效，请重新加载列表") from None
    if offset < 0:
        raise V32Error(422, UNPROCESSABLE, "分页游标无效，请重新加载列表")
    return snapshot_id, offset


# ---------------------------------------------------------------------------
# the project document
# ---------------------------------------------------------------------------


@dataclass
class V32Project:
    """A loaded project plus its write lock.

    ``doc`` is the authoritative in-memory document.  Callers mutate it only
    while holding ``lock`` and then call :meth:`V32Store.save`.
    """

    doc: dict[str, Any]
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    # -- convenience accessors --------------------------------------------

    @property
    def project_id(self) -> str:
        return str(self.doc["project_id"])

    @property
    def owner_id(self) -> str:
        return str(self.doc["owner_id"])

    @property
    def revision(self) -> int:
        return int(self.doc["revision"])

    @property
    def context_version(self) -> int:
        return int(self.doc["context_version"])

    @property
    def corpus_version(self) -> int:
        return int(self.doc["corpus_version"])

    @property
    def active_job_ids(self) -> list[str]:
        return self.doc.setdefault("active_job_ids", [])

    def snapshot_summary(self, snapshot_id: str | None = None) -> dict[str, Any] | None:
        sid = snapshot_id or self.doc.get("current_snapshot_id")
        if not sid:
            return None
        return self.doc.setdefault("snapshots", {}).get(sid)


class V32Store:
    """Atomic, owner-scoped storage rooted at ``<data_dir>/v32``."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.projects_dir = self.root / "projects"
        self.snapshots_dir = self.root / "snapshots"
        self.files_dir = self.root / "files"
        self.backups_dir = self.root / "backups"
        self.idempotency_dir = self.root / "idempotency"
        for directory in (
            self.projects_dir,
            self.snapshots_dir,
            self.files_dir,
            self.backups_dir,
            self.idempotency_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.projects: dict[str, V32Project] = {}
        self.load_errors: list[str] = []
        self.migrations_applied: list[dict[str, Any]] = []
        self._load()

    # -- loading -----------------------------------------------------------

    def _load(self) -> None:
        for path in sorted(self.projects_dir.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                # A corrupt or half-written file is preserved, never replaced.
                self.load_errors.append(f"{path.name}: unreadable ({exc})")
                continue
            try:
                doc = self._migrate_document(raw, source=path)
            except V32Error as exc:
                self.load_errors.append(f"{path.name}: migration refused ({exc.code})")
                continue
            self.projects[doc["project_id"]] = V32Project(doc)

    # -- schema migration --------------------------------------------------

    def _migrate_document(self, raw: dict[str, Any], *, source: Path | None = None) -> dict[str, Any]:
        """Bring ``raw`` up to :data:`SCHEMA_VERSION`, backing up first.

        The chain is explicit and per-version (doc 14).  A document whose
        version is *newer* than this build understands is refused and its file
        is left untouched, so an older program can never silently rewrite a
        newer schema.
        """
        version = raw.get("schema_version")
        if version is None:
            # A legacy session document: no integer schema at all.
            version = 0
        if not isinstance(version, int) or isinstance(version, bool) or version < 0:
            raise V32Error(422, UNPROCESSABLE, "项目文件 schema_version 非法，已保留原文件")
        if version > SCHEMA_VERSION:
            raise V32Error(
                422,
                UNPROCESSABLE,
                f"项目文件 schema_version={version} 高于本程序支持的 {SCHEMA_VERSION}，已保留原文件",
            )
        if version == SCHEMA_VERSION and raw.get("contract_version") == CONTRACT_VERSION:
            self._verify_integrity(raw)
            return raw

        working = json.loads(json.dumps(raw))  # deep copy, never mutate the caller's
        while version < SCHEMA_VERSION:
            migration = _MIGRATIONS.get(version)
            if migration is None:
                raise V32Error(422, UNPROCESSABLE, f"缺少 schema_version={version} 的迁移链")
            working = migration(working)
            version = int(working["schema_version"])

        working["contract_version"] = CONTRACT_VERSION
        self._verify_integrity(working)
        if source is not None:
            backup = self._backup(source)
            self.migrations_applied.append(
                {
                    "file": source.name,
                    "from": raw.get("schema_version", 0),
                    "to": SCHEMA_VERSION,
                    "backup": str(backup) if backup else None,
                    "at": now(),
                }
            )
            atomic_json(source, working)
        return working

    def _backup(self, source: Path) -> Path | None:
        if not source.is_file():
            return None
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        target = self.backups_dir / f"{source.stem}.{stamp}.json"
        try:
            shutil.copy2(source, target)
        except OSError:
            return None
        return target

    @staticmethod
    def _verify_integrity(doc: dict[str, Any]) -> None:
        """Referential integrity: no dangling reference survives a migration."""
        papers = set(doc.get("papers", {}))
        aliases = doc.get("aliases", {})
        for alias, target in aliases.items():
            if target not in papers:
                raise V32Error(422, UNPROCESSABLE, f"alias {alias!r} 指向不存在的 paper_id")
        for pid in doc.get("judgements", {}):
            if pid not in papers:
                raise V32Error(422, UNPROCESSABLE, f"判断引用不存在的 paper_id {pid!r}")
        for edge in doc.get("edges", {}).values():
            for key in ("from_paper_id", "to_paper_id"):
                if edge.get(key) and edge[key] not in papers:
                    raise V32Error(422, UNPROCESSABLE, f"证据边引用不存在的 paper_id {edge[key]!r}")
        for pid in doc.get("seeds", []):
            if pid not in papers:
                raise V32Error(422, UNPROCESSABLE, f"种子引用不存在的 paper_id {pid!r}")
        current = doc.get("current_snapshot_id")
        if current and current not in doc.get("snapshots", {}):
            raise V32Error(422, UNPROCESSABLE, "current_snapshot_id 指向不存在的快照")

    # -- create / lookup ---------------------------------------------------

    @staticmethod
    def validate_id(value: str, *, what: str = "资源") -> str:
        if not isinstance(value, str) or not _ID_RE.fullmatch(value):
            raise V32Error(404, NOT_FOUND, f"{what}不存在")
        return value

    def create_project(
        self,
        *,
        owner_id: str,
        name: str,
        entry_mode: str,
        anchor: dict[str, Any] | None = None,
    ) -> V32Project:
        stamp = now()
        doc: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "contract_version": CONTRACT_VERSION,
            "project_id": uuid.uuid4().hex,
            "owner_id": owner_id,
            "name": (name or "").strip() or "未命名项目",
            "entry_mode": entry_mode,
            "revision": 1,
            "context_version": 1,
            "corpus_version": 1,
            "model_revision": None,
            "current_snapshot_id": None,
            "active_job_ids": [],
            "created_at": stamp,
            "updated_at": stamp,
            "anchor": anchor or _empty_anchor(entry_mode),
            "papers": {},
            "project_papers": {},
            "aliases": {},
            "redirects": {},
            "judgements": {},
            "judgement_events": [],
            "event_sequence": 0,
            "edges": {},
            "snapshots": {},
            "imports": {},
            "attachments": {},
            "seeds": [],
            "zotero_map": {},
            "prisma": {},
        }
        project = V32Project(doc)
        with self.lock:
            self.projects[doc["project_id"]] = project
            self._write(project)
        return project

    def get(self, project_id: str, *, owner_id: str) -> V32Project:
        """Fetch a project the caller owns.

        A project owned by somebody else is reported as ``404``, not ``403``:
        doc 08 says 404 covers "资源不存在或无权查看", and a distinguishable 403
        would leak the existence of another user's project (AC-25).
        """
        self.validate_id(project_id, what="项目")
        with self.lock:
            project = self.projects.get(project_id)
        if project is None or project.owner_id != owner_id:
            raise V32Error(404, NOT_FOUND, "项目不存在")
        return project

    def all_for_owner(self, owner_id: str) -> list[V32Project]:
        with self.lock:
            return [p for p in self.projects.values() if p.owner_id == owner_id]

    # -- writing -----------------------------------------------------------

    def _write(self, project: V32Project) -> None:
        _reject_secrets(project.doc)
        atomic_json(self.projects_dir / f"{project.project_id}.json", project.doc)

    def save(
        self,
        project: V32Project,
        *,
        touch_revision: bool = False,
        touch_context: bool = False,
        touch_corpus: bool = False,
    ) -> dict[str, Any]:
        """Persist ``project``, bumping only the counters the caller owns."""
        with project.lock:
            if touch_revision:
                project.doc["revision"] = project.revision + 1
            if touch_context:
                project.doc["context_version"] = project.context_version + 1
            if touch_corpus:
                project.doc["corpus_version"] = project.corpus_version + 1
            project.doc["updated_at"] = now()
            self._verify_integrity(project.doc)
            self._write(project)
            return self.project_dto(project)

    def check_revision(self, project: V32Project, expected: int | None) -> None:
        """Compare ``expected_revision`` against both memory and disk."""
        if expected is None:
            raise V32Error(409, REVISION_CONFLICT, "请提供当前项目 revision", {"current_revision": project.revision})
        path = self.projects_dir / f"{project.project_id}.json"
        try:
            on_disk = int(json.loads(path.read_text(encoding="utf-8"))["revision"])
        except (OSError, ValueError, KeyError, TypeError):
            raise V32Error(409, REVISION_CONFLICT, "项目存储已改变，请刷新后重试",
                           {"current_revision": project.revision}) from None
        if int(expected) != project.revision or on_disk != project.revision:
            raise V32Error(409, REVISION_CONFLICT, "项目已被其他操作更新，请刷新后重试",
                           {"current_revision": project.revision})

    # -- DTOs --------------------------------------------------------------

    def project_dto(self, project: V32Project) -> dict[str, Any]:
        """``$defs/Project`` — deliberately without owner_id."""
        doc = project.doc
        return {
            "project_id": doc["project_id"],
            "name": doc["name"],
            "entry_mode": doc["entry_mode"],
            "revision": doc["revision"],
            "context_version": doc["context_version"],
            "corpus_version": doc["corpus_version"],
            "current_snapshot_id": doc.get("current_snapshot_id"),
            "active_job_ids": list(doc.get("active_job_ids", [])),
            "created_at": doc["created_at"],
            "updated_at": doc["updated_at"],
            "contract_version": doc.get("contract_version", CONTRACT_VERSION),
            "anchor": doc["anchor"],
        }

    # -- identity ----------------------------------------------------------

    def resolve_paper_id(self, project: V32Project, alias_or_id: str) -> str | None:
        """Resolve an alias (or a bare ``paper_id``) to the canonical id."""
        doc = project.doc
        if alias_or_id in doc["papers"]:
            return alias_or_id
        target = doc["aliases"].get(alias_or_id)
        seen: set[str] = set()
        while target is not None and target in doc["redirects"] and target not in seen:
            seen.add(target)
            target = doc["redirects"][target]
        return target

    def upsert_paper(self, project: V32Project, paper: dict[str, Any]) -> str:
        """Insert or enrich a paper, merging aliases; returns the canonical id.

        An incoming record that carries an identifier already mapped to a
        different internal id merges the two identities instead of creating a
        second record for one work (doc 14: "两个既有内部 ID 合并时选创建时间更早者").
        """
        doc = project.doc
        aliases = {str(a) for a in paper.get("aliases", []) if a}
        if paper.get("doi"):
            aliases.add(str(paper["doi"]))
        if paper.get("paper_id"):
            aliases.add(str(paper["paper_id"]))

        candidate_ids = {
            resolved
            for alias in aliases
            if (resolved := doc["aliases"].get(alias)) is not None and resolved in doc["papers"]
        }
        for alias in sorted(aliases):
            if alias in doc["papers"]:
                candidate_ids.add(alias)

        if not candidate_ids:
            paper_id = str(paper.get("paper_id") or uuid.uuid4().hex)
            record = _new_paper_record(paper, paper_id)
            doc["papers"][paper_id] = record
            doc["project_papers"][paper_id] = _new_project_paper(project.project_id, paper_id)
            for alias in aliases:
                doc["aliases"][alias] = paper_id
            return paper_id

        keep = sorted(candidate_ids, key=lambda pid: (doc["papers"][pid]["created_at"], pid))[0]
        for other in sorted(candidate_ids - {keep}):
            self.merge_identity(project, keep, other)
        _enrich_paper(doc["papers"][keep], paper)
        for alias in aliases:
            doc["aliases"][alias] = keep
        return keep

    def merge_identity(self, project: V32Project, keep_id: str, drop_id: str) -> str:
        """Merge ``drop_id`` into ``keep_id``, moving every reference.

        Aliases, attachments, judgements, evidence edges and PRISMA/Zotero
        mappings migrate (AC-13); the dropped id remains as a redirect alias so
        an old link still resolves.  Self-loops are removed and duplicate edges
        collapse onto their witness sources.
        """
        doc = project.doc
        if keep_id == drop_id:
            return keep_id
        if keep_id not in doc["papers"] or drop_id not in doc["papers"]:
            raise V32Error(404, NOT_FOUND, "要合并的文献不存在")

        for alias, target in list(doc["aliases"].items()):
            if target == drop_id:
                doc["aliases"][alias] = keep_id
        doc["aliases"][drop_id] = keep_id
        doc["redirects"][drop_id] = keep_id

        # attachment references
        for attachment in doc["attachments"].values():
            refs = attachment.get("item_refs", [])
            if drop_id in refs:
                attachment["item_refs"] = sorted({keep_id if r == drop_id else r for r in refs})
        # project-paper rows
        if drop_id in doc["project_papers"]:
            dropped = doc["project_papers"].pop(drop_id)
            kept = doc.setdefault("project_papers", {}).setdefault(
                keep_id, _new_project_paper(project.project_id, keep_id)
            )
            kept["attachment_ids"] = sorted(set(kept.get("attachment_ids", [])) | set(dropped.get("attachment_ids", [])))
            kept["seed_origin"] = kept.get("seed_origin") or dropped.get("seed_origin")
        # judgements: the later explicit user action wins (doc 14)
        dropped_judgement = doc["judgements"].pop(drop_id, None)
        kept_judgement = doc["judgements"].get(keep_id)
        if dropped_judgement and (
            kept_judgement is None
            or (dropped_judgement.get("event_sequence", 0), dropped_judgement.get("updated_at", ""))
            > (kept_judgement.get("event_sequence", 0), kept_judgement.get("updated_at", ""))
        ):
            doc["judgements"][keep_id] = {**dropped_judgement, "paper_id": keep_id}
        for event in doc.get("judgement_events", []):
            if event.get("paper_id") == drop_id:
                event["paper_id"] = keep_id
        # evidence edges: rewrite endpoints, drop self-loops, merge duplicates
        rebuilt: dict[str, dict[str, Any]] = {}
        for edge in doc["edges"].values():
            source = keep_id if edge["from_paper_id"] == drop_id else edge["from_paper_id"]
            target = keep_id if edge["to_paper_id"] == drop_id else edge["to_paper_id"]
            if source == target:
                continue  # 图自环在合并后移除
            key = (source, target, edge["relation"])
            existing = next((e for e in rebuilt.values() if (e["from_paper_id"], e["to_paper_id"], e["relation"]) == key), None)
            if existing is None:
                merged = dict(edge)
                merged["from_paper_id"], merged["to_paper_id"] = source, target
                rebuilt[str(edge["edge_id"])] = merged
            else:
                existing["witness_paper_ids"] = sorted(
                    set(existing.get("witness_paper_ids", [])) | set(edge.get("witness_paper_ids", []))
                )
                existing["source_records"] = _dedupe_records(
                    list(existing.get("source_records", [])) + list(edge.get("source_records", []))
                )
        doc["edges"] = rebuilt
        # seeds, zotero map, linked versions
        doc["seeds"] = sorted({keep_id if s == drop_id else s for s in doc.get("seeds", [])})
        if drop_id in doc.get("zotero_map", {}):
            doc["zotero_map"].setdefault(keep_id, doc["zotero_map"].pop(drop_id))
        for record in doc["papers"].values():
            if drop_id in record.get("linked_version_ids", []):
                record["linked_version_ids"] = sorted(
                    {keep_id if v == drop_id else v for v in record["linked_version_ids"]}
                ) - {record["paper_id"]}
        doc["papers"].pop(drop_id, None)
        doc["corpus_version"] = project.corpus_version + 1
        return keep_id

    # -- judgements --------------------------------------------------------

    def set_judgement(
        self,
        project: V32Project,
        *,
        expected_revision: int | None,
        paper_id: str,
        value: str,
        exclusion_reason: str = ExclusionReason.UNSPECIFIED.value,
        reason: str = "",
    ) -> dict[str, Any]:
        """Atomically save one judgement and return the new revision.

        Cross-field rule from doc 08: a non-``excluded`` value must carry
        ``exclusion_reason = unspecified``.  Revoking (``unjudged``) deletes the
        judgement and removes its feedback effect while the historical event
        stays in the sequence, so "撤销清除其反馈作用" holds without losing the
        audit trail.
        """
        allowed_values = {v.value for v in JudgementValue}
        allowed_reasons = {r.value for r in ExclusionReason}
        if value not in allowed_values:
            raise V32Error(422, UNPROCESSABLE, "判断取值非法")
        if exclusion_reason not in allowed_reasons:
            raise V32Error(422, UNPROCESSABLE, "排除原因取值非法")
        if value != JudgementValue.EXCLUDED.value and exclusion_reason != ExclusionReason.UNSPECIFIED.value:
            raise V32Error(422, UNPROCESSABLE, "非排除判断必须使用 unspecified 排除原因")

        with project.lock:
            self.check_revision(project, expected_revision)
            canonical = self.resolve_paper_id(project, paper_id)
            if canonical is None:
                raise V32Error(404, NOT_FOUND, "文献不存在")
            doc = project.doc
            doc["event_sequence"] = int(doc.get("event_sequence", 0)) + 1
            stamp = now()
            event = {
                "sequence": doc["event_sequence"],
                "paper_id": canonical,
                "value": value,
                "exclusion_reason": exclusion_reason,
                "reason": reason or "",
                "updated_at": stamp,
            }
            doc.setdefault("judgement_events", []).append(event)

            if value == JudgementValue.UNJUDGED.value:
                doc["judgements"].pop(canonical, None)
            else:
                doc["judgements"][canonical] = {
                    "paper_id": canonical,
                    "value": value,
                    "exclusion_reason": exclusion_reason,
                    "reason": reason or "",
                    "updated_at": stamp,
                    "event_sequence": doc["event_sequence"],
                }
            project.doc["revision"] = project.revision + 1
            project.doc["context_version"] = project.context_version + 1
            project.doc["updated_at"] = stamp
            self._verify_integrity(project.doc)
            self._write(project)
            return {
                "revision": project.revision,
                "context_version": project.context_version,
                "judgement": doc["judgements"].get(canonical, {
                    "paper_id": canonical,
                    "value": JudgementValue.UNJUDGED.value,
                    "exclusion_reason": ExclusionReason.UNSPECIFIED.value,
                    "reason": "",
                    "updated_at": stamp,
                    "event_sequence": doc["event_sequence"],
                }),
            }

    def judgement_dtos(self, project: V32Project) -> list[dict[str, Any]]:
        with project.lock:
            return [dict(j) for j in project.doc["judgements"].values()]

    def excluded_ids(self, project: V32Project) -> set[str]:
        with project.lock:
            return {
                pid
                for pid, judgement in project.doc["judgements"].items()
                if judgement["value"] == JudgementValue.EXCLUDED.value
            }

    # -- research anchor ---------------------------------------------------

    def set_anchor(self, project: V32Project, anchor: dict[str, Any], *, seeds: Iterable[str] = ()) -> None:
        """Replace the structured intent.  The raw user text is never overwritten."""
        with project.lock:
            previous_original = project.doc["anchor"].get("original_text")
            merged = dict(anchor)
            if merged.get("original_text") in (None, "") and previous_original:
                merged["original_text"] = previous_original
            merged["seed_paper_ids"] = [self.resolve_paper_id(project, s) or s for s in seeds if s]
            project.doc["anchor"] = merged
            project.doc["context_version"] = project.context_version + 1
            project.doc["updated_at"] = now()
            self._write(project)

    # -- snapshots ---------------------------------------------------------

    def snapshot_path(self, project: V32Project, snapshot_id: str) -> Path:
        return self.snapshots_dir / project.project_id / f"{snapshot_id}.json"

    def load_snapshot(self, project: V32Project, snapshot_id: str) -> dict[str, Any]:
        """Read a snapshot, current or historical.  Historical ones stay readable."""
        self.validate_id(snapshot_id, what="排名快照")
        path = self.snapshot_path(project, snapshot_id)
        if not path.is_file():
            raise V32Error(404, NOT_FOUND, "排名快照不存在")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise V32Error(404, NOT_FOUND, "排名快照不存在") from None

    def commit_snapshot(self, project: V32Project, snapshot: dict[str, Any], *, frozen: dict[str, Any]) -> str:
        """Commit a ranking snapshot, or refuse when it has been superseded.

        "提交时在项目锁/事务内逐项比较当前值；任一不一致，结果为 superseded，
        不覆盖当前快照" (doc 14).  Committing a snapshot does **not** bump
        ``revision`` — it is a snapshot, not a content edit.
        """
        snapshot_id = str(snapshot["snapshot_id"])
        self.validate_id(snapshot_id, what="排名快照")
        with project.lock:
            if (
                int(frozen["context_version"]) != project.context_version
                or int(frozen["corpus_version"]) != project.corpus_version
                or frozen.get("config_hash") != project.doc.get("config_hash")
                or frozen.get("model_revision") != project.doc.get("model_revision")
            ):
                return "superseded"
            path = self.snapshot_path(project, snapshot_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_json(path, snapshot)
            summary = {
                "snapshot_id": snapshot_id,
                "parent_snapshot_id": snapshot.get("parent_snapshot_id"),
                "ranking_stage": snapshot.get("ranking_stage"),
                "as_of_date": snapshot.get("as_of_date"),
                "context_version": snapshot.get("context_version"),
                "corpus_version": snapshot.get("corpus_version"),
                "item_count": len(snapshot.get("items", [])),
                "committed_at": now(),
            }
            project.doc.setdefault("snapshots", {})[snapshot_id] = summary
            project.doc["current_snapshot_id"] = snapshot_id
            project.doc["updated_at"] = now()
            self._write(project)
            return "committed"

    def set_config_hash(self, project: V32Project, config_hash: str, model_revision: str | None) -> None:
        """Record the ranking configuration identity used by later computations."""
        with project.lock:
            if project.doc.get("config_hash") != config_hash or project.doc.get("model_revision") != model_revision:
                project.doc["config_hash"] = config_hash
                project.doc["model_revision"] = model_revision
                project.doc["context_version"] = project.context_version + 1
                self._write(project)

    # -- imports and attachments ------------------------------------------

    def add_attachment(self, project: V32Project, payload: bytes, display_name: str) -> dict[str, Any]:
        """Store file bytes keyed by content hash; identical bytes reuse the row.

        Content de-duplication is separate from command idempotency (doc 14),
        and it is scoped to the project so the existence of a file is never
        revealed across users.
        """
        digest = content_hash(payload)
        with project.lock:
            attachments = project.doc["attachments"]
            existing = attachments.get(digest)
            if existing is not None:
                return dict(existing)
            attachment_id = uuid.uuid4().hex
            target = self.files_dir / f"{attachment_id}.bin"
            tmp = target.with_suffix(".bin.tmp")
            tmp.write_bytes(payload)
            os.replace(tmp, target)
            row = {
                "attachment_id": attachment_id,
                "project_id": project.project_id,
                "content_hash": digest,
                "display_name": _safe_display_name(display_name),
                "size": len(payload),
                "created_at": now(),
                "item_refs": [],
            }
            attachments[digest] = row
            return dict(row)

    def attachment_bytes(self, project: V32Project, attachment_id: str) -> bytes:
        self.validate_id(attachment_id, what="附件")
        with project.lock:
            row = next(
                (a for a in project.doc["attachments"].values() if a["attachment_id"] == attachment_id),
                None,
            )
        if row is None:
            raise V32Error(404, NOT_FOUND, "附件不存在")
        path = self.files_dir / f"{attachment_id}.bin"
        if not path.is_file():
            raise V32Error(404, NOT_FOUND, "附件不存在")
        return path.read_bytes()

    # -- idempotency -------------------------------------------------------

    @staticmethod
    def _idempotency_key(owner_id: str, project_id: str, endpoint: str, key: str) -> str:
        return hashlib.sha256(f"{owner_id}|{project_id}|{endpoint}|{key}".encode()).hexdigest()

    def idempotency_lookup(
        self, *, owner_id: str, project_id: str, endpoint: str, key: str, body_hash: str
    ) -> dict[str, Any] | None:
        """Return the stored response for a repeated command, or ``None``.

        Same key with a *different* body is a contract violation, not a retry:
        it returns :data:`IDEMPOTENCY_CONFLICT` (doc 14/08).
        """
        path = self.idempotency_dir / f"{self._idempotency_key(owner_id, project_id, endpoint, key)}.json"
        if not path.is_file():
            return None
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if record.get("body_hash") != body_hash:
            raise V32Error(409, IDEMPOTENCY_CONFLICT, "同一幂等键对应了不同的请求内容")
        if _expired(record.get("stored_at"), job_defaults()["idempotency_ttl_seconds"]):
            path.unlink(missing_ok=True)
            return None
        return dict(record.get("response") or {})

    def idempotency_store(
        self,
        *,
        owner_id: str,
        project_id: str,
        endpoint: str,
        key: str,
        body_hash: str,
        response: dict[str, Any],
    ) -> None:
        path = self.idempotency_dir / f"{self._idempotency_key(owner_id, project_id, endpoint, key)}.json"
        atomic_json(
            path,
            {
                "owner_id": owner_id,
                "project_id": project_id,
                "endpoint": endpoint,
                "body_hash": body_hash,
                "response": response,
                "stored_at": now(),
            },
        )

    @staticmethod
    def body_hash(payload: Any) -> str:
        """Canonical hash of a normalised request body (order-independent)."""
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _expired(stored_at: str | None, ttl_seconds: int) -> bool:
    if not stored_at:
        return True
    try:
        stamp = datetime.fromisoformat(stored_at)
    except (TypeError, ValueError):
        return True
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - stamp > timedelta(seconds=ttl_seconds)


def _safe_display_name(name: str) -> str:
    """A display name is never a path.  Strip separators and bound the length."""
    text = (name or "").replace("\\", "/").split("/")[-1]
    text = re.sub(r"[\x00-\x1f\x7f]", "", text).strip() or "document.pdf"
    return text[:180]


def _empty_anchor(entry_mode: str) -> dict[str, Any]:
    return {
        "entry_mode": entry_mode,
        "original_text": "",
        "objects": [],
        "tasks": [],
        "methods": [],
        "constraints": [],
        "exclusions": [],
        "query_variants": [],
        "seed_paper_ids": [],
        "context_version": 1,
        "initial_vector_cache_key": None,
    }


def _new_paper_record(paper: dict[str, Any], paper_id: str) -> dict[str, Any]:
    return {
        "paper_id": paper_id,
        "title": paper.get("title") or "",
        "authors": list(paper.get("authors") or []),
        "year": paper.get("year"),
        "abstract": paper.get("abstract"),
        "doi": paper.get("doi"),
        "aliases": sorted({str(a) for a in paper.get("aliases") or [] if a}),
        "citation_count": paper.get("citation_count"),
        "source_records": list(paper.get("source_records") or []),
        "linked_version_ids": list(paper.get("linked_version_ids") or []),
        "created_at": now(),
        # v3.2 additions carried on the record but never on the shared Paper DTO
        "venue": paper.get("venue"),
        "doc_type": paper.get("doc_type"),
        "landing_url": paper.get("landing_url"),
        "reference_ids": list(paper.get("reference_ids") or []),
    }


def _new_project_paper(project_id: str, paper_id: str) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "paper_id": paper_id,
        "judgement": JudgementValue.UNJUDGED.value,
        "exclusion_reason": ExclusionReason.UNSPECIFIED.value,
        "seed_origin": None,
        "attachment_ids": [],
        "current_snapshot_id": None,
    }


def _enrich_paper(record: dict[str, Any], paper: dict[str, Any]) -> None:
    """Fill missing bibliographic facts; never let the latest response win silently.

    A conflicting value is recorded as an extra source record rather than
    overwriting the first observation (doc 08: "冲突记录不能被最后响应静默覆盖").
    """
    for field_name in ("title", "abstract"):
        if not record.get(field_name) and paper.get(field_name):
            record[field_name] = paper[field_name]
    if not record.get("authors") and paper.get("authors"):
        record["authors"] = list(paper["authors"])
    if record.get("year") is None and paper.get("year") is not None:
        record["year"] = paper["year"]
    if not record.get("doi") and paper.get("doi"):
        record["doi"] = paper["doi"]
    elif record.get("doi") and paper.get("doi") and record["doi"] != paper["doi"]:
        record.setdefault("conflicts", []).append({"field": "doi", "kept": record["doi"], "seen": paper["doi"]})
    if record.get("citation_count") is None and paper.get("citation_count") is not None:
        record["citation_count"] = paper["citation_count"]
    record["aliases"] = sorted(set(record.get("aliases", [])) | {str(a) for a in paper.get("aliases") or [] if a})
    record["source_records"] = _dedupe_records(list(record.get("source_records", [])) + list(paper.get("source_records") or []))
    if not record.get("landing_url") and paper.get("landing_url"):
        record["landing_url"] = paper["landing_url"]
    if not record.get("venue") and paper.get("venue"):
        record["venue"] = paper["venue"]


def _dedupe_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple] = set()
    unique: list[dict[str, Any]] = []
    for record in records:
        key = (record.get("provider"), record.get("external_id"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(record)
    return unique


# ---------------------------------------------------------------------------
# migration chain (schema_version N -> N+1)
# ---------------------------------------------------------------------------


def _migrate_0_to_1(legacy: dict[str, Any]) -> dict[str, Any]:
    """Adopt a legacy session/project document into the v3.2 project schema.

    Legacy ``relevance_score`` keeps its original meaning and is **not** copied
    into ``core_score``; every ``judgement`` starts empty and the new ranking is
    recomputed, exactly as doc 14 requires.
    """
    stamp = now()
    project_id = str(legacy.get("project_id") or uuid.uuid4().hex)
    owner_id = str(legacy.get("owner_id") or legacy.get("session_id") or uuid.uuid4().hex)
    papers: dict[str, Any] = {}
    aliases: dict[str, str] = {}
    for raw in legacy.get("papers") or legacy.get("records") or []:
        if not isinstance(raw, dict):
            continue
        paper_id = str(raw.get("paper_id") or raw.get("canonical_id") or uuid.uuid4().hex)
        papers[paper_id] = _new_paper_record(raw, paper_id)
        for alias in list(raw.get("aliases") or []) + ([raw["doi"]] if raw.get("doi") else []):
            if alias:
                aliases[str(alias)] = paper_id
    return {
        "schema_version": 1,
        "contract_version": CONTRACT_VERSION,
        "project_id": project_id,
        "owner_id": owner_id,
        "name": str(legacy.get("name") or legacy.get("topic") or "未命名项目"),
        "entry_mode": str(legacy.get("entry_mode") or "topic"),
        "revision": int(legacy.get("revision") or 1),
        "context_version": 1,
        "corpus_version": 1,
        "model_revision": None,
        "current_snapshot_id": None,
        "active_job_ids": [],
        "created_at": str(legacy.get("created_at") or stamp),
        "updated_at": stamp,
        "anchor": _empty_anchor(str(legacy.get("entry_mode") or "topic")),
        "papers": papers,
        "project_papers": {pid: _new_project_paper(project_id, pid) for pid in papers},
        "aliases": aliases,
        "redirects": {},
        # Legacy judgements are intentionally empty; relevance_score is retained
        # on the record with its original meaning and is never re-labelled.
        "judgements": {},
        "judgement_events": [],
        "event_sequence": 0,
        "edges": {},
        "snapshots": {},
        "imports": {},
        "attachments": {},
        "seeds": [],
        "zotero_map": {},
        "prisma": dict(legacy.get("prisma") or {}),
    }


_MIGRATIONS = {0: _migrate_0_to_1}
