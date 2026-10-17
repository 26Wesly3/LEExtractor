// 期刊指标展示（doc 13 / R15）：CCF 等级与内部引用代理指标必须带来源；
// 代理指标绝不叫影响因子；没有可核验官方指标时显示「未知」及原因。
import { describe, expect, it } from 'vitest'
import { venueBadges, venueRows } from './venue.js'

const metric = (overrides = {}) => ({
  metric_type: 'ccf_rank',
  value: 'A',
  metric_year: null,
  category: 'conference',
  edition: '2026',
  source_name: 'CCF 推荐国际学术会议和期刊目录',
  source_url: null,
  retrieved_at: '2026-10-17T00:00:00Z',
  verification_status: 'unverified',
  license_scope: 'ccf_public_catalogue_unverified_metadata',
  note: '',
  ...overrides,
})

const DATA = {
  venue_id: 'v1',
  name: 'Anonymous Venue',
  venue_type: 'journal',
  issns: [],
  metrics: [
    metric(),
    metric({ metric_type: 'citation_proxy', value: 2.345, source_name: 'openalex', license_scope: 'internal_proxy_not_for_official_display', note: '内部引用代理指标' }),
    metric({ metric_type: 'official_jif', value: null, source_name: '未配置许可来源', verification_status: 'unavailable', license_scope: 'unavailable', note: '无可验证的许可来源：按规范不展示官方 JIF、JCR 分区或中科院分区数值。' }),
  ],
}

describe('venue metrics presentation', () => {
  it('labels the CCF rank and the internal proxy with their sources, never as an impact factor', () => {
    const rows = venueRows(DATA, 'zh')
    const ccf = rows.find(row => row.type === 'ccf_rank')
    expect(ccf.value).toBe('A')
    expect(ccf.source).toContain('CCF 推荐国际学术会议和期刊目录')
    expect(ccf.source).toContain('未核验')
    const proxy = rows.find(row => row.type === 'citation_proxy')
    expect(proxy.label).toContain('内部引用代理指标')
    expect(proxy.label).not.toMatch(/^影响因子|Impact factor/)
    expect(proxy.value).toBe('2.35')
    expect(proxy.source).toContain('openalex')
  })

  it('shows 未知 with the reason when no verified official metric exists', () => {
    const jif = venueRows(DATA, 'zh').find(row => row.type === 'official_jif')
    expect(jif.value).toBe('未知')
    expect(jif.reason).toContain('无可验证的许可来源')
  })

  it('never renders a number without a source', () => {
    const rows = venueRows({ metrics: [metric({ metric_type: 'citation_proxy', value: 3.1, source_name: '' })] }, 'zh')
    expect(rows[0].value).toBe('未知')
    expect(rows[0].reason).toContain('来源')
  })

  it('reports 未知 with a reason when the venue has no metric at all or the request failed', () => {
    expect(venueRows({ metrics: [] }, 'zh')[0]).toMatchObject({ value: '未知' })
    expect(venueRows(null, 'zh')[0].reason).toBeTruthy()
    expect(venueRows({ error: '请求失败' }, 'zh')[0].reason).toContain('请求失败')
  })

  it('puts at most two sourced badges on the card', () => {
    const badges = venueBadges(DATA, 'zh')
    expect(badges.length).toBeLessThanOrEqual(2)
    expect(badges[0].text).toContain('CCF A')
    badges.forEach(badge => expect(badge.source).toBeTruthy())
    expect(badges.map(badge => badge.text).join(' ')).not.toContain('影响因子 2')
    expect(venueBadges({ metrics: [] }, 'zh')).toEqual([])
  })
})
