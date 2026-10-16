// 等待、空、错误状态与恢复：三种空状态文案必须互不相同；
// 视口锚点按 paper_id 保存；未收敛时保留基础排序。
import { describe, expect, it } from 'vitest'
import { anchorFrom, coverageLine, emptyStateCopy, enhancementNotice, rankingStageForJob, rankingStageLabel, reasonTextForError, resolvePinned, resultState, RESULT_STATES, restoreAnchor, STAGES, stageLabel } from './states.js'

function rowRect(top, bottom) { return { top, bottom, left: 0, right: 10, width: 10, height: bottom - top } }

function fakeList(rows, containerTop = 0) {
  const nodes = rows.map(({ id, top, bottom }) => ({
    getAttribute: name => (name === 'data-paper-id' ? id : null),
    getBoundingClientRect: () => rowRect(top, bottom),
  }))
  const container = {
    scrollBy: (options) => { container.scrolled = options },
    getBoundingClientRect: () => ({ top: containerTop, bottom: 1000, left: 0, right: 100, width: 100, height: 1000 }),
    querySelectorAll: () => nodes,
  }
  return container
}

describe('workspace states', () => {
  it('keeps the three empty states distinct', () => {
    expect(resultState({ candidateCount: 0 })).toBe(RESULT_STATES.empty)
    expect(resultState({ candidateCount: 5, recommendationCount: 0, excludedCount: 0 })).toBe(RESULT_STATES.no_recommendation)
    expect(resultState({ candidateCount: 5, recommendationCount: 0, excludedCount: 5 })).toBe(RESULT_STATES.all_excluded)
    expect(resultState({ candidateCount: 5, recommendationCount: 2 })).toBe('ready')
    const copy = [RESULT_STATES.empty, RESULT_STATES.no_recommendation, RESULT_STATES.all_excluded].map(state => emptyStateCopy(state))
    expect(new Set(copy.map(row => row.text)).size).toBe(3)
    expect(new Set(copy.map(row => row.action)).size).toBe(3)
    expect(emptyStateCopy(RESULT_STATES.no_recommendation).text).toContain('证据不足')
    expect(emptyStateCopy(RESULT_STATES.all_excluded).text).toContain('恢复')
  })

  it('shows the three wait states in order and degrades to the basic ranking', () => {
    expect(rankingStageLabel('fast')).toBe('已获得初步结果')
    expect(rankingStageLabel('enhancing')).toBe('正在完善排序')
    expect(rankingStageLabel('refreshed')).toBe('排序已更新')
    expect(rankingStageForJob({ state: 'running', stage: 'fast_ranking' }, true)).toBe('fast')
    expect(rankingStageForJob({ state: 'running', stage: 'enhancing' }, true)).toBe('enhancing')
    expect(rankingStageForJob({ state: 'succeeded', stage: 'finished' }, true)).toBe('refreshed')
    const degraded = enhancementNotice({ state: 'failed', error_codes: ['model_unavailable'] })
    expect(degraded.text).toBe('已使用基础排序')
    expect(degraded.detail).not.toContain('Traceback')
    expect(enhancementNotice({ state: 'succeeded', error_codes: [] })).toBeNull()
  })

  it('collapses a partly unavailable source into one line plus details', () => {
    const line = coverageLine({ status: 'partial', candidate_count: 12, failed_sources: ['crossref'], stop_reasons: ['budget'], scope: 'current corpus' })
    expect(line.text).toContain('部分来源暂不可用')
    expect(line.detail).toContain('crossref')
    expect(coverageLine({ status: 'within_budget', candidate_count: 3, failed_sources: [], stop_reasons: [], scope: 'x' })).toBeNull()
    expect(coverageLine({ status: 'no_results', failed_sources: [] }).text).toContain('没有匹配文献')
    expect(coverageLine({ status: 'failed', failed_sources: ['s2'] }).text).toContain('不代表检索穷尽')
  })

  it('maps contract error codes onto restrained copy', () => {
    expect(reasonTextForError({ code: 'revision_conflict' })).toContain('刷新')
    expect(reasonTextForError({ code: 'cursor_stale' })).toContain('第一页')
    expect(reasonTextForError({ code: 'unknown_thing' })).not.toContain('unknown_thing')
  })

  it('anchors the viewport on a paper id, not on a list position', () => {
    const container = fakeList([{ id: 'w1', top: -200, bottom: -100 }, { id: 'w2', top: 10, bottom: 120 }, { id: 'w3', top: 200, bottom: 300 }])
    const anchor = anchorFrom(container, 'w1')
    expect(anchor.paperId).toBe('w2')
    expect(anchor.offset).toBe(10)

    // After a re-ranking the same record sits elsewhere: the anchor follows it.
    const shifted = fakeList([{ id: 'w9', top: 0, bottom: 60 }, { id: 'w2', top: 300, bottom: 420 }])
    expect(restoreAnchor(shifted, anchor)).toBe(true)
    expect(shifted.scrolled.top).toBe(290)
    expect(restoreAnchor(fakeList([{ id: 'w3', top: 0, bottom: 10 }]), anchor)).toBe(false)
  })

  it('pins the open drawer by paper_id across a re-ranking', () => {
    const before = [{ paper: { paper_id: 'w2' } }, { paper: { paper_id: 'w1' } }]
    expect(resolvePinned('w2', before)?.paper.paper_id).toBe('w2')
    const after = [{ paper: { paper_id: 'w1' } }, { paper: { paper_id: 'w2' } }]
    expect(resolvePinned('w2', after)?.paper.paper_id).toBe('w2')
    expect(resolvePinned('w2', [{ paper: { paper_id: 'w1' } }])).toBeNull()
    expect(resolvePinned('', after)).toBeNull()
  })

  it('names the four stages in order', () => {
    expect(STAGES.map(stage => stage.key)).toEqual(['anchors', 'papers', 'threads', 'insight'])
    expect(stageLabel('papers')).toBe('核心文献')
    expect(stageLabel('insight', 'en')).toBe('Research insight')
  })
})
