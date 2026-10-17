// v3.2 API client for the /api/v3 surface.
//
// Contract facts this file depends on (litsearch/contracts/openapi.json):
//   * every mutating operation requires the `X-CSRF-Token` header, read from
//     GET /api/v3/session ($defs/SessionStatus.csrf_token);
//   * POST /api/projects, /discover, /papers/import, /judgements, /rerank,
//     /expand and /imports/{id}/retry additionally require an `Idempotency-Key`
//     of 16..128 characters — a retry must replay the same key, a new user
//     action must use a fresh one;
//   * every error response is the closed envelope
//     {"error": {code, message, retryable, details, request_id}}
//     ($defs/Error).
//
// A credential secret is NEVER put into a URL, a query string or a log line:
// only PUT /api/settings/credentials/{provider_id} carries it, in the JSON
// body, and the identifier in that path is the provider id — never the secret.

export class V32Error extends Error {
  constructor({ code, message, retryable, details, request_id, status } = {}) {
    super(message || code || `HTTP ${status || 'error'}`)
    this.name = 'V32Error'
    this.code = code || 'error'
    this.retryable = retryable === true
    this.details = details || {}
    this.requestId = request_id || ''
    this.status = status || 0
  }
}

/** Never let a secret reach a log line: only the method and the pathname. */
export const silentLogger = { debug() {}, info() {}, warn() {}, error() {} }

const BASE = '/api/v3'

// Actions that carry an idempotency key, mirroring the contract's parameter list.
export const IDEMPOTENT_ACTIONS = Object.freeze([
  'project.create', 'discover', 'import', 'judgement', 'rerank', 'expand', 'import.retry',
])

let sequence = 0

/** 16..128 chars, url-safe, unique per action and stable across a retry. */
export function newIdempotencyKey(label = 'act') {
  sequence += 1
  const stem = String(label).replace(/[^a-zA-Z0-9._-]/g, '-').slice(0, 40) || 'act'
  const body = `${stem}-${Date.now().toString(36)}-${sequence.toString(36)}-${Math.random().toString(36).slice(2, 14)}`
  return body.padEnd(16, '0').slice(0, 128)
}

export function isIdempotencyKey(value) {
  return typeof value === 'string' && value.length >= 16 && value.length <= 128
}

export function queryString(values = {}) {
  const params = new URLSearchParams()
  Object.entries(values).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return
    params.set(key, Array.isArray(value) ? value.join(',') : String(value))
  })
  return params.toString()
}

/** Only http(s) links may be rendered as an access entry point. */
export function safeUrl(value) {
  try {
    const url = new URL(String(value), 'https://leextractor.invalid')
    return url.protocol === 'https:' || url.protocol === 'http:' ? url.href : ''
  } catch { return '' }
}

export function createV32Client({
  base = BASE,
  fetchImpl,
  logger = silentLogger,
  timeoutMs = 30000,
} = {}) {
  const idempotency = new Map()
  let csrfToken = ''
  let sessionPromise = null

  function transport() {
    if (typeof fetchImpl === 'function') return fetchImpl
    if (typeof fetch === 'function') return fetch
    throw new V32Error({ code: 'no_transport', message: '当前环境没有可用的网络请求实现。' })
  }

  function headersFor(method, { idempotencyKey, form } = {}) {
    const headers = {}
    if (method !== 'GET') headers['X-CSRF-Token'] = csrfToken
    if (form === undefined) headers['Accept'] = 'application/json'
    if (idempotencyKey) headers['Idempotency-Key'] = idempotencyKey
    return headers
  }

  function urlFor(path, query) {
    const suffix = queryString(query)
    return `${base}${path}${suffix ? `?${suffix}` : ''}`
  }

  async function send(method, path, { body, query, idempotencyKey, form, signal } = {}) {
    const url = urlFor(path, query)
    const init = { method, headers: headersFor(method, { idempotencyKey, form }), credentials: 'same-origin', signal }
    if (body !== undefined) {
      init.headers['Content-Type'] = 'application/json'
      init.body = JSON.stringify(body)
    } else if (form !== undefined) {
      init.body = form
    }
    let timerId = null
    if (typeof setTimeout === 'function' && timeoutMs && !init.signal) {
      if (typeof AbortController === 'function') {
        const controller = new AbortController()
        init.signal = controller.signal
        timerId = setTimeout(() => { try { controller.abort() } catch { /* already settled */ } }, timeoutMs)
      } else {
        timerId = setTimeout(() => { /* no cancellation available in this runtime */ }, timeoutMs)
      }
    }
    // The logger receives no query string and no token: a secret can never be logged.
    logger.debug?.(`v32 ${method} ${path}`)
    let response
    try {
      response = await transport()(url, init)
    } catch (error) {
      logger.debug?.(`v32 ${method} ${path} transport error`)
      throw new V32Error({ code: 'network_error', message: '无法连接到本地服务，请确认服务仍在运行。', retryable: true, details: { reason: String(error?.name || 'error') } })
    } finally {
      if (timerId !== null) clearTimeout(timerId)
    }
    if (response.status === 204) return null
    // A real fetch Response exposes text(); some embedded/test transports only
    // expose json(). Accept either so an empty body still ends up as null.
    let data = null
    if (typeof response.text === 'function') {
      const text = await response.text()
      if (text) {
        try { data = JSON.parse(text) } catch { data = null }
      }
    } else if (typeof response.json === 'function' && response.status !== 204) {
      try { data = await response.json() } catch { data = null }
    }
    if (!response.ok) {
      const envelope = data && typeof data === 'object' && data.error ? data.error : null
      throw new V32Error({
        code: envelope?.code || 'http_error',
        message: envelope?.message || `请求失败（HTTP ${response.status}）`,
        retryable: envelope?.retryable === true,
        details: envelope?.details || {},
        request_id: envelope?.request_id || '',
        status: response.status,
      })
    }
    return data
  }

  /**
   * GET a file (not JSON). Returns { blob, filename, contentType }; an error
   * response is decoded into the same V32Error envelope as every other call.
   */
  async function download(path, query, fallbackName) {
    const url = urlFor(path, query)
    logger.debug?.(`v32 GET ${path} (download)`)
    let response
    try {
      response = await transport()(url, { method: 'GET', headers: {}, credentials: 'same-origin' })
    } catch (error) {
      throw new V32Error({ code: 'network_error', message: '无法连接到本地服务，请确认服务仍在运行。', retryable: true, details: { reason: String(error?.name || 'error') } })
    }
    const header = name => (typeof response.headers?.get === 'function' ? response.headers.get(name) : null) || ''
    if (!response.ok) {
      let envelope = null
      try {
        const text = typeof response.text === 'function' ? await response.text() : ''
        envelope = text ? JSON.parse(text)?.error || null : null
      } catch { envelope = null }
      throw new V32Error({
        code: envelope?.code || 'http_error',
        message: envelope?.message || `请求失败（HTTP ${response.status}）`,
        retryable: envelope?.retryable === true,
        details: envelope?.details || {},
        request_id: envelope?.request_id || '',
        status: response.status,
      })
    }
    const contentType = header('content-type')
    let blob
    if (typeof response.blob === 'function') blob = await response.blob()
    else blob = new Blob([typeof response.text === 'function' ? await response.text() : ''], { type: contentType || 'application/octet-stream' })
    return { blob, filename: filenameFrom(header('content-disposition')) || fallbackName, contentType }
  }

  /** GET /api/v3/session — the CSRF token used by every later mutation. */
  async function session({ force = false } = {}) {
    if (csrfToken && !force) return { csrf_token: csrfToken, cached: true }
    if (!sessionPromise || force) {
      sessionPromise = send('GET', '/session').then(data => {
        csrfToken = data?.csrf_token || ''
        return data
      }).catch(error => { sessionPromise = null; throw error })
    }
    return sessionPromise
  }

  async function ensureCsrf() {
    if (!csrfToken) await session()
    return csrfToken
  }

  /** Frozen per user action: a retry replays it, the next action gets a new one. */
  function keyFor(action, { retry = false, scope = '' } = {}) {
    const slot = scope ? `${action}:${scope}` : action
    if (retry && idempotency.has(slot)) return idempotency.get(slot)
    const key = newIdempotencyKey(action)
    idempotency.set(slot, key)
    return key
  }

  function done(action, scope = '') {
    idempotency.delete(scope ? `${action}:${scope}` : action)
  }

  async function read(path, query) { return send('GET', path, { query }) }

  async function write(method, path, { body, action, scope = '', retry = false, idempotencyRequired = true } = {}) {
    await ensureCsrf()
    const idempotencyKey = action && idempotencyRequired ? keyFor(action, { retry, scope }) : ''
    const data = await send(method, path, { body, idempotencyKey: idempotencyKey || undefined })
    if (action) done(action, scope)
    return data
  }

  const project = pid => `/projects/${encodeURIComponent(pid)}`

  return {
    base,
    session,
    csrfToken: () => csrfToken,

    capabilities: () => read('/capabilities'),

    createProject: (body, options = {}) => write('POST', '/projects', { body, action: 'project.create', retry: options.retry }),

    getProject: pid => read(project(pid)),

    discover: (pid, body, options = {}) => write('POST', `${project(pid)}/discover`, { body, action: 'discover', retry: options.retry }),

    importPapers: async (pid, { expectedRevision, files }, options = {}) => {
      await ensureCsrf()
      const form = new FormData()
      form.append('expected_revision', String(expectedRevision))
      Array.from(files || []).forEach(file => form.append('files', file))
      const idempotencyKey = keyFor('import', { retry: options.retry })
      const data = await send('POST', `${project(pid)}/papers/import`, { form, idempotencyKey })
      done('import')
      return data
    },

    getImport: (pid, importId) => read(`${project(pid)}/imports/${encodeURIComponent(importId)}`),

    resolveImport: (pid, importId, body, options = {}) => write('POST', `${project(pid)}/imports/${encodeURIComponent(importId)}/resolve`, { body, action: 'import', scope: importId, retry: options.retry }),

    retryImport: (pid, importId, body, options = {}) => write('POST', `${project(pid)}/imports/${encodeURIComponent(importId)}/retry`, { body, action: 'import.retry', scope: importId, retry: options.retry }),

    papers: (pid, query = {}) => read(`${project(pid)}/papers`, query),

    judge: (pid, body, options = {}) => write('POST', `${project(pid)}/judgements`, { body, action: 'judgement', scope: options.scope || body?.paper_id, retry: options.retry }),

    rerank: (pid, body, options = {}) => write('POST', `${project(pid)}/rerank`, { body, action: 'rerank', retry: options.retry }),

    expand: (pid, body, options = {}) => write('POST', `${project(pid)}/expand`, { body, action: 'expand', retry: options.retry }),

    job: (pid, jobId) => read(`${project(pid)}/jobs/${encodeURIComponent(jobId)}`),

    cancelJob: (pid, jobId) => write('POST', `${project(pid)}/jobs/${encodeURIComponent(jobId)}/cancel`, {}),

    overview: pid => read(`${project(pid)}/overview`),

    accessOptions: (pid, paperId) => read(`${project(pid)}/papers/${encodeURIComponent(paperId)}/access-options`),

    venueMetrics: (pid, paperId) => read(`${project(pid)}/papers/${encodeURIComponent(paperId)}/venue-metrics`),

    credentials: () => read('/settings/credentials'),

    // The secret travels in the body only; the path carries the provider id.
    saveCredential: (providerId, body) => write('PUT', `/settings/credentials/${encodeURIComponent(providerId)}`, { body, idempotencyRequired: false }),

    deleteCredential: providerId => write('DELETE', `/settings/credentials/${encodeURIComponent(providerId)}`, { idempotencyRequired: false }),

    // S9：模型端点（常见供应商 / 中转站 / 拉模型列表 / 记住在本机）。
    modelPresets: () => read('/settings/model/presets'),

    probeModel: body => write('POST', '/settings/model/probe', { body, idempotencyRequired: false }),

    saveModelConfig: body => write('PUT', '/settings/model', { body, idempotencyRequired: false }),

    // S10：本地期刊缓存（出现次数 + 本地 CCF 分区）。
    venueCache: (limit = 50) => read('/venues', { limit }),

    // S11：本机 Zotero（只读本地 API）。
    zoteroLocalStatus: () => read('/zotero/local/status'),

    zoteroLocalCollections: () => read('/zotero/local/collections'),

    zoteroLocalItems: ({ q = '', collection = '', limit = 30 } = {}) => read('/zotero/local/items', { q, collection, limit }),

    importZoteroLocal: (pid, body) => write('POST', `${project(pid)}/zotero-local/import`, { body, idempotencyRequired: false }),

    zoteroImport: (pid, body, options = {}) => write('POST', `${project(pid)}/zotero/import`, { body, action: 'import', scope: 'zotero', retry: options.retry }),

    zoteroExportPreview: (pid, body) => write('POST', `${project(pid)}/zotero/export-preview`, { body, idempotencyRequired: false }),

    zoteroExport: (pid, body, options = {}) => write('POST', `${project(pid)}/zotero/export`, { body, action: 'expand', scope: 'zotero', retry: options.retry }),

    // GET /api/v3/projects/{pid}/export?format=ris|bibtex|csv|evidence_pack|prisma
    exportProject: (pid, format) => download(`${project(pid)}/export`, { format }, `${pid}-${format}.${EXPORT_EXTENSIONS[format] || 'txt'}`),

    zoteroResults: (pid, jobId) => read(`${project(pid)}/jobs/${encodeURIComponent(jobId)}/zotero-results`),
  }
}

export const EXPORT_FORMATS = Object.freeze(['ris', 'bibtex', 'csv', 'evidence_pack', 'prisma'])

const EXPORT_EXTENSIONS = Object.freeze({ ris: 'ris', bibtex: 'bib', csv: 'csv', evidence_pack: 'zip', prisma: 'txt' })

/** The filename from a Content-Disposition header (RFC 6266 filename* first). */
export function filenameFrom(disposition) {
  const value = String(disposition || '')
  const star = value.match(/filename\*\s*=\s*(?:UTF-8'')?([^;]+)/i)
  let name = ''
  if (star) {
    try { name = decodeURIComponent(star[1].trim().replace(/^"|"$/g, '')) } catch { name = '' }
  }
  if (!name) {
    const plain = value.match(/filename\s*=\s*"?([^";]+)"?/i)
    name = plain ? plain[1].trim() : ''
  }
  // Never let a server-supplied name carry a path.
  return name.replace(/^.*[\\/]/, '')
}

/** One restrained line for the user: the contract message, never a stack trace. */
export function errorLine(error, fallback = '请求未完成，请稍后重试。') {
  if (!error) return fallback
  if (error instanceof V32Error) return error.message || fallback
  return fallback
}
