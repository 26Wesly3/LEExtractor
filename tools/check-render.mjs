/* ============================================================================
   渲染冒烟测试：在 Node 里用最小 DOM 桩加载 data.js + app.js，然后对**全部 30 篇**
   调用 LE.drawerHTML()。抽屉是最复杂的渲染路径（七组内容、关系边、引文、日期），
   任何一篇触发异常都会在浏览器里表现成"点了没反应"—— 而浏览器不给提示。

   同时输出每篇生成结果的字符数与存在性检查，防止模板里出现 undefined / NaN。

   运行： node tools/check-render.mjs
   ========================================================================== */

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, join } from 'node:path';
import vm from 'node:vm';

const here = dirname(fileURLToPath(import.meta.url));
const assets = resolve(here, '..', 'prototype', 'assets');

/* ---------------------------------------------------------------- DOM 桩 */
function makeClassList() {
  const set = new Set();
  return {
    add: (...c) => c.forEach((x) => set.add(x)),
    remove: (...c) => c.forEach((x) => set.delete(x)),
    toggle: (c) => (set.has(c) ? (set.delete(c), false) : (set.add(c), true)),
    contains: (c) => set.has(c),
  };
}

function makeElement(tag) {
  const node = {
    tagName: String(tag).toUpperCase(),
    children: [],
    style: { setProperty() {}, removeProperty() {} },
    dataset: {},
    classList: makeClassList(),
    hidden: false,
    textContent: '',
    innerHTML: '',
    className: '',
    value: '',
    files: [],
    setAttribute() {}, getAttribute() { return null; }, removeAttribute() {},
    addEventListener() {}, removeEventListener() {},
    appendChild(c) { this.children.push(c); return c; },
    append(...c) { this.children.push(...c); },
    replaceChildren(...c) { this.children = c; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    closest() { return null; },
    focus() {}, blur() {}, click() {},
    dispatchEvent() { return true; },
    getBoundingClientRect() { return { top: 0, left: 0, width: 0, height: 0 }; },
    animate() { return { finished: Promise.resolve(), cancel() {} }; },
    showModal() {}, close() {},
    insertAdjacentHTML() {},
    offsetWidth: 0,
  };
  return node;
}

/* document 上的监听器要真的能收到事件 —— 语言切换就靠这条链路 */
const docListeners = new Map();
const documentStub = {
  documentElement: makeElement('html'),
  body: makeElement('body'),
  createElement: makeElement,
  // 任何选择器都给一个可用元素，这样 openDrawer 的完整 DOM 路径能被压到
  querySelector: (sel) => makeElement('div'),
  querySelectorAll: () => [],
  getElementById: () => makeElement('div'),
  addEventListener(type, fn) {
    if (!docListeners.has(type)) docListeners.set(type, []);
    docListeners.get(type).push(fn);
  },
  removeEventListener() {},
  dispatchEvent(evt) {
    (docListeners.get(evt && evt.type) || []).forEach((fn) => fn(evt));
    return true;
  },
};

const sandbox = {
  console,
  document: documentStub,
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  sessionStorage: { getItem: () => null, setItem() {} },
  matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
  requestAnimationFrame: (fn) => { fn(0); return 0; },
  cancelAnimationFrame() {},
  performance: { now: () => 0 },
  setTimeout: (fn) => { return 0; },
  clearTimeout() {},
  setInterval: () => 0,
  clearInterval() {},
  location: { hash: '', search: '', href: '' },
  history: { pushState() {}, replaceState() {} },
  navigator: { userAgent: 'node', language: 'zh-CN' },
  CSS: { escape: (s) => s },
  CustomEvent: class CustomEvent {
    constructor(type, opts) {
      this.type = type;
      this.bubbles = !!(opts && opts.bubbles);
      this.detail = opts && opts.detail;
    }
  },
  Blob: class {},
  URL: { createObjectURL: () => '', revokeObjectURL() {} },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.self = sandbox;
vm.createContext(sandbox);

for (const f of ['data.js', 'app.js', 'i18n-pages.js']) {
  try {
    vm.runInContext(readFileSync(join(assets, f), 'utf8'), sandbox, { filename: f });
  } catch (e) {
    console.log(`  FAIL 加载 ${f} 抛异常：${e.message}`);
    process.exit(1);
  }
  console.log(`  ok   加载 ${f}`);
}

const LE = sandbox.window.LE;
const D = sandbox.window.LED;

let failures = 0;
let checks = 0;
function ok(label, pass, detail) {
  checks += 1;
  if (pass) console.log(`  ok   ${label}${detail ? ' — ' + detail : ''}`);
  else { failures += 1; console.log(`  FAIL ${label}${detail ? ' — ' + detail : ''}`); }
}

console.log('\n抽屉渲染（全部 30 篇）');
let minLen = Infinity;
let maxLen = 0;
let errors = 0;
for (const p of D.papers) {
  try {
    const html = LE.drawerHTML(p);
    if (typeof html !== 'string') throw new Error('返回值不是字符串');
    if (/undefined|NaN|\[object Object\]/.test(html)) {
      const hit = html.match(/.{0,40}(undefined|NaN|\[object Object\]).{0,40}/);
      console.log(`  FAIL ${p.id} → 输出含脏值：…${hit[0]}…`);
      failures += 1;
    }
    minLen = Math.min(minLen, html.length);
    maxLen = Math.max(maxLen, html.length);
  } catch (e) {
    errors += 1;
    console.log(`  FAIL ${p.id} → ${e.message}`);
  }
  checks += 1;
  if (errors) failures += 1;
}
ok('全部 30 篇都能生成抽屉内容', errors === 0, `长度 ${minLen}–${maxLen} 字符`);

console.log('\n抽屉页脚与关系');
try {
  const foot = LE.drawerFootHTML(D.papers[0]);
  ok('抽屉页脚生成', typeof foot === 'string' && foot.length > 0, `${foot.length} 字符`);
} catch (e) { ok('抽屉页脚生成', false, e.message); }

/* 七组内容是否齐全（提示词第 9 节要求的顺序） */
const groups = ['drawer.meta', 'drawer.abstract', 'drawer.relevance', 'drawer.trace', 'drawer.relations', 'drawer.screening', 'drawer.fulltext'];
const sample = LE.drawerHTML(D.papers[0]);
const missing = groups.filter((g) => sample.indexOf(`data-i18n="${g}"`) < 0);
ok('七组内容齐全且顺序正确', missing.length === 0, missing.join(', ') || groups.join(' → '));
const order = groups.map((g) => sample.indexOf(`data-i18n="${g}"`));
ok('七组内容按顺序出现', order.every((v, i) => i === 0 || v > order[i - 1]));

console.log('\n缺失字段的诚实显示');
const bare = { ...D.papers[0] };
Object.assign(bare, {
  abstract: null, authors: [], year: null, venue: null, doi: null,
  citation_count: 0, reference_count: 0, reference_ids: [], topics: [],
  identifiers: {}, discovery_traces: [], score_breakdown: {},
});
try {
  const html = LE.drawerHTML(bare);
  ok('缺字段时不抛异常', true);
  ok('缺摘要时写明未提供', html.indexOf('drawer.noAbstract') >= 0 || /未提供/.test(html));
  ok('缺作者时写明未提供', /未提供|not supplied|Not supplied/.test(html));
  ok('零被引标为不确定而非断言零', /不等于零被引|not the same as zero/.test(html));
  ok('缺标识时写明未提供', /未提供|Not supplied/.test(html));
} catch (e) { ok('缺字段时不抛异常', false, e.message); }

console.log('\n关系与引文渲染');
try {
  const withRel = D.papers.find((p) => (p.reference_ids || []).some((r) => D.byId.has(r)));
  const html = LE.drawerHTML(withRel);
  ok('有引文的论文能渲染引文分组', /drawer.relations/.test(html));
  const rows = LE.relationRows(withRel);
  ok('relationRows 返回字符串', typeof rows === 'string', `${rows.length} 字符`);
  ok('关系图例不含历史别名 semantic', rows.indexOf('semantic') < 0);
} catch (e) { ok('关系渲染', false, e.message); }

console.log('\n跨评分上下文提示');
const snowPaper = D.papers.find((p) => p.score_context_id === D.ctx.snowball);
const snowHtml = LE.drawerHTML(snowPaper);
ok('存在两个批次时给出不可比提示', /drawer.ctxWarn|不可直接比较|not comparable/.test(snowHtml));

console.log('\nopenDrawer 完整路径（全部 30 篇 + 未知 id）');
let openErrors = 0;
D.papers.forEach((p) => {
  try {
    LE.openDrawer(p.id, { push: true });
    LE.closeDrawer();
  } catch (e) {
    openErrors += 1;
    console.log(`  FAIL ${p.id} → ${e.message}`);
  }
});
ok('每篇都能走完打开与关闭', openErrors === 0, `${D.papers.length} 篇`);

try {
  LE.openDrawer('不存在的-id');
  ok('未知 id 不抛异常（只提示）', true);
} catch (e) { ok('未知 id 不抛异常（只提示）', false, e.message); }

console.log('\n外观相关导出是否齐全');
['t', 'pt', 'rich', 'esc', 'num', 'pct', 'badgeScreening', 'badgeRetrieval', 'badgeStop',
 'badgeSource', 'badgeDemo', 'badgeJob', 'facetLabel', 'toast', 'mount', 'openDrawer',
 'closeDrawer', 'fx', 'motion', 'state', 'relationRows', 'citationRows', 'drawerHTML',
 'drawerFootHTML', 'orMissing', 'pad2', 'el', 'MODULES'].forEach((k) => {
  ok(`LE.${k} 已导出`, LE[k] !== undefined, typeof LE[k]);
});
ok('LE.fx.countUp 存在', !!(LE.fx && typeof LE.fx.countUp === 'function'));
ok('LE.fx.stagger 存在', !!(LE.fx && typeof LE.fx.stagger === 'function'));
ok('LE.motion.reduced 存在', !!(LE.motion && typeof LE.motion.reduced === 'function'));

console.log('\n语言切换事件链路');
let langEvents = 0;
documentStub.addEventListener('le:lang', () => { langEvents += 1; });
LE.setLang('en');
ok('setLang 在 document 上派发 le:lang（页面监听 document 才能生效）', langEvents >= 1, `${langEvents} 次`);
ok('语言确实切到 en', LE.lang() === 'en');
LE.setLang('zh');
ok('能切回 zh', LE.lang() === 'zh');

console.log('\n双语都不泄漏 i18n 原始 key');
/* 去掉 data-i18n 属性本身，剩下的文本里不应再出现 xxx.yyy 形态的 key */
const strip = (html) => html.replace(/data-i18n(?:-[a-z]+)?="[^"]*"/g, '');
const KEY_LEAK = /\b(?:sch|sstatus|ret|job|dis|stop|drawer|common|act|facet|nav|mod|foot|view|sort|results|demo)\.(?:[a-z_]{2,})/;
['zh', 'en'].forEach((code) => {
  LE.setLang(code);
  const leaks = D.papers.map((p) => strip(LE.drawerHTML(p))).filter((h) => KEY_LEAK.test(h));
  ok(`${code}: 30 篇抽屉均无 key 泄漏`, leaks.length === 0,
    leaks.length ? String(leaks[0]).slice(0, 80) : '');
});
LE.setLang('zh');

console.log('\n筛选徽标：两种词汇表都不能漏 key');
[['session 状态', 'not_started'], ['session 状态', 'preliminary_included'],
 ['session 状态', 'final_included'], ['session 状态', 'excluded'],
 ['逐条决定', 'include'], ['逐条决定', 'exclude'], ['逐条决定', 'maybe'],
 ['逐条决定', 'undecided'], ['空值', undefined]].forEach(([kind, value]) => {
  const html = LE.badgeScreening(value);
  const text = html.replace(/<[^>]*>/g, '');
  ok(`${kind} ${String(value)} 渲染为文案而非 key`, !/[a-z]+\.[a-z_]{2,}/.test(text) && text.length > 0, text);
});

console.log(`\n${failures ? 'FAILED' : 'PASSED'} — ${checks - failures}/${checks} 项通过`);
process.exit(failures ? 1 : 0);
