<script setup>
// v3.2 应用根组件。
//
// 只在路由带 meta.v32 时渲染 v3.2 工作区；其余路由原样渲染既有 App.vue
// （它自带 <v-app> 外壳），因此既有页面、导航与动效完全不变
// （docs/v3.2/05_UX_UI_SPEC.md「视觉基线」）。
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import App from '../App.vue'
import Workspace from './Workspace.vue'
import { isV32Route } from './routes.js'

const route = useRoute()
const isV32 = computed(() => isV32Route(route))
const lang = computed(() => (route.query.lang === 'en' ? 'en' : 'zh'))
</script>

<template>
  <Workspace
    v-if="isV32"
    :project-id="String(route.params.projectId || '')"
    :stage="String(route.params.stage || 'anchors')"
    :view="String(route.meta?.v32View || '')"
    :lang="lang"
  />
  <App v-else />
</template>
