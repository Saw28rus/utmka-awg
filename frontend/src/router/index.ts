import { createRouter, createWebHistory } from 'vue-router'
import { createDiscreteApi } from 'naive-ui'

import { useAuthStore } from '@/stores/auth'
import { useIntegrationsStore } from '@/stores/integrations'
import { usePanelUpdateStore } from '@/stores/panelUpdate'
import ChannelsView from '@/views/ChannelsView.vue'
import HealthView from '@/views/HealthView.vue'
import ChatView from '@/views/ChatView.vue'
import ClientDetailView from '@/views/ClientDetailView.vue'
import ClientsView from '@/views/ClientsView.vue'
import DashboardView from '@/views/DashboardView.vue'
import InvoiceCreatedBatchView from '@/views/InvoiceCreatedBatchView.vue'
import InvoicesView from '@/views/InvoicesView.vue'
import LoginView from '@/views/LoginView.vue'
import ServerDetailView from '@/views/ServerDetailView.vue'
import ServersView from '@/views/ServersView.vue'
import SettingsView from '@/views/SettingsView.vue'
import UsersView from '@/views/UsersView.vue'

const { message } = createDiscreteApi(['message'])

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: LoginView, meta: { public: true } },
    { path: '/', name: 'dashboard', component: DashboardView, meta: { roles: ['admin', 'moderator'] } },
    { path: '/servers', name: 'servers', component: ServersView, meta: { roles: ['admin'] } },
    { path: '/servers/:id', name: 'server-detail', component: ServerDetailView, meta: { roles: ['admin'] } },
    { path: '/channels', name: 'channels', component: ChannelsView, meta: { roles: ['admin'] } },
    { path: '/health', name: 'health', component: HealthView, meta: { roles: ['admin'] } },
    { path: '/clients', name: 'clients', component: ClientsView, meta: { roles: ['admin', 'moderator'] } },
    { path: '/clients/:id', name: 'client-detail', component: ClientDetailView, meta: { roles: ['admin', 'moderator'] } },
    { path: '/invoices', name: 'invoices', component: InvoicesView, meta: { roles: ['admin', 'moderator'], requires: 'yookassa' } },
    {
      path: '/invoices/created',
      name: 'invoices-created',
      component: InvoiceCreatedBatchView,
      meta: { roles: ['admin', 'moderator'], requires: 'yookassa' }
    },
    { path: '/chat', name: 'chat', component: ChatView, meta: { roles: ['admin', 'moderator'], requires: 'chat' } },
    { path: '/users', name: 'users', component: UsersView, meta: { roles: ['admin'] } },
    { path: '/settings', name: 'settings', component: SettingsView, meta: { roles: ['admin'] } }
  ]
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  const panelUpdate = usePanelUpdateStore()
  if (to.meta.public) return true

  if (panelUpdate.isRunning && to.name !== router.currentRoute.value.name) {
    message.warning('Дождитесь завершения обновления или нажмите «Отменить».')
    return false
  }
  if (!auth.isAuthenticated) return { name: 'login' }
  if (!auth.user) {
    try {
      await auth.loadMe()
    } catch {
      auth.logout()
      return { name: 'login' }
    }
  }

  const roles = to.meta.roles as string[] | undefined
  if (roles && auth.user && !roles.includes(auth.user.role)) {
    message.warning('Доступ только к клиентам.')
    return { name: 'clients' }
  }

  if (to.meta.requires === 'yookassa') {
    const integrations = useIntegrationsStore()
    if (!integrations.loaded) await integrations.load()
    if (!integrations.yookassaConnected) {
      message.warning('Подключите ЮKassa в настройках.')
      return { name: 'dashboard' }
    }
  }

  if (to.meta.requires === 'chat') {
    const integrations = useIntegrationsStore()
    if (!integrations.loaded) await integrations.load()
    if (!integrations.chatEnabled) {
      message.warning('Сначала подключите домен чата (Сервер → Безопасность, шаг 3).')
      return { name: 'dashboard' }
    }
  }
  return true
})
