// v3.2 路由定义与判定。
//
// createWorkspaceRouter()（bootstrap.js，本次未修改）以 /:pathMatch(.*)*
// 通配重定向收尾：任何在它之后追加的路由都会被该通配吃掉。因此这里把
// v3.2 路由排到既有路由之前，既有路由表本身保持原样，原有页面、名称与
// 跳转行为不受影响。

const Page = { template: '<span />' }

export function createV32Routes() {
  return [
    { path: '/v32', name: 'v32-entries', component: Page, meta: { v32: true } },
    { path: '/v32/settings', name: 'v32-settings', component: Page, meta: { v32: true, v32View: 'settings' } },
    { path: '/v32/projects/:projectId/:stage', name: 'v32-workspace', component: Page, meta: { v32: true } },
    { path: '/v32/projects/:projectId', name: 'v32-project', component: Page, meta: { v32: true } },
  ]
}

/** 纯判定：只有显式标注 meta.v32 的路由进入 v3.2 界面。 */
export function isV32Route(route) {
  return route?.meta?.v32 === true
}
