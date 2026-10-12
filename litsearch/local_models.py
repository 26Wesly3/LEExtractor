"""Local CPU translation and title/abstract embeddings. No inference API calls."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import threading
from functools import lru_cache
from pathlib import Path

import numpy as np
import requests

EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
EMBEDDING_REPOSITORY = "qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q"
EMBEDDING_REVISION = "faf4aa4225822f3bc6376869cb1164e8e3feedd0"
TRANSLATION_MODEL = "Xenova/opus-mt-zh-en"
TRANSLATION_REVISION = "39d480d52a9ea3065a1f117adfe4dbc55de10e6f"
_LOCK = threading.RLock()
_UNSET_CACHE = object()


def models_root() -> Path:
    return Path(os.environ.get("LEEXTRACTOR_MODEL_DIR", Path.home() / ".cache" / "leextractor"))


def _existing_dirs(candidates) -> list[Path]:
    """Keep existing directories, de-duplicated, in the given order."""
    seen: set[Path] = set()
    ordered: list[Path] = []
    for candidate in candidates:
        if not candidate:
            continue
        try:
            path = Path(candidate).expanduser().resolve()
        except OSError:
            continue
        if path in seen or not path.is_dir():
            continue
        seen.add(path)
        ordered.append(path)
    return ordered


def project_model_dir() -> Path:
    """The ``models`` folder shipped inside a handoff package, next to the source."""
    return Path(__file__).resolve().parents[1] / "models"


def fastembed_cache_dirs() -> list[Path]:
    """Every place FastEmbed/TraceRAG is known to keep its cache.

    ``tempfile.gettempdir()`` is not always ``%TEMP%`` (an embedded launcher or a
    service can change it), so the Windows user temp is listed explicitly as well.
    """
    candidates = [Path(os.environ.get("LEEXTRACTOR_EMBEDDING_CACHE", "") or "")] if os.environ.get("LEEXTRACTOR_EMBEDDING_CACHE") else []
    candidates.append(Path(tempfile.gettempdir()) / "fastembed_cache")
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        candidates.append(Path(local_appdata) / "Temp" / "fastembed_cache")
        candidates.append(Path(local_appdata) / "fastembed_cache")
    candidates.append(Path.home() / ".cache" / "fastembed")
    return _existing_dirs(candidates)


def model_search_roots() -> list[Path]:
    """Ordered local roots searched before anything is downloaded.

    Explicit override → handoff-package ``models/`` → user cache → Hugging Face
    hub cache → FastEmbed caches. Detection runs from the library itself so the
    backend finds already-present models even when it is started without the
    launcher (which is the only place that used to set the environment).
    """
    hf_home = Path(os.environ.get("HF_HOME") or (Path.home() / ".cache" / "huggingface"))
    roots = [os.environ.get("LEEXTRACTOR_MODEL_DIR"), project_model_dir(), Path.home() / ".cache" / "leextractor", hf_home / "hub", *fastembed_cache_dirs()]
    if os.environ.get("LEEXTRACTOR_EMBEDDING_CACHE"):
        roots.append(os.environ["LEEXTRACTOR_EMBEDDING_CACHE"])
    # The handoff package keeps embeddings in models/embedding; treat that as a root too.
    roots.extend(project_model_dir() / "embedding" for _ in (0,))
    return _existing_dirs(roots)


def translation_candidates(roots=None):
    """Directories that may already hold the pinned Marian files."""
    for root in (model_search_roots() if roots is None else _existing_dirs(roots)):
        yield Path(root) / "opus-mt-zh-en"
        yield Path(root) / "models--Xenova--opus-mt-zh-en" / "snapshots" / TRANSLATION_REVISION


def find_translation_dir(pins, roots=None) -> Path | None:
    """First candidate holding every pinned file with the pinned digest."""
    for candidate in translation_candidates(roots):
        try:
            if all((candidate / name).is_file() and hashlib.sha256((candidate / name).read_bytes()).hexdigest() == info["sha256"] for name, info in pins["files"].items()):
                return candidate
        except OSError:
            continue
    return None


def embedding_candidates(cache_dirs=None):
    """Cache directories to probe with ``local_files_only`` before downloading."""
    dirs = fastembed_cache_dirs() if cache_dirs is None else list(cache_dirs)
    expanded = []
    for directory in dirs:
        expanded.extend([directory, Path(directory) / "embedding"])
    return _existing_dirs(expanded)


def find_embedding_snapshot(download, cache_dirs=None) -> tuple[str, str] | None:
    """``(snapshot_path, cache_dir)`` for the first cache that already has the weights.

    ``download`` is ``huggingface_hub.snapshot_download``; it is called with
    ``local_files_only=True`` so this never touches the network.
    """
    for cache in embedding_candidates(cache_dirs):
        try:
            path = download(repo_id=EMBEDDING_REPOSITORY, revision=EMBEDDING_REVISION, cache_dir=str(cache), allow_patterns=["*.json", "model_optimized.onnx"], local_files_only=True)
        except Exception:
            continue
        if (Path(path) / "model_optimized.onnx").is_file():
            return str(path), str(cache)
    return None


def looks_like_proxy_failure(exc) -> bool:
    text = f"{type(exc).__name__}: {exc}"
    return any(marker in text for marker in ("Invalid port", "InvalidURL", "proxy", "Proxy", "ALL_PROXY"))


def download_through_proxy_fallback(fetch, **options):
    """Download, retrying without the environment's proxy when it cannot be parsed.

    A malformed ``HTTP(S)_PROXY`` value (for example one whose port is not a number)
    makes httpx raise before a single request is sent, which used to surface as an
    unexplained "model load failed". A local-first tool should still download.
    """
    try:
        return fetch(**options)
    except Exception as exc:
        if not looks_like_proxy_failure(exc):
            raise
        saved = {}
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy", "NO_PROXY", "no_proxy"):
            if name in os.environ:
                saved[name] = os.environ.pop(name)
        try:
            return fetch(**options)
        finally:
            os.environ.update(saved)


def embedding_failure_message(exc) -> str:
    roots = [str(path) for path in model_search_roots()]
    shown = "、".join(roots[:4]) + ("…" if len(roots) > 4 else "") if roots else "（没有任何可见的本地缓存目录）"
    return (f"本地 Embedding 模型加载失败；已自动查找本地模型：{shown}；"
            f"首次使用需下载约 252 MB（{type(exc).__name__}: {exc}）。也可切换关键词排序继续。")


def first_translation_dir(pins, roots=None) -> Path | None:
    """Path-only probe (no digest reads): used by the health diagnostics."""
    for candidate in translation_candidates(roots):
        if all((candidate / name).is_file() for name in pins["files"]):
            return candidate
    return None


def first_embedding_snapshot_dir(cache_dirs=None) -> Path | None:
    """Path-only probe for an already-downloaded pinned embedding revision."""
    folder = "models--" + EMBEDDING_REPOSITORY.replace("/", "--")
    for cache in embedding_candidates(cache_dirs):
        for candidate in (Path(cache) / folder / "snapshots" / EMBEDDING_REVISION, Path(cache)):
            if (candidate / "model_optimized.onnx").is_file():
                return candidate
    return None


def vector_cache_candidates() -> list[Path]:
    """Writable places for the vector cache, most specific first.

    The cache is an optimisation: a read-only user cache (restricted install,
    service account, sandboxed host) must never turn into "model load failed".
    """
    explicit = os.environ.get("LEEXTRACTOR_VECTOR_CACHE", "").strip()
    candidates = ([Path(explicit)] if explicit else []) + [models_root() / "embeddings.db", Path(tempfile.gettempdir()) / "leextractor" / "embeddings.db"]
    return candidates


def is_writable_directory(directory: Path) -> bool:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ".dsh-write-probe"
        probe.write_bytes(b"1")
        probe.unlink()
        return True
    except OSError:
        return False


def vector_cache_path() -> Path | None:
    for candidate in vector_cache_candidates():
        if is_writable_directory(candidate.parent):
            return candidate
    return None


def model_diagnostics() -> dict:
    """What the backend detected locally — safe to call on every health check."""
    pins = json.loads(Path(__file__).with_name("translation_pins.json").read_text())
    translation = first_translation_dir(pins)
    embedding = first_embedding_snapshot_dir()
    return {
        "search_roots": [str(path) for path in model_search_roots()],
        "translation": {"ready": bool(translation), "path": str(translation) if translation else ""},
        "embedding": {"ready": bool(embedding), "path": str(embedding) if embedding else ""},
        "embedding_revision": EMBEDDING_REVISION,
        "translation_revision": TRANSLATION_REVISION,
        "vector_cache": str(vector_cache_path() or ""),
    }


class LocalModelError(RuntimeError):
    pass


class LocalTranslator:
    """Pinned, quantized Marian encoder/decoder, greedy deterministic CPU inference."""

    def __init__(self):
        self.loaded = False

    def _load(self):
        if self.loaded:
            return
        import onnxruntime as ort
        import sentencepiece as spm

        pins = json.loads(Path(__file__).with_name("translation_pins.json").read_text())
        # Reuse any complete copy that is already on this machine (handoff package,
        # user cache, Hugging Face hub cache, FastEmbed cache) before downloading.
        root = find_translation_dir(pins)
        if root is None:
            root = models_root() / "opus-mt-zh-en"
            for name, info in pins["files"].items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                if not path.exists():
                    temp = path.with_suffix(path.suffix + ".part")
                    try:
                        with requests.get(f"https://huggingface.co/{TRANSLATION_MODEL}/resolve/{TRANSLATION_REVISION}/{name}", stream=True, timeout=(10, 60)) as response:
                            response.raise_for_status()
                            with temp.open("wb") as stream:
                                total = 0
                                for chunk in response.iter_content(1024 * 1024):
                                    total += len(chunk)
                                    if total > info["bytes"]:
                                        raise LocalModelError("模型文件大小不符")
                                    stream.write(chunk)
                        if hashlib.sha256(temp.read_bytes()).hexdigest() != info["sha256"]:
                            raise LocalModelError("模型文件校验失败")
                        temp.replace(path)
                    finally:
                        temp.unlink(missing_ok=True)
                if hashlib.sha256(path.read_bytes()).hexdigest() != info["sha256"]:
                    raise LocalModelError(f"本地翻译模型损坏，请重新下载：{path}")
        self.model_dir = root
        self.config = json.loads((root / "config.json").read_text())
        self.vocab = json.loads((root / "vocab.json").read_text())
        self.inverse = {value: key for key, value in self.vocab.items()}
        self.source = spm.SentencePieceProcessor(model_proto=(root / "source.spm").read_bytes())
        self.target = spm.SentencePieceProcessor(model_proto=(root / "target.spm").read_bytes())
        options = ort.SessionOptions()
        options.intra_op_num_threads = 4
        self.encoder = ort.InferenceSession(str(root / "onnx/encoder_model_quantized.onnx"), options, providers=["CPUExecutionProvider"])
        self.decoder = ort.InferenceSession(str(root / "onnx/decoder_model_quantized.onnx"), options, providers=["CPUExecutionProvider"])
        self.loaded = True

    @lru_cache(maxsize=256)  # noqa: B019 - the process owns one translator singleton
    def translate(self, text: str) -> str:
        if not re.search(r"[\u3400-\u9fff]", text):
            return text.strip()
        with _LOCK:
            try:
                self._load()
                pieces = self.source.encode(text.strip(), out_type=str)
                if len(pieces) > 256:
                    raise LocalModelError("研究方向过长，请缩短到 256 个模型词元以内")
                ids = np.array([[self.vocab.get(p, self.vocab["<unk>"]) for p in pieces] + [self.config["eos_token_id"]]], dtype=np.int64)
                mask = np.ones_like(ids)
                hidden = self.encoder.run(None, {"input_ids": ids, "attention_mask": mask})[0]
                decoded = [self.config["decoder_start_token_id"]]
                for _ in range(128):
                    logits = self.decoder.run(["logits"], {"input_ids": np.array([decoded], dtype=np.int64), "encoder_hidden_states": hidden, "encoder_attention_mask": mask})[0]
                    token = int(np.argmax(logits[0, -1]))
                    if token == self.config["eos_token_id"]:
                        break
                    decoded.append(token)
                else:
                    raise LocalModelError("译文超过长度限制，请缩短研究方向")
                result = self.target.decode_pieces([self.inverse[i] for i in decoded[1:]]).strip()
                if "多智能体" in text:
                    result = re.sub(r"multi[- ]intelligen\w*|multiple intelligent agents?", "multi-agent", result, flags=re.I)
                    if "multi-agent" not in result.lower():
                        result += " (multi-agent)"
                if "计算机视觉" in text:
                    result = re.sub(r"computer visuals?|computational vision", "computer vision", result, flags=re.I)
                    if "computer vision" not in result.lower():
                        result += " (computer vision)"
                if not result or re.search(r"[\u3400-\u9fff]", result):
                    raise LocalModelError("没有得到有效英文译文，请手动填写英文检索词")
                return result
            except LocalModelError:
                raise
            except Exception as exc:
                raise LocalModelError("本地翻译未完成；首次使用需下载约 116 MB 模型。可手动填写英文检索词后继续。") from exc


TRANSLATOR = LocalTranslator()


class LocalEmbedding:
    """Reuse TraceRAG's FastEmbed model; cache vectors by model and exact text."""

    def __init__(self):
        self.model = None
        self.tokenizer = None
        self._cache_db = _UNSET_CACHE
        self.vector_cache = ""

    def _load(self):
        if self.model is None:
            from fastembed import TextEmbedding
            from huggingface_hub import snapshot_download

            # Reuse any cache that already holds the pinned revision (FastEmbed's own
            # cache, the handoff package's models/embedding, the user cache, HF hub),
            # and only then fall back to downloading.
            found = find_embedding_snapshot(snapshot_download)
            if found:
                path, cache = found
            else:
                cache = os.environ.get("LEEXTRACTOR_EMBEDDING_CACHE") or str(models_root() / "embedding")
                Path(cache).mkdir(parents=True, exist_ok=True)
                options = {"repo_id": EMBEDDING_REPOSITORY, "revision": EMBEDDING_REVISION, "cache_dir": cache,
                           "allow_patterns": ["*.json", "model_optimized.onnx"]}
                try:
                    path = download_through_proxy_fallback(snapshot_download, **options)
                except Exception as exc:
                    raise LocalModelError(embedding_failure_message(exc)) from exc
            self.cache_dir = cache
            from tokenizers import Tokenizer
            tokenizer = Tokenizer.from_str((Path(path) / "tokenizer.json").read_text(encoding="utf-8"))
            tokenizer.no_truncation()
            tokenizer.no_padding()
            model = TextEmbedding(model_name=EMBEDDING_MODEL, specific_model_path=path,
                                  cache_dir=cache, threads=4, providers=["CPUExecutionProvider"])
            self.model, self.tokenizer = model, tokenizer

    def ensure_ready(self):
        with _LOCK:
            try:
                self._load()
            except LocalModelError:
                raise
            except Exception as exc:
                raise LocalModelError(embedding_failure_message(exc)) from exc

    def vectors(self, texts: list[str]) -> np.ndarray:
        with _LOCK:
            try:
                self._load()
            except LocalModelError:
                raise
            except Exception as exc:
                raise LocalModelError(embedding_failure_message(exc)) from exc
            keys = [hashlib.sha256((EMBEDDING_MODEL + EMBEDDING_REVISION + "mean_pool_v0.8\n" + t).encode()).hexdigest() for t in texts]
            saved: list[tuple | None] = [None] * len(keys)
            db, db_path = None, self._cache_path()
            if db_path is not None and texts:
                try:
                    db = sqlite3.connect(db_path, timeout=30)
                    db.execute("CREATE TABLE IF NOT EXISTS vectors (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                    saved = [db.execute("SELECT value FROM vectors WHERE key=?", (k,)).fetchone() for k in keys]
                except sqlite3.Error:
                    # 只读/损坏的缓存库：当作没有缓存，继续用内存结果。
                    if db is not None:
                        db.close()
                    db, saved = None, [None] * len(keys)
            try:
                missing = list(dict.fromkeys(i for i, value in enumerate(saved) if value is None))
                if missing:
                    produced = list(self.model.embed([texts[i] for i in missing], batch_size=16))
                    for i, vector in zip(missing, produced, strict=True):
                        value = json.dumps(vector.tolist())
                        saved[i] = (value,)
                        if db is not None:
                            try:
                                db.execute("INSERT OR REPLACE INTO vectors VALUES (?,?)", (keys[i], value))
                            except sqlite3.Error:
                                db.close()
                                db = None
            finally:
                if db is not None:
                    try:
                        db.commit()
                    except sqlite3.Error:
                        pass
                    db.close()
            self.vector_cache = str(db_path) if db_path else ""
            return np.array([json.loads(row[0]) for row in saved], dtype=np.float32)

    def _cache_path(self) -> Path | None:
        if self._cache_db is _UNSET_CACHE:
            self._cache_db = vector_cache_path()
        return self._cache_db

    def _chunks(self, text: str) -> list[str]:
        # This model's tokenizer caps inputs at 128 tokens, despite a declared
        # 512-token architecture. Reserve space for special tokens and count
        # with its actual tokenizer; whitespace counts fail for Chinese text.
        count = (lambda s: len(self.tokenizer.encode(s, add_special_tokens=False).ids)) if self.tokenizer else len
        def split(s):
            if count(s) <= 120:
                return [s]
            middle = len(s) // 2
            boundary = s.rfind(" ", 0, middle)
            cut = boundary + 1 if boundary > middle // 2 else max(1, middle)
            return split(s[:cut]) + split(s[cut:])
        return split(text)

    def scores(self, papers, query: str) -> list[float]:
        # Warm up through the same injectable vector interface, then split
        # with the loaded tokenizer. Long research directions are covered too.
        first_query = self.vectors([query])[0]
        query_chunks = self._chunks(query)
        query_vectors = self.vectors(query_chunks) if len(query_chunks) > 1 else np.array([first_query])
        texts, spans = [], []
        for paper in papers:
            start = len(texts)
            texts.extend(self._chunks(f"{paper.title}\n{paper.abstract or ''}"))
            spans.append((start, len(texts)))
        if not texts:
            return []
        vectors = self.vectors(texts)
        q = query_vectors.mean(axis=0)
        q /= max(float(np.linalg.norm(q)), 1e-12)
        scores = []
        for start, end in spans:
            vector = vectors[start:end].mean(axis=0)
            vector /= max(float(np.linalg.norm(vector)), 1e-12)
            scores.append(float(np.clip(vector @ q, 0, 1)))
        return scores


EMBEDDER = LocalEmbedding()
