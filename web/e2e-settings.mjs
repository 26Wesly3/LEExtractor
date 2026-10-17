// Screenshot the v3.2 settings page so the model-configuration entry can be checked.
//   node e2e-settings.mjs
import { chromium } from 'playwright'
import fs from 'node:fs'

const BASE = process.env.E2E_BASE || 'http://127.0.0.1:8000'
const OUT = process.env.E2E_OUT || 'e2e-shots'
fs.mkdirSync(OUT, { recursive: true })

const browser = await chromium.launch({ channel: 'msedge', headless: true })
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
const problems = []
page.on('pageerror', error => problems.push(`pageerror: ${error.message}`))
page.on('console', message => { if (message.type() === 'error') problems.push(`console.error: ${message.text()}`) })

const summary = { problems, steps: [] }
try {
  await page.goto(`${BASE}/v32/settings`, { waitUntil: 'networkidle', timeout: 30000 })
  await page.waitForSelector('[data-settings-area="model"]', { timeout: 20000 })

  const select = page.locator('[data-settings-profile="ai_model"]')
  const options = await select.evaluate(node => [...node.options].map(o => ({ value: o.value, text: o.textContent.trim() })))
  const status = (await page.locator('[data-settings-area="model"] [data-settings-status]').textContent()) || ''
  summary.steps.push({
    step: 'AI 模型 area',
    provider_options: options,
    status: status.trim(),
    has_key_input: await page.locator('[data-settings-secret="ai_model"]').count(),
    has_model_input: await page.locator('[data-settings-model="ai_model"]').count(),
  })
  await page.locator('[data-settings-area="model"]').screenshot({ path: `${OUT}/10-settings-model.png` })
  summary.shot = `${OUT}/10-settings-model.png`
  summary.ok = problems.length === 0
} catch (error) {
  summary.problems.push(`fatal: ${error.message}`)
  summary.ok = false
} finally {
  await browser.close()
  console.log(JSON.stringify(summary, null, 2))
}
