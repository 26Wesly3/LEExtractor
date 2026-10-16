// @vitest-environment jsdom
// 附加路由：新增 /v32 路由，既有路由的名称与路径完全不变。
import { describe, expect, it } from 'vitest'
import { createMemoryHistory } from 'vue-router'
import { createV32Router } from '../main.js'
import { createV32Routes } from './routes.js'
import { createWorkspaceRouter } from '../bootstrap.js'

const quiet = () => ({ top: 0 })

const LEGACY_NAMES = ['home', 'search', 'projects', 'settings', 'project-search', 'project-results', 'project-expand', 'project-landscape', 'project-questions', 'project-review', 'project-export', 'paper']

describe('v3.2 routes stay additive', () => {
  it('keeps every legacy route exactly as shipped', () => {
    const legacy = createWorkspaceRouter(createMemoryHistory()).options.routes
    const names = legacy.map(route => route.name).filter(Boolean)
    LEGACY_NAMES.forEach(name => expect(names).toContain(name))
    expect(legacy.at(-1).path).toBe('/:pathMatch(.*)*')
    expect(legacy.at(-1).redirect).toBe('/')
  })

  it('resolves the new workspace paths and leaves unknown paths on home', async () => {
    const router = createV32Router(createMemoryHistory(), quiet)
    const names = router.options.routes.map(route => route.name).filter(Boolean)
    LEGACY_NAMES.forEach(name => expect(names).toContain(name))
    expect(createV32Routes().map(route => route.path)).toEqual(['/v32', '/v32/settings', '/v32/projects/:projectId/:stage', '/v32/projects/:projectId'])

    await router.push('/v32')
    await router.isReady()
    expect(router.currentRoute.value.name).toBe('v32-entries')
    expect(router.currentRoute.value.meta.v32).toBe(true)

    await router.push('/v32/projects/p1/insight')
    expect(router.currentRoute.value.name).toBe('v32-workspace')
    expect(router.currentRoute.value.params).toMatchObject({ projectId: 'p1', stage: 'insight' })

    await router.push('/v32/settings')
    expect(router.currentRoute.value.meta.v32View).toBe('settings')

    // 既有路由仍然按原样解析。
    await router.push('/projects/ddd/results')
    expect(router.currentRoute.value.name).toBe('project-results')
    await router.push('/')
    expect(router.currentRoute.value.name).toBe('home')
    await router.push('/still-unknown')
    expect(router.currentRoute.value.name).toBe('home')
  })
})
