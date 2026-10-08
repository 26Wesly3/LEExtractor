"""Offline tests for the MCP surface: tool registration and screening semantics.

The defect these guard is the v0.9.0 default: ``literature_review_search``
auto-screened by ``relevance >= 0.15`` and returned nothing but an empty
``included_papers`` list, so a caller could neither trust the result nor
continue from it. A regression here is a silent, permanent "excluded" verdict
on papers nobody read.
"""

import asyncio
import inspect

import pytest
from fastmcp import Client

from litsearch.demo import demo_state
from litsearch.models import Author, Paper
from litsearch.persistence import state_to_dict
from litsearch.prisma import FULL_TEXT_EXCLUSION_REASONS, ScreeningDecision, ScreeningStage
from litsearch.search import ReviewState
from litsearch.server import (
    SCREENING_STATUS_VALUES,
    literature_review_search,
    mcp,
    paper_screening_status,
    screening_facts,
    screening_section,
)

#: The 12 tools the release must keep registering (spec S0).
EXPECTED_TOOLS = {
    "literature_review_search",
    "snowball_from_seeds",
    "find_similar_papers",
    "download_paper",
    "batch_download",
    "plan_search_strategy",
    "search_literature",
    "explain_paper",
    "build_research_landscape",
    "candidate_research_questions",
    "get_evidence_path",
    "export_research_bundle",
}


def _papers(count=4):
    return [
        Paper(id=f"10.9/p{i}", doi=f"10.9/p{i}", title=f"Crop phenotyping study {i}",
              abstract="wheat image phenotyping", year=2020 + i, source="fake",
              authors=[Author(name="A. Author")], citation_count=i)
        for i in range(count)
    ]


class _StubDownloader:
    def get_download_url(self, paper):
        return f"https://example.invalid/{paper.canonical_id}"

    def get_download_dir(self):
        return "downloads"


class _StubWorkflow:
    """Stands in for the networked workflow; records every screening call."""

    instances: list["_StubWorkflow"] = []

    def __init__(self, *args, **kwargs):
        self.downloader = _StubDownloader()
        self.calls: list = []
        _StubWorkflow.instances.append(self)

    def scope_topic(self, topic, direction, years_back=20, **kwargs):
        self.calls.append("scope_topic")
        state = ReviewState(topic, direction)
        state.scoping_papers = _papers()
        self.state = state
        return state

    def systematic_search(self, state, max_papers=200, years_back=20, **kwargs):
        self.calls.append("systematic_search")
        state.search_papers = _papers()
        state.prisma.add_papers(state.search_papers, source="database_search")
        return state

    def run_snowballing(self, state, **kwargs):
        self.calls.append("run_snowballing")
        return state

    def find_similar(self, state, **kwargs):
        self.calls.append("find_similar")
        return state

    def generate_prisma_report(self, state):
        self.calls.append("generate_prisma_report")
        return state.prisma.generate_report()

    def get_included_papers(self, state):
        return state.prisma.get_included_papers()

    def auto_screened(self):
        return [call for call in self.calls if call == "auto_screen"]


@pytest.fixture
def stub_workflow(monkeypatch):
    _StubWorkflow.instances = []
    monkeypatch.setattr("litsearch.search.LiteratureReviewWorkflow", _StubWorkflow)
    return _StubWorkflow


def test_mcp_registers_exactly_the_twelve_documented_tools():
    async def check():
        async with Client(mcp) as client:
            tools = await client.list_tools()
            names = {tool.name for tool in tools}
            assert names == EXPECTED_TOOLS, names ^ EXPECTED_TOOLS
    asyncio.run(check())


def test_mcp_tools_register_and_share_the_same_evidence_model():
    async def check():
        async with Client(mcp) as client:
            tools = await client.list_tools()
            names = {tool.name for tool in tools}
            assert {"search_literature", "explain_paper", "build_research_landscape",
                    "get_evidence_path", "export_research_bundle",
                    "literature_review_search"} <= names
            session = state_to_dict(demo_state())
            result = await client.call_tool("build_research_landscape", {"session": session})
            assert not result.is_error
            assert result.data["summary"]["nodes"] == 8
            assert result.data["summary"]["edges"] == 6
            explained = await client.call_tool("explain_paper", {"session": session, "paper_id": "demo:1"})
            assert explained.data["discovery_traces"][0]["method"] == "demo_fixture"
    asyncio.run(check())


def test_literature_review_search_defaults_to_no_automatic_screening(stub_workflow):
    signature = inspect.signature(literature_review_search)
    assert signature.parameters["auto_screen"].default is False
    assert signature.parameters["calibration"].default is None

    result = literature_review_search(topic="wheat phenotyping")
    workflow = stub_workflow.instances[-1]
    assert workflow.auto_screened() == []
    assert result["screening_status"] == "not_started"
    assert result["threshold_calibrated"] is False
    assert result["requires_manual_review"] is True
    assert result["full_text_review_completed"] is False


def test_auto_screen_false_still_returns_candidates_and_a_restorable_session(stub_workflow):
    result = literature_review_search(topic="wheat phenotyping", auto_screen=False)

    # Not an empty result: the caller gets the candidates it must screen.
    assert result["included_papers"] == []
    assert result["candidate_count"] == 4
    assert len(result["candidates"]) == 4
    assert {row["screening_status"] for row in result["candidates"]} == {"not_started"}
    assert all(row["screening_decision"] == "pending" for row in result["candidates"])
    assert all(row["relevance_score"] >= 0 for row in result["candidates"])

    # ... and a session that can be resumed and screened elsewhere.
    session = result["session"]
    assert session["topic"] == "wheat phenotyping"
    assert len(session["search_papers"]) == 4
    assert session["prisma"]["records"]
    assert result["screening"]["records_total"] == 4
    assert "manual" in result["screening_note"].lower() or "人工" in result["screening_note"]


def test_auto_screen_true_without_a_calibration_rejects_nothing(stub_workflow):
    result = literature_review_search(topic="wheat phenotyping", auto_screen=True)
    workflow = stub_workflow.instances[-1]

    assert workflow.auto_screened() == []
    assert result["threshold_calibrated"] is False
    assert result["screening_status"] == "not_started"
    assert all(row["screening_status"] == "not_started" for row in result["candidates"])
    assert result["screening"]["calibration"]["present"] is False
    assert "calibrat" in result["screening_note"].lower()


def test_auto_screen_true_with_an_insufficient_calibration_rejects_nothing(stub_workflow):
    """Single-class labels are a ranking aid, never a screening boundary."""
    from litsearch.filters import CalibrationRecord

    insufficient = CalibrationRecord(
        labels={"relevant": 3, "irrelevant": 0},
        positive_count=3,
        negative_count=0,
        query="wheat phenotyping",
        status="insufficient_labels",
        threshold=None,
    )
    result = literature_review_search(
        topic="wheat phenotyping", auto_screen=True, calibration=insufficient.to_dict()
    )
    assert result["threshold_calibrated"] is False
    assert all(row["screening_status"] == "not_started" for row in result["candidates"])


def test_full_text_auto_screening_is_refused_by_the_core_api():
    from litsearch.prisma import AutoScreeningRefused
    from litsearch.search import LiteratureReviewWorkflow

    workflow = LiteratureReviewWorkflow.__new__(LiteratureReviewWorkflow)
    state = ReviewState("wheat", "wheat")
    papers = _papers(2)
    state.search_papers = papers
    state.prisma.add_papers(papers)
    state.prisma.screen_paper(papers[0].id, ScreeningDecision.ACCEPT)

    with pytest.raises(ValueError, match="FULL_TEXT"):
        workflow.auto_screen(state, relevance_threshold=0.3, stage=ScreeningStage.FULL_TEXT)

    with pytest.raises(AutoScreeningRefused):
        state.prisma.screen_by_calibrated_threshold(None, stage=ScreeningStage.FULL_TEXT)
    with pytest.raises(AutoScreeningRefused):
        state.prisma.screen_by_calibrated_threshold(None, stage=ScreeningStage.TITLE_ABSTRACT)

    # Nothing was decided behind the user's back by the refused calls.
    assert state.prisma.records[papers[1].canonical_id].screening_decision is ScreeningDecision.PENDING


def test_run_full_workflow_does_not_auto_screen_by_default():
    from litsearch.search import LiteratureReviewWorkflow

    parameters = inspect.signature(LiteratureReviewWorkflow.run_full_workflow).parameters
    assert parameters["auto_screen"].default is False
    assert parameters["relevance_threshold"].default is None


def test_screening_facts_status_ladder_and_calibration_gate():
    from litsearch.filters import CalibrationRecord

    papers = _papers(3)
    state = ReviewState("wheat", "wheat")
    state.prisma.add_papers(papers)
    state.search_papers = papers

    facts = screening_facts(state)
    assert facts["screening_status"] == "not_started"
    assert facts["threshold_calibrated"] is False
    assert facts["requires_manual_review"] is True
    assert facts["full_text_review_completed"] is False
    for field in ("screening_status", "threshold_calibrated", "requires_manual_review",
                  "full_text_review_completed"):
        assert field in facts["definitions"]

    # A calibrated record is required before the threshold may be called usable.
    state.calibration = CalibrationRecord(status="insufficient_labels", threshold=None)
    assert screening_facts(state)["threshold_calibrated"] is False
    state.calibration = CalibrationRecord(status="calibrated", threshold=0.42)
    assert screening_facts(state)["threshold_calibrated"] is True

    # Title/abstract acceptance is preliminary until the full text is assessed.
    for paper in papers:
        state.prisma.screen_paper(paper.id, ScreeningDecision.ACCEPT)
    facts = screening_facts(state)
    assert facts["screening_status"] == "preliminary_included"
    assert facts["full_text_review_completed"] is False
    assert facts["requires_manual_review"] is True
    assert facts["calibration"]["threshold"] == 0.42

    # Completing the full-text stage decides the final status.
    for paper in papers:
        state.prisma.screen_paper(paper.id, ScreeningDecision.ACCEPT, stage=ScreeningStage.FULL_TEXT)
    facts = screening_facts(state)
    assert facts["screening_status"] == "final_included"
    assert facts["full_text_review_completed"] is True
    assert facts["requires_manual_review"] is False
    assert facts["screening_status"] in SCREENING_STATUS_VALUES

    # A review that finished with no inclusion is "excluded", not "not_started".
    empty = ReviewState("wheat", "wheat")
    empty.prisma.add_papers(_papers(2))
    for paper in empty.prisma.records.values():
        empty.prisma.screen_paper(paper.paper.id, ScreeningDecision.REJECT)
    exhausted = screening_facts(empty)
    assert exhausted["screening_status"] == "excluded"
    assert exhausted["requires_manual_review"] is True


def test_per_paper_screening_status_separates_stages():
    papers = _papers(2)
    state = ReviewState("wheat", "wheat")
    state.prisma.add_papers(papers)
    records = list(state.prisma.records.values())
    assert paper_screening_status(records[0]) == "not_started"

    state.prisma.screen_paper(papers[0].id, ScreeningDecision.ACCEPT)
    assert paper_screening_status(records[0]) == "preliminary_included"

    state.prisma.screen_paper(papers[0].id, ScreeningDecision.ACCEPT, stage=ScreeningStage.FULL_TEXT)
    assert paper_screening_status(records[0]) == "final_included"

    state.prisma.screen_paper(papers[0].id, ScreeningDecision.PENDING, stage=ScreeningStage.FULL_TEXT)
    assert paper_screening_status(records[0]) == "preliminary_included"

    state.prisma.screen_paper(papers[0].id, ScreeningDecision.REJECT)
    assert paper_screening_status(records[0]) == "excluded"


def test_export_bundle_and_questions_expose_screening_and_coverage():
    async def check():
        session = state_to_dict(demo_state())
        async with Client(mcp) as client:
            bundle = await client.call_tool("export_research_bundle", {"session": session})
            assert not bundle.is_error
            data = bundle.data
            for field in ("screening_status", "threshold_calibrated",
                          "requires_manual_review", "full_text_review_completed"):
                assert field in data
            assert data["screening_status"] == "not_started"
            assert data["requires_manual_review"] is True
            assert "## Screening status" in data["agent_handoff_notes"]
            assert "preliminary" in data["agent_handoff_notes"]

            questions = await client.call_tool(
                "candidate_research_questions", {"session": session}
            )
            assert not questions.is_error
            assert "citation_coverage" in questions.data
            assert questions.data["citation_coverage"]["complete"] in (True, False)
            for item in questions.data["questions"]:
                assert item["status"] == "hypothesis"
                assert item["computation_basis"].strip()
                assert item["suggested_next_search"].strip()
                assert isinstance(item["coverage_unknown"], bool)
    asyncio.run(check())


def test_evidence_pack_carries_the_screening_fields_and_definitions():
    """Contract §13: the pack, not just the API, must state the screening status."""
    import io
    import json
    import zipfile

    from litsearch.bundle import build_evidence_pack

    state = demo_state()
    with zipfile.ZipFile(io.BytesIO(build_evidence_pack(state))) as archive:
        handoff = archive.read("AGENT_HANDOFF.md").decode("utf-8")
        screening = json.loads(archive.read("screening.json").decode("utf-8"))
    for field in ("screening_status", "threshold_calibrated", "requires_manual_review",
                  "full_text_review_completed"):
        assert field in screening
        assert field in handoff
    assert "## Screening status" in handoff
    assert "Backwards compatibility" in handoff
    assert screening["screening_status"] == "not_started"
    assert screening["requires_manual_review"] is True
    assert "preliminary" in handoff.lower()


def test_screening_section_documents_definitions_and_compatibility():
    state = demo_state()
    text = screening_section(state)
    for phrase in ("screening_status", "threshold_calibrated", "requires_manual_review",
                   "full_text_review_completed", "preliminary", "v0.9.0"):
        assert phrase in text


def test_full_text_exclusion_reason_list_is_still_the_documented_one():
    """The UI builds its dropdown from this list; it must not drift."""
    assert FULL_TEXT_EXCLUSION_REASONS[0].startswith("全文无法获取")
    assert len(FULL_TEXT_EXCLUSION_REASONS) == 8
