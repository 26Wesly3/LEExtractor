import { createRouter,createWebHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import { VApp,VAlert,VBtn,VCard,VCheckbox,VNavigationDrawer,VPagination,VProgressLinear,VSelect,VSlider,VSwitch,VTextarea,VTextField } from 'vuetify/components'
import { Ripple } from 'vuetify/directives'
import { aliases,mdi } from 'vuetify/iconsets/mdi-svg'
const Page={template:'<span />'}
export function createWorkspaceRouter(history=createWebHistory()) {
  const routes=[{path:'/',name:'home',component:Page},{path:'/search',name:'search',component:Page},{path:'/projects',name:'projects',component:Page},{path:'/settings',name:'settings',component:Page},...['search','results','expand','landscape','questions','review','export'].map(name=>({path:`/projects/:projectId/${name}`,name:`project-${name}`,component:Page})),{path:'/projects/:projectId/papers/:paperKey',name:'paper',component:Page},{path:'/:pathMatch(.*)*',redirect:'/'}]
  return createRouter({history,routes,scrollBehavior:()=>({top:0})})
}
export function createWorkspaceTheme() {
  const components={VApp,VAlert,VBtn,VCard,VCheckbox,VNavigationDrawer,VPagination,VProgressLinear,VSelect,VSlider,VSwitch,VTextarea,VTextField}
  return createVuetify({components,directives:{Ripple},icons:{defaultSet:'mdi',aliases,sets:{mdi}},theme:{defaultTheme:'light',themes:{light:{colors:{primary:'#000000',secondary:'#707780',background:'#ffffff',surface:'#ffffff',error:'#b3261e',success:'#1f7a45',warning:'#9a6100'}}}},defaults:{VBtn:{rounded:'md',elevation:0},VTextField:{variant:'outlined',density:'compact',hideDetails:'auto'},VTextarea:{variant:'outlined',density:'compact',hideDetails:'auto'},VSelect:{variant:'outlined',density:'compact',hideDetails:'auto'},VCard:{variant:'outlined',rounded:'lg'}}})
}
