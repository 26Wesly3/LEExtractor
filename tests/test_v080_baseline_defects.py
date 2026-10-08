"""The exact v0.8.0 baselines, pinned so the fixes cannot silently regress.

These are the measurements taken on the unmodified v0.8.0 source *before* any
change, using the throwaway scripts that produced the defect list. Keeping them
as executable assertions matters because each one is a claim in the release
notes: if a future change reintroduces the old number, this file goes red and
the release note becomes false.

Recorded on v0.8.0 (2026-10-08):

* a pair sharing one reference and cited together by one paper reported a single
  relation of ``weight=2.81`` — BC 1.0 + CC 1.0 + cosine ≈ 0.81 — with all three
  kinds of evidence attached to the bibliographic-coupling row. Distinct
  relation types on that pair: ``{bibliographic_coupling}``.
* identical title+abstract produced ``text_similarity weight=2.0``.
* OpenAlex ``limit=250`` returned 250 rows of which only 200 were distinct;
  ``limit=201`` returned 201 rows with 200 distinct. Page sizes were
  ``[(200, 1), (50, 2)]`` — recomputed per page.
* a citation pagination failing on page 2 cached its 200-row prefix and a later
  healthy call re-issued **zero** requests, permanently returning 200 of 250.
* the snowball engine set ``saturated=True`` and ``completed=True`` when every
  citation lookup had raised.
* ``PRISMATracker`` held 2 records after DOI and S2 records for one work were
  bridged by a third record carrying both identifiers.
* ``questions._in_corpus_citations`` returned ``{}`` for a paper with an
  observed ``forward_citation`` edge, so "nobody followed up" was generated for
  a paper the evidence graph showed was cited.
"""

import pytest

from litsearch.evidence import EvidenceGraph
from litsearch.filters import RelevanceFilter
from litsearch.models import DiscoveryTrace, Paper
from litsearch.prisma import PRISMATracker
from litsearch.snowball import SnowballEngine
from litsearch.stop_reasons import StopReason

SAME_TEXT = "deep learning for plant phenotyping with unmanned aerial vehicles"


def paper(pid, title="Paper", abstract="", refs=(), source="openalex"):
    return Paper(id=pid, title=title, abstract=abstract,
                 reference_ids=list(refs), source=source)


# -- baseline 1: one pair, one mixed relation, summed weight -----------------


def test_v080_reported_one_mixed_relation_with_a_summed_weight():
    a = paper("10.1/a", "Wheat phenotyping with drones",
              "wheat phenotyping uav imaging yield", refs=["10.1/shared"])
    b = paper("10.1/b", "Wheat phenotyping using robots",
              "wheat phenotyping robot imaging yield", refs=["10.1/shared"])
    c = paper("10.1/c", "Wheat phenotyping survey", "wheat phenotyping review",
              refs=["10.1/a", "10.1/b"])

    graph = EvidenceGraph([a, b, c], edge_types=(
        "citation", "bibliographic_coupling", "co_citation", "text_similarity"))

    rows = [r for r in graph.relations_for("10.1/a") if r["paper_id"] == "10.1/b"]
    assert len(rows) == 3, "v0.8.0 kept only one relation per pair"

    scores = sorted(r["score"] for r in rows)
    # The old single number was ≈2.81 = 1.0 (BC) + 1.0 (CC) + 0.81 (cosine).
    assert not any(abs(score - 2.81) < 0.2 for score in scores), (
        "a relation score looks like the old cross-relation sum"
    )
    counts = [r for r in rows if r["edge_type"] != "text_similarity"]
    assert all(r["score"] == float(int(r["score"])) for r in counts), (
        "a count-type relation is no longer a whole number of witnesses"
    )


# -- baseline 2: identical text scored 2.0 ----------------------------------


def test_v080_scored_identical_text_as_two():
    graph = EvidenceGraph(
        [paper("10.2/x", "Identical", SAME_TEXT), paper("10.2/y", "Identical", SAME_TEXT)],
        edge_types=("text_similarity",),
    )
    rows = graph.relations_for("10.2/x")
    assert len(rows) == 1
    assert rows[0]["score"] == pytest.approx(1.0)
    assert rows[0]["score"] != pytest.approx(2.0)


# -- baseline 3/4: pagination duplicated records and cached a failure --------

RECORD_COUNT = 250
RECORDS = [
    {"id": f"https://openalex.org/W{i}", "doi": f"https://doi.org/10.9/{i}",
     "title": f"P{i}", "publication_year": 2020, "cited_by_count": 1,
     "referenced_works": [], "primary_location": None, "topics": [], "authorships": []}
    for i in range(RECORD_COUNT)
]


class _Resp:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200
        self.headers = {}
        self.url = "https://api.openalex.org/works"

    def json(self):
        return self._payload

    @property
    def text(self):
        return ""


class _Transport:
    def __init__(self, fail_pages=(), total=RECORD_COUNT):
        self.calls = []
        self.fail_pages = set(fail_pages)
        self.total = total
        self.headers = {}

    def get(self, url, params=None, timeout=None, **kwargs):
        params = dict(params or {})
        self.calls.append(params)
        page, size = int(params.get("page", 1)), int(params.get("per_page", 25))
        if page in self.fail_pages:
            raise RuntimeError("simulated failure")
        available = RECORDS[:self.total]
        start = (page - 1) * size
        return _Resp({"results": available[start:start + size], "meta": {"count": self.total}})


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("litsearch.sources.time.sleep", lambda _s: None)
    monkeypatch.setattr("litsearch.snowball.time.sleep", lambda _s: None)


def _source(tmp_path, transport, name="c.db"):
    from litsearch.cache import Cache
    from litsearch.sources import OpenAlexSource
    from litsearch.stop_reasons import CountingSession

    source = OpenAlexSource(cache=Cache(str(tmp_path / name)))
    source._session = CountingSession(transport, "openalex")
    source._resolve_oa_id = lambda _pid: "W1"
    return source


@pytest.mark.parametrize("limit,expected_distinct", [(201, 201), (250, 250)])
def test_v080_returned_fewer_distinct_records_than_requested(tmp_path, limit, expected_distinct):
    transport = _Transport()
    papers = _source(tmp_path, transport).search_papers("wheat", limit=limit)
    assert len(papers) == limit
    assert len({p.canonical_id for p in papers}) == expected_distinct, (
        "v0.8.0 returned 201 rows with only 200 distinct ids at limit=201"
    )


def test_v080_changed_page_size_mid_sequence(tmp_path):
    transport = _Transport()
    _source(tmp_path, transport).search_papers("wheat", limit=250)
    sizes = [call["per_page"] for call in transport.calls]
    assert len(set(sizes)) == 1, f"v0.8.0 sent [(200,1),(50,2)]-style sizes; got {sizes}"


def test_v080_cached_a_failed_pagination_as_complete(tmp_path):
    broken = _source(tmp_path, _Transport(fail_pages=(2,)))
    first = broken.search_papers("wheat", limit=250)
    assert len(first) == 100  # page 1 only

    healthy_transport = _Transport()
    again = _source(tmp_path, healthy_transport).search_papers("wheat", limit=250)
    assert len(again) == 250, (
        "v0.8.0 served the 200-row prefix from cache forever with zero new requests"
    )
    assert healthy_transport.calls, "the resumed run issued no requests at all"


# -- baseline 5: a failing run claimed saturation ----------------------------


class _FailingSources:
    snowball_delay = 0

    def __init__(self, fail=True):
        self.fail = fail

    def is_canceled(self):
        return False

    def get_references(self, seed, limit=100):
        if self.fail:
            raise TimeoutError("simulated timeout")
        return []

    def get_citations(self, seed, limit=100):
        if self.fail:
            raise TimeoutError("simulated timeout")
        return []


def test_v080_claimed_saturation_when_every_lookup_failed():
    seed = Paper(id="seed", title="plant phenotyping")
    seed.relevance_score = 1.0
    result = SnowballEngine(_FailingSources(), RelevanceFilter()).run(
        seed_papers=[seed], research_direction="plant phenotyping", max_rounds=2,
    )
    assert result.saturated is False, "a run whose sources all failed claimed saturation"
    assert result.completed is False
    assert result.stop_reason == StopReason.API_FAILURE.value


# -- baseline 6: identity bridging left two entities -------------------------


def test_v080_bridging_two_records_left_two_entities():
    from litsearch.identifiers import PaperIdentifiers

    tracker = PRISMATracker()
    tracker.add_papers([paper("10.4/dup", "Duplicated work", source="openalex")])
    tracker.add_papers([Paper(id="S2CorpusId:99", title="Duplicated work",
                              source="semantic_scholar",
                              identifiers=PaperIdentifiers(semantic_scholar_id="99"))])
    assert len(tracker.records) == 2  # v0.8.0 state: two independent entities

    tracker.add_papers([Paper(
        id="10.4/dup", title="Duplicated work", source="openalex", doi="10.4/dup",
        identifiers=PaperIdentifiers(doi="10.4/dup", semantic_scholar_id="99"),
    )])
    assert len(tracker.records) == 1, (
        "bridging metadata must merge ALL matching records, not just the first"
    )


# -- baseline 7: questions ignored graph citation facts ----------------------


def test_v080_in_corpus_citations_ignored_a_forward_citation_edge():
    from litsearch.questions import _in_corpus_citations

    target = paper("10.5/target", "Target paper", "novel method")
    citer = paper("10.5/citer", "Citing paper", "builds on target")
    citer.discovery_traces.append(DiscoveryTrace(
        method="forward_citation", provider="semantic_scholar",
        seed_id=target.canonical_id, round_no=1,
    ))

    counts = _in_corpus_citations([target, citer])
    assert counts.get(target.canonical_id, 0) == 1, (
        "v0.8.0 returned {} here while the evidence graph drew the edge, so "
        "'nobody followed up' was reported for a paper that was cited"
    )

    graph = EvidenceGraph([target, citer], edge_types=("citation",))
    assert (citer.canonical_id, target.canonical_id) in graph.graph.edges
