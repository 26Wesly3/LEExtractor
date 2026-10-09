/* ============================================================================
   LEExtractor — 共享外壳与渲染器
   ----------------------------------------------------------------------------
   每个页面只需要一个 <div id="shell"> 与一次 LE.mount()，外壳（顶栏、全站导航
   面板、页脚、详情抽屉、Toast）由本文件统一注入，保证 11 个页面完全一致。

   同时提供页面复用的渲染器：分面栏、结果列表/表格、分页、徽标、格式化。
   业务语义（相关度不是纳入概率、未获取全文不是排除、跨评分上下文不可比、
   计数单位是 record/report）在渲染层显式标注，见 badge* 与 note* 函数。
   ========================================================================== */

'use strict';

window.LE = (function () {

  const D = window.LED;

  /* ====================================================================== */
  /* 1. 小工具                                                              */
  /* ====================================================================== */

  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.prototype.slice.call((root || document).querySelectorAll(sel));
  const esc = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  const num = (n) => (n == null || n === '' ? '—' : Number(n).toLocaleString('en-US'));
  const pct = (x) => `${Math.round((x || 0) * 100)}%`;
  const pad2 = (n) => String(n).padStart(2, '0');

  function el(tag, attrs, html) {
    const node = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach((k) => {
      if (k === 'class') node.className = attrs[k];
      else if (k === 'text') node.textContent = attrs[k];
      else node.setAttribute(k, attrs[k]);
    });
    if (html != null) node.innerHTML = html;
    return node;
  }

  /* ====================================================================== */
  /* 2. 双语                                                                */
  /* ====================================================================== */

  const I18N = {
    zh: {
      'brand.tag': '文献发现与证据追踪',
      'nav.history': '[ 历史 ]',
      'nav.menu': '[ 菜单 ]',
      'nav.title': 'LEExtractor 工作台',
      'nav.subtitle': '九个模块，从检索到证据包',
      'nav.close': '关闭',
      'nav.current': '当前',
      'mod.home': '研究探索', 'mod.home.d': '提出研究问题，生成分库检索式',
      'mod.search': '文献搜索', 'mod.search.d': '快速检索 / 高级条件 / 检索计划',
      'mod.results': '文献结果', 'mod.results.d': '分面统计、列表与表格、批量选择',
      'mod.paper': '论文详情', 'mod.paper.d': '元数据、发现路径、关系与筛选决定',
      'mod.expand': '引文扩展', 'mod.expand.d': '种子、轮次、预算与停止原因',
      'mod.landscape': '证据版图', 'mod.landscape.d': '引文网络、主题、时间与覆盖',
      'mod.questions': '研究机会', 'mod.questions.d': '候选研究问题与支撑文献',
      'mod.review': '筛选与 PRISMA', 'mod.review.d': '初筛与全文筛选、账本、流程图',
      'mod.export': '保存与导出', 'mod.export.d': '会话恢复与六种导出格式',
      'mod.settings': '设置与诊断', 'mod.settings.d': '数据源密钥、偏好与运行日志',
      'mod.projects': '项目列表', 'mod.projects.d': '已保存会话的恢复与冲突处理',
      'demo.flag': '界面演示：以下全部为合成的示例数据，不是真实文献，也不能作为科研证据。',
      'demo.chip': '示例数据',
      'demo.tag': '示例数据',
      'foot.motto': '惟学无际，际于天地',
      'foot.edition': 'OPENALEX-STYLE WORKBENCH',
      'common.all': '全部',
      'common.unknown': '未知',
      'common.none': '无',
      'common.notProvided': '数据源未提供',
      'common.more': '更多',
      'common.reset': '重置',
      'common.apply': '应用',
      'common.cancel': '取消',
      'common.close': '关闭',
      'common.back': '返回',
      'common.next': '下一步',
      'common.openPaper': '打开详情',
      'common.expandPage': '展开为完整详情页',
      'common.exportRow': '导出这一条',
      'common.copied': '已复制到剪贴板',
      'common.demoShort': '示例',
      'facet.title': '分面统计',
      'facet.scope': '当前项目',
      'facet.years': '发表年份',
      'facet.sources': '数据来源',
      'facet.topics': '主题',
      'facet.discovery': '发现方式',
      'facet.screening': '筛选状态',
      'facet.retrieval': '全文获取',
      'facet.ctx': '评分批次',
      'facet.clearAll': '清空全部筛选',
      'facet.note': '统计基于当前项目的完整语料 {n} 篇，不是当前页，也不代表全球文献。',
      'sort.relevance': '相关度',
      'sort.citations': '被引次数',
      'sort.year': '最新发表',
      'sort.title': '标题',
      'view.list': '列表',
      'view.table': '表格',
      'results.perPage': '每页',
      'results.selected': '已选 {n} 篇',
      'results.selectAll': '选中本页全部 {n} 篇',
      'results.clearSel': '取消选择',
      'results.addSeed': '设为种子',
      'results.toScreening': '进入人工筛选',
      'results.exportSel': '导出所选',
      'results.empty': '没有符合当前筛选条件的文献',
      'results.emptyHint': '放宽分面条件，或回到搜索页调整检索式后重新检索。',
      'sch.include': '纳入', 'sch.exclude': '排除', 'sch.maybe': '待定', 'sch.undecided': '未决定',
      'sstatus.not_started': '未开始',
      'sstatus.preliminary_included': '初筛纳入（初步）',
      'sstatus.final_included': '最终纳入',
      'sstatus.excluded': '全部排除',
      'ret.retrieved': '已获取', 'ret.paywalled': '付费墙', 'ret.timeout': '超时',
      'ret.too_large': '超限', 'ret.blocked_redirect': '地址校验中止', 'ret.not_attempted': '未尝试',
      'dis.systematic_search': '数据库检索', 'dis.snowball_forward': '正向引文扩展',
      'dis.snowball_backward': '反向引文扩展', 'dis.similar': '关联文献', 'dis.demo_fixture': '演示夹具',
      'drawer.meta': '基本信息',
      'drawer.abstract': '摘要与主题',
      'drawer.relevance': '相关度与评分',
      'drawer.trace': '发现路径',
      'drawer.relations': '引用与关联关系',
      'drawer.screening': '筛选决定',
      'drawer.fulltext': '全文获取',
      'drawer.authors': '作者',
      'drawer.venue': '来源',
      'drawer.year': '年份',
      'drawer.doi': 'DOI',
      'drawer.ids': '标识符',
      'drawer.citations': '被引次数',
      'drawer.references': '参考文献数',
      'drawer.provider': '来源库',
      'drawer.url': '原文链接',
      'drawer.noAbstract': '数据源未提供摘要。',
      'drawer.scoreNote': '相关度是**单次检索内的词法排序信号**，不是纳入概率，也不是论文质量；本批次的评分上下文为 {ctx}。',
      'drawer.ctxWarn': '本项目存在两个评分批次，跨批次分数不可直接比较。',
      'drawer.breakdown': '分项得分',
      'drawer.noTrace': '没有可用的发现记录。',
      'drawer.noRelations': '当前样本内没有可计算的关系。',
      'drawer.relationNote': '引文边与三类关系边并列，不合并加权；历史别名 semantic 不重复计数。',
      'drawer.decisionHistory': '决定历史',
      'drawer.noDecision': '尚未记录人工筛选决定。',
      'drawer.autoScreenNote': '自动筛选默认关闭（auto_screen={v}）；未标定时相关度只用于排序。',
      'drawer.retrievalNote': '获取失败不构成排除理由：未下到全文仍可能是重要文献。',
      'drawer.actions': '可执行动作',
      'act.seed': '设为种子',
      'act.forward': '正向扩展',
      'act.backward': '反向扩展',
      'act.similar': '查找关联文献',
      'act.fulltext': '获取全文',
      'act.include': '纳入',
      'act.exclude': '排除',
      'act.maybe': '待定',
      'act.exportOne': '导出单条',
      'toast.demoAction': '演示原型：该动作在阶段二的真实后端才生效（{a}）。',
      'toast.seedAdded': '已设为种子（当前 {n} 篇）',
      'toast.exported': '已导出 {name}（{n} 条，演示数据）',
      'toast.langSwitched': '界面语言已切换',
      'stop.saturated': '检索饱和：所有来源正常返回且无新增',
      'stop.no_new_results': '所有来源正常返回空结果',
      'stop.low_yield': '新增不足 5 篇（产品启发式，非覆盖证明）',
      'stop.max_rounds': '达到轮数上限',
      'stop.truncated': '达到调用方记录上限（结果可用但不完整）',
      'stop.budget_exhausted': 'API 请求预算耗尽',
      'stop.canceled': '用户取消',
      'stop.api_failure': '来源请求失败导致提前结束',
      'stop.completeNote': '只有「检索饱和」与「来源返回空」允许声称检索已完成。',
      'stop.incompleteNote': '这次结束属于不完整，可继续；不得报告为覆盖完成。',
      'job.queued': '排队中', 'job.running': '进行中', 'job.completed': '已完成',
      'job.partial': '部分完成', 'job.failed': '失败', 'job.canceled': '已取消',
      'clock.locale': 'zh-CN',
    },
    en: {
      'brand.tag': 'Literature discovery & evidence tracking',
      'nav.history': '[ History ]',
      'nav.menu': '[ Menu ]',
      'nav.title': 'LEExtractor workbench',
      'nav.subtitle': 'Nine modules, from query to evidence pack',
      'nav.close': 'Close',
      'nav.current': 'current',
      'mod.home': 'Explore', 'mod.home.d': 'Frame a question, generate per-database strings',
      'mod.search': 'Search', 'mod.search.d': 'Quick / advanced conditions / search plan',
      'mod.results': 'Results', 'mod.results.d': 'Facets, list & table views, bulk selection',
      'mod.paper': 'Paper detail', 'mod.paper.d': 'Metadata, discovery path, relations, decisions',
      'mod.expand': 'Citation expansion', 'mod.expand.d': 'Seeds, rounds, budget, stop reason',
      'mod.landscape': 'Evidence landscape', 'mod.landscape.d': 'Citation network, topics, time, coverage',
      'mod.questions': 'Research opportunities', 'mod.questions.d': 'Candidate questions with support',
      'mod.review': 'Screening & PRISMA', 'mod.review.d': 'Title/abstract and full text, ledger, flow',
      'mod.export': 'Save & export', 'mod.export.d': 'Session restore and six export formats',
      'mod.settings': 'Settings & diagnostics', 'mod.settings.d': 'Provider keys, preferences, run log',
      'mod.projects': 'Projects', 'mod.projects.d': 'Restore saved sessions, resolve conflicts',
      'demo.flag': 'Interface demo: everything below is synthetic sample data — not real literature and not research evidence.',
      'demo.chip': 'sample data',
      'demo.tag': 'sample data',
      'foot.motto': '惟学无际，际于天地',
      'foot.edition': 'OPENALEX-STYLE WORKBENCH',
      'common.all': 'All',
      'common.unknown': 'Unknown',
      'common.none': 'None',
      'common.notProvided': 'Not supplied by the provider',
      'common.more': 'More',
      'common.reset': 'Reset',
      'common.apply': 'Apply',
      'common.cancel': 'Cancel',
      'common.close': 'Close',
      'common.back': 'Back',
      'common.next': 'Next',
      'common.openPaper': 'Open detail',
      'common.expandPage': 'Open full detail page',
      'common.exportRow': 'Export this record',
      'common.copied': 'Copied to clipboard',
      'common.demoShort': 'sample',
      'facet.title': 'Facets',
      'facet.scope': 'Current project',
      'facet.years': 'Publication year',
      'facet.sources': 'Provider',
      'facet.topics': 'Topic',
      'facet.discovery': 'Discovery method',
      'facet.screening': 'Screening status',
      'facet.retrieval': 'Full text',
      'facet.ctx': 'Score batch',
      'facet.clearAll': 'Clear all filters',
      'facet.note': 'Counts cover the whole current project corpus ({n} records), not this page, and not world literature.',
      'sort.relevance': 'Relevance',
      'sort.citations': 'Citations',
      'sort.year': 'Newest',
      'sort.title': 'Title',
      'view.list': 'List',
      'view.table': 'Table',
      'results.perPage': 'Per page',
      'results.selected': '{n} selected',
      'results.selectAll': 'Select all {n} on this page',
      'results.clearSel': 'Clear selection',
      'results.addSeed': 'Set as seed',
      'results.toScreening': 'Send to screening',
      'results.exportSel': 'Export selection',
      'results.empty': 'No papers match the current filters',
      'results.emptyHint': 'Relax the facets, or go back to Search and adjust the query strings.',
      'sch.include': 'Include', 'sch.exclude': 'Exclude', 'sch.maybe': 'Maybe', 'sch.undecided': 'Undecided',
      'sstatus.not_started': 'Not started',
      'sstatus.preliminary_included': 'Preliminary include',
      'sstatus.final_included': 'Finally included',
      'sstatus.excluded': 'All excluded',
      'ret.retrieved': 'Retrieved', 'ret.paywalled': 'Paywalled', 'ret.timeout': 'Timeout',
      'ret.too_large': 'Over size limit', 'ret.blocked_redirect': 'Blocked by URL check', 'ret.not_attempted': 'Not attempted',
      'dis.systematic_search': 'Database search', 'dis.snowball_forward': 'Forward citation',
      'dis.snowball_backward': 'Backward citation', 'dis.similar': 'Related papers', 'dis.demo_fixture': 'Demo fixture',
      'drawer.meta': 'Metadata',
      'drawer.abstract': 'Abstract & topics',
      'drawer.relevance': 'Relevance & scoring',
      'drawer.trace': 'Discovery path',
      'drawer.relations': 'Citations & relations',
      'drawer.screening': 'Screening decision',
      'drawer.fulltext': 'Full-text retrieval',
      'drawer.authors': 'Authors',
      'drawer.venue': 'Venue',
      'drawer.year': 'Year',
      'drawer.doi': 'DOI',
      'drawer.ids': 'Identifiers',
      'drawer.citations': 'Cited by',
      'drawer.references': 'References',
      'drawer.provider': 'Provider',
      'drawer.url': 'Source link',
      'drawer.noAbstract': 'No abstract supplied by the provider.',
      'drawer.scoreNote': 'Relevance is a **lexical ranking signal within one search run** — not an inclusion probability and not paper quality. This batch\'s score context is {ctx}.',
      'drawer.ctxWarn': 'This project has two scoring batches; scores across batches are not comparable.',
      'drawer.breakdown': 'Score breakdown',
      'drawer.noTrace': 'No discovery record available.',
      'drawer.noRelations': 'No computable relations within the current sample.',
      'drawer.relationNote': 'Citation edges and the three relation types are parallel and never combined into one weight; the legacy alias semantic is not double-counted.',
      'drawer.decisionHistory': 'Decision history',
      'drawer.noDecision': 'No human screening decision recorded yet.',
      'drawer.autoScreenNote': 'Automatic screening is off by default (auto_screen={v}); while uncalibrated, relevance only ranks.',
      'drawer.retrievalNote': 'A failed retrieval is not an exclusion reason: a paper without full text may still matter.',
      'drawer.actions': 'Available actions',
      'act.seed': 'Set as seed',
      'act.forward': 'Expand forward',
      'act.backward': 'Expand backward',
      'act.similar': 'Find related',
      'act.fulltext': 'Fetch full text',
      'act.include': 'Include',
      'act.exclude': 'Exclude',
      'act.maybe': 'Maybe',
      'act.exportOne': 'Export record',
      'toast.demoAction': 'Prototype: this action only becomes real in stage two with the live backend ({a}).',
      'toast.seedAdded': 'Set as seed ({n} total)',
      'toast.exported': 'Exported {name} ({n} records, sample data)',
      'toast.langSwitched': 'Interface language switched',
      'stop.saturated': 'Saturated: every source answered normally with no new records',
      'stop.no_new_results': 'All sources returned empty result sets',
      'stop.low_yield': 'Fewer than 5 new records (product heuristic, not proof of coverage)',
      'stop.max_rounds': 'Round cap reached',
      'stop.truncated': 'Caller record limit reached (usable but incomplete)',
      'stop.budget_exhausted': 'API request budget exhausted',
      'stop.canceled': 'Cancelled by the user',
      'stop.api_failure': 'Ended early by a source failure',
      'stop.completeNote': 'Only "saturated" and "all sources empty" allow claiming a completed search.',
      'stop.incompleteNote': 'This ending is incomplete and resumable; it must not be reported as coverage.',
      'job.queued': 'Queued', 'job.running': 'Running', 'job.completed': 'Completed',
      'job.partial': 'Partial', 'job.failed': 'Failed', 'job.canceled': 'Cancelled',
      'clock.locale': 'en-GB',
    },
  };

  const LANG_KEY = 'le.lang';
  let lang = 'zh';
  try { lang = localStorage.getItem(LANG_KEY) || 'zh'; } catch (e) { lang = 'zh'; }
  if (!I18N[lang]) lang = 'zh';

  function t(key, vars) {
    const dict = I18N[lang] || I18N.zh;
    let s = dict[key];
    if (s == null) s = (I18N.zh[key] != null ? I18N.zh[key] : key);
    if (vars) Object.keys(vars).forEach((k) => { s = s.split(`{${k}}`).join(vars[k]); });
    return s;
  }

  function setLang(next) {
    lang = I18N[next] ? next : 'zh';
    try { localStorage.setItem(LANG_KEY, lang); } catch (e) { /* 忽略隐私模式下的写入失败 */ }
    document.documentElement.lang = lang === 'zh' ? 'zh-CN' : 'en';
    applyI18n();
    /* 在 document 上派发且允许冒泡 —— 页面把监听挂在 document 上即可。
       之前只派发给 [data-trigger="rerender"] 且 bubbles:false，导致页面上没有该
       元素时事件根本发不出去，页面文案不会跟着语言切换（子代理实测发现）。 */
    const make = () => new CustomEvent('le:lang', { bubbles: true, detail: { lang } });
    document.dispatchEvent(make());
    $$('[data-trigger="rerender"]').forEach((n) => n.dispatchEvent(make()));
  }

  /** 把 data-i18n / data-i18n-title / data-i18n-ph 应用到 DOM。 */
  function applyI18n(root) {
    $$('[data-i18n]', root).forEach((n) => { n.textContent = t(n.getAttribute('data-i18n')); });
    $$('[data-i18n-html]', root).forEach((n) => { n.innerHTML = t(n.getAttribute('data-i18n-html')); });
    $$('[data-i18n-title]', root).forEach((n) => { n.setAttribute('title', t(n.getAttribute('data-i18n-title'))); });
    $$('[data-i18n-ph]', root).forEach((n) => { n.setAttribute('placeholder', t(n.getAttribute('data-i18n-ph'))); });
    $$('[data-i18n-aria]', root).forEach((n) => { n.setAttribute('aria-label', t(n.getAttribute('data-i18n-aria'))); });
  }

  /* ====================================================================== */
  /* 3. 徽标与语义标注                                                       */
  /* ====================================================================== */

  const SCH_CLASS = { include: 'badge--inc', exclude: 'badge--exc', maybe: 'badge--maybe', undecided: 'badge--undec' };
  /* screening.screening_status 是**会话级**状态，取值与逐条决定不同名，必须分开处理，
     否则界面上会漏出 'sch.not_started' 这样的原始 key。 */
  const SSTATUS_CLASS = {
    not_started: 'badge--undec',
    preliminary_included: 'badge--warn',
    final_included: 'badge--ok',
    excluded: 'badge--exc',
  };
  const RET_CLASS = { retrieved: 'badge--ok', paywalled: 'badge--warn', timeout: 'badge--err', too_large: 'badge--err', blocked_redirect: 'badge--err', not_attempted: '' };
  const JOB_CLASS = { queued: '', running: 'badge--info', completed: 'badge--ok', partial: 'badge--warn', failed: 'badge--err', canceled: '' };

  /** 同时接受逐条决定（include/exclude/maybe/undecided）与会话状态（not_started/…）。 */
  function badgeScreening(value) {
    const v = value || 'undecided';
    if (SSTATUS_CLASS[v]) return `<span class="badge ${SSTATUS_CLASS[v]}">${esc(t('sstatus.' + v))}</span>`;
    const key = SCH_CLASS[v] ? v : 'undecided';
    return `<span class="badge ${SCH_CLASS[key]}">${esc(t('sch.' + key))}</span>`;
  }
  function badgeRetrieval(status) {
    const key = status || 'not_attempted';
    return `<span class="badge ${RET_CLASS[key] || ''}">${esc(t('ret.' + key))}</span>`;
  }
  function badgeJob(status) {
    return `<span class="badge ${JOB_CLASS[status] || ''}">${esc(t('job.' + status))}</span>`;
  }
  function badgeDemo(label) { return `<span class="badge badge--demo">${esc(label || t('demo.tag'))}</span>`; }
  function badgeStop(reason) {
    if (!reason) return '';
    return `<span class="badge ${reason === 'saturated' || reason === 'no_new_results' ? 'badge--ok' : 'badge--warn'}">
      <span aria-hidden="true">${reason === 'saturated' || reason === 'no_new_results' ? '✓' : '!'}</span>${esc(t('stop.' + reason))}</span>`;
  }
  function badgeSource(source) { return `<span class="badge badge--outline">${esc(source)}</span>`; }

  /** 「缺失就写未提供」，绝不显示假值。 */
  function orMissing(v, missingLabel) {
    if (v === null || v === undefined || v === '' || (Array.isArray(v) && !v.length)) {
      return `<span class="t-muted">${esc(missingLabel || t('common.notProvided'))}</span>`;
    }
    return esc(v);
  }

  /* ====================================================================== */
  /* 4. 日期时钟                                                            */
  /* ====================================================================== */

  function startClock(node) {
    function tick() {
      const d = new Date();
      const date = d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }).toUpperCase();
      const time = d.toLocaleTimeString(lang === 'zh' ? 'zh-CN' : 'en-GB', { hour: '2-digit', minute: '2-digit' });
      node.textContent = `${date} · ${time}`;
    }
    tick();
    setInterval(tick, 30000);
  }

  /* ====================================================================== */
  /* 5. 外壳：顶栏 / 导航面板 / 页脚 / Toast / 抽屉                            */
  /* ====================================================================== */

  const MODULES = [
    { id: 'home', href: 'index.html' },
    { id: 'search', href: 'search.html' },
    { id: 'results', href: 'results.html' },
    { id: 'paper', href: 'paper.html' },
    { id: 'expand', href: 'expand.html' },
    { id: 'landscape', href: 'landscape.html' },
    { id: 'questions', href: 'questions.html' },
    { id: 'review', href: 'review.html' },
    { id: 'export', href: 'export.html' },
    { id: 'settings', href: 'settings.html' },
    { id: 'projects', href: 'projects.html' },
  ];

  const CROSS_PAGE = ['search', 'results', 'paper', 'expand', 'landscape', 'questions', 'review', 'export', 'settings', 'projects'];

  function shellHTML(current) {
    const onHome = current === 'home';
    const crumbs = onHome ? '' : `
      <div class="topbar__crumbs">
        <span class="sep" aria-hidden="true">/</span>
        <span data-i18n="mod.${current}">${esc(t('mod.' + current))}</span>
        <span class="t-xs">· ${esc(D.manifest.query)}</span>
      </div>`;
    const moduleItems = MODULES.map((m, i) => `
      <a class="navmap__item" href="${m.href}" ${m.id === current ? 'aria-current="page"' : ''}>
        <span class="navmap__idx">${pad2(i + 1)}</span>
        <span class="navmap__name" data-i18n="mod.${m.id}">${esc(t('mod.' + m.id))}</span>
        <span class="navmap__desc" data-i18n="mod.${m.id}.d">${esc(t('mod.' + m.id + '.d'))}</span>
      </a>`).join('');

    return `
      <header class="topbar ${onHome ? '' : 'topbar--workspace'}">
        <div class="row" style="gap:var(--s-3);min-width:0">
          <a class="brand" href="index.html">LEExtractor</a>
          ${crumbs}
          ${onHome ? '' : `<span class="demo-chip" data-i18n="demo.chip" data-i18n-title="demo.flag"
            title="${esc(t('demo.flag'))}">${esc(t('demo.chip'))}</span>`}
        </div>
        <time class="clock" id="le-clock"></time>
        <nav class="topbar__nav" aria-label="global">
          <div class="langswap" role="group" aria-label="Language">
            <button type="button" data-lang="zh" aria-pressed="${lang === 'zh'}">中文</button>
            <button type="button" data-lang="en" aria-pressed="${lang === 'en'}">EN</button>
          </div>
          <button type="button" id="le-history" data-i18n="nav.history">${esc(t('nav.history'))}</button>
          <button type="button" id="le-menu" aria-expanded="false" aria-haspopup="dialog" data-i18n="nav.menu">${esc(t('nav.menu'))}</button>
        </nav>
      </header>

      <dialog id="le-nav" aria-labelledby="le-nav-title">
        <div class="dialog__top">
          <h2 id="le-nav-title" style="font-size:var(--fs-h4);margin:0" data-i18n="nav.title">${esc(t('nav.title'))}</h2>
          <button type="button" class="close" data-close aria-label="${esc(t('nav.close'))}">×</button>
        </div>
        <div class="dialog__body">
          <p class="t-sm t-muted mb-4" data-i18n="nav.subtitle">${esc(t('nav.subtitle'))}</p>
          <nav class="navmap" aria-label="modules">${moduleItems}</nav>
          <div class="panel-note mt-4">
            <span data-i18n="demo.tag">${esc(t('demo.tag'))}</span> · <span data-i18n="demo.flag">${esc(t('demo.flag'))}</span>
          </div>
        </div>
      </dialog>

      <dialog id="le-history-dialog" aria-labelledby="le-hist-title">
        <div class="dialog__top">
          <h2 id="le-hist-title" style="font-size:var(--fs-h4);margin:0">${esc(lang === 'zh' ? '本次检索' : 'This session')}</h2>
          <button type="button" class="close" data-close aria-label="${esc(t('common.close'))}">×</button>
        </div>
        <div class="dialog__body" id="le-history-body"></div>
      </dialog>

      <button type="button" class="rail-toggle" id="le-rail-toggle" aria-expanded="false" hidden>
        <span aria-hidden="true">≡</span><span id="le-rail-toggle-label"></span>
      </button>

      <aside class="drawer" id="le-drawer" role="dialog" aria-modal="true" aria-labelledby="le-drawer-title" hidden>
        <div class="drawer__bar">
          <span class="drawer__type" id="le-drawer-type"></span>
          <span class="spacer"></span>
          <a class="btn btn--sm" id="le-drawer-expand" href="paper.html" data-i18n="common.expandPage">${esc(t('common.expandPage'))}</a>
          <button type="button" class="close" id="le-drawer-close" aria-label="${esc(t('common.close'))}">×</button>
        </div>
        <div class="drawer__body" id="le-drawer-body"></div>
        <div class="drawer__foot" id="le-drawer-foot"></div>
      </aside>
      <div class="overlay" id="le-overlay" hidden></div>

      <div id="toast" role="status" aria-live="polite"></div>
    `;
  }

  function footHTML() {
    return `
      <footer class="site-foot">
        <span class="site-foot__motto" data-i18n="foot.motto">${esc(t('foot.motto'))}</span>
        <span class="site-foot__meta">
          <span data-i18n="foot.edition">${esc(t('foot.edition'))}</span>
          <span>LEExtractor v${esc(D.version)}</span>
          <span>${esc(D.demoTag)}</span>
        </span>
      </footer>`;
  }

  let toastTimer = null;
  function toast(message) {
    const node = $('#toast');
    if (!node) return;
    node.textContent = message;
    node.classList.add('is-visible');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => node.classList.remove('is-visible'), 2800);
  }

  /* ---------------------------------------------------------------- 抽屉 */

  const state = {
    seeds: [],
    selection: [],
    lastFocus: null,
  };

  function ctxLabel(id) { return id === D.ctx.main ? (lang === 'zh' ? '主检索批次' : 'main search batch') : (lang === 'zh' ? '引文扩展批次' : 'expansion batch'); }

  function relationRows(paper) {
    const rel = D.graph.relations;
    const rows = [];
    ['bibliographic_coupling', 'co_citation', 'text_similarity'].forEach((type) => {
      rel[type].filter((e) => e.a === paper.id || e.b === paper.id).slice(0, 4).forEach((e) => {
        const other = D.byId.get(e.a === paper.id ? e.b : e.a);
        if (!other) return;
        const label = { bibliographic_coupling: lang === 'zh' ? '文献耦合' : 'Bibliographic coupling', co_citation: lang === 'zh' ? '共被引' : 'Co-citation', text_similarity: lang === 'zh' ? '文本相似' : 'Text similarity' }[type];
        const color = { bibliographic_coupling: '#1f6feb', co_citation: '#1f7a45', text_similarity: '#9a6100' }[type];
        rows.push(`
          <div class="edge">
            <span class="edge__dot" style="background:${color}"></span>
            <span class="t-xs t-muted t-nowrap">${esc(label)}</span>
            <button type="button" class="edge__name" style="text-align:left" data-open-paper="${esc(other.id)}">${esc(other.title)}</button>
            <span class="edge__w">${esc(String(e.weight))}</span>
          </div>`);
      });
    });
    return rows.length ? rows.join('') : `<p class="t-sm t-muted" data-i18n="drawer.noRelations"></p>`;
  }

  function citationRows(paper) {
    const refs = (paper.reference_ids || []).filter((r) => D.byId.has(r));
    const citers = D.papers.filter((p) => (p.reference_ids || []).includes(paper.id));
    const block = (title, list) => {
      if (!list.length) return '';
      return `<h4 class="t-xs t-muted mt-3" style="letter-spacing:.06em">${esc(title)} · ${list.length}</h4>
        <div class="edges mt-2">${list.slice(0, 6).map((p) => `
          <div class="edge">
            <span class="edge__dot" style="background:var(--btn)"></span>
            <button type="button" class="edge__name" style="text-align:left" data-open-paper="${esc(p.id)}">${esc(p.title)}</button>
            <span class="edge__w">${esc(String(p.year || '?'))}</span>
          </div>`).join('')}</div>`;
    };
    const refList = refs.map((id) => D.byId.get(id));
    const empty = !refList.length && !citers.length;
    return `
      ${block(lang === 'zh' ? '本文引用（样本内）' : 'References (in sample)', refList)}
      ${block(lang === 'zh' ? '被本文引用（样本内）' : 'Cited by (in sample)', citers)}
      ${empty ? `<p class="t-sm t-muted">${esc(lang === 'zh' ? '当前样本内没有可追溯的引文记录。' : 'No traceable citations inside the current sample.')}</p>` : ''}`;
  }

  function drawerHTML(paper) {
    const dec = D.helpers.decisionOf(paper);
    const ret = D.helpers.retrievalOf(paper);
    const ids = paper.identifiers || {};
    const idLines = [
      ids.doi ? `DOI ${ids.doi}` : '',
      ids.openalex_id ? `OpenAlex ${ids.openalex_id}` : '',
      ids.semantic_scholar_id ? `S2 ${ids.semantic_scholar_id.slice(0, 16)}…` : '',
      ids.arxiv_id ? `arXiv ${ids.arxiv_id}` : '',
    ].filter(Boolean);
    const ctx = paper.score_context_id;
    const multiCtx = D.facets.score_contexts.length > 1;

    const breakdown = Object.keys(paper.score_breakdown || {}).length
      ? `<div class="meter mt-3">${Object.keys(paper.score_breakdown).map((k) => `
          <div class="row" style="gap:var(--s-2)">
            <span class="t-xs t-muted" style="width:64px">${esc(k)}</span>
            <span class="facet-row__bar" style="flex:1 1 auto"><i style="width:${pct(paper.score_breakdown[k])}"></i></span>
            <span class="t-xs t-num">${esc(paper.score_breakdown[k].toFixed(2))}</span>
          </div>`).join('')}</div>`
      : `<p class="t-sm t-muted">${esc(lang === 'zh' ? '没有分项得分记录。' : 'No score breakdown recorded.')}</p>`;

    const traces = (paper.discovery_traces || []).length
      ? paper.discovery_traces.map((tr) => `
          <div class="trace ${tr.seed_id ? '' : 'trace--seed'}">
            <div class="trace__top">
              <span class="badge">${esc(t('dis.' + tr.method) === 'dis.' + tr.method ? tr.method : t('dis.' + tr.method))}</span>
              <span class="t-muted">${esc(tr.provider || t('common.unknown'))}</span>
              ${tr.round_no != null ? `<span class="t-muted">${lang === 'zh' ? '轮次' : 'round'} ${esc(String(tr.round_no))}</span>` : ''}
              ${tr.score != null ? `<span class="t-muted t-num">score ${esc(tr.score.toFixed(2))}</span>` : ''}
              <span class="spacer"></span>
              <span class="t-xs t-muted">${esc((tr.timestamp || '').slice(0, 10))}</span>
            </div>
            ${tr.query ? `<div class="trace__q">${esc(tr.query)}</div>` : ''}
            ${tr.seed_id ? `<div class="t-xs t-muted mt-2">${lang === 'zh' ? '种子' : 'seed'} ${esc(tr.seed_id)}${tr.evidence_ids && tr.evidence_ids.length ? ` · ${lang === 'zh' ? '证据' : 'evidence'} ${esc(tr.evidence_ids.join(', '))}` : ''}</div>` : ''}
          </div>`).join('')
      : `<p class="t-sm t-muted" data-i18n="drawer.noTrace"></p>`;

    const decisionBlock = dec
      ? `<div class="dl dl--tight">
           <dt>${esc(lang === 'zh' ? '阶段' : 'Stage')}</dt><dd>${esc(dec.stage)}</dd>
           <dt>${esc(lang === 'zh' ? '决定' : 'Decision')}</dt><dd>${badgeScreening(dec.decision)}</dd>
           <dt>${esc(lang === 'zh' ? '理由' : 'Reason')}</dt><dd>${esc(dec.reason)}</dd>
           <dt>${esc(lang === 'zh' ? '记录者 / 时间' : 'By / at')}</dt><dd>${esc(dec.by)} · ${esc(dec.at.slice(0, 10))}</dd>
         </div>
         <h4 class="t-xs t-muted mt-4" style="letter-spacing:.06em" data-i18n="drawer.decisionHistory"></h4>
         <div class="edges mt-2"><div class="edge"><span class="edge__dot" style="background:var(--dec-${dec.decision === 'include' ? 'include' : dec.decision === 'exclude' ? 'exclude' : 'maybe'})"></span>
           <span class="edge__name">${esc(dec.reason)}</span><span class="edge__w">${esc(dec.at.slice(0, 10))}</span></div></div>`
      : `<p class="t-sm t-muted" data-i18n="drawer.noDecision"></p>`;

    const conflict = D.conflicts.find((c) => c.paperId === paper.id);
    const conflictBlock = conflict
      ? `<div class="panel-note" style="background:var(--warn-bg);border:1px solid var(--warn-line);margin-top:var(--s-3)">
           <strong>${esc(lang === 'zh' ? '待复核冲突' : 'Conflict to resolve')}</strong> ·
           ${conflict.decisions.map((d) => `${esc(d.by)}: ${esc(t('sch.' + d.decision))}`).join(' / ')}
         </div>`
      : '';

    const fulltext = ret
      ? `<div class="dl dl--tight">
           <dt>${esc(lang === 'zh' ? '状态' : 'Status')}</dt><dd>${badgeRetrieval(ret.status)}</dd>
           <dt>${esc(lang === 'zh' ? '体积' : 'Size')}</dt><dd>${ret.bytes ? esc((ret.bytes / 1048576).toFixed(1)) + ' MiB' : esc(t('common.none'))}</dd>
           <dt>${esc(lang === 'zh' ? '页数' : 'Pages')}</dt><dd>${ret.pages ? esc(String(ret.pages)) : esc(t('common.none'))}</dd>
           <dt>provenance</dt><dd>${orMissing(ret.provenance)}</dd>
           ${ret.note ? `<dt>${esc(lang === 'zh' ? '说明' : 'Note')}</dt><dd>${esc(ret.note)}</dd>` : ''}
         </div>`
      : `<p class="t-sm t-muted">${esc(lang === 'zh' ? '尚未尝试获取全文。' : 'Full-text retrieval not attempted.')}</p>`;

    return `
      <span class="drawer__type">${esc(lang === 'zh' ? '文献' : 'Work')} · ${esc(paper.source)}</span>
      <h2 class="drawer__title" id="le-drawer-title">${esc(paper.title)}</h2>
      <div class="drawer__sub">
        <span>${esc(paper.year || '?')}</span><span class="sep">·</span>
        <span>${esc(paper.venue || t('common.notProvided'))}</span><span class="sep">·</span>
        <span class="t-num">${esc(num(paper.citation_count))} ${esc(lang === 'zh' ? '被引' : 'citations')}</span>
        <span class="sep">·</span>${badgeScreening(dec ? dec.decision : 'undecided')}
        <span class="sep">·</span>${badgeRetrieval(ret ? ret.status : 'not_attempted')}
      </div>

      <div class="section">
        <h3 class="section__title" data-i18n="drawer.meta"></h3>
        <dl class="dl">
          <dt data-i18n="drawer.authors"></dt><dd>${orMissing((paper.authors || []).map((a) => a.name).join(', '))}</dd>
          <dt data-i18n="drawer.venue"></dt><dd>${orMissing(paper.venue)}</dd>
          <dt data-i18n="drawer.year"></dt><dd>${orMissing(paper.year)}</dd>
          <dt data-i18n="drawer.provider"></dt><dd>${orMissing(paper.source)}</dd>
          <dt data-i18n="drawer.doi"></dt><dd>${orMissing(paper.doi, lang === 'zh' ? '该来源未提供 DOI' : 'No DOI from this provider')}</dd>
          <dt data-i18n="drawer.ids"></dt><dd>${idLines.length ? esc(idLines.join(' · ')) : esc(t('common.notProvided'))}</dd>
          <dt data-i18n="drawer.citations"></dt><dd class="t-num">${paper.citation_count ? esc(num(paper.citation_count)) : `${esc(num(0))} <span class="t-muted t-xs">(${esc(lang === 'zh' ? '来源未标记不等于零被引' : 'missing is not the same as zero')})</span>`}</dd>
          <dt data-i18n="drawer.references"></dt><dd class="t-num">${esc(num(paper.reference_count))}</dd>
          <dt data-i18n="drawer.url"></dt><dd>${paper.url ? `<a href="${esc(paper.url)}" target="_blank" rel="noopener noreferrer">${esc(paper.url)}</a>` : esc(t('common.notProvided'))}</dd>
        </dl>
      </div>

      <div class="section">
        <h3 class="section__title" data-i18n="drawer.abstract"></h3>
        <p class="abs">${paper.abstract ? esc(paper.abstract) : esc(t('drawer.noAbstract'))}</p>
        <div class="chips mt-3">
          ${(paper.topics || []).length ? paper.topics.map((x) => `<span class="chip">${esc(x)}</span>`).join('') : `<span class="t-sm t-muted">${esc(lang === 'zh' ? '该来源未提供主题标签。' : 'No topic labels from this provider.')}</span>`}
        </div>
      </div>

      <div class="section">
        <h3 class="section__title" data-i18n="drawer.relevance"></h3>
        <div class="row" style="gap:var(--s-4)">
          <span class="t-num" style="font-size:var(--fs-h3);font-weight:var(--fw-semi)">${esc(paper.relevance_score.toFixed(3))}</span>
          <span class="facet-row__bar" style="flex:1 1 auto;height:6px"><i style="width:${pct(paper.relevance_score)}"></i></span>
        </div>
        <p class="t-sm t-muted mt-2">${t('drawer.scoreNote', { ctx: esc(ctxLabel(ctx)) })}</p>
        ${multiCtx ? `<div class="panel-note mt-2" style="background:var(--warn-bg);border:1px solid var(--warn-line)">${esc(t('drawer.ctxWarn'))}</div>` : ''}
        <h4 class="t-xs t-muted mt-4" style="letter-spacing:.06em" data-i18n="drawer.breakdown"></h4>
        ${breakdown}
        <div class="t-xs t-mono t-muted mt-2">score_context_id: ${esc(ctx)}</div>
      </div>

      <div class="section">
        <h3 class="section__title" data-i18n="drawer.trace"></h3>
        ${traces}
      </div>

      <div class="section">
        <h3 class="section__title" data-i18n="drawer.relations"></h3>
        ${citationRows(paper)}
        <h4 class="t-xs t-muted mt-4" style="letter-spacing:.06em">${esc(lang === 'zh' ? '三类关系边' : 'Three relation types')}</h4>
        <div class="edges mt-2">${relationRows(paper)}</div>
        <p class="t-xs t-muted mt-3">${esc(t('drawer.relationNote'))}</p>
      </div>

      <div class="section">
        <h3 class="section__title" data-i18n="drawer.screening"></h3>
        ${decisionBlock}
        ${conflictBlock}
        <p class="t-xs t-muted mt-3">${esc(t('drawer.autoScreenNote', { v: String(D.screening.auto_screen) }))}</p>
      </div>

      <div class="section">
        <h3 class="section__title" data-i18n="drawer.fulltext"></h3>
        ${fulltext}
        <p class="t-xs t-muted mt-3">${esc(t('drawer.retrievalNote'))}</p>
      </div>`;
  }

  function drawerFootHTML(paper) {
    const dec = D.helpers.decisionOf(paper);
    const seeded = state.seeds.indexOf(paper.id) >= 0;
    return `
      <button type="button" class="btn btn--sm btn--primary" data-act="seed" ${seeded ? 'disabled' : ''}>${esc(seeded ? (lang === 'zh' ? '已是种子' : 'Already a seed') : t('act.seed'))}</button>
      <button type="button" class="btn btn--sm" data-act="forward">${esc(t('act.forward'))}</button>
      <button type="button" class="btn btn--sm" data-act="backward">${esc(t('act.backward'))}</button>
      <button type="button" class="btn btn--sm" data-act="similar">${esc(t('act.similar'))}</button>
      <button type="button" class="btn btn--sm" data-act="fulltext">${esc(t('act.fulltext'))}</button>
      <span class="spacer"></span>
      <span class="btn-group" role="group" aria-label="screening">
        <button type="button" data-act="include" aria-pressed="${dec && dec.decision === 'include'}">${esc(t('act.include'))}</button>
        <button type="button" data-act="maybe" aria-pressed="${dec && dec.decision === 'maybe'}">${esc(t('act.maybe'))}</button>
        <button type="button" data-act="exclude" aria-pressed="${dec && dec.decision === 'exclude'}">${esc(t('act.exclude'))}</button>
      </span>
      <button type="button" class="btn btn--sm btn--quiet" data-act="export">${esc(t('act.exportOne'))}</button>`;
  }

  /* 动效参数集中在这里；CSS 侧的对应变量在 tokens.css 的 --dur-* / --ease-*。
     两处数值必须一致，改一处要改另一处。 */
  const DUR = { fast: 160, base: 240, slow: 320, count: 620, drawer: 240, toast: 2800 };

  let currentPaper = null;

  /* ------------------------------------------------------------------ 抽屉控制器
     动画、遮罩、滚动锁定、焦点在这里统一管理。三个要点：
     1) closeDrawer 用延迟隐藏来等退场动画；openDrawer 必须**先清掉**这个待执行的
        计时器 —— 否则快速「开 → 关 → 再开」会把新抽屉 hidden 掉，而滚动锁定已经
        被清空，页面就卡在既锁不住又不可滚的状态。
     2) 滚动锁定以 drawerCtl.open 为准，不由计时器决定，两者永不失步。
     3) 焦点：打开时记住来源，退场结束后还原；Tab 在抽屉内循环，不进入被遮住的背景。
        焦点还原也放进那个计时器里，重开时一并取消，避免焦点来回闪。 */
  const drawerCtl = { hideTimer: 0, open: false, lastFocus: null };

  function drawerFocusables() {
    const d = $('#le-drawer');
    if (!d) return [];
    return $$('a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])', d)
      .filter((n) => n.offsetParent !== null);
  }

  function lockScroll(on) {
    document.body.style.overflow = on ? 'hidden' : '';
    document.body.classList.toggle('drawer-open', !!on);
  }

  function openDrawer(paperId, opts) {
    const paper = D.byId.get(paperId);
    if (!paper) { toast(lang === 'zh' ? '找不到这篇文献。' : 'Paper not found.'); return; }
    currentPaper = paper;
    const drawer = $('#le-drawer');
    const overlay = $('#le-overlay');
    if (!drawer) return;

    if (drawerCtl.hideTimer) { clearTimeout(drawerCtl.hideTimer); drawerCtl.hideTimer = 0; }
    if (!drawerCtl.open) drawerCtl.lastFocus = document.activeElement;
    drawerCtl.open = true;
    state.lastFocus = drawerCtl.lastFocus;

    $('#le-drawer-type').textContent = `${lang === 'zh' ? '文献' : 'Work'} · ${paper.source}`;
    $('#le-drawer-body').innerHTML = drawerHTML(paper);
    $('#le-drawer-foot').innerHTML = drawerFootHTML(paper);
    $('#le-drawer-expand').setAttribute('href', `paper.html?id=${encodeURIComponent(paper.id)}`);

    drawer.hidden = false;
    drawer.classList.remove('is-open');
    if (overlay) { overlay.hidden = false; overlay.classList.remove('is-open'); }
    requestAnimationFrame(() => {
      drawer.classList.add('is-open');
      if (overlay) overlay.classList.add('is-open');
    });
    lockScroll(true);
    applyI18n(drawer);
    $('#le-drawer-close').focus();

    if (opts && opts.push) {
      try { history.pushState({ drawer: paper.id }, '', `#paper=${encodeURIComponent(paper.id)}`); } catch (e) { /* file:// 下可能受限 */ }
    }
    $$('[data-open-paper]').forEach((b) => { b.onclick = () => openDrawer(b.getAttribute('data-open-paper'), { push: true }); });
    $$('[data-act]', $('#le-drawer-foot')).forEach((b) => {
      b.onclick = () => drawerAction(b.getAttribute('data-act'), paper, b);
    });
  }

  function closeDrawer() {
    const drawer = $('#le-drawer');
    const overlay = $('#le-overlay');
    if (!drawer || !drawerCtl.open) return;
    drawerCtl.open = false;
    drawer.classList.remove('is-open');
    if (overlay) overlay.classList.remove('is-open');
    lockScroll(false);
    if (drawerCtl.hideTimer) clearTimeout(drawerCtl.hideTimer);
    drawerCtl.hideTimer = setTimeout(() => {
      drawerCtl.hideTimer = 0;
      drawer.hidden = true;
      if (overlay) overlay.hidden = true;
      const back = drawerCtl.lastFocus;
      drawerCtl.lastFocus = null;
      state.lastFocus = null;
      if (back && back.focus && document.contains(back)) back.focus();
    }, DUR.drawer);
  }

  function drawerAction(action, paper, btn) {
    if (action === 'seed') {
      if (state.seeds.indexOf(paper.id) < 0) state.seeds.push(paper.id);
      btn.disabled = true;
      btn.textContent = lang === 'zh' ? '已是种子' : 'Already a seed';
      toast(t('toast.seedAdded', { n: state.seeds.length }));
      return;
    }
    if (action === 'export') {
      toast(t('toast.exported', { name: 'JSON', n: 1 }));
      return;
    }
    if (['include', 'maybe', 'exclude'].includes(action)) {
      $$('[data-act]', btn.parentElement).forEach((b) => b.setAttribute('aria-pressed', String(b === btn)));
      toast(lang === 'zh'
        ? `演示原型：决定已记录在界面上（${t('sch.' + action)}），阶段二会写入真实会话并更新 PRISMA 账本。`
        : `Prototype: decision recorded in the UI (${t('sch.' + action)}); stage two writes it to the session and the PRISMA ledger.`);
      return;
    }
    toast(t('toast.demoAction', { a: t('act.' + action) }));
  }

  /* ====================================================================== */
  /* 6. 格式化辅助                                                          */
  /* ====================================================================== */

  function facetLabel(group, key) {
    if (key === '__unknown__') return t('common.unknown');
    if (group === 'discovery') return t('dis.' + key) === 'dis.' + key ? key : t('dis.' + key);
    if (group === 'screening') return t('sch.' + key);
    if (group === 'retrieval') return t('ret.' + key);
    if (group === 'sources') return key;
    return key;
  }

  /* ====================================================================== */
  /* 7. 挂载                                                                */
  /* ====================================================================== */

  function mount(opts) {
    const options = opts || {};
    const current = options.page || document.body.getAttribute('data-page') || 'home';
    const shell = $('#shell');
    if (shell) shell.innerHTML = shellHTML(current);
    const footSlot = $('#foot');
    if (footSlot) footSlot.innerHTML = footHTML();

    document.documentElement.lang = lang === 'zh' ? 'zh-CN' : 'en';
    applyI18n();
    motion.init();

    // 页面入场：整页轻微上浮。首页自己做封面/检索区的景深动画，不叠加。
    const main = document.getElementById('main');
    if (main && current !== 'home' && !motion.reduced()) main.classList.add('page-enter');

    const clock = $('#le-clock');
    if (clock) startClock(clock);

    // 语言切换
    $$('[data-lang]').forEach((b) => {
      b.onclick = () => {
        if (b.getAttribute('data-lang') === lang) return;
        setLang(b.getAttribute('data-lang'));
        $$('[data-lang]').forEach((x) => x.setAttribute('aria-pressed', String(x.getAttribute('data-lang') === lang)));
        const clock2 = $('#le-clock');
        if (clock2) startClock(clock2);
        toast(t('toast.langSwitched'));
      };
    });

    // 导航面板
    const nav = $('#le-nav');
    const menuBtn = $('#le-menu');
    if (menuBtn && nav) {
      menuBtn.onclick = () => { nav.showModal(); menuBtn.setAttribute('aria-expanded', 'true'); };
      nav.addEventListener('close', () => menuBtn.setAttribute('aria-expanded', 'false'));
    }
    $$('dialog').forEach((d) => {
      $$('[data-close]', d).forEach((b) => { b.onclick = () => d.close(); });
      d.addEventListener('click', (e) => {
        if (e.target === d) d.close();
      });
    });

    // 历史面板
    const histBtn = $('#le-history');
    if (histBtn) {
      histBtn.onclick = () => {
        const body = $('#le-history-body');
        const items = [
          { q: D.manifest.query, meta: `${D.papers.length} ${lang === 'zh' ? '篇' : 'papers'} · ${D.screening.status}` },
          { q: lang === 'zh' ? '具身智能与机器人操作' : 'Embodied AI and robot manipulation', meta: `18 ${lang === 'zh' ? '篇' : 'papers'} · not_started` },
          { q: 'deep learning plant phenotyping', meta: `8 ${lang === 'zh' ? '篇' : 'papers'} · not_started` },
        ];
        body.innerHTML = items.map((it) => `
          <button type="button" class="history-item" style="display:block;width:100%;text-align:left;border-bottom:1px solid var(--border);padding:var(--s-3) 0">
            <span style="font-size:var(--fs-sm)">${esc(it.q)}</span>
            <span class="t-xs t-muted" style="display:block">${esc(it.meta)}</span>
          </button>`).join('');
        const dlg = $('#le-history-dialog');
        if (dlg) { dlg.showModal(); $('#le-hist-title').textContent = lang === 'zh' ? '本次检索' : 'This session'; }
      };
    }

    // 抽屉交互
    const closeBtn = $('#le-drawer-close');
    if (closeBtn) closeBtn.onclick = closeDrawer;
    const overlay = $('#le-overlay');
    if (overlay) overlay.onclick = closeDrawer;
    document.addEventListener('keydown', (e) => {
      const d = $('#le-drawer');
      if (!d || d.hidden || !drawerCtl.open) return;
      if (e.key === 'Escape') { e.preventDefault(); closeDrawer(); return; }
      if (e.key !== 'Tab') return;
      /* 焦点约束：Tab 只在抽屉内部循环，不会跑进被遮住的背景 */
      const list = drawerFocusables();
      if (!list.length) return;
      const first = list[0];
      const last = list[list.length - 1];
      const active = document.activeElement;
      if (!d.contains(active)) { e.preventDefault(); first.focus(); return; }
      if (e.shiftKey && active === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && active === last) { e.preventDefault(); first.focus(); }
    });

    window.addEventListener('popstate', () => {
      if (!location.hash.startsWith('#paper=')) closeDrawer();
    });

    // 页面自行声明的重渲染钩子
    document.addEventListener('le:lang', () => applyI18n());

    /* 窄屏的筛选入口：侧栏在 ≤959px 默认收起，由一个悬浮按钮按需展开 ——
       否则用户要先滚过整屏筛选条件才能看到结果。宽屏下按钮不出现、侧栏始终展开。 */
    /* 结果页用的是 .shell（筛选在左，沿用 OpenAlex SERP），其它页是 .shell--main-first；
       两者都带 .shell，所以统一用 .shell > aside 取侧栏。 */
    const rail = document.querySelector('.shell > aside');
    const railBtn = $('#le-rail-toggle');
    const railLabel = $('#le-rail-toggle-label');
    const railQuery = window.matchMedia('(max-width: 959px)');
    const railText = () => (lang === 'zh' ? '筛选与分面' : 'Filters & facets');
    function syncRail() {
      if (!railBtn) return;
      const narrow = railQuery.matches;
      railBtn.hidden = !(narrow && !!rail);
      if (railLabel) railLabel.textContent = railText();
      if (!narrow && rail) {
        rail.classList.remove('is-open');
        railBtn.setAttribute('aria-expanded', 'false');
      }
    }
    if (railBtn && rail) {
      rail.setAttribute('id', rail.getAttribute('id') || 'le-rail');
      railBtn.setAttribute('aria-controls', rail.getAttribute('id'));
      railBtn.onclick = () => {
        const open = !rail.classList.contains('is-open');
        rail.classList.toggle('is-open', open);
        railBtn.setAttribute('aria-expanded', String(open));
        if (open) rail.scrollIntoView({ block: 'start', behavior: motion.reduced() ? 'auto' : 'smooth' });
      };
      if (railQuery.addEventListener) railQuery.addEventListener('change', syncRail);
      syncRail();
    }

    // 深链：#paper=<id> 直接开抽屉
    if (location.hash.startsWith('#paper=')) {
      const id = decodeURIComponent(location.hash.slice(7));
      if (D.byId.has(id)) setTimeout(() => openDrawer(id), 60);
    }

    return { current };
  }

  /* ====================================================================== */
  /* 8. 减少动态效果                                                        */
  /* ====================================================================== */

  const motion = (function () {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    let reduced = mq.matches;
    const listeners = [];
    function apply() {
      document.body.classList.toggle('reduced', reduced);
      listeners.forEach((fn) => { try { fn(reduced); } catch (e) { /* 单个监听器出错不影响其它 */ } });
    }
    mq.addEventListener('change', (e) => { reduced = e.matches; apply(); });
    return {
      reduced: () => reduced,
      set: (v) => { reduced = !!v; apply(); },
      onChange: (fn) => { listeners.push(fn); },
      init: apply,
    };
  })();

  /* ====================================================================== */
  /* 9. 动效工具（CSS 动画的开关在 tokens.css / app.css，这里只管编号与数值） */
  /* ====================================================================== */

  const fx = {
    reduced: () => motion.reduced(),

    /** 给容器子元素编号，让 CSS 里 .stagger > * 的延迟生效。 */
    stagger(container, opts) {
      if (!container || motion.reduced()) return;
      const max = (opts && opts.max) || 14;
      const list = Array.prototype.slice.call(container.children);
      list.forEach((node, i) => {
        if (i < max) node.style.setProperty('--i', String(i));
        else node.style.setProperty('--i', String(max));
      });
      container.classList.add('stagger');
    },

    /** 数字滚动到目标值。允许动效时逐帧，否则直接落位。
        竞态处理：同一元素可能被多个任务先后要求滚动，用递增令牌只让**最新**一次写文字，
        旧动画在下一帧自行退出 —— 否则旧任务会在新任务之后收尾，把数字写回旧值。 */
    countUp(node, to, opts) {
      if (!node) return;
      const target = Number(to) || 0;
      const suffix = (opts && opts.suffix) || '';
      const duration = (opts && opts.duration) || DUR.count;
      const token = String((Number(node.dataset.fxCount) || 0) + 1);
      node.dataset.fxCount = token;
      if (motion.reduced() || typeof requestAnimationFrame !== 'function') {
        node.textContent = target.toLocaleString('en-US') + suffix;
        return;
      }
      const digits = Number(String(node.textContent).replace(/[^\d.-]/g, ''));
      const base = Number.isFinite(digits) && digits > 0 ? digits : 0;
      if (base === target) { node.textContent = target.toLocaleString('en-US') + suffix; return; }
      const started = (window.performance && performance.now()) || Date.now();
      const step = (now) => {
        if (node.dataset.fxCount !== token) return;
        /* 运行中把"减少动态效果"打开时，立即落位而不是继续滚完 */
        if (motion.reduced()) { node.textContent = target.toLocaleString('en-US') + suffix; return; }
        const t = Math.min(1, (now - started) / duration);
        const eased = 1 - Math.pow(1 - t, 3);
        node.textContent = Math.round(base + (target - base) * eased).toLocaleString('en-US') + suffix;
        if (t < 1) requestAnimationFrame(step);
      };
      requestAnimationFrame(step);
    },

    /** 重新触发一次一次性动画（去掉类 → 强制重排 → 加回）。 */
    replay(node, cls) {
      if (!node || motion.reduced()) return;
      const klass = cls || 'fx-swap';
      node.classList.remove(klass);
      void node.offsetWidth;
      node.classList.add(klass);
    },
  };

  /* ====================================================================== */
  /* 10. 导出接口                                                           */
  /* ====================================================================== */

  return {
    motion, fx,
    t, setLang, applyI18n: () => applyI18n(),
    lang: () => lang,
    $, $$, esc, num, pct, el, pad2, orMissing,
    badgeScreening, badgeRetrieval, badgeJob, badgeDemo, badgeStop, badgeSource,
    facetLabel, toast, mount, openDrawer, closeDrawer,
    state, MODULES, CROSS_PAGE, relationRows, citationRows, drawerHTML, drawerFootHTML,
    isHome: () => document.body.getAttribute('data-page') === 'home',
  };
})();
