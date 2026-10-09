/* ============================================================================
   页面自检：把每个 HTML 里的内联脚本抽出来做语法检查，并核对它们引用的
   DOM id 与本地资源是否真的存在。

   这一类错误（`$('#count')` 但 HTML 里已经没有 #count、脚本指向不存在的
   assets/x.js）在浏览器里是静默失败，肉眼审不出来 —— 所以做成工具。

   运行： node tools/check-pages.mjs
   ========================================================================== */

import { readFileSync, readdirSync, writeFileSync, mkdtempSync, rmSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, join } from 'node:path';
import { tmpdir } from 'node:os';
import { execFileSync } from 'node:child_process';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '..');
const proto = join(root, 'prototype');

let failures = 0;
let checks = 0;
function ok(label, pass, detail) {
  checks += 1;
  if (pass) console.log(`  ok   ${label}${detail ? ' — ' + detail : ''}`);
  else { failures += 1; console.log(`  FAIL ${label}${detail ? ' — ' + detail : ''}`); }
}

const htmlFiles = readdirSync(proto).filter((f) => f.endsWith('.html') && !f.startsWith('_')).sort();
const allAssetFiles = new Set();
(function walk(dir, prefix = '') {
  readdirSync(dir).forEach((name) => {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) walk(full, prefix + name + '/');
    else allAssetFiles.add(prefix + name);
  });
})(join(proto, 'assets'), 'assets/');

const scratch = mkdtempSync(join(tmpdir(), 'le-pagecheck-'));

/* 共享外壳注入的元素与动态生成的元素：脚本可以合法引用它们 */
const RUNTIME_IDS = new Set([
  'le-clock', 'le-history', 'le-menu', 'le-nav', 'le-nav-title', 'le-history-dialog',
  'le-history-body', 'le-drawer', 'le-drawer-type', 'le-drawer-title', 'le-drawer-body',
  'le-drawer-foot', 'le-drawer-close', 'le-drawer-expand', 'le-overlay', 'toast',
  'shell', 'foot', 'main', 'skipLink',
]);

console.log(`\n共 ${htmlFiles.length} 个页面\n`);

for (const file of htmlFiles) {
  console.log(file);
  const html = readFileSync(join(proto, file), 'utf8');

  /* ---- 1. 内联脚本语法 ---- */
  const inline = [];
  const re = /<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi;
  let m;
  while ((m = re.exec(html)) !== null) inline.push(m[1]);
  const joined = inline.join('\n;\n');
  const jsPath = join(scratch, file.replace(/\.html$/, '.js'));
  writeFileSync(jsPath, joined, 'utf8');
  try {
    execFileSync(process.execPath, ['--check', jsPath], { stdio: 'pipe' });
    ok('内联脚本语法', true, `${inline.length} 段`);
  } catch (e) {
    ok('内联脚本语法', false, String(e.stderr || e.message).split('\n').slice(0, 4).join(' '));
  }

  /* ---- 2. 脚本引用的 #id 必须存在于 HTML 或运行期注入 ---- */
  const declared = new Set();
  const idRe = /\bid=["']([^"']+)["']/g;
  while ((m = idRe.exec(html)) !== null) declared.add(m[1]);
  RUNTIME_IDS.forEach((id) => declared.add(id));

  const referenced = new Set();
  const qRe = /(?:\$\(|getElementById\()\s*['"]#?([A-Za-z][\w-]*)['"]/g;
  while ((m = qRe.exec(joined)) !== null) referenced.add(m[1]);
  const dynamic = /(?:innerHTML|insertAdjacentHTML|\.html\()/.test(joined);

  const missing = [...referenced].filter((id) => !declared.has(id));
  if (missing.length && dynamic) {
    // 页面会动态注入标记，无法静态判定；降级为提示
    console.log(`  note 无法静态判定的 id 引用（页面有动态注入）：${missing.join(', ')}`);
  } else {
    ok('脚本引用的 id 都有定义', missing.length === 0, missing.join(', ') || `${referenced.size} 个引用`);
  }

  /* ---- 3. 本地资源引用必须存在 ---- */
  const refs = new Set();
  const srcRe = /(?:src|href)=["'](?!https?:|data:|mailto:|#)([^"']+)["']/g;
  while ((m = srcRe.exec(html)) !== null) {
    const r = m[1].split('?')[0];
    if (r.includes('${')) continue;      // 模板字符串里拼接的地址，静态无法判定
    refs.add(r);
  }
  const badRefs = [...refs].filter((r) => !r.endsWith('.html') && !allAssetFiles.has(r));
  const badPages = [...refs].filter((r) => r.endsWith('.html') && !htmlFiles.includes(r.replace(/^\.\//, '')));
  ok('本地资源引用存在', badRefs.length === 0 && badPages.length === 0,
    [...badRefs, ...badPages].join(', ') || `${refs.size} 个引用`);

  /* ---- 4. 页面必须挂载共享外壳 ---- */
  ok('调用了 LE.mount()', /LE\.mount\s*\(/.test(joined));
  ok('处理了语言切换', /le:lang/.test(joined), '');
  ok('声明了 data-page', /data-page=/.test(html), (html.match(/data-page=["']([^"']+)["']/) || [])[1] || '');
  console.log('');
}

/* ---- 5. 跨页一致性 ---- */
console.log('跨页一致性');
const pages = htmlFiles.map((f) => ({ f, html: readFileSync(join(proto, f), 'utf8') }));
const badHead = pages.filter((p) => !/assets\/tokens\.css/.test(p.html) || !/assets\/app\.css/.test(p.html));
ok('每页都引入 tokens.css 与 app.css', badHead.length === 0, badHead.map((p) => p.f).join(', '));

const badOrder = pages.filter((p) => {
  const i = p.html.indexOf('assets/data.js');
  const j = p.html.indexOf('assets/app.js');
  const k = p.html.indexOf('assets/i18n-pages.js');
  if (i < 0 && j < 0) return false;
  return !(i >= 0 && j > i && (k < 0 || k > j));
});
ok('脚本顺序为 data → app → i18n-pages', badOrder.length === 0, badOrder.map((p) => p.f).join(', '));

const noReduced = pages.filter((p) => !/le:lang/.test(p.html));
ok('每页都能整段重渲染以响应语言切换', noReduced.length === 0, noReduced.map((p) => p.f).join(', '));

const i18nPages = new Set();
readdirSync(join(proto, 'assets')).filter((f) => f.startsWith('i18n-')).forEach((f) => {
  const src = readFileSync(join(proto, 'assets', f), 'utf8');
  const mm = src.match(/Object\.assign\(window\.LE_PAGES,\s*\{\s*([a-zA-Z]+):/) || src.match(/^\s*([a-zA-Z]+):\s*\{\s*$/m);
  const names = [...src.matchAll(/^\s{2}([a-z]+):\s*\{\s*$/gm)].map((x) => x[1]);
  names.forEach((n) => i18nPages.add(n));
  if (mm) i18nPages.add(mm[1]);
});
console.log(`  note i18n 字典覆盖的页面：${[...i18nPages].sort().join(', ')}`);

rmSync(scratch, { recursive: true, force: true });
console.log(`\n${failures ? 'FAILED' : 'PASSED'} — ${checks - failures}/${checks} 项通过`);
process.exit(failures ? 1 : 0);
