// @vitest-environment jsdom
// 设置：三个凭据区域、明文不回显、AI 只有两种模式、没有校园登录入口。
import { beforeEach, describe, expect, it, vi } from 'vitest'
import SettingsPanels from './SettingsPanels.vue'
import { V32Error } from './client.js'
import { all, click, mountComponent, settle, setValue, text } from './test-harness.js'

function fakeClient(overrides = {}) {
  return {
    credentials: vi.fn(async () => ({
      items: [
        { provider_id: 'semantic_scholar', kind: 'retrieval', configured: true, updated_at: '2026-10-16T00:00:00Z' },
        { provider_id: 'ai_model', kind: 'model', configured: false, updated_at: null },
        { provider_id: 'zotero', kind: 'zotero', configured: false, updated_at: null },
      ],
    })),
    saveCredential: vi.fn(async (providerId, body) => ({ provider_id: providerId, kind: body.kind, configured: true, updated_at: '2026-10-17T00:00:00Z' })),
    deleteCredential: vi.fn(async () => null),
    modelPresets: vi.fn(async () => ({
      items: [
        { id: 'openai', label: 'OpenAI', base_url: 'https://api.openai.com/v1', dialect: 'openai_compatible', default_model: 'gpt-5.5' },
        { id: 'custom', label: '自定义 / 中转站', base_url: '', dialect: 'openai_compatible', default_model: '' },
      ],
    })),
    probeModel: vi.fn(async () => ({ ok: true, dialect: 'openai_compatible', host: 'relay.example.com', models: ['gpt-4o', 'gpt-4o-mini'], latency_ms: 12 })),
    saveModelConfig: vi.fn(async body => ({ provider_id: 'ai_model', kind: 'model', configured: true, updated_at: '2026-10-17T00:00:00Z', persisted: Boolean(body.persist), host: 'relay.example.com', dialect: 'openai_compatible' })),
    ...overrides,
  }
}

beforeEach(() => { document.body.innerHTML = '' })

describe('SettingsPanels', () => {
  it('renders the three credential areas and no campus sign-in affordance', async () => {
    const mounted = mountComponent(SettingsPanels, { lang: 'zh', client: fakeClient() })
    await settle()
    expect(all('[data-settings-area]')).toHaveLength(3)
    expect(text('[data-settings-area="retrieval"] h2')).toBe('检索服务')
    expect(text('[data-settings-area="model"] h2')).toBe('AI 模型')
    expect(text('[data-settings-area="zotero"] h2')).toBe('Zotero')
    expect(text('[data-settings-area="retrieval"] [data-settings-status]')).toBe('已配置')
    expect(text('[data-settings-area="zotero"] [data-settings-status]')).toBe('未配置')

    const body = document.body.textContent
    ;['登录', 'CARSI', 'CAS', '统一身份', '浙大通行证', 'SSO', 'Sign in', 'Log in'].forEach(word => {
      expect(body.toLowerCase()).not.toContain(word.toLowerCase())
    })
    expect(document.querySelectorAll('a[href*="cas"],a[href*="sso"]')).toHaveLength(0)
    mounted.unmount()
  })

  it('offers exactly 基础模式 and AI 增强 and states what AI 增强 sends', async () => {
    const mounted = mountComponent(SettingsPanels, { lang: 'zh', client: fakeClient() })
    await settle()
    const modes = all('[data-settings-mode]')
    expect(modes).toHaveLength(2)
    expect(modes.map(node => node.getAttribute('data-settings-mode'))).toEqual(['basic', 'ai_enhanced'])
    expect(text('[data-settings-mode="basic"]')).toContain('基础模式')
    expect(text('[data-settings-mode="ai_enhanced"]')).toContain('AI 增强')
    // 没有语义未定义的 "deep 模式"。注意不能扫整个 body 的 "deep"：模型供应商
    // DeepSeek 是正当出现的，要断言的是「没有 deep 这个模式」。
    expect(all('[data-settings-mode]').map(node => node.getAttribute('data-settings-mode')))
      .not.toContain('deep')
    expect(document.body.textContent.toLowerCase()).not.toContain('deep mode')
    expect(text('[data-settings-mode-note]')).toContain('不调用任何远程模型')

    click('[data-settings-mode="ai_enhanced"]')
    await settle()
    expect(text('[data-settings-mode-note]')).toContain('元数据')
    mounted.unmount()
  })

  it('never echoes a plaintext credential after saving and clears the input', async () => {
    const client = fakeClient()
    const mounted = mountComponent(SettingsPanels, { lang: 'zh', client })
    await settle()
    const secret = 'plaintext-credential-placeholder'
    setValue('[data-settings-secret="semantic_scholar"]', secret)
    await settle()
    expect(document.querySelector('[data-settings-secret="semantic_scholar"]').value).toBe(secret)
    click('[data-settings-area="retrieval"] [data-settings-save]')
    await settle()
    expect(client.saveCredential).toHaveBeenCalledTimes(1)
    const [providerId, body] = client.saveCredential.mock.calls[0]
    expect(providerId).toBe('semantic_scholar')
    expect(body).toEqual({ kind: 'retrieval', secret })
    // 明文不再出现在 DOM 中，输入框已清空。
    expect(document.body.textContent).not.toContain(secret)
    expect(document.querySelector('[data-settings-secret="semantic_scholar"]').value).toBe('')
    expect(text('[data-settings-status]')).toContain('已配置')
    mounted.unmount()
  })

  it('probes a relay, lists its models and saves the endpoint without echoing the key', async () => {
    const client = fakeClient()
    const mounted = mountComponent(SettingsPanels, { lang: 'zh', client })
    await settle()
    // 必须能选服务商，也必须能填中转站地址。
    const preset = document.querySelector('[data-settings-preset="ai_model"]')
    expect(preset, 'the model area must offer a provider choice').toBeTruthy()
    expect([...preset.options].map(option => option.value)).toEqual(['openai', 'custom'])
    setValue('[data-settings-preset="ai_model"]', 'custom')
    preset.dispatchEvent(new Event('change', { bubbles: true }))
    setValue('[data-settings-base-url="ai_model"]', 'https://relay.example.com/v1')
    setValue('[data-settings-secret="ai_model"]', 'placeholder-model-credential')
    await settle()

    click('[data-settings-probe="ai_model"]')
    await settle()
    expect(client.probeModel.mock.calls[0][0]).toMatchObject({
      preset_id: 'custom',
      base_url: 'https://relay.example.com/v1',
      api_key: 'placeholder-model-credential',
    })
    expect(text('[data-settings-probe-ok]')).toContain('relay.example.com')
    // 拉到的模型进入选择框，默认选第一个。
    const modelSelect = document.querySelector('[data-settings-model="ai_model"]')
    expect(modelSelect.tagName).toBe('SELECT')
    expect([...modelSelect.options].map(option => option.value)).toEqual(['gpt-4o', 'gpt-4o-mini'])

    click('[data-settings-area="model"] [data-settings-save]')
    await settle()
    expect(client.saveModelConfig.mock.calls[0][0]).toMatchObject({
      preset_id: 'custom',
      base_url: 'https://relay.example.com/v1',
      dialect: 'openai_compatible',
      model: 'gpt-4o',
      api_key: 'placeholder-model-credential',
      persist: true,
    })
    expect(document.body.textContent).not.toContain('placeholder-model-credential')
    mounted.unmount()
  })

  it('reports a credential read failure without inventing a configured state', async () => {
    const client = fakeClient({ credentials: vi.fn(async () => { throw new V32Error({ code: 'network_error', message: '无法连接到本地服务', retryable: true }) }) })
    const mounted = mountComponent(SettingsPanels, { lang: 'zh', client })
    await settle()
    expect(text('[data-settings-error]')).toBe('无法连接到本地服务')
    expect(text('[data-settings-area="retrieval"] [data-settings-status]')).toBe('未配置')
    mounted.unmount()
  })
})
