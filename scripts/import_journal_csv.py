"""把本地期刊数据（含影响因子）导入期刊缓存。

    python scripts/import_journal_csv.py journals.csv
    python scripts/import_journal_csv.py journals.csv --root .web-data

CSV 列名不分大小写、顺序随意，可用的有：
    name / venue / journal / 期刊        期刊名（必填）
    impact_factor / IF / jif / 影响因子   影响因子
    quartile / 分区                      JCR 分区（如 Q1）
    issn, year                          可选

导入只合并元数据，不会改动检索计数，也不会联网。同一份文件重复导入是幂等的。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from litsearch.v32 import venue_cache  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="导入本地期刊数据到缓存")
    parser.add_argument("file", help="CSV/TSV 文件路径")
    parser.add_argument("--root", default=str(ROOT / ".web-data"), help="数据目录（默认 .web-data）")
    args = parser.parse_args()

    source = Path(args.file)
    if not source.exists():
        print(f"找不到文件：{source}", file=sys.stderr)
        return 2
    result = venue_cache.import_csv(Path(args.root), source)
    rows = [row for row in venue_cache.summary(Path(args.root), limit=5) if row.get("impact_factor")]
    print(json.dumps({**result, "cache": str(venue_cache.cache_path(Path(args.root))), "sample": rows}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
