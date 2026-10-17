<script setup>
// 设置：三个凭据区域。依据 docs/v3.2/05_UX_UI_SPEC.md「设置」。
//   * 检索服务 / AI 模型 / Zotero 三个区域；
//   * 写入 Key 后仅显示已配置状态，明文永不回显，也不进入 URL；
//   * AI 提供「基础模式」与「AI 增强」两种，没有语义未定义的 deep 模式；
//   * 选择基础模式不调用任何远程模型；选择 AI 增强时明确说明会发送元数据。
// 本轮不提供任何校园账号登录入口（AC-28）。
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { errorLine } from './client.js'

const props = defineProps({
  lang: { type: String, default: 'zh' },
  client: { type: Object, required: true },
  modelMode: { type: String, default: 'basic' },
})

const emit = defineEmits(['update:modelMode'])

const t = (zh, en) => (props.lang === 'zh' ? zh : en)
const statuses = ref([])
const emptyDrafts = () => ({
  semantic_scholar: { secret: '', model: '' },
  openalex: { secret: '', model: '' },
  crossref: { secret: '', model: '' },
  ai_model: { secret: '', model: '', profile: 'openai' },
  zotero: { secret: '', model: '' },
})
const drafts = reactive(emptyDrafts())
const busy = ref('')
const message = ref('')
const error = ref('')

const AREAS = [
  {
    kind: 'retrieval',
    title: { zh: '检索服务', en: 'Retrieval services' },
    lead: {
      zh: '来源凭据保存在本机服务端，页面只显示配置状态。',
      en: 'Provider credentials stay on the local server; this page shows configuration status only.',
    },
    providers: [
      { id: 'semantic_scholar', label: 'Semantic Scholar' },
      { id: 'openalex', label: 'OpenAlex' },
      { id: 'crossref', label: 'Crossref' },
    ],
  },
  {
    kind: 'model',
    title: { zh: 'AI 模型', en: 'AI model' },
    lead: {
      zh: '基础模式只使用本地排序，不调用远程模型；AI 增强会把题名与摘要等元数据发送给所配置的模型服务。',
      en: 'Basic mode uses the local ranking only and calls no remote model. AI enhanced sends metadata such as titles and abstracts to the configured model service.',
    },
    providers: [{ id: 'ai_model', label: { zh: '模型服务凭据', en: 'Model service credential' } }],
    // 契约里的 endpoint_profile_id 就是「用哪一家」，model 是模型名。
    // 之前只有一个笼统的凭据槽，填了 Key 也不知道该发去哪家服务。
    profiles: [
      { value: 'openai', label: 'OpenAI', model: 'gpt-5.5' },
      { value: 'deepseek', label: 'DeepSeek', model: 'deepseek-flash' },
      { value: 'anthropic', label: 'Anthropic Claude', model: 'claude-opus-5-5' },
    ],
  },
  {
    kind: 'zotero',
    title: { zh: 'Zotero', en: 'Zotero' },
    lead: {
      zh: '用于文献库导入与导出预览；写入后只显示配置状态。',
      en: 'Used for library import and export previews; writing a key only reports the configured status.',
    },
    providers: [{ id: 'zotero', label: 'Zotero API Key' }],
  },
]

const MODES = [
  { value: 'basic', zh: '基础模式', en: 'Basic mode', note: { zh: '不调用远程模型', en: 'No remote model is called' } },
  { value: 'ai_enhanced', zh: 'AI 增强', en: 'AI enhanced', note: { zh: '将发送题名与摘要等元数据', en: 'Sends metadata such as titles and abstracts' } },
]

function statusOf(providerId) {
  return statuses.value.find(row => row.provider_id === providerId) || null
}

function configuredText(providerId) {
  const row = statusOf(providerId)
  if (!row) return t('未配置', 'Not configured')
  return row.configured ? t('已配置', 'Configured') : t('未配置', 'Not configured')
}

function isConfigured(providerId) {
  return statusOf(providerId)?.configured === true
}

async function load() {
  try {
    // 先建立匿名会话：凭据状态接口要求会话 cookie，直接读会在首次进入设置页时
    // 拿到 401，控制台里留一条红字、状态显示成「未配置」但其实是没授权读。
    try { await props.client.session() } catch { /* 会话失败时下面会给出可读错误 */ }
    const data = await props.client.credentials()
    statuses.value = Array.isArray(data?.items) ? data.items : []
    error.value = ''
  } catch (problem) {
    error.value = errorLine(problem, t('未能读取凭据状态。', 'Could not read credential status.'))
  }
}

async function save(area) {
  const provider = area.providers[0]
  const draft = drafts[provider.id]
  if (!draft || !draft.secret) return
  busy.value = provider.id
  message.value = ''
  error.value = ''
  try {
    const body = { kind: area.kind, secret: draft.secret }
    if (area.kind === 'model') {
      if (draft.profile) body.endpoint_profile_id = draft.profile
      if (draft.model && draft.model.trim()) body.model = draft.model.trim()
    }
    const saved = await props.client.saveCredential(provider.id, body)
    // 提交后立即清空输入：明文不再保留在页面状态里。
    draft.secret = ''
    const existing = statuses.value.filter(row => row.provider_id !== provider.id)
    statuses.value = [...existing, saved].filter(Boolean)
    message.value = saved?.configured === true
      ? t('已保存，仅显示配置状态。', 'Saved. Only the configuration status is shown.')
      : t('已提交。', 'Submitted.')
  } catch (problem) {
    error.value = errorLine(problem, t('未能保存配置。', 'Could not save the configuration.'))
  } finally {
    busy.value = ''
  }
}

async function clear(area) {
  const provider = area.providers[0]
  busy.value = provider.id
  message.value = ''
  try {
    await props.client.deleteCredential(provider.id)
    statuses.value = statuses.value.map(row => (row.provider_id === provider.id ? { ...row, configured: false, updated_at: null } : row))
    message.value = t('已删除该凭据。', 'The credential was removed.')
  } catch (problem) {
    error.value = errorLine(problem, t('未能删除凭据。', 'Could not remove the credential.'))
  } finally {
    busy.value = ''
  }
}

function chooseMode(value) {
  selectedMode.value = value
  emit('update:modelMode', value)
}

// 本地模式与父级保持同步：只有两种定义明确的模式，没有语义未定义的 deep。
const selectedMode = ref(props.modelMode === 'ai_enhanced' ? 'ai_enhanced' : 'basic')
watch(() => props.modelMode, value => { if (value !== selectedMode.value) selectedMode.value = value === 'ai_enhanced' ? 'ai_enhanced' : 'basic' })

const activeMode = computed(() => MODES.find(row => row.value === selectedMode.value) || MODES[0])

onMounted(load)
defineExpose({ load })
</script>

<template>
  <section class="le32-settings" data-v32-settings :aria-label="t('凭据设置','Credential settings')">
    <h1>{{ t('设置', 'Settings') }}</h1>
    <div class="le32-settings-grid">
      <article v-for="area in AREAS" :key="area.kind" class="le32-settings-area" :data-settings-area="area.kind">
        <h2>{{ lang === 'zh' ? area.title.zh : area.title.en }}</h2>
        <p class="muted">{{ lang === 'zh' ? area.lead.zh : area.lead.en }}</p>

        <div v-if="area.kind === 'model'" class="le32-modes" data-settings-modes>
          <button
            v-for="mode in MODES"
            :key="mode.value"
            type="button"
            class="le32-mode"
            :class="{ 'is-active': selectedMode === mode.value }"
            :data-settings-mode="mode.value"
            :aria-pressed="selectedMode === mode.value ? 'true' : 'false'"
            @click="chooseMode(mode.value)"
          >
            <strong>{{ lang === 'zh' ? mode.zh : mode.en }}</strong>
            <span>{{ lang === 'zh' ? mode.note.zh : mode.note.en }}</span>
          </button>
          <p class="muted" data-settings-mode-note>
            {{ selectedMode === 'ai_enhanced'
              ? t('当前为 AI 增强：题名与摘要等元数据会发送给所配置的模型服务。','AI enhanced is active: metadata such as titles and abstracts is sent to the configured model service.')
              : t('当前为基础模式：不调用任何远程模型。','Basic mode is active: no remote model is called.') }}
          </p>
        </div>

        <div v-for="provider in area.providers" :key="provider.id" class="le32-settings-provider" :data-settings-provider="provider.id">
          <div class="le32-settings-state">
            <span class="field-label">{{ typeof provider.label === 'string' ? provider.label : (lang === 'zh' ? provider.label.zh : provider.label.en) }}</span>
            <span class="badge" :class="{ configured: isConfigured(provider.id) }" data-settings-status>{{ configuredText(provider.id) }}</span>
          </div>
          <form class="le32-settings-form" @submit.prevent="save(area)">
            <label class="visually-hidden" :for="`v32-secret-${provider.id}`">{{ t('凭据明文', 'Credential secret') }}</label>
            <input
              :id="`v32-secret-${provider.id}`"
              v-model="drafts[provider.id].secret"
              type="password"
              autocomplete="new-password"
              :placeholder="t('输入后保存，明文不会回显','Enter and save; the plaintext is never echoed')"
              :data-settings-secret="provider.id"
            />
            <label v-if="area.kind === 'model'" class="field-label" :for="`v32-profile-${provider.id}`">{{ t('模型服务', 'Model service') }}</label>
            <select
              v-if="area.kind === 'model'"
              :id="`v32-profile-${provider.id}`"
              v-model="drafts[provider.id].profile"
              :data-settings-profile="provider.id"
            >
              <option v-for="option in area.profiles" :key="option.value" :value="option.value">{{ option.label }} · {{ option.model }}</option>
            </select>
            <input
              v-if="area.kind === 'model'"
              v-model="drafts[provider.id].model"
              type="text"
              :placeholder="t('模型名称（可选，留空用上面的默认值）','Model name (optional; blank uses the default above)')"
              :data-settings-model="provider.id"
            />
            <button type="submit" class="le32-primary" :disabled="busy === provider.id" :data-settings-save="provider.id">{{ t('保存', 'Save') }}</button>
            <button type="button" class="le32-action" :disabled="busy === provider.id || !isConfigured(provider.id)" :data-settings-delete="provider.id" @click="clear(area)">{{ t('删除', 'Remove') }}</button>
          </form>
        </div>
      </article>
    </div>
    <p v-if="message" class="muted" role="status" data-settings-message>{{ message }}</p>
    <p v-if="error" class="error-text" role="alert" data-settings-error>{{ error }}</p>
    <p class="muted le32-settings-foot">{{ t('凭据只保存在本机服务端；页面与日志都不会出现明文。','Credentials stay on the local server; neither this page nor the logs contain the plaintext.') }}</p>
  </section>
</template>

<style scoped>
.le32-settings { max-width: 1180px; margin: 0 auto; padding: 0 var(--gutter); }
.le32-settings-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: var(--s-6); margin-top: var(--s-6); }
.le32-settings-area { border: 1px solid var(--border); border-radius: 10px; background: var(--surface-2); padding: var(--s-6); }
.le32-settings-area h2 { font-size: 19px; margin-bottom: var(--s-2); }
.le32-settings-provider { margin-top: var(--s-4); padding-top: var(--s-4); border-top: 1px solid var(--border); }
.le32-settings-state { display: flex; justify-content: space-between; align-items: center; gap: var(--s-3); }
.le32-settings-form { display: flex; flex-direction: column; gap: var(--s-2); margin-top: var(--s-3); }
.le32-settings-form input { min-height: 38px; padding: 8px 10px; border: 1px solid var(--line); border-radius: 6px; background: var(--surface); font-size: 13px; }
.le32-settings-form select { min-height: 38px; padding: 8px 10px; border: 1px solid var(--line); border-radius: 6px; background: var(--surface); font-size: 13px; }
.le32-primary { min-height: 38px; padding: 8px 16px; border: 1px solid var(--ink); border-radius: 8px; background: var(--ink); color: #fff; font-size: 13px; font-weight: 550; }
.le32-primary[disabled] { opacity: .55; cursor: not-allowed; }
.le32-action { min-height: 36px; padding: 8px 14px; border: 1px solid var(--line); border-radius: 8px; background: var(--surface); color: var(--text-2); font-size: 13px; }
.le32-action[disabled] { opacity: .55; cursor: not-allowed; }
.le32-modes { display: flex; flex-wrap: wrap; gap: var(--s-2); margin-top: var(--s-3); }
.le32-mode { flex: 1 1 140px; text-align: left; padding: 10px 12px; border: 1px solid var(--line); border-radius: 8px; background: var(--surface); color: var(--text-2); font-size: 13px; }
.le32-mode strong { display: block; font-size: 13.5px; color: var(--text); }
.le32-mode.is-active { border-color: var(--ink); background: var(--surface-3); }
.le32-settings-foot { margin-top: var(--s-6); }
@media (max-width: 959px) { .le32-settings-grid { grid-template-columns: minmax(0, 1fr); } }
</style>
