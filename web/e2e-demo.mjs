// 完整演示跑：从入口 → 文献检索 → 核心文献筛选推荐 → 研究领域概述 → 创新点探索 → 导出。
//
//   node e2e-demo.mjs                       # 对着本机 8000 端口跑
//   E2E_BASE=http://127.0.0.1:8000 node e2e-demo.mjs
//
// 用真实浏览器、真实后端、真实检索来源；不做任何 mock。截图写到
// docs/demo/screenshots/，同时打印一份 JSON 摘要（项目 id、各阶段计数、耗时）。
// AI 增强不启用：那会消耗用户的模型额度，演示只走本地排序。
import { chromium } from 'playwright'
import fs from 'node:fs'
import path from 'node:path'

const BASE = process.env.E2E_BASE || 'http://127.0.0.1:8000'
const OUT = process.env.E2E_OUT || path.join('..', 'docs', 'demo', 'screenshots')
const TOPIC = process.env.E2E_TOPIC || '薄荷干旱胁迫的转录组响应与次生代谢'
fs.mkdirSync(OUT, { recursive: true })

const shots = []
const steps = []
const problems = []

const browser = await chromium.launch({ channel: 'chrome', headless: true })
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
page.on('pageerror', error => problems.push(`pageerror: ${error.message}`))
page.on('console', message => { if (message.type() === 'error') problems.push(`console.error: ${message.text()}`) })

async function shot(name) {
  const file = path.join(OUT, `${name}.png`)
  await page.screenshot({ path: file, fullPage: false })
  shots.push(file)
  return file
}

async function stage(name) {
  const link = page.locator('.le-nav-stage', { hasText: name })
  await link.first().click()
  await page.waitForTimeout(1200)
  await shot(`0${shots.length + 1}-${name}`)
}

try {
  // 1. 入口：两个入口都在
  await page.goto(`${BASE}/search`, { waitUntil: 'load' })
  await page.waitForSelector('[data-v32-entry-action="topic"]', { timeout: 20000 })
  await shot('01-entry')

  // 2. 输入研究方向 → 建项目 → 进「文献检索」
  await page.click('[data-v32-entry-action="topic"]')
  await page.waitForFunction(() => location.pathname.startsWith('/projects/'), { timeout: 30000 })
  await page.waitForSelector('[data-v32-research-text]', { timeout: 20000 })
  const projectId = new URL(page.url()).pathname.split('/')[2]
  steps.push({ step: 'create project', projectId })

  await page.fill('[data-v32-research-text]', TOPIC)
  await shot('02-anchors-filled')
  const started = Date.now()
  await page.click('[data-v32-discover]')

  // 3. 等检索任务到终态。进度条可能因为轮询/渲染原因留在 DOM 里，所以这里
  //    以「出现结果或超时」为准，而不是死等某个元素消失。
  let progressSeen = false
  try {
    await page.waitForSelector('[data-v32-progress]', { timeout: 20000 })
    progressSeen = true
    await shot('03-progress')
  } catch { problems.push('progress bar never appeared') }
  const deadline = Date.now() + 240000
  let finished = false
  while (Date.now() < deadline) {
    const state = await page.evaluate(() => ({
      progress: Boolean(document.querySelector('[data-v32-progress]')),
      cards: document.querySelectorAll('.le32-card').length,
      notice: document.querySelector('[data-v32-notice], [data-v32-ranking-stage]')?.textContent || '',
    }))
    if (!state.progress && (state.cards > 0 || state.notice.trim().length > 0)) { finished = true; break }
    if (state.cards > 0) { finished = true; break }
    await page.waitForTimeout(3000)
  }
  if (!finished) problems.push('discovery did not settle within 240s')
  const discoverSeconds = Math.round((Date.now() - started) / 1000)
  steps.push({ step: 'discovery finished', progressSeen, finished, discoverSeconds })
  await shot('04-discovered')

  // 4. 核心文献筛选推荐
  await stage('核心文献筛选推荐')
  await page.waitForTimeout(2500)
  const cards = await page.locator('.le32-card').count()
  const firstTitle = (await page.locator('.le32-card-title').first().textContent().catch(() => '')) || ''
  const reasons = await page.locator('.le32-reason, [data-v32-reason]').allTextContents().catch(() => [])
  steps.push({ step: 'papers', cards, firstTitle: firstTitle.trim().slice(0, 60), reasonSamples: reasons.slice(0, 3) })

  // 5. 打开一条文献详情（抽屉）
  if (cards > 0) {
    await page.locator('.le32-card-open').first().click()
    await page.waitForSelector('[data-v32-drawer]', { timeout: 20000 })
    await page.waitForTimeout(800)
    await shot('05-papers-drawer')
    await page.keyboard.press('Escape')
    await page.waitForTimeout(400)
  }

  // 6. 研究领域概述
  await stage('研究领域概述')
  const threadsText = (await page.locator('[data-v32-threads]').textContent().catch(() => '')) || ''
  const journals = await page.locator('[data-v32-journals] li').allTextContents().catch(() => [])
  steps.push({ step: 'threads', hasBranches: await page.locator('[data-v32-branch]').count(), journalRows: journals.length, journalSamples: journals.slice(0, 3) })

  // 7. 创新点探索
  await stage('创新点探索')
  const insightText = (await page.locator('[data-v32-insight]').textContent().catch(() => '')) || ''
  steps.push({ step: 'insight', textLength: insightText.length })

  // 8. 导出：打开菜单看格式
  await page.locator('.le-nav-stage', { hasText: '核心文献筛选推荐' }).first().click()
  await page.waitForTimeout(1200)
  const exportMenu = page.locator('[data-v32-export]').first()
  if (await exportMenu.count()) {
    await exportMenu.locator('summary').click()
    await page.waitForTimeout(500)
    await shot('08-export-menu')
    const formats = await page.locator('[data-v32-export-format]').evaluateAll(nodes => nodes.map(node => node.textContent.trim()))
    steps.push({ step: 'export', formats })
  }

  steps.push({ step: 'threads text', empty: threadsText.trim().length === 0 })
} catch (error) {
  problems.push(`fatal: ${error.message}`)
  await shot('99-failure').catch(() => {})
} finally {
  await browser.close()
  const summary = { base: BASE, topic: TOPIC, shots, steps, problems }
  fs.writeFileSync(path.join(OUT, 'summary.json'), JSON.stringify(summary, null, 2), 'utf8')
  console.log(JSON.stringify(summary, null, 2))
}
