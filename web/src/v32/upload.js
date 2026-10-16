// Pure presentation logic for UploadPanel.vue.
// Per-file status copy is fixed by docs/v3.2/05_UX_UI_SPEC.md ("上传与异常")
// and the ImportItem status enum in litsearch/contracts/domain.schema.json.
// A parser name is never part of the normal interface, so nothing here mentions
// one.

export const UPLOAD_STATUSES = Object.freeze({
  uploaded: { zh: '等待解析', en: 'Queued for parsing', pending: true },
  parsing: { zh: '识别中', en: 'Reading', pending: true },
  matching: { zh: '识别中', en: 'Matching', pending: true },
  matched: { zh: '已识别', en: 'Matched', pending: false },
  needs_confirmation: { zh: '需要确认', en: 'Needs confirmation', pending: false, confirm: true },
  local_seed: { zh: '作为本地文献继续', en: 'Continue as a local seed', pending: false },
  needs_ocr: { zh: '需要文本识别', en: 'Needs text recognition', pending: false },
  encrypted_pdf: { zh: '无法读取', en: 'Cannot be read', pending: false },
  invalid_pdf: { zh: '无法读取', en: 'Cannot be read', pending: false },
  failed: { zh: '等待重试', en: 'Waiting to retry', pending: false },
})

export function fileStatusLabel(status, lang = 'zh') {
  const row = UPLOAD_STATUSES[status]
  if (!row) return lang === 'zh' ? '等待解析' : 'Queued for parsing'
  return lang === 'zh' ? row.zh : row.en
}

export function isPendingStatus(status) {
  return UPLOAD_STATUSES[status]?.pending === true
}

export function needsConfirmation(status) {
  return status === 'needs_confirmation'
}

/**
 * A retry must touch ONLY the items the backend marked retryable: a matched
 * item is never re-parsed because a sibling failed.
 */
export function retryableIds(items) {
  return (Array.isArray(items) ? items : [])
    .filter(item => item?.retryable === true)
    .map(item => item.item_id)
    .filter(Boolean)
}

export function summariseBatch(items, lang = 'zh') {
  const rows = Array.isArray(items) ? items : []
  const total = rows.length
  const done = rows.filter(item => !isPendingStatus(item?.status)).length
  const retryable = retryableIds(rows).length
  const confirming = rows.filter(item => needsConfirmation(item?.status)).length
  const text = lang === 'zh'
    ? `共 ${total} 个文件，${done} 个已处理${confirming ? `，${confirming} 个需要确认` : ''}${retryable ? `，${retryable} 个可重试` : ''}。`
    : `${total} files, ${done} handled${confirming ? `, ${confirming} awaiting confirmation` : ''}${retryable ? `, ${retryable} retryable` : ''}.`
  return { total, done, retryable, confirming, partial: retryable > 0 || confirming > 0, text }
}

/** Reject obviously unusable files before the request leaves the browser. */
export function validateSelection(files, { maxFiles = 10, maxFileBytes = 20 * 1024 * 1024 } = {}) {
  const rows = Array.from(files || [])
  const problems = []
  if (!rows.length) problems.push({ code: 'empty_selection' })
  if (rows.length > maxFiles) problems.push({ code: 'too_many_files', limit: maxFiles, count: rows.length })
  const oversized = rows.filter(file => Number(file?.size) > maxFileBytes)
  if (oversized.length) problems.push({ code: 'file_too_large', limit: maxFileBytes, names: oversized.map(file => file.name) })
  return { ok: problems.length === 0, problems }
}

export function selectionProblemText(problem, lang = 'zh') {
  if (!problem) return ''
  if (problem.code === 'empty_selection') return lang === 'zh' ? '请先选择要上传的文献文件。' : 'Select the files to upload first.'
  if (problem.code === 'too_many_files') return lang === 'zh' ? `一次最多上传 ${problem.limit} 个文件，当前选择了 ${problem.count} 个。` : `At most ${problem.limit} files per batch; ${problem.count} selected.`
  if (problem.code === 'file_too_large') return lang === 'zh' ? `单个文件不得超过 ${Math.round(problem.limit / (1024 * 1024))} MB：${problem.names.join('、')}` : `Each file must stay under ${Math.round(problem.limit / (1024 * 1024))} MB: ${problem.names.join(', ')}`
  return lang === 'zh' ? '所选文件未通过校验。' : 'The selection did not pass validation.'
}
