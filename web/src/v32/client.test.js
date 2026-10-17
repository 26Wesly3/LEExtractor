// 客户端单元测试：CSRF、幂等键、错误信封，以及凭据绝不进入 URL 或日志。
// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { createV32Client, errorLine, isIdempotencyKey, newIdempotencyKey, silentLogger, V32Error } from './client.js'
import { createResponders, jsonResponse, sessionResponder, emptyResponse } from './test-harness.js'

function harness(extra = []) {
  const responders = createResponders(extra)
  sessionResponder(responders)
  return responders
}

describe('v3.2 client', () => {
  it('reads the CSRF token from the session and attaches it to a mutating call', async () => {
    const responders = harness([
      { method: 'POST', match: '/api/v3/projects', handler: () => jsonResponse({ project_id: 'p1', name: 'demo', entry_mode: 'topic' }, 201) },
    ])
    const client = createV32Client({ fetchImpl: (url, options) => responders.resolve(url, options) })
    await client.createProject({ name: 'demo', entry_mode: 'topic' })
    const session = responders.calls.find(call => call.url === '/api/v3/session')
    const create = responders.calls.find(call => call.url === '/api/v3/projects')
    expect(session.method).toBe('GET')
    expect(create.method).toBe('POST')
    expect(create.headers['X-CSRF-Token']).toBe('csrf-token-abc')
    expect(create.headers['Content-Type']).toBe('application/json')
  })

  it('does not repeat the session read for the next mutation', async () => {
    const responders = harness([{ method: 'POST', match: '/api/v3/projects', handler: () => jsonResponse({ project_id: 'p1' }, 201) }])
    const client = createV32Client({ fetchImpl: (url, options) => responders.resolve(url, options) })
    await client.createProject({ name: 'a', entry_mode: 'topic' })
    await client.createProject({ name: 'b', entry_mode: 'topic' })
    expect(responders.calls.filter(call => call.url === '/api/v3/session')).toHaveLength(1)
  })

  it('generates a 16..128 character idempotency key for every keyed mutation', async () => {
    const responders = harness([{ method: 'POST', match: '/api/v3/projects', handler: () => jsonResponse({ project_id: 'p1' }, 201) }])
    const client = createV32Client({ fetchImpl: (url, options) => responders.resolve(url, options) })
    await client.createProject({ name: 'demo', entry_mode: 'topic' })
    const key = responders.calls.find(call => call.method === 'POST').headers['Idempotency-Key']
    expect(isIdempotencyKey(key)).toBe(true)
    expect(key.length).toBeGreaterThanOrEqual(16)
    expect(key.length).toBeLessThanOrEqual(128)
    expect(newIdempotencyKey('x').length).toBeGreaterThanOrEqual(16)
  })

  it('reuses the SAME key when a failed action is retried and issues a new one after success', async () => {
    let attempts = 0
    const responders = harness([
      {
        method: 'POST',
        match: '/api/v3/projects/p1/judgements',
        handler: () => {
          attempts += 1
          if (attempts === 1) return jsonResponse({ error: { code: 'revision_conflict', message: 'conflict', retryable: true, details: { current_revision: 3 }, request_id: 'r1' } }, 409)
          return jsonResponse({ revision: 4, context_version: 1, judgement: { paper_id: 'w1', value: 'core' }, rerank_job_id: null })
        },
      },
    ])
    const client = createV32Client({ fetchImpl: (url, options) => responders.resolve(url, options) })
    const body = { expected_revision: 3, paper_id: 'w1', judgement: 'core' }
    await expect(client.judge('p1', body)).rejects.toBeInstanceOf(V32Error)
    await client.judge('p1', body, { retry: true })
    const keys = responders.calls.filter(call => call.url.endsWith('/judgements')).map(call => call.headers['Idempotency-Key'])
    expect(keys).toHaveLength(2)
    expect(keys[0]).toBe(keys[1])

    // A later, distinct action must not replay the finished key.
    await client.judge('p1', { ...body, expected_revision: 4 })
    const later = responders.calls.filter(call => call.url.endsWith('/judgements')).map(call => call.headers['Idempotency-Key'])
    expect(later[2]).not.toBe(later[0])
  })

  it('surfaces the contract error envelope and never a bare HTTP error', async () => {
    const responders = harness([
      {
        method: 'POST',
        match: '/api/v3/projects/p1/discover',
        handler: () => jsonResponse({
          error: { code: 'capability_unavailable', message: '检索服务尚未配置', retryable: false, details: { capability: 'semantic_scholar' }, request_id: 'req-9' },
        }, 409),
      },
    ])
    const client = createV32Client({ fetchImpl: (url, options) => responders.resolve(url, options) })
    let caught = null
    try { await client.discover('p1', { expected_revision: 1, research_text: 'x', seed_paper_ids: [], mode: 'basic' }) } catch (error) { caught = error }
    expect(caught).toBeInstanceOf(V32Error)
    expect(caught.code).toBe('capability_unavailable')
    expect(caught.message).toBe('检索服务尚未配置')
    expect(caught.retryable).toBe(false)
    expect(caught.details).toEqual({ capability: 'semantic_scholar' })
    expect(caught.requestId).toBe('req-9')
    expect(caught.status).toBe(409)
    expect(errorLine(caught)).toBe('检索服务尚未配置')
    expect(errorLine(new Error('boom'))).not.toContain('boom')
  })

  it('never puts a credential secret into the URL, the query string or a log line', async () => {
    const secret = 'sk-secret-0123456789'
    const responders = harness([
      { method: 'PUT', match: '/api/v3/settings/credentials/semantic_scholar', handler: () => jsonResponse({ provider_id: 'semantic_scholar', kind: 'retrieval', configured: true, updated_at: '2026-10-17T00:00:00Z' }) },
      { method: 'DELETE', match: '/api/v3/settings/credentials/semantic_scholar', handler: () => emptyResponse(204) },
    ])
    const lines = []
    const logger = { debug: line => lines.push(line), info: line => lines.push(line), warn: line => lines.push(line), error: line => lines.push(line) }
    const consoleSpies = ['log', 'info', 'warn', 'error', 'debug'].map(level => vi.spyOn(console, level).mockImplementation(() => {}))
    const client = createV32Client({ fetchImpl: (url, options) => responders.resolve(url, options), logger })
    const saved = await client.saveCredential('semantic_scholar', { kind: 'retrieval', secret })
    expect(saved.configured).toBe(true)

    const put = responders.calls.find(call => call.method === 'PUT')
    expect(put.url).toBe('/api/v3/settings/credentials/semantic_scholar')
    expect(put.body.secret).toBe(secret)
    expect(JSON.stringify([...put.query.keys()])).not.toContain(secret)
    expect(put.headers['X-CSRF-Token']).toBe('csrf-token-abc')
    expect(lines.join('\n')).not.toContain(secret)
    expect(lines.join('\n')).not.toContain('csrf-token-abc')
    expect(consoleSpies.flatMap(spy => spy.mock.calls).flat().join('\n')).not.toContain(secret)
    // No route ever receives the secret as a path segment.
    expect(responders.calls.every(call => !call.url.includes(secret))).toBe(true)
    consoleSpies.forEach(spy => spy.mockRestore())
    expect(silentLogger.debug()).toBeUndefined()
  })

  it('sends a multipart import with the revision, the files and an idempotency key', async () => {
    const responders = harness([
      { method: 'POST', match: '/api/v3/projects/p1/papers/import', handler: () => jsonResponse({ import_id: 'i1', job_id: 'j1', project_id: 'p1', revision: 2, items: [] }, 202) },
    ])
    const client = createV32Client({ fetchImpl: (url, options) => responders.resolve(url, options) })
    const file = new File([new Uint8Array([1, 2, 3])], 'paper.pdf', { type: 'application/pdf' })
    await client.importPapers('p1', { expectedRevision: 2, files: [file] })
    const call = responders.calls.find(call => call.url.endsWith('/papers/import'))
    expect(call.body).toBeInstanceOf(FormData)
    expect(call.body.get('expected_revision')).toBe('2')
    expect(call.body.get('files')).toBeInstanceOf(File)
    expect(isIdempotencyKey(call.headers['Idempotency-Key'])).toBe(true)
    // multipart must let the browser set its own boundary
    expect(call.headers['Content-Type']).toBeUndefined()
  })

  it('treats 204 as an empty success and keeps GET reads free of the CSRF header', async () => {
    const responders = harness([
      { method: 'DELETE', match: '/api/v3/settings/credentials/zotero', handler: () => emptyResponse(204) },
      { method: 'GET', match: '/api/v3/capabilities', handler: () => jsonResponse({ deployment_mode: 'desktop_local', items: [], limits: { max_pdf_files: 10, max_pdf_file_bytes: 1, max_pdf_batch_bytes: 1, candidate_cap: 300 } }) },
    ])
    const client = createV32Client({ fetchImpl: (url, options) => responders.resolve(url, options) })
    await expect(client.deleteCredential('zotero')).resolves.toBeNull()
    const capabilities = await client.capabilities()
    expect(capabilities.deployment_mode).toBe('desktop_local')
    const read = responders.calls.find(call => call.url === '/api/v3/capabilities')
    expect(read.headers['X-CSRF-Token']).toBeUndefined()
    expect(read.headers['Idempotency-Key']).toBeUndefined()
  })

  it('reports a transport failure as retryable instead of leaking the runtime error', async () => {
    const client = createV32Client({ fetchImpl: async () => { throw new TypeError('Failed to fetch secret') } })
    let caught = null
    try { await client.session() } catch (error) { caught = error }
    expect(caught).toBeInstanceOf(V32Error)
    expect(caught.code).toBe('network_error')
    expect(caught.retryable).toBe(true)
    expect(caught.message).not.toContain('Failed to fetch')
    expect(caught.message).not.toContain('secret')
  })
})

describe('v3.2 client export download', () => {
  it('reads GET /projects/{pid}/export?format=… as a blob with the server filename', async () => {
    const responders = harness([{
      method: 'GET',
      match: '/api/v3/projects/p1/export',
      handler: () => ({
        ok: true, status: 200, statusText: 'OK',
        headers: { get: name => ({ 'content-disposition': 'attachment; filename="p1-export.bib"', 'content-type': 'application/x-bibtex' })[name.toLowerCase()] || null },
        blob: async () => new Blob(['@article{x}'], { type: 'application/x-bibtex' }),
      }),
    }])
    const client = createV32Client({ fetchImpl: (url, options) => responders.resolve(url, options) })
    const result = await client.exportProject('p1', 'bibtex')
    const call = responders.calls.find(row => row.url === '/api/v3/projects/p1/export')
    expect(call.method).toBe('GET')
    expect(call.query.get('format')).toBe('bibtex')
    expect(result.filename).toBe('p1-export.bib')
    expect(await result.blob.text()).toBe('@article{x}')
  })

  it('throws the error envelope when the export route is missing (404)', async () => {
    const responders = harness()
    const client = createV32Client({ fetchImpl: (url, options) => responders.resolve(url, options) })
    const caught = await client.exportProject('p1', 'ris').catch(error => error)
    expect(caught).toBeInstanceOf(V32Error)
    expect(caught.status).toBe(404)
  })
})
