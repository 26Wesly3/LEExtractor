// @vitest-environment jsdom
// 回归：/settings 直接用路由渲染设置面板时没有人传 client。
// 之前 client 是 required prop，未传时页面上所有请求都是 undefined.method，
// 表现为「服务商下拉为空 + 检测失败」。
import { beforeEach, describe, expect, it } from 'vitest'
import SettingsPanels from './SettingsPanels.vue'
import { all, mountComponent, settle, text } from '../v32/test-harness.js'

beforeEach(() => { document.body.innerHTML = '' })

describe('SettingsPanels without a client prop', () => {
  it('still renders the areas and a non-empty provider list', async () => {
    globalThis.fetch = async () => ({ ok: true, status: 200, text: async () => JSON.stringify({ csrf_token: 'c', items: [] }) })
    const mounted = mountComponent(SettingsPanels, { lang: 'zh' })
    await settle(20)

    expect(all('[data-settings-area]')).toHaveLength(3)
    const preset = document.querySelector('[data-settings-preset="ai_model"]')
    expect(preset, 'the provider select must exist').toBeTruthy()
    expect([...preset.options].map(option => option.value)).toEqual(['openai', 'deepseek', 'anthropic', 'custom'])
    expect(text('[data-settings-base-url="ai_model"]') || document.querySelector('[data-settings-base-url="ai_model"]').value)
      .toBe('https://api.openai.com/v1')
    mounted.unmount()
  })
})
