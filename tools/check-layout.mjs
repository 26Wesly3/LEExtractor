/* ============================================================================
   布局不变量检查：守 docs/layout-spec.md §6 的五条规则。

   为什么需要它：CSS 自定义属性未定义时，`grid-template-columns: var(--x)`
   会在**计算期**整条失效，栅格静默退化成单列 —— 页面照常打开、控制台无报错、
   所有 DOM 断言照过，只是两栏变一栏、页面底部对不齐。改名 token 漏改引用时
   必然踩这个坑，所以把它做成断言。

   运行： node tools/check-layout.mjs
   ========================================================================== */

import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const proto = resolve(here, '..', 'prototype');

let failures = 0;
let checks = 0;
function ok(label, pass, detail) {
  checks += 1;
  if (pass) console.log(`  ok   ${label}${detail ? ' — ' + detail : ''}`);
  else { failures += 1; console.log(`  FAIL ${label}${detail ? ' — ' + detail : ''}`); }
}

const cssFiles = readdirSync(join(proto, 'assets')).filter((f) => f.endsWith('.css'));
/* 先剥掉注释：注释里既可能提到变量名，也可能夹在 `;` 与下一个声明之间，
   让"声明位置"的判断失效（第一版就因此把几乎所有定义都漏掉了）。 */
const cssText = cssFiles.map((f) => readFileSync(join(proto, 'assets', f), 'utf8'))
  .join('\n').replace(/\/\*[\s\S]*?\*\//g, '');
const pages = readdirSync(proto).filter((f) => f.endsWith('.html') && !f.startsWith('_')).sort();
const pageText = pages.map((f) => ({ f, text: readFileSync(join(proto, f), 'utf8') }));

/* ---------------------------------------------------------------- A. 变量完整性 */
console.log('\nA. CSS 自定义属性完整性（改名漏改引用会让栅格静默失效）');

const defined = new Set();
/* 只认"声明位置"的定义：紧跟在 { 或 ; 之后的 --x:。
   否则 `.btn--primary:hover`、`.trace--seed::before` 会被当成变量定义。 */
for (const m of cssText.matchAll(/[{;]\s*(--[a-z0-9-]+)\s*:/gi)) defined.add(m[1]);

/* 收集所有 var(--x) 与 var(--x, fallback)：有回退值的即使未定义也不算错 */
const used = new Map();   // name -> [来源]
function scanVars(text, where) {
  for (const m of text.matchAll(/var\(\s*(--[a-z0-9-]+)\s*(,)?/gi)) {
    const name = m[1];
    const hasFallback = !!m[2];
    if (!used.has(name)) used.set(name, { hasFallback: false, where: new Set() });
    const rec = used.get(name);
    if (hasFallback) rec.hasFallback = true;
    rec.where.add(where);
  }
}
scanVars(cssText, cssFiles.join(','));
pageText.forEach(({ f, text }) => scanVars(text.replace(/<!--[\s\S]*?-->/g, ''), f));

const undefinedVars = [...used.entries()]
  .filter(([name, rec]) => !defined.has(name) && !rec.hasFallback)   // 有回退值的不算错
  .map(([name, rec]) => `${name}（用于 ${[...rec.where].join(', ')}）`);
ok('所有 var(--x) 都在某个 CSS 里定义', undefinedVars.length === 0,
  undefinedVars.join('; ') || `${used.size} 个变量，${defined.size} 个定义`);

/* 反向：定义了却没人用（提示，不算失败） */
const unused = [...defined].filter((n) => !used.has(n) && !/^--(z|sh|ease|dur|fw|lh|fs|r|s)-/.test(n));
if (unused.length) console.log(`  note 定义但未被引用：${unused.join(', ')}`);

/* 栅格列必须引用已定义的宽度 token，且名字与 tokens.css 一致 */
const gridDecls = [...cssText.matchAll(/grid-template-columns:([^;]+);/g)].map((m) => m[1].trim());
const badGrid = gridDecls.filter((d) => {
  const vars = [...d.matchAll(/var\(\s*(--[a-z0-9-]+)\s*\)/gi)].map((x) => x[1]);
  return vars.some((v) => !defined.has(v));
});
ok('grid-template-columns 里没有未定义的变量', badGrid.length === 0,
  badGrid.join(' | ') || `${gridDecls.length} 条声明`);
ok('侧栏宽度 token 名与 tokens.css 一致（--rail-w / --rail-w-wide）',
  /--rail-w\s*:/.test(cssText) && /--rail-w-wide\s*:/.test(cssText)
  && !/var\(\s*--facet-w/.test(cssText));

/* ---------------------------------------------------------------- B. 断点阈值 */
console.log('\nB. 媒体查询阈值（规范只允许 1279 / 959 / 599）');
const ALLOWED = new Set(['1279px', '959px', '599px']);
const allCss = cssFiles.map((f) => ({ f, text: readFileSync(join(proto, 'assets', f), 'utf8') }));
const badBreaks = [];
allCss.forEach(({ f, text }) => {
  for (const m of text.matchAll(/@media\s*\(([^)]*max-width[^)]*)\)/g)) {
    const val = (m[1].match(/(\d+)px/) || [])[1] + 'px';
    if (!ALLOWED.has(val)) badBreaks.push(`${f}: max-width ${val}`);
  }
});
ok('宽度断点只用 1279 / 959 / 599', badBreaks.length === 0, badBreaks.join('; ') || '符合规范');

/* ---------------------------------------------------------------- C. 列顺序 */
console.log('\nC. 两栏列顺序（DOM 顺序必须与规范 §4 一致）');
pageText.forEach(({ f, text }) => {
  const idx = text.indexOf('class="shell');
  if (idx < 0) return;
  const open = text.indexOf('>', idx) + 1;
  const after = text.slice(open);
  const isMainFirst = /class="shell[^"]*shell--main-first/.test(text.slice(idx, open));
  const firstTag = (after.match(/<(section|article|aside|div)\b[^>]*>/) || [])[1];
  const firstIsAside = firstTag === 'aside';
  const firstIsContent = firstTag === 'section' || firstTag === 'article';
  ok(`${f} 列顺序正确`,
    isMainFirst ? firstIsContent : firstIsAside,
    isMainFirst ? `main-first，首元素 <${firstTag}>` : `分面栏在左，首元素 <${firstTag}>`);
});

/* ---------------------------------------------------------------- D. 容器一致性 */
console.log('\nD. 容器类一致性（工作台页统一用 container--wide）');
const WRONG = [];
pageText.forEach(({ f, text }) => {
  if (f === 'index.html') return;                       // 首页不用 container
  const m = text.match(/class="container[^"]*"/);
  if (!m) { WRONG.push(`${f}: 没有 container`); return; }
  if (!/container--wide/.test(m[0])) WRONG.push(`${f}: ${m[0]}`);
});
ok('每页都用 container container--wide', WRONG.length === 0, WRONG.join('; ') || `${pages.length - 1} 页`);

/* ---------------------------------------------------------------- E. 可访问名称 */
console.log('\nE. 可访问性小项');
const noLabel = [];
pageText.forEach(({ f, text }) => {
  for (const m of text.matchAll(/<aside\b[^>]*>/g)) {
    if (!/aria-label|aria-labelledby/.test(m[0])) noLabel.push(`${f}: ${m[0].slice(0, 60)}`);
  }
});
ok('每个 <aside> 都有可访问名称', noLabel.length === 0, noLabel.join('; ') || '全部具备');

/* aria-labelledby 指向的 id 必须真的存在（允许在页面脚本的模板里生成） */
const badRef = [];
pageText.forEach(({ f, text }) => {
  for (const m of text.matchAll(/aria-labelledby="([^"]+)"/g)) {
    m[1].split(/\s+/).forEach((id) => {
      if (!text.includes(`id="${id}"`)) badRef.push(`${f}: ${id}`);
    });
  }
});
ok('aria-labelledby 指向的 id 都存在', badRef.length === 0, badRef.join('; ') || '全部可解析');

const dupIds = [];
pageText.forEach(({ f, text }) => {
  const ids = [...text.matchAll(/\bid="([^"]+)"/g)].map((m) => m[1]);
  const dup = ids.filter((x, i) => ids.indexOf(x) !== i);
  if (dup.length) dupIds.push(`${f}: ${[...new Set(dup)].join(', ')}`);
});
ok('页面内没有重复 id', dupIds.length === 0, dupIds.join('; ') || '无重复');

console.log(`\n${failures ? 'FAILED' : 'PASSED'} — ${checks - failures}/${checks} 项通过`);
process.exit(failures ? 1 : 0);
