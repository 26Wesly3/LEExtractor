// 卡片展示逻辑：最多 2 条真实依据、角色只在有依据时展示、core_score 不叫核心概率，
// 人工确认与算法推荐使用不同标签。
import { describe, expect, it } from 'vitest'
import { cardReasons, cardRole, coreScoreText, judgementLabel, judgementStateLabel, MAX_REASONS, metaLine, snapshotLabel, systemEligibilityLabel } from './card.js'

const item = (overrides = {}) => ({
  paper: {
    paper_id: 'w1',
    title: 'A retrieved record',
    authors: ['Ada Lovelace', 'Alan Turing'],
    year: 2024,
    abstract: 'An abstract.',
    doi: '10.1000/x',
    citation_count: 12,
    source_records: [{ provider: 'openalex', external_id: 'W1', source_url: null, retrieved_at: '2026-10-17T00:00:00Z' }],
  },
  ranking: {
    rank: 1,
    core_score: 0.812,
    core_role: 'foundational',
    role_origin: 'rule',
    eligibility: 'recommended',
    recommendation_evidence: [
      { code: 'topic_match', text: '题名与摘要与研究方向的词法重合度为 0.71。', paper_ids: ['w1'], evidence_ids: ['e1'] },
      { code: 'seed_similarity', text: '与已确认种子向量的余弦相似度为 0.64。', paper_ids: ['w1'], evidence_ids: ['e2'] },
      { code: 'graph_support', text: '存在 3 条入边，图内百分位 0.90。', paper_ids: ['w1'], evidence_ids: ['e3'] },
    ],
  },
  judgement: null,
  ...overrides,
})

describe('literature card rules', () => {
  it('keeps at most two real reasons and never invents one', () => {
    expect(MAX_REASONS).toBe(2)
    const rows = cardReasons(item())
    expect(rows).toHaveLength(2)
    expect(rows[0].text).toBe('题名与摘要与研究方向的词法重合度为 0.71。')
    expect(rows[0].evidenceIds).toEqual(['e1'])
    expect(cardReasons({ ranking: { recommendation_evidence: [] } })).toEqual([])
    // A blank reason is not a reason.
    expect(cardReasons({ ranking: { recommendation_evidence: [{ code: 'x', text: '   ' }] } })).toEqual([])
  })

  it('shows a role only when both the role and its origin exist', () => {
    expect(cardRole(item())?.text).toBe('奠基工作')
    expect(cardRole(item({ ranking: { core_role: 'unknown', role_origin: 'rule' } }))).toBeNull()
    expect(cardRole(item({ ranking: { core_role: 'review', role_origin: 'unknown' } }))).toBeNull()
    expect(cardRole({})).toBeNull()
  })

  it('never calls core_score 核心概率 and keeps it for the detail view', () => {
    const label = coreScoreText(item())
    expect(label).toContain('综合排序分')
    expect(label).not.toContain('核心概率')
    expect(label).toContain('0.812')
    expect(coreScoreText({ ranking: { core_score: null } })).toContain('证据不足')
    expect(coreScoreText({})).not.toContain('核心概率')
  })

  it('labels an algorithm recommendation differently from a human confirmation', () => {
    const systemLabel = systemEligibilityLabel(item())
    expect(systemLabel).toBe('算法推荐核心')
    expect(systemLabel).not.toBe(judgementLabel('core'))
    expect(systemLabel).not.toBe(judgementStateLabel('core'))
    expect(systemEligibilityLabel(item({ ranking: { eligibility: 'exploration' } }))).toBe('')
  })

  it('drives the human label from the user judgement, including undo', () => {
    expect(judgementLabel('core')).toBe('已标记核心')
    expect(judgementLabel('relevant')).toBe('已标记相关')
    expect(judgementLabel('excluded')).toBe('已排除')
    expect(judgementLabel('unjudged')).toBe('标记核心')
    expect(judgementStateLabel('core')).toBe('你已确认核心')
    expect(judgementStateLabel('unjudged')).toBe('')
  })

  it('renders the meta line from real fields and the snapshot version from the backend', () => {
    expect(metaLine(item())).toBe('Ada Lovelace, Alan Turing · 2024 · openalex')
    expect(metaLine({ paper: { authors: [], year: null, source_records: [] } })).toBe('')
    expect(snapshotLabel('abcdef0123456789')).toContain('排名快照版本')
    expect(snapshotLabel('abcdef0123456789')).toContain('abcdef01')
    expect(snapshotLabel('')).toContain('未提供')
    expect(snapshotLabel('snap-1', 'en')).toContain('Ranking snapshot')
  })
})
