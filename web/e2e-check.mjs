// Real-browser end-to-end check of the v3.2 flow. Not a unit test.
//
//   node e2e-check.mjs
//
// Drives the actual shipped bundle in the installed Edge, walks
// /v32 -> 输入研究方向 -> workspace -> 开始发现核心文献 -> progress -> papers,
// and writes screenshots plus a JSON summary. If the UI cannot actually do the
// thing, this fails loudly instead of passing a mocked assertion.
import { chromium } from 'playwright'
import fs from 'node:fs'

const BASE = process.env.E2E_BASE || 'http://127.0.0.1:8000'
const OUT = process.env.E2E_OUT || 'e2e-shots'
fs.mkdirSync(OUT, { recursive: true })

const problems = []
const shots = []
const summary = { base: BASE, steps: [], problems, shots }

const browser = await chromium.launch({ channel: 'msedge', headless: true })
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
page.on('console', message => {
  if (message.type() === 'error') problems.push(`console.error: ${message.text()}`)
})
page.on('pageerror', error => problems.push(`pageerror: ${error.message}`))
page.on('requestfailed', request => {
  const url = request.url()
  if (url.includes('/api/')) problems.push(`requestfailed: ${request.method()} ${url} ${request.failure()?.errorText}`)
})

async function shot(name) {
  const path = `${OUT}/${name}.png`
  await page.screenshot({ path, fullPage: false })
  shots.push(path)
  return path
}

try {
  // 1. entry page
  await page.goto(`${BASE}/v32`, { waitUntil: 'networkidle', timeout: 30000 })
  await shot('01-entries')
  const entryButtons = await page.locator('[data-v32-entry-action]').count()
  summary.steps.push({ step: 'entry page', entry_buttons: entryButtons })

  // 2. click 输入研究方向 -> must create a project and navigate
  await page.click('[data-v32-entry-action="topic"]')
  await page.waitForFunction(() => location.pathname.startsWith('/v32/projects/'), { timeout: 25000 })
  const projectPath = new URL(page.url()).pathname
  await shot('02-workspace')
  summary.steps.push({ step: 'create project + enter workspace', path: projectPath })

  // 3. the workspace must show the four stage tabs and the anchors form
  const stages = await page.locator('[data-v32-stage]').count()
  const hasTextarea = await page.locator('[data-v32-research-text]').count()
  summary.steps.push({ step: 'workspace stage tabs', tabs: stages, has_research_textarea: hasTextarea })

  // 4. type a research direction and start discovery
  await page.fill('[data-v32-research-text]', '薄荷干旱胁迫的转录组响应与次生代谢')
  await shot('03-typed')
  await page.click('[data-v32-discover]')

  // 5. a progress indicator MUST appear while the job runs
  let progressSeen = false
  let progressStage = ''
  let progressPercent = ''
  try {
    await page.waitForSelector('[data-v32-progress]', { timeout: 20000 })
    progressSeen = true
    progressStage = (await page.locator('[data-v32-progress-stage]').first().textContent()) || ''
    progressPercent = (await page.locator('[data-v32-progress-percent]').first().textContent().catch(() => '')) || ''
    await shot('04-progress')
  } catch {
    problems.push('the progress bar never appeared after 开始发现核心文献')
  }
  summary.steps.push({ step: 'progress bar', visible: progressSeen, stage: progressStage.trim(), percent: progressPercent.trim() })

  // 6. the job must reach a terminal state (progress disappears)
  let finished = false
  try {
    await page.waitForFunction(() => !document.querySelector('[data-v32-progress]'), { timeout: 240000 })
    finished = true
  } catch {
    problems.push('the discovery job never left the running state within 240s')
  }
  await shot('05-finished')
  const notice = (await page.locator('[data-v32-notice], [data-v32-ranking-stage]').first().textContent().catch(() => '')) || ''
  summary.steps.push({ step: 'job finished', finished, notice: notice.trim() })

  // 7. the papers stage must list real records
  await page.click('[data-v32-stage="papers"]')
  await page.waitForTimeout(4000)
  await shot('06-papers')
  const cards = await page.locator('.le32-card').count()
  const firstTitle = (await page.locator('.le32-card-title').first().textContent().catch(() => '')) || ''
  const coverage = (await page.locator('[data-v32-coverage]').first().textContent().catch(() => '')) || ''
  summary.steps.push({
    step: 'papers stage',
    cards,
    first_title: firstTitle.trim().slice(0, 80),
    coverage: coverage.trim().slice(0, 120),
  })
  if (cards === 0) problems.push('the papers stage shows zero cards after a completed discovery')

  // 8. the insight stage must render the overview
  await page.click('[data-v32-stage="insight"]')
  await page.waitForTimeout(3000)
  await shot('07-insight')
  const insightText = ((await page.textContent('[data-v32-workspace]')) || '').length
  summary.steps.push({ step: 'insight stage', text_length: insightText })

  summary.ok = problems.length === 0
} catch (error) {
  problems.push(`fatal: ${error.message}`)
  await shot('99-failure').catch(() => {})
  summary.ok = false
} finally {
  await browser.close()
  fs.writeFileSync(`${OUT}/summary.json`, JSON.stringify(summary, null, 2), 'utf-8')
  console.log(JSON.stringify(summary, null, 2))
}
