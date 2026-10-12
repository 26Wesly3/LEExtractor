"""v0.9.13 deployment hardening: configuration, health contract, shutdown, logs.

These tests describe what a deployment may rely on. They are deliberately
explicit about the *degradation* rules, because the difference between "cache is
read-only, keep searching" and "data is read-only, refuse to be ready" is the
whole point of the version.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from litsearch import config
from litsearch.demo import DemoSource
from litsearch.downloader import PaperDownloader
from litsearch.logging_setup import configure as configure_logging
from litsearch.logging_setup import log_event, redact
from litsearch.models import Paper
from litsearch.search import LiteratureReviewWorkflow
from litsearch.version import __version__
from litsearch.web import create_app
from litsearch.web.jobs import retrieval_slots

ENV_KEYS = (
    "LEEXTRACTOR_DATA_DIR", "LEEXTRACTOR_MODEL_DIR", "LEEXTRACTOR_CACHE_DIR",
    "LEEXTRACTOR_LOG_DIR", "LEEXTRACTOR_HOST", "LEEXTRACTOR_PORT",
    "LEEXTRACTOR_PUBLIC_HOSTS", "LEEXTRACTOR_MAX_ACTIVE_JOBS",
    "LEEXTRACTOR_MAX_ACTIVE_SEARCH_JOBS", "LEEXTRACTOR_EMBEDDING_THREADS",
    "LEEXTRACTOR_EMBEDDING_BATCH", "LEEXTRACTOR_LOG_FORMAT", "LEEXTRACTOR_LOG_LEVEL",
)


@pytest.fixture(autouse=True)
def clean_deployment_env(monkeypatch):
    """Every test starts from "nothing configured", like a fresh container."""
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


class BlockingSource(DemoSource):
    """A source that never finishes until the test releases it."""

    def __init__(self, entered, release):
        super().__init__()
        self.entered, self.release = entered, release

    def search_all_sources(self, query, **kwargs):
        self.entered.set()
        while not self.release.wait(0.01):
            pass
        return [Paper("10.1234/a", "Machine learning plants", year=2024, source="fake")]


def build_client(tmp_path, sources=None):
    def factory(demo):
        return LiteratureReviewWorkflow(DemoSource() if demo else sources or DemoSource(),
                                        PaperDownloader(str(tmp_path / "downloads")))
    app = create_app(tmp_path, workflow_factory=factory)
    return app, TestClient(app)


def test_app_data_root_follows_the_environment(tmp_path, clean_deployment_env):
    """A container must write projects on its volume, not inside the image."""
    clean_deployment_env.setenv("LEEXTRACTOR_DATA_DIR", str(tmp_path / "volume-data"))
    app = create_app()
    assert Path(app.state.repository.root) == tmp_path / "volume-data"

    clean_deployment_env.delenv("LEEXTRACTOR_DATA_DIR", raising=False)
    assert Path(create_app().state.repository.root).name == ".web-data"


# --------------------------------------------------------------------------
# Configuration: defaults, overrides, clamping, degradation
# --------------------------------------------------------------------------


def test_defaults_keep_the_service_local_and_single_job():
    assert config.api_host() == "127.0.0.1"
    assert config.api_port() == 8012
    assert config.max_active_search_jobs() == 1
    assert config.public_hosts() == ()
    assert "demo.duckdns.org" not in config.allowed_hosts()
    assert {"127.0.0.1", "localhost", "::1"} <= config.allowed_hosts()


def test_public_hosts_widen_the_allowlist_without_dropping_local(clean_deployment_env):
    clean_deployment_env.setenv("LEEXTRACTOR_PUBLIC_HOSTS", "Demo.DuckDNS.org, other.example ")
    hosts = config.allowed_hosts()
    assert {"demo.duckdns.org", "other.example"} <= hosts
    assert "127.0.0.1" in hosts and config.public_hosts() == ("demo.duckdns.org", "other.example")


def test_job_limit_is_clamped_and_typos_cannot_raise_it(clean_deployment_env):
    clean_deployment_env.setenv("LEEXTRACTOR_MAX_ACTIVE_JOBS", "3")
    assert config.max_active_search_jobs() == 3
    for bad in ("0", "99", "-1", "many", ""):
        clean_deployment_env.setenv("LEEXTRACTOR_MAX_ACTIVE_JOBS", bad)
        assert config.max_active_search_jobs() == config.DEFAULT_MAX_ACTIVE_JOBS, bad
    # The older ADR spelling still works.
    clean_deployment_env.delenv("LEEXTRACTOR_MAX_ACTIVE_JOBS", raising=False)
    clean_deployment_env.setenv("LEEXTRACTOR_MAX_ACTIVE_SEARCH_JOBS", "2")
    assert config.max_active_search_jobs() == 2


def test_embedding_budget_is_bounded(clean_deployment_env):
    assert (config.embedding_threads(), config.embedding_batch()) == (2, 8)
    clean_deployment_env.setenv("LEEXTRACTOR_EMBEDDING_THREADS", "1")
    clean_deployment_env.setenv("LEEXTRACTOR_EMBEDDING_BATCH", "4")
    assert (config.embedding_threads(), config.embedding_batch()) == (1, 4)
    clean_deployment_env.setenv("LEEXTRACTOR_EMBEDDING_THREADS", "0")
    assert config.embedding_threads() == 2  # out of range -> default, never 0


def test_data_root_must_be_writable_and_is_a_readiness_condition(tmp_path, clean_deployment_env):
    good = tmp_path / "data"
    clean_deployment_env.setenv("LEEXTRACTOR_DATA_DIR", str(good))
    assert config.data_ready() is True
    assert config.storage_report()["data"] == "writable"

    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    clean_deployment_env.setenv("LEEXTRACTOR_DATA_DIR", str(blocker / "data"))
    assert config.data_ready() is False
    assert config.storage_report()["data"] == "read_only"


def test_read_only_cache_falls_back_instead_of_failing_search(tmp_path, clean_deployment_env):
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    clean_deployment_env.setenv("LEEXTRACTOR_CACHE_DIR", str(blocker / "cache"))
    path = config.default_cache_path()
    assert path.endswith("litsearch_cache.db")
    assert str(blocker) not in path, "a read-only cache root must not be used"
    assert config.storage_report()["cache"] == "read_only"


def test_log_dir_reports_stdout_when_it_cannot_be_used(tmp_path, clean_deployment_env):
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    clean_deployment_env.setenv("LEEXTRACTOR_LOG_DIR", str(blocker / "logs"))
    assert config.storage_report()["logs"] == "stdout"


# --------------------------------------------------------------------------
# Health contract
# --------------------------------------------------------------------------


def test_health_reports_version_runtime_storage_and_models(tmp_path, clean_deployment_env):
    clean_deployment_env.setenv("LEEXTRACTOR_DATA_DIR", str(tmp_path / "data"))
    _, client = build_client(tmp_path)
    payload = client.get("/api/health").json()

    assert payload["status"] == "ok"
    assert payload["version"] == __version__
    assert payload["storage"]["data"] == "writable"
    assert payload["runtime"]["max_active_jobs"] == 1
    assert payload["runtime"]["embedding_batch"] == 8
    assert "models" in payload and "unreadable_projects" in payload
    assert payload["local_only"] is True


def test_health_never_leaks_secrets_or_absolute_paths(tmp_path, clean_deployment_env, monkeypatch):
    monkeypatch.setenv("S2_API_KEY", "s2k-abcdefghijklmnopqrstuvwxyz012345")
    monkeypatch.setenv("LEEXTRACTOR_DATA_DIR", str(tmp_path / "data"))
    _, client = build_client(tmp_path)
    body = client.get("/api/health").text
    assert "s2k-" not in body
    assert "S2_API_KEY" not in body
    assert str(tmp_path) not in body, "absolute paths must not be reported"


def test_liveness_is_minimal_and_readiness_follows_the_data_root(tmp_path, clean_deployment_env):
    clean_deployment_env.setenv("LEEXTRACTOR_DATA_DIR", str(tmp_path / "data"))
    _, client = build_client(tmp_path)
    assert client.get("/api/health/live").json() == {"status": "ok"}
    ready = client.get("/api/health/ready")
    assert ready.status_code == 200 and ready.json()["status"] == "ready"

    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    clean_deployment_env.setenv("LEEXTRACTOR_DATA_DIR", str(blocker / "data"))
    broken = client.get("/api/health/ready")
    assert broken.status_code == 503 and broken.json()["status"] == "not_ready"


def test_reverse_proxy_origin_is_accepted_but_other_origins_are_not(tmp_path, clean_deployment_env):
    """A browser behind Caddy sends https Origin while the app sees http.

    Without honouring X-Forwarded-Proto every same-origin POST is rejected with
    403 origin_rejected and the SPA renders an empty page (found in production).
    """
    clean_deployment_env.setenv("LEEXTRACTOR_PUBLIC_HOSTS", "demo.duckdns.org")
    _, client = build_client(tmp_path)
    headers = {"Host": "demo.duckdns.org", "Origin": "https://demo.duckdns.org"}

    # Without the proxy header the https Origin really is a different scheme and
    # must stay rejected — that is the protection, not a bug.
    assert client.get("/api/projects", headers=headers).status_code == 403
    proxied = client.get("/api/projects", headers={**headers, "X-Forwarded-Proto": "https"})
    assert proxied.status_code == 200, proxied.text
    created = client.post("/api/projects", headers={**headers, "X-Forwarded-Proto": "https"},
                          json={"name": "Origin check", "topic": "plants"})
    assert created.status_code == 201, created.text

    # A different site, or an https origin with no proxy header, stays rejected.
    assert client.get("/api/projects", headers={"Host": "demo.duckdns.org",
                                                "Origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/projects", headers={**headers,
                                                "X-Forwarded-Proto": "http"}).status_code == 403


def test_reverse_proxy_host_is_accepted_only_when_configured(tmp_path, clean_deployment_env):
    _, client = build_client(tmp_path)
    assert client.get("/api/health", headers={"Host": "evil.example"}).status_code == 403

    clean_deployment_env.setenv("LEEXTRACTOR_PUBLIC_HOSTS", "demo.duckdns.org")
    proxied = client.get("/api/health", headers={"Host": "demo.duckdns.org"})
    assert proxied.status_code == 200
    assert proxied.json()["local_only"] is False
    assert client.get("/api/health", headers={"Host": "evil.example"}).status_code == 403


# --------------------------------------------------------------------------
# Concurrency slots and graceful shutdown
# --------------------------------------------------------------------------


def test_retrieval_slots_are_process_wide(clean_deployment_env):
    clean_deployment_env.setenv("LEEXTRACTOR_MAX_ACTIVE_JOBS", "2")
    slots = retrieval_slots()
    assert slots is retrieval_slots(), "slots must be shared by every app object"
    assert slots._value == 2  # noqa: SLF001 - the capacity is the contract here


def test_shutdown_marks_a_running_job_interrupted(tmp_path, clean_deployment_env):
    import threading

    clean_deployment_env.setenv("LEEXTRACTOR_DATA_DIR", str(tmp_path / "data"))
    entered, release = threading.Event(), threading.Event()
    app, client = build_client(tmp_path, BlockingSource(entered, release))
    project = client.post("/api/projects", json={"name": "Shutdown", "topic": "plants"}).json()
    started = client.post(f"/api/projects/{project['project_id']}/search",
                          json={"expected_revision": project["revision"], "year_from": 2020,
                                "year_to": 2025, "max_papers": 10})
    assert started.status_code == 202, started.text
    assert entered.wait(timeout=5), "the blocking search never started"

    app.state.jobs.shutdown(grace=0.2)
    release.set()

    deadline = time.monotonic() + 5
    rows = []
    while time.monotonic() < deadline:
        rows = [job.payload for job in app.state.jobs.jobs.values()]
        if rows and all(row["status"] in {"completed", "partial", "failed", "canceled"}
                        for row in rows):
            break
        time.sleep(0.05)
    assert rows, "the job registry is empty"
    assert rows[0]["status"] == "partial", rows[0]
    assert rows[0]["stop_reason"] == "interrupted", rows[0]
    assert rows[0]["finished_at"], rows[0]


# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------


def test_redact_strips_credentials_and_truncates():
    payload = redact({"S2_API_KEY": "s2k-abcdefghijklmnopqrstuvwxyz012345",
                      "note": "token 6f1e0d3c-1111-2222-3333-444455556666",
                      "nested": {"authorization": "Bearer abc"}})
    assert payload["S2_API_KEY"] == "***"
    assert payload["nested"]["authorization"] == "***"
    assert "6f1e0d3c" not in payload["note"]
    assert len(redact({"text": "x" * 900})["text"]) <= 401


def test_logging_falls_back_to_stdout_then_uses_a_rotating_file(tmp_path, clean_deployment_env, capsys):
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    clean_deployment_env.setenv("LEEXTRACTOR_LOG_DIR", str(blocker / "logs"))
    assert configure_logging(force=True)["sink"] == "stdout"

    logs = tmp_path / "logs"
    clean_deployment_env.setenv("LEEXTRACTOR_LOG_DIR", str(logs))
    clean_deployment_env.setenv("LEEXTRACTOR_LOG_FORMAT", "json")
    sinks = configure_logging(force=True)
    assert sinks["sink"] == "file"
    log_event(logging.getLogger("leextractor.test"), "job_started", component="jobs",
              job_id="abc", provider="crossref", duration_ms=12)
    logging.shutdown()
    line = (logs / "leextractor.log").read_text(encoding="utf-8").strip().splitlines()[-1]
    record = json.loads(line)
    assert record["event"] == "job_started" and record["component"] == "jobs"
    assert record["version"] == __version__ and record["provider"] == "crossref"
    configure_logging(force=True)  # leave the root logger usable for other tests
