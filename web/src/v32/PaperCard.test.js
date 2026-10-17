// @vitest-environment jsdom
// 文献卡片：最多 2 条依据、无「核心概率」、排除与标记核心标签不同。
import { beforeEach, describe, expect, it, vi } from 'vitest'
import PaperCard from './PaperCard.vue'
import { all, click, mountComponent, settle, text } from './test-harness.js'

const item = {
  paper: {
    paper_id: 'w1',
    title: 'Evidence retained from a provider',
    authors: ['Researcher A', 'Researcher B'],
    year: 2024,
    abstract: 'A complete abstract supplied by the backend.',
    doi: '10.1000/observed-record',
    citation_count: 12,
    source_records: [{ provider: 'openalex', external_id: 'W1', source_url: null, retrieved_at: '2026-10-17T00:00:00Z' }],
  },
  ranking: {
    rank: 3,
    core_score: 0.812,
    core_role: 'review',
    role_origin: 'rule',
    eligibility: 'recommended',
    recommendation_evidence: [
      { code: 'topic_match', text: '题名与摘要的词法重合度为 0.71。', paper_ids: ['w1'], evidence_ids: ['e1'] },
      { code: 'seed_similarity', text: '与已确认种子的余弦相似度为 0.64。', paper_ids: ['w1'], evidence_ids: ['e2'] },
      { code: 'graph_support', text: '存在 3 条入边。', paper_ids: ['w1'], evidence_ids: ['e3'] },
    ],
  },
  judgement: null,
}

let mounted
beforeEach(() => { document.body.innerHTML = '' })

describe('PaperCard', () => {
  it('renders the ordinal, the real meta line and at most two reasons', () => {
    mounted = mountComponent(PaperCard, { item, lang: 'zh', snapshotId: 'snapshot-0123456789' })
    expect(text('[data-card-rank]')).toBe('3')
    expect(text('[data-card-meta]')).toContain('Researcher A, Researcher B · 2024 · openalex')
    const reasons = all('[data-card-reasons] li')
    expect(reasons).toHaveLength(2)
    expect(reasons[0].textContent).toContain('词法重合度')
    expect(document.body.textContent).not.toContain('存在 3 条入边')
    expect(text('[data-card-role]')).toContain('综述')
    expect(text('[data-card-system-label]')).toBe('算法推荐核心')
    expect(document.querySelector('[data-card-human-label]')).toBeNull()
    mounted.unmount()
  })

  it('never renders the literal 核心概率 and shows the score only in the detail view', async () => {
    mounted = mountComponent(PaperCard, { item, lang: 'zh', snapshotId: 'snapshot-1' })
    expect(document.body.textContent).not.toContain('核心概率')
    expect(document.querySelector('[data-card-detail]')).toBeNull()
    click('.le32-card-detail-toggle')
    await settle()
    expect(document.body.textContent).not.toContain('核心概率')
    expect(text('[data-card-score]')).toContain('综合排序分')
    expect(text('[data-card-score]')).toContain('0.812')
    mounted.unmount()
  })

  it('labels 排除 and 标记核心 differently and reports the user judgement', async () => {
    const seen = []
    mounted = mountComponent(PaperCard, { item, lang: 'zh', onJudge: payload => seen.push(payload) })
    expect(text('[data-card-action="core"]')).toBe('标记核心')
    expect(text('[data-card-action="excluded"]')).toBe('排除')
    click('[data-card-action="excluded"]')
    await settle()
    // 排除先选原因；默认「未说明」不进入负反馈。
    expect(seen).toEqual([])
    click('[data-card-exclusion-option="unspecified"]')
    await settle()
    expect(seen).toEqual([{ paperId: 'w1', value: 'excluded', exclusionReason: 'unspecified' }])
    mounted.unmount()

    // Escape 或「取消」关闭原因选择且不发送。
    const cancelled = []
    mounted = mountComponent(PaperCard, { item, lang: 'zh', onJudge: payload => cancelled.push(payload) })
    click('[data-card-action="excluded"]')
    await settle()
    click('[data-card-exclusion-cancel]')
    await settle()
    expect(document.querySelector('[data-card-exclusion]')).toBeNull()
    expect(cancelled).toEqual([])
    mounted.unmount()

    // After the backend reports the judgement, the human label replaces the action label.
    const judged = { ...item, judgement: { value: 'excluded', exclusion_reason: 'unspecified' } }
    mounted = mountComponent(PaperCard, { item: judged, lang: 'zh' })
    expect(text('[data-card-action="excluded"]')).toBe('已排除')
    expect(text('[data-card-human-label]')).toContain('你已排除')
    // 人工确认与算法推荐同时可见，但标签不同：系统推荐不会冒充人工确认。
    expect(text('[data-card-system-label]')).toBe('算法推荐核心')
    expect(text('[data-card-human-label]')).not.toBe(text('[data-card-system-label]'))
    click('[data-card-action="excluded"]')
    await settle()
    mounted.unmount()

    // Undo is offered and sends unjudged.
    const seenUndo = []
    mounted = mountComponent(PaperCard, { item: judged, lang: 'zh', onJudge: payload => seenUndo.push(payload) })
    click('[data-card-action="undo"]')
    await settle()
    expect(seenUndo).toEqual([{ paperId: 'w1', value: 'unjudged' }])
    mounted.unmount()
  })

  it('hides the role when there is no evidence and disables the actions while busy', () => {
    const bare = { ...item, ranking: { ...item.ranking, core_role: 'unknown', role_origin: 'unknown', recommendation_evidence: [] } }
    mounted = mountComponent(PaperCard, { item: bare, lang: 'zh', busy: true })
    expect(document.querySelector('[data-card-role]')).toBeNull()
    expect(text('[data-card-no-reason]')).toContain('没有可展示的推荐依据')
    all('[data-card-action]').forEach(node => expect(node.disabled).toBe(true))
    mounted.unmount()
  })

  it('shows sourced venue badges and the unknown official metrics in the detail view', async () => {
    const venue = { metrics: [
      { metric_type: 'ccf_rank', value: 'A', source_name: 'CCF 推荐国际学术会议和期刊目录', verification_status: 'unverified', note: '' },
      { metric_type: 'official_jif', value: null, source_name: '未配置许可来源', verification_status: 'unavailable', note: '无可验证的许可来源。' },
    ] }
    mounted = mountComponent(PaperCard, { item, lang: 'zh', venue })
    expect(text('[data-card-venue]')).toContain('CCF A')
    click('.le32-card-detail-toggle')
    await settle()
    expect(text('[data-card-venue-detail]')).toContain('未知')
    expect(text('[data-card-venue-detail]')).toContain('无可验证的许可来源')
    mounted.unmount()
  })
})
