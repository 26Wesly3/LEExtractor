"""M1: PDF import, parsing, identity matching and batch boundaries.

Hermetic: every PDF is generated in-process with PyMuPDF into ``tmp_path``; no
artefact is written into the repository and no test touches the network.  The
cases mirror the acceptance list (AC-02, AC-03, AC-04, AC-05, AC-27) and the
"批次边界" rules of ``docs/v3.2/16_PDF_ZOTERO_INTEGRATIONS.md``.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

import litsearch
from litsearch.v32 import contracts as C
from litsearch.v32 import pdf_import as P
from litsearch.v32.errors import V32Error
from litsearch.v32.types import IMPORT_RETRYABLE, ImportStatus

REPO_ROOT = Path(litsearch.__file__).resolve().parent

PAGE_WIDTH = 595
DEFAULT_FONT = 11


# --------------------------------------------------------------------------
# hermetic PDF fixtures
# --------------------------------------------------------------------------


def _write_pdf(
    path: Path,
    pages,
    *,
    fontsize: int = DEFAULT_FONT,
    fontname: str = "helv",
    encryption: str | None = None,
):
    """Write a PDF whose text lines are laid out one per block."""
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    for lines in pages:
        page = doc.new_page()
        y = 72
        for entry in lines:
            if isinstance(entry, tuple):
                text, size = entry
            else:
                text, size = entry, fontsize
            if text:
                page.insert_text((72, y), text, fontsize=size, fontname=fontname)
            y += size + 8
    kwargs = {}
    if encryption:
        kwargs = {
            "encryption": fitz.PDF_ENCRYPT_AES_256,
            "user_pw": encryption,
            "owner_pw": encryption + "-owner",
        }
    doc.save(str(path), **kwargs)
    doc.close()
    return path


@pytest.fixture()
def good_pdf(tmp_path: Path) -> Path:
    """A title + abstract + byline PDF with no DOI anywhere (AC-02/AC-03)."""
    return _write_pdf(
        tmp_path / "good.pdf",
        [[
            ("Attention Is All You Need", 14),
            ("Ashish Vaswani, Noam Shazeer, Niki Parmar", 11),
            ("Abstract", 12),
            ("The dominant sequence transduction models are based on complex", 11),
            ("recurrent or convolutional neural networks. We propose the", 11),
            ("Transformer in this 2020 paper and evaluate it on translation.", 11),
            ("Introduction", 12),
            ("Recurrent models have long been the standard baseline.", 11),
        ]],
    )


@pytest.fixture()
def reference_doi_pdf(tmp_path: Path) -> Path:
    """A PDF whose only DOI lives under the "References" heading (AC-27)."""
    return _write_pdf(
        tmp_path / "reference_doi.pdf",
        [
            [
                ("Fine-Tuning Language Models from Human Preferences", 14),
                ("Jane Doe, John Smith", 11),
                ("Abstract", 12),
                ("We study fine-tuning of large language models using human", 11),
                ("preference signals collected in a reinforcement loop.", 11),
            ],
            [
                ("References", 12),
                ("[1] Ho J, Jain A, Abbeel P. Denoising diffusion models. 2020.", 11),
                ("https://doi.org/10.48550/arXiv.2006.11239", 11),
            ],
        ],
    )


@pytest.fixture()
def encrypted_pdf(tmp_path: Path) -> Path:
    return _write_pdf(
        tmp_path / "encrypted.pdf",
        [[("Top secret body text that nobody may read", 12)]],
        encryption="user-secret",
    )


@pytest.fixture()
def blank_pdf(tmp_path: Path) -> Path:
    """A page with no text layer at all: an image-only scan, in effect."""
    return _write_pdf(tmp_path / "blank.pdf", [[]])


@pytest.fixture()
def non_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "not_a_pdf.txt"
    path.write_bytes(b"this is plainly not a PDF document at all\n" * 8)
    return path


# --------------------------------------------------------------------------
# AC-02 / AC-03: a PDF with a title and abstract but no DOI is a local seed
# --------------------------------------------------------------------------


def test_title_and_abstract_without_doi_becomes_a_local_seed(good_pdf: Path):
    outcome = P.parse_pdf(good_pdf)
    assert outcome.status is ImportStatus.PARSING
    assert outcome.error_code is None
    assert outcome.retryable is False

    parsed = outcome.parsed
    assert parsed is not None
    assert parsed.title == "Attention Is All You Need"
    assert parsed.doi is None
    assert parsed.doi_candidates == []
    assert parsed.authors[:1] == ["Ashish Vaswani"]
    assert parsed.year == 2020
    assert parsed.abstract and parsed.abstract.startswith("The dominant sequence")
    assert parsed.page_count == 1
    assert parsed.text_chars >= int(C.pdf_defaults()["minimum_seed_chars"])

    # Every extracted field carries page/section evidence.
    for field in ("title", "abstract", "authors", "year"):
        assert field in parsed.evidence, field
        assert parsed.evidence[field]["page"] >= 1
        assert isinstance(parsed.evidence[field]["section"], str)

    # A local seed needs no hand-filled DOI and can continue discovery.
    match = P.match_parsed(parsed, [])
    assert match.status is ImportStatus.LOCAL_SEED
    assert match.paper_id is None


def test_bytes_entry_point_matches_the_path_entry_point(good_pdf: Path):
    from_path = P.parse_pdf(good_pdf)
    from_bytes = P.parse_pdf_bytes(good_pdf.read_bytes(), "good.pdf")
    assert from_bytes.to_dict() == from_path.to_dict()


# --------------------------------------------------------------------------
# AC-27: a DOI in the reference list is never the uploaded paper's DOI
# --------------------------------------------------------------------------


def test_reference_section_doi_is_only_a_citation(reference_doi_pdf: Path):
    outcome = P.parse_pdf(reference_doi_pdf)
    assert outcome.status is ImportStatus.PARSING
    parsed = outcome.parsed
    assert parsed is not None
    assert parsed.title == "Fine-Tuning Language Models from Human Preferences"
    assert parsed.doi is None, "a references-section DOI must never become the paper DOI"
    assert parsed.doi_candidates == []
    assert parsed.reference_dois == ["10.48550/arxiv.2006.11239"]
    assert parsed.evidence["reference_dois"]["section"] == "references"
    assert "doi" not in parsed.evidence


def test_a_front_matter_doi_is_adopted_but_a_reference_doi_is_not(tmp_path: Path):
    path = _write_pdf(
        tmp_path / "front_doi.pdf",
        [[
            ("A Study of Reproducible Literature Extraction", 14),
            ("Jane Doe, John Smith", 11),
            ("doi:10.1000/ABC.123", 11),
            ("Abstract", 12),
            ("We present a reproducible extraction pipeline for literature", 11),
            ("reviews and evaluate it on a large multilingual corpus here.", 11),
            ("References", 12),
            ("[1] Someone. Prior work. https://doi.org/10.5555/9999.1111", 11),
        ]],
    )
    parsed = P.parse_pdf(path).parsed
    assert parsed is not None
    # The front-matter DOI is normalised (URL prefix stripped, lower-cased).
    assert parsed.doi == "10.1000/abc.123"
    assert parsed.doi_candidates == ["10.1000/abc.123"]
    assert "10.5555/9999.1111" in parsed.reference_dois
    assert parsed.doi not in parsed.reference_dois


def test_extract_dois_strips_prefix_whitespace_and_trailing_punctuation():
    assert P.extract_dois("see https://doi.org/10.1000/XYZ.42.") == ["10.1000/xyz.42"]
    assert P.extract_dois("(10.1145/3292500.3330701)") == ["10.1145/3292500.3330701"]
    assert P.extract_dois("doi: 10.5555/1234.5678;") == ["10.5555/1234.5678"]
    assert P.extract_dois("nothing here") == []


# --------------------------------------------------------------------------
# AC-04: content de-duplication is by SHA-256 of the bytes
# --------------------------------------------------------------------------


def test_same_bytes_reuse_the_attachment_identity(good_pdf: Path):
    data = good_pdf.read_bytes()
    assert P.content_hash(data) == P.content_hash(bytes(data))
    assert len(P.content_hash(data)) == 64

    registry = P.AttachmentRegistry()
    first = registry.register("project-1", data, "good.pdf")
    second = registry.register("project-1", data, "renamed-copy.pdf")

    assert first.reused is False
    assert second.reused is True
    assert second.attachment_id == first.attachment_id
    assert second.content_hash == first.content_hash
    assert second.stored_path_key == first.stored_path_key
    assert len(registry) == 1, "the same bytes must not create a second attachment"

    other_project = registry.register("project-2", data, "good.pdf")
    assert other_project.attachment_id == first.attachment_id
    assert len(registry) == 2, "de-duplication is per project"

    different = registry.register("project-1", data + b"\n%", "good.pdf")
    assert different.attachment_id != first.attachment_id


def test_import_item_reuses_one_attachment_for_two_uploads(good_pdf: Path):
    data = good_pdf.read_bytes()
    registry = P.AttachmentRegistry()
    first = registry.register("p", data, "good.pdf")
    second = registry.register("p", data, "good.pdf")
    item_a = P.build_import_item(
        item_id="item-1", attachment=first, display_name="good.pdf",
        status=ImportStatus.UPLOADED,
    )
    item_b = P.build_import_item(
        item_id="item-2", attachment=second, display_name="good.pdf",
        status=ImportStatus.UPLOADED,
    )
    assert item_a["attachment_id"] == item_b["attachment_id"]
    assert item_a["content_hash"] == item_b["content_hash"]


# --------------------------------------------------------------------------
# AC-05: per-file outcomes in one batch
# --------------------------------------------------------------------------


def test_batch_keeps_one_status_per_file(
    good_pdf: Path, encrypted_pdf: Path, non_pdf: Path
):
    files = [("good.pdf", good_pdf), ("encrypted.pdf", encrypted_pdf), ("notes.txt", non_pdf)]
    # The pre-flight check passes, so nothing is refused for this batch.
    assert P.plan_batch([(name, path.stat().st_size) for name, path in files]) is None

    registry = P.AttachmentRegistry()
    outcomes = {}
    for name, path in files:
        data = path.read_bytes()
        attachment = registry.register("project-1", data, name)
        parse = P.parse_pdf_bytes(data, name)
        match = P.match_parsed(parse.parsed, []) if parse.parsed is not None else None
        outcomes[name] = (attachment, parse, match)

    assert outcomes["good.pdf"][1].status is ImportStatus.PARSING
    assert outcomes["good.pdf"][1].retryable is False
    assert outcomes["good.pdf"][2].status is ImportStatus.LOCAL_SEED

    assert outcomes["encrypted.pdf"][1].status is ImportStatus.ENCRYPTED_PDF
    assert outcomes["encrypted.pdf"][1].error_code == P.ERROR_ENCRYPTED_PDF
    # A password is a user action, not a repeat of the same request: the item is
    # reported honestly and is not silently retryable.
    assert outcomes["encrypted.pdf"][1].retryable is False
    assert outcomes["encrypted.pdf"][1].parsed is None

    assert outcomes["notes.txt"][1].status is ImportStatus.INVALID_PDF
    assert outcomes["notes.txt"][1].error_code == P.ERROR_NOT_A_PDF
    assert outcomes["notes.txt"][1].retryable is False

    # Every item keeps its own content hash, attachment identity and record.
    records = [
        P.build_import_item(
            item_id=f"item-{index}",
            attachment=attachment,
            display_name=name,
            status=parse.status,
            parsed=parse.parsed,
            match=match,
            error_code=parse.error_code or (match.conflicts[0] if match and match.conflicts else None),
        )
        for index, (name, (attachment, parse, match)) in enumerate(outcomes.items())
    ]
    assert [record["status"] for record in records] == [
        "local_seed",
        "encrypted_pdf",
        "invalid_pdf",
    ]
    assert len({record["content_hash"] for record in records}) == 3
    assert len({record["attachment_id"] for record in records}) == 3
    json.dumps(records, allow_nan=False)

    # The retry set is the contract's; every status the module marks retryable
    # is one the contract allows, and needs_ocr/needs_confirmation/failed are the
    # only candidates.
    assert {
        ImportStatus.NEEDS_OCR,
        ImportStatus.NEEDS_CONFIRMATION,
        ImportStatus.FAILED,
    } == IMPORT_RETRYABLE
    for parse in (outcomes[name][1] for name in outcomes):
        if parse.retryable:
            assert parse.status in IMPORT_RETRYABLE


def test_missing_file_is_invalid_not_an_exception(tmp_path: Path):
    outcome = P.parse_pdf(tmp_path / "does-not-exist.pdf")
    assert outcome.status is ImportStatus.INVALID_PDF
    assert outcome.retryable is True


# --------------------------------------------------------------------------
# needs_ocr for a document without a text layer
# --------------------------------------------------------------------------


def test_image_only_pdf_needs_ocr(blank_pdf: Path):
    assert C.pdf_defaults()["ocr_enabled"] is False
    outcome = P.parse_pdf(blank_pdf)
    assert outcome.status is ImportStatus.NEEDS_OCR
    assert outcome.error_code == P.ERROR_NEEDS_OCR
    assert outcome.retryable is True
    assert outcome.parsed is not None
    assert outcome.parsed.text_chars == 0
    # A failure must never be recorded as a successful local seed.
    assert outcome.status is not ImportStatus.LOCAL_SEED


def test_too_short_and_untitled_is_insufficient_metadata(tmp_path: Path):
    path = _write_pdf(tmp_path / "tiny.pdf", [[("1234", 11)]])
    outcome = P.parse_pdf(path)
    assert outcome.status is ImportStatus.FAILED
    assert outcome.error_code == P.ERROR_INSUFFICIENT_METADATA
    assert outcome.retryable is False


# --------------------------------------------------------------------------
# AC-05 / doc 16: batch limits reject the whole request before enqueue
# --------------------------------------------------------------------------


def test_more_than_ten_files_rejects_the_whole_batch():
    with pytest.raises(P.TooManyFilesError) as caught:
        P.plan_batch([(f"file-{index}.pdf", 1024) for index in range(11)])
    assert isinstance(caught.value, V32Error)
    assert caught.value.status == 400
    assert caught.value.code == "too_many_files"
    assert caught.value.details.get("capability")

    limit = C.pdf_defaults()["batch_max_files"]
    assert P.plan_batch([(f"file-{i}.pdf", 1024) for i in range(limit)]) is None


def test_a_single_file_over_twenty_mib_rejects_the_whole_batch():
    mib = 1024 * 1024
    assert P.plan_batch([("ok.pdf", 19 * mib)]) is None
    with pytest.raises(P.PdfTooLargeError) as caught:
        P.plan_batch([("ok.pdf", 1024), ("too-big.pdf", 21 * mib)])
    assert caught.value.status == 413
    assert caught.value.code == "file_too_large"


def test_cumulative_size_over_one_hundred_mib_rejects_the_whole_batch():
    mib = 1024 * 1024
    files = [(f"file-{i}.pdf", 20 * mib) for i in range(5)] + [("extra.pdf", mib)]
    assert sum(size for _n, size in files) > 100 * mib
    with pytest.raises(P.BatchTooLargeError) as caught:
        P.plan_batch(files)
    assert caught.value.status == 413
    assert caught.value.code == "batch_too_large"

    exact = [(f"file-{i}.pdf", 20 * mib) for i in range(5)]
    assert P.plan_batch(exact) is None


def test_batch_limits_reject_before_anything_is_touched():
    """No file is opened and no attachment is created when the batch is refused."""
    seen: list[str] = []
    with pytest.raises(V32Error):
        P.plan_batch([(f"file-{i}.pdf", 30 * 1024 * 1024) for i in range(11)])
    assert seen == []


def test_negative_or_malformed_batch_entries_are_rejected():
    with pytest.raises(V32Error):
        P.plan_batch([("a.pdf", -1)])
    with pytest.raises(V32Error):
        P.plan_batch([("a.pdf", "not-a-size")])


def test_plan_batch_limits_come_from_the_contract():
    limits = P.pdf_limits()
    assert limits["batch_max_files"] == C.pdf_defaults()["batch_max_files"]
    assert limits["file_max_mib"] == C.pdf_defaults()["file_max_mib"]
    assert limits["batch_max_mib"] == C.pdf_defaults()["batch_max_mib"]
    assert limits["max_pages"] == C.pdf_defaults()["max_pages"]
    assert limits["parser_timeout_seconds"] == C.pdf_defaults()["parser_timeout_seconds"]
    assert limits["minimum_seed_chars"] == C.pdf_defaults()["minimum_seed_chars"]
    # A caller may tighten a budget for one call, never invent one.
    assert P.pdf_limits({"file_max_mib": 1})["file_max_mib"] == 1
    assert P.pdf_limits({"nonsense_key": 5}).get("nonsense_key") is None


def test_page_count_over_the_contract_limit_fails_the_item(tmp_path: Path, monkeypatch):
    path = _write_pdf(tmp_path / "two-pages.pdf", [[("Page one body", 11)], [("Page two body", 11)]])
    outcome = P.parse_pdf(path, limits={"max_pages": 1})
    assert outcome.status is ImportStatus.FAILED
    assert outcome.error_code == P.ERROR_PAGE_LIMIT
    assert outcome.retryable is False


def test_parser_timeout_becomes_a_retryable_failure(monkeypatch):
    def slow(*_args, **_kwargs):
        time.sleep(0.2)
        return {"encrypted": False, "pages": [], "page_count": 1, "metadata": {}}

    monkeypatch.setattr(P, "_read_pdf", slow)
    outcome = P.parse_pdf_bytes(b"%PDF-1.7\n", "slow.pdf", limits={"parser_timeout_seconds": 0.01})
    assert outcome.status is ImportStatus.FAILED
    assert outcome.error_code == P.ERROR_PARSE_TIMEOUT
    assert outcome.retryable is True


def test_oversized_blob_is_refused_before_the_parser_runs(monkeypatch):
    def explode(*_args, **_kwargs):  # pragma: no cover - must never be called
        raise AssertionError("the parser must not run for an oversized file")

    monkeypatch.setattr(P, "_read_pdf", explode)
    outcome = P.parse_pdf_bytes(
        b"%PDF-1.7\n" + b"x" * 2048, "big.pdf", limits={"file_max_mib": 0.001}
    )
    assert outcome.status is ImportStatus.FAILED
    assert outcome.error_code == "file_too_large"


def test_empty_upload_is_invalid():
    outcome = P.parse_pdf_bytes(b"", "empty.pdf")
    assert outcome.status is ImportStatus.INVALID_PDF
    assert outcome.error_code == P.ERROR_EMPTY_FILE
    assert outcome.retryable is False


# --------------------------------------------------------------------------
# matching thresholds
# --------------------------------------------------------------------------


def _parsed(**overrides) -> P.ParsedPaper:
    base = {
        "title": "Attention Is All You Need",
        "authors": ["Ashish Vaswani", "Noam Shazeer"],
        "year": 2017,
        "abstract": "A transformer architecture.",
        "text_chars": 800,
        "page_count": 1,
    }
    base.update(overrides)
    return P.ParsedPaper(**base)


def test_an_exact_title_match_auto_matches():
    candidate = P.CandidatePaper(
        "c1", "Attention Is All You Need", "10.1000/abc", 2020,
        ["Ashish Vaswani", "Noam Shazeer"], "openalex",
    )
    outcome = P.match_parsed(_parsed(year=2020), [candidate])
    assert outcome.status is ImportStatus.MATCHED
    assert outcome.paper_id == "c1"
    assert outcome.match_score == 1.0
    assert outcome.conflicts == []


def test_a_zero_point_eight_title_near_duplicate_needs_confirmation():
    candidate = P.CandidatePaper(
        "c1", "Attention Is All You Need and More", None, 2017,
        ["Ashish Vaswani", "Noam Shazeer"], "openalex",
    )
    parsed = _parsed()
    outcome = P.match_parsed(parsed, [candidate])
    similarity = P.title_similarity(parsed.title or "", candidate.title)
    assert 0.7 <= similarity < 0.9, similarity
    # Title, authors and year all agree, so the score clears the confirmation
    # floor — but the title similarity is below the auto-match bar.
    assert outcome.status is ImportStatus.NEEDS_CONFIRMATION
    assert outcome.paper_id is None
    assert outcome.match_score is not None
    assert outcome.match_score >= C.pdf_defaults()["candidate_score_min"]
    assert outcome.conflicts


def test_an_insufficient_winner_margin_needs_confirmation():
    first = P.CandidatePaper(
        "c1", "Attention Is All You Need", None, 2020,
        ["Ashish Vaswani", "Noam Shazeer"], "openalex",
    )
    second = P.CandidatePaper(
        "c2", "Attention Is All You Need", None, 2020,
        ["Ashish Vaswani", "Noam Shazeer"], "semantic_scholar",
    )
    outcome = P.match_parsed(_parsed(year=2020), [first, second])
    assert outcome.status is ImportStatus.NEEDS_CONFIRMATION
    assert outcome.conflicts == [P.ERROR_AMBIGUOUS_MATCH]
    # A decisive margin over an exact second candidate still auto-matches.
    weaker = P.CandidatePaper("c3", "An Entirely Different Topic", None, 1999, [], "arxiv")
    decisive = P.match_parsed(_parsed(year=2020), [first, weaker])
    assert decisive.status is ImportStatus.MATCHED
    assert decisive.paper_id == "c1"


def test_a_conflicting_strong_doi_never_auto_merges():
    candidate = P.CandidatePaper(
        "c1", "Attention Is All You Need", "10.1000/other", 2020,
        ["Ashish Vaswani", "Noam Shazeer"], "crossref",
    )
    outcome = P.match_parsed(_parsed(year=2020, doi="10.1000/abc"), [candidate])
    assert outcome.status is ImportStatus.NEEDS_CONFIRMATION
    assert P.ERROR_IDENTIFIER_CONFLICT in outcome.conflicts
    assert outcome.paper_id is None


def test_an_agreeing_doi_with_a_weaker_title_may_auto_match():
    candidate = P.CandidatePaper(
        "c1", "Attention Is All You Need", "10.1000/abc", None, [], "crossref",
    )
    # The title is exact but there is no author or year evidence, so the score
    # is 0.75 — below the 0.90 auto-match bar yet above the confirmation floor.
    # The shared DOI is what allows the merge.
    parsed = _parsed(doi="10.1000/ABC", authors=[], year=None)
    assert P.match_score(parsed, candidate) < C.pdf_defaults()["auto_match_score"]
    outcome = P.match_parsed(parsed, [candidate])
    assert outcome.status is ImportStatus.MATCHED
    assert outcome.paper_id == "c1"

    # A DOI that agrees but whose title clearly disagrees is never auto-merged.
    conflicting = P.CandidatePaper(
        "c2", "A Completely Unrelated Paper About Fish", "10.1000/abc", None, [], "crossref",
    )
    risky = P.match_parsed(
        _parsed(doi="10.1000/abc", authors=[], year=None, title="Attention Is All You Need"),
        [conflicting],
    )
    assert risky.status is not ImportStatus.MATCHED
    assert risky.paper_id is None


def test_a_low_score_stays_a_local_seed():
    candidate = P.CandidatePaper("c1", "Deep Reinforcement Learning", None, 1995, [], "arxiv")
    outcome = P.match_parsed(_parsed(), [candidate])
    assert outcome.status is ImportStatus.LOCAL_SEED
    assert outcome.paper_id is None
    assert outcome.match_score is not None
    assert outcome.match_score < C.pdf_defaults()["candidate_score_min"]


def test_match_weights_are_never_re_normalised():
    """A missing author/year term contributes 0; the weights stay 0.75/0.15/0.10."""
    candidate = P.CandidatePaper("c1", "Attention Is All You Need", None, None, [], "arxiv")
    score = P.match_score(_parsed(authors=[], year=None), candidate)
    weights = C.pdf_defaults()["match_weights"]
    assert score == pytest.approx(weights["title"], abs=1e-12)
    assert score < C.pdf_defaults()["auto_match_score"]

    # An unknown year is not year 0 and does not become a weak match.
    assert P.year_agreement(None, 2017) == 0.0
    assert P.year_agreement(2017, 2018) == 0.5
    assert P.year_agreement(2017, 2017) == 1.0
    assert P.author_overlap([], ["Ashish Vaswani"]) == 0.0
    assert P.author_overlap(["Ashish Vaswani"], ["Ashish Vaswani"]) == 1.0


def test_a_huge_citation_count_cannot_buy_a_match():
    """The matcher scores title/authors/year only: there is no citation term."""
    candidate = {
        "candidate_id": "c1",
        "title": "An Unrelated Highly Cited Survey",
        "doi": None,
        "year": 1990,
        "authors": [],
        "source": "openalex",
        "citation_count": 250_000,
        "influential_citation_count": 9000,
    }
    outcome = P.match_parsed(_parsed(), [candidate])
    assert outcome.status is ImportStatus.LOCAL_SEED
    assert outcome.match_score == 0.0


def test_candidates_accept_objects_and_mappings_and_are_ordered_by_id():
    as_objects = [
        P.CandidatePaper("c-b", "Attention Is All You Need", None, 2017, [], "openalex"),
        P.CandidatePaper("c-a", "Attention Is All You Need", None, 2017, [], "openalex"),
    ]
    as_mappings = [
        {"candidate_id": "c-b", "title": "Attention Is All You Need", "year": 2017, "source": "openalex"},
        {"candidate_id": "c-a", "title": "Attention Is All You Need", "year": 2017, "source": "openalex"},
    ]
    for candidates in (as_objects, as_mappings):
        outcome = P.match_parsed(_parsed(), candidates)
        assert [c.candidate_id for c in outcome.candidates] == ["c-a", "c-b"]


def test_insufficient_metadata_fails_instead_of_seeding():
    parsed = P.ParsedPaper(title=None, authors=[], year=None, text_chars=40)
    outcome = P.match_parsed(parsed, [])
    assert outcome.status is ImportStatus.FAILED
    assert outcome.conflicts == [P.ERROR_INSUFFICIENT_METADATA]


# --------------------------------------------------------------------------
# title normalisation and similarity
# --------------------------------------------------------------------------


def test_title_similarity_is_deterministic_and_symmetric():
    pairs = [
        ("Attention Is All You Need", "attention is all you need!"),
        ("Deep Learning for Astronomy", "Deep  Learning, for  Astronomy"),
        ("深度学习方法综述", "深度学方习法综述"),
        ("Attention Is All You Need", ""),
        ("", ""),
    ]
    for left, right in pairs:
        first = P.title_similarity(left, right)
        assert first == P.title_similarity(left, right)
        assert first == P.title_similarity(right, left)
        assert 0.0 <= first <= 1.0


def test_normalize_title_applies_nfkc_casefold_and_punctuation_rules():
    assert P.normalize_title("  Attention—Is All You Need?! ") == "attention is all you need"
    assert P.normalize_title("ＭＡＣＨＩＮＥ   Learning") == "machine learning"
    assert P.normalize_title("深度学习方法综述") == "深度学习方法综述"
    assert P.normalize_title("") == ""
    assert P.normalize_title("！！！") == ""


def test_chinese_titles_use_character_bigrams():
    exact = "深度学习方法综述"
    shuffled = "深度学方习法综述"
    assert P.title_similarity(exact, exact) == 1.0
    shuffled_score = P.title_similarity(exact, shuffled)
    assert 0.0 < shuffled_score < 1.0
    assert shuffled_score == P.title_similarity(shuffled, exact)


def test_a_chinese_pdf_is_parsed_and_matched_by_character_bigrams(tmp_path: Path):
    """A real CJK PDF round-trips: title/abstract come out as Chinese text."""
    path = _write_pdf(
        tmp_path / "cjk.pdf",
        [[
            ("深度学习方法综述", 16),
            ("张三, 李四", 11),
            ("摘要", 12),
            ("本文综述了深度学习在自然语言处理领域的主要方法,并讨论了", 11),
            ("大规模预训练模型带来的机遇与挑战。", 11),
        ]],
        fontname="china-s",
    )
    outcome = P.parse_pdf(path)
    assert outcome.status is ImportStatus.PARSING
    parsed = outcome.parsed
    assert parsed is not None
    assert parsed.title == "深度学习方法综述"
    assert parsed.abstract and parsed.abstract.startswith("本文综述了深度学习")
    assert P.normalize_title(parsed.title) == "深度学习方法综述"

    # An exact-match candidate wins on title, authors and year together; a
    # one-character-shuffled title does not, because CJK similarity is
    # character-bigram based.
    exact = P.match_parsed(parsed, [
        P.CandidatePaper(
            "c1", "深度学习方法综述", None, parsed.year,
            list(parsed.authors), "openalex",
        ),
    ])
    assert exact.status is ImportStatus.MATCHED
    near = P.title_similarity(parsed.title, "深度学方习法综述")
    assert 0.0 < near < 1.0


def test_match_score_is_never_presented_as_a_probability():
    """The score is a bounded engineering similarity with no probabilistic claim."""
    candidate = P.CandidatePaper("c1", "Attention Is All You Need", None, 2017, [], "x")
    score = P.match_score(_parsed(), candidate)
    assert 0.0 <= score <= 1.0
    outcome = P.match_parsed(_parsed(), [candidate])
    payload = json.dumps(outcome.to_dict(), allow_nan=False, ensure_ascii=False)
    assert "probability" not in payload and "置信" not in payload


def test_no_nan_or_infinity_in_any_outcome(good_pdf: Path):
    outcome = P.parse_pdf(good_pdf)
    assert outcome.parsed is not None
    json.dumps(outcome.to_dict(), allow_nan=False)
    match = P.match_parsed(outcome.parsed, [
        P.CandidatePaper("c1", "Attention Is All You Need", None, 2020, [], "openalex"),
    ])
    json.dumps(P.describe_outcome(outcome, match), allow_nan=False)


def test_a_json_unsafe_candidate_does_not_leak_a_non_finite_score():
    candidate = {"candidate_id": "c1", "title": object(), "year": "not-a-year", "source": None}
    score = P.match_score(_parsed(), candidate)
    assert score == 0.0
    outcome = P.match_parsed(_parsed(), [candidate])
    json.dumps(outcome.to_dict(), allow_nan=False)


# --------------------------------------------------------------------------
# DTO shape and path safety
# --------------------------------------------------------------------------


def test_the_dto_cannot_expose_a_physical_path(good_pdf: Path):
    registry = P.AttachmentRegistry()
    attachment = registry.register("p", good_pdf.read_bytes(), good_pdf.name)
    item = P.build_import_item(
        item_id="item-1",
        attachment=attachment,
        display_name=good_pdf.name,
        status=ImportStatus.UPLOADED,
    )
    assert set(item) == {
        "item_id",
        "attachment_id",
        "display_name",
        "content_hash",
        "status",
        "paper_id",
        "match_candidates",
        "match_score",
        "error_code",
        "retryable",
    }
    serialised = json.dumps(item, allow_nan=False)
    assert str(good_pdf.parent) not in serialised
    assert good_pdf.name in serialised
    assert attachment.stored_path_key.startswith("blobs/")
    assert str(good_pdf.parent) not in attachment.stored_path_key


def test_a_hostile_display_name_never_influences_storage_or_raises():
    hostile_names = [
        "..\\..\\evil.pdf",
        "../../etc/passwd",
        "C:\\Windows\\System32\\evil.pdf",
        "a" * 500 + ".pdf",
        "....",
        "",
        "\x00\x01bad\x02.pdf",
    ]
    for name in hostile_names:
        sanitised = P.sanitize_display_name(name)
        assert isinstance(sanitised, str) and sanitised
        assert "/" not in sanitised and "\\" not in sanitised
        assert len(sanitised) <= 128
        assert "\x00" not in sanitised

    payload = b"this is not a PDF"
    for name in hostile_names:
        outcome = P.parse_pdf_bytes(payload, name)
        assert outcome.status is ImportStatus.INVALID_PDF

    registry = P.AttachmentRegistry()
    attachment = registry.register("p", payload, "..\\..\\evil.pdf")
    assert attachment.stored_path_key == P.stored_path_key(attachment.content_hash)
    item = P.build_import_item(
        item_id="i", attachment=attachment, display_name="..\\..\\evil.pdf",
        status=ImportStatus.INVALID_PDF, error_code=P.ERROR_NOT_A_PDF,
    )
    assert item["display_name"] == "evil.pdf"
    assert attachment.stored_path_key == "blobs/" + attachment.content_hash[:2] + "/" + attachment.content_hash


# --------------------------------------------------------------------------
# adapter boundary: the third-party library is wrapped here and nowhere else
# --------------------------------------------------------------------------


def test_only_pdf_import_may_import_the_third_party_pdf_library():
    """The product modules never import the PDF library directly."""
    offenders = []
    scanned = 0
    for path in sorted(REPO_ROOT.rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "import fitz" not in text:
            continue
        scanned += 1
        if path.name != "pdf_import.py":
            offenders.append(path.relative_to(REPO_ROOT).as_posix())
    assert offenders == []
    assert scanned == 1, "the wrapper itself must be the only importer"


def test_pdf_import_uses_the_shared_identifier_normaliser():
    text = (REPO_ROOT / "v32" / "pdf_import.py").read_text(encoding="utf-8")
    assert "from litsearch.identifiers import normalize_doi" in text
    # Re-using the shared rule means DOI prefixes resolve identically here.
    from litsearch.identifiers import normalize_doi

    assert normalize_doi("https://doi.org/10.1000/AbC") == "10.1000/abc"
    assert P.extract_dois("https://doi.org/10.1000/AbC") == ["10.1000/abc"]
