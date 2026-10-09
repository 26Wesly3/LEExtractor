"""Atomic, project-scoped storage for the single-user local web application."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from litsearch.persistence import state_from_dict, state_to_dict
from litsearch.search import ReviewState
from litsearch.session_schema import validate_session_dict


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class APIError(Exception):
    def __init__(self, status: int, code: str, message: str, detail=None):
        self.status, self.code, self.message, self.detail = status, code, message, detail
        super().__init__(message)


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".write-", delete=False) as handle:
            name = handle.name
            json.dump(data, handle, ensure_ascii=False, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


@dataclass
class Project:
    project_id: str
    name: str
    state: ReviewState
    revision: int = 1
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)
    demo: bool = False
    active_job_id: str | None = None
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)


class ProjectRepository:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.projects_dir = self.root / "projects"
        self.jobs_dir = self.root / "jobs"
        self.files_dir = self.root / "files"
        for directory in (self.projects_dir, self.jobs_dir, self.files_dir):
            directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.projects: dict[str, Project] = {}
        self.load_errors = 0
        for path in self.projects_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                pid = data["project_id"]
                self.validate_id(pid)
                state = state_from_dict(validate_session_dict(data["state"]))
                self.projects[pid] = Project(pid, str(data["name"]), state,
                                             int(data["revision"]), data["created_at"],
                                             data["updated_at"], bool(data.get("demo")))
            except (KeyError, ValueError, TypeError, OSError, APIError):
                # A corrupt project is preserved on disk; do not silently replace it.
                self.load_errors += 1

    @staticmethod
    def validate_id(value: str) -> None:
        if not re.fullmatch(r"[a-f0-9]{32}", value):
            raise APIError(404, "not_found", "项目或任务不存在")

    def get(self, pid: str) -> Project:
        self.validate_id(pid)
        with self.lock:
            if pid not in self.projects:
                raise APIError(404, "not_found", "项目不存在")
            return self.projects[pid]

    def create(self, name: str, state: ReviewState, demo=False) -> Project:
        project = Project(uuid.uuid4().hex, name or state.topic or "未命名项目", state, demo=demo)
        with self.lock:
            self.projects[project.project_id] = project
            self._write(project)
        return project

    def _write(self, project: Project) -> None:
        atomic_json(self.projects_dir / f"{project.project_id}.json", {
            "project_id": project.project_id, "name": project.name,
            "revision": project.revision, "created_at": project.created_at,
            "updated_at": project.updated_at, "demo": project.demo,
            "state": state_to_dict(project.state),
        })

    def check_revision(self, project: Project, revision: int | None) -> None:
        if revision is None:
            raise APIError(409, "revision_required", "请提供当前项目 revision，避免覆盖其他页面的修改")
        path = self.projects_dir / f"{project.project_id}.json"
        try:
            disk_revision = json.loads(path.read_text(encoding="utf-8"))["revision"]
        except (OSError, ValueError, KeyError, TypeError):
            raise APIError(409, "storage_conflict", "磁盘项目已改变或不可读取") from None
        if revision != project.revision or disk_revision != project.revision:
            raise APIError(409, "revision_conflict", "项目已更新，请刷新后重试",
                           {"current_revision": project.revision})

    def require_idle(self, project: Project) -> None:
        if project.active_job_id:
            raise APIError(409, "project_busy", "该项目已有运行中的任务",
                           {"job_id": project.active_job_id})

    def save(self, project: Project, state=None) -> None:
        # Callers hold the project lock. Jobs checkpoint complete snapshots,
        # never expose a state object concurrently mutated by an algorithm.
        self.check_revision(project, project.revision)
        if state is not None:
            project.state = state_from_dict(state_to_dict(state))
        project.revision += 1
        project.updated_at = now()
        self._write(project)

    def add_file(self, project_id: str, payload: bytes, filename: str, media_type: str) -> dict:
        file_id = uuid.uuid4().hex
        (self.files_dir / f"{file_id}.bin").write_bytes(payload)
        meta = {"file_id": file_id, "project_id": project_id,
                "filename": filename, "media_type": media_type, "size": len(payload)}
        atomic_json(self.files_dir / f"{file_id}.json", meta)
        return {**meta, "download_url": f"/api/files/{file_id}"}

    def file(self, file_id: str) -> tuple[Path, dict]:
        self.validate_id(file_id)
        try:
            meta = json.loads((self.files_dir / f"{file_id}.json").read_text(encoding="utf-8"))
            path = self.files_dir / f"{file_id}.bin"
            if not path.is_file():
                raise FileNotFoundError
            # Filenames never determine the physical path.
            return path, meta
        except (OSError, ValueError):
            raise APIError(404, "file_not_found", "下载文件不存在") from None
