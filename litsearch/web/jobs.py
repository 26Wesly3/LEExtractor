"""Persisted jobs with observable progress and real cooperative cancellation."""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, field
from types import MethodType

from litsearch.persistence import state_from_dict, state_to_dict
from litsearch.sources import RetrievalBudgetExceeded, RetrievalCanceled
from litsearch.stop_reasons import http_budget_snapshot, reset_http_budget
from litsearch.web.dto import public
from litsearch.web.repository import APIError, atomic_json, now

# Existing domain HTTP accounting is process-global. Serialize all retrieval
# jobs, including jobs from separate create_app instances, rather than mixing
# two projects' requests/cache hits/stop reasons into one budget snapshot.
RETRIEVAL_LOCK = threading.Lock()
TERMINAL = {"completed", "partial", "failed", "canceled"}
PARTIAL_REASONS = {"low_yield", "max_rounds", "truncated", "budget_exhausted", "api_failure"}


class BudgetExceeded(RetrievalBudgetExceeded):
    pass


class RequestGuard:
    def __init__(self, event: threading.Event, limit: int):
        self.event, self.limit, self.issued, self.exhausted = event, limit, 0, False

    def check(self):
        if self.event.is_set():
            raise RetrievalCanceled("User canceled local web job")
        if self.issued >= self.limit:
            self.exhausted = True
            raise BudgetExceeded("Job HTTP budget exhausted")
        self.issued += 1

    def bind(self, sources):
        hook = getattr(sources, "set_cancel_check", None)
        if hook:
            hook(self.event.is_set)
        for name in ("s2", "oa", "cr", "arxiv"):
            provider = getattr(sources, name, None)
            if provider is None:
                continue
            provider.set_request_budget(self.limit)
            transport = provider._transport_request

            def guarded(_source, method, url, _transport=transport, **kwargs):
                self.check()
                return _transport(method, url, **kwargs)

            provider._transport_request = MethodType(guarded, provider)


@dataclass
class Job:
    payload: dict
    event: threading.Event = field(default_factory=threading.Event)
    lock: threading.RLock = field(default_factory=threading.RLock)
    thread: threading.Thread | None = None


class JobManager:
    def __init__(self, repository, workflow_factory):
        self.repo, self.factory = repository, workflow_factory
        self.jobs: dict[str, Job] = {}
        self.lock = threading.RLock()
        for path in self.repo.jobs_dir.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                self.repo.validate_id(payload["job_id"])
                if payload.get("status") not in TERMINAL:
                    payload.update(status="partial", stop_reason="interrupted",
                                   finished_at=now(), cancel_requested=False,
                                   error={"code": "interrupted", "message": "服务重启，任务已中断；保留已完成检查点"})
                    atomic_json(path, payload)
                self.jobs[payload["job_id"]] = Job(payload)
            except (OSError, ValueError, KeyError, TypeError, APIError):
                continue

    def dto(self, job):
        with job.lock:
            return public(dict(job.payload))

    def get(self, job_id):
        self.repo.validate_id(job_id)
        with self.lock:
            if job_id not in self.jobs:
                raise APIError(404, "job_not_found", "任务不存在")
            return self.jobs[job_id]

    def update(self, job, **fields):
        with job.lock:
            job.payload.update(public(fields))
            atomic_json(self.repo.jobs_dir / f"{job.payload['job_id']}.json", job.payload)

    def submit(self, project, kind, revision, operation, http_limit=200):
        with project.lock:
            self.repo.require_idle(project)
            self.repo.check_revision(project, revision)
            state = state_from_dict(state_to_dict(project.state))
            payload = {"job_id": uuid.uuid4().hex, "project_id": project.project_id,
                       "kind": kind, "status": "queued", "stop_reason": "",
                       "http_budget": {}, "progress": {"stage": "queued"},
                       "result": None, "error": None, "created_at": now(),
                       "started_at": None, "finished_at": None, "cancel_requested": False}
            job = Job(payload)
            project.active_job_id = payload["job_id"]
            with self.lock:
                self.jobs[payload["job_id"]] = job
            self.update(job)
            job.thread = threading.Thread(target=self._run,
                                          args=(job, project, state, operation, http_limit), daemon=True)
            job.thread.start()
        return job

    def _run(self, job, project, state, operation, http_limit):
        acquired = False
        guard = RequestGuard(job.event, http_limit)
        try:
            while not job.event.is_set():
                if RETRIEVAL_LOCK.acquire(timeout=0.1):
                    acquired = True
                    break
            if job.event.is_set():
                raise RetrievalCanceled("Canceled while queued")
            self.update(job, status="running", started_at=now(), progress={"stage": job.payload["kind"]})
            reset_http_budget()
            workflow = self.factory(project.demo)
            guard.bind(workflow.sources)

            def checkpoint(progress):
                state.http_budget = {**http_budget_snapshot(), "limit": http_limit,
                                     "transport_attempts": guard.issued}
                with project.lock:
                    self.repo.save(project, state)
                self.update(job, progress=progress, http_budget=state.http_budget)

            result, replacement = operation(workflow, state, guard, checkpoint)
            if replacement is not None:
                state = replacement
            if job.event.is_set():
                reason, status = "canceled", "canceled"
            elif guard.exhausted:
                reason, status = "budget_exhausted", "partial"
            else:
                reason = state.stop_reason or ""
                status = "partial" if reason in PARTIAL_REASONS else "completed"
            state.stop_reason = reason
            state.http_budget = {**http_budget_snapshot(), "limit": http_limit,
                                 "transport_attempts": guard.issued}
            with project.lock:
                self.repo.save(project, state)
                project.active_job_id = None
            self.update(job, status=status, stop_reason=reason, http_budget=state.http_budget,
                        result=result, progress={"stage": "finished", "records": project.state.total_papers_tracked},
                        finished_at=now())
        except Exception:
            canceled = job.event.is_set()
            reason = "canceled" if canceled else "budget_exhausted" if guard.exhausted else "api_failure"
            state.stop_reason = reason
            state.http_budget = {**http_budget_snapshot(), "limit": http_limit,
                                 "transport_attempts": guard.issued} if acquired else {"limit": http_limit, "requests": 0}
            try:
                with project.lock:
                    self.repo.save(project, state)
            except APIError:
                # Another process edited the project; preserve its disk file.
                reason = "storage_conflict"
            with project.lock:
                project.active_job_id = None
            self.update(job, status="canceled" if canceled else "partial" if guard.exhausted else "failed",
                        stop_reason=reason, http_budget=state.http_budget, finished_at=now(),
                        error={"code": reason, "message": "任务已取消" if canceled else "任务未完整结束；已保存当前检查点"})
        finally:
            with project.lock:
                project.active_job_id = None
            if acquired:
                RETRIEVAL_LOCK.release()

    def cancel(self, job):
        with job.lock:
            if job.payload["status"] not in TERMINAL:
                job.event.set()
                self.update(job, cancel_requested=True)
        return self.dto(job)

    def shutdown(self):
        with self.lock:
            pending = list(self.jobs.values())
        for job in pending:
            if job.payload["status"] not in TERMINAL:
                self.cancel(job)
        for job in pending:
            if job.thread and job.thread.is_alive():
                job.thread.join(timeout=0.1)
