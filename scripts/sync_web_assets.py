"""Copy the built frontend into the Python wheel's package data."""

from pathlib import Path
from shutil import copytree

ROOT = Path(__file__).resolve().parents[1]


def main():
    source = ROOT / "web" / "dist"
    target = ROOT / "litsearch" / "web" / "static"
    if target.is_symlink() or not target.resolve().is_relative_to(ROOT.resolve()):
        raise SystemExit("Refusing to replace a Web asset directory outside the project")
    if not (source / "index.html").exists():
        raise SystemExit("Build web/ with npm run build first")
    # A content-hashed asset supersedes older hashes. Keep only the current
    # bundle; this directory contains generated assets, never project state.
    target.mkdir(parents=True, exist_ok=True)
    for path in sorted(target.rglob("*"), reverse=True):
        if path.is_file():
            path.unlink()
        elif path.is_dir():
            path.rmdir()
    copytree(source, target, dirs_exist_ok=True)
    print(f"Bundled {sum(p.is_file() for p in target.rglob('*'))} Web files")


if __name__ == "__main__":
    main()
