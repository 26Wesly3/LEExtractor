// @vitest-environment jsdom
// S1：一张路由表。旧路由名不删除而是重定向到新路径，所以每个旧书签都能落到
// 一个确定的阶段；/v32/* 也按同一张表重定向，e2e-check.mjs 继续可用。
import { describe, expect, it } from 'vitest'
import { createMemoryHistory } from 'vue-router'
import { createAppRouter } from '../main.js'
import { createAppRoutes, STAGES } from '../router.js'

const quiet = () => ({ top: 0 })
const PROJECT = 'd'.repeat(32)
const PAPER = 'a'.repeat(64)

async function resolve(path) {
  const router = createAppRouter(createMemoryHistory(), quiet)
  await router.push(path)
  await router.isReady()
  return router.currentRoute.value
}

describe('one route table', () => {
  it('keeps every legacy route name, as a redirect', () => {
    const routes = createAppRoutes()
    const byName = new Map(routes.filter(route => route.name).map(route => [route.name, route]))
    const legacy = ['home', 'search', 'projects', 'settings', 'project-search', 'project-results', 'project-expand', 'project-landscape', 'project-questions', 'project-review', 'project-export', 'paper']
    legacy.forEach(name => expect(byName.has(name), `missing route name ${name}`).toBe(true))
    // 旧页面本身不再渲染组件，只把地址交给新阶段。
    for (const name of ['project-search', 'project-results', 'project-expand', 'project-landscape', 'project-questions', 'project-review', 'project-export', 'paper']) {
      expect(byName.get(name).redirect, `${name} must redirect`).toBeTruthy()
      expect(byName.get(name).component, `${name} must not render a component`).toBeUndefined()
    }
  })

  it('has exactly the four v3.2 stages, in order', () => {
    expect(STAGES).toEqual(['anchors', 'papers', 'threads', 'insight'])
  })

  it('folds every legacy project page into its stage', async () => {
    const cases = [
      [`/projects/${PROJECT}/search`, 'anchors'],
      [`/projects/${PROJECT}/results`, 'papers'],
      [`/projects/${PROJECT}/review`, 'papers'],
      [`/projects/${PROJECT}/expand`, 'threads'],
      [`/projects/${PROJECT}/landscape`, 'threads'],
      [`/projects/${PROJECT}/questions`, 'insight'],
    ]
    for (const [path, stage] of cases) {
      const route = await resolve(path)
      expect(route.name, path).toBe('workspace')
      expect(route.params.stage, path).toBe(stage)
      expect(route.params.projectId, path).toBe(PROJECT)
      expect(route.meta.v32, path).toBe(true)
    }
  })

  it('carries the old export and paper intents as query parameters', async () => {
    const exported = await resolve(`/projects/${PROJECT}/export`)
    expect(exported.name).toBe('workspace')
    expect(exported.params.stage).toBe('papers')
    expect(exported.query.export).toBe('1')

    const paper = await resolve(`/projects/${PROJECT}/papers/${PAPER}`)
    expect(paper.name).toBe('workspace')
    expect(paper.params.stage).toBe('papers')
    expect(paper.query.paper).toBe(PAPER)
  })

  it('sends a bare project path to the first stage', async () => {
    const route = await resolve(`/projects/${PROJECT}`)
    expect(route.name).toBe('workspace')
    expect(route.params.stage).toBe('anchors')
  })

  it('keeps the v3.2 entry points as redirects so old bookmarks still work', async () => {
    expect((await resolve('/v32')).path).toBe('/search')
    expect((await resolve('/v32/settings')).path).toBe('/settings')
    const workspace = await resolve(`/v32/projects/${PROJECT}/papers`)
    expect(workspace.name).toBe('workspace')
    expect(workspace.params.stage).toBe('papers')
    const unknownStage = await resolve(`/v32/projects/${PROJECT}/bogus`)
    expect(unknownStage.params.stage).toBe('anchors')
    const bare = await resolve(`/v32/projects/${PROJECT}`)
    expect(bare.params.stage).toBe('anchors')
  })

  it('leaves unknown paths on the home page', async () => {
    const route = await resolve('/still-unknown')
    expect(route.name).toBe('home')
    expect(route.meta.v32).toBeFalsy()
  })

  it('hands the route parameters to the workspace as props (S2 regression)', () => {
    // S2 之后 Workspace 由 <router-view/> 直接渲染，不再有 AppRoot 替它传参；
    // 少了这条映射，工作区会因为 projectId 为空而一直停在入口页。
    const workspace = createAppRoutes().find(route => route.name === 'workspace')
    expect(typeof workspace.props).toBe('function')
    const props = workspace.props({ params: { projectId: 'p1', stage: 'papers' }, meta: { v32: true }, query: {} })
    expect(props).toMatchObject({ projectId: 'p1', stage: 'papers', view: '', lang: 'zh' })
    const english = workspace.props({ params: { projectId: 'p1', stage: 'papers' }, meta: {}, query: { lang: 'en' } })
    expect(english.lang).toBe('en')
  })
})
