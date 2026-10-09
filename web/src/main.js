import { createApp } from 'vue'
import { createWorkspaceRouter,createWorkspaceTheme } from './bootstrap.js'
import 'vuetify/styles'
import App from './App.vue'
import './styles.css'
createApp(App).use(createWorkspaceRouter()).use(createWorkspaceTheme()).mount('#app')
