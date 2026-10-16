// 上传状态文案与重试范围。
import { describe, expect, it } from 'vitest'
import { fileStatusLabel, isPendingStatus, needsConfirmation, retryableIds, selectionProblemText, summariseBatch, UPLOAD_STATUSES, validateSelection } from './upload.js'

describe('upload presentation', () => {
  it('uses the eight fixed status labels', () => {
    expect(fileStatusLabel('uploaded')).toBe('等待解析')
    expect(fileStatusLabel('parsing')).toBe('识别中')
    expect(fileStatusLabel('matching')).toBe('识别中')
    expect(fileStatusLabel('matched')).toBe('已识别')
    expect(fileStatusLabel('needs_confirmation')).toBe('需要确认')
    expect(fileStatusLabel('local_seed')).toBe('作为本地文献继续')
    expect(fileStatusLabel('needs_ocr')).toBe('需要文本识别')
    expect(fileStatusLabel('encrypted_pdf')).toBe('无法读取')
    expect(fileStatusLabel('invalid_pdf')).toBe('无法读取')
    expect(fileStatusLabel('failed')).toBe('等待重试')
    expect(Object.keys(UPLOAD_STATUSES)).toHaveLength(10)
    // A parser name never appears in the normal interface.
    expect(JSON.stringify(UPLOAD_STATUSES)).not.toMatch(/fitz|pymupdf|pypdf/i)
  })

  it('retries only the items the backend marked retryable', () => {
    const items = [
      { item_id: 'i1', status: 'matched', retryable: false },
      { item_id: 'i2', status: 'failed', retryable: true },
      { item_id: 'i3', status: 'encrypted_pdf', retryable: true },
      { item_id: 'i4', status: 'needs_confirmation', retryable: false },
    ]
    expect(retryableIds(items)).toEqual(['i2', 'i3'])
    expect(retryableIds([])).toEqual([])
    const summary = summariseBatch(items)
    expect(summary.total).toBe(4)
    expect(summary.retryable).toBe(2)
    expect(summary.confirming).toBe(1)
    expect(summary.partial).toBe(true)
    expect(isPendingStatus('parsing')).toBe(true)
    expect(isPendingStatus('matched')).toBe(false)
    expect(needsConfirmation('needs_confirmation')).toBe(true)
    expect(needsConfirmation('matched')).toBe(false)
  })

  it('rejects an unusable selection before any request is sent', () => {
    const good = [new File([new Uint8Array(4)], 'a.pdf')]
    expect(validateSelection(good).ok).toBe(true)
    const many = Array.from({ length: 11 }, (unused, index) => new File([new Uint8Array(4)], `p${index}.pdf`))
    const tooMany = validateSelection(many)
    expect(tooMany.ok).toBe(false)
    expect(selectionProblemText(tooMany.problems[0])).toContain('10')
    const big = validateSelection([new File([new Uint8Array(4)], 'big.pdf')], { maxFiles: 10, maxFileBytes: 2 })
    expect(big.ok).toBe(false)
    expect(selectionProblemText(big.problems[0])).toContain('big.pdf')
    expect(selectionProblemText(validateSelection([]).problems[0])).toContain('选择')
  })
})
