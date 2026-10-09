/* ============================================================================
   LEExtractor — 研究机会页文案（questions）
   ----------------------------------------------------------------------------
   与 i18n-pages.js 同构：只做一次 Object.assign(window.LE_PAGES, ...)，
   不碰共享外壳字典，也不改动 i18n-pages.js。

   用法（页面脚本里）：
     const s = (k, v) => LE.pt('questions', k, v);
     s('title')

   文案原则（本轮界面优化）：
     · 页面只回答三件事：有哪些候选问题、凭什么、下一步做什么。
     · 「样本内观察 ≠ 已证实的研究空白」只在页头说一次，卡片与页尾不再重复。
     · 判定门槛、字段名、计数口径这类实现细节收进「计算口径与字段名」折叠区，
       默认不出现；界面第一层只出现易懂的说法。
     · 内部枚举一律转白话：status=hypothesis → 候选（待验证）；
       kind=combination_gap → 组合缺口；evidence_strength=moderate → 中等。
       原始字段名只在折叠区里出现，供排查问题用。
   ========================================================================== */

'use strict';

window.LE_PAGES = window.LE_PAGES || {};

Object.assign(window.LE_PAGES, {

  questions: {
    zh: {
      skip: '跳到主内容',
      title: '研究机会',
      lead: '从当前样本里挑出**待验证的候选研究问题**：每条写清它为什么算一个缺口、缺口有多大、哪几篇文献撑着它。',
      toSearch: '继续检索 →',
      toExpand: '引文扩展 →',
      toResults: '← 文献结果',

      sampleNote: '以下候选都从当前样本的 {n} 篇文献算出。**样本里没看到，不等于没人做过** —— 这些是候选，不是已证实的研究空白。',

      statTitle: '候选问题',
      statAll: '合计',
      statCombination: '组合缺口',
      statCoverage: '覆盖缺口',
      statUnfollowed: '结果未被跟进',
      statCandidate: '均为候选',

      kindCombinationShort: '两个主题各自有支撑，但几乎没有一起出现过',
      kindCoverageShort: '某个主题的占比明显低于均衡水平',
      kindUnfollowedShort: '近年、较新颖，但样本内还没有人引用',

      cardKind: '类型',
      cardStatus: '状态',
      statusCandidate: '候选（待验证）',
      cardStrength: '证据强度',
      strengthModerate: '中等',
      strengthWeak: '较弱',
      cardWhy: '为什么算一个缺口',
      cardCoverageGap: '缺口有多大',
      cardNextStep: '下一步',
      cardSupport: '支撑文献（样本内 {n} 篇）',
      cardSupportEmpty: '当前样本里没有可列出的支撑文献。',
      cardOpen: '打开「{title}」的详情',

      methodTitle: '计算口径与字段名',
      methodIntro: '下面是这些候选问题怎么算出来的，以及界面上白话对应的原始字段。排查问题时才需要看。',
      methodThresholds: '判定门槛',
      methodThresholdCombination: '组合缺口：两个术语各自支撑 ≥ 3 篇，且两者共现 ≤ 1 篇。',
      methodThresholdCoverage: '覆盖缺口：覆盖分析中占比低于均衡水平一半的主题，最多 2 条。',
      methodThresholdUnfollowed: '结果未被跟进：新颖度 ≥ 0.75、年份不早于最近年份 − 1、且样本内被引 0 次。',
      methodStrength: '证据强度只有「中等」「较弱」两级。排序先按类型，同类型内中等的排在前面。',
      methodFields: '字段名：kind（combination_gap / coverage_gap / unfollowed_result）、status（hypothesis）、evidence_strength（moderate / weak）。另有 risks、needs_verification、computation_basis、nearest_existing_work 尚未渲染。',
      methodCounts: '计数由当前样本的候选问题派生。真实实现截断到问题上限后统计，未出现的类型不补零；本页固定列出三种，便于逐类对照。',

      entryTitle: '下一步',
      entrySearchTitle: '把样本做大',
      entrySearchHint: '回到检索页调整检索式与年份范围后重新检索。',
      entryExpandTitle: '检验「没人跟进」',
      entryExpandHint: '以本页支撑文献为种子做引文扩展；扩展的停止原因如实标注。',

      empty: '当前样本没有产出候选问题。样本太小或术语支撑不足时会直接返回空列表，而不是硬凑一条。',
    },

    en: {
      skip: 'Skip to main content',
      title: 'Research opportunities',
      lead: 'Candidate research questions drawn from the current sample: each one states why it counts as a gap, how wide the gap is, and which records support it.',
      toSearch: 'Run another search →',
      toExpand: 'Citation expansion →',
      toResults: '← Results',

      sampleNote: 'Every candidate below comes from the {n} records of the current sample. **Not seeing it here does not mean nobody has done it** — these are candidates, not proven research gaps.',

      statTitle: 'Candidate questions',
      statAll: 'Total',
      statCombination: 'Combination gaps',
      statCoverage: 'Coverage gaps',
      statUnfollowed: 'Unfollowed results',
      statCandidate: 'all candidates',

      kindCombinationShort: 'Two topics are each supported, yet almost never appear together',
      kindCoverageShort: 'A topic whose share sits well below an even baseline',
      kindUnfollowedShort: 'Recent and novel, but nothing in the sample cites it yet',

      cardKind: 'Kind',
      cardStatus: 'Status',
      statusCandidate: 'Candidate (to verify)',
      cardStrength: 'Evidence strength',
      strengthModerate: 'Moderate',
      strengthWeak: 'Weak',
      cardWhy: 'Why this counts as a gap',
      cardCoverageGap: 'How wide the gap is',
      cardNextStep: 'Next step',
      cardSupport: 'Supporting records ({n} in sample)',
      cardSupportEmpty: 'No supporting record can be listed inside the current sample.',
      cardOpen: 'Open details for "{title}"',

      methodTitle: 'How these are computed, and the field names',
      methodIntro: 'Below is how the candidates are derived and which raw field each plain-language label maps to. You only need this when debugging.',
      methodThresholds: 'Thresholds',
      methodThresholdCombination: 'Combination gap: two terms each supporting ≥ 3 records and co-occurring in ≤ 1 record.',
      methodThresholdCoverage: 'Coverage gap: topics whose share falls below half the even baseline, at most 2 rows.',
      methodThresholdUnfollowed: 'Unfollowed result: novelty ≥ 0.75, year no earlier than the latest year − 1, and 0 citations inside the sample.',
      methodStrength: 'Evidence strength has exactly two levels, moderate and weak. Ordering is by kind first, then moderate before weak inside a kind.',
      methodFields: 'Field names: kind (combination_gap / coverage_gap / unfollowed_result), status (hypothesis), evidence_strength (moderate / weak). risks, needs_verification, computation_basis and nearest_existing_work are not rendered yet.',
      methodCounts: 'Counts derive from the candidates of the current sample. The real implementation counts after truncating to the question cap, so a kind that never occurs is not padded with a zero; this page always lists the three kinds so they can be compared.',

      entryTitle: 'Next',
      entrySearchTitle: 'Grow the sample',
      entrySearchHint: 'Go back to Search, adjust the strings and year range, and search again.',
      entryExpandTitle: 'Test the "no follow-up" claim',
      entryExpandHint: 'Expand citations from the supporting records on this page; the stop reason is shown as-is.',

      empty: 'The current sample produced no candidate questions. With too few records or too little term support the real implementation returns an empty list instead of forcing one.',
    },
  },

});
