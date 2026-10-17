<script setup>
// 导出是页面动作（docs/v3.2/05_UX_UI_SPEC.md「页面结构」）。
// 依赖 GET /api/v3/projects/{pid}/export?format=…：以 blob 下载；
// 路由报错（例如尚未上线时的 404）时如实显示错误，不假装成功。
//
// 形态与其它下拉一致：36px 高的动作按钮 + 右对齐菜单面板，菜单项等宽、可键盘
// 操作（Esc 关闭），导出后自动收起；下载结果与失败信息留在按钮旁边，不随菜单消失。
import { onBeforeUnmount, onMounted, ref } from 'vue'
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
const root = ref(null)

const LABELS = {
  ris: ['RIS', 'RIS'],
  bibtex: ['BibTeX', 'BibTeX'],
  csv: ['CSV 表格', 'CSV table'],
  evidence_pack: ['证据包', 'Evidence pack'],
  prisma: ['PRISMA 流程', 'PRISMA flow'],
}
const HINTS = {
  ris: ['EndNote / Zotero 通用', 'For EndNote and Zotero'],
  bibtex: ['LaTeX 参考文献', 'For LaTeX'],
  csv: ['逐条字段表格', 'One row per record'],
  evidence_pack: ['含取舍理由与来源', 'Decisions and sources'],
  prisma: ['检索与筛选流程', 'Search and screening flow'],
}
const label = format => (props.lang === 'zh' ? LABELS[format][0] : LABELS[format][1])
const hint = format => (props.lang === 'zh' ? HINTS[format][0] : HINTS[format][1])

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

function toggle(event) {
  // 每次展开都是一次新的动作：清掉上一次的结果，别让"已下载"误导这一次。
  if (event.target?.open) {
    done.value = ''
    failure.value = ''
  }
}

function close() {
  if (root.value && root.value.open) root.value.open = false
}

function onDocumentClick(event) {
  if (!root.value?.open) return
  if (!root.value.contains(event.target)) close()
}

function onKeydown(event) {
  if (event.key === 'Escape' && root.value?.open) {
    close()
    root.value.querySelector('summary')?.focus()
  }
}

onMounted(() => {
  document.addEventListener('click', onDocumentClick)
  document.addEventListener('keydown', onKeydown)
})
onBeforeUnmount(() => {
  document.removeEventListener('click', onDocumentClick)
  document.removeEventListener('keydown', onKeydown)
})

async function run(format) {
  if (busy.value || typeof props.client?.exportProject !== 'function') return
  busy.value = format
  done.value = ''
  failure.value = ''
  try {
    const { blob, filename } = await props.client.exportProject(props.projectId, format)
    save(blob, filename)
    done.value = t(`已下载 ${filename}`, `Downloaded ${filename}`)
    close()
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
  <div class="le32-export-wrap" :class="{ 'has-line': done || failure }">
    <details ref="root" class="le32-export" data-v32-export @toggle="toggle">
      <summary class="le32-action" role="button" data-v32-export-toggle>
        <span>{{ t('导出', 'Export') }}</span>
        <span class="le32-export-caret" aria-hidden="true">▾</span>
      </summary>
      <div class="le32-export-panel" role="menu" :aria-label="t('导出格式', 'Export format')">
        <button
          v-for="format in EXPORT_FORMATS"
          :key="format"
          type="button"
          class="le32-export-item"
          role="menuitem"
          :data-v32-export-format="format"
          :disabled="!!busy"
          :aria-busy="busy === format ? 'true' : 'false'"
          @click="run(format)"
        >
          <span class="le32-export-name">{{ busy === format ? t('导出中…', 'Exporting…') : label(format) }}</span>
          <span class="le32-export-hint">{{ hint(format) }}</span>
        </button>
      </div>
    </details>
    <p v-if="done" class="muted le32-export-line" data-v32-export-state role="status">{{ done }}</p>
    <p v-if="failure" class="error-text le32-export-line" data-v32-export-error role="alert">{{ failure }}</p>
  </div>
</template>

<style scoped>
/* 与工作区其它动作按钮同一套度量：36px 高、8px 圆角、13px 字。 */
.le32-export-wrap { display: inline-flex; flex-direction: column; align-items: flex-end; gap: 2px; }
.le32-export { position: relative; display: inline-flex; }
.le32-export > summary {
  list-style: none;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  user-select: none;
}
.le32-export > summary::-webkit-details-marker { display: none; }
.le32-export > summary:focus-visible { outline: 2px solid var(--ink); outline-offset: 2px; }
.le32-action {
  min-height: 36px;
  padding: 7px 14px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface);
  color: var(--text-2);
  font-size: 13px;
}
.le32-export-caret { font-size: 10px; color: var(--text-3, var(--text-2)); }
.le32-export-panel {
  position: absolute;
  z-index: 20;
  top: calc(100% + 6px);
  right: 0;
  min-width: 208px;
  padding: 6px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 10px;
  box-shadow: 0 8px 24px rgba(15, 23, 42, .12);
}
.le32-export-item {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  width: 100%;
  padding: 7px 10px;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: var(--text-2);
  font-size: 13px;
  text-align: left;
  cursor: pointer;
}
.le32-export-item:hover,
.le32-export-item:focus-visible { background: var(--surface-3); color: var(--ink); }
.le32-export-item[disabled] { opacity: .55; cursor: not-allowed; }
.le32-export-name { font-weight: 550; }
.le32-export-hint { font-size: 12px; color: var(--text-3, var(--text-2)); }
.le32-export-line { margin: var(--s-2) 0 0; font-size: 13px; }
</style>
