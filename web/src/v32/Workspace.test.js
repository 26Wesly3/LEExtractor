// @vitest-environment jsdom
// 工作区：四阶段、等待状态、三种空状态、独立校园资源访问动作。
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import Workspace from './Workspace.vue'
import { all, click, mountComponent, settle, text } from './test-harness.js'

const paper = (id, title, eligibility = 'recommended') => ({
  paper: {
    paper_id: id,
    title,
    authors: ['Researcher A'],
    year: 2024,
    abstract: 'Abstract text.',
    doi: `10.1000/${id}`,
    citation_count: 5,
    source_records: [{ provider: 'openalex', external_id: id, source_url: null, retrieved_at: '2026-10-17T00:00:00Z' }],
  },
  ranking: {
    rank: 1,
    core_score: 0.7,
    core_role: 'review',
    role_origin: 'rule',
    eligibility,
    recommendation_evidence: [{ code: 'topic_match', text: '题名与摘要的词法重合度为 0.6。', paper_ids: [id], evidence_ids: ['e1'] }],
  },
  judgement: null,
})

function fakeClient({ items = [paper('w1', 'First record'), paper('w2', 'Second record', 'exploration')], job = null, coverage = null, overview = null, accessItems = [] } = {}) {
  return {
    session: vi.fn(async () => ({ csrf_token: 't' })),
    getProject: vi.fn(async () => ({ project_id: 'p1', revision: 4, entry_mode: 'topic', name: 'demo' })),
    papers: vi.fn(async () => ({ snapshot_id: 'snapshot-abcdef123456', revision: 4, items, next_cursor: null })),
    discover: vi.fn(async () => ({ job_id: 'j1', state: 'queued' })),
    job: vi.fn(async () => job),
    expand: vi.fn(async () => ({ job_id: 'j2', state: 'queued' })),
    judge: vi.fn(async () => ({ revision: 5, context_version: 1, judgement: { paper_id: 'w1', value: 'core' }, rerank_job_id: null })),
    cancelJob: vi.fn(async () => ({ job_id: 'j1', state: 'cancelled' })),
    overview: vi.fn(async () => overview),
    accessOptions: vi.fn(async () => ({ items: accessItems })),
  }
}

const OVERVIEW = {
  snapshot_id: 'snapshot-abcdef123456',
  status: 'partial',
  statements: [
    { text: '当前检索集内共有 2 条文献。', type: 'fact', paper_ids: ['w1'], evidence_ids: ['e1'], scope: '当前检索集', limitations: [] },
    { text: '该方向可能仍有未检索的分支。', type: 'hypothesis', paper_ids: ['w1'], evidence_ids: ['e1'], scope: '当前检索集', limitations: ['样本有限'] },
  ],
  leads: [{
    claim_type: 'hypothesis',
    text: '把方法 A 用于任务 B 尚缺少直接证据。',
    paper_ids: ['w1'],
    nearest_work_ids: ['w2'],
    reason: '两条分支各自成立，但交叉组合未检索到直接工作。',
    coverage_scope: '当前检索集',
    next_queries: ['method A task B'],
    verification_status: 'needs_verification',
  }],
  coverage: { status: 'partial', candidate_count: 2, failed_sources: ['crossref'], stop_reasons: ['budget'], scope: '当前检索集' },
  branches: [{
    branch_id: 'b1',
    label: '检索排序',
    label_origin: 'text_statistics',
    paper_ids: ['w1'],
    representative_paper_ids: ['w1'],
    evidence_ids: ['e1'],
    method: 'c-TF-IDF',
    limitations: [],
  }],
  timeline: [{ year: 2024, count: 2, paper_ids: ['w1', 'w2'] }, { year: null, count: 1, paper_ids: ['w3'] }],
  trend_status: 'insufficient_data',
  trends: [],
  recommended_paper_ids: ['w1'],
}

beforeEach(() => { document.body.innerHTML = '' })

describe('Workspace', () => {
  it('opens the v3.2 import panel for a PDF-entry project and the direction form otherwise', async () => {
    const client = fakeClient()
    client.getProject = vi.fn(async () => ({ project_id: 'p1', revision: 4, entry_mode: 'pdf', name: 'demo' }))
    const mounted = mountComponent(Workspace, { client, projectId: 'p1', stage: 'anchors', lang: 'zh' })
    await settle(60)
    expect(document.querySelector('[data-v32-anchor-import]')).toBeTruthy()
    expect(document.querySelector('[data-v32-upload]')).toBeTruthy()
    expect(document.querySelector('[data-v32-research-text]')).toBeNull()
    mounted.unmount()

    const topic = mountComponent(Workspace, { client: fakeClient(), projectId: 'p1', stage: 'anchors', lang: 'zh' })
    await settle(60)
    expect(document.querySelector('[data-v32-research-text]')).toBeTruthy()
    expect(document.querySelector('[data-v32-anchor-import]')).toBeNull()
    topic.unmount()
  })

  it('opens the three credential areas from the settings view and never renders a plaintext key', async () => {
    const client = fakeClient()
    client.credentials = vi.fn(async () => ({ items: [{ provider_id: 'semantic_scholar', kind: 'retrieval', configured: true, updated_at: null }] }))
    client.saveCredential = vi.fn(async (providerId, body) => ({ provider_id: providerId, kind: body.kind, configured: true, updated_at: null }))
    client.deleteCredential = vi.fn(async () => null)
    const mounted = mountComponent(Workspace, { client, projectId: '', view: 'settings', lang: 'zh' })
    await settle(40)
    expect(all('[data-settings-area]')).toHaveLength(3)
    const secret = 'sk-never-echoed'
    document.querySelector('[data-settings-secret="semantic_scholar"]').value = secret
    document.querySelector('[data-settings-secret="semantic_scholar"]').dispatchEvent(new Event('input', { bubbles: true }))
    await settle()
    click('[data-settings-area="retrieval"] [data-settings-save]')
    await settle(40)
    expect(document.body.textContent).not.toContain(secret)
    mounted.unmount()
  })

  it('shows the two primary entries when no project is open', async () => {
    const mounted = mountComponent(Workspace, { client: fakeClient(), projectId: '', lang: 'zh' })
    await settle()
    expect(all('[data-v32-entry]')).toHaveLength(2)
    expect(text('[data-v32-entry="topic"] h2')).toBe('从研究方向开始')
    expect(text('[data-v32-entry="pdf"] h2')).toBe('从已有文献开始')
    mounted.unmount()
  })

  it('renders the stage it was routed to and the wait state of the fast result', async () => {
    // S4：阶段条在 shell/AppNav.vue（见 shell/AppNav.test.js），工作区自己按
    // props.stage 渲染，不再有内部 tab 与本地 activeStage。
    const mounted = mountComponent(Workspace, { client: fakeClient(), projectId: 'p1', stage: 'papers', lang: 'zh' })
    await settle(60)
    expect(all('[data-v32-stage]')).toHaveLength(0)
    expect(text('[data-v32-ranking-stage]')).toBe('排序已更新')
    expect(text('[data-v32-snapshot]')).toContain('排名快照版本')
    // 默认视图只展示推荐组，探索列表在独立视图中保留。
    expect(all('[data-paper-id]')).toHaveLength(1)
    click('[data-v32-view="exploration"]')
    await settle()
    expect(all('[data-paper-id]')).toHaveLength(1)
    click('[data-v32-view="all"]')
    await settle()
    expect(all('[data-paper-id]')).toHaveLength(2)
    mounted.unmount()
  })

  it('distinguishes the three empty states', async () => {
    const empty = mountComponent(Workspace, { client: fakeClient({ items: [] }), projectId: 'p1', stage: 'papers', lang: 'zh' })
    await settle(60)
    const emptyCopy = text('[data-v32-empty-copy]')
    expect(emptyCopy).toContain('没有取得候选文献')
    empty.unmount()

    const noRecommendation = mountComponent(Workspace, { client: fakeClient({ items: [paper('w1', 'Only exploration', 'exploration')] }), projectId: 'p1', stage: 'papers', lang: 'zh' })
    await settle(60)
    const noCopy = text('[data-v32-empty-copy]')
    expect(noCopy).toContain('证据不足')
    noRecommendation.unmount()

    const excludedItems = [{ ...paper('w1', 'Excluded record'), judgement: { value: 'excluded', exclusion_reason: 'unspecified' } }]
    const allExcluded = mountComponent(Workspace, { client: fakeClient({ items: excludedItems }), projectId: 'p1', stage: 'papers', lang: 'zh' })
    await settle(60)
    const excludedCopy = text('[data-v32-empty-copy]')
    expect(excludedCopy).toContain('全部被排除')
    expect(new Set([emptyCopy, noCopy, excludedCopy]).size).toBe(3)
    allExcluded.unmount()
  })

  it('collapses a partial source into one line plus 查看详情 and degrades a model failure', async () => {
    const client = fakeClient({ coverage: OVERVIEW.coverage, overview: OVERVIEW })
    const mounted = mountComponent(Workspace, { client, projectId: 'p1', stage: 'papers', lang: 'zh' })
    await settle(60)
    expect(text('[data-v32-coverage]')).toContain('部分来源暂不可用')
    expect(document.querySelector('[data-v32-coverage-detail]')).toBeNull()
    click('[data-v32-coverage-details]')
    await settle()
    expect(text('[data-v32-coverage-detail]')).toContain('crossref')
    mounted.unmount()
  })

  it('keeps a model failure on the basic ranking without a stack trace', async () => {
    const client = fakeClient({ job: { job_id: 'j1', state: 'failed', stage: 'enhancing', error_codes: ['model_unavailable'], coverage: null } })
    const mounted = mountComponent(Workspace, { client, projectId: 'p1', stage: 'papers', lang: 'zh' })
    await settle(60)
    mounted.instance.job = { job_id: 'j1', state: 'failed', stage: 'enhancing', error_codes: ['model_unavailable'], coverage: null }
    await settle(20)
    expect(text('[data-v32-degraded]')).toContain('已使用基础排序')
    expect(text('[data-v32-degraded]')).toContain('本次增强排序未完成')
    expect(document.body.textContent).not.toContain('Traceback')
    mounted.unmount()
  })

  it('keeps the open record in ?paper= and plays the exit before unmounting (S5)', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:projectId/:stage', name: 'workspace', component: { template: '<span />' } }],
    })
    await router.push('/projects/p1/papers')
    await router.isReady()
    const mounted = mountComponent(Workspace, { client: fakeClient(), projectId: 'p1', stage: 'papers', lang: 'zh' }, { router })
    await settle(60)
    click('[data-v32-view="all"]')
    await settle()
    click('[data-paper-id="w2"] .le32-card-open')
    await settle(20)

    // 详情进 URL：刷新、前进后退、分享链接都能回到同一条记录。
    expect(router.currentRoute.value.query.paper).toBe('w2')
    expect(document.querySelector('[data-v32-drawer]').classList.contains('is-open')).toBe(true)

    click('[data-v32-drawer-close]')
    await settle(20)
    expect(router.currentRoute.value.query.paper).toBeUndefined()
    const closing = document.querySelector('[data-v32-drawer]')
    expect(closing, 'the drawer must stay mounted for its 260ms exit').toBeTruthy()
    expect(closing.classList.contains('is-closing')).toBe(true)

    await new Promise(resolve => setTimeout(resolve, 300))
    await settle()
    expect(document.querySelector('[data-v32-drawer]')).toBeNull()
    mounted.unmount()
  })

  it('keeps the open drawer pinned by paper_id and renders the campus access entry points', async () => {
    const client = fakeClient({
      accessItems: [
        { provider: 'Elsevier', kind: 'fulltext_link', url: 'https://example.org/full', auth_required: false, institution: null, availability: 'public', verified: true, checked_at: '2026-10-17T00:00:00Z', source_url: null, granularity: 'article' },
        { provider: 'carsi', kind: 'institution_link', url: 'https://example.org/carsi', auth_required: true, institution: 'Zhejiang University', availability: 'entry_only', verified: false, checked_at: '2026-10-17T00:00:00Z', source_url: null, granularity: 'portal' },
        { provider: 'doi', kind: 'resolver', url: 'https://doi.org/10.1/x', auth_required: false, institution: null, availability: 'entry_only', verified: false, checked_at: '2026-10-17T00:00:00Z', source_url: null, granularity: 'database_guide' },
      ],
    })
    const mounted = mountComponent(Workspace, { client, projectId: 'p1', stage: 'papers', lang: 'zh' })
    await settle(60)
    click('[data-v32-view="all"]')
    await settle()
    click('[data-paper-id="w2"] .le32-card-open')
    await settle(40)
    expect(client.accessOptions).toHaveBeenCalledWith('p1', 'w2')
    expect(text('[data-v32-drawer] h3')).toBe('Second record')
    expect(text('[data-v32-access-note]')).toContain('不代表已获得授权')
    const kinds = all('[data-v32-access-option]').map(node => node.getAttribute('data-v32-access-option'))
    // 出版社原文 → DOI 解析 → 机构入口：顺序稳定，用户按序尝试。
    expect(kinds).toEqual(['fulltext_link', 'resolver', 'institution_link'])
    const accessText = text('[data-v32-access]')
    expect(accessText).toContain('出版社原文 · Elsevier')
    // 三种入口必须各说各的：DOI 解析不是「图书馆数据库导航」，CARSI 也不是全部机构入口的统称。
    expect(accessText).toContain('机构访问入口 · CARSI 联邦门户')
    // DOI 解析的 kind 与 provider 同名，只显示一次，不要「× · ×」。
    expect(accessText).toContain('DOI 官方解析')
    expect(accessText).not.toMatch(/DOI 官方解析 · DOI 官方解析/)
    expect(accessText).not.toContain('图书馆数据库导航')
    expect(accessText).not.toContain('通过学校访问 CARSI')
    // provider id 不能原样出现在界面上。
    expect(accessText).not.toContain('cars')
    expect(accessText).not.toContain(' · doi')
    // 可用性如实标注，不能把「仅提供入口」写成已授权。
    expect(accessText).toContain('公开获取')
    expect(accessText).toContain('仅提供检索入口')
    // 机构入口必须说明「不代你登录」。
    expect(accessText).toContain('本应用不代你登录')
    expect(document.querySelector('[data-v32-copy-citation]')).toBeTruthy()
    // 抽屉按 paper_id 保持：列表顺序变化后仍是同一条记录。
    expect(mounted.instance.pinned()).toBe('w2')
    client.papers.mockResolvedValueOnce({ snapshot_id: 'snapshot-abcdef123456', revision: 4, items: [paper('w2', 'Second record'), paper('w1', 'First record')], next_cursor: null })
    await mounted.instance.loadPapers({ keepAnchor: false })
    await settle(40)
    expect(text('[data-v32-drawer] h3')).toBe('Second record')
    mounted.unmount()
  })

  it('offers no campus sign-in affordance anywhere in the workspace', async () => {
    const mounted = mountComponent(Workspace, { client: fakeClient(), projectId: 'p1', stage: 'papers', lang: 'zh' })
    await settle(60)
    click('[data-paper-id="w1"] .le32-card-open')
    await settle(40)
    const body = document.body.textContent
    ;['浙大账号登录', '校园账号登录', '统一身份认证', 'Sign in', 'Log in', 'SSO'].forEach(word => {
      expect(body.toLowerCase()).not.toContain(word.toLowerCase())
    })
    expect(document.querySelectorAll('a[href*="cas"],a[href*="sso"],button[data-v32-cas]')).toHaveLength(0)
    mounted.unmount()
  })

  it('renders the research insight sections from the overview', async () => {
    const mounted = mountComponent(Workspace, { client: fakeClient({ overview: OVERVIEW }), projectId: 'p1', stage: 'insight', lang: 'zh' })
    await settle(60)
    expect(text('[data-v32-insight]')).toContain('研究范围')
    expect(text('[data-v32-insight]')).toContain('优先阅读')
    expect(all('[data-v32-priority] li')).toHaveLength(1)
    expect(text('[data-v32-statements]')).toContain('当前检索集内共有 2 条文献')
    expect(text('[data-v32-statements]')).toContain('待验证假设')
    expect(text('[data-v32-leads]')).toContain('支持论文')
    expect(text('[data-v32-leads]')).toContain('最邻近已有研究')
    expect(text('[data-v32-leads]')).toContain('理由')
    expect(text('[data-v32-leads]')).toContain('建议检索式')
    expect(text('[data-v32-leads]')).toContain('需要进一步核查')
    expect(text('[data-v32-insufficient]')).toContain('证据不足项')
    mounted.unmount()
  })

  it('shows the branch, timeline and active-direction sections on the threads stage', async () => {
    const mounted = mountComponent(Workspace, { client: fakeClient({ overview: OVERVIEW }), projectId: 'p1', stage: 'threads', lang: 'zh' })
    await settle(60)
    expect(text('[data-v32-branch="b1"]')).toContain('检索排序')
    expect(text('[data-v32-branch="b1"]')).toContain('文本统计')
    expect(text('[data-v32-branch="b1"]')).toContain('First record')
    expect(text('[data-v32-timeline]')).toContain('年份未知')
    expect(text('[data-v32-trends-empty]')).toContain('暂不足以判断趋势')
    mounted.unmount()
  })
})
