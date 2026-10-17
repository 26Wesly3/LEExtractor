"""The v3.2 job state machine and bounded scheduler.

Two rules dominate this module, and both are easy to get subtly wrong:

**Terminal states are immutable.**  The legal edges live in
``contracts/job_states.json`` and are enforced by
:func:`litsearch.v32.contracts.assert_transition` rather than by an ad-hoc
``if`` chain, so a state can never be rewritten once it is terminal.

**A stale computation must never overwrite a newer one.**  Jobs carry the
``context_version``/``corpus_version`` they were started with, a newer judgement
invalidates a queued rerank, and the completion path is guarded by
:meth:`V32Store.commit_snapshot`'s supersede check (doc 14, AC-09).

The same file also implements the resource budget from doc 15: one heavy worker,
a queue ceiling of 20 (over which the API answers 429 with a retry time), and
project round-robin scheduling so one user cannot monopolise the machine.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from litsearch.jsonio import atomic_json

from .contracts import assert_transition, initial_state, job_defaults, terminal_states
from .errors import NOT_FOUND, RATE_LIMITED, V32Error
from .store import V32Project, V32Store, now
from .types import JobStage, JobState

__all__ = ["JobRecord", "JobManager", "JobHandler", "checkpoint_summary"]

#: A job handler receives the job and the project and returns a result dict.
JobHandler = Callable[[V32Project, "JobRecord"], dict[str, Any]]


def checkpoint_summary(checkpoint: dict[str, Any] | None) -> dict[str, Any]:
    """A redacted view of a job checkpoint, safe to put in an HTTP response.

    Doc 14: a checkpoint stores only a reproducible cursor, already-committed
    ids, the budget and the input versions — never a key.  The redaction here is
    defence in depth, so a handler that mistakenly stores something extra cannot
    get it onto the wire.
    """
    checkpoint = checkpoint or {}
    allowed = ("cursor", "committed_ids", "budget", "input_versions", "provider_cursor", "stop_reason")
    return {key: checkpoint[key] for key in allowed if key in checkpoint}


@dataclass
class JobRecord:
    """One persisted job."""

    doc: dict[str, Any]
    event: threading.Event = field(default_factory=threading.Event, repr=False)
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    thread: threading.Thread | None = field(default=None, repr=False)
    #: The submitting session, held in memory only (never written to the job
    #: file).  Credentials are resolved from it when the job runs (doc 10:
    #: "任务只保存凭据引用，在执行时解析").
    session_ref: str | None = field(default=None, repr=False)

    # -- accessors ---------------------------------------------------------

    @property
    def job_id(self) -> str:
        return str(self.doc["job_id"])

    @property
    def project_id(self) -> str:
        return str(self.doc["project_id"])

    @property
    def state(self) -> str:
        return str(self.doc["state"])

    @property
    def stage(self) -> str:
        return str(self.doc["stage"])

    @property
    def is_terminal(self) -> bool:
        return self.state in terminal_states()

    @property
    def cancel_requested(self) -> bool:
        return bool(self.doc.get("cancel_requested"))

    def covers(self, *, context_version: int, corpus_version: int) -> bool:
        """True when this job was computed against exactly these versions."""
        return (
            int(self.doc.get("context_version", -1)) == int(context_version)
            and int(self.doc.get("corpus_version", -1)) == int(corpus_version)
        )


class JobManager:
    """Bounded, owner-scoped job scheduler with a contract-driven state machine."""

    def __init__(
        self,
        store: V32Store,
        *,
        max_queue: int | None = None,
        max_workers: int | None = None,
        autostart: bool = True,
    ):
        defaults = job_defaults()
        self.store = store
        self.jobs_dir = store.root / "jobs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.max_queue = int(defaults["max_queue"] if max_queue is None else max_queue)
        self.max_workers = int(defaults["max_heavy_workers"] if max_workers is None else max_workers)
        self.debounce_ms = int(defaults["feedback_debounce_ms"])
        self.jobs: dict[str, JobRecord] = {}
        self.handlers: dict[str, JobHandler] = {}
        self.lock = threading.RLock()
        self._wake = threading.Event()
        self._stopping = False
        self._dispatch_thread: threading.Thread | None = None
        #: One "latest pending rerank" slot per project, so a burst of
        #: judgements collapses into a single recomputation (doc 14).
        self._pending_rerank: dict[str, str] = {}
        self._running: dict[str, int] = {}
        self._last_project: str | None = None
        self._load()
        if autostart:
            self.start()

    # -- persistence -------------------------------------------------------

    def _load(self) -> None:
        for path in sorted(self.jobs_dir.glob("*.json")):
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(doc, dict) or "job_id" not in doc:
                    continue
            except (OSError, ValueError):
                continue
            job = JobRecord(doc)
            if not job.is_terminal:
                # A process that died mid-run leaves running/cancel_requested
                # behind; label it interrupted rather than an eternal "running"
                # (doc 14, AC-11).
                doc.update(
                    state=JobState.INTERRUPTED.value,
                    stage=JobStage.FINISHED.value,
                    updated_at=now(),
                    finished_at=now(),
                    error_codes=sorted(set(doc.get("error_codes", [])) | {"interrupted"}),
                )
                self._write(job)
            self.jobs[job.job_id] = job

    def _write(self, job: JobRecord) -> None:
        with job.lock:
            atomic_json(self.jobs_dir / f"{job.job_id}.json", job.doc)

    # -- state machine -----------------------------------------------------

    def transition(self, job: JobRecord, target: str, **fields: Any) -> None:
        """Move ``job`` to ``target``, refusing an illegal or terminal rewrite."""
        with job.lock:
            current = job.state
            if current == target:
                if fields:
                    job.doc.update(fields)
                    job.doc["updated_at"] = now()
                    self._write(job)
                return
            assert_transition(current, target)
            job.doc.update(fields)
            job.doc["state"] = target
            job.doc["updated_at"] = now()
            if target in terminal_states():
                job.doc["finished_at"] = now()
        self._write(job)

    def set_stage(self, job: JobRecord, stage: str, *, progress: float | None = None, **fields: Any) -> None:
        """Advance the ``stage`` field.  ``finished`` never implies success."""
        self.transition(
            job, job.state, stage=stage,
            progress=(progress if progress is not None else job.doc.get("progress")),
            **fields,
        )

    # -- submission --------------------------------------------------------

    def _require_capacity(self, project_id: str) -> None:
        with self.lock:
            if len(self._active()) >= self.max_queue:
                raise V32Error(
                    429, RATE_LIMITED, "任务队列已满，请稍后重试",
                    {"retry_after_seconds": 5},
                )

    def _active(self) -> list[JobRecord]:
        return [job for job in self.jobs.values() if not job.is_terminal]

    def submit(
        self,
        project: V32Project,
        kind: str,
        *,
        stage: str = JobStage.QUEUED.value,
        parent_job_id: str | None = None,
        checkpoint: dict[str, Any] | None = None,
        priority: bool = False,
        submitted_revision: int | None = None,
        session_ref: str | None = None,
        **extra: Any,
    ) -> JobRecord:
        """Create a queued job.  The caller has already validated the revision.

        ``parent_job_id`` is set when a job resumes an interrupted one, so the
        chain of a recovered computation stays visible (doc 14).
        """
        self._require_capacity(project.project_id)
        with project.lock:
            job = JobRecord({
                "job_id": uuid.uuid4().hex,
                "project_id": project.project_id,
                "owner_id": project.owner_id,
                "parent_job_id": parent_job_id,
                "kind": kind,
                "state": initial_state(),
                "stage": stage,
                "progress": 0.0,
                "context_version": project.context_version,
                "corpus_version": project.corpus_version,
                "submitted_revision": project.revision if submitted_revision is None else int(submitted_revision),
                "created_at": now(),
                "updated_at": now(),
                "started_at": None,
                "finished_at": None,
                "cancel_requested": False,
                "snapshot_id": None,
                "error_codes": [],
                "coverage": None,
                "result": None,
                "checkpoint": checkpoint or {},
                "priority": bool(priority),
                **extra,
            })
            job.session_ref = str(session_ref) if session_ref else None
        with self.lock:
            self.jobs[job.job_id] = job
            project.active_job_ids.append(job.job_id)
        self._write(job)
        self._wake.set()
        return job

    def schedule_rerank(
        self,
        project: V32Project,
        *,
        debounce_ms: int | None = None,
        session_ref: str | None = None,
        mode: str | None = None,
    ) -> JobRecord:
        """Collapse a burst of judgements into one pending rerank (doc 14).

        The newest request supersedes the previous *pending* one; a job that has
        already started is left to finish and will be discarded by the supersede
        check if the context moved under it.
        """
        delay = self.debounce_ms if debounce_ms is None else int(debounce_ms)
        with self.lock:
            previous_id = self._pending_rerank.get(project.project_id)
        if previous_id:
            previous = self.jobs.get(previous_id)
            if previous is not None and previous.state == JobState.QUEUED.value and not previous.thread:
                # Never-started work is simply cancelled; nothing was committed.
                self.transition(previous, JobState.SUPERSEDED.value,
                                error_codes=sorted(set(previous.doc["error_codes"]) | {"superseded_by_newer_context"}))
                self._detach(previous)
        job = self.submit(
            project, "rerank",
            debounce_until=time.monotonic() + delay / 1000.0,
            priority=True,
            session_ref=session_ref,
            mode=mode or "basic",
        )
        with self.lock:
            self._pending_rerank[project.project_id] = job.job_id
        return job

    # -- lookup / cancellation --------------------------------------------

    def get(self, project: V32Project, job_id: str) -> JobRecord:
        self.store.validate_id(job_id, what="任务")
        with self.lock:
            job = self.jobs.get(job_id)
        if job is None or job.project_id != project.project_id:
            # A job from another project is reported as absent (AC-25).
            raise V32Error(404, NOT_FOUND, "任务不存在")
        return job

    def cancel(self, project: V32Project, job_id: str) -> JobRecord:
        """Request cancellation; an already-terminal job returns its state."""
        job = self.get(project, job_id)
        with job.lock:
            if job.is_terminal:
                return job
            job.event.set()
            job.doc["cancel_requested"] = True
            job.doc["updated_at"] = now()
        if job.state == JobState.QUEUED.value:
            self.transition(job, JobState.CANCELLED.value, error_codes=["cancel_requested"])
            self._detach(job)
        else:
            self.transition(job, JobState.CANCEL_REQUESTED.value)
        return job

    def _detach(self, job: JobRecord) -> None:
        with self.lock:
            self._pending_rerank = {k: v for k, v in self._pending_rerank.items() if v != job.job_id}
        for project in self.store.projects.values():
            if job.job_id in project.active_job_ids:
                project.active_job_ids.remove(job.job_id)

    # -- execution ---------------------------------------------------------

    def register(self, kind: str, handler: JobHandler) -> None:
        self.handlers[kind] = handler

    def start(self, *, background: bool = True) -> None:
        if self._dispatch_thread is not None:
            return
        if background:
            self._dispatch_thread = threading.Thread(target=self._dispatch_loop, daemon=True)
            self._dispatch_thread.start()

    def _dispatch_loop(self) -> None:
        while not self._stopping:
            self._wake.wait(timeout=0.25)
            self._wake.clear()
            try:
                self.drain()
            except Exception:  # pragma: no cover - the loop must survive anything
                time.sleep(0.1)

    def drain(self, *, max_jobs: int = 100) -> int:
        """Run eligible queued jobs.  Synchronous, so tests need no sleeping."""
        executed = 0
        for _ in range(max_jobs):
            job = self._next_job()
            if job is None:
                break
            self._run(job)
            executed += 1
        return executed

    def _next_job(self) -> JobRecord | None:
        """Project round-robin selection (doc 15: 项目间轮询).

        The rotation key is the project served *last*, not just the currently
        running count: jobs are executed one at a time, so a running-count-only
        rule degenerates to "oldest job wins" and lets one project's backlog
        starve every other project.
        """
        with self.lock:
            running_total = sum(self._running.values())
            if running_total >= self.max_workers:
                return None
            candidates = [
                job for job in self.jobs.values()
                if job.state == JobState.QUEUED.value
                and not job.thread
                and self._debounce_elapsed(job)
            ]
            if not candidates:
                return None
            last = self._last_project
            candidates.sort(key=lambda j: (
                self._running.get(j.project_id, 0),
                1 if (last is not None and j.project_id == last) else 0,
                0 if j.doc.get("priority") else 1,
                j.doc["created_at"],
                j.job_id,
            ))
            best = candidates[0]
            self._running[best.project_id] = self._running.get(best.project_id, 0) + 1
            self._last_project = best.project_id
            best.thread = threading.current_thread()
            return best

    @staticmethod
    def _debounce_elapsed(job: JobRecord) -> bool:
        until = job.doc.get("debounce_until")
        if until is None:
            return True
        if time.monotonic() < float(until):
            return False
        job.doc["debounce_until"] = None
        return True

    def _run(self, job: JobRecord) -> None:
        try:
            project = self._project_for(job)
            if project is None:
                self.transition(job, JobState.FAILED.value, error_codes=["project_missing"])
                return
            if job.event.is_set():
                self.transition(job, JobState.CANCELLED.value, error_codes=["cancel_requested"])
                return
            self.transition(job, JobState.RUNNING.value, started_at=now())
            handler = self.handlers.get(job.doc["kind"])
            if handler is None:
                self.transition(job, JobState.FAILED.value, error_codes=["no_handler"])
                return
            result = handler(project, job)
            self._finish(job, result or {})
        except Exception as exc:  # noqa: BLE001 - every failure becomes a job state
            self._fail(job, exc)
        finally:
            with self.lock:
                remaining = self._running.get(job.project_id, 1) - 1
                if remaining <= 0:
                    self._running.pop(job.project_id, None)
                else:
                    self._running[job.project_id] = remaining
            self._detach(job)

    def _project_for(self, job: JobRecord) -> V32Project | None:
        with self.store.lock:
            project = self.store.projects.get(job.project_id)
        if project is None or project.owner_id != job.doc.get("owner_id"):
            return None
        return project

    def _finish(self, job: JobRecord, result: dict[str, Any]) -> None:
        if job.event.is_set():
            self.transition(job, JobState.CANCELLED.value, result=result,
                            error_codes=sorted(set(job.doc["error_codes"]) | {"cancel_requested"}))
            return
        if result.get("superseded"):
            # A newer context won; the partial work is dropped, not merged.
            self.transition(job, JobState.SUPERSEDED.value, result=result,
                            snapshot_id=result.get("snapshot_id"),
                            error_codes=sorted(set(job.doc["error_codes"]) | {"superseded"}))
            return
        outcome = result.get("outcome")
        if outcome == "failed":
            self.transition(job, JobState.FAILED.value, result=result,
                            snapshot_id=result.get("snapshot_id"),
                            coverage=result.get("coverage"),
                            error_codes=sorted(set(job.doc["error_codes"]) | set(result.get("error_codes", []))))
        elif outcome == "partial":
            self.transition(job, JobState.PARTIAL.value, result=result,
                            snapshot_id=result.get("snapshot_id"),
                            coverage=result.get("coverage"),
                            error_codes=sorted(set(job.doc["error_codes"]) | set(result.get("error_codes", []))))
        else:
            # stage=finished is NOT the same claim as "everything succeeded";
            # the state carries that meaning (doc 14).
            self.transition(job, JobState.SUCCEEDED.value, stage=JobStage.FINISHED.value,
                            progress=1.0, result=result,
                            snapshot_id=result.get("snapshot_id"),
                            coverage=result.get("coverage"))

    def _fail(self, job: JobRecord, exc: Exception) -> None:
        code = getattr(exc, "code", None) or type(exc).__name__
        with job.lock:
            if job.is_terminal:
                return
            job.doc["error_codes"] = sorted(set(job.doc["error_codes"]) | {str(code)})
            job.doc["error_message"] = str(exc)[:500]
        # A failed job that was already asked to cancel must finish as
        # "cancelled": the contract gives cancel_requested no edge to "failed",
        # and inventing one would leave the job permanently non-terminal.
        cancelling = job.event.is_set() or job.state == JobState.CANCEL_REQUESTED.value
        target = JobState.CANCELLED.value if cancelling else JobState.FAILED.value
        try:
            self.transition(job, target, stage=JobStage.FINISHED.value)
        except V32Error:  # pragma: no cover - already terminal
            pass

    # -- shutdown ----------------------------------------------------------

    def shutdown(self, grace: float = 5.0) -> None:
        """Stop accepting work and never leave a job stuck on ``running``."""
        self._stopping = True
        self._wake.set()
        thread = self._dispatch_thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=grace)
        with self.lock:
            pending = list(self.jobs.values())
        for job in pending:
            if job.is_terminal:
                continue
            if job.state == JobState.QUEUED.value:
                self.transition(job, JobState.CANCELLED.value, error_codes=["shutdown"])
            else:
                with job.lock:
                    job.event.set()

    # -- DTO ---------------------------------------------------------------

    def dto(self, job: JobRecord) -> dict[str, Any]:
        """``$defs/Job``.  Never exposes a checkpoint byte-for-byte."""
        with job.lock:
            doc = dict(job.doc)
        return {
            "job_id": doc["job_id"],
            "project_id": doc["project_id"],
            "parent_job_id": doc.get("parent_job_id"),
            "state": doc["state"],
            "stage": doc["stage"],
            "progress": doc.get("progress"),
            "context_version": int(doc.get("context_version", 0)),
            "corpus_version": int(doc.get("corpus_version", 0)),
            "created_at": doc["created_at"],
            "updated_at": doc["updated_at"],
            "snapshot_id": doc.get("snapshot_id"),
            "error_codes": list(doc.get("error_codes", [])),
            "coverage": doc.get("coverage"),
            "checkpoint": checkpoint_summary(doc.get("checkpoint")),
            "result": doc.get("result"),
        }

    def accepted(self, job: JobRecord) -> dict[str, Any]:
        return {
            "job_id": job.job_id,
            "project_id": job.project_id,
            "revision": job.doc.get("submitted_revision", 0),
            "state": job.state,
            "poll_url": f"/api/v3/projects/{job.project_id}/jobs/{job.job_id}",
        }

    def jobs_for(self, project: V32Project, *, states: Iterable[str] | None = None) -> list[dict[str, Any]]:
        wanted = set(states) if states is not None else None
        with self.lock:
            selected = [
                job for job in self.jobs.values()
                if job.project_id == project.project_id and (wanted is None or job.state in wanted)
            ]
        return [self.dto(job) for job in selected]
