// 抽屉菜单的条目与说明（S2 从 App.vue 的 navModules()/navDesc() 原样搬出）。
//
// S3 会把条目换成新导航（首页 / 新研究 / 当前项目四个阶段 / 导出 / 全部项目 /
// 设置）。在那之前先用同一份文字，保证顶栏与菜单的行为在 S2 里完全没有变化。
export const PROJECT_TABS = [
  ['search', '检索', 'Search'],
  ['results', '文献结果', 'Results'],
  ['expand', '引文扩展', 'Expand'],
  ['landscape', '证据版图', 'Landscape'],
  ['questions', '候选问题', 'Questions'],
  ['review', '人工筛选', 'Review'],
  ['export', '导出与保存', 'Export'],
]

export function menuItems(t) {
  return [
    ['home', t('首页', 'Home')],
    ...PROJECT_TABS.map(([key, zh, en]) => [key, t(zh, en)]),
    ['projects', t('全部项目', 'All projects')],
  ]
}

export function menuDesc(t, key) {
  return ({
    home: t('封面与最近项目', 'Cover and recent projects'),
    search: t('描述方向并核对检索计划', 'Describe the focus and check the plan'),
    results: t('比较文献与发现依据', 'Compare records and their evidence'),
    expand: t('沿引文与参考文献继续扩展', 'Follow citations and references'),
    landscape: t('语料关系与分布', 'Relations, clusters and distribution'),
    questions: t('从语料整理的探索线索', 'Exploration leads from the corpus'),
    review: t('人工纳入与排除决定', 'Human include and exclude decisions'),
    export: t('导出文献、轨迹与会话', 'Export records, evidence and session'),
    projects: t('恢复或新建研究项目', 'Resume or create a research project'),
  })[key] || ''
}
