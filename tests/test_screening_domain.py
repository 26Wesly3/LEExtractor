"""Task 5 — the screening domain must not live behind the MCP adapter (v0.9.3).

The defect this guards (static, verified on the tree before the move): the
screening/review-status *domain* — ``screening_facts``,
``SCREENING_STATUS_VALUES``, the published field definitions,
``paper_screening_status`` and the calibration gate — was implemented inside
``litsearch/server.py``, the MCP adapter module that imports ``fastmcp`` at
module level. ``litsearch/bundle.py`` and ``app.py`` therefore had to reach
*through* the protocol layer to read domain truth, so the export bundle and the
Streamlit UI could not state a review's screening status without acquiring
fastmcp. That is an inverted dependency: a domain concept lived behind the MCP
protocol layer.

The fix moves the implementation verbatim into ``litsearch/screening.py``
(no ``fastmcp``, no ``litsearch.server``), keeps ``litsearch.server``
re-exporting the same names for existing callers, and points ``app.py`` and
``litsearch/bundle.py`` at the domain module.

Asserted here:
* **dependency direction** — by AST, so comments and docstrings cannot satisfy
  it — plus a fresh interpreter proving that importing the domain and the
  bundle never loads ``fastmcp``;
* **one implementation** — the adapter re-exports the very objects the UI and
  the bundle import;
* **the v0.9.2 state machine survives the move** — ``PENDING`` and ``MAYBE``
  are both actionable, a stage is complete only when nothing at or before it is
  actionable, ``full_text_review_completed`` needs the full-text stage to have
  run *and* a fully decided title/abstract stage, and
  ``requires_manual_review`` is true whenever the review is incomplete or
  anything is actionable/flagged;
* **the MCP surface is unchanged** — exactly the 12 tools with the same
  schemas, listed from a live client.
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import subprocess
import sys
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: The names contract §2 freezes; ``litsearch.server`` must re-export each one.
DOMAIN_EXPORTS = (
    "SCREENING_STATUS_VALUES",
    "SCREENING_FIELD_DEFINITIONS",
    "SCREENING_COMPATIBILITY_NOTE",
    "screening_facts",
    "screening_section",
    "paper_screening_status",
    "threshold_calibrated",
    "calibration_summary",
)

#: ``litsearch/server.py`` is the only module allowed to know about fastmcp.
MCP_ADAPTER = "litsearch/server.py"


def _import_targets(path: Path) -> set[str]:
    """Every module path this file imports, at any nesting depth.

    AST-based on purpose: a grep for ``fastmcp`` matches the docstrings that
    explain why the dependency is forbidden, and matches nothing if the import
    is written as ``from . import server``.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                # Relative to the ``litsearch`` package for every file below.
                base = f"litsearch.{base}" if base else "litsearch"
            targets.add(base)
            # ``from litsearch import server`` / ``from . import server``
            targets.update(f"{base}.{alias.name}" for alias in node.names)
    return targets


def _module_level_imports(path: Path) -> set[str]:
    """Only the imports executed by ``import <module>`` itself."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    targets: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            targets.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = f"litsearch.{base}" if base else "litsearch"
            targets.add(base)
    return targets


def _is_forbidden(module: str) -> bool:
    return module == "fastmcp" or module.startswith("fastmcp.") or (
        module == "litsearch.server" or module.startswith("litsearch.server.")
    )


# ---------------------------------------------------------------------------
# Dependency direction — static
# ---------------------------------------------------------------------------


def test_screening_domain_never_imports_fastmcp_or_the_mcp_adapter():
    """The exact assertion: no import node in screening.py names either."""
    targets = _import_targets(ROOT / "litsearch" / "screening.py")
    offending = sorted(t for t in targets if _is_forbidden(t))
    assert offending == [], (
        f"litsearch/screening.py imports {offending}; the domain must not depend "
        f"on the MCP protocol layer (fastmcp / litsearch.server)"
    )


def test_screening_domain_imports_nothing_at_module_import_time():
    """Importing the domain must not drag in the package (or the MCP stack).

    Every import in ``litsearch/screening.py`` is function-local, so
    ``import litsearch.screening`` cannot transitively reach ``fastmcp``.
    """
    targets = _module_level_imports(ROOT / "litsearch" / "screening.py")
    assert targets == set(), f"module-level imports appeared: {sorted(targets)}"


@pytest.mark.parametrize("relative", ["litsearch/bundle.py", "app.py"])
def test_consumers_import_the_domain_not_the_mcp_adapter(relative):
    targets = _import_targets(ROOT / relative)
    offending = sorted(t for t in targets if _is_forbidden(t))
    assert offending == [], (
        f"{relative} imports {offending}; the export bundle and the UI must read "
        f"domain truth from litsearch.screening, not through the MCP adapter"
    )
    assert "litsearch.screening" in targets, (
        f"{relative} no longer imports the single screening implementation"
    )


def test_only_the_mcp_adapter_knows_about_fastmcp():
    offenders = sorted(
        path.name
        for path in (ROOT / "litsearch").glob("*.py")
        if f"litsearch/{path.name}" != MCP_ADAPTER
        and any(_is_forbidden(t) for t in _import_targets(path))
    )
    assert offenders == [], f"modules outside {MCP_ADAPTER} import fastmcp: {offenders}"


def test_server_still_reexports_the_domain_names():
    targets = _import_targets(ROOT / "litsearch" / "server.py")
    assert "litsearch.screening" in targets, (
        "litsearch/server.py must import the domain module so "
        "`from litsearch.server import screening_facts` keeps working"
    )


# ---------------------------------------------------------------------------
# Dependency direction — runtime
# ---------------------------------------------------------------------------


def test_domain_and_bundle_import_without_fastmcp():
    """A fresh interpreter: importing the domain + bundle must not load fastmcp."""
    program = (
        "import sys\n"
        "import litsearch.screening\n"
        "import litsearch.bundle\n"
        "print('FASTMCP=' + str('fastmcp' in sys.modules))\n"
        "print('SERVER=' + str('litsearch.server' in sys.modules))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", program],
        cwd=str(ROOT), capture_output=True, text=True, timeout=180,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
    )
    assert proc.returncode == 0, proc.stderr[-800:]
    assert "FASTMCP=False" in proc.stdout, (
        "importing litsearch.screening/litsearch.bundle loaded fastmcp; the domain "
        f"path still depends on the MCP layer:\n{proc.stdout}"
    )
    assert "SERVER=False" in proc.stdout, proc.stdout


# ---------------------------------------------------------------------------
# One implementation, shared by the adapter, the UI and the bundle
# ---------------------------------------------------------------------------


def test_adapter_reexports_the_same_objects_as_the_domain():
    import litsearch.screening as domain
    import litsearch.server as adapter

    for name in DOMAIN_EXPORTS:
        assert hasattr(domain, name), f"litsearch.screening is missing {name}"
        assert getattr(adapter, name) is getattr(domain, name), (
            f"litsearch.server.{name} is not the domain object — two implementations "
            f"of the screening status would drift apart"
        )


def test_bundle_reads_the_domain_objects():
    import litsearch.bundle as bundle
    import litsearch.screening as domain

    assert bundle.screening_facts is domain.screening_facts
    assert bundle.screening_section is domain.screening_section
    assert bundle.paper_screening_status is domain.paper_screening_status


def test_contract_shapes_are_unchanged_by_the_move():
    from litsearch.screening import (
        SCREENING_COMPATIBILITY_NOTE,
        SCREENING_FIELD_DEFINITIONS,
        SCREENING_STATUS_VALUES,
    )

    assert isinstance(SCREENING_STATUS_VALUES, tuple)
    assert SCREENING_STATUS_VALUES == (
        "not_started", "preliminary_included", "final_included", "excluded",
    )
    assert isinstance(SCREENING_FIELD_DEFINITIONS, dict)
    assert set(SCREENING_FIELD_DEFINITIONS) == {
        "screening_status", "threshold_calibrated", "requires_manual_review",
        "full_text_review_completed",
    }
    assert isinstance(SCREENING_COMPATIBILITY_NOTE, str) and "v0.9.0" in SCREENING_COMPATIBILITY_NOTE


# ---------------------------------------------------------------------------
# The v0.9.2 state machine, exercised through the domain module
# ---------------------------------------------------------------------------


def _papers(count=3):
    from litsearch.models import Author, Paper

    return [
        Paper(id=f"10.9/s{i}", doi=f"10.9/s{i}", title=f"Wheat phenotyping study {i}",
              abstract="wheat image phenotyping", year=2020 + i, source="fake",
              authors=[Author(name="A. Author")], citation_count=i)
        for i in range(count)
    ]


def _state(count=3):
    from litsearch.search import ReviewState

    state = ReviewState("wheat phenotyping", "wheat image phenotyping")
    papers = _papers(count)
    state.search_papers = papers
    state.prisma.add_papers(papers)
    return state, papers


def test_pending_and_maybe_are_both_actionable():
    from litsearch.prisma import ScreeningDecision
    from litsearch.screening import screening_facts

    state, papers = _state(3)
    facts = screening_facts(state)
    assert facts["title_abstract_pending"] == 3
    assert facts["title_stage_complete"] is False

    for paper in papers[:2]:
        state.prisma.screen_paper(paper.id, ScreeningDecision.ACCEPT)
    assert screening_facts(state)["title_abstract_pending"] == 1

    # MAYBE means "a human must look again": it stays actionable, exactly like
    # PENDING, so the stage cannot call itself complete.
    state.prisma.screen_paper(papers[0].id, ScreeningDecision.MAYBE)
    facts = screening_facts(state)
    assert facts["title_abstract_pending"] == 2
    assert facts["title_abstract_accepted"] == 1
    assert facts["title_stage_complete"] is False
    assert facts["full_text_review_completed"] is False
    assert facts["requires_manual_review"] is True
    assert facts["screening_status"] != "final_included"


def test_maybe_alone_blocks_the_stage_from_being_complete():
    from litsearch.prisma import ScreeningDecision
    from litsearch.screening import screening_facts

    state, papers = _state(2)
    state.prisma.screen_paper(papers[0].id, ScreeningDecision.ACCEPT)
    state.prisma.screen_paper(papers[1].id, ScreeningDecision.MAYBE)

    facts = screening_facts(state)
    assert facts["title_stage_complete"] is False
    assert facts["requires_manual_review"] is True
    # Work has progressed, but the review is unfinished: preliminary, never final.
    assert facts["screening_status"] == "preliminary_included"


def test_full_text_completion_needs_the_full_text_stage_and_a_decided_title_stage():
    from litsearch.prisma import ScreeningDecision, ScreeningStage
    from litsearch.screening import screening_facts

    state, papers = _state(2)
    for paper in papers:
        state.prisma.screen_paper(paper.id, ScreeningDecision.ACCEPT)

    facts = screening_facts(state)
    assert facts["title_stage_complete"] is True
    assert facts["full_text_review_completed"] is False  # the stage never ran
    assert facts["screening_status"] == "preliminary_included"

    state.prisma.screen_paper(papers[0].id, ScreeningDecision.ACCEPT,
                              stage=ScreeningStage.FULL_TEXT)
    assert screening_facts(state)["full_text_review_completed"] is False  # one still open

    state.prisma.screen_paper(papers[1].id, ScreeningDecision.ACCEPT,
                              stage=ScreeningStage.FULL_TEXT)
    facts = screening_facts(state)
    assert facts["full_text_review_completed"] is True
    assert facts["screening_status"] == "final_included"
    assert facts["requires_manual_review"] is False

    # A finished full-text stage with no inclusion is "excluded", not "not_started".
    empty, empty_papers = _state(2)
    for paper in empty_papers:
        empty.prisma.screen_paper(paper.id, ScreeningDecision.REJECT)
    exhausted = screening_facts(empty)
    assert exhausted["screening_status"] == "excluded"
    assert exhausted["title_stage_complete"] is True


def test_an_unfinished_review_is_never_reported_as_completed_or_final():
    """The exact v0.9.1 contradiction: one paper finished, others undecided."""
    from litsearch.prisma import ScreeningDecision, ScreeningStage
    from litsearch.screening import screening_facts

    state, papers = _state(3)
    state.prisma.screen_paper(papers[0].id, ScreeningDecision.ACCEPT)
    state.prisma.screen_paper(papers[0].id, ScreeningDecision.ACCEPT,
                              stage=ScreeningStage.FULL_TEXT)

    facts = screening_facts(state)
    assert facts["screening_status"] != "final_included"
    assert facts["full_text_review_completed"] is False
    assert facts["requires_manual_review"] is True
    assert facts["title_abstract_pending"] == 2


def test_requires_manual_review_is_true_when_a_record_is_flagged():
    from litsearch.prisma import ScreeningDecision, ScreeningStage
    from litsearch.screening import screening_facts

    state, papers = _state(1)
    state.prisma.screen_paper(papers[0].id, ScreeningDecision.ACCEPT)
    state.prisma.screen_paper(papers[0].id, ScreeningDecision.ACCEPT,
                              stage=ScreeningStage.FULL_TEXT)
    facts = screening_facts(state)
    assert facts["full_text_review_completed"] is True
    assert facts["requires_manual_review"] is False

    next(iter(state.prisma.records.values())).requires_manual_review = True
    facts = screening_facts(state)
    assert facts["flagged_for_manual_review"] == 1
    assert facts["requires_manual_review"] is True


def test_an_empty_session_is_not_started_not_excluded():
    from litsearch.screening import screening_facts

    state, _ = _state(0)
    facts = screening_facts(state)
    assert facts["screening_status"] == "not_started"
    assert facts["requires_manual_review"] is True


def test_calibration_gate_unchanged():
    from litsearch.filters import CalibrationRecord
    from litsearch.screening import calibration_summary, threshold_calibrated

    state, _ = _state(1)
    assert threshold_calibrated(state) is False
    assert calibration_summary(state)["present"] is False

    state.calibration = CalibrationRecord(status="insufficient_labels", threshold=None)
    assert threshold_calibrated(state) is False

    state.calibration = CalibrationRecord(status="calibrated", threshold=0.42)
    assert threshold_calibrated(state) is True
    assert calibration_summary(state)["threshold"] == 0.42

    invalidated = CalibrationRecord(status="calibrated", threshold=0.42,
                                    evaluation={"invalidated_reason": "corpus changed"})
    state.calibration = invalidated
    assert threshold_calibrated(state) is False


def test_per_paper_status_uses_the_four_contract_values():
    from litsearch.prisma import ScreeningDecision, ScreeningStage
    from litsearch.screening import paper_screening_status

    state, papers = _state(2)
    records = list(state.prisma.records.values())
    assert paper_screening_status(records[0]) == "not_started"

    state.prisma.screen_paper(papers[0].id, ScreeningDecision.ACCEPT)
    assert paper_screening_status(records[0]) == "preliminary_included"

    state.prisma.screen_paper(papers[0].id, ScreeningDecision.ACCEPT,
                              stage=ScreeningStage.FULL_TEXT)
    assert paper_screening_status(records[0]) == "final_included"

    state.prisma.screen_paper(papers[0].id, ScreeningDecision.PENDING,
                              stage=ScreeningStage.FULL_TEXT)
    assert paper_screening_status(records[0]) == "preliminary_included"

    state.prisma.screen_paper(papers[0].id, ScreeningDecision.REJECT)
    assert paper_screening_status(records[0]) == "excluded"


def test_screening_section_still_documents_the_fields():
    from litsearch.screening import screening_section

    state, _ = _state(1)
    text = screening_section(state)
    for phrase in ("## Screening status", "screening_status", "threshold_calibrated",
                   "requires_manual_review", "full_text_review_completed",
                   "preliminary", "Backwards compatibility", "v0.9.0"):
        assert phrase in text


# ---------------------------------------------------------------------------
# The MCP surface is unchanged
# ---------------------------------------------------------------------------

#: Tool name -> its request schema properties, frozen from the pre-move server.
EXPECTED_TOOL_SCHEMAS = {
    "literature_review_search": (
        "auto_screen", "calibration", "max_papers", "research_direction",
        "snowball_rounds", "topic", "years_back",
    ),
    "snowball_from_seeds": (
        "max_per_direction", "max_rounds", "paper_dois", "research_direction",
    ),
    "find_similar_papers": ("paper_dois", "top_k"),
    "download_paper": ("paper_doi_or_title",),
    "batch_download": ("dois",),
    "plan_search_strategy": ("research_direction", "topic", "years_back"),
    "search_literature": ("max_papers", "research_direction", "topic", "years_back"),
    "explain_paper": ("paper_id", "session"),
    "build_research_landscape": ("include_relations", "session"),
    "candidate_research_questions": ("max_questions", "session"),
    "get_evidence_path": ("session", "source_id", "target_id"),
    "export_research_bundle": ("session",),
}


def test_mcp_still_registers_the_twelve_tools_with_their_schemas():
    from fastmcp import Client

    from litsearch.server import mcp

    async def list_schemas():
        async with Client(mcp) as client:
            tools = await client.list_tools()
        return {
            tool.name: tuple(sorted((tool.inputSchema or {}).get("properties", {})))
            for tool in tools
        }

    schemas = asyncio.run(list_schemas())
    assert len(schemas) == 12, f"expected 12 MCP tools, listed {sorted(schemas)}"
    assert set(schemas) == set(EXPECTED_TOOL_SCHEMAS)
    assert schemas == EXPECTED_TOOL_SCHEMAS


def test_evidence_pack_writes_the_domain_screening_facts():
    """The pack, built without any MCP client, carries the domain's own output."""
    from litsearch.bundle import build_evidence_pack
    from litsearch.demo import demo_state
    from litsearch.screening import screening_facts

    state = demo_state()
    with zipfile.ZipFile(BytesIO(build_evidence_pack(state))) as archive:
        payload = json.loads(archive.read("screening.json").decode("utf-8"))
        handoff = archive.read("AGENT_HANDOFF.md").decode("utf-8")

    assert payload == screening_facts(state)
    assert "## Screening status" in handoff
    assert payload["screening_status"] == "not_started"
    assert payload["requires_manual_review"] is True
