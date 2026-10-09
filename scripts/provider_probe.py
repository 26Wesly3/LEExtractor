"""Small, bounded live checks kept separate from the offline test suite."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from litsearch.config import current_year  # noqa: E402
from litsearch.diagnostics import get_diagnostics, reset_diagnostics  # noqa: E402
from litsearch.persistence import paper_to_dict  # noqa: E402
from litsearch.sources import (  # noqa: E402
    ArxivSource,
    CrossrefSource,
    OpenAlexSource,
    SemanticScholarSource,
)


def main():
    results = []
    for source in (SemanticScholarSource(), OpenAlexSource(), ArxivSource(), CrossrefSource()):
        reset_diagnostics()
        source.MAX_RETRIES = 0
        original_request = source._transport_request
        def bounded(method, url, _request=original_request, **kwargs):
            kwargs["timeout"] = 12
            return _request(method, url, **kwargs)
        source._transport_request = bounded
        source.MAX_HTTP_RETRIES = 0
        source.set_request_budget(1)
        if source.name == "crossref":
            paper = source.get_paper("10.1038/nature14539")
            papers = [paper] if paper else []
        else:
            papers = source.search_papers("deep learning", limit=2, year_from=2020, year_to=current_year())
        results.append({"provider": source.name, "returned": len(papers),
                        "papers": [paper_to_dict(p) for p in papers],
                        "diagnostics": [{"kind": e.kind.value, "status": e.status, "message": e.message} for e in get_diagnostics().events]})
        print(f"{source.name}: {len(papers)} records", flush=True)
    output = Path(__file__).resolve().parents[1] / "artifacts" / "provider_probe.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps({"year_to": current_year(), "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
