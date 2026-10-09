/* ============================================================================
   截图验收：用无头 Edge 给每个页面拍整页图，供人（或代理）肉眼复核版式。

   为什么需要它：语法检查与 DOM 检查都查不出「两栏高低不齐」「文字压在说明上」
   「颜色太多」这类问题。只有看图能发现。这一步不占用用户屏幕。

   用法： node tools/shots.mjs [baseUrl] [width] [height]
   输出： <repo>/shots/<page>-<w>x<h>.png
   ========================================================================== */

import { execFileSync } from 'node:child_process';
import { readFileSync, readdirSync, mkdirSync, existsSync, rmSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '..');
const proto = join(root, 'prototype');
const outDir = join(root, 'shots');

const base = (process.argv[2] || 'http://127.0.0.1:8765').replace(/\/$/, '');
const W = process.argv[3] || '1440';
const H = process.argv[4] || '2200';

const EDGE = [
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
].find((p) => { try { readFileSync(p); return true; } catch { return false; } });
if (!EDGE) { console.log('FAIL 找不到 msedge.exe'); process.exit(1); }

if (existsSync(outDir)) rmSync(outDir, { recursive: true, force: true });
mkdirSync(outDir, { recursive: true });

const pages = readdirSync(proto).filter((f) => f.endsWith('.html')).sort();
const profile = join(process.env.TEMP || '.', 'le-shots-' + Date.now());

/* 注意：`--headless=new` 在这台机器的 Edge 上**不会写出** --screenshot 文件
   （不报错、静默失败）。经典的 `--headless=old` 可以。这里两种都试，以文件是否
   真的落盘为准，不靠退出码。 */
function shoot(page, out) {
  const attempts = ['old', 'new'];
  for (const mode of attempts) {
    rmSync(out, { force: true });
    try {
      execFileSync(EDGE, [
        `--headless=${mode}`, '--disable-gpu', '--no-first-run', '--no-default-browser-check',
        '--disable-extensions', '--hide-scrollbars', '--force-device-scale-factor=1',
        `--window-size=${W},${H}`, '--virtual-time-budget=4500',
        `--user-data-dir=${profile}-${mode}`, `--screenshot=${out}`, `${base}/${page}`,
      ], { stdio: 'ignore', timeout: 90000 });
    } catch (e) { /* 继续试下一种 */ }
    if (existsSync(out)) return mode;
  }
  return null;
}

for (const page of pages) {
  const out = join(outDir, `${page.replace(/\.html$/, '')}-${W}x${H}.png`);
  const mode = shoot(page, out);
  if (mode) {
    const kb = Math.round(readFileSync(out).length / 1024);
    console.log(`  ${page.padEnd(18)} -> shots/${out.split(/[\\/]/).pop()}  (${kb} KB, headless=${mode})`);
  } else {
    console.log(`  ${page.padEnd(18)} -> 失败：两种 headless 模式都没写出文件`);
  }
}

console.log(`\n共 ${pages.length} 张，目录：${outDir}`);
