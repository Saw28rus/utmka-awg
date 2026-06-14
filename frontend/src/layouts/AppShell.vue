<template>
  <div class="app-shell">
    <aside class="sidebar">
      <RouterLink class="brand" to="/">
        <ShieldCheck :size="22" />
        <span>{{ panel.appName }}</span>
      </RouterLink>

      <nav class="nav">
        <RouterLink v-for="item in visibleNav" :key="item.to" :to="item.to" class="nav-link">
          <component :is="item.icon" :size="18" />
          <span>{{ item.label }}</span>
        </RouterLink>
      </nav>
    </aside>

    <main class="workspace">
      <header class="topbar">
        <div>
          <p class="eyebrow">{{ eyebrow }}</p>
          <h1>{{ title }}</h1>
        </div>
        <div class="topbar-actions">
          <n-popover
            v-if="isAdmin"
            trigger="click"
            placement="bottom-end"
            :width="340"
            @update:show="onBellToggle"
          >
            <template #trigger>
              <n-badge :value="notifications.unread" :max="99" :show="notifications.unread > 0">
                <n-button tertiary circle aria-label="Уведомления">
                  <template #icon><Bell :size="17" /></template>
                </n-button>
              </n-badge>
            </template>
            <div class="notif-pop">
              <div class="notif-head">
                <span>Уведомления</span>
                <button
                  v-if="notifications.unread > 0"
                  class="notif-readall"
                  @click="notifications.markRead()"
                >
                  Прочитать все
                </button>
              </div>
              <div v-if="!notifications.items.length" class="notif-empty">Пока пусто</div>
              <ul v-else class="notif-list">
                <li
                  v-for="n in notifications.items"
                  :key="n.id"
                  class="notif-item"
                  :class="[`lvl-${n.level}`, { unread: !n.read }]"
                >
                  <strong>{{ n.title }}</strong>
                  <span class="notif-msg">{{ n.message }}</span>
                  <span class="notif-time">{{ formatTime(n.created_at) }}</span>
                </li>
              </ul>
            </div>
          </n-popover>
          <n-button tertiary circle :title="themeLabel" aria-label="Сменить тему" @click="toggleTheme">
            <template #icon>
              <Sun v-if="theme.mode === 'dark'" :size="17" />
              <Moon v-else :size="17" />
            </template>
          </n-button>
          <n-button tertiary circle aria-label="Выйти" @click="logout">
            <template #icon><LogOut :size="17" /></template>
          </n-button>
        </div>
      </header>

      <section class="content">
        <slot />
      </section>
    </main>
  </div>
</template>

<script setup lang="ts">
import { Activity, Bell, KeyRound, LayoutDashboard, LogOut, MessagesSquare, Moon, Network, Receipt, Server, Settings, ShieldCheck, Sun, Users, Waypoints } from '@lucide/vue'
import { NBadge, NButton, NPopover } from 'naive-ui'
import { computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'
import { useIntegrationsStore } from '@/stores/integrations'
import { useNotificationsStore } from '@/stores/notifications'
import { usePanelStore } from '@/stores/panel'
import { usePanelUpdateStore } from '@/stores/panelUpdate'
import { useThemeStore } from '@/stores/theme'

defineProps<{
  title: string
  eyebrow?: string
}>()

const router = useRouter()
const auth = useAuthStore()
const theme = useThemeStore()
const panel = usePanelStore()
const panelUpdate = usePanelUpdateStore()
const integrations = useIntegrationsStore()
const notifications = useNotificationsStore()

const isAdmin = computed(() => auth.user?.role === 'admin')

type NavItem = {
  to: string
  label: string
  icon: object
  roles: string[]
  requires?: 'yookassa' | 'chat'
}

const allNav: NavItem[] = [
  { to: '/', label: 'Дашборд', icon: LayoutDashboard, roles: ['admin', 'moderator'] },
  { to: '/servers', label: 'Серверы', icon: Server, roles: ['admin'] },
  { to: '/channels', label: 'Каналы', icon: Network, roles: ['admin'] },
  { to: '/map', label: 'Карта', icon: Waypoints, roles: ['admin'] },
  { to: '/health', label: 'Здоровье', icon: Activity, roles: ['admin'] },
  { to: '/clients', label: 'Клиенты', icon: KeyRound, roles: ['admin', 'moderator'] },
  { to: '/invoices', label: 'Счета и оплата', icon: Receipt, roles: ['admin', 'moderator'], requires: 'yookassa' },
  { to: '/chat', label: 'Чат', icon: MessagesSquare, roles: ['admin', 'moderator'], requires: 'chat' },
  { to: '/users', label: 'Пользователи', icon: Users, roles: ['admin'] },
  { to: '/settings', label: 'Настройки', icon: Settings, roles: ['admin'] }
]

const visibleNav = computed(() => {
  const role = auth.user?.role || 'admin'
  return allNav.filter((item) => {
    if (!item.roles.includes(role)) return false
    if (item.requires === 'yookassa' && !integrations.yookassaConnected) return false
    if (item.requires === 'chat' && !integrations.chatEnabled) return false
    return true
  })
})

const themeLabel = computed(() => (theme.mode === 'dark' ? 'Светлая тема' : 'Тёмная тема'))

onMounted(() => {
  panel.loadPublicName()
  void integrations.load()
  if (auth.user?.role === 'admin') {
    void panelUpdate.resume()
    notifications.startPolling()
  }
})

onUnmounted(() => {
  notifications.stopPolling()
})

function onBellToggle(show: boolean) {
  if (show && notifications.unread > 0) {
    void notifications.markRead()
  }
}

function formatTime(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function toggleTheme() {
  theme.toggle()
}

function logout() {
  auth.logout()
  router.push({ name: 'login' })
}
</script>

<style scoped>
.app-shell {
  min-height: 100vh;
  display: grid;
  grid-template-columns: var(--sidebar-width) minmax(0, 1fr);
  background: var(--color-bg);
}

.sidebar {
  min-height: 100vh;
  border-right: 1px solid var(--color-border);
  background: var(--color-sidebar);
  padding: 16px 12px;
}

.brand {
  height: 40px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 10px;
  color: var(--color-text);
  font-weight: 700;
}

.brand svg {
  color: var(--color-accent);
}

.nav {
  display: grid;
  gap: 4px;
  margin-top: 24px;
}

.nav-link {
  height: 38px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 10px;
  border-radius: 7px;
  color: var(--color-muted);
  font-size: 14px;
}

.nav-link.router-link-active {
  color: var(--color-text);
  background: var(--color-surface-2);
}

.workspace {
  min-width: 0;
}

.topbar {
  min-height: var(--topbar-height);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 24px;
  border-bottom: 1px solid var(--color-border);
}

.eyebrow {
  margin: 0 0 2px;
  color: var(--color-dim);
  font-size: 12px;
}

h1 {
  margin: 0;
  font-size: 20px;
  font-weight: 650;
  letter-spacing: 0;
}

.topbar-actions {
  display: flex;
  gap: 8px;
}

.content {
  padding: 24px;
}

.notif-pop {
  display: flex;
  flex-direction: column;
  max-height: 420px;
}

.notif-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 2px 8px;
  font-weight: 650;
  font-size: 14px;
}

.notif-readall {
  background: none;
  border: none;
  cursor: pointer;
  color: var(--color-accent);
  font-size: 12px;
  padding: 0;
}

.notif-empty {
  padding: 16px 2px;
  color: var(--color-dim);
  font-size: 13px;
  text-align: center;
}

.notif-list {
  list-style: none;
  margin: 0;
  padding: 0;
  overflow-y: auto;
  display: grid;
  gap: 6px;
}

.notif-item {
  display: grid;
  gap: 2px;
  padding: 8px 10px;
  border-radius: 8px;
  background: var(--color-surface-2);
  border-left: 3px solid var(--color-border);
  font-size: 13px;
}

.notif-item.unread {
  background: var(--color-surface-3, var(--color-surface-2));
}

.notif-item.lvl-danger {
  border-left-color: #e5484d;
}

.notif-item.lvl-warning {
  border-left-color: #f5a623;
}

.notif-item.lvl-info {
  border-left-color: var(--color-accent);
}

.notif-msg {
  color: var(--color-muted);
  font-size: 12px;
}

.notif-time {
  color: var(--color-dim);
  font-size: 11px;
}

@media (max-width: 840px) {
  .brand span,
  .nav-link span {
    display: none;
  }

  .content,
  .topbar {
    padding-left: 16px;
    padding-right: 16px;
  }
}
</style>
