// @vitest-environment jsdom
// S3：顶栏与抽屉菜单（A5–A7、A12）。
//
// 这里接管原来在 App.dom.test.js 里的「vertical module menu」用例，并补上计划
// 第 5 节要求的键盘与焦点行为：焦点进面板、Tab 在面板内循环、关闭后回「导航」，
// 以及新导航条目（首页 / 新研究 / 四个阶段 / 导出 / 全部项目 / 设置）。
import { createApp, nextTick } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import AppShell from './AppShell.vue'
import { createWorkspaceTheme } from '../bootstrap.js'

const PROJECT = 'd'.repeat(32)
const Page = { template: '<span />' }

const routes = () => [
  { path: '/', name: 'home', component: Page },
  { path: '/search', name: 'search', component: Page },
  { path: '/projects', name: 'projects', component: Page },
  { path: '/settings', name: 'settings', component: Page },
  { path: '/projects/:projectId/:stage(anchors|papers|threads|insight)', name: 'workspace', component: Page },
]

let app
let host
let router
let styleHost

async function settle(times = 40) {
  for (let index = 0; index < times; index += 1) { await Promise.resolve(); await nextTick() }
}

async function mount(path) {
  router = createRouter({ history: createMemoryHistory(), routes: routes() })
  await router.push(path)
  await router.isReady()
  host = document.createElement('div')
  document.body.appendChild(host)
  app = createApp(AppShell)
  app.use(router)
  app.use(createWorkspaceTheme())
  app.mount(host)
  await settle()
}

function toggleButton() {
  return [...document.querySelectorAll('.toplinks button')].find(node => node.textContent.includes('导航'))
}

async function openMenu() {
  const toggle = toggleButton()
  expect(toggle, 'the 导航 button must exist').toBeTruthy()
  toggle.click()
  await settle()
  return toggle
}

beforeEach(() => {
  // Vuetify 的 VApp 依赖这两个浏览器 API，jsdom 都没有。
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }))
  // 动画名与过渡属性来自真实样式表；jsdom 不会自己加载它。
  styleHost = document.createElement('style')
  styleHost.textContent = readFileSync('src/styles.css', 'utf8')
  document.head.appendChild(styleHost)
})

afterEach(() => {
  app?.unmount()
  host?.remove()
  styleHost?.remove()
  document.body.innerHTML = ''
})

describe('AppNav', () => {
  it('lists the new navigation inside a project: home, new research, four stages, export, all projects, settings', async () => {
    await mount(`/projects/${PROJECT}/papers`)
    await openMenu()
    const items = [...document.querySelectorAll('.navmap__item')]
    expect(items).toHaveLength(9)
    expect(items.map(node => node.querySelector('.navmap__name').textContent.trim())).toEqual([
      '首页', '新研究', '研究起点', '核心文献', '研究脉络', '研究洞察', '导出', '全部项目', '设置',
    ])
    expect(items[0].querySelector('.navmap__idx').textContent).toBe('01')
    expect(items[0].querySelector('.navmap__desc').textContent.length).toBeGreaterThan(4)
  })

  it('marks the current stage, not a stale page name', async () => {
    await mount(`/projects/${PROJECT}/threads`)
    await openMenu()
    const current = document.querySelector('.navmap__item.current')
    expect(current, 'exactly one current item').toBeTruthy()
    expect(current.querySelector('.navmap__name').textContent.trim()).toBe('研究脉络')
    expect(current.getAttribute('aria-current')).toBe('page')
  })

  it('drops the project-only entries when there is no project', async () => {
    await mount('/search')
    await openMenu()
    const names = [...document.querySelectorAll('.navmap__item .navmap__name')].map(node => node.textContent.trim())
    expect(names).toEqual(['首页', '新研究', '全部项目', '设置'])
  })

  it('animates in, animates out and unmounts after the 260ms exit', async () => {
    await mount(`/projects/${PROJECT}/papers`)
    await openMenu()
    const panel = document.querySelector('.nav-panel')
    expect(panel.classList.contains('open')).toBe(true)
    const items = [...panel.querySelectorAll('.navmap__item')]
    expect(getComputedStyle(items[0]).animationName).toBe('le-nav-in')
    expect(getComputedStyle(panel).transitionProperty).toContain('transform')

    panel.querySelector('.nav-panel__close').click()
    await settle()
    expect(document.querySelector('.nav-panel').classList.contains('is-closing')).toBe(true)
    await new Promise(resolve => setTimeout(resolve, 320))
    await settle()
    expect(document.querySelector('.nav-panel')).toBeNull()
  })

  it('closes on the scrim and on Escape', async () => {
    await mount(`/projects/${PROJECT}/papers`)
    await openMenu()
    const scrim = document.querySelector('.nav-scrim')
    expect(scrim.classList.contains('open')).toBe(true)
    expect(getComputedStyle(scrim).transitionProperty).toContain('opacity')
    scrim.click()
    await settle()
    expect(document.querySelector('.nav-panel').classList.contains('is-closing')).toBe(true)
    await new Promise(resolve => setTimeout(resolve, 320))
    await settle()
    expect(document.querySelector('.nav-scrim')).toBeNull()

    await openMenu()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await settle()
    expect(document.querySelector('.nav-panel').classList.contains('is-closing')).toBe(true)
  })

  it('moves focus into the panel, keeps Tab inside it and returns focus to 导航', async () => {
    await mount(`/projects/${PROJECT}/papers`)
    const toggle = await openMenu()
    expect(document.activeElement?.classList.contains('nav-panel__close')).toBe(true)

    const focusables = [...document.querySelectorAll('.nav-panel button')]
    const last = focusables[focusables.length - 1]
    last.focus()
    document.querySelector('.nav-panel').dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true }))
    expect(document.activeElement).toBe(focusables[0])

    document.querySelector('.nav-panel__close').click()
    await settle()
    expect(document.activeElement).toBe(toggle)
  })

  it('shows the stage bar as router links with roving tabindex (S4)', async () => {
    await mount(`/projects/${PROJECT}/threads`)
    const bar = document.querySelector('.le-nav-stages')
    expect(bar, 'a workspace route must show the stage bar').toBeTruthy()
    const links = [...bar.querySelectorAll('.le-nav-stage')]
    expect(links.map(node => node.textContent.trim())).toEqual(['研究起点', '核心文献', '研究脉络', '研究洞察'])
    expect(links.map(node => node.getAttribute('href'))).toEqual([
      `/projects/${PROJECT}/anchors`, `/projects/${PROJECT}/papers`, `/projects/${PROJECT}/threads`, `/projects/${PROJECT}/insight`,
    ])
    const current = links.find(node => node.getAttribute('aria-current') === 'step')
    expect(current.textContent.trim()).toBe('研究脉络')
    // roving tabindex：只有当前项在 Tab 序列里，左右键在项之间移动焦点。
    expect(links.map(node => node.getAttribute('tabindex'))).toEqual(['-1', '-1', '0', '-1'])
    links[2].focus()
    links[2].dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }))
    expect(document.activeElement).toBe(links[3])
    links[3].dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }))
    expect(document.activeElement).toBe(links[0])
  })

  it('keeps the stage bar out of the home page', async () => {
    await mount('/')
    expect(document.querySelector('.le-nav-stages')).toBeNull()
  })
})
