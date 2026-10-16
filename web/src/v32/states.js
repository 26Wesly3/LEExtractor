// Wait, empty, error and recovery copy for the v3.2 workspace.
//
// Three empty states must stay distinct (docs/v3.2/05_UX_UI_SPEC.md, "上传与异常"):
// an empty candidate set, a corpus without any recommendation, and a corpus in
// which the user excluded everything. A model failure degrades to the basic
// ranking and never shows a stack trace. A partly unavailable source collapses
// into ONE status line plus 查看详情.

export const RESULT_STATES = Object.freeze({
  empty: 'empty_candidates',
  no_recommendation: 'no_recommendation',
  all_excluded: 'all_excluded',
})

export function resultState({ candidateCount = 0, recommendationCount = 0, excludedCount = 0 } = {}) {
  if (recommendationCount > 0) return 'ready'
  if (candidateCount <= 0) return RESULT_STATES.empty
  if (excludedCount >= candidateCount) return RESULT_STATES.all_excluded
  return RESULT_STATES.no_recommendation
}

export const EMPTY_STATE_COPY = Object.freeze({
  [RESULT_STATES.empty]: {
    zh: '本轮检索没有取得候选文献。来源可能没有返回结果，或检索词过窄；可以调整研究方向后重新发现。',
    en: 'This round retrieved no candidate records. Either the sources returned nothing or the query was too narrow; adjust the research direction and discover again.',
    action: { zh: '输入研究方向', en: 'Enter a research direction' },
  },
  [RESULT_STATES.no_recommendation]: {
    zh: '已取得候选文献，但当前证据不足以给出核心推荐。全部候选仍按探索列表保留，可继续发现或人工标记核心。',
    en: 'Candidates were retrieved, but the current evidence does not support a core recommendation. Every candidate remains in the exploration list; continue discovery or mark a core yourself.',
    action: { zh: '基于当前文献继续发现', en: 'Continue discovery from the current records' },
  },
  [RESULT_STATES.all_excluded]: {
    zh: '当前候选已全部被排除。排除只影响推荐与自动扩展，证据与判断都已保留；可以逐条恢复。',
    en: 'Every current candidate has been excluded. Exclusion only affects recommendation and automatic expansion; the records and judgements are preserved and can be restored one by one.',
    action: { zh: '恢复已排除文献', en: 'Restore excluded records' },
  },
})

export function emptyStateCopy(state, lang = 'zh') {
  const row = EMPTY_STATE_COPY[state] || EMPTY_STATE_COPY[RESULT_STATES.empty]
  return { text: lang === 'zh' ? row.zh : row.en, action: lang === 'zh' ? row.action.zh : row.action.en }
}

/** Wait states of the fast local ranking and the background enhancement. */
export const RANKING_STAGES = Object.freeze({
  idle: '',
  fast: { zh: '已获得初步结果', en: 'Preliminary results available' },
  enhancing: { zh: '正在完善排序', en: 'Refining the ranking' },
  refreshed: { zh: '排序已更新', en: 'Ranking updated' },
})

export function rankingStageLabel(stage, lang = 'zh') {
  const row = RANKING_STAGES[stage]
  if (!row) return ''
  return lang === 'zh' ? row.zh : row.en
}

/** Map a Job.stage/job state onto the visible wait state. */
export function rankingStageForJob(job, hasResults = false) {
  if (!job) return hasResults ? 'fast' : 'idle'
  const state = job.state
  if (state === 'queued' || state === 'running' || state === 'cancel_requested') {
    if (['enhancing', 'mapping', 'parsing', 'matching', 'retrieving'].includes(job.stage)) return 'enhancing'
    if (job.stage === 'fast_ranking') return hasResults ? 'fast' : 'enhancing'
    return hasResults ? 'fast' : 'enhancing'
  }
  if (state === 'succeeded' || state === 'partial') return hasResults ? 'refreshed' : 'idle'
  return hasResults ? 'refreshed' : 'idle'
}

/** One line + 查看详情 when only part of the sources answered. */
export function coverageLine(coverage, lang = 'zh') {
  if (!coverage) return null
  const failed = Array.isArray(coverage.failed_sources) ? coverage.failed_sources : []
  if (coverage.status === 'within_budget' && !failed.length) return null
  const detail = lang === 'zh'
    ? `范围：${coverage.scope || '当前检索集'}；候选 ${coverage.candidate_count ?? 0} 条；停止原因 ${(coverage.stop_reasons || []).join('、') || '未记录'}；失败来源 ${failed.join('、') || '无'}。`
    : `Scope: ${coverage.scope || 'current retrieved set'}; ${coverage.candidate_count ?? 0} candidates; stop reasons ${(coverage.stop_reasons || []).join(', ') || 'none recorded'}; failed sources ${failed.join(', ') || 'none'}.`
  if (coverage.status === 'no_results') return { status: 'no_results', text: lang === 'zh' ? '所选来源均正常返回，且确实没有匹配文献。' : 'Every selected source answered normally and returned no matching record.', detail }
  if (coverage.status === 'failed') return { status: 'failed', text: lang === 'zh' ? '可用来源未能完成检索，当前结果不代表检索穷尽。' : 'The available sources did not finish; the current result is not an exhaustive search.', detail }
  return { status: coverage.status || 'partial', text: lang === 'zh' ? '已获取部分文献，部分来源暂不可用。' : 'Part of the corpus was retrieved; some sources are temporarily unavailable.', detail }
}

/** A model/rerank failure keeps the local order and shows no stack trace. */
export function enhancementNotice(job, lang = 'zh') {
  if (!job) return null
  const codes = Array.isArray(job.error_codes) ? job.error_codes : []
  const failed = job.state === 'failed' || codes.length > 0
  if (!failed) return null
  return {
    text: lang === 'zh' ? '已使用基础排序' : 'Basic ranking in use',
    detail: lang === 'zh'
      ? '本次增强排序未完成，当前列表仍为本地排序结果；可以直接使用，也可以稍后重试。'
      : 'The enhancement did not finish. The list is the local ranking result; it remains usable and can be retried later.',
    codes,
  }
}

export function reasonTextForError(error, lang = 'zh') {
  const code = error?.code || ''
  if (code === 'revision_conflict') return lang === 'zh' ? '项目已被其他操作更新，请刷新后重试。' : 'The project changed elsewhere. Refresh and retry.'
  if (code === 'cursor_stale') return lang === 'zh' ? '分页游标已过期，列表已从第一页重新读取。' : 'The page cursor expired; the list was reloaded from the first page.'
  if (code === 'forbidden' || code === 'csrf_invalid') return lang === 'zh' ? '会话校验失败，请刷新页面后重试。' : 'The session check failed. Refresh the page and retry.'
  if (code === 'not_found') return lang === 'zh' ? '该项目或记录不存在，或不属于当前会话。' : 'The project or record does not exist, or does not belong to this session.'
  return lang === 'zh' ? '请求未完成，请稍后重试。' : 'The request did not complete. Try again shortly.'
}

/**
 * Viewport anchoring (docs/v3.2/05_UX_UI_SPEC.md: "保留用户当前阅读条目的视口锚点").
 * The anchor is a paper id, never a list index, so a re-ranking cannot scroll
 * the reader to a different record.
 */
export function anchorFrom(container, activeId = '') {
  if (!container || typeof container.querySelectorAll !== 'function') return { paperId: activeId || '', offset: 0 }
  const rows = [...container.querySelectorAll('[data-paper-id]')]
  const top = Number(container.getBoundingClientRect?.().top || 0)
  const anchor = rows.find(row => row.getBoundingClientRect().bottom > top + 1)
    || rows.find(row => row.getAttribute('data-paper-id') === activeId)
  if (!anchor) return { paperId: activeId || '', offset: 0 }
  return { paperId: anchor.getAttribute('data-paper-id') || activeId || '', offset: anchor.getBoundingClientRect().top - top }
}

export function restoreAnchor(container, anchor) {
  if (!container || !anchor?.paperId || typeof container.querySelectorAll !== 'function') return false
  const row = [...container.querySelectorAll('[data-paper-id]')].find(node => node.getAttribute('data-paper-id') === anchor.paperId)
  if (!row) return false
  const top = Number(container.getBoundingClientRect?.().top || 0)
  const delta = (row.getBoundingClientRect().top - top) - (Number(anchor.offset) || 0)
  if (typeof container.scrollBy === 'function') container.scrollBy({ top: delta, behavior: prefersReducedMotion() ? 'auto' : 'auto' })
  return true
}

export function prefersReducedMotion() {
  try {
    return typeof window !== 'undefined' && typeof window.matchMedia === 'function'
      && window.matchMedia('(prefers-reduced-motion: reduce)').matches === true
  } catch { return false }
}

/**
 * The open drawer stays pinned by paper_id: after a re-ranking the same record
 * is still the one on screen, or the drawer closes instead of showing a
 * different paper.
 */
export function resolvePinned(pinnedId, items) {
  const rows = Array.isArray(items) ? items : []
  if (!pinnedId) return null
  return rows.find(item => item?.paper?.paper_id === pinnedId) || null
}

export const STAGES = Object.freeze([
  { key: 'anchors', zh: '研究起点', en: 'Research anchors' },
  { key: 'papers', zh: '核心文献', en: 'Core literature' },
  { key: 'threads', zh: '研究脉络', en: 'Research threads' },
  { key: 'insight', zh: '研究洞察', en: 'Research insight' },
])

export function stageLabel(key, lang = 'zh') {
  const row = STAGES.find(stage => stage.key === key) || STAGES[0]
  return lang === 'zh' ? row.zh : row.en
}

/** Order of the three undo-capable actions; used by the card and its tests. */
export const JUDGEMENT_ACTIONS = Object.freeze([
  { value: 'core', zh: '标记核心', en: 'Mark core' },
  { value: 'relevant', zh: '相关', en: 'Relevant' },
  { value: 'excluded', zh: '排除', en: 'Exclude' },
])
