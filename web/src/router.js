import { createRouter, createWebHashHistory } from 'vue-router'

import { auth } from './api'
import PipelineLibrary from './views/PipelineLibrary.vue'
import RunForm from './views/RunForm.vue'
import RunList from './views/RunList.vue'
import RunDetail from './views/RunDetail.vue'
import Artifacts from './views/Artifacts.vue'
import Settings from './views/Settings.vue'
import Login from './views/Login.vue'

const routes = [
  { path: '/login', name: 'login', component: Login, meta: { fullscreen: true, public: true } },
  { path: '/', name: 'library', component: PipelineLibrary },
  { path: '/pipelines/:id', name: 'run-form', component: RunForm },
  { path: '/runs', name: 'runs', component: RunList },
  { path: '/runs/:id', name: 'run-detail', component: RunDetail },
  { path: '/runs/:id/artifacts', name: 'artifacts', component: Artifacts },
  { path: '/settings', name: 'settings', component: Settings },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

router.beforeEach((to) => {
  if (!to.meta.public && !auth.token) return { name: 'login' }
  return true
})

export default router
