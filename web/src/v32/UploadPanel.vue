<script setup>
// 上传面板：逐文件展示状态。依据 docs/v3.2/05_UX_UI_SPEC.md「上传与异常」。
// 状态文案固定为：等待解析、识别中、已识别、需要确认、作为本地文献继续、
// 无法读取、需要文本识别、等待重试；普通界面不暴露 parser 名称。
// 部分成功时逐项展示状态与重试动作，重试只触达 retryable 的条目。
import { computed, ref } from 'vue'
import { errorLine } from './client.js'
import { fileStatusLabel, isPendingStatus, needsConfirmation, retryableIds, selectionProblemText, summariseBatch, validateSelection } from './upload.js'

const props = defineProps({
  lang: { type: String, default: 'zh' },
  client: { type: Object, required: true },
  projectId: { type: String, required: true },
  expectedRevision: { type: Number, default: 0 },
  limits: { type: Object, default: () => ({ maxFiles: 10, maxFileBytes: 20 * 1024 * 1024 }) },
})

const emit = defineEmits(['batch', 'error', 'settled'])

const t = (zh, en) => (props.lang === 'zh' ? zh : en)
const files = ref([])
const problems = ref([])
const busy = ref(false)
const message = ref('')
const batch = ref(null)
const importId = ref('')
let pollTimer = null

const items = computed(() => batch.value?.items || [])
const summary = computed(() => summariseBatch(items.value, props.lang))
const pending = computed(() => items.value.some(item => isPendingStatus(item?.status)))
const canRetry = computed(() => retryableIds(items.value).length > 0)

function onPick(event) {
  files.value = Array.from(event?.target?.files || [])
  const result = validateSelection(files.value, props.limits)
  problems.value = result.problems
}

function stopPolling() {
  if (pollTimer !== null) { clearInterval(pollTimer); pollTimer = null }
}

async function refresh() {
  if (!importId.value) return null
  try {
    const next = await props.client.getImport(props.projectId, importId.value)
    batch.value = next
    emit('batch', next)
    if (!next.items.some(item => isPendingStatus(item?.status))) { stopPolling(); emit('settled', next) }
    return next
  } catch (error) {
    stopPolling()
    message.value = errorLine(error, t('读取上传结果失败。', 'Could not read the import result.'))
    return null
  }
}

function startPolling() {
  stopPolling()
  if (typeof setInterval !== 'function') return
  pollTimer = setInterval(() => { refresh() }, 800)
}

async function submit() {
  const result = validateSelection(files.value, props.limits)
  problems.value = result.problems
  if (!result.ok) return
  busy.value = true
  message.value = ''
  try {
    const accepted = await props.client.importPapers(props.projectId, { expectedRevision: props.expectedRevision, files: files.value })
    importId.value = accepted?.import_id || ''
    batch.value = accepted
    emit('batch', accepted)
    startPolling()
    await refresh()
  } catch (error) {
    message.value = errorLine(error, t('上传未完成。', 'The upload did not complete.'))
    emit('error', error)
  } finally {
    busy.value = false
  }
}

async function retryItem(item) {
  if (!retryableIds([item]).length) return
  busy.value = true
  message.value = ''
  try {
    // 只提交该项：其他文件的已识别结果不被重算。
    const next = await props.client.retryImport(props.projectId, importId.value, { expected_revision: props.expectedRevision, item_ids: [item.item_id] })
    batch.value = next
    emit('batch', next)
    await refresh()
  } catch (error) {
    message.value = errorLine(error, t('重试未能提交。', 'The retry could not be submitted.'))
  } finally {
    busy.value = false
  }
}

async function resolveItem(item, action, candidateId = null) {
  busy.value = true
  message.value = ''
  try {
    const next = await props.client.resolveImport(props.projectId, importId.value, { expected_revision: props.expectedRevision, item_id: item.item_id, action, candidate_id: candidateId })
    batch.value = next
    emit('batch', next)
  } catch (error) {
    message.value = errorLine(error, t('确认未能提交。', 'The confirmation could not be submitted.'))
  } finally {
    busy.value = false
  }
}

/** 恢复一个已存在的批次（页面刷新后继续展示逐项状态）。 */
function setBatch(next) {
  batch.value = next || null
  importId.value = next?.import_id || importId.value
  emit('batch', batch.value)
  if (batch.value && !items.value.some(item => isPendingStatus(item?.status))) emit('settled', batch.value)
}

defineExpose({ refresh, stopPolling, setBatch })
</script>

<template>
  <section class="le32-upload" data-v32-upload :aria-label="t('上传已有文献','Upload existing literature')">
    <h2>{{ t('上传已有文献', 'Upload existing literature') }}</h2>
    <p class="muted">
      {{ t('选取 1 至 10 个 PDF。每个文件独立解析与匹配，无法读取的文件明确失败，不会记为成功。','Select 1 to 10 PDFs. Each file is parsed and matched independently; an unreadable file fails explicitly instead of being recorded as a success.') }}
    </p>
    <div class="le32-upload-pick">
      <input id="le32-upload-input" ref="fileInput" class="le32-upload-input" type="file" accept="application/pdf,.pdf" multiple :disabled="busy" :aria-label="t('选择 PDF 文件','Choose PDF files')" data-v32-upload-input @change="onPick" />
      <label class="le32-primary le32-upload-label" :class="{ 'is-disabled': busy }" for="le32-upload-input">
        {{ files.length ? `${files.length} ${t('个文件已选择','files selected')}` : t('选择文件', 'Choose files') }}
      </label>
      <button type="button" class="le32-primary" :disabled="busy || !files.length" data-v32-upload-submit @click="submit">
        {{ t('导入并发现文献', 'Import and discover literature') }}
      </button>
    </div>
    <p v-if="files.length" class="muted" data-v32-upload-selection>{{ files.length }} {{ t('个文件已选择','files selected') }}</p>
    <p v-for="problem in problems" :key="problem.code" class="error-text" role="alert">{{ selectionProblemText(problem, lang) }}</p>
    <p v-if="message" class="error-text" role="alert" data-v32-upload-error>{{ message }}</p>

    <div v-if="items.length" class="le32-upload-batch" data-v32-upload-batch>
      <p :class="['le32-batch-line', summary.partial ? 'is-partial' : '']" data-v32-upload-summary role="status">{{ summary.text }}</p>
      <ul class="le32-upload-items">
        <li v-for="item in items" :key="item.item_id" class="le32-upload-item" :data-v32-upload-item="item.item_id" :data-upload-status="item.status">
          <span class="le32-upload-name">{{ item.display_name }}</span>
          <span class="badge" data-upload-status-label>{{ fileStatusLabel(item.status, lang) }}</span>
          <span v-if="item.error_code" class="muted" data-upload-error-code>{{ item.error_code }}</span>
          <button v-if="item.retryable" type="button" class="le32-action" :disabled="busy" data-upload-retry @click="retryItem(item)">
            {{ t('重试', 'Retry') }}
          </button>
          <template v-if="needsConfirmation(item.status)">
            <button type="button" class="le32-action" :disabled="busy" data-upload-confirm="local" @click="resolveItem(item, 'keep_local')">
              {{ t('作为本地文献继续', 'Continue as a local seed') }}
            </button>
            <button
              v-for="candidate in item.match_candidates.slice(0, 1)"
              :key="candidate.candidate_id"
              type="button"
              class="le32-action"
              :disabled="busy"
              data-upload-confirm="match"
              @click="resolveItem(item, 'match', candidate.candidate_id)"
            >{{ t('采用候选记录', 'Use the candidate record') }}</button>
          </template>
        </li>
      </ul>
    </div>
  </section>
</template>

<style scoped>
.le32-upload { border: 1px solid var(--border); border-radius: 10px; background: var(--surface-2); padding: var(--s-6); }
.le32-upload h2 { font-size: 20px; margin-bottom: var(--s-2); }
.le32-upload-pick { display: flex; flex-wrap: wrap; gap: var(--s-3); align-items: center; margin-top: var(--s-4); }
/* 原生 file input 无法统一外观：藏起来，用 label 做同一个黑色按钮。
   元素仍在 DOM 里，键盘与读屏照常可用（label 的 for 指向它）。 */
.le32-upload-input { position: absolute; width: 1px; height: 1px; opacity: 0; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
.le32-upload-label { display: inline-flex; align-items: center; cursor: pointer; }
.le32-upload-input:focus-visible + .le32-upload-label { outline: 3px solid #677b94; outline-offset: 2px; }
.le32-upload-label.is-disabled { opacity: .55; cursor: not-allowed; }
.le32-primary { min-height: 40px; padding: 10px 18px; border: 1px solid var(--ink); border-radius: 8px; background: var(--ink); color: #fff; font-size: 14px; font-weight: 550; }
.le32-primary[disabled] { opacity: .55; cursor: not-allowed; }
.le32-upload-batch { margin-top: var(--s-4); }
.le32-batch-line { font-size: 13px; color: var(--text-2); }
.le32-batch-line.is-partial { color: #9a6100; }
.le32-upload-items { list-style: none; margin: var(--s-3) 0 0; padding: 0; }
.le32-upload-item { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s-2) var(--s-3); padding: var(--s-3) 0; border-bottom: 1px solid var(--border); font-size: 13.5px; }
.le32-upload-name { min-width: 0; overflow-wrap: anywhere; }
.le32-action { min-height: 34px; padding: 6px 12px; border: 1px solid var(--line); border-radius: 8px; background: var(--surface); color: var(--text-2); font-size: 13px; }
.le32-action:hover { border-color: #9aa2ad; color: var(--ink); }
</style>
