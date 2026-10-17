<script setup>
// 本机 Zotero（S11）：只读本地 API，把选中的条目导入当前项目。
// 不写回 Zotero、不要求 Web API Key；连不上时给一句可操作的提示，而不是空白。
import { onMounted, ref } from 'vue'
import { errorLine } from './client.js'

const props = defineProps({
  client: { type: Object, required: true },
  projectId: { type: String, required: true },
  revision: { type: Number, default: 1 },
  lang: { type: String, default: 'zh' },
})
const emit = defineEmits(['imported'])

const t = (zh, en) => (props.lang === 'zh' ? zh : en)

const status = ref(null)
const collections = ref([])
const rows = ref([])
const selected = ref([])
const query = ref('')
const collection = ref('')
const busy = ref(false)
const message = ref('')
const error = ref('')

async function loadStatus() {
  try {
    status.value = await props.client.zoteroLocalStatus()
    if (!status.value?.running) return
    const data = await props.client.zoteroLocalCollections()
    collections.value = Array.isArray(data?.items) ? data.items : []
    await search()
  } catch (problem) {
    status.value = { running: false, message: errorLine(problem, t('连不上本机 Zotero。', 'Could not reach the local Zotero.')) }
  }
}

async function search() {
  if (!status.value?.running) return
  busy.value = true
  error.value = ''
  try {
    const data = await props.client.zoteroLocalItems({ q: query.value, collection: collection.value, limit: 30 })
    rows.value = Array.isArray(data?.items) ? data.items : []
    selected.value = []
  } catch (problem) {
    error.value = errorLine(problem, t('读取 Zotero 条目失败。', 'Could not read Zotero items.'))
  } finally {
    busy.value = false
  }
}

function toggle(key) {
  selected.value = selected.value.includes(key)
    ? selected.value.filter(value => value !== key)
    : [...selected.value, key]
}

async function importSelected() {
  if (!selected.value.length || busy.value) return
  busy.value = true
  message.value = ''
  error.value = ''
  try {
    const result = await props.client.importZoteroLocal(props.projectId, {
      item_keys: selected.value,
      expected_revision: Number(props.revision || 1),
    })
    message.value = t(
      `已导入 ${result.imported} 条（新增 ${result.created} 条）。`,
      `Imported ${result.imported} items (${result.created} new).`,
    )
    selected.value = []
    emit('imported', result)
  } catch (problem) {
    error.value = errorLine(problem, t('导入未完成。', 'The import did not complete.'))
  } finally {
    busy.value = false
  }
}

onMounted(loadStatus)
</script>

<template>
  <section class="le32-zotero" data-v32-zotero-local :aria-label="t('从本机 Zotero 导入', 'Import from the local Zotero')">
    <div class="le32-zotero-head">
      <h2>{{ t('从本机 Zotero 导入', 'Import from the local Zotero') }}</h2>
      <span class="badge" :class="{ configured: status?.running }" data-v32-zotero-status>
        {{ status?.running ? t('已连接', 'Connected') : t('未连接', 'Not connected') }}
      </span>
    </div>

    <p v-if="status && !status.running" class="muted" data-v32-zotero-hint>
      {{ status.message || t('连不上本机 Zotero。请确认 Zotero 正在运行，并在「偏好设置 → 高级」里勾选允许其他应用通讯。', 'Could not reach Zotero. Make sure Zotero is running and "Allow other applications on this computer to communicate with Zotero" is enabled.') }}
    </p>

    <template v-else-if="status?.running">
      <p class="muted">{{ status.library_name || t('我的文库', 'My library') }} · {{ t('只读：不会改动你的 Zotero 库', 'Read-only: your Zotero library is not modified') }}</p>
      <div class="le32-zotero-tools">
        <select v-model="collection" :aria-label="t('按分类筛选', 'Filter by collection')" data-v32-zotero-collection @change="search">
          <option value="">{{ t('全部分类', 'All collections') }}</option>
          <option v-for="row in collections" :key="row.key" :value="row.key">{{ row.name }}</option>
        </select>
        <input v-model="query" type="search" :placeholder="t('按标题 / 作者 / 年份搜索', 'Search title, author or year')" data-v32-zotero-query @keyup.enter="search" />
        <button type="button" class="le32-action" :disabled="busy" data-v32-zotero-search @click="search">{{ t('搜索', 'Search') }}</button>
      </div>

      <ul v-if="rows.length" class="brief-list le32-zotero-rows" data-v32-zotero-items>
        <li v-for="row in rows" :key="row.item_key">
          <label class="le32-zotero-row">
            <input type="checkbox" :value="row.item_key" :checked="selected.includes(row.item_key)" :data-v32-zotero-item="row.item_key" @change="toggle(row.item_key)" />
            <span>
              <strong>{{ row.title || t('无标题', 'Untitled') }}</strong>
              <span class="muted"> · {{ (row.creators || []).slice(0, 3).join(', ') }}<template v-if="row.year"> · {{ row.year }}</template><template v-if="row.venue"> · {{ row.venue }}</template></span>
            </span>
          </label>
        </li>
      </ul>
      <p v-else class="muted">{{ t('没有匹配的条目。', 'No matching items.') }}</p>

      <button type="button" class="le32-primary" :disabled="busy || !selected.length" data-v32-zotero-import @click="importSelected">
        {{ t(`导入选中的 ${selected.length} 条`, `Import ${selected.length} selected`) }}
      </button>
      <p v-if="message" class="muted" role="status" data-v32-zotero-message>{{ message }}</p>
      <p v-if="error" class="error-text" role="alert" data-v32-zotero-error>{{ error }}</p>
    </template>
  </section>
</template>

<style scoped>
.le32-zotero { border: 1px solid var(--border); border-radius: 10px; background: var(--surface-2); padding: var(--s-6); margin-top: var(--s-5); }
.le32-zotero-head { display: flex; align-items: center; justify-content: space-between; gap: var(--s-3); }
.le32-zotero h2 { font-size: 20px; margin: 0; }
.le32-zotero-tools { display: flex; flex-wrap: wrap; gap: var(--s-2); align-items: center; margin: var(--s-3) 0; }
.le32-zotero-tools select, .le32-zotero-tools input { min-height: 36px; padding: 6px 10px; border: 1px solid var(--line); border-radius: 8px; font: inherit; font-size: 13px; }
.le32-zotero-tools input { flex: 1 1 240px; }
.le32-zotero-rows { max-height: 320px; overflow-y: auto; margin: 0 0 var(--s-3); }
.le32-zotero-row { display: flex; gap: var(--s-2); align-items: flex-start; padding: var(--s-2) 0; border-bottom: 1px solid var(--border); font-size: 13.5px; cursor: pointer; }
.le32-zotero-row input { margin-top: 3px; }
</style>
