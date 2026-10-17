"""把插件目录里的文本文件统一成 UTF-8（无 BOM），并报告哪些原本不是 UTF-8。"""
from pathlib import Path

SUFFIXES = {".json", ".js", ".ftl", ".md", ".mjs"}
ROOT = Path(".")

fixed = []
for path in sorted(ROOT.rglob("*")):
    if not path.is_file() or path.suffix not in SUFFIXES or "dist" in path.parts:
        continue
    raw = path.read_bytes()
    if raw[:3] == b"\xef\xbb\xbf":
        text = raw[3:].decode("utf-8")
        path.write_text(text, encoding="utf-8")
        fixed.append(f"{path} (stripped BOM)")
        continue
    try:
        text = raw.decode("utf-8")
        if path.suffix in {".json", ".js", ".ftl", ".md", ".mjs"}:
            path.write_text(text, encoding="utf-8")  # normalise newlines/encoding
    except UnicodeDecodeError:
        for encoding in ("gbk", "gb18030", "latin-1"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        path.write_text(text, encoding="utf-8")
        fixed.append(f"{path} ({encoding} -> utf-8)")

print("fixed:", len(fixed))
for line in fixed:
    print(" ", line)
