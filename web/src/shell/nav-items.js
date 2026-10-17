// 抽屉菜单条目（S3：换成新导航）。
//
// 旧菜单是「七个旧工作台页面」，那套页面已经被 S1 折叠进四个阶段。新菜单按
// 用户确认的 D2/D4 来：首页、新研究、当前项目的四个阶段、导出、全部项目、设置。
// 首页顶栏本身逐像素不变，变化只体现在这里与工作区阶段条（S4）。
import { STAGES } from '../v32/states.js'

const STAGE_DESC = {
  anchors: { zh: '输入方向或 PDF，检索真实来源', en: 'Search real sources from a direction or PDFs' },
  papers: { zh: '排序推荐与人工筛选', en: 'Ranked recommendations and screening' },
  threads: { zh: '领域版图、主题与时间线', en: 'Field map, topics and timeline' },
  insight: { zh: '可检验的创新点与线索', en: 'Testable innovation points and leads' },
}

export function buildMenu({ t, lang, hasProject }) {
  const items = [
    { key: 'home', label: t('首页', 'Home'), desc: t('封面与最近项目', 'Cover and recent projects') },
    { key: 'search', label: t('新研究', 'New research'), desc: t('从研究方向或已有 PDF 开始', 'Start from a direction or existing PDFs') },
  ]
  if (hasProject) {
    for (const stage of STAGES) {
      const desc = STAGE_DESC[stage.key]
      items.push({ key: stage.key, label: lang === 'zh' ? stage.zh : stage.en, desc: lang === 'zh' ? desc.zh : desc.en })
    }
    items.push({ key: 'export', label: t('导出', 'Export'), desc: t('文献、证据包与 PRISMA', 'Records, evidence pack and PRISMA') })
  }
  items.push(
    { key: 'projects', label: t('全部项目', 'All projects'), desc: t('恢复或新建研究项目', 'Resume or create a research project') },
    { key: 'settings', label: t('设置', 'Settings'), desc: t('凭据、模型与界面偏好', 'Credentials, models and preferences') },
  )
  return items
}
