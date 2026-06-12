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
import { KeyRound, LayoutDashboard, LogOut, MessagesSquare, Moon, Receipt, Server, Settings, ShieldCheck, Sun, Users } from '@lucide/vue'
import { NButton } from 'naive-ui'
import { computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'
import { useIntegrationsStore } from '@/stores/integrations'
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
  }
})

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
