"""Conservative venue classification from the CCF-authored 2026 catalogue."""

import json
import re
from functools import lru_cache
from pathlib import Path


def normalized(text):
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


@lru_cache(maxsize=1)
def catalog():
    return json.loads(Path(__file__).with_name("ccf_catalog.json").read_text(encoding="utf-8"))


def venue_classification(paper):
    return dict(_classify(paper.venue or "", paper.identifiers.dblp_key))


@lru_cache(maxsize=4096)
def _classify(venue, key):
    base = {"rank": None, "edition": "2026", "eligibility": "unverified",
            "source": catalog()["source"], "note": "会议／期刊级别，论文类型与正式发表状态需另行核查。"}
    excluded = re.search(r"\b(findings|workshops?|short papers?|demo|technical brief|summary|nier)\b", venue, re.I)
    candidates = []
    for entry in catalog()["entries"]:
        full = normalized(entry["full_name"])
        acronym = entry["acronym"]
        aliases = [acronym]
        if acronym == "NeurIPS":
            aliases.append("NIPS")
        if acronym == "ACM SIGOPS ATC":
            aliases.extend(["USENIX ATC", "USENIX Annual Technical Conference"])
        stream = re.search(r"/db/(conf|journals)/([^/\s]+)", entry["url"])
        by_key = key and stream and key.startswith(f"{stream[1]}/{stream[2]}/")
        exact = full and normalized(venue) == full or any(normalized(venue) == normalized(a) for a in aliases if a)
        acronym_match = any(len(a) >= 4 and re.search(r"(?<![A-Za-z])" + re.escape(a) + r"(?![A-Za-z])", venue, re.I) for a in aliases if a)
        if by_key or exact or acronym_match:
            if excluded and not exact:
                return dict(base, eligibility="excluded_track", note="Workshop、Findings、短文等不按主会议级别认定。")
            candidates.append(entry)
    unique = {(e["rank"], e["kind"]) for e in candidates}
    if len(unique) != 1:
        return base
    entry = candidates[0]
    return dict(base, rank=entry["rank"], kind=entry["kind"], acronym=entry["acronym"],
                page=entry["page"], pdf_source=catalog()["pdf_mirror"])
