// 唯一的路由表（UI_INTEGRATION_PLAN.md S1）。
//
// 取代 bootstrap.js 的 createWorkspaceRouter 与 v32/routes.js 的
// createV32Routes：一张表、一套项目路径、一套阶段名。旧路径与旧路由名不删除，
// 而是用 redirect 承接，所以书签、e2e-check.mjs 与现有测试节点都还能走通。
//
// 阶段只有四个，与 v3.2 工作区一致：
//   anchors  切入点 / papers  文献 / threads  线索 / insight  概览
// 旧工作台的七个页面按下表折叠进来（计划第 3 节）：
//   search→anchors  results,review→papers  expand,landscape→threads
//   questions→insight  export→papers?export=1  paper→papers?paper=:paperKey
import App from './App.vue'
import ProjectList from './shell/ProjectList.vue'
import SettingsPanels from './v32/SettingsPanels.vue'
import Workspace from './v32/Workspace.vue'

/** 阶段名与顺序：工作区阶段条、路由守卫和测试共用这一份。 */
export const STAGES = ['anchors', 'papers', 'threads', 'insight']

/** 旧工作台页面 → 新阶段。 */
export const LEGACY_STAGE = {
  'project-search': 'anchors',
  'project-results': 'papers',
  'project-review': 'papers',
  'project-export': 'papers',
  'project-expand': 'threads',
  'project-landscape': 'threads',
  'project-questions': 'insight',
}

// 项目列表（S6）：旧版只读列表，取代 App.vue 里的旧项目页。
const ProjectListPlaceholder = ProjectList

const toWorkspace = (to, stage) => ({ name: 'workspace', params: { projectId: to.params.projectId, stage } })
const queryOf = (to, extra) => ({ ...to.query, ...extra })

export function createAppRoutes() {
  return [
    { path: '/', name: 'home', component: App, meta: { home: true } },
    // /search 是「无项目状态的工作区」：入口选择的两个按钮由 Workspace 处理
    // （它负责建项目并跳阶段）。直接挂 EntryChoice 会让事件没人接，按钮点不动。
    {
      path: '/search',
      name: 'search',
      component: Workspace,
      props: route => ({ projectId: '', stage: 'anchors', view: '', lang: route.query.lang === 'en' ? 'en' : 'zh' }),
    },
    { path: '/projects', name: 'projects', component: ProjectListPlaceholder },
    { path: '/settings', name: 'settings', component: SettingsPanels },

    // 旧工作台路径先于阶段路由声明：静态段优先，不会被 :stage 吃掉。
    { path: '/projects/:projectId/search', name: 'project-search', redirect: to => toWorkspace(to, 'anchors') },
    { path: '/projects/:projectId/results', name: 'project-results', redirect: to => toWorkspace(to, 'papers') },
    { path: '/projects/:projectId/review', name: 'project-review', redirect: to => toWorkspace(to, 'papers') },
    { path: '/projects/:projectId/export', name: 'project-export', redirect: to => ({ ...toWorkspace(to, 'papers'), query: queryOf(to, { export: '1' }) }) },
    { path: '/projects/:projectId/expand', name: 'project-expand', redirect: to => toWorkspace(to, 'threads') },
    { path: '/projects/:projectId/landscape', name: 'project-landscape', redirect: to => toWorkspace(to, 'threads') },
    { path: '/projects/:projectId/questions', name: 'project-questions', redirect: to => toWorkspace(to, 'insight') },
    { path: '/projects/:projectId/papers/:paperKey', name: 'paper', redirect: to => ({ ...toWorkspace(to, 'papers'), query: queryOf(to, { paper: String(to.params.paperKey) }) }) },

    // 阶段路由：唯一的工作区入口。
    // 组件直接由 router-view 渲染，所以参数必须显式映射成 props（S2 之后不再有
    // AppRoot 替它传参）；否则 projectId 为空，工作区会一直停在入口页。
    {
      path: '/projects/:projectId/:stage(anchors|papers|threads|insight)',
      name: 'workspace',
      component: Workspace,
      meta: { v32: true },
      props: route => ({
        projectId: String(route.params.projectId || ''),
        stage: String(route.params.stage || 'anchors'),
        view: String(route.meta?.v32View || ''),
        lang: route.query.lang === 'en' ? 'en' : 'zh',
      }),
    },
    { path: '/projects/:projectId', name: 'project', redirect: to => toWorkspace(to, 'anchors') },

    // v3.2 旧入口保留为重定向，书签与 e2e-check.mjs 不失效。
    { path: '/v32', name: 'v32-entries', redirect: '/search' },
    { path: '/v32/settings', name: 'v32-settings', redirect: '/settings' },
    {
      path: '/v32/projects/:projectId/:stage',
      name: 'v32-workspace',
      redirect: to => toWorkspace(to, STAGES.includes(String(to.params.stage)) ? String(to.params.stage) : 'anchors'),
    },
    { path: '/v32/projects/:projectId', name: 'v32-project', redirect: to => toWorkspace(to, 'anchors') },

    { path: '/:pathMatch(.*)*', name: 'not-found', redirect: '/' },
  ]
}

/** 纯判定：只有显式标注 meta.v32 的路由进入 v3.2 界面。 */
export function isV32Route(route) {
  return route?.meta?.v32 === true
}
