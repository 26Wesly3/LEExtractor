"""The source release carries a runnable Web UI and resolvable documentation."""

from __future__ import annotations

import html
import posixpath
import re
import sys
import zipfile
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

import pytest
from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import package_release


class LocalReferences(HTMLParser):
    def __init__(self):
        super().__init__()
        self.targets: list[str] = []

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name in {"src", "href"} and value:
                self.targets.append(value)

    handle_startendtag = handle_starttag


def markdown_targets(text: str) -> list[str]:
    """Read actual rendered links/images, not names in prose or code examples."""
    targets = []

    def visit(token):
        if token.type == "link_open":
            targets.append(token.attrGet("href"))
        elif token.type == "image":
            targets.append(token.attrGet("src"))
        elif token.type in {"html_inline", "html_block"}:
            parser = LocalReferences()
            parser.feed(token.content)
            targets.extend(parser.targets)
        for child in token.children or []:
            visit(child)

    for token in MarkdownIt("commonmark", {"html": True}).parse(text):
        visit(token)
    return [target for target in targets if target]


def relative_target(document: str, target: str, *, served_root: str | None = None) -> str | None:
    parsed = urlsplit(html.unescape(target))
    # Absolute filesystem links are not portable release links. Other URL
    # schemes (including data icons and mail links) do not name package files.
    if parsed.scheme == "file" or (len(parsed.scheme) == 1 and parsed.scheme.isalpha()):
        raise AssertionError(f"{document} has a non-portable filesystem link: {target}")
    if parsed.scheme or parsed.netloc or not parsed.path:
        return None
    path = unquote(parsed.path).replace("\\", "/")
    if path.startswith("/"):
        if served_root is None:
            raise AssertionError(f"{document} has an absolute local link: {target}")
        path = posixpath.join(served_root, path.lstrip("/"))
    else:
        path = posixpath.join(posixpath.dirname(document), path)
    path = posixpath.normpath(path)
    assert path != ".." and not path.startswith("../"), f"{document} escapes the archive: {target}"
    return path


@pytest.fixture(scope="module")
def source_archive():
    # A module-specific output avoids the shared tmp_path fixture and rebuilds
    # the actual source ZIP once for all the archive checks below.
    output = ROOT / "tests" / "_workspace_tmp" / "web_release_contract"
    summary = package_release.package(ROOT, output, prefix="LEExtractor")
    with zipfile.ZipFile(summary["path"]) as archive:
        assert archive.testzip() is None
        files = {name.removeprefix("LEExtractor/"): archive.read(name)
                 for name in archive.namelist() if name.startswith("LEExtractor/")}
    return files


def test_web_source_release_contains_ui_source_built_bundle_and_startup(source_archive):
    required = {
        "web/dist/index.html", "web/src/App.vue", "web/src/main.js", "web/src/api.js",
        "web/package.json", "web/package-lock.json", "web/vite.config.js",
        "启动Web版.bat", "docs/web-integration.md", "scripts/sync_web_assets.py",
        "litsearch/web/api.py", "litsearch/web/jobs.py", "litsearch/web/repository.py",
    }
    assert not (required - source_archive.keys()), f"release omits {sorted(required - source_archive.keys())}"
    assert any(name.endswith(".vue") and name.startswith("web/src/") for name in source_archive)


def test_built_html_and_styles_references_resolve_inside_source_zip(source_archive):
    index_name = "web/dist/index.html"
    assert index_name in source_archive
    parser = LocalReferences()
    parser.feed(source_archive[index_name].decode("utf-8"))
    assets = [relative_target(index_name, target, served_root="web/dist") for target in parser.targets]
    assets = [asset for asset in assets if asset is not None]
    assert any(asset.endswith(".js") for asset in assets), "the HTML has no built JavaScript bundle"
    assert any(asset.endswith(".css") for asset in assets), "the HTML has no built stylesheet"
    for asset in assets:
        assert asset in source_archive, f"built HTML references an omitted asset: {asset}"
        assert source_archive[asset], f"empty built asset: {asset}"
    for name, content in source_archive.items():
        if not (name.startswith("web/dist/") and name.endswith(".css")):
            continue
        # CSS url() is an asset reference, unlike a filename in Markdown prose.
        for target in re.findall(r"url\(\s*['\"]?([^'\")]+)['\"]?\s*\)", content.decode("utf-8")):
            asset = relative_target(name, target.strip(), served_root="web/dist")
            if asset:
                assert asset in source_archive, f"built CSS references an omitted asset: {name} -> {asset}"


def test_root_documents_and_web_guide_links_resolve_in_source_zip(source_archive):
    root_docs = [name for name in source_archive if name.endswith(".md") and len(PurePosixPath(name).parts) == 1]
    assert "docs/web-integration.md" in source_archive
    documents = [*root_docs, "docs/web-integration.md"]
    missing = []
    for document in documents:
        for target in markdown_targets(source_archive[document].decode("utf-8")):
            local = relative_target(document, target)
            if local is not None and local not in source_archive:
                missing.append(f"{document} -> {target} ({local})")
    assert not missing, "unresolvable local Markdown links:\n" + "\n".join(missing)


def test_source_zip_excludes_credentials_runtime_data_and_node_modules(source_archive):
    bad = []
    forbidden_parts = {"node_modules", ".web-data", ".sessions", ".venv", ".git",
                       "__pycache__", "_workspace_tmp", ".pytest_cache"}
    for name in source_archive:
        path = PurePosixPath(name)
        if (set(path.parts) & forbidden_parts
                or path.name in {"secrets.toml", "settings.json", "autosave.json", ".env"}
                or (path.name.startswith(".env.") and path.name != ".env.example")
                or path.suffix in {".db", ".sqlite", ".pyc", ".log", ".part"}):
            bad.append(name)
    assert not bad, f"release contains credentials/runtime artifacts: {bad}"


def test_secret_and_runtime_canaries_are_never_packaged(tmp_path):
    fixture = tmp_path / "source"
    (fixture / "litsearch").mkdir(parents=True)
    (fixture / "litsearch" / "version.py").write_text(
        '__version__ = "0.9.7"\nAPP_NAME = "LEExtractor"\n', encoding="utf-8")
    private_files = (
        ".env", ".env.local", "web/.env.production", "web/node_modules/example/index.js",
        "web/.web-data/projects/private.json", "docs/.sessions/autosave.json",
        "litsearch/.web-data/settings.json", "web/cache.db",
    )
    for name in private_files:
        path = fixture / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("PRIVATE-RELEASE-CANARY", encoding="utf-8")
    (fixture / ".env.example").write_text("S2_API_KEY=\n", encoding="utf-8")
    bundle = fixture / "web" / "dist" / "index.html"
    bundle.parent.mkdir(parents=True, exist_ok=True)
    bundle.write_text("<html>fixture interface</html>", encoding="utf-8")
    summary = package_release.package(fixture, tmp_path / "output")
    with zipfile.ZipFile(summary["path"]) as archive:
        names = archive.namelist()
        assert "LEExtractor/.env.example" in names
        assert "LEExtractor/web/dist/index.html" in names
        for name in names:
            assert b"PRIVATE-RELEASE-CANARY" not in archive.read(name), f"private file was packaged: {name}"


def test_markdown_link_reader_ignores_prose_code_and_resolves_reference_links():
    text = """A planned missing_module.py and `planned.md` are plain text.
`[not a link](inline-code.md)`

```md
[not a link](fenced-code.md)
```

[Guide][guide] and ![Preview](images/preview.png).
<a href="../README.md">Home</a>

[guide]: docs/guide.md
"""
    assert markdown_targets(text) == ["docs/guide.md", "images/preview.png", "../README.md"]
