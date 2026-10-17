// 重新冻结首页模板块。
//
//   node src/__baseline__/freeze-home-template.mjs           # 只比较，退出码 0/1
//   node src/__baseline__/freeze-home-template.mjs --write   # 覆盖基线
//
// 只有「首页确实被有意改动」时才用 --write，并在提交信息里写明原因。
// 平时用比较模式：它等价于测试里那条逐字节断言，但能直接从命令行看差异。
import { readFileSync, writeFileSync } from 'node:fs'
import { extractHomeBlock } from './home-block.js'

const SOURCE = new URL('../App.vue', import.meta.url)
const BASELINE = new URL('./home-template.html', import.meta.url)

const current = extractHomeBlock(readFileSync(SOURCE, 'utf8'))
const write = process.argv.includes('--write')

if (write) {
  writeFileSync(BASELINE, current, 'utf8')
  console.log(`wrote ${current.split('\n').length - 1} lines to src/__baseline__/home-template.html`)
  process.exit(0)
}

let frozen = ''
try { frozen = readFileSync(BASELINE, 'utf8') } catch { frozen = '' }
if (frozen === current) {
  console.log('home block matches the frozen baseline')
  process.exit(0)
}
const frozenLines = frozen.split('\n')
const currentLines = current.split('\n')
for (let index = 0; index < Math.max(frozenLines.length, currentLines.length); index += 1) {
  if (frozenLines[index] !== currentLines[index]) {
    console.error(`first difference at line ${index + 1}`)
    console.error(`  baseline: ${(frozenLines[index] || '').slice(0, 160)}`)
    console.error(`  App.vue : ${(currentLines[index] || '').slice(0, 160)}`)
    break
  }
}
process.exit(1)
