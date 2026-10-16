// @vitest-environment jsdom
// 上传面板：逐文件状态、部分成功、按项重试。
import { beforeEach, describe, expect, it, vi } from 'vitest'
import UploadPanel from './UploadPanel.vue'
import { all, click, mountComponent, settle, text } from './test-harness.js'

const batch = {
  import_id: 'i1',
  job_id: 'j1',
  project_id: 'p1',
  revision: 3,
  items: [
    { item_id: 'i1', attachment_id: 'a1', display_name: 'matched.pdf', content_hash: 'h1', status: 'matched', paper_id: 'w1', match_candidates: [], match_score: 0.95, error_code: null, retryable: false },
    { item_id: 'i2', attachment_id: 'a2', display_name: 'confirm.pdf', content_hash: 'h2', status: 'needs_confirmation', paper_id: null, match_candidates: [{ candidate_id: 'c1', title: 'Near duplicate', doi: null, year: 2024, match_score: 0.82, source: 'openalex', conflicts: [] }], match_score: 0.82, error_code: null, retryable: false },
    { item_id: 'i3', attachment_id: 'a3', display_name: 'broken.pdf', content_hash: 'h3', status: 'failed', paper_id: null, match_candidates: [], match_score: null, error_code: 'parser_failed', retryable: true },
  ],
}

function fakeClient(overrides = {}) {
  return {
    getImport: vi.fn(async () => batch),
    importPapers: vi.fn(async () => batch),
    retryImport: vi.fn(async () => ({ ...batch, items: batch.items.map(item => (item.item_id === 'i3' ? { ...item, status: 'matched', retryable: false } : item)) })),
    resolveImport: vi.fn(async () => batch),
    ...overrides,
  }
}

beforeEach(() => { document.body.innerHTML = '' })

describe('UploadPanel', () => {
  it('renders the eight statuses from one batch and retries only retryable items', async () => {
    const client = fakeClient()
    const mounted = mountComponent(UploadPanel, { lang: 'zh', client, projectId: 'p1', expectedRevision: 3 })
    // 服务端批次可以直接恢复（刷新页面后继续展示逐项状态）。
    mounted.instance.setBatch(batch)
    await settle()
    expect(all('[data-v32-upload-item]')).toHaveLength(3)
    expect(text('[data-v32-upload-item="i1"] [data-upload-status-label]')).toBe('已识别')
    expect(text('[data-v32-upload-item="i2"] [data-upload-status-label]')).toBe('需要确认')
    expect(text('[data-v32-upload-item="i3"] [data-upload-status-label]')).toBe('等待重试')
    expect(document.querySelector('[data-v32-upload-item="i1"] [data-upload-retry]')).toBeNull()
    expect(document.querySelector('[data-v32-upload-item="i2"] [data-upload-retry]')).toBeNull()
    expect(document.querySelector('[data-v32-upload-item="i3"] [data-upload-retry]')).toBeTruthy()
    expect(text('[data-v32-upload-summary]')).toContain('可重试')
    // 需要确认的项提供「作为本地文献继续」与候选采用，matched 项没有。
    expect(document.querySelector('[data-v32-upload-item="i2"] [data-upload-confirm="local"]')).toBeTruthy()
    expect(document.querySelector('[data-v32-upload-item="i2"] [data-upload-confirm="match"]')).toBeTruthy()
    expect(document.querySelector('[data-v32-upload-item="i1"] [data-upload-confirm="local"]')).toBeNull()
    // 普通界面不暴露 parser 名称。
    expect(document.body.textContent).not.toMatch(/fitz|pymupdf|pypdf|PyMuPDF/i)

    click('[data-v32-upload-item="i3"] [data-upload-retry]')
    await settle(40)
    expect(client.retryImport).toHaveBeenCalledTimes(1)
    expect(client.retryImport.mock.calls[0][2]).toEqual({ expected_revision: 3, item_ids: ['i3'] })
    mounted.unmount()
  })

  it('uploads a selection and drives per-file status from the server batch', async () => {
    const client = fakeClient()
    const mounted = mountComponent(UploadPanel, { lang: 'zh', client, projectId: 'p1', expectedRevision: 3 })
    const file = new File([new Uint8Array([1, 2, 3])], 'x.pdf', { type: 'application/pdf' })
    const input = document.querySelector('[data-v32-upload-input]')
    Object.defineProperty(input, 'files', { value: [file], configurable: true })
    input.dispatchEvent(new Event('change', { bubbles: true }))
    await settle()
    click('[data-v32-upload-submit]')
    await settle(80)
    expect(client.importPapers).toHaveBeenCalledTimes(1)
    expect(client.importPapers.mock.calls[0][1]).toMatchObject({ expectedRevision: 3 })
    expect(all('[data-v32-upload-item]')).toHaveLength(3)
    mounted.unmount()
  })

  it('rejects an over-large selection before any request leaves the browser', async () => {
    const client = fakeClient()
    const mounted = mountComponent(UploadPanel, { lang: 'zh', client, projectId: 'p1', expectedRevision: 1, limits: { maxFiles: 10, maxFileBytes: 2 } })
    const input = document.querySelector('[data-v32-upload-input]')
    Object.defineProperty(input, 'files', { value: [new File([new Uint8Array(8)], 'big.pdf')], configurable: true })
    input.dispatchEvent(new Event('change', { bubbles: true }))
    await settle()
    click('[data-v32-upload-submit]')
    await settle()
    expect(client.importPapers).not.toHaveBeenCalled()
    expect(document.body.textContent).toContain('big.pdf')
    mounted.unmount()
  })
})
