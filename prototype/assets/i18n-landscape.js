/* ============================================================================
   LEExtractor — 证据版图页文案（landscape）
   ----------------------------------------------------------------------------
   与 i18n-pages.js 同构：本文件只做一次 Object.assign(window.LE_PAGES, ...)，
   不碰共享外壳字典，也不改动 i18n-pages.js。

   用法（页面脚本里）：
     const s = (k, v) => LE.pt('landscape', k, v);
     s('title')

   业务语义（全部有据，不违反 docs/business-contract-notes.md 与
   docs/known-gaps-v095.md）：
     · PageRank / 社群 / 最短路径只跑引文边（citation，有向 source→target）。
     · 三类关系边的规范名只有 bibliographic_coupling / co_citation /
       text_similarity；分数永不互相加权或相加（G3）。
     · 本页一切分析都限定当前样本（sample_only / sample_size）。
     ========================================================================== */

'use strict';

window.LE_PAGES = window.LE_PAGES || {};

Object.assign(window.LE_PAGES, {

  landscape: {
    zh: {
      skip: '跳到主内容',
      title: '证据版图',
      lead: '把当前样本画成一张图：**引文边**决定 PageRank 与社群，三类关系边各自成层；样本内算不出的关系，不等于它不存在。',
      toResults: '← 文献结果',
      toExpand: '引文扩展',
      toQuestions: '研究机会 →',
      demoNote: '本页所有数字都从示例语料确定性推导，不是真实文献计量结果。',

      sampleNote: '样本限定：本页全部图形、榜单与表格只描述当前样本的 {n} 篇文献（sample_only={flag}），不是领域全景，也不代表全球文献。样本外情况未知，缺关系记录只说明当前样本没有观测到。',

      limitsTitle: '本页的已知边界',
      limitSample: '所有分析限定当前样本 {n} 篇；样本外的文献没有被观测。',
      limitRelation: '关系未知不等于不存在：样本内算不出的关系边，只说明当前样本没有对应记录。',
      limitComm: '社群划分用引文连通分量近似；v0.9.5 的真实实现是 Louvain 社群检测，组数与成员会随样本变化。',
      limitNovelty: '新颖度与覆盖平衡都是样本内的相对量，没有外部领域基线。',
      limitCite: '指向样本之外的引用不画在图上：那是正常事实，只表示这些被引工作没有被本次检索收进来。',

      graphTitle: '引文网络（样本内）',
      graphIntro: '节点按**社群**分组，沿同一个圆环排成扇区；节点半径按 PageRank 分档（前 3 / 前 8 / 其余）。位置只由数据顺序算出，没有随机数，刷新后完全一致。点按或按回车/空格打开详情抽屉。',
      graphArrow: '箭头方向 = source（引用方）→ target（被引方），与 graph.links 的字段同义。',
      graphA11y: '键盘路径：Tab 聚焦图内节点 → 回车或空格打开抽屉 → Esc 关闭。也可以直接使用下方「中心度榜」表格，它列出同样的前 10 篇。',
      graphScroll: '窄屏提示：这张图可以左右滑动查看整幅，节点因此保持可点按的大小。',
      graphEmpty: '当前样本内没有引文边，因此没有可绘制的引文网络；PageRank 与社群也会为空。这是缺数据，不是「没有引用关系」。',
      statNodes: '节点（样本内文献）',
      statEdges: '引文边（样本内）',
      statEdgesSub: '另有 {ext} 条引用指向样本之外，不画在图上',
      statGroups: '社群（连通分量近似）',
      statGroupsSub: '真实实现为 Louvain',
      statDangling: '悬空引文边',
      statDanglingSub: '端点必须在样本内；0 表示不变量成立',
      graphLegendGroups: '社群着色',
      graphLegendRadius: '半径 = PageRank 分档（前 3 / 前 8 / 其余）',
      groupLabel: '社群 {i} · {n} 篇',
      graphSelfNote: '样本内共 {n} 条可追溯引文边；它们全部来自 reference_ids 的解析结果。',
      nodeAria: '{title} · 社群 {c} · PageRank {score}（回车打开详情抽屉）',

      citeOnlyTitle: '只跑引文边的算法',
      citeOnlyBody: 'PageRank、社群划分与最短证据路径**只**使用引文边（有向，source→target）。三类关系边即使叠加显示，也不会进入这三个计算。',
      citeOnlyEdge: '引文边（样本内）',
      citeOnlyPR: 'PageRank',
      citeOnlyPRValue: '前 10 名，按分数降序（同分再按 id）',
      citeOnlyComm: '社群划分',
      citeOnlyCommValue: '{k} 组 · 连通分量近似',
      citeOnlyPath: '最短证据路径',
      citeOnlyPathValue: '原型未渲染（源码 path() 在无路径时返回空）',
      citeOnlyUnknown: '关系未知不等于不存在：样本内没有算出的引文或关系，只说明当前样本没有观测到对应记录。',

      relTitle: '三类关系边（各自成层）',
      relIntro: '三个规范名固定为下面三条，数量直接取 relationCounts()。开启后叠加在引文网络图上，只为位置参考，不代表它们属于引文图。',
      relBC: '文献耦合：两篇引用了同一篇文献（权重 = 共享参考文献数）',
      relCC: '共被引：两篇被样本内同一篇文献引用（权重 = 共同引用者数）',
      relTS: '文本相似：标题与摘要的词法余弦（不是 embedding 语义相似）',
      relShow: '在图中叠加',
      relHide: '从图中移除',
      relDrawn: '图中只画权重最高的 {n} 条（这一类共 {all} 条）。',
      relEmpty: '当前样本内这一类关系边为 0 条，因此没有可叠加的内容。',
      relNote: '关系边与引文边并列，**分数永不相互加权或相加**，也不合成一条「关系总分」。图例只写三个规范名：不列历史别名，也不列合计行。',
      relUnknown: '某一对文献没有关系边，只表示当前样本没有算出这条关系，不等于它们之间真的没有关系。',

      centTitle: '中心度榜（PageRank · 只跑引文边）',
      centNote: '样本内前 10 条。PageRank 只看引文结构，与被引次数是两件事；跨样本不可比较。',
      colRank: '#',
      colPaper: '文献',
      colScore: 'PageRank',
      colCites: '被引',
      centOpen: '打开「{title}」的详情抽屉',

      clusterTitle: '主题簇（样本内）',
      clusterNote: '主题簇由样本内主题标签聚合，与上面的引文社群**不是同一种划分**：一个是主题，一个是引文连通性，两者不能互相解释。',
      clusterTerms: '术语',
      clusterSize: '{n} 篇',

      yearsTitle: '时间演变（样本内）',
      yearsNote: '柱高按样本内篇数；年份未知的记录不并入任何年份。这条曲线只反映本次检索收到的样本，不代表领域走势。',
      yearsUnknown: '年份未知：{n} 篇（不并入任何年份）',

      noveltyTitle: '新颖度（样本内）',
      noveltyNote: '逐篇计算：**新颖度 = 1 − 与更早文献的最大文本相似度**。样本内没有更早文献可比较时该格为空值（不是 0，也不是讨喜的 1.0），并排在表格最后。原型用词元 Jaccard 代理 TF-IDF 余弦，字段名、可空性与排序规则与契约一致，数值仅供界面演示。',
      nvTopic: '文献',
      nvLatest: '年份',
      nvPapers: '最相似的更早文献',
      nvScore: '新颖度',
      nvNoEarlier: '样本内无更早文献可比较',
      nvNull: '空值',

      covTitle: '覆盖平衡（样本内）',
      covNote: '覆盖平衡只在当前样本内比较，样本外情况未知。占比按样本内篇数计算，参考占比是均分基线，平衡度 = 1 − 极差。',
      covTopic: '主题',
      covShare: '占比',
      covTarget: '参考占比',
      covBalance: '平衡度',

      srcNote: '数据自带的说明：',
      footNote: '以上全部为样本内推断；要改变样本范围，请回到搜索页或引文扩展页，而不是在这张图上调参。',
    },

    en: {
      skip: 'Skip to main content',
      title: 'Evidence landscape',
      lead: 'The current sample as one picture: **citation edges** drive PageRank and communities, the three relation types stay in their own layers, and a relation we cannot compute is not proof that none exists.',
      toResults: '← Results',
      toExpand: 'Citation expansion',
      toQuestions: 'Research opportunities →',
      demoNote: 'Every number here is derived deterministically from the sample corpus — not a real bibliometric result.',

      sampleNote: 'Sample scope: every figure, ranking and table on this page describes only the {n} records of the current sample (sample_only={flag}). It is not a view of the field. Outside the sample nothing was observed: a missing relation only means this sample holds no such record.',

      limitsTitle: 'Known limits of this page',
      limitSample: 'Every analysis is confined to the current {n} records; nothing outside the sample was observed.',
      limitRelation: 'An unknown relation is not a missing relation: an edge this sample cannot compute only means no such record exists here.',
      limitComm: 'Communities are approximated by citation connected components; the v0.9.5 implementation uses Louvain, so group count and membership follow the sample.',
      limitNovelty: 'Novelty and coverage balance are relative quantities inside this sample; there is no external field baseline.',
      limitCite: 'References pointing outside the sample are not drawn: that is a normal fact, it only means those works were not retrieved into this project.',

      graphTitle: 'Citation network (in sample)',
      graphIntro: 'Nodes are grouped by **community** into sectors of one ring, and each node radius is one of three PageRank tiers. Positions come only from data order — no random numbers, identical after every reload. Click, or press Enter/Space, to open the detail drawer.',
      graphArrow: 'Arrow direction = source (citing paper) → target (cited paper), the same fields as in graph.links.',
      graphA11y: 'Keyboard path: Tab into the graph → Enter or Space opens the drawer → Esc closes it. The "centrality ranking" table below lists the same top 10 records.',
      graphScroll: 'Narrow screens: scroll this figure sideways to see the whole network — that is what keeps the nodes big enough to tap.',
      graphEmpty: 'This sample has no citation edges, so there is no citation network to draw; PageRank and communities are empty too. That is missing data, not "no citations exist".',
      statNodes: 'Nodes (records in sample)',
      statEdges: 'Citation edges (in sample)',
      statEdgesSub: '{ext} references point outside the sample and are not drawn',
      statGroups: 'Communities (component approx.)',
      statGroupsSub: 'the real implementation uses Louvain',
      statDangling: 'Dangling citation edges',
      statDanglingSub: 'both ends must be in the sample; 0 means the invariant holds',
      graphLegendGroups: 'Community colour',
      graphLegendRadius: 'Radius = PageRank tier (top 3 / top 8 / remainder)',
      groupLabel: 'Community {i} · {n} records',
      graphSelfNote: 'All {n} traceable citation edges in the sample come from resolved reference_ids.',
      nodeAria: '{title} · community {c} · PageRank {score} (press Enter to open the detail drawer)',

      citeOnlyTitle: 'Algorithms that run on citation edges only',
      citeOnlyBody: 'PageRank, community detection and the shortest evidence path use **only** citation edges (directed, source→target). The three relation types never enter those computations, even when their layers are displayed.',
      citeOnlyEdge: 'Citation edges (in sample)',
      citeOnlyPR: 'PageRank',
      citeOnlyPRValue: 'top 10, by descending score (ties broken by id)',
      citeOnlyComm: 'Community detection',
      citeOnlyCommValue: '{k} groups · connected-component approximation',
      citeOnlyPath: 'Shortest evidence path',
      citeOnlyPathValue: 'not rendered in the prototype (path() returns empty without a path)',
      citeOnlyUnknown: 'An unknown relation is not a missing relation: an uncomputed edge only means this sample holds no such record.',

      relTitle: 'Three relation types (one layer each)',
      relIntro: 'The three canonical names are fixed below and the counts come straight from relationCounts(). Turning a layer on overlays it on the citation network for position only — it does not make it part of the citation graph.',
      relBC: 'Bibliographic coupling: two records cite the same work (weight = shared references)',
      relCC: 'Co-citation: two records are cited by the same record in the sample (weight = citing records)',
      relTS: 'Text similarity: lexical cosine over title and abstract (not an embedding-based similarity)',
      relShow: 'Overlay on the graph',
      relHide: 'Remove from the graph',
      relDrawn: 'Only the {n} heaviest edges are drawn (this type has {all}).',
      relEmpty: 'This relation type has 0 edges in the current sample, so there is nothing to overlay.',
      relNote: 'Relation edges sit beside citation edges: **scores are never weighted or summed together**, and no combined "relation score" is produced. The legend lists only the three canonical names — no legacy alias, no sum row.',
      relUnknown: 'A pair without a relation edge only means this sample could not compute one; it is not proof that no relation exists.',

      centTitle: 'Centrality ranking (PageRank · citation edges only)',
      centNote: 'Top 10 inside the sample. PageRank reads citation structure only and is a different thing from the citation count; it is not comparable across samples.',
      colRank: '#',
      colPaper: 'Record',
      colScore: 'PageRank',
      colCites: 'Cited by',
      centOpen: 'Open the detail drawer for "{title}"',

      clusterTitle: 'Topic clusters (in sample)',
      clusterNote: 'Topic clusters are aggregated from topic labels in the sample and are **not the same partition** as the citation communities above: one is about topics, the other about citation connectivity, and neither explains the other.',
      clusterTerms: 'Terms',
      clusterSize: '{n} records',

      yearsTitle: 'Change over time (in sample)',
      yearsNote: 'Bar height is the record count in the sample; records without a year join no year. The shape only reflects what this search returned, not a trend in the field.',
      yearsUnknown: 'Unknown year: {n} records (merged into no year)',

      noveltyTitle: 'Novelty (in sample)',
      noveltyNote: 'Computed **per record**: novelty = 1 − max text similarity to strictly earlier records. When the sample holds no earlier record the cell is empty (not 0, and not a flattering 1.0) and the row sorts last. The prototype proxies TF-IDF cosine with word-token Jaccard: field names, nullability and sort order match the contract, the numbers are for interface demonstration only.',
      nvTopic: 'Paper',
      nvLatest: 'Year',
      nvPapers: 'Most similar earlier paper',
      nvScore: 'Novelty',
      nvNoEarlier: 'No earlier paper in the sample',
      nvNull: 'null',

      covTitle: 'Coverage balance (in sample)',
      covNote: 'Coverage balance is compared inside this sample only; nothing outside it was observed. Share is computed over sample records, the reference share is the even baseline, and balance = 1 − spread.',
      covTopic: 'Topic',
      covShare: 'Share',
      covTarget: 'Reference share',
      covBalance: 'Balance',

      srcNote: 'Note shipped with the data:',
      footNote: 'Everything above is an inference inside the sample. To change the sample, go back to Search or Citation expansion instead of tuning this figure.',
    },
  },

});
