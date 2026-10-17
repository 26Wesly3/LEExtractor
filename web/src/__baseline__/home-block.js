// 首页模板块的唯一截取规则。
//
// 生成（freeze-home-template.mjs）与断言（v32/home-first-screen.test.js）共用
// 这一个函数，保证「逐字节相同」在两侧是同一套行为：先按行切开、去掉 CRLF 差异，
// 再从 `<template v-else-if="page==='home'">` 截到它自己的 `</template>`。
//
// 这是 UI 整合计划 S0 的产物：S1–S8 会重排路由、外壳与导航，但首页这一块
// 不允许变，除非有人显式重新生成基线并说明理由。
export const HOME_BLOCK_OPEN = `<template v-else-if="page==='home'">`
export const HOME_BLOCK_CLOSE = '</template>'

export function extractHomeBlock(source) {
  const lines = String(source).split(/\r?\n/)
  const start = lines.findIndex(line => line.trim() === HOME_BLOCK_OPEN)
  if (start === -1) throw new Error(`home block opening tag not found: ${HOME_BLOCK_OPEN}`)
  const end = lines.findIndex((line, index) => index > start && line.trim() === HOME_BLOCK_CLOSE)
  if (end === -1) throw new Error('home block closing tag not found')
  // 块内不允许再嵌套 template，否则截取会提前结束：这里直接报错而不是猜。
  for (let index = start + 1; index < end; index += 1) {
    if (lines[index].includes('<template')) throw new Error(`nested <template> inside the home block at line ${index + 1}`)
  }
  return `${lines.slice(start, end + 1).join('\n')}\n`
}
