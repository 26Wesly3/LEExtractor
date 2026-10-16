// @vitest-environment jsdom
// 根组件判定：只有 meta.v32 路由进入 v3.2 界面；其余路由仍渲染既有 App.vue。
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { isV32Route } from './routes.js'
import { createV32Routes } from './routes.js'
import { createMemoryHistory } from 'vue-router'
import { createV32Router } from '../main.js'

describe('AppRoot routing decision', () => {
  it('only routes explicitly marked meta.v32 enter the v3.2 workspace', () => {
    expect(isV32Route({ meta: { v32: true } })).toBe(true)
    expect(isV32Route({ meta: {} })).toBe(false)
    expect(isV32Route({ name: 'home' })).toBe(false)
    expect(isV32Route(null)).toBe(false)
    createV32Routes().forEach(route => expect(isV32Route(route)).toBe(true))
  })

  it('resolves unknown paths back to the legacy home page', async () => {
    const router = createV32Router(createMemoryHistory(), () => ({ top: 0 }))
    await router.push('/unknown/page')
    await router.isReady()
    expect(router.currentRoute.value.name).toBe('home')
    expect(isV32Route(router.currentRoute.value)).toBe(false)
  })

  it('keeps both views wired in the root component', () => {
    const source = readFileSync('src/v32/AppRoot.vue', 'utf8')
    expect(source).toContain('<Workspace')
    expect(source).toContain('<App v-else')
  })
})
