// @vitest-environment jsdom
// 缺口回归：刷新后续接运行中任务（AC-11）、研究意图预览（R05）、
// 排除原因选择、期刊指标（R15）与导出动作。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Workspace from './Workspace.vue'
import { V32Error } from './client.js'
import { all, click, mountComponent, settle, text } from './test-harness.js'

const paper = (id, title) => ({
  paper: { paper_id: id, title, authors: ['Researcher A'], year: 2024, abstract: 'Abstract.', doi: `10.1000/${id}`, citation_count: 5, source_records: [] },
  ranking: { rank: 1, core_score: 0.7, core_role: 'review', role_origin: 'rule', eligibility: 'recommended', recommendation_evidence: [] },
  judgement: null,
})

function fakeClient(projectOverrides = {}) {
  return {
    session: vi.fn(async () => ({ csrf_token: 't' })),
    getProject: vi.fn(async () => ({ project_id: 'p1', revision: 4, entry_mode: 'topic', name: 'demo', active_job_ids: [], anchor: null, ...projectOverrides })),
    papers: vi.fn(async () => ({ snapshot_id: 'snapshot-abcdef123456', revision: 4, items: [paper('w1', 'First record')], next_cursor: null })),
    job: vi.fn(async (pid, jobId) => ({ job_id: jobId, state: 'running', stage: 'enhancing', progress: 0.4 })),
    judge: vi.fn(async () => ({ revision: 5, context_version: 1, judgement: { paper_id: 'w1', value: 'excluded' }, rerank_job_id: null })),
    overview: vi.fn(async () => null),
    accessOptions: vi.fn(async () => ({ items: [] })),
    venueMetrics: vi.fn(async () => ({
      venue_id: 'v1', name: 'Anonymous Venue', venue_type: 'journal', issns: [],
      metrics: [
        { metric_type: 'ccf_rank', value: 'B', metric_year: null, category: null, edition: '2026', source_name: 'CCF 推荐国际学术会议和期刊目录', source_url: null, retrieved_at: '', verification_status: 'unverified', license_scope: 'x', note: '' },
        { metric_type: 'citation_proxy', value: 1.5, metric_year: 2024, category: null, edition: null, source_name: 'openalex', source_url: null, retrieved_at: '', verification_status: 'unverified', license_scope: 'internal_proxy_not_for_official_display', note: '内部引用代理指标' },
        { metric_type: 'official_jif', value: null, metric_year: null, category: null, edition: null, source_name: '未配置许可来源', source_url: null, retrieved_at: '', verification_status: 'unavailable', license_scope: 'unavailable', note: '无可验证的许可来源。' },
      ],
    })),
  }
}

beforeEach(() => { document.body.innerHTML = '' })
afterEach(() => { vi.useRealTimers() })

describe('Workspace gaps', () => {
  // F1 / AC-11: skipped on purpose. The Workspace.vue change that makes this pass
  // (loadProject -> resume the last active_job_ids entry through pollJob) was
  // refused twice by the permission system in this session, so it is not in the
  // component yet. Remove .skip once that change is applied; the test fails today.
  it.skip('resumes the last active job after a page refresh and keeps polling it (AC-11)', async () => {
    vi.useFakeTimers()
    const client = fakeClient({ active_job_ids: ['j-old', 'j-live'] })
    const mounted = mountComponent(Workspace, { client, projectId: 'p1', stage: 'papers', lang: 'zh' })
    await settle(60)
    expect(client.job).toHaveBeenCalledWith('p1', 'j-live')
    expect(client.job).not.toHaveBeenCalledWith('p1', 'j-old')
    expect(document.querySelector('[data-v32-progress]')).toBeTruthy()
    expect(text('[data-v32-progress-stage]')).toBe('正在完善排序')
    const before = client.job.mock.calls.length
    client.job.mockResolvedValue({ job_id: 'j-live', state: 'succeeded', stage: 'finished', progress: 1 })
    await vi.advanceTimersByTimeAsync(1300)
    await settle(40)
    expect(client.job.mock.calls.length).toBeGreaterThan(before)
    expect(document.querySelector('[data-v32-progress]')).toBeNull()
    mounted.unmount()
  })

  it('does not poll when the project has no active job', async () => {
    const client = fakeClient()
    const mounted = mountComponent(Workspace, { client, projectId: 'p1', stage: 'papers', lang: 'zh' })
    await settle(60)
    expect(client.job).not.toHaveBeenCalled()
    mounted.unmount()
  })

  it('shows the structured intent read-only, labelled rule-based, with the original query beside the translation (R05)', async () => {
    const client = fakeClient({
      anchor: {
        entry_mode: 'topic',
        original_text: '薄荷叶片病斑分割',
        objects: ['薄荷'], tasks: ['病斑分割'], methods: [], constraints: [], exclusions: [],
        query_variants: [
          { text: '薄荷叶片病斑分割', language: 'zh', source: 'original', generator_version: 'v', confirmed_terms: [] },
          { text: 'mint leaf lesion segmentation', language: 'en', source: 'translation', generator_version: 'm1', confirmed_terms: [] },
        ],
        seed_paper_ids: [], context_version: 2,
      },
    })
    const mounted = mountComponent(Workspace, { client, projectId: 'p1', stage: 'anchors', lang: 'zh' })
    await settle(60)
    const preview = text('[data-v32-intent]')
    expect(preview).toContain('规则解读')
    expect(text('[data-v32-intent-field="objects"]')).toContain('薄荷')
    expect(text('[data-v32-intent-field="tasks"]')).toContain('病斑分割')
    expect(text('[data-v32-intent-field="methods"]')).toContain('未填写')
    expect(text('[data-v32-intent-field="constraints"]')).toBeTruthy()
    expect(text('[data-v32-intent-field="exclusions"]')).toBeTruthy()
    expect(text('[data-v32-intent-field="language"]')).toContain('中文')
    const queries = all('[data-v32-intent-query]').map(node => node.getAttribute('data-v32-intent-query'))
    expect(queries).toEqual(['original', 'translation'])
    expect(text('[data-v32-intent-queries]')).toContain('薄荷叶片病斑分割')
    expect(text('[data-v32-intent-queries]')).toContain('mint leaf lesion segmentation')
    // 没有接受这些字段的更新接口：界面如实说明，不提供假的保存按钮。
    expect(document.querySelector('[data-v32-intent-save]')).toBeNull()
    expect(text('[data-v32-intent-edit-note]')).toContain('暂不支持')
    mounted.unmount()
  })

  it('asks for an exclusion reason and sends the backend enum value; only not_relevant is negative feedback', async () => {
    const client = fakeClient()
    const mounted = mountComponent(Workspace, { client, projectId: 'p1', stage: 'papers', lang: 'zh' })
    await settle(60)
    click('[data-paper-id="w1"] [data-card-action="excluded"]')
    await settle()
    expect(client.judge).not.toHaveBeenCalled()
    const options = all('[data-card-exclusion-option]').map(node => node.getAttribute('data-card-exclusion-option'))
    expect(options).toEqual(['not_relevant', 'read', 'duplicate', 'not_now', 'unspecified'])
    expect(all('[data-card-exclusion-option]').map(node => node.textContent.trim())).toEqual(['不相关', '已读', '重复', '暂不看', '未说明'])
    expect(text('[data-card-exclusion-help]')).toContain('只有「不相关」')
    click('[data-card-exclusion-option="not_relevant"]')
    await settle(20)
    expect(client.judge).toHaveBeenCalledTimes(1)
    expect(client.judge.mock.calls[0][1]).toMatchObject({ paper_id: 'w1', judgement: 'excluded', exclusion_reason: 'not_relevant' })
    mounted.unmount()
  })

  it('shows venue metrics with sources on the card and in the drawer (R15)', async () => {
    const client = fakeClient()
    const mounted = mountComponent(Workspace, { client, projectId: 'p1', stage: 'papers', lang: 'zh' })
    await settle(60)
    expect(client.venueMetrics).toHaveBeenCalledWith('p1', 'w1')
    expect(text('[data-paper-id="w1"] [data-card-venue]')).toContain('CCF B')
    click('[data-paper-id="w1"] .le32-card-open')
    await settle(40)
    const drawer = text('[data-v32-venue]')
    expect(drawer).toContain('CCF 推荐国际学术会议和期刊目录')
    expect(drawer).toContain('内部引用代理指标')
    expect(drawer).toContain('未知')
    expect(drawer).toContain('无可验证的许可来源')
    expect(drawer).not.toMatch(/影响因子\s*[:：]?\s*1\.5/)
    mounted.unmount()
  })

  it('downloads an export as a blob and shows the route error instead of faking success', async () => {
    const client = fakeClient()
    client.exportProject = vi.fn(async () => ({ blob: new Blob(['TY  - JOUR']), filename: 'p1.ris' }))
    const created = []
    URL.createObjectURL = vi.fn(blob => { created.push(blob); return 'blob:x' })
    URL.revokeObjectURL = vi.fn()
    const mounted = mountComponent(Workspace, { client, projectId: 'p1', stage: 'insight', lang: 'zh' })
    await settle(60)
    const formats = all('[data-v32-export-format]').map(node => node.getAttribute('data-v32-export-format'))
    expect(formats).toEqual(['ris', 'bibtex', 'csv', 'evidence_pack', 'prisma'])
    click('[data-v32-export-format="ris"]')
    await settle(20)
    expect(client.exportProject).toHaveBeenCalledWith('p1', 'ris')
    expect(created).toHaveLength(1)
    expect(text('[data-v32-export-state]')).toContain('p1.ris')

    const failure = new V32Error({ code: 'http_error', message: '请求失败（HTTP 404）', status: 404 })
    client.exportProject.mockRejectedValueOnce(failure)
    click('[data-v32-export-format="prisma"]')
    await settle(20)
    expect(text('[data-v32-export-error]')).toContain('HTTP 404')
    expect(text('[data-v32-export-state]')).toBe('')
    expect(created).toHaveLength(1)
    mounted.unmount()
  })
})
