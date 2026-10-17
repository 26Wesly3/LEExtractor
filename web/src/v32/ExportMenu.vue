<script setup>
// 导出是页面动作（docs/v3.2/05_UX_UI_SPEC.md「页面结构」）。
// 依赖 GET /api/v3/projects/{pid}/export?format=…：以 blob 下载；
// 路由报错（例如尚未上线时的 404）时如实显示错误，不假装成功。
import { ref } from 'vue'
import { errorLine, EXPORT_FORMATS } from './client.js'

const props = defineProps({
  client: { type: Object, required: true },
  projectId: { type: String, required: true },
  lang: { type: String, default: 'zh' },
})

const t = (zh, en) => (props.lang === 'zh' ? zh : en)
const busy = ref('')
const done = ref('')
const failure = ref('')

const LABELS = {
  ris: ['RIS', 'RIS'],
  bibtex: ['BibTeX', 'BibTeX'],
  csv: ['CSV 表格', 'CSV table'],
  evidence_pack: ['证据包', 'Evidence pack'],
  prisma: ['PRISMA 流程', 'PRISMA flow'],
}
const label = format => (props.lang === 'zh' ? LABELS[format][0] : LABELS[format][1])

function save(blob, filename) {
  if (typeof URL === 'undefined' || typeof URL.createObjectURL !== 'function') {
    throw new Error('download_unsupported')
  }
  const href = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = href
  link.download = filename
  link.rel = 'noopener'
  document.body.appendChild(link)
  try { link.click() } finally {
    link.remove()
    setTimeout(() => { try { URL.revokeObjectURL(href) } catch { /* already released */ } }, 0)
  }
}

async function run(format) {
  if (busy.value || typeof props.client?.exportProject !== 'function') return
  busy.value = format
  done.value = ''
  failure.value = ''
  try {
    const { blob, filename } = await props.client.exportProject(props.projectId, format)
    save(blob, filename)
    done.value = t(`已下载 ${filename}`, `Downloaded ${filename}`)
  } catch (problem) {
    failure.value = problem?.message === 'download_unsupported'
      ? t('浏览器不支持文件下载。', 'This browser cannot save the file.')
      : errorLine(problem, t('导出未完成，请稍后重试。', 'The export did not complete; try again shortly.'))
  } finally {
    busy.value = ''
  }
}
</script>

<template>
  <details class="le32-export" data-v32-export>
    <summary class="le32-action">{{ t('导出', 'Export') }}</summary>
    <div class="le32-export-menu" role="group" :aria-label="t('导出格式', 'Export format')">
      <button
        v-for="format in EXPORT_FORMATS"
        :key="format"
        type="button"
        class="le32-action"
        :data-v32-export-format="format"
        :disabled="!!busy"
        :aria-busy="busy === format ? 'true' : 'false'"
        @click="run(format)"
      >{{ label(format) }}</button>
    </div>
    <p class="muted le32-export-line" data-v32-export-state role="status">{{ done }}</p>
    <p v-if="failure" class="error-text le32-export-line" data-v32-export-error role="alert">{{ failure }}</p>
  </details>
</template>

<style scoped>
.le32-export { position: relative; }
.le32-export > summary { list-style: none; display: inline-flex; align-items: center; cursor: pointer; }
.le32-export > summary::-webkit-details-marker { display: none; }
.le32-export-menu { display: flex; gap: var(--s-2); flex-wrap: wrap; margin-top: var(--s-2); }
.le32-action { min-height: 36px; padding: 7px 14px; border: 1px solid var(--line); border-radius: 8px; background: var(--surface); color: var(--text-2); font-size: 13px; }
.le32-action[disabled] { opacity: .55; cursor: not-allowed; }
.le32-export-line { margin: var(--s-2) 0 0; font-size: 13px; }
.le32-export-line:empty { display: none; }
</style>
