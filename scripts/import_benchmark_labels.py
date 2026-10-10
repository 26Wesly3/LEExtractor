"""Import explicit human judgments; blank rows remain unjudged."""

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from litsearch.benchmark import BenchmarkDataset, LabelledPaper  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--annotations", required=True)
    parser.add_argument("--reviewer", required=True, help="actual human reviewer identifier")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    if not args.reviewer.strip():
        parser.error("reviewer cannot be blank")
    data = BenchmarkDataset.load(args.dataset)
    cases = {case.case_id: case for case in data.cases}
    labels = {key: {p.paper_id: p for p in case.papers} for key, case in cases.items()}
    count = 0
    with Path(args.annotations).open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if not row["label"].strip():
                continue
            if row["case_id"] not in cases:
                raise ValueError("annotation refers to an unknown case")
            paper = LabelledPaper(row["paper_id"], row["label"].strip(), title=row.get("title", ""), evidence_notes=row.get("evidence_notes", ""))
            prior = labels[row["case_id"]].get(paper.paper_id)
            if prior is not None and prior.label != paper.label:
                raise ValueError("conflicting judgments; resolve reviewer disagreement before import")
            labels[row["case_id"]][paper.paper_id] = paper
            cases[row["case_id"]].labelled_by = args.reviewer.strip()
            count += 1
    if not count:
        raise ValueError("no human labels supplied; blank annotation sheets cannot produce scores")
    for key, case in cases.items():
        case.papers = list(labels[key].values())
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Imported {count} judgments; unjudged candidates remain unjudged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
