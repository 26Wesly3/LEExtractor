import { createApp } from 'vue'
import { createRouter,createWebHistory } from 'vue-router'
import { createWorkspaceTheme } from './bootstrap.js'
import 'vuetify/styles'
import AppShell from './shell/AppShell.vue'
import { createAppRoutes,isV32Route } from './router.js'
import './styles.css'

// 路由表在 src/router.js（S1），外壳在 src/shell/AppShell.vue（S2），这里只负责装配。
export { createAppRoutes,isV32Route,STAGES } from './router.js'

export function createAppRouter(history=createWebHistory(),scrollBehavior=()=>({top:0})){
  return createRouter({history,routes:createAppRoutes(),scrollBehavior})
}

if(typeof document!=='undefined'&&document.getElementById('app')){
  createApp(AppShell).use(createAppRouter()).use(createWorkspaceTheme()).mount('#app')
}
