"""The v3.2 job state machine and scheduler.

Doc 14 is the specification under test: the contract's transitions are the only
legal ones, terminal states are immutable, a restart never leaves a job stuck on
``running``, a stale computation is superseded rather than merged, and a burst of
judgements collapses into one pending rerank.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from litsearch.v32 import contracts
from litsearch.v32.errors import V32Error
from litsearch.v32.jobs import checkpoint_summary, JobManager
from litsearch.v32.store import V32Store


@pytest.fixture()
def store(tmp_path: Path) -> V32Store:
    return V32Store(tmp_path / "v32")


@pytest.fixture()
def manager(store: V32Store) -> JobManager:
    mgr = JobManager(store, autostart=False)
    yield mgr
    mgr.shutdown(grace=0.1)


def _project(store: V32Store, owner: str = "o"):
    return store.create_project(owner_id=owner, name="p", entry_mode="topic")


# ---------------------------------------------------------------------------
# state machine
# ---------------------------------------------------------------------------


def test_every_legal_transition_in_the_contract_is_accepted(store, manager):
    """Walk the contract edge by edge: no legal edge may be rejected."""
    machine = contracts.job_states()
    for source, targets in machine["transitions"].items():
        for target in targets:
            project = _project(store)
            job = manager.submit(project, "noop")
            # Drive the job into `source` by the shortest legal path.
            for step in _path_to(machine, machine["initial"], source):
                if job.state != step:
                    manager.transition(job, step)
            manager.transition(job, target)
            assert job.state == target


def test_illegal_and_terminal_transitions_are_rejected(store, manager):
    project = _project(store)
    job = manager.submit(project, "noop")
    with pytest.raises(contracts.ContractError):
        manager.transition(job, "succeeded")  # queued -> succeeded is not an edge

    manager.transition(job, "running")
    manager.transition(job, "succeeded")
    assert job.is_terminal
    # Terminal states are immutable: no edge out of them at all.
    for target in ("running", "queued", "failed", "partial", "cancelled"):
        with pytest.raises(contracts.ContractError):
            manager.transition(job, target)
    assert job.state == "succeeded"


def _path_to(machine, start: str, goal: str) -> list[str]:
    if start == goal:
        return []
    seen = {start}
    queue: list[list[str]] = [[start]]
    while queue:
        path = queue.pop(0)
        for nxt in machine["transitions"][path[-1]]:
            if nxt == goal:
                return path[1:] + [goal]
            if nxt not in seen:
                seen.add(nxt)
                queue.append(path + [nxt])
    raise AssertionError(f"no legal path {start} -> {goal}")


def test_stage_is_independent_of_state(store, manager):
    """stage=finished must never be read as "everything succeeded"."""
    project = _project(store)
    job = manager.submit(project, "noop")
    manager.transition(job, "running")
    manager.set_stage(job, "retrieving", progress=0.4)
    assert (job.state, job.stage) == ("running", "retrieving")
    assert manager.dto(job)["progress"] == 0.4
    manager.transition(job, "partial", stage="finished")
    assert job.state == "partial"


# ---------------------------------------------------------------------------
# execution
# ---------------------------------------------------------------------------


def test_handler_result_decides_success_partial_or_failure(store, manager):
    project = _project(store)
    manager.register("ok", lambda p, j: {"outcome": "succeeded"})
    manager.register("some", lambda p, j: {"outcome": "partial", "error_codes": ["api_failure"],
                                           "coverage": {"status": "partial"}})
    manager.register("bad", lambda p, j: {"outcome": "failed", "error_codes": ["no_results"]})

    ok = manager.submit(project, "ok")
    some = manager.submit(project, "some")
    bad = manager.submit(project, "bad")
    manager.drain()

    assert ok.state == "succeeded"
    assert some.state == "partial"
    assert some.doc["error_codes"] == ["api_failure"]
    assert bad.state == "failed"
    assert bad.is_terminal


def test_a_superseded_result_is_dropped_not_merged(store, manager):
    """AC-09: an older job completing must not overwrite the newer snapshot."""
    project = _project(store)
    manager.register("stale", lambda p, j: {"superseded": True})
    job = manager.submit(project, "stale")
    manager.drain()
    assert job.state == "superseded"
    assert job.doc["error_codes"] == ["superseded"]
    assert project.doc["current_snapshot_id"] is None


def test_a_raising_handler_becomes_a_failed_job_without_crashing_the_scheduler(store, manager):
    project = _project(store)

    def boom(_project, _job):
        raise RuntimeError("provider exploded")

    manager.register("boom", boom)
    job = manager.submit(project, "boom")
    manager.drain()
    assert job.state == "failed"
    assert "RuntimeError" in job.doc["error_codes"]
    # The scheduler is still usable afterwards.
    manager.register("ok", lambda p, j: {"outcome": "succeeded"})
    assert manager.submit(project, "ok").state == "queued"
    manager.drain()


def test_cancelling_a_queued_job_keeps_it_terminal(store, manager):
    project = _project(store)
    manager.register("ok", lambda p, j: {"outcome": "succeeded"})
    job = manager.submit(project, "ok")
    dto = manager.cancel(project, job.job_id)
    assert dto.state == "cancelled"
    assert dto.is_terminal
    # Cancelling again returns the same terminal state, it does not rewrite it.
    assert manager.cancel(project, job.job_id).state == "cancelled"
    manager.drain()
    assert job.state == "cancelled"


def test_cancelling_a_running_job_passes_through_cancel_requested(store, manager):
    """A cooperative handler observes the flag, stops, and the job cancels.

    This is the real path: the cancellation is issued *from inside* the running
    handler, which is the only moment ``cancel_requested`` actually exists.
    """
    project = _project(store)
    seen: list[str] = []

    def cooperative(_project, job):
        manager.cancel(project, job.job_id)
        assert job.state == "cancel_requested"
        assert job.event.is_set()
        seen.append(job.state)
        return {"outcome": "succeeded"}

    manager.register("slow", cooperative)
    job = manager.submit(project, "slow")
    manager.drain()
    assert seen == ["cancel_requested"]
    assert job.state == "cancelled"
    assert "cancel_requested" in job.doc["error_codes"]


def test_a_handler_that_raises_while_cancelling_still_reaches_a_terminal_state(store, manager):
    """cancel_requested has no edge to failed; the job must not get stuck."""
    project = _project(store)

    def exploding(_project, job):
        manager.cancel(project, job.job_id)
        raise RuntimeError("died mid-cancel")

    manager.register("exploding", exploding)
    job = manager.submit(project, "exploding")
    manager.drain()
    assert job.is_terminal
    assert job.state == "cancelled"


def test_queue_ceiling_answers_429_with_a_retry_time(store):
    manager = JobManager(store, max_queue=3, max_workers=1, autostart=False)
    project = _project(store)
    for _ in range(3):
        manager.submit(project, "noop")
    with pytest.raises(V32Error) as excinfo:
        manager.submit(project, "noop")
    assert excinfo.value.status == 429
    assert excinfo.value.retryable is True
    assert excinfo.value.details["retry_after_seconds"] >= 1
    manager.shutdown(grace=0.1)


def test_only_one_heavy_job_runs_at_a_time(store):
    manager = JobManager(store, max_queue=10, max_workers=1, autostart=False)
    project = _project(store)
    concurrency: list[int] = []
    live = {"n": 0}

    def probe(_project, _job):
        live["n"] += 1
        concurrency.append(live["n"])
        live["n"] -= 1
        return {"outcome": "succeeded"}

    manager.register("probe", probe)
    for _ in range(4):
        manager.submit(project, "probe")
    manager.drain()
    assert concurrency == [1, 1, 1, 1]
    manager.shutdown(grace=0.1)


def test_scheduling_is_round_robin_across_projects(store):
    """Doc 15: 任务调度采取项目间轮询，单用户不能独占所有执行机会."""
    manager = JobManager(store, max_queue=20, max_workers=1, autostart=False)
    first = _project(store, owner="a")
    second = _project(store, owner="b")
    order: list[str] = []
    manager.register("probe", lambda p, j: (order.append(p.project_id), {"outcome": "succeeded"})[1])
    for _ in range(3):
        manager.submit(first, "probe")
    for _ in range(3):
        manager.submit(second, "probe")
    manager.drain()
    # The first two picks come from different projects, so neither is starved.
    assert order[0] != order[1]
    assert len(order) == 6
    manager.shutdown(grace=0.1)


# ---------------------------------------------------------------------------
# debounce and supersede
# ---------------------------------------------------------------------------


def test_a_burst_of_judgements_collapses_into_one_pending_rerank(store, manager):
    project = _project(store)
    manager.register("rerank", lambda p, j: {"outcome": "succeeded"})
    jobs = [manager.schedule_rerank(project, debounce_ms=0) for _ in range(5)]
    fresh = jobs[-1]
    stale = jobs[:-1]
    assert all(job.state == "superseded" for job in stale)
    assert fresh.state == "queued"
    # Only one rerank is actually pending for the project.
    assert manager._pending_rerank[project.project_id] == fresh.job_id
    manager.drain()
    assert fresh.state == "succeeded"
    assert [j.state for j in stale] == ["superseded"] * 4


def test_a_debounced_rerank_does_not_start_before_its_delay(store, manager):
    project = _project(store)
    manager.register("rerank", lambda p, j: {"outcome": "succeeded"})
    job = manager.schedule_rerank(project, debounce_ms=60_000)
    assert manager.drain() == 0
    assert job.state == "queued"


# ---------------------------------------------------------------------------
# restart, ownership, redaction
# ---------------------------------------------------------------------------


def test_restart_marks_an_unfinished_job_interrupted(store, tmp_path):
    project = _project(store)
    manager = JobManager(store, autostart=False)
    job = manager.submit(project, "noop")
    manager.transition(job, "running")
    path = manager.jobs_dir / f"{job.job_id}.json"
    assert json.loads(path.read_text(encoding="utf-8"))["state"] == "running"
    manager.shutdown(grace=0)

    # A second manager, as after a process restart.
    restarted = JobManager(V32Store(tmp_path / "v32"), autostart=False)
    reloaded = restarted.jobs[job.job_id]
    assert reloaded.state == "interrupted"
    assert reloaded.is_terminal
    assert "interrupted" in reloaded.doc["error_codes"]
    restarted.shutdown(grace=0)


def test_a_job_from_another_project_is_not_readable(store, manager):
    first = _project(store, owner="a")
    second = _project(store, owner="b")
    job = manager.submit(first, "noop")
    with pytest.raises(V32Error) as excinfo:
        manager.get(second, job.job_id)
    assert excinfo.value.status == 404


def test_checkpoint_summary_never_exposes_an_unexpected_field(store, manager):
    project = _project(store)
    job = manager.submit(project, "noop", checkpoint={
        "cursor": "abc", "committed_ids": ["x"], "budget": 42,
        "input_versions": {"context_version": 1},
        "api_key": "must-never-surface", "authorization": "must-never-surface",
    })
    dto = manager.dto(job)
    assert dto["checkpoint"] == {
        "cursor": "abc", "committed_ids": ["x"], "budget": 42,
        "input_versions": {"context_version": 1},
    }
    assert "api_key" not in json.dumps(dto)
    assert checkpoint_summary(None) == {}


def test_job_accepted_points_at_the_v3_poll_url(store, manager):
    project = _project(store)
    job = manager.submit(project, "noop")
    accepted = manager.accepted(job)
    assert accepted["state"] == "queued"
    assert accepted["poll_url"] == f"/api/v3/projects/{project.project_id}/jobs/{job.job_id}"
    assert accepted["revision"] == project.revision


def test_active_job_ids_track_submission_and_completion(store, manager):
    project = _project(store)
    manager.register("ok", lambda p, j: {"outcome": "succeeded"})
    job = manager.submit(project, "ok")
    assert project.active_job_ids == [job.job_id]
    manager.drain()
    assert project.active_job_ids == []
