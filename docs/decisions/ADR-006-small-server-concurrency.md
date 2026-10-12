# ADR-006 · Concurrency and memory budget on a 2 vCPU / 2 GB host

## Context

v0.9.12 runs one retrieval job at a time through a module-level
`RETRIEVAL_LOCK`, but the limit was implicit: `ADR-003` already named
`LEEXTRACTOR_MAX_ACTIVE_SEARCH_JOBS` without implementing it, so an operator
could neither see nor tune the real limit. On a 2 GB host, two concurrent
semantic searches are the fastest way to reach the OOM killer.

Two different kinds of concurrency must not be confused:

* **provider HTTP concurrency** — mostly network waiting, cheap in RAM
  (`LEEXTRACTOR_PROVIDER_CONCURRENCY`, default 4);
* **semantic search jobs** — each loads the embedding model and encodes
  candidates, expensive in RAM.

## Decision

1. `LEEXTRACTOR_MAX_ACTIVE_JOBS` (alias: the older
   `LEEXTRACTOR_MAX_ACTIVE_SEARCH_JOBS`) governs **jobs**, implemented as one
   **process-wide** `threading.BoundedSemaphore`.
2. Default **1**. Values are clamped to 1..8; a typo or an absurd number can
   never raise it by accident.
3. A job that cannot take a slot stays `queued` (no busy start), and
   `job_id` is returned immediately — the HTTP layer never blocks for a search.
4. The compose file carries a **provisional** `memory: 1024M` /
   `cpus: 1.50` limit plus `stop_grace_period: 20s`.

Process-wide, not per app object: an earlier draft put the semaphore on
`JobManager`, and `test_web_contract_review.py` immediately failed with
"another app bypassed the global retrieval lock" — two FastAPI app objects (the
test suite, reload, several workers) would each have allowed one job.

## Alternatives

* **Thread pool sized by CPU count** — the classic "cores × 4" guess, explicitly
  rejected by the prompt and by ADR-003; it is not a memory measurement.
* **Queue in the proxy** (Caddy/nginx limiting) — cannot see job semantics and
  would hold HTTP connections open for minutes.
* **Redis/Celery queue** — banned by the version scope for a single-container app.

## Reason

Memory is the binding constraint, not CPU: the embedding model's footprint is
what decides how many searches fit, and it is measurable with one variable.
Defaulting to 1 keeps the pre-existing behaviour exactly and makes the limit
visible, configurable and testable.

## Consequences

* Two extra semantic searches queue instead of degrading the running one.
* Raising the limit requires a benchmark on the real host (prompt §26); until
  that data exists, the documented recommendation stays **1**.
* The compose memory limit is provisional until the same benchmark runs.

## Rollback

Set `LEEXTRACTOR_MAX_ACTIVE_JOBS=0`… is not honoured (clamped to >= 1) by design;
to restore the old implicit behaviour set it to `1` (identical) or delete the
variable. Reverting the code restores `RETRIEVAL_LOCK`, which is still defined.
