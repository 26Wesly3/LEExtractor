// @vitest-environment jsdom
// S11：本机 Zotero 面板——连不上时给可操作提示；连上时能搜索、勾选、导入。
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ZoteroLocalPanel from './ZoteroLocalPanel.vue'
import { all, click, mountComponent, settle, setValue, text } from './test-harness.js'

function fakeClient(overrides = {}) {
  return {
    zoteroLocalStatus: vi.fn(async () => ({ running: true, library_name: '我的文库' })),
    zoteroLocalCollections: vi.fn(async () => ({ items: [{ key: 'C1', name: 'Mint' }] })),
    zoteroLocalItems: vi.fn(async () => ({
      items: [
        { item_key: 'ABC12345', title: 'Drought transcriptome', creators: ['Ada Lovelace'], year: 2021, venue: 'Plant Methods' },
        { item_key: 'DEF67890', title: 'Mint secondary metabolites', creators: ['Mint Lab'], year: 2019, venue: null },
      ],
    })),
    importZoteroLocal: vi.fn(async (pid, body) => ({ imported: body.item_keys.length, created: body.item_keys.length })),
    ...overrides,
  }
}

beforeEach(() => { document.body.innerHTML = '' })

describe('ZoteroLocalPanel', () => {
  it('explains how to enable the local API when Zotero is unreachable', async () => {
    const client = fakeClient({
      zoteroLocalStatus: vi.fn(async () => ({ running: false, message: '连不上本机 Zotero。' })),
    })
    const mounted = mountComponent(ZoteroLocalPanel, { client, projectId: 'p1', revision: 3, lang: 'zh' })
    await settle()
    expect(text('[data-v32-zotero-status]')).toBe('未连接')
    expect(text('[data-v32-zotero-hint]')).toContain('Zotero')
    expect(document.querySelector('[data-v32-zotero-items]')).toBeNull()
    mounted.unmount()
  })

  it('searches, selects and imports items into the project', async () => {
    const client = fakeClient()
    const events = []
    const mounted = mountComponent(ZoteroLocalPanel, { client, projectId: 'p1', revision: 3, lang: 'zh' })
    await settle(30)

    expect(text('[data-v32-zotero-status]')).toBe('已连接')
    expect(all('[data-v32-zotero-item]')).toHaveLength(2)
    expect(text('[data-v32-zotero-items]')).toContain('Drought transcriptome')

    // 按分类筛一次，并把关键词交给后端搜索
    setValue('[data-v32-zotero-query]', 'mint')
    click('[data-v32-zotero-search]')
    await settle(20)
    expect(client.zoteroLocalItems).toHaveBeenLastCalledWith({ q: 'mint', collection: '', limit: 30 })

    // 勾选第一条再导入
    const first = document.querySelector('[data-v32-zotero-item="ABC12345"]')
    first.checked = true
    first.dispatchEvent(new Event('change', { bubbles: true }))
    await settle()
    click('[data-v32-zotero-import]')
    await settle(20)

    expect(client.importZoteroLocal).toHaveBeenCalledTimes(1)
    const [projectId, body] = client.importZoteroLocal.mock.calls[0]
    expect(projectId).toBe('p1')
    expect(body).toEqual({ item_keys: ['ABC12345'], expected_revision: 3 })
    expect(text('[data-v32-zotero-message]')).toContain('已导入')
    mounted.unmount()
    expect(events).toEqual([])
  })
})
