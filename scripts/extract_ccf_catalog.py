"""Build a factual venue lookup from the CCF-authored 2026 PDF (pdfplumber required)."""

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path


def extract(path):
    import pdfplumber

    entries, rank, kind = [], "", ""
    with pdfplumber.open(path) as pdf:
        for page_no, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            if "推荐国际学术会议" in text:
                kind = "conference"
            elif "推荐国际学术期刊" in text:
                kind = "journal"
            match = re.search(r"[一二三]、([ABC])\s*类", text)
            if match:
                rank = match[1]
            for table in page.extract_tables():
                for row in table:
                    if len(row) < 5 or not (row[0] or "").strip().isdigit():
                        continue
                    acronym, full, url = [(row[i] or "").replace("\n", " ").strip() for i in (1, 2, 4)]
                    acronym = re.sub(r"[（(].*", "", acronym).strip()
                    entries.append({"acronym": acronym, "full_name": full, "rank": rank,
                                    "kind": kind, "url": url, "page": page_no})
    return {"edition": "2026 seventh edition", "source": "https://www.ccf.org.cn/Academic_Evaluation/By_category/",
            "pdf_mirror": "https://lib.zjgsu.edu.cn/_upload/article/files/f5/b1/f4f7201343b88c0f10564f590ebe/854a3c68-36a6-4698-a359-5184d5e30a0c.pdf",
            "pdf_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(), "entries": entries}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf")
    parser.add_argument("--output", default="litsearch/ccf_catalog.json")
    args = parser.parse_args()
    catalog = extract(args.pdf)
    Path(args.output).write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"total": len(catalog["entries"]), "kinds": dict(Counter(e["kind"] for e in catalog["entries"])), "ranks": dict(Counter(e["rank"] for e in catalog["entries"]))}))
