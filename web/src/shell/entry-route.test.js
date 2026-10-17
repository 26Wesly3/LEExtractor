// @vitest-environment jsdom
// 回归：/search 的入口按钮必须真的建项目并跳阶段。
//
// 背景：S1/S2 之后 /search 由 <router-view/> 直接渲染组件。如果它挂的是
// EntryChoice，两个按钮只会 emit('select')，没有父组件接手，点了毫无反应。
// 这里按真实用户路径（AppShell + 真实路由表）点一次，断言请求与跳转都发生。
import { createApp, nextTick } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import AppShell from './AppShell.vue'
import { createAppRoutes } from '../router.js'
import { createWorkspaceTheme } from '../bootstrap.js'
import { jsonResponse, sessionResponder, stubFetch } from '../v32/test-harness.js'

const PROJECT_ID = 'a'.repeat(32)
let app = null
let host = null
let styleHost = null

function settle(times = 60) {
  return new Promise(async resolve => {
    for (let index = 0; index < times; index += 1) { await Promise.resolve(); await nextTick() }
    resolve()
  })
}

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }))
  styleHost = document.createElement('style')
  styleHost.textContent = readFileSync('src/styles.css', 'utf8')
  document.head.appendChild(styleHost)
})

afterEach(() => {
  app?.unmount()
  host?.remove()
  styleHost?.remove()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
})

async function mountAt(path) {
  const api = stubFetch()
  sessionResponder(api)
  api.on('POST', '/api/v3/projects', call => jsonResponse({
    project_id: PROJECT_ID,
    name: call.body?.name || '',
    entry_mode: call.body?.entry_mode || 'topic',
    revision: 1,
    context_version: 1,
    corpus_version: 1,
    current_snapshot_id: null,
    active_job_ids: [],
    created_at: '2026-10-17T00:00:00Z',
    updated_at: '2026-10-17T00:00:00Z',
    contract_version: '3.2',
    anchor: {},
  }, 201))
  const router = createRouter({ history: createMemoryHistory(), routes: createAppRoutes() })
  await router.push(path)
  await router.isReady()
  host = document.createElement('div')
  document.body.appendChild(host)
  app = createApp(AppShell)
  app.use(router)
  app.use(createWorkspaceTheme())
  app.mount(host)
  await settle()
  return { api, router }
}

describe('/search entry route', () => {
  it('renders the two entries and creates a project when one is clicked', async () => {
    const { api, router } = await mountAt('/search')
    expect(document.querySelector('[data-v32-entry-action="topic"]')).toBeTruthy()

    const button = document.querySelector('[data-v32-entry-action="topic"]')
    expect(button.closest('.le32-entries'), 'EntryChoice must be rendered by Workspace').toBeTruthy()
    button.click()
    await settle()

    const created = api.calls.find(call => call.url === '/api/v3/projects')
    expect(created, 'clicking 输入研究方向 must create a project').toBeTruthy()
    expect(created.body.entry_mode).toBe('topic')
    expect(router.currentRoute.value.path).toBe(`/projects/${PROJECT_ID}/anchors`)
  })
})
