import { createVuetify } from 'vuetify'
import { VApp,VAlert,VBtn,VCard,VCheckbox,VNavigationDrawer,VPagination,VProgressLinear,VSelect,VSlider,VSwitch,VTextarea,VTextField } from 'vuetify/components'
import { Ripple } from 'vuetify/directives'
import { aliases,mdi } from 'vuetify/iconsets/mdi-svg'

// 路由表已统一到 src/router.js（UI_INTEGRATION_PLAN.md S1）。
// 这里只剩 Vuetify 主题：首页的三个 v-btn 依赖它。
export function createWorkspaceTheme() {
  const components={VApp,VAlert,VBtn,VCard,VCheckbox,VNavigationDrawer,VPagination,VProgressLinear,VSelect,VSlider,VSwitch,VTextarea,VTextField}
  return createVuetify({components,directives:{Ripple},icons:{defaultSet:'mdi',aliases,sets:{mdi}},theme:{defaultTheme:'light',themes:{light:{colors:{primary:'#000000',secondary:'#707780',background:'#ffffff',surface:'#ffffff',error:'#b3261e',success:'#1f7a45',warning:'#9a6100'}}}},defaults:{VBtn:{rounded:'md',elevation:0},VTextField:{variant:'outlined',density:'compact',hideDetails:'auto'},VTextarea:{variant:'outlined',density:'compact',hideDetails:'auto'},VSelect:{variant:'outlined',density:'compact',hideDetails:'auto'},VCard:{variant:'outlined',rounded:'lg'}}})
}
