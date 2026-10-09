"""Local FastAPI bridge to the existing literature review algorithms."""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from litsearch.cache import Cache
from litsearch.config import current_year, max_pdf_size_mib
from litsearch.demo import DemoSource, demo_state
from litsearch.downloader import PaperDownloader
from litsearch.evidence import EvidenceGraph, corpus_papers
from litsearch.export import to_bibtex, to_csv, to_ris
from litsearch.filters import RelevanceFilter, corpus_hash
from litsearch.intent import parse_intent, plan_payload
from litsearch.landscape import build_landscape
from litsearch.persistence import state_from_dict, state_to_dict
from litsearch.prisma import ScreeningDecision, ScreeningStage
from litsearch.questions import candidate_questions
from litsearch.screening import calibration_summary, screening_facts
from litsearch.search import LiteratureReviewWorkflow, ReviewState, ranking_query
from litsearch.session_schema import SessionSchemaError, validate_session_dict
from litsearch.sources import SourceManager
from litsearch.stop_reasons import get_http_budget
from litsearch.version import __version__
from litsearch.web.dto import (
    evidence_dto,
    facets_dto,
    graph_dto,
    paper_dto,
    paper_key,
    project_dto,
    public,
    record_for,
    screening_dto,
)
from litsearch.web.jobs import JobManager
from litsearch.web.repository import APIError, ProjectRepository, atomic_json

MAX_BODY = 32 * 1024 * 1024


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateProject(Input):
    name: str = Field(default="", max_length=200)
    topic: str = Field(default="", max_length=4000)
    research_direction: str = Field(default="", max_length=4000)


class Revision(Input):
    expected_revision: int = Field(ge=1)


class PlanInput(Input):
    topic: str | None = Field(default=None, max_length=4000)
    research_direction: str | None = Field(default=None, max_length=4000)
    year_from: int = Field(default=2006, ge=1990, le=2200)
    year_to: int = Field(default_factory=current_year, ge=1990, le=2200)


class SearchInput(Revision):
    providers: list[Literal["semantic_scholar", "openalex", "arxiv", "crossref"]] = Field(default_factory=lambda: ["semantic_scholar", "openalex", "arxiv", "crossref"], min_length=1, max_length=4)
    query: str | None = Field(default=None, min_length=1, max_length=4000)
    research_direction: str | None = Field(default=None, max_length=4000)
    mode: Literal["systematic", "scoping"] = "systematic"
    max_papers: int = Field(default=100, ge=1, le=2000)
    year_from: int = Field(default=2006, ge=1990, le=2200)
    year_to: int = Field(default_factory=current_year, ge=1990, le=2200)
    min_citations: int = Field(default=0, ge=0)
    use_query_plan: bool = True
    http_budget: int = Field(default=200, ge=0, le=2000)


class ExpandInput(Revision):
    seed_keys: list[str] = Field(default_factory=list, max_length=100)
    num_seeds: int = Field(default=10, ge=1, le=100)
    max_rounds: int = Field(default=3, ge=1, le=10)
    max_per_direction: int = Field(default=50, ge=1, le=500)
    year_from: int = Field(default=1990, ge=1990, le=2200)
    year_to: int = Field(default_factory=current_year, ge=1990, le=2200)
    min_citations: int = Field(default=0, ge=0)
    http_budget: int = Field(default=200, ge=0, le=2000)
    resume: bool = False


class SimilarInput(Revision):
    seed_keys: list[str] = Field(default_factory=list, max_length=100)
    num_seeds: int = Field(default=10, ge=1, le=100)
    top_k: int = Field(default=30, ge=1, le=500)
    http_budget: int = Field(default=200, ge=0, le=2000)


class DecisionInput(Revision):
    paper_key: str = Field(min_length=64, max_length=64)
    stage: Literal["title_abstract", "full_text"] = "title_abstract"
    decision: Literal["pending", "accept", "reject", "maybe"]
    reason: str = Field(default="", max_length=4000)
    read_confirmed: bool = False
    reviewer: str = Field(default="local user", max_length=200)


class CalibrationLabel(Input):
    paper_key: str = Field(min_length=64, max_length=64)
    label: Literal["relevant", "irrelevant"]


class CalibrationInput(Revision):
    labels: list[CalibrationLabel] = Field(min_length=1, max_length=1000)


class DownloadInput(Revision):
    paper_keys: list[str] = Field(min_length=1, max_length=100)
    http_budget: int = Field(default=100, ge=0, le=2000)


class ExportInput(Input):
    format: Literal["csv", "ris", "bibtex", "session", "evidence_pack", "prisma"]
    scope: Literal["corpus", "preliminary", "included", "selected"] = "corpus"
    paper_keys: list[str] = Field(default_factory=list, max_length=2000)


class SaveInput(Revision):
    name: str | None = Field(default=None, max_length=200)


class ImportInput(Input):
    session: dict
    name: str = Field(default="", max_length=200)


class SettingsInput(Input):
    s2_api_key: str | None = Field(default=None, max_length=2048)
    openalex_api_key: str | None = Field(default=None, max_length=2048)
    unpaywall_email: str | None = Field(default=None, max_length=320)


def _window(start, end):
    if start > end or end > current_year():
        raise APIError(422, "invalid_year_window", "年份范围须递增，结束年份不能晚于当前年份")


def _find(state, key):
    for paper in corpus_papers(state):
        if paper_key(paper) == key:
            return paper
    raise APIError(404, "paper_not_found", "项目中没有该文献")


def _settings():
    return {"local_only": True, "max_pdf_size_mib": max_pdf_size_mib(), "providers": {
        "semantic_scholar": {"configured": bool(os.environ.get("S2_API_KEY", "").strip())},
        "openalex": {"configured": bool(os.environ.get("OPENALEX_API_KEY", "").strip())},
        "unpaywall": {"configured": bool(os.environ.get("LEEXTRACTOR_UNPAYWALL_EMAIL", "").strip())},
    }}


def create_app(data_dir=None, workflow_factory=None, static_dir=None):
    """Build an isolated app. No network request or background job starts here.

    ``workflow_factory`` receives ``demo: bool`` and returns a workflow with
    independent source objects. Fake sources can be injected for offline tests.
    """
    root = Path(data_dir or Path(__file__).resolve().parents[2] / ".web-data")
    repository = ProjectRepository(root)
    settings_path = repository.root / "settings.json"
    try:
        saved_settings = json.loads(settings_path.read_text(encoding="utf-8"))
        if isinstance(saved_settings, dict):
            for key in ("S2_API_KEY", "OPENALEX_API_KEY", "LEEXTRACTOR_UNPAYWALL_EMAIL"):
                if isinstance(saved_settings.get(key), str):
                    os.environ[key] = saved_settings[key]
    except (OSError, ValueError, TypeError):
        pass

    def default_factory(demo):
        sources = DemoSource() if demo else SourceManager(Cache(str(repository.root / "cache.db")))
        return LiteratureReviewWorkflow(sources, downloader=PaperDownloader(str(repository.root / "downloads")))

    jobs = JobManager(repository, workflow_factory or default_factory)

    @asynccontextmanager
    async def lifespan(_app):
        yield
        jobs.shutdown()

    app = FastAPI(title="LEExtractor Local API", version=__version__, lifespan=lifespan,
                  docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)
    app.state.repository, app.state.jobs = repository, jobs

    @app.middleware("http")
    async def local_access(request: Request, call_next):
        host = request.headers.get("host", "").lower()
        hostname = urlsplit("http://" + host).hostname
        test_client = request.client and request.client.host == "testclient"
        if hostname not in {"127.0.0.1", "localhost", "::1"} and not (test_client and hostname == "testserver"):
            return JSONResponse({"error": {"code": "invalid_host", "message": "仅允许本机访问", "detail": None}}, 403)
        origin = request.headers.get("origin")
        if origin:
            parsed = urlsplit(origin)
            if parsed.scheme != request.url.scheme or parsed.netloc.lower() != host:
                return JSONResponse({"error": {"code": "origin_rejected", "message": "仅允许同源请求", "detail": None}}, 403)
        if request.method in {"POST", "PUT", "PATCH"}:
            try:
                length = int(request.headers.get("content-length", "0"))
            except ValueError:
                length = MAX_BODY + 1
            if length > MAX_BODY:
                return JSONResponse({"error": {"code": "body_too_large", "message": "上传文件过大", "detail": None}}, 413)
            if len(await request.body()) > MAX_BODY:
                return JSONResponse({"error": {"code": "body_too_large", "message": "上传文件过大", "detail": None}}, 413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(APIError)
    async def error_handler(_request, exc):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message,
                                       "detail": public(exc.detail)}}, exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_request, exc):
        # Pydantic error.input may contain secret strings; only expose locations.
        return JSONResponse(public({"error": {"code": "invalid_request", "message": "请求参数无效",
                                              "detail": [{"field": ".".join(map(str, item["loc"])),
                                                          "code": item["type"]} for item in exc.errors()]}}), 422)

    @app.exception_handler(Exception)
    async def unexpected_handler(_request, _exc):
        return JSONResponse({"error": {"code": "internal_error", "message": "操作未完成，请检查服务日志或稍后重试", "detail": None}}, 500)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "app_name": "LEExtractor", "version": __version__, "ui": "vue", "local_only": True,
                "storage": "ready", "unreadable_projects": repository.load_errors,
                "dependencies": {"fastapi": True, "algorithms": True}}

    @app.get("/api/settings")
    def settings():
        return _settings()

    @app.post("/api/settings")
    def set_settings(body: SettingsInput):
        names = {"s2_api_key": "S2_API_KEY", "openalex_api_key": "OPENALEX_API_KEY",
                 "unpaywall_email": "LEEXTRACTOR_UNPAYWALL_EMAIL"}
        with repository.lock:
            if any(project.active_job_id for project in repository.projects.values()):
                raise APIError(409, "settings_busy", "任务运行中不能修改凭据")
            values = {name: os.environ.get(name, "") for name in names.values()}
            for field, name in names.items():
                value = getattr(body, field)
                if value is None:
                    continue
                if any(char in value for char in "\r\n\0"):
                    raise APIError(422, "invalid_setting", "设置值不能含换行或控制符")
                values[name] = value.strip()
            atomic_json(settings_path, values)
            for name, value in values.items():
                os.environ[name] = value
        return _settings()

    @app.get("/api/projects")
    def projects():
        with repository.lock:
            items = sorted(repository.projects.values(), key=lambda p: p.updated_at, reverse=True)
        rows = []
        for project in items:
            with project.lock:
                rows.append(project_dto(project))
        return {"items": rows, "total": len(rows)}

    @app.post("/api/projects", status_code=201)
    def create_project(body: CreateProject):
        return project_dto(repository.create(body.name, ReviewState(body.topic, body.research_direction)))

    @app.post("/api/projects/import", status_code=201)
    def import_project(body: ImportInput):
        try:
            state = state_from_dict(validate_session_dict(body.session))
        except SessionSchemaError as exc:
            detail = [{"field": row.get("field"), "code": row.get("code")}
                      for row in exc.errors]
            raise APIError(422, "invalid_session", "会话校验失败，未导入", detail) from None
        except (ValueError, TypeError, KeyError):
            raise APIError(422, "invalid_session", "会话无法恢复，未导入") from None
        demo = state.search_manifest.get("mode") == "synthetic_demo"
        return project_dto(repository.create(body.name, state, demo=demo))

    @app.post("/api/demo/projects", status_code=201)
    def demo_project():
        state = demo_state()
        # The old fixture predates whole-corpus scoring contexts.
        workflow = LiteratureReviewWorkflow.__new__(LiteratureReviewWorkflow)
        workflow.filters = RelevanceFilter()
        workflow.rerank_corpus(state, stage="synthetic_demo")
        return project_dto(repository.create("离线演示（虚构数据）", state, demo=True))

    @app.get("/api/projects/{pid}")
    def project_state(pid: str):
        project = repository.get(pid)
        with project.lock:
            return project_dto(project)

    @app.post("/api/projects/{pid}/save")
    def save_project(pid: str, body: SaveInput):
        project = repository.get(pid)
        with project.lock:
            repository.require_idle(project)
            repository.check_revision(project, body.expected_revision)
            if body.name is not None:
                project.name = body.name
            repository.save(project)
            return project_dto(project)

    @app.post("/api/projects/{pid}/query-plan")
    def query_plan(pid: str, body: PlanInput):
        _window(body.year_from, body.year_to)
        project = repository.get(pid)
        with project.lock:
            return public(plan_payload(body.topic if body.topic is not None else project.state.topic,
                                       body.research_direction if body.research_direction is not None
                                       else project.state.research_direction, body.year_from, body.year_to))

    @app.get("/api/projects/{pid}/papers")
    def papers(pid: str, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
               q: str = "", provider: str = "", method: str = "", status: str = "",
               year_from: int | None = None, year_to: int | None = None,
               min_score: float = Query(0, ge=0, le=1),
               sort: Literal["relevance", "citations", "year", "title"] = "relevance"):
        project = repository.get(pid)
        with project.lock:
            rows = [paper_dto(project.state, p) for p in corpus_papers(project.state)]
            rows = [r for r in rows if (not q or q.lower() in (r["title"] + " " + (r["abstract"] or "")).lower())
                    and (not provider or provider in r["providers"])
                    and (not method or any(t["method"] == method for t in r["discovery_traces"]))
                    and (not status or r["screening"]["status"] == status)
                    and (year_from is None or r["year"] is not None and r["year"] >= year_from)
                    and (year_to is None or r["year"] is not None and r["year"] <= year_to)
                    and r["relevance_score"] >= min_score]
            field = {"relevance": "relevance_score", "citations": "citation_count", "year": "year", "title": "title"}[sort]
            rows.sort(key=lambda r: (r[field] if r[field] is not None else 0, r["paper_key"]), reverse=sort != "title")
            total = len(rows)
            return {"items": rows[(page - 1) * page_size:page * page_size], "total": total,
                    "page": page, "page_size": page_size, "score_context_id": project.state.score_context_id,
                    "revision": project.revision}

    @app.get("/api/projects/{pid}/facets")
    def facets(pid: str):
        project = repository.get(pid)
        with project.lock:
            return facets_dto(project.state)

    @app.get("/api/projects/{pid}/papers/{key}")
    def paper_detail(pid: str, key: str):
        project = repository.get(pid)
        with project.lock:
            paper = _find(project.state, key)
            row = paper_dto(project.state, paper, detail=True)
            graph = graph_dto(project.state)
            row["relations"] = [edge for edge in graph["edges"] if key in {edge["source"], edge["target"]}]
            return row

    @app.post("/api/projects/{pid}/search", status_code=202)
    def search(pid: str, body: SearchInput):
        _window(body.year_from, body.year_to)
        if len(body.providers) != len(set(body.providers)):
            raise APIError(422, "duplicate_providers", "来源不能重复选择")
        project = repository.get(pid)
        if project.demo:
            raise APIError(409, "demo_search_disabled", "演示项目只运行本地模拟扩展；请新建真实检索项目")
        if not (body.query or project.state.topic).strip():
            raise APIError(422, "query_required", "请填写检索主题")

        def operation(workflow, state, guard, checkpoint):
            configure = getattr(workflow.sources, "set_search_providers", None)
            if configure:
                configure(body.providers)
            state.topic = body.query or state.topic
            state.research_direction = body.research_direction if body.research_direction is not None else state.research_direction
            state.stop_reason = ""
            if body.mode == "scoping":
                state = workflow.scope_topic(state.topic, state.research_direction, initial_limit=body.max_papers,
                                             use_query_plan=body.use_query_plan, year_from=body.year_from, year_to=body.year_to)
            else:
                workflow.systematic_search(state, max_papers=body.max_papers, min_citations=body.min_citations,
                                           use_query_plan=body.use_query_plan, year_from=body.year_from, year_to=body.year_to)
            return {"records": len(corpus_papers(state))}, state

        return jobs.dto(jobs.submit(project, "search", body.expected_revision, operation, body.http_budget))

    @app.post("/api/projects/{pid}/expand", status_code=202)
    def expand(pid: str, body: ExpandInput):
        _window(body.year_from, body.year_to)
        project = repository.get(pid)
        with project.lock:
            selected = [_find(project.state, key).canonical_id for key in body.seed_keys]
            if not corpus_papers(project.state):
                raise APIError(422, "seeds_required", "请先检索或导入种子文献")
            if body.resume and project.state.snowball_result is None:
                raise APIError(409, "no_checkpoint", "当前项目没有可恢复的引文扩展检查点")

        def operation(workflow, state, guard, checkpoint):
            state.stop_reason = ""
            seeds = [p for p in corpus_papers(state) if p.canonical_id in selected] if selected else None
            if seeds is None and not state.search_papers:
                seeds = corpus_papers(state)[:body.num_seeds]

            def on_round(round_no, _max, discovered):
                checkpoint({"stage": "expanding", "round": round_no, "new_records": discovered})

            workflow.run_snowballing(state, num_seeds=body.num_seeds, max_rounds=body.max_rounds,
                                     max_per_direction=body.max_per_direction, min_citations=body.min_citations,
                                     year_from=body.year_from, year_to=body.year_to,
                                     on_round=on_round, resume=body.resume, seeds=seeds)
            state.stop_reason = state.snowball_result.stop_reason
            return {"rounds": len(state.snowball_result.rounds), "new_records": state.snowball_result.total_discovered}, None

        return jobs.dto(jobs.submit(project, "expand", body.expected_revision, operation, body.http_budget))

    @app.post("/api/projects/{pid}/similar", status_code=202)
    def similar(pid: str, body: SimilarInput):
        project = repository.get(pid)
        with project.lock:
            selected = [_find(project.state, key).canonical_id for key in body.seed_keys]
            if not corpus_papers(project.state):
                raise APIError(422, "seeds_required", "关联检索需要已有语料")

        def operation(workflow, state, guard, checkpoint):
            state.stop_reason = ""
            original = state.search_papers
            if selected:
                state.search_papers = [p for p in corpus_papers(state) if p.canonical_id in selected]
            elif not state.search_papers:
                state.search_papers = corpus_papers(state)[:body.num_seeds]
            try:
                workflow.find_similar(state, num_seeds=len(selected) if selected else body.num_seeds, top_k=body.top_k)
            finally:
                # Seed selection is a view, not replacement of the project corpus.
                state.search_papers = original
            workflow.rerank_corpus(state, stage="similar")
            return {"records": len(state.similar_papers)}, None

        return jobs.dto(jobs.submit(project, "similar", body.expected_revision, operation, body.http_budget))

    @app.get("/api/jobs/{jid}")
    def job(jid: str):
        return jobs.dto(jobs.get(jid))

    @app.post("/api/jobs/{jid}/cancel")
    def cancel_job(jid: str):
        return jobs.cancel(jobs.get(jid))

    @app.get("/api/projects/{pid}/landscape")
    def landscape(pid: str):
        project = repository.get(pid)
        with project.lock:
            state = state_from_dict(state_to_dict(project.state))
        return evidence_dto(state, {"graph": graph_dto(state), "landscape": build_landscape(corpus_papers(state)),
                                    "score_context_id": state.score_context_id})

    @app.get("/api/projects/{pid}/questions")
    def questions(pid: str):
        project = repository.get(pid)
        with project.lock:
            state = state_from_dict(state_to_dict(project.state))
        papers = corpus_papers(state)
        return evidence_dto(state, candidate_questions(papers, landscape=build_landscape(papers),
                                                     intent={"slots": parse_intent(state.topic, state.research_direction).slot_dict()}))

    @app.get("/api/projects/{pid}/landscape/path")
    def evidence_path(pid: str, source: str, target: str):
        project = repository.get(pid)
        with project.lock:
            papers = corpus_papers(project.state)
            first, last = _find(project.state, source), _find(project.state, target)
            graph = EvidenceGraph(papers)
            path = graph.path(first.canonical_id, last.canonical_id)
            selected = {p.canonical_id: p for p in papers}
            return {"path": [paper_key(selected[key]) for key in path],
                    "nodes": [paper_dto(project.state, selected[key]) for key in path],
                    "scope": "observed directed citation path"}

    @app.get("/api/projects/{pid}/review")
    def review(pid: str, stage: Literal["title_abstract", "full_text"] = "title_abstract"):
        project = repository.get(pid)
        with project.lock:
            state = project.state
            report = state.prisma.generate_report()
            return public({"facts": screening_facts(state), "prisma": report.to_flow_dict(),
                           "ledger": report.ledger,
                           "queue": [paper_dto(state, r.paper) for r in state.prisma.get_actionable_queue(ScreeningStage(stage))],
                           "records": [{"paper_key": paper_key(r.paper), **screening_dto(r),
                                        "history": r.history, "conflicts": r.conflicts} for r in state.prisma.records.values()],
                           "revision": project.revision, "score_context_id": state.score_context_id})

    @app.post("/api/projects/{pid}/screening-decisions")
    def decisions(pid: str, body: DecisionInput):
        project = repository.get(pid)
        with project.lock:
            repository.require_idle(project)
            repository.check_revision(project, body.expected_revision)
            if body.decision == "reject" and not body.reason.strip():
                raise APIError(422, "reason_required", "排除文献需要填写理由")
            if body.stage == "full_text" and body.decision == "accept" and not body.read_confirmed:
                raise APIError(422, "read_confirmation_required", "纳入全文前须确认已经阅读全文")
            paper = _find(project.state, body.paper_key)
            record = record_for(project.state, paper)
            if body.stage == "full_text" and (record is None or not record.passed_screening):
                raise APIError(409, "title_screening_required", "请先通过题目摘要筛选")
            if record is None:
                project.state.prisma.add_papers([paper], source="manual")
                record = record_for(project.state, paper)
            if body.stage == "full_text" and body.read_confirmed:
                project.state.prisma.mark_full_text_retrieved(paper.canonical_id, True, reviewer=body.reviewer)
            project.state.prisma.screen_paper(paper.canonical_id, ScreeningDecision(body.decision),
                                             body.reason, stage=ScreeningStage(body.stage), reviewer=body.reviewer)
            repository.save(project)
            return public({"revision": project.revision, "paper": paper_dto(project.state, paper, detail=True),
                           "facts": screening_facts(project.state)})

    @app.post("/api/projects/{pid}/calibration")
    def calibration(pid: str, body: CalibrationInput):
        project = repository.get(pid)
        with project.lock:
            repository.require_idle(project)
            repository.check_revision(project, body.expected_revision)
            if len({label.paper_key for label in body.labels}) != len(body.labels):
                raise APIError(422, "duplicate_labels", "同一篇文献不能重复标注")
            state = project.state
            labels = {"relevant": [], "irrelevant": []}
            for label in body.labels:
                labels[label.label].append(_find(state, label.paper_key))
            state.calibration = RelevanceFilter.build_calibration(labels, query=ranking_query(state),
                                                                 corpus_hash=corpus_hash(corpus_papers(state)),
                                                                 score_context_id=state.score_context_id)
            repository.save(project)
            return public({"calibration": calibration_summary(state), "revision": project.revision})

    @app.post("/api/projects/{pid}/downloads", status_code=202)
    def downloads(pid: str, body: DownloadInput):
        project = repository.get(pid)
        if project.demo:
            raise APIError(409, "synthetic_no_fulltext", "虚构演示文献没有真实全文")
        with project.lock:
            selected = [_find(project.state, key).canonical_id for key in body.paper_keys]

        def operation(workflow, state, guard, checkpoint):
            downloader = PaperDownloader(str(repository.root / "downloads" / project.project_id))
            original_request = downloader._session.request

            def guarded_request(method, url, **kwargs):
                guard.check()
                try:
                    response = original_request(method, url, **kwargs)
                except Exception:
                    get_http_budget().note_error("fulltext")
                    raise
                get_http_budget().note_request("fulltext", response.status_code)
                return response

            downloader._session.request = guarded_request
            results = []
            papers = [p for p in corpus_papers(state) if p.canonical_id in selected]
            state.stop_reason = ""
            for index, paper in enumerate(papers):
                if guard.event.is_set():
                    break
                outcome = downloader.download_pdf_with_provenance(paper)
                state.prisma.mark_full_text_retrieved(paper.canonical_id, outcome.ok)
                file = None
                if outcome.ok:
                    path = Path(outcome.path).resolve()
                    allowed = (repository.root / "downloads" / project.project_id).resolve()
                    if path.is_relative_to(allowed):
                        file = repository.add_file(project.project_id, path.read_bytes(), path.name, "application/pdf")
                results.append({"paper_key": paper_key(paper), "status": outcome.status,
                                "provenance": public(outcome.provenance), "file": file})
                checkpoint({"stage": "downloading", "completed": index + 1, "total": len(papers)})
            return {"downloads": results}, None

        return jobs.dto(jobs.submit(project, "downloads", body.expected_revision, operation, body.http_budget))

    @app.post("/api/projects/{pid}/exports")
    def exports(pid: str, body: ExportInput):
        project = repository.get(pid)
        with project.lock:
            state = state_from_dict(state_to_dict(project.state))
        papers = corpus_papers(state)
        if body.scope == "preliminary":
            papers = [p for p in papers if (r := record_for(state, p)) and r.passed_screening]
        elif body.scope == "included":
            papers = state.prisma.get_included_papers()
        elif body.scope == "selected":
            papers = [_find(state, key) for key in body.paper_keys]
        if body.format in {"session", "evidence_pack", "prisma"} and body.scope != "corpus":
            raise APIError(422, "invalid_export_scope", "会话、证据包与流程报告保存整个项目，请使用 corpus 范围")
        if body.format == "csv":
            payload, filename, media = to_csv(papers, include_abstract=True).encode("utf-8-sig"), "papers.csv", "text/csv"
        elif body.format == "ris":
            payload, filename, media = to_ris(papers).encode("utf-8"), "references.ris", "application/x-research-info-systems"
        elif body.format == "bibtex":
            payload, filename, media = to_bibtex(papers).encode("utf-8"), "references.bib", "application/x-bibtex"
        elif body.format == "session":
            payload, filename, media = json.dumps(public(state_to_dict(state)), ensure_ascii=False).encode("utf-8"), "session.json", "application/json"
        elif body.format == "prisma":
            payload, filename, media = json.dumps(public(state.prisma.generate_report().to_flow_dict()), ensure_ascii=False).encode("utf-8"), "prisma.json", "application/json"
        else:
            from litsearch.bundle import build_evidence_pack

            # Build from sanitized session so imported credential/path fields
            # cannot leak through the archive's manifest/session.json.
            safe_state = state_from_dict(public(state_to_dict(state)))
            payload, filename, media = build_evidence_pack(safe_state), "evidence_pack.zip", "application/zip"
        return repository.add_file(project.project_id, payload, filename, media)

    @app.get("/api/files/{file_id}")
    def controlled_file(file_id: str):
        path, meta = repository.file(file_id)
        return FileResponse(path, media_type=meta["media_type"], filename=meta["filename"])

    source_dist = Path(__file__).resolve().parents[2] / "web" / "dist"
    dist = Path(static_dir or (source_dist if source_dist.is_dir() else Path(__file__).parent / "static")).resolve()

    @app.get("/{requested:path}", include_in_schema=False)
    def spa(requested: str):
        if requested.startswith("api/") or requested == "api":
            raise APIError(404, "not_found", "接口不存在")
        candidate = (dist / requested).resolve()
        if not candidate.is_relative_to(dist):
            raise APIError(404, "not_found", "文件不存在")
        if candidate.is_file():
            return FileResponse(candidate)
        if requested.startswith("assets/"):
            raise APIError(404, "not_found", "静态资源不存在")
        index = dist / "index.html"
        if not index.is_file():
            raise APIError(503, "frontend_not_built", "Web UI 尚未构建，请运行发布包或先构建 web 目录")
        return FileResponse(index)

    return app


def main():
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description="LEExtractor local web application")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--data-dir", default=None)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    uvicorn.run(create_app(data_dir=args.data_dir), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
