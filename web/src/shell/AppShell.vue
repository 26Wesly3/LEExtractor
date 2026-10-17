<script setup>
// 应用外壳（UI_INTEGRATION_PLAN.md S2；S3 起顶栏交给 AppNav.vue）。
//
// 外壳只负责三件事：`<v-app>` 的主题 class、跳到主要内容的链接、以及常驻的
// `<main id="main">`。`main` 必须比路由活得久，A1 的「景深到场」动画才有对象可播。
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import AppNav from './AppNav.vue'
import { lang } from './prefs.js'

const route = useRoute()
const isHome = computed(() => route.name === 'home')
const t = (zh, en) => (lang.value === 'zh' ? zh : en)
</script>

<template>
  <v-app :class="{ 'theme-home': isHome, 'theme-v32': !isHome }">
    <a class="skip-link" href="#main">{{ t('跳到主要内容', 'Skip to content') }}</a>
    <AppNav />
    <main id="main"><router-view /></main>
    <footer class="footer"><router-link to="/">LEExtractor</router-link><span>{{ t('惟学无际，际于天地', 'Knowledge without limits') }}</span><span>{{ t('证据留下，判断由你。', 'Keep the evidence. Make the judgment.') }}</span></footer>
  </v-app>
</template>
