// @vitest-environment jsdom
// S8：项目页同时显示 v3 项目（可打开）与旧版项目（只读 + RIS 导出）。
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ProjectList from './ProjectList.vue'
import { click, mountComponent, settle, stubFetch } from '../v32/test-harness.js'

// api.js 的 request() 读 response.json()，而 test-harness 的 jsonResponse 只给
// text()（v3.2 客户端用 text()）。这里给项目页一个同时具备两者的响应对象。
function apiResponse(data, status = 200) {
  return { ok: status < 400, status, statusText: status < 400 ? 'OK' : 'Error', json: async () => data, text: async () => JSON.stringify(data) }
}

const Page = { template: '<span />' }
const router = () => createRouter({
  history: createMemoryHistory(),
  routes: [
    { path: '/projects', name: 'projects', component: Page },
    { path: '/search', name: 'search', component: Page },
    { path: '/projects/:projectId/:stage', name: 'workspace', component: Page },
  ],
})

let mounted = null

afterEach(() => {
  mounted?.unmount()
  mounted = null
  document.body.innerHTML = ''
})

async function mountWith({ modern = [], legacy = [] } = {}) {
  const api = stubFetch()
  api.on('GET', '/api/v3/session', () => apiResponse({ csrf_token: 'csrf-1', authenticated: false }))
  api.on('GET', '/api/v3/projects', () => apiResponse({ items: modern, total: modern.length }))
  api.on('GET', '/api/projects', () => apiResponse({ items: legacy, total: legacy.length }))
  const only = router()
  await only.push('/projects')
  await only.isReady()
  mounted = mountComponent(ProjectList, {}, { router: only })
  await settle(40)
  return { api, router: only }
}

describe('ProjectList', () => {
  it('lists workspace projects as openable links and legacy ones as read-only', async () => {
    await mountWith({
      modern: [{ project_id: 'p-v3', name: 'New direction', entry_mode: 'topic', updated_at: '2026-10-17T00:00:00Z' }],
      legacy: [{ project_id: 'p-old', name: 'Old work', topic: 'legacy topic', updated_at: '2026-10-01T00:00:00Z' }],
    })
    const modern = document.querySelector('[data-v3-project="p-v3"]')
    expect(modern).toBeTruthy()
    expect(modern.querySelector('a').getAttribute('href')).toBe('/projects/p-v3/anchors')

    const legacy = document.querySelector('[data-legacy-project="p-old"]')
    expect(legacy).toBeTruthy()
    expect(legacy.textContent).toContain('旧版·只读')
    expect(legacy.querySelector('a'), 'legacy projects must not be openable').toBeNull()
    expect(modern.textContent).toContain('可打开')
  })

  it('exports a legacy project as RIS through the old endpoint', async () => {
    const { api } = await mountWith({ legacy: [{ project_id: 'p-old', name: 'Old work', revision: 4 }] })
    let downloaded = null
    globalThis.URL.createObjectURL = vi.fn(() => 'blob:demo')
    globalThis.URL.revokeObjectURL = vi.fn()
    api.on('POST', '/api/projects/p-old/exports', call => {
      downloaded = call.body
      return { ok: true, status: 200, blob: async () => new Blob(['TY  - JOUR'], { type: 'application/x-research-info-systems' }) }
    })

    click('[data-legacy-export="p-old"]')
    await settle(40)

    expect(downloaded).toMatchObject({ format: 'ris', scope: 'corpus', expected_revision: 4 })
    expect(document.querySelector('[role="alert"]')).toBeNull()
  })

  it('says so honestly when the legacy list cannot be read', async () => {
    const api = stubFetch()
    api.on('GET', '/api/v3/session', () => apiResponse({ csrf_token: 'c' }))
    api.on('GET', '/api/v3/projects', () => apiResponse({ items: [] }))
    api.on('GET', '/api/projects', () => apiResponse({ error: { code: 'internal', message: '仓库不可读', retryable: true } }, 500))
    const only = router()
    await only.push('/projects')
    await only.isReady()
    mounted = mountComponent(ProjectList, {}, { router: only })
    await settle(40)
    expect(document.querySelector('[role="alert"]').textContent).toContain('仓库不可读')
  })
})
