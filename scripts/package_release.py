"""Package code and docs only; exclude local secrets, sessions and runtimes."""

import json
import os
import zipfile
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    output = root / "deliverables" / "LEExtractor_v0.8.0_优化版.zip"
    output.parent.mkdir(exist_ok=True)
    excluded = {".git", ".venv", ".reference", "artifacts", "deliverables", ".sessions", "__pycache__", ".pytest_cache", ".ruff_cache", "litsearch.egg-info", "build", "dist"}
    files = []
    for directory, dirs, names in os.walk(root):
        dirs[:] = [name for name in dirs if name not in excluded]
        for name in names:
            path = Path(directory) / name
            if name not in {".env", "secrets.toml"} and path.suffix not in {".db", ".pyc", ".pyo"}:
                files.append(path)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            archive.write(path, "LEExtractor/" + path.relative_to(root).as_posix())
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        assert not any(Path(name).name in {".env", "secrets.toml"} or "/.sessions/" in name or "/.venv/" in name for name in archive.namelist())
    print(json.dumps({"path": str(output), "files": len(files), "bytes": output.stat().st_size}, ensure_ascii=False))


if __name__ == "__main__":
    main()
