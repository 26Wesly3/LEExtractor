"""Explicitly synthetic, deterministic, offline workflow demonstration."""

from litsearch.filters import RelevanceFilter
from litsearch.models import Author, DiscoveryTrace, Paper
from litsearch.search import ReviewPhase, ReviewState


def demo_state():
    state = ReviewState(topic="deep learning plant phenotyping", research_direction="image based wheat plant trait estimation", phase=ReviewPhase.SYSTEMATIC_SEARCH)
    titles = ["Image based wheat plant trait estimation", "Deep learning for plant phenotyping",
              "Plant phenotype segmentation with convolutional networks", "Wheat traits from aerial images",
              "Root architecture measurements using computer vision", "Crop image analysis under changing light",
              "Benchmarking non-destructive plant measurements", "Marine sediment transport"]
    papers = []
    for i, title in enumerate(titles):
        p = Paper(id=f"demo:{i + 1}", title=title + " [SYNTHETIC DEMO]", year=2018 + i,
                  abstract="Synthetic test fixture. " + title + ". This record is fictional and is not a research finding.",
                  authors=[Author(name="Demo Author")], venue="Synthetic demo dataset", source="demo_fixture",
                  reference_ids=[f"demo:{i}"] if i and i < 7 else [])
        p.discovery_traces = [DiscoveryTrace(method="demo_fixture", provider="synthetic_demo", query=state.topic)]
        papers.append(p)
    RelevanceFilter().compute_relevance(papers, state.research_direction)
    state.search_papers = papers
    state.prisma.add_papers(papers)
    state.search_manifest = {"mode": "synthetic_demo", "query": state.topic, "fixture_version": 1,
                             "warning": "Fictional papers for interaction tests; not real research evidence"}
    return state


class DemoSource:
    """Small synthetic citation fixture; never touches the network."""
    snowball_delay = 0

    def __init__(self):
        papers = demo_state().search_papers
        for i in range(9, 12):
            papers.append(Paper(id=f"demo:{i}", title=f"Plant trait image experiment {i} [SYNTHETIC DEMO]",
                                year=2025, abstract="Fictional plant trait estimation from wheat images for testing.",
                                reference_ids=["demo:1"], source="demo_fixture", venue="Synthetic demo dataset"))
        self.papers = {p.id: p for p in papers}

    @staticmethod
    def _key(paper):
        return paper.id if isinstance(paper, Paper) else paper

    def get_paper(self, paper):
        return self.papers.get(self._key(paper))

    def get_references(self, paper, limit=100):
        record = self.get_paper(paper)
        return [self.papers[key] for key in record.reference_ids if key in self.papers][:limit] if record else []

    def get_citations(self, paper, limit=100):
        key = self._key(paper)
        return [p for p in self.papers.values() if key in p.reference_ids][:limit]
