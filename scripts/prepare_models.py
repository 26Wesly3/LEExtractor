"""Prepare the two pinned CPU models before opening the Web workspace."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def prepare() -> bool:
    bundled = ROOT / 'models'
    if (bundled / 'opus-mt-zh-en/config.json').is_file():
        os.environ.setdefault('LEEXTRACTOR_MODEL_DIR', str(bundled))
    if (bundled / 'embedding').is_dir():
        os.environ.setdefault('LEEXTRACTOR_EMBEDDING_CACHE', str(bundled / 'embedding'))
    from litsearch.local_models import LocalEmbedding, LocalTranslator

    ready = True
    for name, loader in [('中译英 Marian（约 116 MB）', LocalTranslator()._load),
                         ('多语言 MiniLM Embedding（约 267 MB）', LocalEmbedding().ensure_ready)]:
        print('检查本地模型：' + name + '；缺失时自动下载，请保持窗口打开。', flush=True)
        try:
            loader()
        except Exception as exc:
            ready = False
            print(f'模型准备未完成（{type(exc).__name__}）：{name}。检查网络后重新启动，或先手动输入英文并使用关键词排序。', flush=True)
        else:
            print('模型已就绪：' + name, flush=True)
    return ready


def main() -> int:
    return 0 if prepare() else 1


if __name__ == '__main__':
    raise SystemExit(main())
