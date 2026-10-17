import { createApp } from 'vue'
import { createRouter,createWebHistory } from 'vue-router'
import { createWorkspaceTheme } from './bootstrap.js'
import 'vuetify/styles'
import AppRoot from './v32/AppRoot.vue'
import { createAppRoutes,isV32Route } from './router.js'
import './styles.css'

// 路由表在 src/router.js（UI_INTEGRATION_PLAN.md S1），这里只负责装配。
export { createAppRoutes,isV32Route,STAGES } from './router.js'
// 兼容在 S2 之前仍按旧名引用 v3.2 路由表的测试与组件。
export { createV32Routes } from './v32/routes.js'

export function createAppRouter(history=createWebHistory(),scrollBehavior=()=>({top:0})){
  return createRouter({history,routes:createAppRoutes(),scrollBehavior})
}

/** S1 过渡期的旧名：S2 起统一用 createAppRouter。 */
export const createV32Router=createAppRouter

if(typeof document!=='undefined'&&document.getElementById('app')){
  createApp(AppRoot).use(createAppRouter()).use(createWorkspaceTheme()).mount('#app')
}
