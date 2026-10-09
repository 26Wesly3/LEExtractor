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


def models_root() -> Path:
    return Path(os.environ.get("LEEXTRACTOR_MODEL_DIR", Path.home() / ".cache" / "leextractor"))


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
                raise LocalModelError("本地翻译模型损坏，请重新下载")
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

    def _load(self):
        if self.model is None:
            from fastembed import TextEmbedding
            from huggingface_hub import snapshot_download

            # FastEmbed's default cache is also the existing TraceRAG cache.
            cache = os.environ.get("LEEXTRACTOR_EMBEDDING_CACHE", str(Path(tempfile.gettempdir()) / "fastembed_cache"))
            options = {"repo_id": EMBEDDING_REPOSITORY, "revision": EMBEDDING_REVISION, "cache_dir": cache,
                       "allow_patterns": ["*.json", "model_optimized.onnx"]}
            try:
                path = snapshot_download(**options, local_files_only=True)
                if not (Path(path) / "model_optimized.onnx").is_file():
                    raise FileNotFoundError("Model weights missing")
            except Exception:
                path = snapshot_download(**options)
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
            except Exception as exc:
                raise LocalModelError("本地 Embedding 模型加载失败；首次使用需下载约 252 MB，已有 TraceRAG 缓存会复用。可切换关键词排序继续。") from exc

    def vectors(self, texts: list[str]) -> np.ndarray:
        with _LOCK:
            try:
                self._load()
                root = models_root()
                root.mkdir(parents=True, exist_ok=True)
                with sqlite3.connect(root / "embeddings.db") as db:
                    db.execute("CREATE TABLE IF NOT EXISTS vectors (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                    keys = [hashlib.sha256((EMBEDDING_MODEL + EMBEDDING_REVISION + "mean_pool_v0.8\n" + t).encode()).hexdigest() for t in texts]
                    saved = [db.execute("SELECT value FROM vectors WHERE key=?", (k,)).fetchone() for k in keys]
                    missing = list(dict.fromkeys(i for i, value in enumerate(saved) if value is None))
                    if missing:
                        produced = list(self.model.embed([texts[i] for i in missing], batch_size=16))
                        for i, vector in zip(missing, produced, strict=True):
                            value = json.dumps(vector.tolist())
                            db.execute("INSERT OR REPLACE INTO vectors VALUES (?,?)", (keys[i], value))
                            saved[i] = (value,)
                    return np.array([json.loads(row[0]) for row in saved], dtype=np.float32)
            except Exception as exc:
                raise LocalModelError("本地 Embedding 未完成；请检查模型缓存或网络，也可切换到关键词排序。") from exc

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
