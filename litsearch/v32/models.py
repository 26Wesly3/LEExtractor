"""Find models that already exist on this machine; never download from here.

The pinned translation model is 116 MB and the pinned embedding model is 224 MB.
Downloading them again when a complete copy is already on disk is pure waste — and
on a machine whose copy lives outside FastEmbed's default caches, the old lookup
reported "missing" even though the files were right there. That is what this
module fixes: it *discovers* what is already present and reports it honestly,
without ever fetching anything.

Search order, first match wins per model kind:

1. ``LEEXTRACTOR_MODEL_DIR`` (explicit override)
2. ``LEEXTRACTOR_EXTRA_MODEL_DIRS`` (os.pathsep-separated list)
3. ``<repo>/models/``
4. ``~/.cache/leextractor``
5. the Hugging Face hub cache
6. FastEmbed's caches (temp, LocalAppData, ``~/.cache/fastembed``)
7. sibling ``embedding_cache`` / ``models/embedding`` directories found near the
   repository (this is where a portable/older LEExtractor keeps its copy)

A finding is only reported ``usable`` when the weights *and* the tokenizer files are
present, so a half-finished download is never mistaken for a working model.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

__all__ = [
    "ModelFinding",
    "DiscoveryReport",
    "discover_local_models",
    "candidate_roots",
    "embedding_cache_dirs",
    "discovered_extra_dirs",
]

#: Files a FastEmbed ONNX snapshot needs before it can actually run.
_EMBEDDING_WEIGHTS = "model_optimized.onnx"
_EMBEDDING_COMPANIONS = ("config.json", "tokenizer.json", "tokenizer_config.json")
#: Files the pinned Marian translation model needs.
_TRANSLATION_WEIGHTS = ("encoder_model_quantized.onnx", "decoder_model_quantized.onnx")

#: Directory names that are never worth walking.
_SKIP = frozenset({
    "node_modules", ".git", "__pycache__", ".venv", "venv", "dist", "build",
    ".pytest_cache", ".ruff_cache", "_workspace_tmp", "tests",
})
#: How deep to look for sibling model directories, and how many dirs to visit.
_MAX_DEPTH = 4
_MAX_VISITS = 4000


@dataclass
class ModelFinding:
    """One model copy found on disk."""

    kind: str                      # "translation" | "embedding"
    name: str
    path: str
    size_mb: float
    usable: bool
    missing: list[str] = field(default_factory=list)
    source: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DiscoveryReport:
    checked_roots: list[str]
    translation: list[ModelFinding]
    embedding: list[ModelFinding]

    @property
    def translation_ready(self) -> bool:
        return any(item.usable for item in self.translation)

    @property
    def semantic_ready(self) -> bool:
        return any(item.usable for item in self.embedding)

    def best(self, kind: str) -> ModelFinding | None:
        found = [item for item in (self.translation if kind == "translation" else self.embedding) if item.usable]
        return max(found, key=lambda item: item.size_mb) if found else None

    def to_dict(self) -> dict:
        return {
            "checked_roots": self.checked_roots,
            "translation_ready": self.translation_ready,
            "semantic_ready": self.semantic_ready,
            "translation": [item.to_dict() for item in self.translation],
            "embedding": [item.to_dict() for item in self.embedding],
            "best_translation": (self.best("translation").to_dict() if self.best("translation") else None),
            "best_embedding": (self.best("embedding").to_dict() if self.best("embedding") else None),
        }


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _existing(paths) -> list[Path]:
    seen: list[Path] = []
    for raw in paths:
        if not raw:
            continue
        try:
            path = Path(raw).expanduser()
        except (TypeError, ValueError):
            continue
        if path.is_dir() and path not in seen:
            seen.append(path)
    return seen


def discovered_extra_dirs(around: Path | None = None) -> list[Path]:
    """Sibling model directories that a portable install or an older build left behind.

    Deliberately bounded: this runs when the user opens the app or asks for
    capabilities, so it must never walk a whole drive. Only directory *names*
    matter, and only a handful of levels are visited.
    """
    base = (around or _repo_root()).resolve()
    # Look one level above the repo (the workspace root) and at the repo itself.
    starts = [base, *list(base.parents)[:2]]
    wanted: list[Path] = []
    visited = 0
    for start in starts:
        if not start.is_dir():
            continue
        for current, dirs, _files in os.walk(start):
            visited += 1
            if visited > _MAX_VISITS:
                return _existing(wanted)
            depth = len(Path(current).relative_to(start).parts)
            dirs[:] = [
                name for name in dirs
                if name not in _SKIP and depth < _MAX_DEPTH
            ]
            name = Path(current).name
            if name in {"embedding_cache", "models--qdrant--paraphrase-multilingual-MiniLM-L12-v2-onnx-Q"}:
                wanted.append(Path(current))
            elif name == "embedding" and Path(current).parent.name == "models":
                wanted.append(Path(current))
    return _existing(wanted)


def candidate_roots() -> list[Path]:
    """Every root worth looking in for a model, in priority order."""
    roots: list[Path] = []
    if os.environ.get("LEEXTRACTOR_MODEL_DIR"):
        roots.append(Path(os.environ["LEEXTRACTOR_MODEL_DIR"]))
    for raw in os.environ.get("LEEXTRACTOR_EXTRA_MODEL_DIRS", "").split(os.pathsep):
        if raw.strip():
            roots.append(Path(raw.strip()))
    roots.append(_repo_root() / "models")
    roots.append(_repo_root() / "models" / "embedding")
    roots.append(Path.home() / ".cache" / "leextractor")
    roots.append(Path.home() / ".cache" / "huggingface" / "hub")
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        roots.append(Path(local_appdata) / "Temp" / "fastembed_cache")
        roots.append(Path(local_appdata) / "fastembed_cache")
    roots.append(Path.home() / ".cache" / "fastembed")
    import tempfile

    roots.append(Path(tempfile.gettempdir()) / "fastembed_cache")
    roots.extend(discovered_extra_dirs())
    return _existing(roots)


def embedding_cache_dirs() -> list[Path]:
    """Roots that may contain a ``models--*/snapshots/*`` FastEmbed layout."""
    return candidate_roots()


def _snapshot_dirs(root: Path) -> list[Path]:
    """``models--*/snapshots/<rev>`` directories under a cache root, plus the root itself."""
    found: list[Path] = []
    if (root / _EMBEDDING_WEIGHTS).is_file():
        found.append(root)
    for snapshots in root.glob("models--*/snapshots"):
        for revision in snapshots.iterdir() if snapshots.is_dir() else []:
            if revision.is_dir():
                found.append(revision)
    if (root / "snapshots").is_dir():
        for revision in (root / "snapshots").iterdir():
            if revision.is_dir():
                found.append(revision)
    return found


def _embedding_findings(roots: list[Path]) -> list[ModelFinding]:
    findings: list[ModelFinding] = []
    seen: set[str] = set()
    for root in roots:
        for snapshot in _snapshot_dirs(root):
            key = str(snapshot.resolve()) if snapshot.exists() else str(snapshot)
            if key in seen:
                continue
            seen.add(key)
            weights = snapshot / _EMBEDDING_WEIGHTS
            if not weights.is_file():
                continue
            missing = [name for name in _EMBEDDING_COMPANIONS if not (snapshot / name).is_file()]
            # A truncated download leaves a 0-byte or tiny onnx; treat that as unusable.
            size_mb = round(weights.stat().st_size / 1024 / 1024, 1)
            if size_mb < 1:
                missing.append(f"{_EMBEDDING_WEIGHTS} (truncated: {size_mb} MB)")
            findings.append(ModelFinding(
                kind="embedding",
                name=snapshot.parent.parent.name if snapshot.parent.name == "snapshots" else snapshot.name,
                path=str(snapshot),
                size_mb=size_mb,
                usable=not missing,
                missing=missing,
                source=str(root),
            ))
    return findings


def _translation_findings(roots: list[Path]) -> list[ModelFinding]:
    findings: list[ModelFinding] = []
    seen: set[str] = set()
    for root in roots:
        for onnx_dir in [root, *root.glob("opus-mt-zh-en/onnx"), *root.glob("*/onnx")]:
            if not onnx_dir.is_dir():
                continue
            key = str(onnx_dir)
            if key in seen:
                continue
            seen.add(key)
            present = [name for name in _TRANSLATION_WEIGHTS if (onnx_dir / name).is_file()]
            if not present:
                continue
            missing = [name for name in _TRANSLATION_WEIGHTS if name not in present]
            total = sum((onnx_dir / name).stat().st_size for name in present) / 1024 / 1024
            findings.append(ModelFinding(
                kind="translation",
                name="opus-mt-zh-en",
                path=str(onnx_dir),
                size_mb=round(total, 1),
                usable=not missing,
                missing=missing,
                source=str(root),
            ))
    return findings


def discover_local_models(roots: list[Path] | None = None) -> DiscoveryReport:
    """Report every model already on disk.  Reads only; never downloads."""
    checked = list(roots) if roots is not None else candidate_roots()
    return DiscoveryReport(
        checked_roots=[str(path) for path in checked],
        translation=_translation_findings(checked),
        embedding=_embedding_findings(checked),
    )
