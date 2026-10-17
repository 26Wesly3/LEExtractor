"""Report which models already exist on this machine.  Never downloads anything.

    python scripts/v32_models.py            # human-readable
    python scripts/v32_models.py --json     # machine-readable

The pinned translation model is 116 MB and the embedding model 224 MB, so
re-downloading a copy that is already on disk is pure waste. This prints every
copy found, where it came from, and whether it is actually runnable (weights AND
tokenizer files present, non-truncated).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from litsearch.v32.models import discover_local_models  # noqa: E402


def main(argv: list[str]) -> int:
    as_json = "--json" in argv
    report = discover_local_models()
    if as_json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        return 0

    print("搜索过的根目录：")
    for root in report.checked_roots:
        print(f"  - {root}")
    print()

    def show(title: str, findings: list, ready: bool) -> None:
        print(f"{title}：{'可用' if ready else '未找到可用副本'}")
        if not findings:
            print("  （没有找到任何副本）")
        for item in findings:
            state = "可用" if item.usable else "不可用"
            print(f"  - {state}  {item.size_mb} MB  {item.path}")
            if item.missing:
                print(f"      缺少：{', '.join(item.missing)}")
        print()

    show("中译英模型", report.translation, report.translation_ready)
    show("语义嵌入模型", report.embedding, report.semantic_ready)

    print("提示：本脚本只做发现，不下载。要指定自定义位置，设置")
    print("  LEEXTRACTOR_MODEL_DIR=<目录>  或  LEEXTRACTOR_EXTRA_MODEL_DIRS=<目录1;目录2>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
