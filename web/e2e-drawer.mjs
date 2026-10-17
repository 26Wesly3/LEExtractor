// Real-browser check of the paper-detail drawer's "获取全文" block.
//
//   node e2e-drawer.mjs <projectId>
//
// Opens the papers stage of an existing project, opens the first record, and
// screenshots the drawer. Prints the rendered access text so the labels can be
// checked without squinting at a picture.
import { chromium } from 'playwright'
import fs from 'node:fs'

const BASE = process.env.E2E_BASE || 'http://127.0.0.1:8000'
const projectId = process.argv[2]
if (!projectId) {
  console.error('usage: node e2e-drawer.mjs <projectId>')
  process.exit(2)
}
const OUT = process.env.E2E_OUT || 'e2e-shots'
fs.mkdirSync(OUT, { recursive: true })

const browser = await chromium.launch({ channel: 'msedge', headless: true })
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
const problems = []
page.on('pageerror', error => problems.push(`pageerror: ${error.message}`))
page.on('console', message => { if (message.type() === 'error') problems.push(`console.error: ${message.text()}`) })

const summary = { projectId, steps: [], problems }
try {
  await page.goto(`${BASE}/v32/projects/${projectId}/papers`, { waitUntil: 'networkidle', timeout: 30000 })
  await page.waitForSelector('.le32-card', { timeout: 25000 })
  summary.steps.push({ step: 'papers stage', cards: await page.locator('.le32-card').count() })

  await page.locator('.le32-card-open').first().click()
  await page.waitForSelector('[data-v32-drawer]', { timeout: 15000 })

  const note = (await page.locator('[data-v32-access-note]').textContent()) || ''
  const options = await page.locator('[data-v32-access-option]').evaluateAll(nodes => nodes.map(node => ({
    kind: node.getAttribute('data-v32-access-option'),
    text: node.textContent.replace(/\s+/g, ' ').trim(),
  })))
  summary.steps.push({ step: 'drawer access block', note: note.replace(/\s+/g, ' ').trim(), options })

  // Screenshot the drawer itself rather than the whole page.
  const drawer = page.locator('[data-v32-drawer]')
  await drawer.screenshot({ path: `${OUT}/drawer-access.png` })
  summary.shot = `${OUT}/drawer-access.png`
  summary.ok = problems.length === 0
} catch (error) {
  summary.problems.push(`fatal: ${error.message}`)
  summary.ok = false
} finally {
  await browser.close()
  console.log(JSON.stringify(summary, null, 2))
}
