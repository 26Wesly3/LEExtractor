<script setup>
// 顶栏与抽屉菜单（UI_INTEGRATION_PLAN.md S3，A5–A7 + A12）。
//
// 从 AppShell.vue 抽出来，条目换成新导航（nav-items.js）。顶栏的 DOM 与 class
// 一个字符都没动：首页要求逐像素一致，所以这里只搬不改。
//
// 菜单的三条退出路径（面板内 ×、点遮罩、Esc）与焦点管理都在这一个组件里：
// 打开时焦点进面板，Tab 在面板内循环，关闭后焦点回到「导航」按钮。
import { computed, nextTick, onBeforeUnmount, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { lang, motionReduced } from './prefs.js'
import { buildMenu } from './nav-items.js'
import { projectTitle } from './project-title.js'
import { STAGES } from '../v32/states.js'

const route = useRoute()
const router = useRouter()

const t = (zh, en) => (lang.value === 'zh' ? zh : en)
const hasProject = computed(() => Boolean(route.params.projectId))

const navOpen = ref(false)
const navClosing = ref(false)
const panelEl = ref(null)
const closeEl = ref(null)
const toggleEl = ref(null)
let navTimer = null

const items = computed(() => buildMenu({ t, lang: lang.value, hasProject: hasProject.value }))

/* 工作区阶段条（S4）：由路由驱动，刷新与前进后退都不会丢阶段。 */
const inWorkspace = computed(() => route.name === 'workspace' || route.name === 'paper')
const projectId = computed(() => String(route.params.projectId || ''))
const stages = computed(() => STAGES.map(stage => ({ key: stage.key, label: lang.value === 'zh' ? stage.zh : stage.en })))
const currentProjectName = computed(() => projectTitle.value || projectId.value)

/** 阶段条用 roving tabindex：只有当前项可 Tab 到，左右键在项之间移动焦点。 */
function onStageKeydown(event, index) {
  const step = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0
  if (!step) return
  event.preventDefault()
  const nodes = [...(event.currentTarget?.parentElement?.querySelectorAll('.le-nav-stage') || [])]
  const next = nodes[(index + step + nodes.length) % nodes.length]
  if (next) next.focus()
}

/** 当前条目：workspace 用阶段名，旧路由名归一成阶段。 */
const activeKey = computed(() => {
  if (route.name === 'workspace') return String(route.params.stage || 'anchors')
  const name = String(route.name || 'home').replace('project-', '')
  if (name === 'results' || name === 'review') return 'papers'
  if (name === 'expand' || name === 'landscape') return 'threads'
  if (name === 'questions') return 'insight'
  return name
})

function toggleElement() {
  return toggleEl.value?.$el || toggleEl.value || null
}

function focusables() {
  return [...(panelEl.value?.querySelectorAll('button') || [])]
}

async function openNav() {
  clearTimeout(navTimer)
  navClosing.value = false
  navOpen.value = true
  await nextTick()
  // 焦点先进面板；减弱动效时同样如此，键盘用户不依赖动画。
  const target = closeEl.value || focusables()[0]
  if (target && typeof target.focus === 'function') target.focus()
}

function closeNav() {
  if (!navOpen.value && !navClosing.value) return
  navOpen.value = false
  navClosing.value = true
  clearTimeout(navTimer)
  const duration = motionReduced() ? 0 : 260
  navTimer = setTimeout(() => { navClosing.value = false }, duration)
  const toggle = toggleElement()
  if (toggle && typeof toggle.focus === 'function') toggle.focus()
}

function toggleLang() { lang.value = lang.value === 'zh' ? 'en' : 'zh' }

/** 菜单条目跳转：四个阶段用当前项目 id 拼路径，导出带上 export=1。 */
function go(key) {
  closeNav()
  const projectId = String(route.params.projectId || '')
  if (key === 'home') return router.push('/')
  if (key === 'search') return router.push('/search')
  if (key === 'projects') return router.push('/projects')
  if (key === 'settings') return router.push('/settings')
  if (!projectId) return router.push('/search')
  if (key === 'export') return router.push(`/projects/${projectId}/papers?export=1`)
  return router.push(`/projects/${projectId}/${key}`)
}

/** Tab 在面板内循环，不能跑到页面后面去。 */
function onPanelKeydown(event) {
  if (event.key !== 'Tab' || !navOpen.value) return
  const list = focusables()
  if (!list.length) return
  const first = list[0]
  const last = list[list.length - 1]
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
}

function onGlobalKeydown(event) {
  if (event.key !== 'Escape') return
  if (navOpen.value || navClosing.value) closeNav()
}

// A12：减少动效时面板仍然要能开合，只是没有 260ms 退场。
onBeforeUnmount(() => clearTimeout(navTimer))
if (typeof document !== 'undefined') document.addEventListener('keydown', onGlobalKeydown)
onBeforeUnmount(() => { if (typeof document !== 'undefined') document.removeEventListener('keydown', onGlobalKeydown) })
</script>

<template>
  <header class="topbar">
    <router-link to="/" class="brand" aria-label="LEExtractor home">LEExtractor<span class="brand-tag">{{ t('文献研究工作台', 'Research workspace') }}</span></router-link>
    <nav class="toplinks" :aria-label="t('主导航', 'Main navigation')"><router-link to="/projects">{{ t('项目', 'Projects') }}</router-link><router-link to="/settings">{{ t('设置', 'Settings') }}</router-link><v-btn size="small" variant="text" @click="toggleLang" :aria-label="t('切换到英文', 'Switch to Chinese')">{{ lang === 'zh' ? 'EN' : '中文' }}</v-btn><v-btn ref="toggleEl" size="small" variant="outlined" @click="navOpen ? closeNav() : openNav()" :aria-expanded="navOpen" aria-controls="le-nav-panel">{{ t('导航', 'Navigate') }} <span aria-hidden="true">☰</span></v-btn></nav>
  </header>
  <nav v-if="inWorkspace" class="le-nav-stages" :aria-label="t('工作区阶段', 'Workspace stages')">
    <router-link v-for="(stage, index) in stages" :key="stage.key" class="le-nav-stage" :class="{ current: activeKey === stage.key }" :to="`/projects/${projectId}/${stage.key}`" :aria-current="activeKey === stage.key ? 'step' : null" :tabindex="activeKey === stage.key ? 0 : -1" @keydown="onStageKeydown($event, index)">{{ stage.label }}</router-link>
    <span class="project-name">{{ currentProjectName }}</span>
    <button type="button" class="le-nav-export" @click="go('export')">{{ t('导出', 'Export') }}</button>
  </nav>
  <div v-if="navOpen || navClosing" class="nav-scrim" :class="{ open: navOpen }" aria-hidden="true" @click="closeNav()"></div>
  <nav v-if="navOpen || navClosing" id="le-nav-panel" ref="panelEl" class="nav-panel" :class="{ open: navOpen, 'is-closing': navClosing }" :aria-label="t('工作台导航', 'Workspace navigation')" @keydown="onPanelKeydown">
    <div class="nav-panel__head"><strong>{{ t('LEExtractor 工作台', 'LEExtractor workbench') }}</strong><button ref="closeEl" type="button" class="nav-panel__close" @click="closeNav()" :aria-label="t('关闭菜单', 'Close menu')">×</button></div>
    <nav class="navmap">
      <button v-for="(item, index) in items" :key="item.key" type="button" class="navmap__item" :class="{ current: activeKey === item.key }" :aria-current="activeKey === item.key ? 'page' : null" :style="{ '--nav-i': index }" @click="go(item.key)">
        <span class="navmap__idx">{{ String(index + 1).padStart(2, '0') }}</span>
        <span class="navmap__name">{{ item.label }}</span>
        <span class="navmap__desc">{{ item.desc }}</span>
      </button>
    </nav>
  </nav>
</template>
