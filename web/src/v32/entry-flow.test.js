// @vitest-environment jsdom
// The v3.2 entry choice must CREATE A PROJECT and NAVIGATE.
//
// Regression: Workspace.vue renders EntryChoice on /v32 (a route with no
// projectId), and its handler used to be
//     async function openEntry(mode) { entryMode.value = mode; activeStage.value = 'anchors' }
// — it never called the API and never navigated, so both main entries were dead
// buttons: the user clicked 输入研究方向 and nothing happened at all. It also
// assigned the emitted `{ mode }` object into a ref that the template renders,
// so even the local state was wrong.
//
// These tests drive the real component with the real v3.2 client against a
// stubbed fetch, so "nothing happened" cannot come back unnoticed.
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it } from 'vitest'

import Workspace from './Workspace.vue'
import { createV32Routes } from './routes.js'
import { click, jsonResponse, mountComponent, sessionResponder, settle, stubFetch } from './test-harness.js'

const PROJECT_ID = 'a'.repeat(32)

function memoryRouter() {
  return createRouter({ history: createMemoryHistory(), routes: createV32Routes() })
}

async function mountEntries() {
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
  const router = memoryRouter()
  await router.push('/v32')
  await router.isReady()
  const mounted = mountComponent(Workspace, { projectId: '', stage: 'anchors', lang: 'zh' }, { router })
  await settle()
  return { api, router, ...mounted }
}

function calls(api) {
  return api.calls.map(call => `${call.method} ${call.url}`)
}

describe('v3.2 entry choice', () => {
  it('renders both main entries when the route has no project', async () => {
    const { unmount } = await mountEntries()
    expect(document.querySelector('[data-v32-entry-action="topic"]')).toBeTruthy()
    expect(document.querySelector('[data-v32-entry-action="pdf"]')).toBeTruthy()
    unmount()
  })

  it('opens a session, creates a topic project and routes to its 研究起点', async () => {
    const { api, router, unmount } = await mountEntries()

    click('[data-v32-entry-action="topic"]')
    await settle()

    // The regression: neither of these used to happen.
    expect(calls(api)).toContain('GET /api/v3/session')
    expect(calls(api)).toContain('POST /api/v3/projects')

    const created = api.calls.find(call => call.url === '/api/v3/projects')
    expect(created.body.entry_mode).toBe('topic')
    expect(created.headers['X-CSRF-Token']).toBe('csrf-token-abc')
    expect(String(created.headers['Idempotency-Key']).length).toBeGreaterThanOrEqual(16)

    expect(router.currentRoute.value.path).toBe(`/v32/projects/${PROJECT_ID}/anchors`)
    unmount()
  })

  it('creates a pdf-entry project from the second entry and routes to the same stage', async () => {
    const { api, router, unmount } = await mountEntries()

    click('[data-v32-entry-action="pdf"]')
    await settle()

    const created = api.calls.find(call => call.url === '/api/v3/projects')
    expect(created.body.entry_mode).toBe('pdf')
    expect(router.currentRoute.value.path).toBe(`/v32/projects/${PROJECT_ID}/anchors`)
    unmount()
  })

  it('shows one status line and does not navigate when project creation fails', async () => {
    const api = stubFetch()
    sessionResponder(api)
    api.on('POST', '/api/v3/projects', () => jsonResponse({
      error: { code: 'capability_unavailable', message: '服务暂不可用', retryable: true, details: {}, request_id: 'r1' },
    }, 503))
    const router = memoryRouter()
    await router.push('/v32')
    await router.isReady()
    const { unmount } = mountComponent(Workspace, { projectId: '', stage: 'anchors', lang: 'zh' }, { router })
    await settle()

    click('[data-v32-entry-action="topic"]')
    await settle()

    const alert = document.querySelector('[role="alert"]')
    expect(alert, 'a failed entry must surface one restful status line').toBeTruthy()
    expect(alert.textContent.trim().length).toBeGreaterThan(0)
    // The stack trace must never be rendered.
    expect(alert.textContent).not.toContain('V32Error')
    expect(alert.textContent).not.toContain('at ')
    // And the page stays on the entries rather than pretending to be in a project.
    expect(router.currentRoute.value.path).toBe('/v32')
    unmount()
  })
})
