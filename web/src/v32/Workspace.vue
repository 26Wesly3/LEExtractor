<script setup>
// v3.2 工作区：文献检索 → 核心文献筛选推荐 → 研究领域概述 → 创新点探索。
// 依据 docs/v3.2/05_UX_UI_SPEC.md「页面结构」「文献卡片」「上传与异常」与
// docs/v3.2/02_USER_WORKFLOW.md。
//
// 关键约束：
//   * 快速本地结果先出现（已获得初步结果），后台增强完成后显示排序已更新；
//   * 翻新排序时保留当前阅读条目的视口锚点，打开的抽屉按 paper_id 保持；
//   * 推荐、空与错误状态各自有独立文案；
//   * 本轮没有校园登录入口与 CAS 调用。
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { createV32Client, errorLine, safeUrl } from './client.js'
import { anchorFrom, coverageLine, emptyStateCopy, enhancementNotice, rankingStageForJob, rankingStageLabel, reasonTextForError, resolvePinned, restoreAnchor, resultState, stageLabel, STAGES } from './states.js'
import { judgementStateLabel, snapshotLabel } from './card.js'
import { EXCLUSION_REASONS } from './states.js'
import { venueRows } from './venue.js'
import { setProjectTitle } from '../shell/project-title.js'
import ExportMenu from './ExportMenu.vue'
import EntryChoice from './EntryChoice.vue'
import PaperCard from './PaperCard.vue'
import SettingsPanels from './SettingsPanels.vue'
import UploadPanel from './UploadPanel.vue'
import ZoteroLocalPanel from './ZoteroLocalPanel.vue'

const props = defineProps({
  client: { type: Object, default: null },
  projectId: { type: String, default: '' },
  stage: { type: String, default: 'anchors' },
  view: { type: String, default: '' },
  lang: { type: String, default: 'zh' },
})

const t = (zh, en) => (props.lang === 'zh' ? zh : en)
const api = props.client || createV32Client()

const page = ref(props.view === 'settings' ? 'settings' : 'workspace')
// S4：阶段由路由驱动。组件不再自己记住阶段，刷新、前进后退和直接粘贴链接都
// 落到同一个阶段；未知阶段（例如旧书签）回落到文献检索。
const activeStage = computed(() => (STAGES.some(stage => stage.key === props.stage) ? props.stage : 'anchors'))
const entryMode = ref('')
const researchText = ref('')
const modelMode = ref('basic')
const project = ref(props.projectId ? { project_id: props.projectId, revision: 0, entry_mode: 'topic' } : null)
const items = ref([])
// 本地期刊缓存（S10）：name/count/ccf_rank，用来给「主要期刊」补分区。
const venueCacheRows = ref([])
const snapshotId = ref('')
const listView = ref('recommended')
const nextCursor = ref(null)
const busy = ref(false)
const error = ref('')
const notice = ref('')
const job = ref(null)
const overview = ref(null)
// S5：详情态由 ?paper= 驱动。刷新、前进后退、直接粘贴链接都能回到同一条记录，
// 抽屉的打开/关闭也就不再是组件私有状态。
// 单测里 Workspace 可以在没有路由的情况下挂载（只测渲染），所以这里允许缺席。
const injectedRoute = useRoute()
const route = injectedRoute || { query: {} }
const pinnedId = ref(String(route.query.paper || ''))
const drawerClosing = ref(false)
let drawerTimer = null
const paperQuery = paperId => {
  const query = { ...route.query }
  if (paperId) query.paper = paperId
  else delete query.paper
  return query
}
const judged = reactive({})
const reasons = reactive({})
// paper_id -> 后端枚举 exclusion_reason（not_relevant / read / duplicate / not_now / unspecified）。
const exclusionCodes = reactive({})
// paper_id -> GET …/venue-metrics 的结果，或 { error }。
const venues = reactive({})
const coverageExpanded = ref(false)
const accessOptions = ref([])
const copyState = ref('')
const importBatch = ref(null)
const client = computed(() => api)

let pollTimer = null
let stopped = false

const LANG = computed(() => props.lang)
const stageTabs = computed(() => STAGES.map(stage => ({ ...stage, label: stageLabel(stage.key, LANG.value) })))
const stageNotice = computed(() => rankingStageLabel(job.value ? rankingStageForJob(job.value, items.value.length > 0) : (items.value.length ? 'refreshed' : 'idle'), LANG.value))
// 任务自己记录的覆盖状态最接近事实（快照里的 coverage 可能是自动扩展轮之前
// 写的，或者为空），所以优先用它，拿不到再退回概览。
const coverage = computed(() => coverageLine(job.value?.coverage || overview.value?.coverage || null, LANG.value))
const degradation = computed(() => enhancementNotice(job.value, LANG.value))
const snapshot = computed(() => snapshotLabel(snapshotId.value, LANG.value))

// 任务进度。以前这里只有一行状态文字，而一次发现任务最长有 45 秒预算，
// 界面在等待期间看上去完全没反应（用户明确反馈「进度条也没有」）。
// 现在：任务未结束就显示进度条；拿不到真实百分比时用不确定态动画，
// 有进度时显示百分比，并遵守 prefers-reduced-motion。
const jobActive = computed(() => !!job.value && ['queued', 'running', 'cancel_requested'].includes(job.value.state))
const jobProgress = computed(() => {
  const raw = job.value?.progress
  // 0 与缺省都按「不确定」处理：显示 0% 会被误读成卡死。
  return typeof raw === 'number' && Number.isFinite(raw) && raw > 0 ? Math.min(1, raw) : null
})
const jobProgressPercent = computed(() => (jobProgress.value === null ? 0 : Math.round(jobProgress.value * 100)))
const JOB_STAGE_COPY = {
  queued: ['排队等待', 'Queued'],
  parsing: ['正在解析文件', 'Parsing files'],
  matching: ['正在匹配文献身份', 'Matching identities'],
  retrieving: ['正在检索来源', 'Retrieving from sources'],
  fast_ranking: ['正在快速排序', 'Fast ranking'],
  enhancing: ['正在完善排序', 'Enhancing the ranking'],
  mapping: ['正在生成研究领域概述', 'Building the research map'],
  exporting: ['正在导出', 'Exporting'],
  finished: ['已结束', 'Finished'],
}
const jobStageCopy = computed(() => {
  const key = job.value?.stage || job.value?.state || ''
  const pair = JOB_STAGE_COPY[key]
  if (pair) return LANG.value === 'zh' ? pair[0] : pair[1]
  return key
})

const recommendedItems = computed(() => items.value.filter(item => item?.ranking?.eligibility === 'recommended'))

const visibleItems = computed(() => {
  if (listView.value === 'excluded') return items.value.filter(item => judgementOf(item) === 'excluded')
  if (listView.value === 'exploration') return items.value.filter(item => judgementOf(item) !== 'excluded' && item?.ranking?.eligibility !== 'recommended')
  if (listView.value === 'all') return items.value
  // 默认视图只显示推荐组；探索列表属于独立视图（doc 05：非门槛文献仍保留）。
  return recommendedItems.value.filter(item => judgementOf(item) !== 'excluded')
})

const state = computed(() => resultState({
  candidateCount: items.value.length,
  recommendationCount: recommendedItems.value.filter(item => judgementOf(item) !== 'excluded').length,
  excludedCount: items.value.filter(item => judgementOf(item) === 'excluded').length,
}))

const emptyCopy = computed(() => emptyStateCopy(state.value, LANG.value))
const pinnedItem = computed(() => resolvePinned(pinnedId.value, items.value))
const pinnedJudgement = computed(() => (pinnedItem.value ? judgementStateLabel(judgementOf(pinnedItem.value), LANG.value) : ''))
// 详情中的综合排序分：只在详情面板出现，且不叫「核心概率」。
const detailScore = computed(() => {
  const value = pinnedItem.value?.ranking?.core_score
  const label = t('综合排序分', 'Ranking score')
  return typeof value === 'number' && Number.isFinite(value) ? `${label}：${value.toFixed(3)}` : `${label}：${t('证据不足', 'insufficient evidence')}`
})

const insight = computed(() => {
  const data = overview.value
  if (!data) return null
  return {
    status: data.status,
    statements: data.statements || [],
    branches: data.branches || [],
    timeline: data.timeline || [],
    trendStatus: data.trend_status,
    trends: data.trends || [],
    leads: data.leads || [],
    scope: data.coverage?.scope || '',
  }
})

/**
 * 主要期刊：只统计检索记录里确实存在的期刊名，不猜影响因子。
 * 记录到的期刊会进本地缓存（GET /venues），缓存自带 CCF 分区（仓库内的
 * ccf_catalog.json，2026 第七版）；影响因子本机没有授权数据源，所以不显示、
 * 也不估算，界面直说原因。
 */
const venueRankIndex = computed(() => {
  const index = new Map()
  for (const row of venueCacheRows.value) {
    const key = normaliseVenue(row.name)
    if (key) index.set(key, { ccf: row.ccf_rank || '', impactFactor: row.impact_factor || '', quartile: row.quartile || '' })
  }
  return index
})

const journalRows = computed(() => {
  const counts = new Map()
  let known = 0
  for (const item of items.value) {
    const paper = item?.paper || item || {}
    const venue = String(paper.venue || '').trim()
    if (!venue) continue
    known += 1
    counts.set(venue, (counts.get(venue) || 0) + 1)
  }
  return [...counts.entries()]
    .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]))
    .slice(0, 8)
    .map(([venue, count]) => {
      const meta = venueRankIndex.value.get(normaliseVenue(venue)) || {}
      return { venue, count, share: known ? count / known : 0, ccf: meta.ccf || '', impactFactor: meta.impactFactor || '', quartile: meta.quartile || '' }
    })
})

function normaliseVenue(name) {
  return String(name || '').toLowerCase().replace(/[^a-z0-9]/g, '')
}

/** 记录里没有期刊名时（预印本/数据库记录），按来源统计，别留空。 */
const sourceRows = computed(() => {
  if (journalRows.value.length) return []
  const counts = new Map()
  let known = 0
  for (const item of items.value) {
    const paper = item?.paper || item || {}
    const records = Array.isArray(paper.source_records) ? paper.source_records : []
    const source = String(
      (records[0] && records[0].provider) || paper.source || paper.provider || '',
    ).trim()
    if (!source) continue
    known += 1
    counts.set(source, (counts.get(source) || 0) + 1)
  }
  return [...counts.entries()]
    .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]))
    .slice(0, 8)
    .map(([source, count]) => ({ source, count, share: known ? count / known : 0 }))
})

// 研究意图预览（doc 02「从研究方向开始」/ R05）。只读：后端目前没有接受
// objects / tasks / methods / exclusions 的更新接口（discover 只收 research_text，
// 且请求体 extra="forbid"），因此这里不提供保存按钮。
const INTENT_FIELDS = [
  { key: 'objects', zh: '研究对象', en: 'Objects' },
  { key: 'tasks', zh: '任务/问题', en: 'Task / question' },
  { key: 'methods', zh: '方法', en: 'Methods' },
  { key: 'constraints', zh: '必要约束', en: 'Constraints' },
  { key: 'exclusions', zh: '排除项', en: 'Exclusions' },
]
const QUERY_SOURCES = {
  original: ['原文', 'Original'],
  rule: ['规则生成', 'Rule-generated'],
  translation: ['译文', 'Translation'],
  model: ['模型生成', 'Model-generated'],
}
const CJK = /[㐀-鿿]/
const intent = computed(() => {
  const anchor = project.value?.anchor
  if (!anchor || !anchor.original_text) return null
  const variants = Array.isArray(anchor.query_variants) ? anchor.query_variants : []
  const fromModel = anchor.intent_origin === 'model_inference' || variants.some(row => row?.source === 'model')
  const language = variants.find(row => row?.source === 'original')?.language || (CJK.test(anchor.original_text) ? 'zh' : 'en')
  return {
    origin: fromModel ? t('AI 解读（模型推断，待你确认）', 'AI reading (model inference, for you to confirm)') : t('规则解读（未调用模型）', 'Rule-based reading (no model called)'),
    fields: INTENT_FIELDS.map(field => {
      const values = Array.isArray(anchor[field.key]) ? anchor[field.key].filter(Boolean) : []
      return { key: field.key, label: t(field.zh, field.en), text: values.length ? values.join('；') : t('未填写', 'Not filled in') }
    }),
    language: language === 'zh' ? t('中文', 'Chinese') : language === 'en' ? t('英文', 'English') : language,
    queries: variants.filter(row => row?.text).map((row, index) => ({
      key: `${row.source}-${index}`,
      source: row.source || 'original',
      sourceLabel: QUERY_SOURCES[row.source] ? t(...QUERY_SOURCES[row.source]) : (row.source || ''),
      text: row.text,
      language: row.language || '',
    })),
  }
})

const reasonChoices = computed(() => EXCLUSION_REASONS.map(row => ({ value: row.value, label: t(row.zh, row.en) })))
const pinnedVenue = computed(() => (pinnedItem.value ? venueRows(venues[pinnedItem.value.paper.paper_id] || null, LANG.value) : []))

function judgementOf(item) {
  return judged[item?.paper?.paper_id] ?? item?.judgement?.value ?? 'unjudged'
}

function statusText(status) {
  return { ready: t('证据充分', 'Evidence sufficient'), partial: t('部分证据', 'Partial evidence'), insufficient_data: t('证据不足', 'Insufficient evidence'), pending: t('有待生成', 'Pending') }[status]
    || t('状态未标注', 'Status not provided')
}

function branchLabel(branch) {
  const origin = branch.label_origin === 'model_inference' ? t('模型推断', 'Model inference') : t('文本统计', 'Text statistics')
  return `${branch.label} · ${origin}`
}

const ACCESS_KINDS = Object.freeze({
  fulltext_link: { zh: '出版社原文', en: 'Publisher full text' },
  resolver: { zh: 'DOI 官方解析', en: 'Official DOI resolver' },
  institution_link: { zh: '机构访问入口', en: 'Institutional entry' },
  metadata_api: { zh: '元数据来源', en: 'Metadata source' },
})

// provider_id -> 人话。之前直接把 provider id（carsi / zju_library / doi）打到界面上；
// 更糟的是 kind 映射把 resolver 写成「图书馆数据库导航」，又把**所有** institution_link
// 都写成「通过学校访问 CARSI」——于是 DOI 解析页和图书馆导航都被说成 CARSI 认证入口，
// 看上去就像一个坏的浙大登录跳转。
const ACCESS_PROVIDERS = Object.freeze({
  doi: { zh: 'DOI 官方解析', en: 'Official DOI resolver' },
  carsi: { zh: 'CARSI 联邦门户', en: 'CARSI federation portal' },
  zju_library: { zh: '浙江大学图书馆数据库导航', en: 'ZJU library database navigation' },
  zju_sciencedirect: { zh: 'ScienceDirect 学校访问说明', en: 'ScienceDirect institutional access guide' },
})

// doc 11：如实说明可用性，不能把「仅提供入口」说成「已授权」。
const ACCESS_AVAILABILITY = Object.freeze({
  public: { zh: '公开获取', en: 'Open access' },
  requires_institution: { zh: '需要机构权限', en: 'Institutional access required' },
  entry_only: { zh: '仅提供检索入口', en: 'Entry point only' },
  unavailable: { zh: '暂不可用', en: 'Unavailable' },
  unknown: { zh: '未核实', en: 'Not verified' },
})

function accessKindLabel(kind) {
  const row = ACCESS_KINDS[kind]
  return row ? (props.lang === 'zh' ? row.zh : row.en) : kind
}

function accessProviderLabel(provider) {
  const row = ACCESS_PROVIDERS[provider]
  return row ? (props.lang === 'zh' ? row.zh : row.en) : (provider || '')
}

function accessAvailabilityLabel(value) {
  const row = ACCESS_AVAILABILITY[value]
  return row ? (props.lang === 'zh' ? row.zh : row.en) : (value || '')
}

/**
 * 「入口类型 · 提供方」，两者相同时只显示一次。
 * DOI 解析的 kind 与 provider 都是「DOI 官方解析」，直接拼会变成
 * 「DOI 官方解析 · DOI 官方解析」。未知 provider 回退成原值，宁可露出原始
 * 标识，也不为了好看把信息藏掉。
 */
function accessTitle(option) {
  const kind = accessKindLabel(option.kind)
  const provider = accessProviderLabel(option.provider)
  if (!provider || provider === kind) return kind
  return `${kind} · ${provider}`
}

function accessHint(option) {
  if (option.kind === 'institution_link') {
    return t(
      '打开后在门户里自行选择学校并登录，再用题名或 DOI 查找。本应用不代你登录，也不读取学校的登录状态。',
      'Choose your institution and sign in on that portal, then find the article by title or DOI. This app does not sign in for you and never reads the campus login state.',
    )
  }
  return t('打开后可在目标页面用题名或 DOI 查找。', 'Find the article on the target page by title or DOI.')
}

/** 出版社原文 → DOI 解析 → 机构入口，顺序稳定，便于用户按序尝试。 */
const orderedAccessOptions = computed(() => {
  const order = { fulltext_link: 0, resolver: 1, institution_link: 2, metadata_api: 3 }
  return [...accessOptions.value].sort((a, b) => (order[a.kind] ?? 9) - (order[b.kind] ?? 9))
})

function paperTitle(paperId) {
  const row = items.value.find(item => item?.paper?.paper_id === paperId)
  return row?.paper?.title || paperId
}

async function ensureSession() {
  try { await api.session() } catch (problem) { error.value = errorLine(problem, t('会话初始化失败。', 'The session could not be initialised.')) }
}

async function loadProject() {
  if (!props.projectId) return
  try {
    const data = await api.getProject(props.projectId)
    project.value = data
    setProjectTitle(data?.name || '')
    entryMode.value = data?.entry_mode || entryMode.value
    // AC-11: a refresh resumes the most recent job that is still running.
    const active = Array.isArray(data?.active_job_ids) ? data.active_job_ids : []
    if (active.length) pollJob(active[active.length - 1])
  } catch (problem) {
    error.value = errorLine(problem, t('未能读取项目。', 'Could not read the project.'))
  }
}

async function loadPapers({ view = listView.value, cursor = null, keepAnchor = true } = {}) {
  if (!props.projectId) return
  const container = listHost.value
  const anchor = keepAnchor ? anchorFrom(container, pinnedId.value) : null
  busy.value = true
  try {
    const data = await api.papers(props.projectId, { view: view === 'excluded' ? 'all' : view, cursor, limit: 20 })
    const rows = Array.isArray(data?.items) ? data.items : []
    items.value = cursor ? [...items.value, ...rows] : rows
    snapshotId.value = data?.snapshot_id || snapshotId.value
    nextCursor.value = data?.next_cursor || null
    if (project.value && typeof data?.revision === 'number') project.value = { ...project.value, revision: data.revision }
    rows.forEach(row => {
      const id = row?.paper?.paper_id
      if (id && row?.judgement?.value) judged[id] = row.judgement.value
      if (id && row?.judgement?.exclusion_reason) exclusionCodes[id] = row.judgement.exclusion_reason
    })
    loadVenues(rows)
    error.value = ''
    if (anchor) { await nextTick(); restore(anchor) }
  } catch (problem) {
    if (problem?.code === 'cursor_stale') {
      notice.value = reasonTextForError(problem, LANG.value)
      nextCursor.value = null
      await loadPapers({ view, cursor: null, keepAnchor: false })
    } else {
      error.value = reasonTextForError(problem, LANG.value)
    }
  } finally {
    busy.value = false
  }
}

const listHost = ref(null)

/** 期刊指标按 paper_id 缓存；读取失败记录原因，不影响列表。 */
async function loadVenues(rows) {
  if (typeof api.venueMetrics !== 'function') return
  const ids = rows.map(row => row?.paper?.paper_id).filter(id => id && !(id in venues))
  await Promise.all(ids.map(async id => {
    venues[id] = null
    try { venues[id] = await api.venueMetrics(props.projectId, id) } catch (problem) {
      venues[id] = { error: errorLine(problem, t('请求未完成', 'request failed')) }
    }
  }))
}

function anchorNow() { return anchorFrom(listHost.value, pinnedId.value) }

function restore(anchor) {
  if (!anchor) return
  restoreAnchor(listHost.value, anchor)
}

async function startDiscovery() {
  if (!props.projectId || !researchText.value.trim()) return
  busy.value = true
  notice.value = ''
  try {
    const accepted = await api.discover(props.projectId, {
      expected_revision: project.value?.revision ?? 0,
      research_text: researchText.value.trim(),
      seed_paper_ids: [],
      mode: modelMode.value,
    })
    job.value = { job_id: accepted?.job_id, state: accepted?.state || 'queued', stage: 'queued' }
    await loadPapers({ keepAnchor: false })
    pollJob(accepted?.job_id)
  } catch (problem) {
    error.value = reasonTextForError(problem, LANG.value)
  } finally {
    busy.value = false
  }
}

function stopPolling() {
  if (pollTimer !== null) { clearInterval(pollTimer); pollTimer = null }
}

function pollJob(jobId) {
  stopPolling()
  if (!jobId || typeof setInterval !== 'function') return
  const tick = async () => {
    if (stopped) return
    try {
      const next = await api.job(props.projectId, jobId)
      if (stopped) return
      job.value = next
      const active = ['queued', 'running', 'cancel_requested'].includes(next?.state)
      if (!active) {
        stopPolling()
        // 「继续发现」时后端会用 discover 取代 expand（superseded）。此时不能就
        // 此停住：跟到这个项目当前真正在跑的任务上，否则界面会一直停在旧列表，
        // 看起来像"点了开始没反应"。
        if (next?.state === 'superseded' || next?.state === 'interrupted') {
          await loadProject()
          if (pollTimer !== null) return
        }
        const anchor = anchorNow()
        await loadPapers({ keepAnchor: false })
        restore(anchor)
        await loadOverview()
      }
    } catch { /* 轮询失败保留既有结果，不打断阅读 */ }
  }
  pollTimer = setInterval(tick, 1200)
  // First check right away, so a refreshed page shows the running job at once.
  tick()
}

async function loadOverview() {
  if (!props.projectId) return
  try { overview.value = await api.overview(props.projectId) } catch { overview.value = overview.value }
  await loadVenueCache()
}

/** S10：本地期刊缓存（出现次数 + CCF 分区），拿不到就退回按本批统计。 */
async function loadVenueCache() {
  if (!props.projectId) return
  try {
    const data = await api.venueCache(200)
    venueCacheRows.value = Array.isArray(data?.items) ? data.items : []
  } catch {
    venueCacheRows.value = []
  }
}

/** 导入批次完成后刷新语料与概览；面板本身的逐项状态由 UploadPanel 负责。 */
function onImportBatch(batch) { importBatch.value = batch || null }

async function onImportSettled() {
  await loadPapers({ keepAnchor: false })
  await loadOverview()
}

/** S11：从本机 Zotero 导入完成后，语料和概览都要跟着刷新。 */
async function onZoteroImported(result) {
  notice.value = t(
    `已从本机 Zotero 导入 ${result?.imported ?? 0} 条。`,
    `Imported ${result?.imported ?? 0} items from the local Zotero.`,
  )
  await loadProject()
  await loadPapers({ keepAnchor: false })
  await loadOverview()
}

/** S11：Zotero 用侧栏接入，开关写在 URL 里（和 ?paper= 同一套做法）。 */
const zoteroOpen = computed(() => Boolean(injectedRoute && route.query.zotero === '1'))

/** Zotero 插件点过来时带 ?item=<条目键>，侧栏加载完自动勾上它。 */
const zoteroPreselect = computed(() => String((injectedRoute && route.query.item) || ''))

function openZotero() {
  if (!injectedRoute) return
  router.push({ query: { ...route.query, zotero: '1' } })
}

function closeZotero() {
  if (!injectedRoute) return
  const query = { ...route.query }
  delete query.zotero
  router.push({ query })
}

async function judge({ paperId, value, reason, exclusionReason }) {
  if (!paperId) return
  const before = judged[paperId]
  const beforeCode = exclusionCodes[paperId]
  judged[paperId] = value
  if (value === 'excluded' && reason !== undefined) reasons[paperId] = reason
  if (value === 'excluded') exclusionCodes[paperId] = exclusionReason || exclusionCodes[paperId] || 'unspecified'
  try {
    const saved = await api.judge(props.projectId, {
      expected_revision: project.value?.revision ?? 0,
      paper_id: paperId,
      judgement: value,
      // 排除默认仅隐藏并禁止自动扩展；只有原因 not_relevant 才进入负反馈。
      // 非排除判断必须是 unspecified（store.set_judgement 会拒绝其他值）。
      exclusion_reason: value === 'excluded' ? exclusionCodes[paperId] : 'unspecified',
      reason: value === 'excluded' ? (reasons[paperId] || '') : '',
    }, { scope: paperId })
    if (saved?.revision !== undefined && project.value) project.value = { ...project.value, revision: saved.revision }
    notice.value = value === 'unjudged' ? t('已撤销判断。', 'Judgement undone.') : t('判断已保存，后台按需重排。', 'Judgement saved; the ranking is refreshed in the background when needed.')
  } catch (problem) {
    judged[paperId] = before
    exclusionCodes[paperId] = beforeCode
    if (problem?.code === 'revision_conflict') {
      error.value = reasonTextForError(problem, LANG.value)
      await loadProject()
    } else {
      error.value = reasonTextForError(problem, LANG.value)
    }
  }
}

function openPaper(paperId) {
  clearTimeout(drawerTimer)
  drawerClosing.value = false
  pinnedId.value = paperId
  accessOptions.value = []
  copyState.value = ''
  loadAccessOptions(paperId)
  // S5：详情进 URL，刷新和前进后退都能回到同一条记录。
  if (injectedRoute && String(route.query.paper || '') !== paperId) router.replace({ query: paperQuery(paperId) })
}

function closePaper() {
  if (!pinnedId.value && !drawerClosing.value) return
  drawerClosing.value = true
  clearTimeout(drawerTimer)
  drawerTimer = setTimeout(() => { drawerClosing.value = false; pinnedId.value = '' }, 260)
  if (injectedRoute && route.query.paper) router.replace({ query: paperQuery('') })
}

/** 校园资源访问是独立动作：只读取入口，不发起任何校园登录调用。 */
async function loadAccessOptions(paperId) {
  try {
    const data = await api.accessOptions(props.projectId, paperId)
    accessOptions.value = (Array.isArray(data?.items) ? data.items : []).filter(option => safeUrl(option?.url))
  } catch {
    accessOptions.value = []
  }
}

async function copyCitation() {
  const paper = pinnedItem.value?.paper
  if (!paper) return
  const text = [paper.title, paper.doi || ''].filter(Boolean).join('\n')
  try {
    if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) await navigator.clipboard.writeText(text)
    copyState.value = t('已复制题名与 DOI。', 'Title and DOI copied.')
  } catch {
    copyState.value = t('浏览器未允许复制，请手动选取。', 'The browser blocked copying; select the text manually.')
  }
}

async function cancelJob() {
  if (!job.value?.job_id) return
  try {
    job.value = await api.cancelJob(props.projectId, job.value.job_id)
    notice.value = t('已请求取消；已提交的候选与判断保留。', 'Cancellation requested; committed candidates and judgements are preserved.')
  } catch (problem) {
    error.value = reasonTextForError(problem, LANG.value)
  }
}

async function continueDiscovery() {
  if (!props.projectId) return
  busy.value = true
  try {
    const accepted = await api.expand(props.projectId, { expected_revision: project.value?.revision ?? 0 })
    job.value = { job_id: accepted?.job_id, state: accepted?.state || 'queued', stage: 'queued' }
    pollJob(accepted?.job_id)
  } catch (problem) {
    error.value = reasonTextForError(problem, LANG.value)
  } finally {
    busy.value = false
  }
}

// 入口选择：**先创建项目，再进入文献检索**。
//
// 这里曾经只写 `entryMode.value = mode; activeStage.value = 'anchors'`：在
// /v32（路由里没有 projectId）下 Workspace 会渲染入口选择，于是两个主入口
// 变成死按钮——不发任何请求、不跳转，界面看上去就是"点不开"。入口选择的语义
// 是"建立项目并开始"，因此必须走 API 并路由到该项目，与 App.vue 的
// startV32Entry 保持一致。
//
// EntryChoice 通过 emit 传的是 `{ mode }` 对象；这里同时兼容字符串，避免把对象
// 写进 entryMode 而在界面上渲染成 [object Object]。
const router = useRouter()

async function openEntry(payload) {
  if (busy.value) return
  const requested = typeof payload === 'string' ? payload : payload?.mode
  const chosen = requested === 'pdf' ? 'pdf' : 'topic'
  entryMode.value = chosen
  // 阶段由路由决定：下面 push 到 /projects/:id/anchors，无需在这里改本地状态。
  error.value = ''
  notice.value = ''
  busy.value = true
  try {
    await api.session()
    const created = await api.createProject({
      name: chosen === 'pdf'
        ? t('已有文献项目', 'Existing literature project')
        : t('研究方向项目', 'Research direction project'),
      entry_mode: chosen,
    })
    await router.push(`/projects/${encodeURIComponent(created.project_id)}/anchors`)
  } catch (failure) {
    // 失败只显示一行状态，不阻断页面其余内容。
    error.value = errorLine(
      failure,
      t('未能创建项目，请确认本地服务仍在运行。', 'The project could not be created; check that the local service is running.'),
    )
  } finally {
    busy.value = false
  }
}

async function loadMore() {
  if (!nextCursor.value) return
  await loadPapers({ cursor: nextCursor.value, keepAnchor: true })
}

watch(() => props.projectId, async value => {
  if (!value) return
  await ensureSession()
  await loadProject()
  await loadPapers({ keepAnchor: false })
  await loadOverview()
})

watch(() => props.view, value => { page.value = value === 'settings' ? 'settings' : 'workspace' })

// 进入某个阶段时读取该阶段所需的事实（读取幂等，不改动判断）。
watch(activeStage, async value => {
  if (!props.projectId) return
  if (value === 'papers') await loadPapers({ keepAnchor: false })
  if (value === 'threads' || value === 'insight') await loadOverview()
})

// 外部导航（后退、粘贴 ?paper=）同步抽屉；关掉 query 时走同一套退场。
watch(() => route.query.paper, value => {
  const wanted = String(value || '')
  if (wanted === pinnedId.value) return
  if (!wanted) {
    if (pinnedId.value) closePaper()
    return
  }
  clearTimeout(drawerTimer)
  drawerClosing.value = false
  pinnedId.value = wanted
  accessOptions.value = []
  copyState.value = ''
  loadAccessOptions(wanted)
})

/** ?export=1 打开导出菜单：它是 <details>，用属性打开并滚到可见处。 */
async function openExportIfRequested() {
  if (String(route.query.export || '') !== '1') return
  await nextTick()
  const nodes = [...document.querySelectorAll('[data-v32-export]')]
  const target = nodes.find(node => node.offsetParent !== null) || nodes[0]
  if (!target) return
  target.open = true
  target.scrollIntoView({ block: 'center' })
  const summary = target.querySelector('summary')
  if (summary && typeof summary.focus === 'function') summary.focus()
}

watch(() => route.query.export, () => { openExportIfRequested() })

onBeforeUnmount(() => { stopped = true; stopPolling(); clearTimeout(drawerTimer) })

/** 测试与父级集成使用：纯状态访问，不改变既有行为。 */
defineExpose({
  pollJob,
  loadPapers,
  loadOverview,
  judge,
  job,
  items,
  lastCursor: () => nextCursor.value,
  currentState: () => state.value,
  pinned: () => pinnedId.value,
})

if (props.projectId) {
  ensureSession().then(() => { loadProject(); loadPapers({ keepAnchor: false }); loadOverview() }).then(() => openExportIfRequested())
}
</script>

<template>
  <div class="le32-workspace" data-v32-workspace>
    <SettingsPanels v-if="page === 'settings'" :client="client" :lang="lang" />

    <template v-else>
      <template v-if="!projectId">
        <EntryChoice :lang="lang" :busy="busy" :error="error" @select="openEntry" />
        <UploadPanel v-if="entryMode === 'pdf'" class="le32-upload-inline" :client="client" :lang="lang" :project-id="projectId" :expected-revision="0" />
      </template>

      <template v-else>
        <div
          v-if="jobActive"
          class="le32-progress"
          data-v32-progress
          role="progressbar"
          :aria-label="t('任务进度', 'Job progress')"
          aria-valuemin="0"
          aria-valuemax="100"
          :aria-valuenow="jobProgress === null ? null : jobProgressPercent"
          :aria-valuetext="jobStageCopy + (jobProgress === null ? '' : ' ' + jobProgressPercent + '%')"
        >
          <div class="le32-progress-head">
            <span data-v32-progress-stage>{{ jobStageCopy }}</span>
            <span v-if="jobProgress !== null" data-v32-progress-percent>{{ jobProgressPercent }}%</span>
          </div>
          <div class="le32-progress-track" :class="{ 'is-indeterminate': jobProgress === null }">
            <div class="le32-progress-fill" :style="jobProgress === null ? {} : { width: jobProgressPercent + '%' }"></div>
          </div>
        </div>

        <p v-if="stageNotice" class="le32-ranking-stage" data-v32-ranking-stage role="status">{{ stageNotice }}</p>

        <template v-if="activeStage === 'anchors'">
          <section v-if="project?.entry_mode === 'pdf'" class="le32-anchor" data-v32-anchor data-v32-anchor-import>
            <h2>{{ t('文献检索', 'Literature search') }}</h2>
            <p class="muted">{{ t('上传的 PDF 逐文件解析与匹配；已识别或已确认为本地文献的文件作为种子继续发现。','Each uploaded PDF is parsed and matched; files that matched or continued as local seeds become discovery seeds.') }}</p>
            <UploadPanel
              :client="client"
              :lang="lang"
              :project-id="projectId"
              :expected-revision="project?.revision ?? 0"
              @batch="onImportBatch"
              @settled="onImportSettled"
            />
            <!-- 「从已有文献开始」不只有上传 PDF 一条路：本机 Zotero 里的条目也是已有文献。 -->
            <div class="le32-import-options" data-v32-import-options>
              <p class="muted">{{ t('或者：把本机 Zotero 里的条目直接导进来。','Or: import items straight from your local Zotero library.') }}</p>
              <button type="button" class="le32-action" data-v32-zotero-import-start @click="openZotero">{{ t('从 Zotero 导入文献', 'Import literature from Zotero') }}</button>
            </div>
          </section>
          <section v-else class="le32-anchor" data-v32-anchor>
            <h2>{{ t('文献检索', 'Literature search') }}</h2>
            <template v-if="project?.anchor?.original_text">
              <p class="muted">{{ t('已保存的研究意图','The saved research intent') }}</p>
              <p data-v32-anchor-text>{{ project.anchor.original_text }}</p>
              <div v-if="intent" class="le32-intent" data-v32-intent>
                <div class="le32-intent-head">
                  <h3>{{ t('研究意图预览', 'Research intent preview') }}</h3>
                  <span class="badge badge--system" data-v32-intent-origin>{{ intent.origin }}</span>
                </div>
                <dl class="le32-intent-grid">
                  <div v-for="field in intent.fields" :key="field.key" :data-v32-intent-field="field.key"><dt>{{ field.label }}</dt><dd>{{ field.text }}</dd></div>
                  <div data-v32-intent-field="language"><dt>{{ t('语言', 'Language') }}</dt><dd>{{ intent.language }}</dd></div>
                </dl>
                <template v-if="intent.queries.length">
                  <h4>{{ t('实际检索式', 'Queries sent') }}</h4>
                  <ul class="brief-list" data-v32-intent-queries>
                    <li v-for="query in intent.queries" :key="query.key" :data-v32-intent-query="query.source"><span class="muted">{{ query.sourceLabel }}<template v-if="query.language"> · {{ query.language }}</template>：</span>{{ query.text }}</li>
                  </ul>
                </template>
                <p class="muted" data-v32-intent-edit-note>{{ t('暂不支持在此编辑这些字段：当前接口只接受研究方向原文。缺失的字段保持为空，不会自动补写。', 'These fields cannot be edited here yet: the current interface only accepts the original research text. Missing fields stay empty and are never filled in automatically.') }}</p>
              </div>
            </template>
            <template v-else>
              <label class="field-label" for="v32-research-text">{{ t('输入研究方向', 'Enter a research direction') }}</label>
              <textarea id="v32-research-text" v-model="researchText" rows="4" data-v32-research-text :placeholder="t('研究对象、任务与约束；缺失的字段可以留空。','Objects, tasks and constraints; missing fields may stay empty.')" />
              <div class="le32-mode-row">
                <button type="button" class="le32-mode" :class="{ 'is-active': modelMode === 'basic' }" data-v32-mode="basic" @click="modelMode = 'basic'">{{ t('基础模式', 'Basic mode') }}</button>
                <button type="button" class="le32-mode" :class="{ 'is-active': modelMode === 'ai_enhanced' }" data-v32-mode="ai_enhanced" @click="modelMode = 'ai_enhanced'">{{ t('AI 增强', 'AI enhanced') }}</button>
              </div>
              <p class="muted" data-v32-mode-note>{{ modelMode === 'ai_enhanced' ? t('将把题名与摘要等元数据发送给配置的模型服务。','Titles and abstracts are sent to the configured model service.') : t('不调用任何远程模型。','No remote model is called.') }}</p>
              <button type="button" class="le32-primary" :disabled="busy || !researchText.trim()" data-v32-discover @click="startDiscovery">{{ t('开始发现核心文献', 'Discover core literature') }}</button>
            </template>
          </section>
        </template>

        <template v-else-if="activeStage === 'papers'">
          <section class="le32-papers" data-v32-papers>
            <div class="le32-papers-head">
              <h2>{{ t('核心文献筛选推荐', 'Core literature: screening and recommendations') }}</h2>
              <span class="muted" data-v32-snapshot>{{ snapshot }}</span>
              <button type="button" class="le32-action" data-v32-zotero-open="papers" @click="openZotero">{{ t('从本机 Zotero 导入', 'Import from local Zotero') }}</button>
              <ExportMenu :client="client" :project-id="projectId" :lang="lang" />
            </div>
            <div class="le32-view-row">
              <button v-for="option in ['recommended', 'exploration', 'excluded', 'all']" :key="option" type="button" class="le32-view" :class="{ 'is-active': listView === option }" :data-v32-view="option" @click="listView = option">
                {{ { recommended: t('推荐','Recommended'), exploration: t('探索','Exploration'), excluded: t('已排除','Excluded'), all: t('全部','All') }[option] }}
              </button>
              <button type="button" class="le32-action" :disabled="busy" data-v32-continue @click="continueDiscovery">{{ t('基于当前文献继续发现', 'Continue discovery from the current records') }}</button>
              <button v-if="job && ['queued','running','cancel_requested'].includes(job.state)" type="button" class="le32-action" data-v32-cancel @click="cancelJob">{{ t('取消', 'Cancel') }}</button>
            </div>

            <p v-if="coverage" class="le32-coverage" data-v32-coverage role="status">
              {{ coverage.text }}
              <button type="button" class="le32-link" data-v32-coverage-details :aria-expanded="coverageExpanded ? 'true' : 'false'" @click="coverageExpanded = !coverageExpanded">{{ t('查看详情', 'Details') }}</button>
            </p>
            <p v-if="coverage && coverageExpanded" class="muted" data-v32-coverage-detail>{{ coverage.detail }}</p>

            <p v-if="degradation" class="le32-degraded" data-v32-degraded role="status">{{ degradation.text }} — {{ degradation.detail }}</p>
            <p v-if="notice" class="muted" data-v32-notice role="status">{{ notice }}</p>
            <p v-if="error" class="error-text" data-v32-error role="alert">{{ error }}</p>

            <ol v-if="visibleItems.length" ref="listHost" class="le32-list" data-v32-list>
              <PaperCard
                v-for="(item, index) in visibleItems"
                :key="item.paper.paper_id"
                :item="item"
                :index="index"
                :lang="lang"
                :snapshot-id="snapshotId"
                :busy="busy"
                :pinned="pinnedId === item.paper.paper_id"
                :venue="venues[item.paper.paper_id] || null"
                @open="openPaper"
                @judge="judge"
              />
            </ol>
            <div v-else class="empty" data-v32-empty>
              <p data-v32-empty-copy>{{ emptyCopy.text }}</p>
              <!-- 导入/上传进来的条目没有主题信号，落在「探索」而不是「推荐」；
                   默认视图空着会让人以为没导入成功。 -->
              <p v-if="listView === 'recommended'" class="muted" data-v32-empty-view-hint>
                {{ t('「推荐」只放有主题相关证据的条目。你导入或上传的文献在「探索」和「全部」里。', 'Recommended only lists items with topic evidence. Imported or uploaded records are under Exploration and All.') }}
                <button type="button" class="le32-link" data-v32-empty-view-all @click="listView = 'all'">{{ t('看全部', 'See all') }}</button>
              </p>
              <button type="button" class="le32-primary" :disabled="busy" data-v32-empty-action @click="continueDiscovery">{{ emptyCopy.action }}</button>
            </div>

            <button v-if="nextCursor" type="button" class="le32-action" data-v32-more :disabled="busy" @click="loadMore">{{ t('读取更多', 'Load more') }}</button>
          </section>
        </template>

        <template v-else-if="activeStage === 'threads'">
          <section class="le32-threads" data-v32-threads>
            <h2>{{ t('研究领域概述', 'Field overview') }}</h2>
            <p v-if="!insight" class="muted">{{ t('研究工作区尚未生成分支结构。','No branch structure has been generated for this project yet.') }}</p>
            <template v-else>
              <div v-if="insight.branches.length" class="le32-branch-grid">
                <article v-for="branch in insight.branches" :key="branch.branch_id" class="le32-branch" :data-v32-branch="branch.branch_id">
                  <h3>{{ branch.label }}</h3>
                  <p class="muted">{{ branchLabel(branch) }} · {{ branch.paper_ids.length }} {{ t('篇','records') }}</p>
                  <p v-if="branch.method" class="muted">{{ branch.method }}</p>
                  <ul class="brief-list">
                    <li v-for="paperId in branch.representative_paper_ids" :key="paperId">{{ paperTitle(paperId) }}</li>
                  </ul>
                </article>
              </div>
              <p v-else class="empty" data-v32-threads-empty>{{ t('这批文献之间还没有足够的引用/共引关系，做不出分支聚类。先看这批文献本身说明了什么：','This set does not yet carry enough citation or co-citation links to cluster into branches. What the set itself says:') }}</p>
              <ul v-if="!insight.branches.length && insight.statements.length" class="brief-list le32-thread-facts" data-v32-threads-facts>
                <li v-for="row in insight.statements" :key="row.text">{{ row.text }}</li>
              </ul>
              <h3>{{ t('主要期刊', 'Main venues') }}</h3>
              <ul v-if="journalRows.length" class="brief-list" data-v32-journals>
                <li v-for="row in journalRows" :key="row.venue">{{ row.venue }} · {{ row.count }} {{ t('篇', 'records') }}（{{ Math.round(row.share * 100) }}%）<span v-if="row.ccf" class="badge" :data-v32-venue-rank="row.venue">CCF {{ row.ccf }}</span><span v-if="row.impactFactor" class="badge" :data-v32-venue-if="row.venue">IF {{ row.impactFactor }}<template v-if="row.quartile"> · {{ row.quartile }}</template></span></li>
              </ul>
              <ul v-else-if="sourceRows.length" class="brief-list" data-v32-sources>
                <li v-for="row in sourceRows" :key="row.source">{{ row.source }} · {{ row.count }} {{ t('篇', 'records') }}（{{ Math.round(row.share * 100) }}%）</li>
              </ul>
              <p v-else class="muted" data-v32-journals-empty>{{ t('检索记录里没有期刊字段，无法统计。','The retrieved records carry no venue field, so there is nothing to count.') }}</p>
              <p v-if="!journalRows.length && sourceRows.length" class="muted">{{ t('这些记录来自预印本/数据库，没有期刊名，所以先按来源统计。','These records come from preprints/databases and carry no journal name, so they are counted by source.') }}</p>
              <p class="muted le32-venue-note" data-v32-venue-note>{{ t('期刊名与篇数来自本项目的检索记录，并会存进本地期刊缓存（换项目也能查）；CCF 分区来自仓库自带的 2026 第七版目录（只覆盖计算机领域）。影响因子可选：把期刊表用 scripts/import_journal_csv.py 导入本机缓存后，这里会显示 IF 与分区；没有导入就不显示，也不估算。','Venue names and counts come from this project and are kept in a local venue cache; CCF ranks come from the bundled 2026 catalogue (computer science only). Impact factors are optional: import a journal table with scripts/import_journal_csv.py and they appear here; without an import they are neither shown nor estimated.') }}</p>
              <h3>{{ t('时间线', 'Timeline') }}</h3>
              <ul class="le32-timeline" data-v32-timeline>
                <li v-for="point in insight.timeline" :key="point.year === null ? 'unknown' : point.year">{{ point.year === null ? t('年份未知', 'Year unknown') : point.year }} · {{ point.count }}</li>
              </ul>
              <h3>{{ t('检索集内活跃方向', 'Active directions inside the retrieved set') }}</h3>
              <ul v-if="insight.trends.length" class="brief-list" data-v32-trends>
                <li v-for="trend in insight.trends" :key="trend.branch_id">{{ t('近三年', 'Last 3 years') }} {{ trend.recent_count }} · {{ t('更早', 'Earlier') }} {{ trend.prior_count }}</li>
              </ul>
              <p v-else class="muted" data-v32-trends-empty>{{ t('暂不足以判断趋势。','There is not enough information to judge a trend.') }}</p>
            </template>
          </section>
        </template>

        <template v-else>
          <section class="le32-insight" data-v32-insight>
            <div class="le32-papers-head">
              <h2>{{ t('创新点探索', 'Innovation exploration') }}</h2>
              <ExportMenu :client="client" :project-id="projectId" :lang="lang" />
            </div>
            <p v-if="!insight" class="muted">{{ t('概览尚未生成。','The overview has not been generated yet.') }}</p>
            <template v-else>
              <p class="muted"><strong>{{ t('研究范围', 'Research scope') }}</strong> {{ insight.scope || t('当前检索集','the current retrieved set') }} · {{ statusText(insight.status) }}</p>
              <div class="le32-priority">
                <h3>{{ t('优先阅读', 'Priority reading') }}</h3>
                <ul class="brief-list" data-v32-priority>
                  <li v-for="item in recommendedItems.slice(0, 5)" :key="item.paper.paper_id">
                    <button type="button" class="le32-link" @click="openPaper(item.paper.paper_id)">{{ item.paper.title }}</button>
                  </li>
                </ul>
              </div>
              <template v-if="insight.statements.length">
                <h3>{{ t('依据与结论', 'Statements with evidence') }}</h3>
                <ul class="le32-statements" data-v32-statements>
                  <li v-for="(statement, index) in insight.statements" :key="index" :data-v32-statement="statement.type">
                    <p>{{ statement.text }}</p>
                    <p class="muted">{{ statement.type === 'fact' ? t('事实','Fact') : statement.type === 'hypothesis' ? t('待验证假设','Hypothesis') : t('推断','Inference') }} · {{ statement.scope }}<span v-if="statement.limitations.length"> · {{ statement.limitations.join('；') }}</span></p>
                  </li>
                </ul>
              </template>
              <h3>{{ t('潜在研究切入点', 'Potential research entry points') }}</h3>
              <ul v-if="insight.leads.length" class="le32-leads" data-v32-leads>
                <li v-for="(lead, index) in insight.leads" :key="index" class="le32-lead">
                  <p data-v32-lead-text>{{ lead.text }}</p>
                  <p class="muted">{{ t('理由','Reason') }}：{{ lead.reason }}</p>
                  <p class="muted">{{ t('支持论文','Supporting papers') }}：{{ lead.paper_ids.map(paperTitle).join('、') }}</p>
                  <p class="muted">{{ t('最邻近已有研究','Nearest existing work') }}：{{ (lead.nearest_work_ids || []).map(paperTitle).join('、') || t('未记录','Not recorded') }}</p>
                  <p class="muted">{{ t('建议检索式','Suggested queries') }}：{{ (lead.next_queries || []).join('；') }}</p>
                  <p class="muted">{{ lead.verification_status === 'checked_in_current_sources' ? t('已在当前来源中检索','Checked against the current sources') : t('需要进一步核查','Needs verification') }} · {{ lead.coverage_scope }}</p>
                </li>
              </ul>
              <p v-else class="muted" data-v32-leads-empty>{{ t('证据不足，暂不提出研究切入点。','Evidence is insufficient, so no research entry point is proposed.') }}</p>
              <details class="le32-insufficient" data-v32-insufficient>
                <summary>{{ t('证据不足项', 'Items with insufficient evidence') }}</summary>
                <ul class="brief-list">
                  <li v-for="(statement, index) in insight.statements.filter(row => row.type !== 'fact')" :key="index">{{ statement.text }}</li>
                </ul>
              </details>
            </template>
          </section>
        </template>
      </template>
    </template>

    <aside v-if="pinnedItem || drawerClosing" class="le32-drawer" :class="{ 'is-open': !!pinnedItem && !drawerClosing, 'is-closing': drawerClosing }" data-v32-drawer role="dialog" :aria-label="t('文献详情','Record details')">
      <div class="le32-drawer-head">
        <span class="eyebrow">{{ t('文献详情', 'Record details') }}</span>
        <button type="button" class="le32-action" data-v32-drawer-close @click="closePaper">{{ t('关闭', 'Close') }}</button>
      </div>
      <h3>{{ pinnedItem.paper.title }}</h3>
      <p class="paper-meta">{{ pinnedItem.paper.year || t('年份未知','Year unknown') }} · {{ pinnedItem.paper.citation_count === null || pinnedItem.paper.citation_count === undefined ? t('被引次数未知','Citations unknown') : pinnedItem.paper.citation_count }}</p>
      <p v-if="pinnedJudgement" class="badge badge--human" data-v32-drawer-judgement>{{ pinnedJudgement }}</p>
      <p v-else class="badge badge--system" data-v32-drawer-system>{{ t('算法推荐核心', 'Recommended by the ranking') }}</p>
      <p class="muted">{{ snapshot }}</p>

      <h4>{{ t('出版源指标', 'Venue metrics') }}</h4>
      <ul class="brief-list le32-venue" data-v32-venue>
        <li v-for="row in pinnedVenue" :key="row.type" :data-v32-venue-metric="row.type">
          <strong>{{ row.label }}</strong>：{{ row.value }}<span v-if="row.source" class="muted"> · {{ t('来源','Source') }}：{{ row.source }}</span><span v-if="row.reason" class="muted"> · {{ row.reason }}</span>
        </li>
      </ul>

      <h4>{{ t('获取全文', 'Get the full text') }}</h4>
      <p class="muted" data-v32-access-note>{{ t('按顺序尝试：先看出版社原文或 DOI 官方解析，再走机构入口。打开链接只代表进入入口页，不代表已获得授权；本应用不会代你登录，也不会读取学校的登录状态。','Try in order: the publisher page or the official DOI resolver first, then an institutional entry. Opening a link only opens an entry point and does not mean access was granted; this app does not sign in for you and never reads the campus login state.') }}</p>
      <ul v-if="accessOptions.length" class="le32-access" data-v32-access>
        <li v-for="option in orderedAccessOptions" :key="option.url" :data-v32-access-option="option.kind">
          <div class="le32-access-head">
            <a :href="safeUrl(option.url)" target="_blank" rel="noopener noreferrer">{{ accessTitle(option) }}</a>
            <span class="le32-access-badge" :data-access-availability="option.availability">{{ accessAvailabilityLabel(option.availability) }}</span>
          </div>
          <p class="muted le32-access-hint">{{ accessHint(option) }}</p>
        </li>
      </ul>
      <p v-else class="muted" data-v32-access-empty>{{ t('尚未取得可用的访问入口。','No access entry point is available yet.') }}</p>
      <button type="button" class="le32-action" data-v32-copy-citation @click="copyCitation">{{ t('复制题名与 DOI', 'Copy title and DOI') }}</button>
      <p v-if="copyState" class="muted" data-v32-copy-state role="status">{{ copyState }}</p>

      <template v-if="judgementOf(pinnedItem) === 'excluded'">
        <h4>{{ t('排除原因', 'Exclusion reason') }}</h4>
        <select
          :value="exclusionCodes[pinnedItem.paper.paper_id] || 'unspecified'"
          data-v32-exclusion-code
          :aria-label="t('排除原因','Exclusion reason')"
          @change="judge({ paperId: pinnedItem.paper.paper_id, value: 'excluded', exclusionReason: $event.target.value })"
        >
          <option v-for="option in reasonChoices" :key="option.value" :value="option.value">{{ option.label }}</option>
        </select>
        <p class="muted">{{ t('只有「不相关」会作为负反馈影响排序；其余原因只隐藏这篇文献。', 'Only “Not relevant” is used as negative feedback; the other reasons only hide the record.') }}</p>
        <h4>{{ t('排除说明（可选）', 'Exclusion note (optional)') }}</h4>
        <input v-model="reasons[pinnedItem.paper.paper_id]" type="text" :placeholder="t('不填写时按未指定处理','Left blank it stays unspecified')" data-v32-exclusion-reason />
      </template>

      <h4>{{ t('排序依据', 'Ranking basis') }}</h4>
      <p class="muted" data-v32-detail-score>{{ detailScore }}</p>
    </aside>

    <!-- S11：本机 Zotero 侧栏。与文献详情用同一个抽屉外观，状态在 URL 里（?zotero=1），
         刷新与分享链接都能回到同一个侧栏。 -->
    <aside v-if="zoteroOpen" class="le32-drawer is-open" data-v32-zotero-drawer role="dialog" :aria-label="t('本机 Zotero', 'Local Zotero')">
      <div class="le32-drawer-head">
        <span class="eyebrow">{{ t('本机 Zotero', 'Local Zotero') }}</span>
        <button type="button" class="le32-action" data-v32-zotero-drawer-close @click="closeZotero">{{ t('关闭', 'Close') }}</button>
      </div>
      <ZoteroLocalPanel
        :client="client"
        :project-id="projectId"
        :revision="project?.revision ?? 1"
        :lang="lang"
        :preselect="zoteroPreselect"
        @imported="onZoteroImported"
      />
    </aside>
  </div>
</template>

<style scoped>
.le32-workspace { max-width: 1180px; margin: 0 auto; padding: var(--s-6) var(--gutter) var(--s-16); }
/* 侧栏开关不占整行：它是动作，不是表单。 */
.le32-anchor > .le32-action { width: auto; justify-self: start; align-self: flex-start; margin-top: var(--s-3); }
.le32-papers-head > .le32-action { width: auto; }
.le32-thread-facts { margin: var(--s-2) 0 var(--s-4); }
.le32-venue-note { font-size: 12.5px; margin: var(--s-2) 0 var(--s-5); }
/* S4：阶段条移到 shell/AppNav.vue（.le-nav-*），这里不再保留 .le32-stage-* 规则。 */
.le32-ranking-stage { font-size: 13px; color: var(--text-2); margin-bottom: var(--s-3); }
.le32-anchor { border: 1px solid var(--border); border-radius: 10px; background: var(--surface-2); padding: var(--s-6); display: flex; flex-direction: column; gap: var(--s-3); }
.le32-anchor textarea { width: 100%; padding: 10px 12px; border: 1px solid var(--line); border-radius: 8px; font: inherit; font-size: 14px; }

/* 任务进度条：等待期间必须有可见反馈（最长 45 秒的检索窗口）。 */
.le32-progress { margin: 14px 0 6px; }
.le32-progress-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin-bottom: 6px; font-size: 13px; color: var(--text-2); }
.le32-progress-track { position: relative; height: 4px; border-radius: 999px; background: rgba(0, 0, 0, .08); overflow: hidden; }
.le32-progress-fill { height: 100%; width: 0; border-radius: 999px; background: var(--ink); transition: width .35s ease; }
.le32-progress-track.is-indeterminate .le32-progress-fill { width: 35%; animation: le32-progress-slide 1.2s ease-in-out infinite; }
@keyframes le32-progress-slide {
  0% { transform: translateX(-100%); }
  100% { transform: translateX(285%); }
}
@media (prefers-reduced-motion: reduce) {
  .le32-progress-fill { transition: none; }
  .le32-progress-track.is-indeterminate .le32-progress-fill { animation: none; width: 100%; opacity: .35; }
}
.le32-mode-row { display: flex; gap: var(--s-2); flex-wrap: wrap; }
.le32-mode { padding: 8px 14px; border: 1px solid var(--line); border-radius: 8px; background: var(--surface); color: var(--text-2); font-size: 13px; }
.le32-mode.is-active { border-color: var(--ink); color: var(--ink); background: var(--surface-3); }
.le32-primary { align-self: flex-start; min-height: 40px; padding: 10px 18px; border: 1px solid var(--ink); border-radius: 8px; background: var(--ink); color: #fff; font-size: 14px; font-weight: 550; }
.le32-primary[disabled] { opacity: .55; cursor: not-allowed; }
.le32-papers-head { display: flex; justify-content: space-between; align-items: baseline; gap: var(--s-4); }
.le32-view-row { display: flex; gap: var(--s-2); flex-wrap: wrap; margin-top: var(--s-3); align-items: center; }
.le32-view { padding: 7px 12px; border: 1px solid var(--line); border-radius: 8px; background: var(--surface); color: var(--text-2); font-size: 13px; }
.le32-view.is-active { border-color: var(--ink); color: var(--ink); background: var(--surface-3); }
.le32-action { min-height: 36px; padding: 7px 14px; border: 1px solid var(--line); border-radius: 8px; background: var(--surface); color: var(--text-2); font-size: 13px; }
.le32-link { background: none; border: 0; padding: 0 4px; color: var(--link); font-size: inherit; }
.le32-coverage { margin-top: var(--s-4); font-size: 13.5px; color: var(--text-2); }
.le32-degraded { margin-top: var(--s-3); font-size: 13.5px; color: #9a6100; }
.le32-list { list-style: none; margin: 0; padding: 0; }
.le32-branch-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--s-4); margin-top: var(--s-4); }
.le32-branch { border: 1px solid var(--border); border-radius: 10px; padding: var(--s-5); background: var(--surface-2); }
.le32-timeline { display: flex; flex-wrap: wrap; gap: var(--s-3); list-style: none; margin: var(--s-3) 0; padding: 0; font-size: 13px; color: var(--text-2); }
.le32-statements { list-style: none; margin: var(--s-3) 0; padding: 0; }
.le32-statements li { padding: var(--s-3) 0; border-bottom: 1px solid var(--border); }
.le32-lead { padding: var(--s-4) 0; border-bottom: 1px solid var(--border); }
.le32-drawer { position: fixed; top: var(--topbar-h); right: 0; bottom: 0; width: min(560px, 96vw); overflow-y: auto; background: var(--surface); border-left: 1px solid var(--border); box-shadow: -18px 0 32px #1b1c2014; padding: var(--s-6); z-index: 102; }
.le32-drawer-head { display: flex; justify-content: space-between; align-items: center; gap: var(--s-3); }
.le32-drawer h4 { margin-top: var(--s-5); font-size: 15px; }
.le32-access { list-style: none; margin: var(--s-3) 0; padding: 0; font-size: 13.5px; display: flex; flex-direction: column; gap: var(--s-2); }
.le32-access li { padding: var(--s-3) var(--s-4); border: 1px solid var(--border); border-radius: 8px; background: var(--surface-2); }
.le32-access-head { display: flex; align-items: baseline; justify-content: space-between; gap: var(--s-3); }
.le32-access-head a { font-weight: 550; }
.le32-access-badge { flex: none; padding: 2px 8px; border: 1px solid var(--line); border-radius: 999px; font-size: 12px; color: var(--text-2); white-space: nowrap; }
.le32-access-badge[data-access-availability="public"] { border-color: #2f7d4f; color: #2f7d4f; }
.le32-access-badge[data-access-availability="requires_institution"] { border-color: #9a6100; color: #9a6100; }
.le32-access-hint { margin: 6px 0 0; font-size: 12.5px; }
.le32-upload-inline { margin-top: var(--s-6); }
.le32-intent { border-top: 1px solid var(--border); padding-top: var(--s-4); display: flex; flex-direction: column; gap: var(--s-2); }
.le32-intent-head { display: flex; align-items: baseline; justify-content: space-between; gap: var(--s-3); flex-wrap: wrap; }
.le32-intent-head h3 { font-size: 15px; }
.le32-intent-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--s-2) var(--s-4); margin: 0; font-size: 13.5px; }
.le32-intent-grid dt { color: var(--muted); font-size: 12.5px; }
.le32-intent-grid dd { margin: 2px 0 0; overflow-wrap: anywhere; }
.le32-intent h4 { font-size: 13.5px; margin-top: var(--s-2); }
.le32-venue { font-size: 13.5px; }
.le32-drawer select { min-height: 36px; padding: 6px 10px; border: 1px solid var(--line); border-radius: 8px; background: var(--surface); font: inherit; font-size: 13px; }
@media (max-width: 599px) { .le32-intent-grid { grid-template-columns: minmax(0, 1fr); } }
@media (max-width: 959px) { .le32-branch-grid { grid-template-columns: minmax(0, 1fr); } }
</style>
