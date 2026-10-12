// 首页运行概况的数据通道：首屏一次读取 + 之后由服务端 SSE 推送（真实回传，不是定时轮询）。
// 所有调用都"失败即忽略"：统计不可用绝不能阻塞首页或进入检索。
import { post, request } from './api.js'

export const fetchStats = () => request('/stats')

const swallow = () => null

export const recordHomeView = entryId => post('/stats/home-view', { entry_id: entryId }).catch(swallow)
export const recordSearchEntryClick = entryId => post('/stats/search-entry-click', { entry_id: entryId }).catch(swallow)

/** 每次页面进入生成一个幂等 id，服务端据此在同一时间窗内只计一次。 */
export function newEntryId() {
  try {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  } catch { /* 忽略：回退到随机串 */ }
  return `e-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

// 同一次页面进入共用同一个 id：首页访问与检索入口点击都归属这次进入。
let currentEntryId = ''
export function pageEntryId() {
  if (!currentEntryId) currentEntryId = newEntryId()
  return currentEntryId
}

/**
 * 订阅运行概况实时流。返回取消订阅函数。
 * 不支持 EventSource 或连接失败时安静降级（页面仍显示首屏读取到的数字）。
 */
export function subscribeStats(onUpdate) {
  if (typeof EventSource !== 'function') return () => {}
  let source = null
  try { source = new EventSource('/api/stats/stream') } catch { return () => {} }
  const handler = event => {
    try { onUpdate(JSON.parse(event.data)) } catch { /* 忽略坏帧 */ }
  }
  source.addEventListener('stats', handler)
  source.onerror = () => { /* 浏览器自动重连；失败不影响页面 */ }
  return () => {
    try { source.removeEventListener('stats', handler); source.close() } catch { /* 忽略 */ }
  }
}
