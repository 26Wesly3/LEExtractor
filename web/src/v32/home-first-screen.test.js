// @vitest-environment node
// 首页必须保持基线原样。
//
// 背景：为了让「从研究方向开始 / 从已有文献开始」进入首页首屏，这里曾经加过
// `.theme-home .cover{height:auto;min-height:min(38svh,320px)}` 和一条窄屏规则，
// 首页封面因此变矮、入场动效观感也变了。用户明确要求「首页要原封不动回来」，
// 所以那两条规则被移除，v3.2 改成 /v32 独立入口页。
//
// 这个文件现在守的是相反方向：任何人不许再通过样式表去改首页封面，也不许把
// v3.2 的入口塞回首页模板；同时基线的封面、字标转子、入场动画必须仍然存在。
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { extractHomeBlock } from '../__baseline__/home-block.js'

const css = readFileSync('src/styles.css', 'utf8')
const app = readFileSync('src/App.vue', 'utf8')
// S0 冻结的首页模板块（UI_INTEGRATION_PLAN.md 第 4 节验证 1）。
// 重新生成用 `node src/__baseline__/freeze-home-template.mjs --write`。
const homeTemplate = readFileSync('src/__baseline__/home-template.html', 'utf8')

describe('the baseline home is left exactly as it was', () => {
  it('never overrides the home cover from the stylesheet', () => {
    expect(css).not.toMatch(/\.theme-home\s+\.cover\s*\{/)
    expect(css).not.toContain('min(38svh')
    // And no narrow-screen rule may reach the v3.2 entry block from the global sheet.
    expect(css).not.toContain('.le32-entries .le32-lead')
    expect(css).not.toContain('.le32-entry>p{display:none}')
  })

  it('keeps the original full-viewport cover rule', () => {
    expect(css).toMatch(/\.cover\s*\{[^}]*height:\s*calc\(100svh - var\(--topbar-h\)\)/)
  })

  it('keeps the baseline home template: cover, rotor, hero button and the three actions', () => {
    // The cover, the pointer-driven rotor and the entrance animation are the
    // things the user noticed were gone.
    expect(app).toContain('class="cover"')
    expect(app).toContain('hero-button')
    expect(app).toContain('rotorMove')
    expect(app).toContain("querySelector('.cover')")
    expect(app).toMatch(/\.animate\(/)
    // The original three home actions.
    expect(app).toContain('开始检索')
    expect(app).toContain('创建离线演示项目')
    expect(app).toContain('恢复已有项目')
    // 运行概况 / 项目里程碑 stay on the home page.
    expect(app).toContain('HomeOverview')
  })

  it('does not carry the v3.2 entry block in the home template any more', () => {
    expect(app).not.toContain('EntryChoice')
    expect(app).not.toContain('startV32Entry')
    expect(app).not.toContain('data-home-secondary')
  })

  it('freezes the whole home template byte for byte', () => {
    // 不是"关键片段还在"这种松断言：整块 `<template v-else-if="page==='home'">`
    // 必须与 src/__baseline__/home-template.html 完全相同。路由、外壳、导航可以改，
    // 首页这一块不行。
    expect(extractHomeBlock(app)).toBe(homeTemplate)
  })

  it('keeps the frozen snapshot itself free of v3.2 entry markup', () => {
    expect(homeTemplate).toContain('class="cover"')
    expect(homeTemplate).toContain('id="rotor"')
    expect(homeTemplate).toContain('id="eagleCanvas"')
    expect(homeTemplate).toContain('HomeOverview')
    expect(homeTemplate).not.toContain('le32-')
  })
})
