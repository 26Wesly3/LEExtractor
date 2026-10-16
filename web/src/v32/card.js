// Pure presentation logic for the v3.2 literature card (PaperCard.vue).
// Kept out of the component so the copy rules are unit-testable:
//   * at most 2 REAL recommendation reasons, taken from the backend's
//     recommendation_evidence — never generated locally;
//   * a role is shown only when a role and its origin exist;
//   * core_score lives in the detail view only and is never called 核心概率;
//   * an algorithm recommendation never carries the same label as a human
//     confirmation.

export const MAX_REASONS = 2

export const ROLE_LABELS = Object.freeze({
  foundational: { zh: '奠基工作', en: 'Foundational' },
  representative: { zh: '代表性工作', en: 'Representative' },
  review: { zh: '综述', en: 'Review' },
  methodology: { zh: '方法工作', en: 'Methodology' },
  frontier: { zh: '前沿工作', en: 'Frontier' },
  application: { zh: '应用工作', en: 'Application' },
  background: { zh: '背景文献', en: 'Background' },
  peripheral: { zh: '边缘文献', en: 'Peripheral' },
  unknown: { zh: '角色未定', en: 'Role unknown' },
})

export const ROLE_ORIGIN_LABELS = Object.freeze({
  metadata: { zh: '依据元数据', en: 'From metadata' },
  rule: { zh: '依据规则', en: 'From rules' },
  model_inference: { zh: '模型推断', en: 'Model inference' },
  unknown: { zh: '来源未标注', en: 'Origin unknown' },
})

export const JUDGEMENT_LABELS = Object.freeze({
  unjudged: { zh: '标记核心', en: 'Mark core', state: { zh: '未判断', en: 'Unjudged' } },
  core: { zh: '已标记核心', en: 'Marked core', state: { zh: '你已确认核心', en: 'You confirmed this as core' } },
  relevant: { zh: '已标记相关', en: 'Marked relevant', state: { zh: '你已标记相关', en: 'You marked this relevant' } },
  excluded: { zh: '已排除', en: 'Excluded', state: { zh: '你已排除', en: 'You excluded this record' } },
  read: { zh: '已读', en: 'Read', state: { zh: '你已标记为已读', en: 'You marked this as read' } },
})

/** The human-confirmation label is derived from the user's own judgement. */
export function judgementLabel(value, lang = 'zh') {
  const row = JUDGEMENT_LABELS[value] || JUDGEMENT_LABELS.unjudged
  return lang === 'zh' ? row.zh : row.en
}

export function judgementStateLabel(value, lang = 'zh') {
  if (!value || value === 'unjudged') return ''
  const row = JUDGEMENT_LABELS[value] || JUDGEMENT_LABELS.unjudged
  return lang === 'zh' ? row.state.zh : row.state.en
}

/**
 * The system recommendation label is deliberately different copy from the
 * human confirmation label above, so a recommendation cannot impersonate a
 * person's decision.
 */
export function systemEligibilityLabel(item, lang = 'zh') {
  const eligibility = item?.ranking?.eligibility
  if (eligibility !== 'recommended') return ''
  return lang === 'zh' ? '算法推荐核心' : 'Recommended by the ranking'
}

/** Up to two real reasons, with their evidence ids preserved. */
export function cardReasons(item, lang = 'zh') {
  const rows = Array.isArray(item?.ranking?.recommendation_evidence) ? item.ranking.recommendation_evidence : []
  return rows
    .filter(row => row && typeof row.text === 'string' && row.text.trim())
    .slice(0, MAX_REASONS)
    .map((row, index) => ({
      key: `${row.code || 'reason'}-${index}`,
      code: row.code || '',
      text: row.text.trim(),
      paperIds: Array.isArray(row.paper_ids) ? row.paper_ids : [],
      evidenceIds: Array.isArray(row.evidence_ids) ? row.evidence_ids : [],
    }))
}

/** Only a real role with a real origin is displayed. */
export function cardRole(item, lang = 'zh') {
  const role = item?.ranking?.core_role
  const origin = item?.ranking?.role_origin
  if (!role || role === 'unknown' || !origin || origin === 'unknown') return null
  const roleLabel = ROLE_LABELS[role] || { zh: role, en: role }
  const originLabel = ROLE_ORIGIN_LABELS[origin] || { zh: origin, en: origin }
  return {
    role,
    origin,
    text: lang === 'zh' ? roleLabel.zh : roleLabel.en,
    originText: lang === 'zh' ? originLabel.zh : originLabel.en,
  }
}

export function authorsText(paper) {
  const values = Array.isArray(paper?.authors) ? paper.authors : []
  return values.map(value => (typeof value === 'string' ? value : value?.name || '')).filter(Boolean).join(', ')
}

export function venueText(paper) {
  const records = Array.isArray(paper?.source_records) ? paper.source_records : []
  const first = records.find(row => row && row.provider)
  return first?.provider || ''
}

export function metaLine(item) {
  const paper = item?.paper || {}
  return [authorsText(paper), paper.year ?? '', venueText(paper)].filter(value => value !== '' && value !== null && value !== undefined).join(' · ')
}

/** Detail-view only. The label must never read 核心概率. */
export function coreScoreText(item, lang = 'zh') {
  const value = item?.ranking?.core_score
  const label = lang === 'zh' ? '综合排序分' : 'Ranking score'
  if (typeof value !== 'number' || !Number.isFinite(value)) return `${label}：证据不足`
  return `${label}：${value.toFixed(3)}`
}

export function snapshotLabel(snapshotId, lang = 'zh') {
  const value = snapshotId ? String(snapshotId) : ''
  if (!value) return lang === 'zh' ? '排名快照版本未提供' : 'No ranking snapshot version'
  const short = value.length > 12 ? `${value.slice(0, 8)}…` : value
  return lang === 'zh' ? `排名快照版本 ${short}` : `Ranking snapshot ${short}`
}
