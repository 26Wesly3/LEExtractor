<script setup>
// 首页「运行概况 + 项目里程碑」。
// 视觉参照 OpenAlex 首页的数字条：大数字 / 简短标签 / 细分隔线 / 灰色说明 / 宽留白。
// 数字全部来自服务端真实回传（首屏 GET，其后 SSE 推送），不写死、不用本地存储冒充。
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { DEFAULT_MILESTONES, MILESTONES } from './milestones.js'
import { fetchStats, pageEntryId, recordHomeView, subscribeStats } from './stats.js'

const props = defineProps({
  lang: { type: String, default: 'zh' },
  milestones: { type: Number, default: DEFAULT_MILESTONES },
})

const t = (zh, en) => (props.lang === 'zh' ? zh : en)
const stats = ref(null)
let unsubscribe = () => {}

onMounted(() => {
  unsubscribe = subscribeStats(payload => { stats.value = payload })
  fetchStats().then(payload => { if (payload) stats.value = payload }).catch(() => {})
  // 记一次首页访问；返回值即最新快照（失败被吞掉，不影响页面）。
  recordHomeView(pageEntryId()).then(payload => { if (payload) stats.value = payload }).catch(() => {})
})
onUnmounted(() => unsubscribe())

/** 数字变化时做一次滚动动画；尊重系统"减少动态效果"，也支持首帧直接落位。 */
function rolling(source, duration = 650) {
  const shown = ref(null)
  let frame = 0
  const reduced = () => typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches
  watch(source, (next, previous) => {
    if (typeof cancelAnimationFrame === 'function') cancelAnimationFrame(frame)
    if (typeof next !== 'number') { shown.value = null; return }
    const from = typeof previous === 'number' ? previous : null
    // 首帧直接落位，只有数值发生变化时才滚动（避免每次进首页都从 0 跳字）。
    if (from === null || reduced() || from === next || typeof requestAnimationFrame !== 'function') { shown.value = next; return }
    const started = performance.now()
    const step = now => {
      const ratio = Math.min(1, (now - started) / duration)
      shown.value = Math.round(from + (next - from) * (1 - Math.pow(1 - ratio, 3)))
      if (ratio < 1) frame = requestAnimationFrame(step)
    }
    frame = requestAnimationFrame(step)
  }, { immediate: true })
  onUnmounted(() => { if (typeof cancelAnimationFrame === 'function') cancelAnimationFrame(frame) })
  return shown
}

const launchDays = computed(() => stats.value?.launch?.days ?? null)
const views = rolling(computed(() => stats.value?.home_views?.count ?? null))
const clicks = rolling(computed(() => stats.value?.search_entry_clicks?.count ?? null))
const runs = rolling(computed(() => stats.value?.search_runs?.count ?? null))
const records = rolling(computed(() => stats.value?.records?.count ?? null))

const launchDate = computed(() => stats.value?.launch?.date || '—')
const number = value => (typeof value === 'number' ? value.toLocaleString(props.lang === 'zh' ? 'zh-CN' : 'en-US') : '—')
const days = value => (typeof value === 'number' ? `${value} ${t('天', 'days')}` : '—')

/** 进程连续运行时长：与"上线至今"分开显示，避免把两者混为一谈。 */
const uptime = computed(() => {
  const seconds = stats.value?.runtime?.process_uptime_seconds
  if (typeof seconds !== 'number') return ''
  if (seconds < 3600) return t(`本次进程已连续运行 ${Math.max(1, Math.round(seconds / 60))} 分钟`, `Process up for ${Math.max(1, Math.round(seconds / 60))} min`)
  return t(`本次进程已连续运行 ${(seconds / 3600).toFixed(1)} 小时`, `Process up for ${(seconds / 3600).toFixed(1)} h`)
})

const planned = computed(() => MILESTONES.filter(item => item.status === 'planned'))
// 默认窗口内总节点数不超过 milestones（已完成在前，后续计划占一个名额）。
const done = computed(() => MILESTONES.filter(item => item.status === 'done')
  .slice(0, Math.max(1, props.milestones - planned.value.length)))
const statusLabel = item => (item.status === 'done' ? t('已完成', 'Completed') : t('后续计划', 'Planned'))
</script>

<template>
  <section class="home-band overview-band" aria-labelledby="overview-title">
    <h2 id="overview-title" class="overview-heading">{{ t('运行概况', 'Operating overview') }}</h2>

    <div class="stat-strip" data-stats>
      <article class="stat">
        <b data-stat="launch-date">{{ launchDate }}</b>
        <span class="stat-label">{{ t('项目上线日期', 'Launched on') }}</span>
        <span class="stat-desc">{{ t('按日期计算', 'Counted by date') }} · {{ t('上线至今', 'Live for') }} {{ days(launchDays) }}</span>
        <span v-if="uptime" class="stat-sub">{{ uptime }}</span>
      </article>
      <article class="stat">
        <b data-stat="home-views">{{ number(views) }}</b>
        <span class="stat-label">{{ t('网页点击次数', 'Page views') }}</span>
        <span class="stat-desc">{{ t('首页访问量（PV），自统计启用起累计；非独立访客数。', 'Home page views (PV) since counting began; not unique visitors.') }}</span>
        <span class="stat-sub">{{ t('其中检索入口点击', 'Search entry clicks') }} {{ number(clicks) }}</span>
      </article>
      <article class="stat">
        <b data-stat="search-runs">{{ number(runs) }}</b>
        <span class="stat-label">{{ t('搜索运行次数', 'Search runs') }}</span>
        <span class="stat-desc">{{ t('已记录的检索任务数，与检索成功次数分别统计。', 'Recorded search jobs; success is tracked separately.') }}</span>
      </article>
      <article class="stat">
        <b data-stat="records">{{ number(records) }}</b>
        <span class="stat-label">{{ t('共检索得文献', 'Records retrieved') }}</span>
        <span class="stat-desc">{{ t('各项目语料去重后的文献合计。', 'Unique records currently held across project corpora.') }}</span>
      </article>
    </div>

    <h2 class="overview-heading overview-heading--sub">{{ t('项目里程碑', 'Project milestones') }}</h2>
    <ol class="milestone-list" data-milestones>
      <li v-for="item in done" :key="item.date + item.title.zh" class="milestone done">
        <div class="milestone-head">
          <span class="milestone-date">{{ item.date }}</span>
          <span class="milestone-status">{{ statusLabel(item) }}</span>
        </div>
        <h3>{{ item.title[props.lang] || item.title.zh }}</h3>
        <p>{{ item.summary[props.lang] || item.summary.zh }}</p>
        <details v-if="item.details">
          <summary>{{ t('查看详情', 'Details') }}</summary>
          <p>{{ item.details[props.lang] || item.details.zh }}</p>
        </details>
      </li>
      <li v-for="item in planned" :key="item.title.zh" class="milestone planned">
        <div class="milestone-head">
          <span class="milestone-date">{{ t('后续计划', 'Planned') }}</span>
          <span class="milestone-status">{{ statusLabel(item) }}</span>
        </div>
        <h3>{{ item.title[props.lang] || item.title.zh }}</h3>
        <p>{{ item.summary[props.lang] || item.summary.zh }}</p>
        <details v-if="item.details">
          <summary>{{ t('查看详情', 'Details') }}</summary>
          <p>{{ item.details[props.lang] || item.details.zh }}</p>
        </details>
      </li>
    </ol>
  </section>
</template>
