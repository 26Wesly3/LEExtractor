// @vitest-environment jsdom
// S12：导出下拉的交互规范——默认收起、可键盘关闭、导出中给状态、成功后收起并留结果行。
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ExportMenu from './ExportMenu.vue'
import { V32Error } from './client.js'
import { all, click, mountComponent, settle, text } from './test-harness.js'

function fakeClient(exportProject) {
  return { exportProject }
}

let createObjectURL
let revokeObjectURL

beforeEach(() => {
  document.body.innerHTML = ''
  createObjectURL = vi.fn(() => 'blob:mock')
  revokeObjectURL = vi.fn()
  global.URL.createObjectURL = createObjectURL
  global.URL.revokeObjectURL = revokeObjectURL
})

describe('ExportMenu', () => {
  it('starts closed and opens from the summary', async () => {
    const mounted = mountComponent(ExportMenu, { client: fakeClient(vi.fn()), projectId: 'p1', lang: 'zh' })
    await settle()
    const details = document.querySelector('[data-v32-export]')
    expect(details.open).toBe(false)
    expect(all('[data-v32-export-format]')).toHaveLength(5)
    click('[data-v32-export-toggle]')
    await settle()
    expect(details.open).toBe(true)
    mounted.unmount()
  })

  it('closes on Escape and keeps focus recoverable', async () => {
    const mounted = mountComponent(ExportMenu, { client: fakeClient(vi.fn()), projectId: 'p1', lang: 'zh' })
    await settle()
    const details = document.querySelector('[data-v32-export]')
    click('[data-v32-export-toggle]')
    await settle()
    expect(details.open).toBe(true)
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await settle()
    expect(details.open).toBe(false)
    mounted.unmount()
  })

  it('shows the busy label while a format is exporting, then closes with a result line', async () => {
    let resolveExport
    const pending = new Promise(resolve => { resolveExport = resolve })
    const exportProject = vi.fn(() => pending)
    const mounted = mountComponent(ExportMenu, { client: fakeClient(exportProject), projectId: 'p1', lang: 'zh' })
    await settle()
    click('[data-v32-export-toggle]')
    await settle()

    click('[data-v32-export-format="ris"]')
    await settle()
    expect(text('[data-v32-export-format="ris"]')).toContain('导出中')
    expect(exportProject).toHaveBeenCalledWith('p1', 'ris')

    resolveExport({ blob: new Blob(['x']), filename: 'p1.ris' })
    await settle(20)
    expect(text('[data-v32-export-state]')).toContain('p1.ris')
    expect(document.querySelector('[data-v32-export]').open).toBe(false)
    expect(createObjectURL).toHaveBeenCalled()
    mounted.unmount()
  })

  it('reports a failure without closing anything', async () => {
    const exportProject = vi.fn(async () => {
      throw new V32Error({ code: 'not_found', message: 'HTTP 404', status: 404 })
    })
    const mounted = mountComponent(ExportMenu, { client: fakeClient(exportProject), projectId: 'p1', lang: 'zh' })
    await settle()
    click('[data-v32-export-toggle]')
    await settle()
    click('[data-v32-export-format="prisma"]')
    await settle(20)
    expect(text('[data-v32-export-error]')).toContain('HTTP 404')
    expect(text('[data-v32-export-state]')).toBe('')
    mounted.unmount()
  })
})
