// 期刊指标展示（docs/v3.2/13_VENUE_INTELLIGENCE.md，R15）。纯函数，便于单测：
//   * 每个数值都带来源；没有来源的数值一律改为「未知」并说明原因；
//   * Citation Proxy 写明「内部引用代理指标」，不能称影响因子；
//   * 没有可核验的官方指标（JIF / JCR / 中科院分区）时显示「未知」及原因；
//   * 卡片最多 2 个标签，完整来源在详情。

const LABELS = Object.freeze({
  ccf_rank: { zh: 'CCF 等级', en: 'CCF rank' },
  citation_proxy: { zh: '内部引用代理指标（非影响因子）', en: 'Internal citation proxy (not an impact factor)' },
  openalex_h_index: { zh: '出版源 h 指数', en: 'Venue h-index' },
  official_jif: { zh: '官方影响因子', en: 'Official impact factor' },
  jcr_quartile: { zh: 'JCR 分区', en: 'JCR quartile' },
  cas_partition: { zh: '中科院分区', en: 'CAS partition' },
})

const ORDER = ['ccf_rank', 'citation_proxy', 'openalex_h_index', 'official_jif', 'jcr_quartile', 'cas_partition']

const VERIFICATION = Object.freeze({
  verified: { zh: '已核验', en: 'verified' },
  unverified: { zh: '未核验', en: 'not verified' },
  unavailable: { zh: '不可用', en: 'unavailable' },
})

const pick = (row, lang) => (row ? (lang === 'zh' ? row.zh : row.en) : '')

function formatValue(type, value) {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return type === 'citation_proxy' ? value.toFixed(2) : String(value)
  }
  return String(value)
}

function sourceText(metric, lang) {
  const name = String(metric?.source_name || '').trim()
  if (!name) return ''
  const release = [metric.edition, metric.metric_year].filter(value => value !== null && value !== undefined && value !== '').join(' / ')
  const status = pick(VERIFICATION[metric.verification_status], lang) || String(metric.verification_status || '')
  return [name, release, status].filter(Boolean).join(' · ')
}

/** One display row per metric; a missing value or source becomes 未知 with a reason. */
export function venueRows(data, lang = 'zh') {
  const unknown = lang === 'zh' ? '未知' : 'Unknown'
  if (!data || data.error) {
    const reason = data?.error
      ? (lang === 'zh' ? `未能读取期刊指标：${data.error}` : `Venue metrics could not be read: ${data.error}`)
      : (lang === 'zh' ? '尚未取得期刊指标。' : 'Venue metrics have not been read yet.')
    return [{ type: 'none', label: lang === 'zh' ? '期刊指标' : 'Venue metrics', value: unknown, source: '', reason }]
  }
  const metrics = Array.isArray(data.metrics) ? data.metrics : []
  if (!metrics.length) {
    return [{
      type: 'none',
      label: lang === 'zh' ? '期刊指标' : 'Venue metrics',
      value: unknown,
      source: '',
      reason: lang === 'zh' ? '记录中没有出版源，或该出版源没有可核验的指标来源。' : 'The record has no venue, or the venue has no verifiable metric source.',
    }]
  }
  return [...metrics]
    .sort((a, b) => (ORDER.indexOf(a.metric_type) + 1 || 99) - (ORDER.indexOf(b.metric_type) + 1 || 99))
    .map(metric => {
      const type = String(metric.metric_type || '')
      const label = pick(LABELS[type], lang) || type
      const source = sourceText(metric, lang)
      const hasValue = metric.value !== null && metric.value !== undefined && metric.value !== ''
      const available = hasValue && metric.verification_status !== 'unavailable'
      if (!available) {
        return { type, label, value: unknown, source, reason: String(metric.note || '').trim() || (lang === 'zh' ? '没有可核验的许可来源。' : 'No verifiable licensed source.') }
      }
      if (!source) {
        return { type, label, value: unknown, source: '', reason: lang === 'zh' ? '数值缺少来源，按规范不展示。' : 'The value has no source, so it is not shown.' }
      }
      const category = metric.category ? String(metric.category) : ''
      return { type, label, value: formatValue(type, metric.value), category, source: category ? `${source} · ${category}` : source, reason: '' }
    })
}

/** At most two high-value, sourced tags for the card. */
export function venueBadges(data, lang = 'zh') {
  return venueRows(data, lang)
    .filter(row => row.source && !row.reason && (row.type === 'ccf_rank' || row.type === 'citation_proxy'))
    .slice(0, 2)
    .map(row => ({
      type: row.type,
      text: row.type === 'ccf_rank'
        ? `CCF ${row.value}`
        : `${lang === 'zh' ? '内部引用代理指标' : 'Internal citation proxy'} ${row.value}`,
      source: row.source,
    }))
}
