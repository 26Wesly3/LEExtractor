"""Stage timing for search jobs: cheap, additive, and never fatal.

Timings are **observability data, not core data** — recording them must never fail
a job (see docs/devlog/v0.9.12.md). Anything that cannot be measured is simply
absent from the snapshot instead of raising.

Retry/429/timeout diagnostics deliberately stay in the existing ``http_budget``
block rather than being duplicated here.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

#: Stage names written into ``search_manifest["timings"]``.
STAGE_QUERY_PLANNING = "query_planning"
STAGE_TRANSLATION = "translation"
STAGE_EMBEDDING_LOAD = "embedding_load"
STAGE_PROVIDER_TOTAL = "provider_total"
STAGE_NORMALIZATION = "normalization"
STAGE_RANKING = "ranking"
STAGE_PERSISTENCE = "persistence"
STAGE_TOTAL = "total"

#: Sub-mapping holding one entry per provider.
PROVIDER_STAGE = "providers"


class StageTimer:
    """Accumulates wall-clock seconds per stage name (thread-safe)."""

    def __init__(self) -> None:
        self._seconds: dict[str, float] = {}
        self._providers: dict[str, float] = {}
        self._lock = threading.Lock()

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        started = time.perf_counter()
        try:
            yield
        finally:
            self.add(name, time.perf_counter() - started)

    def add(self, name: str, seconds: float) -> None:
        """Add ``seconds`` to ``name``; silently ignore anything unusable."""
        if not name:
            return
        try:
            value = float(seconds)
        except (TypeError, ValueError):
            return
        if value < 0:
            return
        try:
            with self._lock:
                self._seconds[name] = round(self._seconds.get(name, 0.0) + value, 4)
        except Exception:  # pragma: no cover - observability must never raise
            return

    def add_provider(self, provider: str, seconds: float) -> None:
        """Record one provider's own duration (kept separate from stage totals)."""
        if not provider:
            return
        try:
            value = float(seconds)
        except (TypeError, ValueError):
            return
        if value < 0:
            return
        try:
            with self._lock:
                self._providers[provider] = round(self._providers.get(provider, 0.0) + value, 4)
        except Exception:  # pragma: no cover
            return

    def snapshot(self, **extra: float) -> dict:
        """A JSON-serialisable snapshot; never raises."""
        try:
            with self._lock:
                data = dict(self._seconds)
                providers = dict(self._providers)
        except Exception:  # pragma: no cover
            data, providers = {}, {}
        if providers:
            data[PROVIDER_STAGE] = providers
        for key, value in extra.items():
            try:
                data[key] = round(float(value), 4)
            except (TypeError, ValueError):
                continue
        return data


def timed_call(timer: StageTimer | None, name: str, call, *args, **kwargs):
    """Run ``call`` and record its duration under ``name`` when a timer is present."""
    if timer is None:
        return call(*args, **kwargs)
    started = time.perf_counter()
    try:
        return call(*args, **kwargs)
    finally:
        timer.add(name, time.perf_counter() - started)
