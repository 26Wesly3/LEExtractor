// 首页像素基线截图（UI_INTEGRATION_PLAN.md S0）。
//
//   npx vite build
//   node e2e-home-baseline.mjs --tag before      # 改动前
//   node e2e-home-baseline.mjs --tag after       # 每步之后
//   node e2e-home-baseline.mjs --compare         # before 与 after 逐字节比较
//
// 做法：自己用 node:http 静态托管 web/dist（SPA 回退），Playwright 用真实 Chrome
// 打开 `/`，把所有 /api/** 拦成固定 JSON，所以数字每次一样。字体是本地
// `/assets/anton.ttf`，截图前等 `document.fonts.ready`。
//
// 冻结项：`emulateMedia({reducedMotion:'reduce'})` 关掉转子与鹰动画；
// 动效那张单独不减弱，并用遮罩盖住 #eagle（它按帧画字符画，无法逐帧复现）。
//
// 判断「像素差为 0」用的是同一浏览器、同一视口下 PNG 字节相同：输入确定时
// Chrome 的截图编码是确定的，字节不同即说明渲染结果真的变了。
import { chromium } from 'playwright'
import { createHash } from 'node:crypto'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { readFile } from 'node:fs/promises'
import http from 'node:http'
import path from 'node:path'

const OUT = process.env.E2E_OUT || 'e2e-shots'
const DIST = path.resolve('dist')
const PORT = Number(process.env.E2E_PORT || 4178)

const VIEWPORTS = [
  { width: 1440, height: 900 },
  { width: 1024, height: 768 },
  { width: 390, height: 844 },
]

const STATS = {
  launch: { date: '2026-09-28', days: 19 },
  home_views: { count: 1240 },
  search_entry_clicks: { count: 96 },
  search_runs: { count: 318 },
  records: { count: 24817 },
  runtime: { process_uptime_seconds: 7345 },
}
const PROJECTS = {
  items: [
    { project_id: 'p-demo', name: 'Mint drought transcriptome', topic: 'Transcriptome response of mint under drought stress', demo: false, updated_at: '2026-10-16T09:20:00+08:00' },
    { project_id: 'p-second', name: 'Waterlily cultivar morphology', topic: 'Morphological grading of waterlily cultivars', demo: false, updated_at: '2026-10-14T15:05:00+08:00' },
    { project_id: 'p-demo-2', name: 'Offline demo project', topic: 'Synthetic demo corpus', demo: true, updated_at: '2026-10-12T11:00:00+08:00' },
  ],
}

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.ttf': 'font/ttf',
  '.svg': 'image/svg+xml',
  '.woff2': 'font/woff2',
  '.ico': 'image/x-icon',
}

function startStaticServer() {
  const server = http.createServer(async (request, response) => {
    const url = new URL(request.url, 'http://127.0.0.1')
    const file = path.join(DIST, decodeURIComponent(url.pathname))
    if (!file.startsWith(DIST)) { response.writeHead(403).end(); return }
    try {
      const body = await readFile(file)
      response.writeHead(200, { 'content-type': MIME[path.extname(file)] || 'application/octet-stream' })
      response.end(body)
    } catch {
      // SPA 回退：/ 与任何非静态路径都返回 index.html。
      try {
        const body = await readFile(path.join(DIST, 'index.html'))
        response.writeHead(200, { 'content-type': MIME['.html'] })
        response.end(body)
      } catch {
        response.writeHead(404, { 'content-type': 'text/plain' })
        response.end('dist/index.html not found — run `npx vite build` first')
      }
    }
  })
  return new Promise(resolve => server.listen(PORT, '127.0.0.1', () => resolve(server)))
}

function sha256(buffer) {
  return createHash('sha256').update(buffer).digest('hex')
}

/** 给定 tag，返回该 tag 下所有截图文件的 { 视口键: 路径 }。 */
function shotsFor(tag) {
  const found = {}
  for (const viewport of VIEWPORTS) {
    const key = `${viewport.width}x${viewport.height}`
    for (const suffix of ['', '-full']) {
      const file = path.join(OUT, `home-${tag}-${key}${suffix}.png`)
      try { readFileSync(file); found[`${key}${suffix}`] = file } catch { /* 缺这张就是缺 */ }
    }
  }
  const motion = path.join(OUT, `home-${tag}-motion.png`)
  try { readFileSync(motion); found.motion = motion } catch { /* 同上 */ }
  return found
}

function compare(from, to) {
  const before = shotsFor(from)
  const after = shotsFor(to)
  const keys = [...new Set([...Object.keys(before), ...Object.keys(after)])].sort()
  const rows = []
  let identical = 0
  let different = 0
  for (const key of keys) {
    if (!before[key] || !after[key]) {
      rows.push({ shot: key, identical: false, reason: before[key] ? `missing in ${to}` : `missing in ${from}` })
      different += 1
      continue
    }
    const a = readFileSync(before[key])
    const b = readFileSync(after[key])
    const same = a.equals(b)
    if (same) identical += 1; else different += 1
    rows.push({ shot: key, identical: same, bytes_before: a.length, bytes_after: b.length, sha_before: sha256(a).slice(0, 16), sha_after: sha256(b).slice(0, 16) })
  }
  console.log(JSON.stringify({ compare: { from, to }, identical, different, rows }, null, 2))
  return different === 0 ? 0 : 1
}

/** 在页面里盖住 #eagle：字符画逐帧重绘，不参与像素比较。 */
async function maskEagle(page) {
  await page.evaluate(() => {
    const eagle = document.querySelector('#eagle')
    if (!eagle) return
    const box = eagle.getBoundingClientRect()
    const cover = document.createElement('div')
    cover.setAttribute('data-e2e-eagle-mask', 'true')
    Object.assign(cover.style, {
      position: 'fixed', left: '0', top: '0', width: `${box.width}px`, height: `${box.height}px`,
      transform: `translate(${box.left}px, ${box.top}px)`, background: '#f4f6f9', zIndex: '9999', pointerEvents: 'none',
    })
    document.body.appendChild(cover)
  })
}

function fulfillApi(route) {
  const url = new URL(route.request().url())
  if (url.pathname === '/api/projects') {
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(PROJECTS) })
  }
  if (url.pathname === '/api/stats/stream') {
    // 空 SSE：EventSource 不会重连风暴，页面用首屏数字。
    return route.fulfill({ status: 200, headers: { 'content-type': 'text/event-stream' }, body: '' })
  }
  if (url.pathname.startsWith('/api/stats')) {
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(STATS) })
  }
  return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [] }) })
}

async function collect(base, tag) {
  const browser = await chromium.launch({ channel: 'chrome', headless: true })
  const shots = []
  const problems = []
  try {
    for (const viewport of VIEWPORTS) {
      const page = await browser.newPage({ viewport, deviceScaleFactor: 1 })
      page.on('pageerror', error => problems.push(`pageerror: ${error.message}`))
      page.on('console', message => { if (message.type() === 'error') problems.push(`console.error: ${message.text()}`) })

      await page.route('**/api/**', fulfillApi)
      await page.emulateMedia({ reducedMotion: 'reduce' })
      await page.goto(`${base}/`, { waitUntil: 'load' })
      await page.evaluate(() => document.fonts.ready)
      await page.waitForTimeout(400)

      const key = `${viewport.width}x${viewport.height}`
      const top = path.join(OUT, `home-${tag}-${key}.png`)
      await page.screenshot({ path: top, animations: 'disabled', caret: 'hide' })
      shots.push(top)
      const full = path.join(OUT, `home-${tag}-${key}-full.png`)
      await page.screenshot({ path: full, fullPage: true, animations: 'disabled', caret: 'hide' })
      shots.push(full)
      await page.close()

      if (viewport.width === 1440) {
        // 动效那张：不减弱动效，只遮住鹰。
        const motionPage = await browser.newPage({ viewport, deviceScaleFactor: 1 })
        await motionPage.route('**/api/**', fulfillApi)
        await motionPage.goto(`${base}/`, { waitUntil: 'load' })
        await motionPage.evaluate(() => document.fonts.ready)
        await maskEagle(motionPage)
        await motionPage.waitForTimeout(400)
        const motion = path.join(OUT, `home-${tag}-motion.png`)
        await motionPage.screenshot({ path: motion, caret: 'hide' })
        shots.push(motion)
        await motionPage.close()
      }
    }
  } finally {
    await browser.close()
  }
  return { shots, problems }
}

async function main() {
  mkdirSync(OUT, { recursive: true })

  if (process.argv.includes('--compare')) {
    const from = process.env.E2E_FROM || 'before'
    const to = process.env.E2E_TO || 'after'
    process.exitCode = compare(from, to)
    return
  }

  const tagIndex = process.argv.indexOf('--tag')
  const tag = tagIndex >= 0 ? process.argv[tagIndex + 1] : ''
  if (!['before', 'after'].includes(tag)) {
    console.error('usage: node e2e-home-baseline.mjs --tag before|after | --compare')
    process.exitCode = 2
    return
  }

  const server = await startStaticServer()
  const base = `http://127.0.0.1:${PORT}`
  try {
    const { shots, problems } = await collect(base, tag)
    const summary = { tag, base, viewports: VIEWPORTS.map(v => `${v.width}x${v.height}`), shots, problems }
    writeFileSync(path.join(OUT, `home-${tag}.json`), JSON.stringify(summary, null, 2), 'utf8')
    console.log(JSON.stringify(summary, null, 2))
    if (problems.length) process.exitCode = 1
  } finally {
    server.close()
  }
}

await main()
