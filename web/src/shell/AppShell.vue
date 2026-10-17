<script setup>
// 应用外壳（UI_INTEGRATION_PLAN.md S2）：`<v-app>` + 跳转链接 + 顶栏 + 常驻 `#main`。
//
// 顶栏的 DOM 与 class 是从 App.vue 原样搬过来的，因为首页要求逐像素不变
// （web/e2e-shots/home-before-*.png 是基线，S2 的验收就是逐字节相同）。
// `<main id="main">` 常驻在这里，A1 的「景深到场」在路由切换后才有对象可播。
//
// S3 会把这一段顶栏与抽屉抽成 AppNav.vue 并换成新导航条目；现在它只是把
// 旧的行为原样接过来，所以 S2 的截图差必须为 0。
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { lang } from './prefs.js'
import { menuDesc, menuItems } from './nav-items.js'

const route = useRoute()
const router = useRouter()

const isHome = computed(() => route.name === 'home')
const t = (zh, en) => (lang.value === 'zh' ? zh : en)

const navOpen = ref(false)
const navClosing = ref(false)
let navTimer = null

/* 菜单：打开即插入，关闭先播 260ms 退场再卸载（A5，样式见 styles.css）。 */
function openNav() { clearTimeout(navTimer); navClosing.value = false; navOpen.value = true }
function closeNav() {
  if (!navOpen.value && !navClosing.value) return
  navOpen.value = false
  navClosing.value = true
  clearTimeout(navTimer)
  navTimer = setTimeout(() => { navClosing.value = false }, 260)
}

function toggleLang() { lang.value = lang.value === 'zh' ? 'en' : 'zh' }

/** 菜单条目：项目内的页面用当前项目 id 拼接，旧路径由 router.js 重定向到阶段。 */
function go(key) {
  closeNav()
  const projectId = String(route.params.projectId || '')
  if (key === 'home') return router.push('/')
  if (key === 'projects') return router.push('/projects')
  if (!projectId) return router.push('/search')
  return router.push(`/projects/${projectId}/${key}`)
}

function onGlobalKeydown(event) {
  if (event.key === 'Escape' && (navOpen.value || navClosing.value)) closeNav()
}

onMounted(() => document.addEventListener('keydown', onGlobalKeydown))
onBeforeUnmount(() => {
  document.removeEventListener('keydown', onGlobalKeydown)
  clearTimeout(navTimer)
})

const items = computed(() => menuItems(t))
// 当前条目：旧路由名 project-* 归一成 search/results/…，paper 单独保留。
const activeKey = computed(() => (route.name === 'paper' ? 'paper' : String(route.name || 'home').replace('project-', '')))
</script>

<template>
  <v-app :class="{ 'theme-home': isHome, 'theme-v32': !isHome }">
    <a class="skip-link" href="#main">{{ t('跳到主要内容', 'Skip to content') }}</a>
    <header class="topbar">
      <router-link to="/" class="brand" aria-label="LEExtractor home">LEExtractor<span class="brand-tag">{{ t('文献研究工作台', 'Research workspace') }}</span></router-link>
      <nav class="toplinks" :aria-label="t('主导航', 'Main navigation')"><router-link to="/projects">{{ t('项目', 'Projects') }}</router-link><router-link to="/settings">{{ t('设置', 'Settings') }}</router-link><v-btn size="small" variant="text" @click="toggleLang" :aria-label="t('切换到英文', 'Switch to Chinese')">{{ lang === 'zh' ? 'EN' : '中文' }}</v-btn><v-btn size="small" variant="outlined" @click="navOpen ? closeNav() : openNav()" :aria-expanded="navOpen">{{ t('导航', 'Navigate') }} <span aria-hidden="true">☰</span></v-btn></nav>
    </header>
    <div v-if="navOpen || navClosing" class="nav-scrim" :class="{ open: navOpen }" aria-hidden="true" @click="closeNav()"></div>
    <nav v-if="navOpen || navClosing" class="nav-panel" :class="{ open: navOpen, 'is-closing': navClosing }" :aria-label="t('工作台导航', 'Workspace navigation')">
      <div class="nav-panel__head"><strong>{{ t('LEExtractor 工作台', 'LEExtractor workbench') }}</strong><button type="button" class="nav-panel__close" @click="closeNav()" :aria-label="t('关闭菜单', 'Close menu')">×</button></div>
      <nav class="navmap">
        <button v-for="([key, text], index) in items" :key="key" type="button" class="navmap__item" :class="{ current: activeKey === key }" :aria-current="activeKey === key ? 'page' : null" :style="{ '--nav-i': index }" @click="go(key)">
          <span class="navmap__idx">{{ String(index + 1).padStart(2, '0') }}</span>
          <span class="navmap__name">{{ text }}</span>
          <span class="navmap__desc">{{ menuDesc(t, key) }}</span>
        </button>
      </nav>
    </nav>
    <main id="main"><router-view /></main>
  </v-app>
</template>
