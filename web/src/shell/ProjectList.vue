<script setup>
// 项目列表（UI_INTEGRATION_PLAN.md S6，按用户确认的 D1）。
//
// 旧存储的项目在 v3 工作区里打不开，所以这里只读：显示、标「旧版·只读」、提供
// RIS 导出。要开始新工作走「新研究」；导出的 RIS 可以在上传面板里导入成 v3 项目。
// 不假装这些项目能直接打开，也不提供自动迁移。
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { request } from '../api.js'
import { createV32Client } from '../v32/client.js'
import { lang } from './prefs.js'

const router = useRouter()
const t = (zh, en) => (lang.value === 'zh' ? zh : en)

const rows = ref([])
const modern = ref([])
const loading = ref(true)
const error = ref('')
const busy = ref('')

async function load() {
  loading.value = true
  error.value = ''
  // v3 项目：先建立匿名会话（GET /session），再读列表；失败时不影响旧版列表。
  try {
    const api = createV32Client()
    await api.session()
    const data = await request('/v3/projects')
    modern.value = Array.isArray(data?.items) ? data.items : []
  } catch {
    modern.value = []
  }
  try {
    const data = await request('/projects')
    rows.value = Array.isArray(data?.items) ? data.items : []
  } catch (problem) {
    error.value = problem?.message || t('未能读取项目列表。', 'Could not read the project list.')
  } finally {
    loading.value = false
  }
}

/** 旧接口是文件响应，不能走 JSON 客户端；失败如实显示。 */
async function exportRis(row) {
  if (busy.value) return
  busy.value = row.project_id
  error.value = ''
  try {
    const response = await fetch(`/api/projects/${encodeURIComponent(row.project_id)}/exports`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ format: 'ris', scope: 'corpus', expected_revision: Number(row.revision || 0) }),
    })
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`)
    const blob = await response.blob()
    if (typeof URL === 'undefined' || typeof URL.createObjectURL !== 'function') throw new Error(t('浏览器不支持下载', 'This browser cannot save files'))
    const href = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = href
    link.download = `${row.name || row.project_id}.ris`
    link.rel = 'noopener'
    document.body.appendChild(link)
    try { link.click() } finally {
      link.remove()
      setTimeout(() => { try { URL.revokeObjectURL(href) } catch { /* 已释放 */ } }, 0)
    }
  } catch (problem) {
    error.value = `${t('导出未完成：', 'Export failed: ')}${problem?.message || ''}`
  } finally {
    busy.value = ''
  }
}

onMounted(load)
</script>

<template>
  <section class="le-list" aria-labelledby="le-list-title">
    <header class="le-list-head">
      <div>
        <p class="eyebrow">{{ t('旧版存储', 'Legacy storage') }}</p>
        <h1 id="le-list-title">{{ t('项目', 'Projects') }}</h1>
        <p class="muted le-list-lead">{{ t('工作区项目可以直接打开。旧版项目属于旧存储：只读，只提供 RIS 导出；导出的 RIS 可以在上传面板里导入成一个 v3 项目。新的研究请在「开始新研究」里开始。', 'Workspace projects open directly. Legacy projects use the old store: read-only, RIS export only; an exported RIS can be imported into a v3 project from the upload panel. Start new work from New research.') }}</p>
      </div>
      <button type="button" class="le-list-primary" @click="router.push('/search')">{{ t('开始新研究', 'Start new research') }}</button>
    </header>

    <p v-if="error" class="le-list-error" role="alert">{{ error }}</p>
    <p v-if="loading" class="muted">{{ t('正在读取项目…', 'Loading projects…') }}</p>

    <template v-else>
      <h2 class="le-list-section">{{ t('当前工作区项目', 'Workspace projects') }}</h2>
      <ul v-if="modern.length" class="le-list-rows" data-v3-projects>
        <li v-for="row in modern" :key="row.project_id" class="le-list-row" :data-v3-project="row.project_id">
          <div class="le-list-row-main">
            <strong>{{ row.name || row.project_id }}</strong>
            <p class="muted">{{ row.entry_mode === 'pdf' ? t('从已有文献开始', 'Started from PDFs') : t('从研究方向开始', 'Started from a direction') }}</p>
            <p class="le-list-meta"><span class="le-list-badge">{{ t('可打开', 'Openable') }}</span><span>{{ row.updated_at || '' }}</span></p>
          </div>
          <router-link class="le-list-action" :to="`/projects/${row.project_id}/anchors`">{{ t('打开', 'Open') }}</router-link>
        </li>
      </ul>
      <p v-else class="muted">{{ t('还没有工作区项目。用「开始新研究」建一个。', 'No workspace projects yet. Start a new research project.') }}</p>

      <h2 class="le-list-section">{{ t('旧版项目（只读）', 'Legacy projects (read-only)') }}</h2>
      <ul v-if="rows.length" class="le-list-rows" data-legacy-projects>
        <li v-for="row in rows" :key="row.project_id" class="le-list-row" :data-legacy-project="row.project_id">
          <div class="le-list-row-main">
            <strong>{{ row.name || row.topic || row.project_id }}</strong>
            <p class="muted">{{ row.topic || t('未填写主题', 'No topic') }}</p>
            <p class="le-list-meta"><span class="le-list-badge">{{ t('旧版·只读', 'Legacy · read-only') }}</span><span>{{ row.updated_at || '' }}</span></p>
          </div>
          <button type="button" class="le-list-action" :disabled="busy === row.project_id" :data-legacy-export="row.project_id" @click="exportRis(row)">{{ busy === row.project_id ? t('导出中…', 'Exporting…') : t('导出 RIS', 'Export RIS') }}</button>
        </li>
      </ul>
      <p v-else class="muted">{{ t('还没有旧版项目。', 'No legacy projects yet.') }}</p>
    </template>
  </section>
</template>
