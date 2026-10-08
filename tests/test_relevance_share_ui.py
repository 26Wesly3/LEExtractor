"""Task 2: an uncalibrated run must not present a "relevant share".

v0.9.2 removed the fixed ``0.15`` cut-off from the expansion loop, which was
right, but the UI kept rendering ``relevant_count / count`` as a percentage. With
``relevant_count`` now permanently 0 that column reads **"0% relevant"** — a
claim that nothing is relevant, made by a run that classified nothing at all.

The rule this file enforces: a relevance share is a statement that papers were
*classified*, so it may only appear when a human-fitted threshold is present and
still describes the scores on screen. Everything else shows counts, which are
observations, instead.
"""

import re
from pathlib import Path

from litsearch.models import Paper
from litsearch.search import LiteratureReviewWorkflow, ReviewState

APP = Path("app.py")


def make(pid, title="plant phenotyping study"):
    return Paper(id=pid, title=title, doi=pid, abstract=title)


class StubSources:
    snowball_delay = 0

    def __init__(self, papers=(), references=None):
        self.papers = list(papers)
        self.references = references or {}
        self.last_search_manifest = {}

    def is_canceled(self):
        return False

    def search_all_sources(self, *a, **k):
        return list(self.papers)

    def get_references(self, seed, limit=100):
        return list(self.references.get(seed.canonical_id, []))

    def get_citations(self, seed, limit=100):
        return []


def workflow(sources):
    wf = LiteratureReviewWorkflow(sources)
    wf.downloader = None
    return wf


def app_module():
    import app  # imported lazily: app.py boots Streamlit on import

    return app


# ---------------------------------------------------------------------------
# The gate itself
# ---------------------------------------------------------------------------


def test_uncalibrated_state_offers_no_relevance_share():
    share = app_module().calibrated_relevant_share(ReviewState("t", "d"))
    assert share["available"] is False
    assert share["threshold"] is None
    assert "calibration" in share["reason"].lower()


def test_a_calibration_that_cannot_prove_validity_offers_no_share():
    """'I cannot tell' must not become a displayed percentage."""
    state = ReviewState("t", "d")

    class Opaque:
        usable = True
        reliable = True
        threshold = 0.42

    state.calibration = Opaque()
    share = app_module().calibrated_relevant_share(state)
    assert share["available"] is False, (
        "a threshold that cannot be re-validated was used to label papers"
    )


def test_an_unusable_calibration_offers_no_share():
    state = ReviewState("t", "d")

    class NotUsable:
        usable = False
        reliable = False
        threshold = None

    state.calibration = NotUsable()
    assert app_module().calibrated_relevant_share(state)["available"] is False


def test_a_calibration_stamped_for_the_current_context_offers_a_share():
    state = ReviewState("t", "d")
    state.score_context_id = "ctx-1"

    class Stamped:
        usable = True
        reliable = False
        threshold = 0.42

    state.calibration = Stamped()
    assert state.calibration_context_id == "ctx-1", "assignment stamps the context"
    share = app_module().calibrated_relevant_share(state)
    assert share["available"] is True
    assert share["threshold"] == 0.42
    assert share["reliable"] is False


def test_a_calibration_from_another_context_offers_no_share():
    state = ReviewState("t", "d")
    state.score_context_id = "ctx-1"

    class Stamped:
        usable = True
        reliable = True
        threshold = 0.42

    state.calibration = Stamped()
    state.score_context_id = "ctx-2"  # the scale moved on
    share = app_module().calibrated_relevant_share(state)
    assert share["available"] is False


def test_a_real_calibration_record_is_gated_by_is_valid_for():
    """With a real record, validity is decided by the record, not by the stamp.

    Built as a real :class:`CalibrationRecord` rather than a stand-in, because
    the point is that the gate consults the record's own ``is_valid_for``.
    """
    from litsearch.filters import CalibrationRecord, corpus_hash

    state = ReviewState("topic", "deep learning for plant phenotyping")
    papers = [make(f"10.1/p{i}") for i in range(4)]
    state.search_papers = papers
    state.score_context_id = "ctx-real"
    digest = corpus_hash(papers)

    record = CalibrationRecord(
        threshold=0.42,
        status="calibrated",
        corpus_hash=digest,
        query="deep learning for plant phenotyping",
        evaluation={"separation": True, "score_context_id": "ctx-real"},
    )
    state.calibration = record
    share = app_module().calibrated_relevant_share(state)
    assert share["available"] is True
    assert share["threshold"] == 0.42

    # A different corpus invalidates it, and the share disappears.
    state.search_papers = papers[:2]
    assert app_module().calibrated_relevant_share(state)["available"] is False


# ---------------------------------------------------------------------------
# The rendered table
# ---------------------------------------------------------------------------


def test_uncalibrated_snowball_does_not_show_a_relevance_rate():
    """The rendered column set must not contain a relevance share."""
    source = APP.read_text(encoding="utf-8")
    assert "相关率" not in source, "the uncalibrated 'relevant share' label is back"
    assert '"Relevant share"' not in source, "the uncalibrated share label is back"


def test_uncalibrated_expansion_reports_counts_not_a_percentage():
    """The round table's columns are observations unless a calibration exists."""
    source = APP.read_text(encoding="utf-8")
    # The share may only be added inside the `if share["available"]` branch.
    guard = re.search(
        r'if share\["available"\]:\s*\n(.*?)\n\s*rows\.append\(row\)',
        source, re.S,
    )
    assert guard, "the calibrated-share guard is missing from the round table"
    assert "relevant_count" in guard.group(1), (
        "the share must be computed inside the guard, not unconditionally"
    )
    # And the unconditional row must not mention it.
    unconditional = re.search(r"row = \{(.*?)\n            \}", source, re.S)
    assert unconditional, "could not find the unconditional row literal"
    assert "relevant_count" not in unconditional.group(1)


def test_the_caption_explains_why_no_share_is_shown():
    source = APP.read_text(encoding="utf-8")
    assert "未标定，因此不显示" in source
    assert "No calibration, so no relevant share is shown" in source


def test_no_magic_threshold_was_reintroduced_to_fill_the_ui():
    """No fixed cut-off may stand in for a calibration."""
    source = APP.read_text(encoding="utf-8")
    for magic in ("0.15", "0.2)", "0.5)"):
        assert magic not in source, f"{magic} looks like a reintroduced cut-off"
    assert "SCORE_VERSION" in source, "the validity check must pin the ranker version"


def test_the_expansion_loop_still_labels_no_papers_itself():
    """The engine must not classify; only a human threshold may."""
    from litsearch.snowball import SnowballRound

    assert SnowballRound(round_number=1, source_papers=[], new_papers=[],
                         direction="both").relevant_count == 0


def test_the_help_text_separates_counts_from_classification():
    """The caption must point at counts as the reliable part."""
    source = APP.read_text(encoding="utf-8")
    for phrase in ("去重新增", "边际变化", "标定"):
        assert phrase in source
