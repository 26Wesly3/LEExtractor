<script setup>
// 文献卡片。依据 docs/v3.2/05_UX_UI_SPEC.md「文献卡片」：
//   序号、题名、作者/年份/出版源、最多 2 条实际推荐依据、角色（有依据时）、
//   「标记核心」「相关」「排除」三个动作；core_score 仅在详情中展示，不叫核心概率；
//   用户确认与算法推荐使用不同标签。
import { computed, nextTick, ref } from 'vue'
import { EXCLUSION_REASONS } from './states.js'
import { venueBadges, venueRows } from './venue.js'
import { cardReasons, cardRole, coreScoreText, judgementLabel, judgementStateLabel, metaLine, snapshotLabel, systemEligibilityLabel } from './card.js'

const props = defineProps({
  item: { type: Object, required: true },
  index: { type: Number, default: 0 },
  lang: { type: String, default: 'zh' },
  snapshotId: { type: String, default: '' },
  busy: { type: Boolean, default: false },
  pinned: { type: Boolean, default: false },
  // GET …/venue-metrics 的结果（或 { error }）；未读取时为 null。
  venue: { type: Object, default: null },
})

const emit = defineEmits(['open', 'judge'])

const t = (zh, en) => (props.lang === 'zh' ? zh : en)
const detailOpen = ref(false)

const paper = computed(() => props.item?.paper || {})
const ranking = computed(() => props.item?.ranking || null)
const judgement = computed(() => props.item?.judgement?.value || 'unjudged')
const ordinal = computed(() => (typeof ranking.value?.rank === 'number' ? ranking.value.rank : props.index + 1))
const reasons = computed(() => cardReasons(props.item, props.lang))
const role = computed(() => cardRole(props.item, props.lang))
const humanLabel = computed(() => judgementLabel(judgement.value, props.lang))
const humanState = computed(() => judgementStateLabel(judgement.value, props.lang))
const systemLabel = computed(() => systemEligibilityLabel(props.item, props.lang))
const meta = computed(() => metaLine(props.item))
const scoreText = computed(() => coreScoreText(props.item, props.lang))
const snapshot = computed(() => snapshotLabel(props.snapshotId, props.lang))
const eligibility = computed(() => ranking.value?.eligibility || '')
const abstract = computed(() => paper.value.abstract || '')
const venueTags = computed(() => venueBadges(props.venue, props.lang))
const venueDetail = computed(() => venueRows(props.venue, props.lang))
const reasonOptions = computed(() => EXCLUSION_REASONS.map(row => ({ value: row.value, label: props.lang === 'zh' ? row.zh : row.en })))
// 排除先选原因：只有「不相关」进入负反馈（doc 02「人工判断」）。
const choosingReason = ref(false)
const reasonHost = ref(null)

const actions = computed(() => [
  { value: 'core', label: t('标记核心', 'Mark core') },
  { value: 'relevant', label: t('相关', 'Relevant') },
  { value: 'excluded', label: t('排除', 'Exclude') },
])

function judge(value) {
  if (props.busy) return
  if (value === 'excluded' && judgement.value !== 'excluded') {
    choosingReason.value = true
    nextTick(() => reasonHost.value?.querySelector('button')?.focus?.())
    return
  }
  choosingReason.value = false
  // 再次点击同一动作即撤销判断（unjudged）。
  emit('judge', { paperId: paper.value.paper_id, value: judgement.value === value ? 'unjudged' : value })
}

function chooseReason(code) {
  if (props.busy) return
  choosingReason.value = false
  emit('judge', { paperId: paper.value.paper_id, value: 'excluded', exclusionReason: code })
}

function cancelReason() { choosingReason.value = false }

function undo() {
  if (props.busy) return
  emit('judge', { paperId: paper.value.paper_id, value: 'unjudged' })
}
</script>

<template>
  <article class="le32-card" :class="{ 'is-pinned': pinned }" :data-paper-id="paper.paper_id">
    <div class="le32-card-index" :data-card-rank="ordinal" aria-hidden="true">{{ ordinal }}</div>
    <div class="le32-card-body">
      <h3 class="le32-card-title">
        <button type="button" class="le32-card-open" :aria-expanded="detailOpen ? 'true' : 'false'" @click="emit('open', paper.paper_id)">
          {{ paper.title || t('题名未提供', 'Title not provided') }}
        </button>
      </h3>
      <p class="paper-meta" data-card-meta>{{ meta || t('作者、年份与出版源未提供','Authors, year and venue not provided') }}</p>

      <ul v-if="reasons.length" class="le32-reasons" data-card-reasons>
        <li v-for="reason in reasons" :key="reason.key" :data-reason-code="reason.code">{{ reason.text }}</li>
      </ul>
      <p v-else class="muted" data-card-no-reason>{{ t('当前没有可展示的推荐依据。','No recommendation reason is available.') }}</p>

      <div class="le32-card-badges">
        <span v-if="role" class="badge" :data-card-role="role.role">{{ role.text }} · {{ role.originText }}</span>
        <span v-if="systemLabel" class="badge badge--system" data-card-system-label>{{ systemLabel }}</span>
        <span v-if="humanState" class="badge badge--human" data-card-human-label>{{ humanState }}</span>
        <span v-if="eligibility === 'exploration'" class="badge" data-card-eligibility="exploration">{{ t('探索列表','Exploration list') }}</span>
        <span v-for="tag in venueTags" :key="tag.type" class="badge le32-venue-badge" data-card-venue :data-card-venue-type="tag.type" :title="tag.source">{{ tag.text }}<span class="le32-venue-src"> · {{ t('来源','Source') }}：{{ tag.source }}</span></span>
      </div>

      <div class="le32-card-actions" role="group" :aria-label="t('文献判断','Record judgement')">
        <button
          v-for="action in actions"
          :key="action.value"
          type="button"
          class="le32-action"
          :class="{ 'is-active': judgement === action.value }"
          :data-card-action="action.value"
          :data-card-action-label="judgement === action.value ? humanLabel : action.label"
          :aria-pressed="judgement === action.value ? 'true' : 'false'"
          :disabled="busy"
          @click="judge(action.value)"
        >{{ judgement === action.value ? humanLabel : action.label }}</button>
        <button v-if="judgement !== 'unjudged'" type="button" class="le32-action le32-action--undo" data-card-action="undo" :disabled="busy" @click="undo">
          {{ t('撤销判断', 'Undo judgement') }}
        </button>
      </div>

      <div
        v-if="choosingReason"
        ref="reasonHost"
        class="le32-exclusion"
        data-card-exclusion
        role="group"
        :aria-label="t('排除原因','Exclusion reason')"
        @keydown.esc.stop.prevent="cancelReason"
      >
        <span class="le32-exclusion-title">{{ t('排除原因', 'Why exclude?') }}</span>
        <div class="le32-exclusion-options">
          <button v-for="option in reasonOptions" :key="option.value" type="button" class="le32-action" :data-card-exclusion-option="option.value" :disabled="busy" @click="chooseReason(option.value)">{{ option.label }}</button>
          <button type="button" class="le32-link-btn" data-card-exclusion-cancel @click="cancelReason">{{ t('取消', 'Cancel') }}</button>
        </div>
        <p class="muted le32-exclusion-help" data-card-exclusion-help>{{ t('排除只隐藏这篇文献并不再用它扩展；只有「不相关」会作为负反馈影响排序。', 'Excluding only hides this record and stops expansion from it; only “Not relevant” is used as negative feedback.') }}</p>
      </div>

      <button type="button" class="le32-card-detail-toggle" :aria-expanded="detailOpen ? 'true' : 'false'" @click="detailOpen = !detailOpen">
        {{ detailOpen ? t('收起详情', 'Hide details') : t('查看详情', 'Details') }}
      </button>

      <div v-if="detailOpen" class="le32-card-detail" data-card-detail>
        <p data-card-score>{{ scoreText }}</p>
        <p v-if="abstract" class="le32-abstract">{{ abstract }}</p>
        <p class="muted">{{ snapshot }}</p>
        <dl class="meta-grid">
          <div><dt>{{ t('规范身份','Canonical id') }}</dt><dd class="mono">{{ paper.paper_id }}</dd></div>
          <div><dt>DOI</dt><dd class="mono">{{ paper.doi || t('未提供','Not provided') }}</dd></div>
          <div><dt>{{ t('被引次数','Cited by') }}</dt><dd>{{ paper.citation_count === null || paper.citation_count === undefined ? t('未知','Unknown') : paper.citation_count }}</dd></div>
        </dl>
        <ul class="brief-list le32-venue-detail" data-card-venue-detail>
          <li v-for="row in venueDetail" :key="row.type" :data-venue-metric="row.type">
            {{ row.label }}：{{ row.value }}<span v-if="row.source"> · {{ t('来源','Source') }}：{{ row.source }}</span><span v-if="row.reason"> · {{ row.reason }}</span>
          </li>
        </ul>
        <details v-if="ranking?.breakdown?.length" class="le32-breakdown">
          <summary>{{ t('排序依据','Ranking basis') }}</summary>
          <ul class="brief-list">
            <li v-for="row in ranking.breakdown" :key="row.signal">
              {{ row.signal }} · {{ row.available ? t('启用','active') : t('停用','inactive') }}<span v-if="row.missing_reason"> · {{ row.missing_reason }}</span>
            </li>
          </ul>
        </details>
      </div>
    </div>
  </article>
</template>

<style scoped>
.le32-card { display: flex; gap: var(--s-4); padding: var(--s-6) 0; border-bottom: 1px solid var(--border); }
.le32-card.is-pinned { background: var(--surface-2); }
.le32-card-index { flex: 0 0 34px; font-variant-numeric: tabular-nums; color: var(--muted); font-size: 14px; padding-top: 3px; }
.le32-card-body { flex: 1; min-width: 0; overflow-wrap: anywhere; }
.le32-card-title { font-size: 19px; line-height: 1.45; }
.le32-card-open { text-align: left; color: var(--text); font-weight: 600; background: none; border: 0; padding: 0; font-size: inherit; }
.le32-card-open:hover { color: var(--link); text-decoration: underline; }
.le32-reasons { margin: var(--s-3) 0 0; padding-left: 18px; font-size: 13.5px; color: var(--text-2); line-height: 1.7; }
.le32-card-badges { display: flex; gap: 6px; flex-wrap: wrap; margin-top: var(--s-3); }
.badge--system { border-style: dashed; }
.badge--human { color: #1f7a45; background: #eaf6ee; border-color: #bfe3cb; }
.le32-card-actions { display: flex; gap: var(--s-2); flex-wrap: wrap; margin-top: var(--s-4); }
.le32-action { min-height: 36px; padding: 7px 14px; border: 1px solid var(--line); border-radius: 8px; background: var(--surface); color: var(--text-2); font-size: 13px; transition: border-color .12s ease, background .12s ease; }
.le32-action:hover { border-color: #9aa2ad; color: var(--ink); }
.le32-action.is-active { border-color: var(--ink); color: var(--ink); background: var(--surface-3); font-weight: 600; }
.le32-action[disabled] { opacity: .55; cursor: not-allowed; }
.le32-card-detail-toggle { margin-top: var(--s-3); font-size: 13px; color: var(--link); background: none; border: 0; padding: 0; }
.le32-card-detail { margin-top: var(--s-3); padding: var(--s-4); border: 1px solid var(--border); border-radius: 8px; background: var(--surface-2); }
.le32-card-detail p { font-size: 13.5px; }
.le32-abstract { margin-top: var(--s-3) !important; color: var(--text-2); line-height: 1.8; }
.le32-abstract { display: -webkit-box; -webkit-line-clamp: 6; -webkit-box-orient: vertical; overflow: hidden; }
.le32-breakdown { margin-top: var(--s-3); }
.le32-venue-src { color: var(--muted); font-weight: 400; }
.le32-venue-detail { margin-top: var(--s-3); font-size: 13px; color: var(--text-2); }
.le32-exclusion { margin-top: var(--s-3); padding: var(--s-3) var(--s-4); border: 1px solid var(--border); border-radius: 8px; background: var(--surface-2); }
.le32-exclusion-title { font-size: 13px; font-weight: 600; color: var(--ink); }
.le32-exclusion-options { display: flex; gap: var(--s-2); flex-wrap: wrap; align-items: center; margin-top: var(--s-2); }
.le32-exclusion-help { margin: var(--s-2) 0 0; font-size: 12.5px; }
.le32-link-btn { background: none; border: 0; padding: 0 4px; color: var(--link); font-size: 13px; }
</style>
