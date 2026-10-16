// @vitest-environment node
// 首屏：截面高度与窄屏入口块各只允许一条附加规则，不得触碰字体、字标、求是鹰、动效或既有 .cover 规则。
//
// 实测（Playwright + 本机 Edge，真实布局，非 jsdom，scrollY=0）：
//   1440x900 封面 56..376，入口块 376..732，两个动作按钮底边 707,707  → 两个入口均在首屏内
//   1024x768 封面 56..348，入口块 348..704，两个动作按钮底边 679,679  → 两个入口均在首屏内
//   390x844  封面 56..376，入口块 376..822，卡片顶 532/689，动作按钮底边 640,797 → 均在 844 内
// 窄屏（<=599px）两张卡片上下排列，入口块 595 -> 446：导语限两行（59px）、卡片说明句隐藏，
// 两个入口的标题与动作按钮全部保留。封面不再压缩——压到约 193px 时求是鹰会压住字标。
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const css = readFileSync('src/styles.css', 'utf8')

/** 取出包含 marker 的 @media(max-width:599px) 规则块（按花括号配平，避免误配其它块）。 */
function narrowBlock(marker) {
  let index = -1
  while ((index = css.indexOf('@media(max-width:599px){', index + 1)) !== -1) {
    let depth = 0
    let end = css.length
    for (let cursor = index; cursor < css.length; cursor += 1) {
      if (css[cursor] === '{') depth += 1
      else if (css[cursor] === '}') {
        depth -= 1
        if (depth === 0) { end = cursor; break }
      }
    }
    const block = css.slice(index, end + 1)
    if (block.includes(marker)) return block
  }
  return ''
}

/** 块内所有选择器（去掉外层 @media 头与声明体）。 */
function selectorsOf(block) {
  const inner = block.slice(block.indexOf('{') + 1, block.lastIndexOf('}'))
  return inner
    .split('}')
    .map(part => part.split('{')[0].trim())
    .filter(Boolean)
    .flatMap(part => part.split(',').map(selector => selector.trim()))
    .filter(Boolean)
}

describe('home first screen', () => {
  it('shortens only the home cover, with a viewport-bounded cap', () => {
    const overrides = css.match(/\.theme-home \.cover\{[^}]*\}/g) || []
    expect(overrides).toHaveLength(1)
    expect(overrides[0]).toContain('height:auto')
    const cap = overrides[0].match(/min-height:min\((\d+)svh,(\d+)px\)/)
    expect(cap, `unexpected cover cap: ${overrides[0]}`).toBeTruthy()
    // 上限必须与视口相关（否则小屏封面不会随之收缩）。
    expect(Number(cap[1])).toBeLessThanOrEqual(40)
    expect(Number(cap[2])).toBeLessThanOrEqual(320)
  })

  it('leaves the original cover rule in place', () => {
    expect(css).toContain('.cover{height:calc(100svh - var(--topbar-h));min-height:420px')
  })

  it('compacts only the v3.2 entry block on narrow screens', () => {
    const block = narrowBlock('.le32-entries .le32-lead')
    expect(block, 'the narrow-screen v3.2 entry rule is missing').toBeTruthy()
    // 导语限两行；卡片说明句隐藏；两个入口的标题与动作按钮不受影响。
    expect(block).toContain('.le32-entries .le32-lead{display:-webkit-box;-webkit-line-clamp:2')
    expect(block).toContain('.le32-entry>p{display:none}')
    expect(block).not.toContain('.le32-entry-action')
    expect(block).not.toContain('.le32-entry h2')
    // 该规则只允许选择 v3.2 自己的入口类，不能顺着它改动基线选择器。
    const selectors = selectorsOf(block)
    expect(selectors.length).toBeGreaterThanOrEqual(2)
    selectors.forEach(selector => expect(selector.startsWith('.le32-'), `baseline selector reached: ${selector}`).toBe(true))
  })

  it('keeps the font, the design tokens, the eagle and the motion rules untouched', () => {
    expect(css.match(/@font-face/g)).toHaveLength(1)
    expect(css).toContain("@font-face{font-family:Anton;src:url('/assets/anton.ttf') format('truetype');font-display:swap}")
    expect(css).toContain('--font-sans:Inter')
    expect(css).toContain('.eagle{position:absolute;left:24px;bottom:24px;width:220px;height:auto;max-height:160px;object-fit:contain;opacity:.75}')
    expect(css).toContain('.wordmark{font-family:var(--font-display)')
    expect(css).toContain('@media(prefers-reduced-motion:reduce)')
    expect(css).toContain('@keyframes le-drawer-in')
    expect(css).toContain('@keyframes le-nav-in')
  })
})
