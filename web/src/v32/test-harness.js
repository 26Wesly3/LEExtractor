// Shared vitest harness for the v3.2 components. No network: every request is
// answered by the in-process responder table below.
import { createApp, nextTick } from 'vue'

export function jsonResponse(data, status = 200) {
  return {
    ok: status < 400,
    status,
    statusText: status < 400 ? 'OK' : 'Error',
    text: async () => JSON.stringify(data),
  }
}

export function emptyResponse(status = 204) {
  return { ok: status < 400, status, statusText: 'No Content', text: async () => '' }
}

/** Origin-style responder table for a fetch stub. */
export function createResponders(initial = []) {
  const table = [...initial]
  const on = (method, match, handler) => { table.push({ method: method.toUpperCase(), match, handler }); return api }
  const api = {
    calls: [],
    on,
    respond(method, match, handler) { return on(method, match, handler) },
    resolve(url, options = {}) {
      const parsed = new URL(url, 'http://localhost')
      const method = (options.method || 'GET').toUpperCase()
      const call = { url: parsed.pathname, query: parsed.searchParams, method, headers: options.headers || {}, body: undefined }
      if (typeof options.body === 'string') { try { call.body = JSON.parse(options.body) } catch { call.body = options.body } }
      else if (options.body instanceof FormData) call.body = options.body
      api.calls.push(call)
      const row = table.find(entry => entry.method === method && (typeof entry.match === 'string' ? entry.match === parsed.pathname : entry.match.test(parsed.pathname)))
      if (!row) return jsonResponse({ error: { code: 'unexpected', message: `no responder for ${method} ${parsed.pathname}`, retryable: false, details: {}, request_id: 'r1' } }, 404)
      return row.handler(call, table)
    },
  }
  return api
}

/** Minimal fetch stub. Returns the responder table. */
export function stubFetch(responses) {
  const table = responses || createResponders()
  globalThis.fetch = async (url, options) => table.resolve(url, options)
  return table
}

/** A representative GET /api/v3/session payload. */
export function sessionResponder(api, token = 'csrf-token-abc') {
  api.on('GET', '/api/v3/session', () => jsonResponse({ authenticated: true, mode: 'anonymous_private', csrf_token: token, expires_at: '2026-10-18T00:00:00Z' }))
  return api
}

export async function settle(times = 40) {
  for (let index = 0; index < times; index += 1) { await Promise.resolve(); await nextTick() }
}

export function mountComponent(component, props = {}, options = {}) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const errors = []
  const app = createApp(component, props)
  app.config.errorHandler = error => errors.push(error)
  if (options.router) app.use(options.router)
  const instance = app.mount(host)
  return { app, host, instance, errors, unmount: () => { app.unmount(); host.remove() } }
}

export function text(selector) {
  return document.querySelector(selector)?.textContent?.trim() || ''
}

export function all(selector) {
  return [...document.querySelectorAll(selector)]
}

export function click(selector) {
  const node = document.querySelector(selector)
  if (!node) throw new Error(`missing node ${selector}`)
  node.click()
  return node
}

export function setValue(selector, value) {
  const node = document.querySelector(selector)
  if (!node) throw new Error(`missing node ${selector}`)
  node.value = value
  node.dispatchEvent(new Event('input', { bubbles: true }))
  return node
}
