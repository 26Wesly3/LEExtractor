<script setup>
// v3.2 工作区：研究起点 → 核心文献 → 研究脉络 → 研究洞察。
// 依据 docs/v3.2/05_UX_UI_SPEC.md「页面结构」「文献卡片」「上传与异常」与
// docs/v3.2/02_USER_WORKFLOW.md。
//
// 关键约束：
//   * 快速本地结果先出现（已获得初步结果），后台增强完成后显示排序已更新；
//   * 翻新排序时保留当前阅读条目的视口锚点，打开的抽屉按 paper_id 保持；
//   * 推荐、空与错误状态各自有独立文案；
//   * 本轮没有校园登录入口与 CAS 调用。
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { createV32Client, errorLine, safeUrl } from './client.js'
import { anchorFrom, coverageLine, emptyStateCopy, enhancementNotice, rankingStageForJob, rankingStageLabel, reasonTextForError, resolvePinned, restoreAnchor, resultState, stageLabel, STAGES } from './states.js'
import { judgementStateLabel, snapshotLabel } from './card.js'
import EntryChoice from './EntryChoice.vue'
import PaperCard from './PaperCard.vue'
import SettingsPanels from './SettingsPanels.vue'
import UploadPanel from './UploadPanel.vue'

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
const activeStage = ref(props.stage || 'anchors')
const entryMode = ref('')
const researchText = ref('')
const modelMode = ref('basic')
const project = ref(props.projectId ? { project_id: props.projectId, revision: 0, entry_mode: 'topic' } : null)
const items = ref([])
const snapshotId = ref('')
const listView = ref('recommended')
const nextCursor = ref(null)
const busy = ref(false)
const error = ref('')
const notice = ref('')
const job = ref(null)
const overview = ref(null)
const pinnedId = ref('')
const judged = reactive({})
const reasons = reactive({})
const coverageExpanded = ref(false)
const accessOptions = ref([])
const copyState = ref('')
const client = computed(() => api)

let pollTimer = null
let stopped = false

const LANG = computed(() => props.lang)
const stageTabs = computed(() => STAGES.map(stage => ({ ...stage, label: stageLabel(stage.key, LANG.value) })))
const stageNotice = computed(() => rankingStageLabel(job.value ? rankingStageForJob(job.value, items.value.length > 0) : (items.value.length ? 'refreshed' : 'idle'), LANG.value))
const coverage = computed(() => coverageLine(overview.value?.coverage || job.value?.coverage || null, LANG.value))
const degradation = computed(() => enhancementNotice(job.value, LANG.value))
const snapshot = computed(() => snapshotLabel(snapshotId.value, LANG.value))

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
  institution_link: { zh: '通过学校访问 CARSI', en: 'Campus access via CARSI' },
  resolver: { zh: '图书馆数据库导航', en: 'Library database navigation' },
  metadata_api: { zh: '元数据来源', en: 'Metadata source' },
})

function accessKindLabel(kind) {
  const row = ACCESS_KINDS[kind]
  return row ? (props.lang === 'zh' ? row.zh : row.en) : kind
}

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
    entryMode.value = data?.entry_mode || entryMode.value
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
    })
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
  pollTimer = setInterval(async () => {
    if (stopped) return
    try {
      const next = await api.job(props.projectId, jobId)
      if (stopped) return
      job.value = next
      const active = ['queued', 'running', 'cancel_requested'].includes(next?.state)
      if (!active) {
        stopPolling()
        const anchor = anchorNow()
        await loadPapers({ keepAnchor: false })
        restore(anchor)
        await loadOverview()
      }
    } catch { /* 轮询失败保留既有结果，不打断阅读 */ }
  }, 1200)
}

async function loadOverview() {
  if (!props.projectId) return
  try { overview.value = await api.overview(props.projectId) } catch { overview.value = overview.value }
}

async function judge({ paperId, value, reason = '' }) {
  if (!paperId) return
  const before = judged[paperId]
  judged[paperId] = value
  if (value === 'excluded') reasons[paperId] = reason
  try {
    const saved = await api.judge(props.projectId, {
      expected_revision: project.value?.revision ?? 0,
      paper_id: paperId,
      judgement: value,
      // 排除默认仅隐藏并禁止自动扩展；只有原因 not_relevant 才进入负反馈。
      exclusion_reason: 'unspecified',
      reason: value === 'excluded' ? (reasons[paperId] || '') : '',
    }, { scope: paperId })
    if (saved?.revision !== undefined && project.value) project.value = { ...project.value, revision: saved.revision }
    notice.value = value === 'unjudged' ? t('已撤销判断。', 'Judgement undone.') : t('判断已保存，后台按需重排。', 'Judgement saved; the ranking is refreshed in the background when needed.')
  } catch (problem) {
    judged[paperId] = before
    if (problem?.code === 'revision_conflict') {
      error.value = reasonTextForError(problem, LANG.value)
      await loadProject()
    } else {
      error.value = reasonTextForError(problem, LANG.value)
    }
  }
}

function openPaper(paperId) {
  pinnedId.value = paperId
  accessOptions.value = []
  copyState.value = ''
  loadAccessOptions(paperId)
}

function closePaper() { pinnedId.value = '' }

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

async function openEntry(mode) {
  entryMode.value = mode
  activeStage.value = 'anchors'
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

onBeforeUnmount(() => { stopped = true; stopPolling() })

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
  ensureSession().then(() => { loadProject(); loadPapers({ keepAnchor: false }); loadOverview() })
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
        <nav class="le32-stage-nav" data-v32-stages :aria-label="t('工作区阶段','Workspace stages')">
          <button
            v-for="stage in stageTabs"
            :key="stage.key"
            type="button"
            class="le32-stage"
            :class="{ 'is-active': activeStage === stage.key }"
            :data-v32-stage="stage.key"
            :aria-current="activeStage === stage.key ? 'step' : null"
            @click="activeStage = stage.key"
          >{{ stage.label }}</button>
        </nav>

        <p v-if="stageNotice" class="le32-ranking-stage" data-v32-ranking-stage role="status">{{ stageNotice }}</p>

        <template v-if="activeStage === 'anchors'">
          <section class="le32-anchor" data-v32-anchor>
            <h2>{{ t('研究起点', 'Research anchors') }}</h2>
            <template v-if="project?.anchor?.original_text">
              <p class="muted">{{ t('已保存的研究意图','The saved research intent') }}</p>
              <p data-v32-anchor-text>{{ project.anchor.original_text }}</p>
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
              <h2>{{ t('核心文献', 'Core literature') }}</h2>
              <span class="muted" data-v32-snapshot>{{ snapshot }}</span>
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
                @open="openPaper"
                @judge="judge"
              />
            </ol>
            <div v-else class="empty" data-v32-empty>
              <p data-v32-empty-copy>{{ emptyCopy.text }}</p>
              <button type="button" class="le32-primary" :disabled="busy" data-v32-empty-action @click="continueDiscovery">{{ emptyCopy.action }}</button>
            </div>

            <button v-if="nextCursor" type="button" class="le32-action" data-v32-more :disabled="busy" @click="loadMore">{{ t('读取更多', 'Load more') }}</button>
          </section>
        </template>

        <template v-else-if="activeStage === 'threads'">
          <section class="le32-threads" data-v32-threads>
            <h2>{{ t('研究脉络', 'Research threads') }}</h2>
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
              <p v-else class="empty" data-v32-threads-empty>{{ t('关系数据不足，暂不给出分支结论。','The relationship data is insufficient, so no branch conclusion is offered.') }}</p>
              <h3>{{ t('时间线', 'Timeline') }}</h3>
              <ul class="le32-timeline" data-v32-timeline>
                <li v-for="point in insight.timeline" :key="point.year === null ? 'unknown' : point.year">{{ point.year === null ? t('年份未知', 'Year unknown') : point.year }} · {{ point.count }}</li>
              </ul>
              <h3>{{ t('检索集内活跃方向', 'Active directions inside the retrieved set') }}</h3>
              <ul v-if="insight.trends.length" class="brief-list" data-v32-trends>
                <li v-for="trend in insight.trends" :key="trend.branch_id">{{ trend.branch_id }} · {{ trend.recent_count }} / {{ trend.prior_count }}</li>
              </ul>
              <p v-else class="muted" data-v32-trends-empty>{{ t('暂不足以判断趋势。','There is not enough information to judge a trend.') }}</p>
            </template>
          </section>
        </template>

        <template v-else>
          <section class="le32-insight" data-v32-insight>
            <h2>{{ t('研究洞察', 'Research insight') }}</h2>
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

    <aside v-if="pinnedItem" class="le32-drawer" data-v32-drawer role="dialog" :aria-label="t('文献详情','Record details')">
      <div class="le32-drawer-head">
        <span class="eyebrow">{{ t('文献详情', 'Record details') }}</span>
        <button type="button" class="le32-action" data-v32-drawer-close @click="closePaper">{{ t('关闭', 'Close') }}</button>
      </div>
      <h3>{{ pinnedItem.paper.title }}</h3>
      <p class="paper-meta">{{ pinnedItem.paper.year || t('年份未知','Year unknown') }} · {{ pinnedItem.paper.citation_count === null || pinnedItem.paper.citation_count === undefined ? t('被引次数未知','Citations unknown') : pinnedItem.paper.citation_count }}</p>
      <p v-if="pinnedJudgement" class="badge badge--human" data-v32-drawer-judgement>{{ pinnedJudgement }}</p>
      <p v-else class="badge badge--system" data-v32-drawer-system>{{ t('算法推荐核心', 'Recommended by the ranking') }}</p>
      <p class="muted">{{ snapshot }}</p>

      <h4>{{ t('校园资源访问', 'Campus resource access') }}</h4>
      <p class="muted" data-v32-access-note>{{ t('校园资源访问是独立动作，打开链接只代表进入入口，不代表已获得授权。','Campus resource access is an independent action. Opening a link only opens an entry point and does not mean access was granted.') }}</p>
      <ul v-if="accessOptions.length" class="le32-access" data-v32-access>
        <li v-for="option in accessOptions" :key="option.url" :data-v32-access-option="option.kind">
          <a :href="safeUrl(option.url)" target="_blank" rel="noopener noreferrer">{{ accessKindLabel(option.kind) }} · {{ option.provider }}</a>
        </li>
      </ul>
      <p v-else class="muted" data-v32-access-empty>{{ t('尚未取得可用的访问入口。','No access entry point is available yet.') }}</p>
      <button type="button" class="le32-action" data-v32-copy-citation @click="copyCitation">{{ t('复制题名与 DOI', 'Copy title and DOI') }}</button>
      <p v-if="copyState" class="muted" data-v32-copy-state role="status">{{ copyState }}</p>

      <template v-if="judgementOf(pinnedItem) === 'excluded'">
        <h4>{{ t('排除说明（可选）', 'Exclusion note (optional)') }}</h4>
        <input v-model="reasons[pinnedItem.paper.paper_id]" type="text" :placeholder="t('不填写时按未指定处理','Left blank it stays unspecified')" data-v32-exclusion-reason />
      </template>

      <h4>{{ t('排序依据', 'Ranking basis') }}</h4>
      <p class="muted" data-v32-detail-score>{{ detailScore }}</p>
    </aside>
  </div>
</template>

<style scoped>
.le32-workspace { max-width: 1180px; margin: 0 auto; padding: var(--s-6) var(--gutter) var(--s-16); }
.le32-stage-nav { display: flex; gap: var(--s-2); flex-wrap: wrap; border-bottom: 1px solid var(--border); margin-bottom: var(--s-4); }
.le32-stage { padding: 12px 16px; background: none; border: 0; border-bottom: 2px solid transparent; color: var(--muted); font-size: 14px; }
.le32-stage.is-active { border-color: var(--ink); color: var(--ink); font-weight: 600; }
.le32-ranking-stage { font-size: 13px; color: var(--text-2); margin-bottom: var(--s-3); }
.le32-anchor { border: 1px solid var(--border); border-radius: 10px; background: var(--surface-2); padding: var(--s-6); display: flex; flex-direction: column; gap: var(--s-3); }
.le32-anchor textarea { width: 100%; padding: 10px 12px; border: 1px solid var(--line); border-radius: 8px; font: inherit; font-size: 14px; }
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
.le32-access { list-style: none; margin: var(--s-3) 0; padding: 0; font-size: 13.5px; }
.le32-access li { padding: 4px 0; }
.le32-upload-inline { margin-top: var(--s-6); }
@media (max-width: 959px) { .le32-branch-grid { grid-template-columns: minmax(0, 1fr); } }
</style>
