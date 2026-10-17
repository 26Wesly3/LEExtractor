"""修正 core.js 的挂载对象：Zotero 用 loadSubScript(url, scope) 加载，scope 才是全局。"""
from pathlib import Path

p = Path("lib/core.js")
src = p.read_text(encoding="utf-8")
old_tail = '})(typeof globalThis !== "undefined" ? globalThis : this);'
new_tail = (
    '  // Zotero 用 loadSubScript(url, scope) 加载本文件：scope 才是脚本的全局对象。\n'
    '  // 只挂到 globalThis 上时 scope.LEExtractorCore 永远是 undefined，startup 会在\n'
    '  // core.DEFAULT_BASE 处抛错，插件装了也什么都不做。\n'
    '  if (root !== globalThis) {\n'
    '    try {\n'
    '      root.LEExtractorCore = api;\n'
    '    } catch (error) {\n'
    '      /* 顶层 this 不可写时忽略 */\n'
    '    }\n'
    '  }\n'
    '})(typeof this !== "undefined" && this ? this : globalThis);'
)
if old_tail not in src:
    raise SystemExit("tail not found")
src = src.replace(old_tail, new_tail)
p.write_text(src, encoding="utf-8")
print("core.js patched")
print(src[-260:])
