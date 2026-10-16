<script setup>
// 首页入口：首屏只保留两个主入口。
// 依据 docs/v3.2/05_UX_UI_SPEC.md「页面结构」：首页首屏展示「从研究方向开始」
// 「从已有文献开始」；运行概况与项目里程碑属于其后的次要区域，由现有的
// HomeOverview.vue 继续承担，不在这里重复。
import { computed } from 'vue'

const props = defineProps({
  lang: { type: String, default: 'zh' },
  busy: { type: Boolean, default: false },
  error: { type: String, default: '' },
})

const emit = defineEmits(['select'])

const t = (zh, en) => (props.lang === 'zh' ? zh : en)

const entries = computed(() => [
  {
    mode: 'topic',
    key: 'topic',
    title: t('从研究方向开始', 'Start from a research direction'),
    lead: t(
      '输入研究方向，系统保留原文并生成可编辑的研究意图预览，再向真实来源发起检索。',
      'Enter a research direction. The original text is kept, an editable intent preview is produced, and the real sources are queried.',
    ),
    hint: t('输入研究方向', 'Enter a research direction'),
  },
  {
    mode: 'pdf',
    key: 'pdf',
    title: t('从已有文献开始', 'Start from existing literature'),
    lead: t(
      '上传 1 至 10 篇 PDF，逐文件解析与匹配后，以题名、摘要或内容继续发现。',
      'Upload 1 to 10 PDFs. Each file is parsed and matched, then discovery continues from its title, abstract or text.',
    ),
    hint: t('上传已有文献', 'Upload existing literature'),
  },
])

function choose(entry) {
  if (props.busy) return
  emit('select', { mode: entry.mode })
}
</script>

<template>
  <section class="le32-entries" data-v32-entries :aria-label="t('选择研究入口','Choose an entry')">
    <p class="eyebrow">{{ t('研究入口','Research entry') }}</p>
    <h1>{{ t('从哪一步开始','Where would you like to start') }}</h1>
    <p class="le32-lead">
      {{ t('两个入口互不替代：研究方向适合从问题出发，已有文献适合从手头材料出发。两者都需要你确认检索结果。','The two entries are not interchangeable: a research direction starts from the question, existing literature starts from the material at hand. Both require you to confirm the retrieved records.') }}
    </p>
    <div class="le32-entry-grid">
      <article v-for="entry in entries" :key="entry.key" class="le32-entry" :data-v32-entry="entry.key">
        <h2>{{ entry.title }}</h2>
        <p>{{ entry.lead }}</p>
        <button type="button" class="le32-entry-action" :disabled="busy" :data-v32-entry-action="entry.key" @click="choose(entry)">
          {{ entry.hint }}
        </button>
      </article>
    </div>
    <p v-if="error" class="error-text le32-entry-error" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
.le32-entries { max-width: 1180px; margin: 0 auto 40px; padding: 0 var(--gutter); }
.le32-entries h1 { margin-bottom: 12px; }
.le32-lead { max-width: 80ch; color: var(--text-2); font-size: 16px; line-height: 1.85; }
.le32-entry-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--s-6); margin-top: var(--s-6); }
.le32-entry { border: 1px solid var(--line); border-radius: 10px; background: #fff; padding: var(--s-6); display: flex; flex-direction: column; gap: var(--s-3); }
.le32-entry h2 { font-size: 20px; }
.le32-entry p { color: var(--text-2); font-size: 14px; line-height: 1.7; }
.le32-entry-action { margin-top: auto; align-self: flex-start; min-height: 40px; padding: 10px 18px; border: 1px solid var(--ink); border-radius: 8px; background: var(--ink); color: #fff; font-size: 14px; font-weight: 550; }
.le32-entry-action[disabled] { opacity: .55; cursor: not-allowed; }
.le32-entry-action:focus-visible { outline: 3px solid #677b94; outline-offset: 3px; }
.le32-entry-error { margin-top: var(--s-4); }
@media (max-width: 959px) { .le32-entry-grid { grid-template-columns: minmax(0, 1fr); } }
</style>
