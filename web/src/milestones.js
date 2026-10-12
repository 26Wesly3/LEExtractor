// 项目里程碑：集中维护，便于后续增删节点与更新中英文文案。
// 规则（与展示口径一致）：
//   · 最新节点在最前；status = 'done' 表示已完成，'planned' 表示后续计划；
//   · 只记录仓库提交与 DevLog 能核验的事实；"公网可用"不等于"完整发布验收完成"；
//   · details 仅在有真实内容时提供，前端据此决定是否可展开。
export const MILESTONES = [
  {
    date: '2026-10-12',
    status: 'done',
    title: { zh: '公网测试上线', en: 'Public test deployment' },
    summary: {
      zh: 'HTTPS、健康检查与项目数据持久化均已验证；并发压测通过。',
      en: 'HTTPS, health checks and project-data persistence verified; concurrency stress test passed.',
    },
    details: {
      zh: '容器化部署（Dockerfile / compose / 部署文档）、健康检查拆分存活与就绪、数据与缓存目录规范、任务级并发上限；修复反向代理环境下不可访问的缺陷；发布 Windows 免安装便携版。统计口径：并发压测覆盖 1 / 5 / 10 / 20 用户场景。',
      en: 'Containerised deployment (Dockerfile / compose / deployment guide), liveness and readiness split, data and cache directory conventions, a per-process job concurrency cap, plus a fix for the reverse-proxy access defect and a portable Windows build. Stress test covered 1 / 5 / 10 / 20 concurrent users.',
    },
  },
  {
    date: '2026-10-10',
    status: 'done',
    title: { zh: '完成首轮检索质量基准', en: 'First retrieval-quality benchmark' },
    summary: {
      zh: 'SciFact 试点完成，评分与排序可复核。',
      en: 'SciFact pilot completed; scores and rankings are reproducible.',
    },
    details: {
      zh: '以公开数据集完成可复现评分，冻结输入指纹以保证跨平台一致；同时补充跨来源身份确认的支持。',
      en: 'Reproducible scoring on a public dataset with frozen input fingerprints for cross-platform consistency, plus support for confirmed cross-source identities.',
    },
  },
  {
    date: '2026-10-09',
    status: 'done',
    title: { zh: 'Web 工作台接入', en: 'Web workbench integration' },
    summary: {
      zh: 'Web 界面与文献综述后端完成集成，11 个页面使用真实接口。',
      en: 'The web interface is integrated with the review backend; all 11 pages use real endpoints.',
    },
    details: {
      zh: '接入 Vue 3 / Vuetify 与本机 FastAPI；复用查询规划、系统检索、雪球扩展、研究版图、候选问题、PRISMA、标定与导出算法；后台任务支持预算、取消、检查点与重启恢复；项目独立存储并以 revision 防止旧标签覆盖。',
      en: 'Vue 3 / Vuetify on a local FastAPI service reusing the existing planning, search, snowballing, landscape, questions, PRISMA, calibration and export logic; jobs support budgets, cancellation, checkpoints and restart recovery; projects are stored independently with revision protection.',
    },
  },
  {
    date: '2026-10-03',
    status: 'done',
    title: { zh: '完成组队', en: 'Team formed' },
    summary: {
      zh: '参赛团队组建完成，进入工程实施阶段。',
      en: 'The competition team was formed and engineering work began.',
    },
  },
  {
    date: '2026-09-27',
    status: 'done',
    title: { zh: '项目正式启动', en: 'Project started' },
    summary: {
      zh: '依据竞赛通知确定选题与交付计划。',
      en: 'Topic and delivery plan were fixed from the competition notice.',
    },
  },
  {
    date: '后续计划',
    status: 'planned',
    title: { zh: '稳定性加固与公开 Beta 准备', en: 'Stability hardening and public Beta preparation' },
    summary: {
      zh: '故障注入、连续运行与资源占用测量，作为公开 Beta 的判定依据。',
      en: 'Fault injection, soak runs and resource measurement as the gate for a public Beta.',
    },
    details: {
      zh: '计划内容：来源与模型故障注入（超时、限流、断网、坏数据）、连续检索的泄漏排查、峰值内存与并发上限复核。上述项目尚未完成，不计入已完成里程碑。',
      en: 'Planned: provider and model fault injection (timeouts, rate limits, network loss, malformed payloads), leak checks over long runs, and peak-memory / concurrency re-verification. These are not complete and are not listed as done.',
    },
  },
]

export const DEFAULT_MILESTONES = 5
