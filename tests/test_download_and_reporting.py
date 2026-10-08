"""PDF acquisition boundary + PRISMA reporting regressions (spec G / contract §10).

Sandbox note (V091_INTERFACE_CONTRACT.md §0): the system temp directory is
ACL-denied on this machine, so tests that need a directory use the ``workdir``
fixture under ``tests/_workspace_tmp/`` instead of ``tmp_path``.

No test here touches the network: the HTTP layer is a fake response object and
DNS is monkeypatched through ``litsearch.downloader.resolve_host``.
"""

from __future__ import annotations

import hashlib
import itertools
import shutil
import socket
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

import litsearch.downloader as downloader_module
from litsearch.downloader import (
    PROVENANCE_KEYS,
    PaperDownloader,
    sanitize_filename_part,
)
from litsearch.identifiers import PaperIdentifiers
from litsearch.models import Author, Paper, SearchResult
from litsearch.prisma import PRISMATracker, ScreeningDecision, ScreeningStage

_TMP_ROOT = Path(__file__).resolve().parent / "_workspace_tmp"
_COUNTER = itertools.count()

VALID_PDF = b"%PDF-1.7\n" + b"x" * 200 + b"\n%%EOF\n"
PUBLIC_IP = "93.184.216.34"
PUBLIC_IP_2 = "93.184.216.35"


@pytest.fixture()
def workdir():
    """A writable directory inside the workspace (tmp_path is ACL-denied here)."""
    path = _TMP_ROOT / f"download_{next(_COUNTER)}"
    if path.exists():
        _rmtree(path)
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        _rmtree(path)


def _rmtree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def public_dns(monkeypatch, mapping: dict[str, list[str]] | None = None):
    """Resolve every host to a public address unless the test says otherwise."""

    def resolve(host, port=None):
        if mapping and host in mapping:
            return list(mapping[host])
        return [PUBLIC_IP]

    monkeypatch.setattr(downloader_module, "resolve_host", resolve)


def failing_dns(monkeypatch):
    def resolve(host, port=None):
        raise socket.gaierror(f"getaddrinfo failed for {host}")

    monkeypatch.setattr(downloader_module, "resolve_host", resolve)


class FakeResponse:
    """Minimal ``requests.Response`` double.

    ``iter_content`` is the only body API the downloader may use; reading
    ``.content`` (the whole body into memory) is the defect under test, so the
    property counts accesses and the tests assert it stayed at zero.
    """

    def __init__(self, status_code=200, headers=None, body=b"", location="",
                 chunks=None, error=None, delay=0.0, endless=False):
        self.status_code = status_code
        self.headers = dict(headers or {})
        if location:
            self.headers["location"] = location
        self._body = body
        self._chunks = chunks
        self._error = error
        self._delay = delay
        self._endless = endless
        self.closed = False
        self.iter_calls = 0
        self.content_reads = 0

    @property
    def content(self):  # pragma: no cover - must never be touched
        self.content_reads += 1
        return self._body

    def iter_content(self, chunk_size=8192, decode_unicode=False):
        self.iter_calls += 1
        if self._endless:
            while True:
                if self._delay:
                    time.sleep(self._delay)
                yield b"%PDF-1.7 " + b"z" * 16
        if self._chunks is not None:
            source = self._chunks
        else:
            source = [self._body[i:i + chunk_size] for i in range(0, len(self._body), chunk_size)]
        for chunk in source:
            if self._delay:
                time.sleep(self._delay)
            yield chunk
        if self._error is not None:
            raise self._error

    def close(self):
        self.closed = True


def paper(pid="10.1/x", title="test paper", **kwargs):
    return Paper(id=pid, title=title, **kwargs)


def one_url_downloader(workdir, response, monkeypatch, url="https://example.org/paper.pdf"):
    """A downloader whose only candidate is ``url``, answered by ``response``."""
    public_dns(monkeypatch)
    downloader = PaperDownloader(str(workdir))
    # side_effect (not return_value): a bare iterator would be exhausted by the
    # first download of a multi-paper batch.
    downloader._candidate_urls = Mock(side_effect=lambda *a, **k: iter([url]))
    downloader._session.get = Mock(return_value=response)
    return downloader


# ---------------------------------------------------------------------------
# Existing regression (kept, now driven through the streaming body API)
# ---------------------------------------------------------------------------


def test_batch_download_uses_existing_method_and_rejects_html_even_with_pdf_mime(workdir, monkeypatch):
    downloader = PaperDownloader(str(workdir))
    downloader._candidate_urls = Mock(return_value=iter(["https://example.org/wrong.pdf", "https://example.org/right.pdf"]))
    html = FakeResponse(status_code=200, headers={"content-type": "application/pdf"},
                        body=b"<html>error</html>" * 1000)
    valid = FakeResponse(status_code=200, headers={"content-type": "application/octet-stream"},
                         body=VALID_PDF)
    responses = [html, valid]
    public_dns(monkeypatch)
    downloader._session.get = Mock(side_effect=responses)
    p = Paper(id="paper", title="test paper")
    results = downloader.batch_download([p], delay_seconds=0)
    assert results[0][1]
    assert len(list(workdir.glob("*.pdf"))) == 1
    assert downloader._session.get.call_count == 2
    assert list(workdir.glob("*.pdf"))[0].read_bytes() == VALID_PDF


# ---------------------------------------------------------------------------
# G1/G2: real streaming, size cap on decoded bytes, no whole-body reads
# ---------------------------------------------------------------------------


def test_streaming_never_reads_whole_body_and_records_provenance(workdir, monkeypatch):
    response = FakeResponse(status_code=200, headers={"content-type": "application/pdf"},
                            body=VALID_PDF)
    downloader = one_url_downloader(workdir, response, monkeypatch)
    outcome = downloader.download_pdf_with_provenance(paper())
    assert outcome.status == "downloaded"
    assert response.content_reads == 0, "resp.content must not be used"
    assert response.iter_calls == 1
    assert Path(outcome.path).read_bytes() == VALID_PDF
    assert set(outcome.provenance) == set(PROVENANCE_KEYS)
    assert outcome.provenance["bytes"] == len(VALID_PDF)
    assert outcome.provenance["sha256"] == hashlib.sha256(VALID_PDF).hexdigest()
    assert outcome.provenance["failure_reason"] == ""
    assert outcome.provenance["source"] == "example.org"
    assert outcome.provenance["final_url"] == "https://example.org/paper.pdf"
    assert outcome.provenance["fetched_at"]
    assert list(workdir.glob("*.part")) == []


def test_oversized_content_length_is_refused_before_the_body_is_streamed(workdir, monkeypatch):
    monkeypatch.setenv("LEEXTRACTOR_MAX_PDF_SIZE", "1024")
    response = FakeResponse(status_code=200, headers={"content-type": "application/pdf",
                                                      "content-length": "5000"},
                            body=VALID_PDF)
    downloader = one_url_downloader(workdir, response, monkeypatch)
    outcome = downloader.download_pdf_with_provenance(paper())
    assert outcome.path is None
    assert outcome.status == "not_retrieved"
    assert outcome.provenance["failure_reason"] == "size_limit_exceeded"
    assert response.iter_calls == 0, "a declared oversize body must not be streamed at all"
    assert response.closed is True
    assert list(workdir.glob("*.pdf")) == []
    assert list(workdir.glob("*.part")) == []


def test_missing_content_length_is_counted_while_streaming(workdir, monkeypatch):
    monkeypatch.setenv("LEEXTRACTOR_MAX_PDF_SIZE", "1024")
    chunks = [b"%PDF-1.7\n", b"a" * 600, b"b" * 600, b"\n%%EOF\n"]
    response = FakeResponse(status_code=200, headers={"content-type": "application/pdf"},
                            chunks=chunks)
    downloader = one_url_downloader(workdir, response, monkeypatch)
    outcome = downloader.download_pdf_with_provenance(paper())
    assert outcome.path is None
    assert outcome.provenance["failure_reason"] == "size_limit_exceeded"
    assert response.closed is True
    assert list(workdir.glob("*.part")) == []
    assert list(workdir.glob("*.pdf")) == []


def test_forged_small_content_length_does_not_bypass_the_cap(workdir, monkeypatch):
    monkeypatch.setenv("LEEXTRACTOR_MAX_PDF_SIZE", "1024")
    response = FakeResponse(status_code=200,
                            headers={"content-type": "application/pdf", "content-length": "20"},
                            chunks=[b"%PDF-1.7\n"] + [b"x" * 800] * 3)
    downloader = one_url_downloader(workdir, response, monkeypatch)
    outcome = downloader.download_pdf_with_provenance(paper())
    assert outcome.path is None
    assert outcome.provenance["failure_reason"] == "size_limit_exceeded"
    assert list(workdir.glob("*.part")) == []


def test_non_numeric_content_length_is_ignored_and_the_stream_is_capped(workdir, monkeypatch):
    monkeypatch.setenv("LEEXTRACTOR_MAX_PDF_SIZE", "1024")
    response = FakeResponse(status_code=200,
                            headers={"content-type": "application/pdf", "content-length": "not-a-number"},
                            chunks=[b"%PDF-1.7\n"] + [b"x" * 800] * 3)
    downloader = one_url_downloader(workdir, response, monkeypatch)
    outcome = downloader.download_pdf_with_provenance(paper())
    assert outcome.path is None
    assert outcome.provenance["failure_reason"] == "size_limit_exceeded"


def test_body_exactly_at_the_cap_is_accepted(workdir, monkeypatch):
    body = b"%PDF-1.7\n" + b"x" * 20 + b"\n%%EOF\n"
    monkeypatch.setenv("LEEXTRACTOR_MAX_PDF_SIZE", str(len(body)))
    response = FakeResponse(status_code=200, headers={"content-type": "application/pdf",
                                                      "content-length": str(len(body))},
                            body=body)
    downloader = one_url_downloader(workdir, response, monkeypatch)
    outcome = downloader.download_pdf_with_provenance(paper())
    assert outcome.path is not None
    assert Path(outcome.path).read_bytes() == body


@pytest.mark.parametrize("status", [403, 404, 500, 302])
def test_non_200_status_is_refused_with_the_status_in_the_reason(workdir, monkeypatch, status):
    response = FakeResponse(status_code=status, headers={"content-type": "text/html"}, body=b"nope")
    downloader = one_url_downloader(workdir, response, monkeypatch)
    outcome = downloader.download_pdf_with_provenance(paper())
    assert outcome.path is None
    assert outcome.provenance["failure_reason"].startswith("http_")
    assert response.closed is True


def test_html_pretending_to_be_a_pdf_is_rejected(workdir, monkeypatch):
    response = FakeResponse(status_code=200, headers={"content-type": "application/pdf"},
                            body=b"<html><body>captcha</body></html>" * 50)
    downloader = one_url_downloader(workdir, response, monkeypatch)
    outcome = downloader.download_pdf_with_provenance(paper())
    assert outcome.path is None
    assert outcome.provenance["failure_reason"] == "not_a_pdf"
    assert list(workdir.glob("*.part")) == []
    assert list(workdir.glob("*.pdf")) == []


def test_pdf_without_eof_marker_is_rejected(workdir, monkeypatch):
    response = FakeResponse(status_code=200, headers={"content-type": "application/pdf"},
                            body=b"%PDF-1.7\n" + b"x" * 100)
    downloader = one_url_downloader(workdir, response, monkeypatch)
    outcome = downloader.download_pdf_with_provenance(paper())
    assert outcome.path is None
    assert outcome.provenance["failure_reason"] == "not_a_pdf"


def test_truncated_stream_cleans_up_and_closes(workdir, monkeypatch):
    response = FakeResponse(status_code=200, headers={"content-type": "application/pdf"},
                            chunks=[b"%PDF-1.7\n", b"x" * 100],
                            error=ConnectionError("connection reset"))
    downloader = one_url_downloader(workdir, response, monkeypatch)
    outcome = downloader.download_pdf_with_provenance(paper())
    assert outcome.path is None
    assert outcome.status == "not_retrieved"
    assert outcome.provenance["failure_reason"] in {"truncated_stream", "network_error"}
    assert response.closed is True
    assert list(workdir.glob("*.part")) == []
    assert list(workdir.glob("*.pdf")) == []


def test_total_time_budget_aborts_an_endless_stream(workdir, monkeypatch):
    response = FakeResponse(status_code=200, headers={"content-type": "application/pdf"},
                            endless=True, delay=0.02)
    downloader = one_url_downloader(workdir, response, monkeypatch)
    downloader._total_timeout = 0.05
    started = time.monotonic()
    outcome = downloader.download_pdf_with_provenance(paper())
    elapsed = time.monotonic() - started
    assert outcome.path is None
    assert outcome.provenance["failure_reason"] == "timeout"
    assert elapsed < 2.0
    assert response.closed is True
    assert list(workdir.glob("*.part")) == []


# ---------------------------------------------------------------------------
# G3: SSRF — protocol + resolved address of the initial URL and every hop
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url, reason", [
    ("http://127.0.0.1/x.pdf", "non_public_address"),
    ("http://localhost/x.pdf", "non_public_address"),
    ("http://10.0.0.5/x.pdf", "non_public_address"),
    ("http://192.168.1.20/x.pdf", "non_public_address"),
    ("http://172.16.4.4/x.pdf", "non_public_address"),
    ("http://169.254.169.254/latest/meta-data", "non_public_address"),
    ("http://0.0.0.0/x.pdf", "non_public_address"),
    ("http://100.64.0.7/x.pdf", "non_public_address"),
    ("http://224.0.0.1/x.pdf", "non_public_address"),
    ("http://[::1]/x.pdf", "non_public_address"),
    ("http://[fd00::1]/x.pdf", "non_public_address"),
    ("http://[fe80::1]/x.pdf", "non_public_address"),
    ("file:///C:/Windows/win.ini", "unsupported_scheme"),
    ("ftp://example.org/x.pdf", "unsupported_scheme"),
])
def test_local_and_non_public_targets_are_refused_without_connecting(workdir, monkeypatch, url, reason):
    downloader = PaperDownloader(str(workdir))
    downloader._candidate_urls = Mock(return_value=iter([url]))
    get = Mock(side_effect=AssertionError("must not connect"))
    downloader._session.get = get
    # Resolve names through the real rule so literal IPs are judged as such.
    monkeypatch.setattr(downloader_module, "resolve_host", lambda host, port=None: [host])
    outcome = downloader.download_pdf_with_provenance(paper())
    assert outcome.path is None
    assert outcome.provenance["failure_reason"] == reason
    assert get.call_count == 0


def test_hostname_resolving_to_a_private_address_is_refused(workdir, monkeypatch):
    downloader = PaperDownloader(str(workdir))
    downloader._candidate_urls = Mock(return_value=iter(["https://internal.example.net/x.pdf"]))
    get = Mock(side_effect=AssertionError("must not connect"))
    downloader._session.get = get
    public_dns(monkeypatch, {"internal.example.net": ["10.1.2.3"]})
    outcome = downloader.download_pdf_with_provenance(paper())
    assert outcome.path is None
    assert outcome.provenance["failure_reason"] == "non_public_address"
    assert get.call_count == 0


def test_hostname_resolving_to_a_mix_of_public_and_private_is_refused(workdir, monkeypatch):
    downloader = PaperDownloader(str(workdir))
    downloader._candidate_urls = Mock(return_value=iter(["https://mixed.example.net/x.pdf"]))
    downloader._session.get = Mock(side_effect=AssertionError("must not connect"))
    public_dns(monkeypatch, {"mixed.example.net": [PUBLIC_IP, "10.0.0.9"]})
    outcome = downloader.download_pdf_with_provenance(paper())
    assert outcome.provenance["failure_reason"] == "non_public_address"


def test_dns_failure_is_a_refusal_not_a_connection(workdir, monkeypatch):
    downloader = PaperDownloader(str(workdir))
    downloader._candidate_urls = Mock(return_value=iter(["https://gone.example.org/x.pdf"]))
    get = Mock(side_effect=AssertionError("must not connect"))
    downloader._session.get = get
    failing_dns(monkeypatch)
    outcome = downloader.download_pdf_with_provenance(paper())
    assert outcome.path is None
    assert outcome.provenance["failure_reason"] == "dns_failure"
    assert get.call_count == 0


def test_redirect_to_a_private_address_is_refused_before_connecting(workdir, monkeypatch):
    redirect = FakeResponse(status_code=302, headers={"content-type": "text/html"},
                            location="http://169.254.169.254/latest/meta-data")
    downloader = PaperDownloader(str(workdir))
    downloader._candidate_urls = Mock(return_value=iter(["https://example.org/x.pdf"]))
    get = Mock(return_value=redirect)
    downloader._session.get = get
    public_dns(monkeypatch, {"example.org": [PUBLIC_IP]})
    outcome = downloader.download_pdf_with_provenance(paper())
    assert outcome.path is None
    assert outcome.provenance["failure_reason"] == "non_public_address"
    assert get.call_count == 1, "the private hop must not be requested"
    assert redirect.closed is True
    assert outcome.provenance["final_url"] == "http://169.254.169.254/latest/meta-data"


def test_every_redirect_hop_is_validated_not_only_the_last(workdir, monkeypatch):
    first = FakeResponse(status_code=302, location="https://cdn.example.net/step2.pdf")
    second = FakeResponse(status_code=302, location="https://internal.example.net/step3.pdf")
    downloader = PaperDownloader(str(workdir))
    downloader._candidate_urls = Mock(return_value=iter(["https://example.org/x.pdf"]))
    get = Mock(side_effect=[first, second, AssertionError("hop 3 must not be requested")])
    downloader._session.get = get
    public_dns(monkeypatch, {"example.org": [PUBLIC_IP],
                             "cdn.example.net": [PUBLIC_IP_2],
                             "internal.example.net": ["10.0.0.9"]})
    outcome = downloader.download_pdf_with_provenance(paper())
    assert outcome.provenance["failure_reason"] == "non_public_address"
    assert get.call_count == 2
    assert [call.args[0] for call in get.call_args_list] == [
        "https://example.org/x.pdf", "https://cdn.example.net/step2.pdf"]


def test_relative_redirect_is_resolved_against_the_current_hop(workdir, monkeypatch):
    redirect = FakeResponse(status_code=301, location="/moved/paper.pdf")
    good = FakeResponse(status_code=200, headers={"content-type": "application/pdf"}, body=VALID_PDF)
    downloader = PaperDownloader(str(workdir))
    downloader._candidate_urls = Mock(return_value=iter(["https://example.org/a/paper.pdf"]))
    downloader._session.get = Mock(side_effect=[redirect, good])
    public_dns(monkeypatch, {"example.org": [PUBLIC_IP]})
    outcome = downloader.download_pdf_with_provenance(paper())
    assert outcome.path is not None
    assert outcome.provenance["final_url"] == "https://example.org/moved/paper.pdf"
    assert outcome.provenance["resolver"] == "direct"


def test_redirect_loop_is_bounded(workdir, monkeypatch):
    redirect = FakeResponse(status_code=302, location="https://example.org/loop.pdf")
    downloader = PaperDownloader(str(workdir))
    downloader._candidate_urls = Mock(return_value=iter(["https://example.org/loop.pdf"]))
    downloader._session.get = Mock(return_value=redirect)
    public_dns(monkeypatch, {"example.org": [PUBLIC_IP]})
    outcome = downloader.download_pdf_with_provenance(paper())
    assert outcome.path is None
    assert outcome.provenance["failure_reason"] == "too_many_redirects"
    assert downloader._session.get.call_count <= 6


# ---------------------------------------------------------------------------
# G8: existing files preserved, corrupt files re-fetched
# ---------------------------------------------------------------------------


def test_existing_valid_pdf_is_preserved_without_any_request(workdir, monkeypatch):
    downloader = PaperDownloader(str(workdir))
    p = paper()
    target = workdir / f"{downloader.safe_filename(p)}.pdf"
    target.write_bytes(VALID_PDF)
    downloader._session.get = Mock(side_effect=AssertionError("must not request"))
    # Path.read_bytes() on an existing file is the whole-file read under test:
    # validation must be bounded (head + tail), never the entire document.
    whole_file_read = Mock(side_effect=AssertionError("whole-file read"))
    monkeypatch.setattr(Path, "read_bytes", whole_file_read)
    outcome = downloader.download_pdf_with_provenance(p)
    assert outcome.path == str(target)
    assert outcome.status == "existing"
    assert downloader._session.get.call_count == 0
    assert whole_file_read.call_count == 0
    assert target.open("rb").read() == VALID_PDF


def test_preexisting_corrupt_file_is_refetched(workdir, monkeypatch):
    downloader = PaperDownloader(str(workdir))
    p = paper()
    target = workdir / f"{downloader.safe_filename(p)}.pdf"
    target.write_bytes(b"<html>this is not a pdf</html>")
    response = FakeResponse(status_code=200, headers={"content-type": "application/pdf"}, body=VALID_PDF)
    public_dns(monkeypatch)
    downloader._candidate_urls = Mock(return_value=iter(["https://example.org/paper.pdf"]))
    downloader._session.get = Mock(return_value=response)
    outcome = downloader.download_pdf_with_provenance(p)
    assert outcome.status == "downloaded"
    with open(target, "rb") as handle:
        assert handle.read() == VALID_PDF


def test_failed_download_keeps_a_good_existing_file(workdir, monkeypatch):
    downloader = PaperDownloader(str(workdir))
    p = paper()
    target = workdir / f"{downloader.safe_filename(p)}.pdf"
    target.write_bytes(VALID_PDF)
    downloader._candidate_urls = Mock(return_value=iter(["https://example.org/paper.pdf"]))
    public_dns(monkeypatch)
    downloader._session.get = Mock(side_effect=ConnectionError("offline"))
    outcome = downloader.download_pdf_with_provenance(p, skip_existing=False)
    assert outcome.path is None
    with open(target, "rb") as handle:
        assert handle.read() == VALID_PDF, "a failed re-fetch must not destroy the good file"


def test_target_path_that_is_a_directory_is_reported_not_crashed(workdir, monkeypatch):
    downloader = PaperDownloader(str(workdir))
    p = paper()
    (workdir / f"{downloader.safe_filename(p)}.pdf").mkdir()
    downloader._candidate_urls = Mock(return_value=iter(["https://example.org/paper.pdf"]))
    public_dns(monkeypatch)
    downloader._session.get = Mock(side_effect=AssertionError("must not request"))
    outcome = downloader.download_pdf_with_provenance(p)
    assert outcome.path is None
    assert outcome.provenance["failure_reason"] == "target_unusable"


# ---------------------------------------------------------------------------
# G5/G6: authors, Windows filenames, reserved words, collisions
# ---------------------------------------------------------------------------


def test_empty_author_name_falls_back_to_unknown():
    no_authors = Paper(id="a", title="t")
    blank = Paper(id="b", title="t", authors=[Author(name="   ")])
    assert "Unknown" in PaperDownloader._sanitize_filename(no_authors)
    assert "Unknown" in PaperDownloader._sanitize_filename(blank)


@pytest.mark.parametrize("name", ["CON", "con", "PRN", "AUX", "NUL", "COM1", "com9", "LPT1", "LPT9",
                                  "CON.pdf", "nul.txt", "con."])
def test_windows_reserved_device_names_are_escaped(name):
    safe = sanitize_filename_part(name)
    assert safe.lower().split(".")[0].rstrip(" .") not in {
        "con", "prn", "aux", "nul",
        *(f"com{i}" for i in range(10)), *(f"lpt{i}" for i in range(10)),
    }
    assert safe


@pytest.mark.parametrize("raw", ['a<b>c:d"e/f\\g|h?i*j', "tab\there", "nl\nhere", "trail. ", "..dots"])
def test_illegal_and_control_characters_are_removed(raw):
    safe = sanitize_filename_part(raw)
    assert not set(safe) & set('<>:"/\\|?*')
    assert all(ord(ch) >= 32 for ch in safe)
    assert not safe.endswith((" ", "."))


def test_long_titles_and_authors_are_truncated_to_a_writable_length(workdir, monkeypatch):
    response = FakeResponse(status_code=200, headers={"content-type": "application/pdf"}, body=VALID_PDF)
    downloader = one_url_downloader(workdir, response, monkeypatch)
    p = Paper(id="10.1/long", title="A" * 400, authors=[Author(name="B" * 200)], year=2020)
    outcome = downloader.download_pdf_with_provenance(p)
    assert outcome.path is not None
    name = Path(outcome.path).name
    assert len(name) <= 120
    assert len(str(Path(outcome.path))) < 260
    assert Path(outcome.path).exists()


def test_same_sanitized_name_for_two_papers_does_not_collide(workdir, monkeypatch):
    response = FakeResponse(status_code=200, headers={"content-type": "application/pdf"}, body=VALID_PDF)
    downloader = one_url_downloader(workdir, response, monkeypatch)
    first = Paper(id="10.1/one", title="Same title", authors=[Author(name="Smith")], year=2020)
    second = Paper(id="10.1/two", title="Same title", authors=[Author(name="Smith")], year=2020)
    paths = {downloader.download_pdf_with_provenance(p).path for p in (first, second)}
    assert len(paths) == 2
    assert len(list(workdir.glob("*.pdf"))) == 2


def test_an_explicit_reserved_filename_is_escaped(workdir, monkeypatch):
    response = FakeResponse(status_code=200, headers={"content-type": "application/pdf"}, body=VALID_PDF)
    downloader = one_url_downloader(workdir, response, monkeypatch)
    outcome = downloader.download_pdf_with_provenance(paper(), filename="CON")
    assert outcome.path is not None
    assert Path(outcome.path).stem.lower() != "con"
    assert Path(outcome.path).exists()


def test_a_filename_with_a_directory_is_still_rejected(workdir):
    downloader = PaperDownloader(str(workdir))
    with pytest.raises(ValueError):
        downloader.download_pdf(paper(), filename="../escape")


# ---------------------------------------------------------------------------
# G4/G7: provenance, resolver/source, and "not retrieved" != excluded
# ---------------------------------------------------------------------------


def test_provenance_records_the_resolver_and_the_failure_reason(workdir, monkeypatch):
    response = FakeResponse(status_code=404, headers={"content-type": "text/html"}, body=b"gone")
    downloader = one_url_downloader(workdir, response, monkeypatch,
                                    url="https://arxiv.org/pdf/2401.00001.pdf")
    outcome = downloader.download_pdf_with_provenance(paper())
    assert outcome.provenance["resolver"] == "arxiv"
    assert set(outcome.provenance) == set(PROVENANCE_KEYS)
    assert outcome.provenance["failure_reason"].startswith("http_")
    assert outcome.provenance["bytes"] == 0
    assert outcome.provenance["sha256"] == ""
    assert outcome.provenance["fetched_at"]


def test_download_attempts_are_recorded_for_the_whole_batch(workdir, monkeypatch):
    bad = FakeResponse(status_code=404, headers={}, body=b"")
    good = FakeResponse(status_code=200, headers={"content-type": "application/pdf"}, body=VALID_PDF)
    downloader = PaperDownloader(str(workdir))
    downloader._candidate_urls = Mock(return_value=iter(["https://example.org/a.pdf",
                                                         "https://example.org/b.pdf"]))
    public_dns(monkeypatch)
    downloader._session.get = Mock(side_effect=[bad, good])
    results = downloader.batch_download([paper()], delay_seconds=0)
    assert results[0][1]
    assert len(downloader.attempts) == 2
    assert downloader.attempts[0]["failure_reason"].startswith("http_")
    assert downloader.attempts[1]["failure_reason"] == ""


def test_unfetched_open_access_file_stays_not_retrieved_and_is_not_excluded(workdir, monkeypatch):
    """Spec G6: no OA file ⇒ 未获取, never 全文不合格."""
    response = FakeResponse(status_code=404, headers={"content-type": "text/html"}, body=b"")
    downloader = one_url_downloader(workdir, response, monkeypatch)
    p = paper(pid="10.1/oa-missing")
    tracker = PRISMATracker()
    tracker.add_papers([p])
    tracker.screen_paper(p.id, ScreeningDecision.ACCEPT)
    outcome = downloader.download_pdf_with_provenance(p)
    assert outcome.status == "not_retrieved"
    record = tracker.records[p.id]
    assert record.full_text_decision == ScreeningDecision.PENDING
    assert record.full_text_retrieved is False
    assert record.full_text_reason == ""
    report = tracker.generate_report()
    assert report.full_text_excluded == 0


def test_download_failure_does_not_write_any_session_or_prisma_state(workdir, monkeypatch):
    response = FakeResponse(status_code=500, headers={}, body=b"")
    downloader = one_url_downloader(workdir, response, monkeypatch)
    downloader.download_pdf(paper())
    assert downloader.last_provenance["failure_reason"].startswith("http_")
    assert not hasattr(downloader, "prisma")


# ---------------------------------------------------------------------------
# kept: reporting / PRISMA counting regressions
# ---------------------------------------------------------------------------


def test_arxiv_is_taken_from_own_metadata_and_never_abstract_mentions():
    p = Paper(id="hash", title="a paper", abstract="We compare against arxiv:1706.03762")
    assert PaperDownloader._extract_arxiv_id(p) is None
    p.identifiers = PaperIdentifiers(arxiv_id="1810.04805")
    assert PaperDownloader._extract_arxiv_id(p) == "1810.04805"


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
    p = Paper(id="10.1/a", title="same", doi="10.1/a")
    tracker.add_papers([p, p])
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


def test_search_result_export_carries_the_http_budget_contract_fields():
    """Reporting must expose the new stop/budget fields (contract §2/§7)."""
    result = SearchResult()
    assert hasattr(result, "stop_reason")
    assert hasattr(result, "http_budget")
