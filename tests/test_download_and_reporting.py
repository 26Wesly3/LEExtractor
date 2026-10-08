from types import SimpleNamespace
from unittest.mock import Mock

from litsearch.downloader import PaperDownloader
from litsearch.identifiers import PaperIdentifiers
from litsearch.models import Paper
from litsearch.prisma import PRISMATracker, ScreeningDecision, ScreeningStage


def test_batch_download_uses_existing_method_and_rejects_html_even_with_pdf_mime(tmp_path):
    downloader = PaperDownloader(str(tmp_path))
    downloader._candidate_urls = Mock(return_value=iter(["https://example.org/wrong.pdf", "https://example.org/right.pdf"]))
    valid_pdf = b"%PDF-1.7\n" + b"x" * 100 + b"\n%%EOF\n"
    responses = [SimpleNamespace(status_code=200, headers={"content-type": "application/pdf"}, content=b"<html>error</html>" * 1000),
                 SimpleNamespace(status_code=200, headers={"content-type": "application/octet-stream"}, content=valid_pdf)]
    downloader._session.get = Mock(side_effect=responses)
    paper = Paper(id="paper", title="test paper")
    results = downloader.batch_download([paper], delay_seconds=0)
    assert results[0][1]
    assert len(list(tmp_path.glob("*.pdf"))) == 1
    assert downloader._session.get.call_count == 2
    assert list(tmp_path.glob("*.pdf"))[0].read_bytes() == valid_pdf


def test_arxiv_is_taken_from_own_metadata_and_never_abstract_mentions():
    paper = Paper(id="hash", title="a paper", abstract="We compare against arxiv:1706.03762")
    assert PaperDownloader._extract_arxiv_id(paper) is None
    paper.identifiers = PaperIdentifiers(arxiv_id="1810.04805")
    assert PaperDownloader._extract_arxiv_id(paper) == "1810.04805"


def test_unattempted_full_text_and_not_retrieved_are_counted_separately():
    tracker = PRISMATracker()
    tracker.add_papers([Paper(id=str(i), title=f"paper {i}") for i in range(4)])
    for i in range(4):
        tracker.screen_paper(str(i), ScreeningDecision.ACCEPT)
    tracker.mark_full_text_retrieved("0", False)
    tracker.mark_full_text_retrieved("1", True)
    tracker.screen_paper("1", ScreeningDecision.REJECT, "wrong outcome", ScreeningStage.FULL_TEXT)
    tracker.mark_full_text_retrieved("2", True)
    tracker.screen_paper("2", ScreeningDecision.ACCEPT, stage=ScreeningStage.FULL_TEXT)
    report = tracker.generate_report()
    assert report.reports_not_retrieved == 1
    assert report.reports_pending_retrieval == 1
    assert report.full_text_assessed == 2
    assert report.full_text_excluded == 1
    assert report.studies_included == 1
    assert report.full_text_exclusion_reasons == {"wrong outcome": 1}
    assert report.reports_sought == report.reports_not_retrieved + report.reports_pending_retrieval + report.full_text_assessed
    tracker.mark_full_text_retrieved("0", True)
    assert tracker.records["0"].full_text_decision == ScreeningDecision.PENDING


def test_duplicate_registration_conserves_identification_counts():
    tracker = PRISMATracker()
    paper = Paper(id="10.1/a", title="same", doi="10.1/a")
    tracker.add_papers([paper, paper])
    report = tracker.generate_report()
    assert report.database_results == 2
    assert report.duplicates_removed == 1
    assert report.records_after_dedup == 1


def test_title_rejection_cannot_leave_a_stale_full_text_inclusion():
    tracker = PRISMATracker()
    tracker.add_papers([Paper(id="x", title="title")])
    tracker.screen_paper("x", ScreeningDecision.ACCEPT)
    tracker.screen_paper("x", ScreeningDecision.ACCEPT, stage=ScreeningStage.FULL_TEXT)
    tracker.screen_paper("x", ScreeningDecision.REJECT)
    assert tracker.get_included_papers() == []
    assert tracker.generate_report().full_text_assessed == 0
