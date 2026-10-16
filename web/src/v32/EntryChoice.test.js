// @vitest-environment jsdom
// 首页入口：恰好两个主入口。
import { beforeEach, describe, expect, it } from 'vitest'
import EntryChoice from './EntryChoice.vue'
import { all, click, mountComponent, settle, text, setValue } from './test-harness.js'

beforeEach(() => { document.body.innerHTML = '' })

describe('EntryChoice', () => {
  it('offers exactly the two primary entries', async () => {
    const seen = []
    const mounted = mountComponent(EntryChoice, { lang: 'zh', onSelect: payload => seen.push(payload) })
    expect(all('[data-v32-entry]')).toHaveLength(2)
    expect(text('[data-v32-entry="topic"] h2')).toBe('从研究方向开始')
    expect(text('[data-v32-entry="pdf"] h2')).toBe('从已有文献开始')
    expect(text('[data-v32-entry-action="topic"]')).toBe('输入研究方向')
    expect(text('[data-v32-entry-action="pdf"]')).toBe('上传已有文献')
    click('[data-v32-entry-action="pdf"]')
    await settle()
    expect(seen).toEqual([{ mode: 'pdf' }])
    mounted.unmount()
  })

  it('disables both entries while an entry is starting and shows the failure copy', () => {
    const mounted = mountComponent(EntryChoice, { lang: 'zh', busy: true, error: '请求未完成，请稍后重试。' })
    all('[data-v32-entry-action]').forEach(node => expect(node.disabled).toBe(true))
    expect(text('.le32-entry-error')).toBe('请求未完成，请稍后重试。')
    mounted.unmount()
  })
})
