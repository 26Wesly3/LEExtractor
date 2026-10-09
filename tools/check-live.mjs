/* ============================================================================
   真机渲染验收：用无头 Edge 逐页加载，检查**渲染后的 DOM**。

   静态语法检查查不出"脚本中途抛异常导致半页空白"这类问题 —— 无头浏览器能。
   这一步不碰用户屏幕，也不依赖 Node 侧的 DOM 桩。

   前置：原型目录已由本地 HTTP 服务提供（默认 http://127.0.0.1:8765）。
   运行： node tools/check-live.mjs [baseUrl]
   ========================================================================== */

import { execFileSync } from 'node:child_process';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const proto = resolve(here, '..', 'prototype');
const base = (process.argv[2] || 'http://127.0.0.1:8765').replace(/\/$/, '');

const EDGE = [
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
].find((p) => { try { readFileSync(p); return true; } catch { return false; } });

if (!EDGE) { console.log('FAIL 找不到 msedge.exe，无法做真机渲染验收'); process.exit(1); }

const pages = readdirSync(proto).filter((f) => f.endsWith('.html') && !f.startsWith('_')).sort();

/* 每页必须真的出现的东西：脚本若中途抛异常，这些区块会是空的 */
const EXPECT = {
  'index.html': ['LEExtractor', '开始检索', '这套工作台能做什么', '最近项目'],
  'search.html': ['快速检索', '高级条件', '检索计划', 'semantic_scholar'],
  'results.html': ['分面统计', '数据源返回', 'Benchmarking spatial transcriptomics', '相关度'],
  'paper.html': ['基本信息', '发现路径', '筛选决定', '全文获取'],
  'expand.html': ['种子文献', '每轮产出', '停止原因', '边际收益'],
  'landscape.html': ['引文网络', '新颖度', '覆盖平衡', '中心度'],
  // 文案精简后：页头只留一句「样本里没看到不等于没人做过」，判定门槛收进折叠区，
  // 原始字段名只出现在「计算口径与字段名」里（所以 hypothesis 仍应存在）。
  'questions.html': ['研究机会', 'hypothesis', '不等于没人做过', '计算口径与字段名'],
  'review.html': ['标题摘要筛选', 'PRISMA', '计数账本', '标定'],
  'export.html': ['导出范围', 'CSV', 'Evidence Pack', '会话'],
  'settings.html': ['数据源', '诊断', 'HTTP', '停止原因'],
  'projects.html': ['项目', '会话', 'schema'],
};

/* 渲染后不应出现的 i18n 原始 key（形如 >xxx.yyy<） */
const KEY_LEAK = />\s*(?:sch|sstatus|ret|job|dis|stop|drawer|common|act|facet|nav|mod|foot|view|sort|results|demo|home|search|paper|expand|landscape|questions|review|export|settings|projects)\.[a-z_]{2,}\s*</;

let failures = 0;
let checks = 0;
function ok(label, pass, detail) {
  checks += 1;
  if (pass) console.log(`  ok   ${label}${detail ? ' — ' + detail : ''}`);
  else { failures += 1; console.log(`  FAIL ${label}${detail ? ' — ' + detail : ''}`); }
}

function dump(url) {
  return execFileSync(EDGE, [
    '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
    '--disable-extensions', '--virtual-time-budget=4000',
    '--user-data-dir=' + join(process.env.TEMP || '.', 'le-headless-' + Date.now()),
    '--dump-dom', url,
  ], { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024, stdio: ['ignore', 'pipe', 'pipe'] });
}

console.log(`\n真机渲染验收 · ${base}\n`);

for (const page of pages) {
  console.log(page);
  let dom = '';
  try {
    dom = dump(`${base}/${page}`);
  } catch (e) {
    ok('页面可加载', false, String(e.message).slice(0, 160));
    console.log('');
    continue;
  }
  const text = dom.replace(/<script[\s\S]*?<\/script>/gi, '').replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');

  ok('页面渲染出内容', text.length > 500, `${text.length} 字符`);
  ok('无 i18n 原始 key 泄漏', !KEY_LEAK.test(dom),
    (dom.match(KEY_LEAK) || [''])[0].replace(/\s+/g, ' ').slice(0, 60));

  const missing = (EXPECT[page] || []).filter((needle) => !text.includes(needle) && !dom.includes(needle));
  ok('关键区块都在', missing.length === 0, missing.length ? '缺：' + missing.join(', ') : (EXPECT[page] || []).length + ' 项');

  ok('共享外壳已注入（顶栏/导航/页脚）',
    /id="le-drawer"/.test(dom) && /id="le-nav"/.test(dom) && /site-foot/.test(dom));
  ok('页面入场动效已加', /page-enter/.test(dom) || /theme-home/.test(dom));

  console.log('');
}

console.log('交叉验证');
const resultsDom = dump(`${base}/results.html`);
const ids = [...resultsDom.matchAll(/data-open="([^"]+)"/g)].map((m) => m[1]);
ok('结果页渲染出了可点开的文献标题', ids.length > 5, `${ids.length} 个`);
const facetRows = [...resultsDom.matchAll(/data-facet="([^"]+)"/g)].length;
ok('分面行渲染出来', facetRows > 10, `${facetRows} 行`);

const homeDom = dump(`${base}/index.html`);
ok('首页字标与求是鹰都在', /class="wordmark"/.test(homeDom) && /eagleCanvas/.test(homeDom));
ok('首页检索计划渲染出四个来源', (homeDom.match(/class="plan__q"/g) || []).length >= 4,
  `${(homeDom.match(/class="plan__q"/g) || []).length} 条检索式`);

console.log(`\n${failures ? 'FAILED' : 'PASSED'} — ${checks - failures}/${checks} 项通过`);
process.exit(failures ? 1 : 0);
