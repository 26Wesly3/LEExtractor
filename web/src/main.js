import { createApp } from 'vue'
import { createRouter,createWebHistory } from 'vue-router'
import { createWorkspaceRouter,createWorkspaceTheme } from './bootstrap.js'
import 'vuetify/styles'
import AppRoot from './v32/AppRoot.vue'
import { createV32Routes } from './v32/routes.js'
import './styles.css'

export { createV32Routes, isV32Route } from './v32/routes.js'

/**
 * 附加路由，不替换既有路由：v3.2 路由排在既有路由表之前，
 * 既有路由（含结尾的 /:pathMatch(.*)* 通配重定向）保持原样。
 */
export function createV32Router(history=createWebHistory(),scrollBehavior=()=>({top:0})){
  const routes=[...createV32Routes(),...createWorkspaceRouter(history).options.routes]
  return createRouter({history,routes,scrollBehavior})
}

if(typeof document!=='undefined'&&document.getElementById('app')){
  createApp(AppRoot).use(createV32Router()).use(createWorkspaceTheme()).mount('#app')
}
