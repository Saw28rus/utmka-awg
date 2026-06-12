<template>
  <AppShell title="Дашборд" eyebrow="Обзор инфраструктуры">
    <div class="dashboard">
      <MetricStrip :metrics="metricItems" />

      <section class="main-grid">
        <div class="server-list panel">
          <div class="section-head">
            <div>
              <h2>Серверы</h2>
              <p>{{ servers.length ? 'Состояние SSH, AWG2 и клиентов' : 'Добавь VPS для начала работы' }}</p>
            </div>
            <n-button
              v-if="isAdmin"
              type="primary"
              @click="$router.push({ path: '/servers', query: { add: '1' } })"
            >
              <template #icon><Plus :size="16" /></template>
              Добавить сервер
            </n-button>
          </div>

          <div v-if="servers.length" class="server-rows">
            <template v-if="isAdmin">
              <RouterLink
                v-for="server in servers"
                :key="server.id"
                :to="{ name: 'server-detail', params: { id: server.id } }"
                class="server-row"
              >
                <div class="server-main">
                  <span class="entity-avatar entity-avatar--md">{{ server.name.charAt(0).toUpperCase() }}</span>
                  <div>
                    <strong>{{ server.name }}</strong>
                    <span class="mono">{{ server.host }}:{{ server.ssh_port }}</span>
                  </div>
                </div>
                <span class="server-meta">{{ server.active_peers }} клиентов</span>
                <StatusBadge
                  :label="serverStatusLabel(server)"
                  :tone="serverStatusTone(server)"
                  :pulse="serverOnline(server)"
                />
              </RouterLink>
            </template>
            <template v-else>
              <div v-for="server in servers" :key="server.id" class="server-row">
                <div class="server-main">
                  <span class="entity-avatar entity-avatar--md">{{ server.name.charAt(0).toUpperCase() }}</span>
                  <div>
                    <strong>{{ server.name }}</strong>
                    <span class="mono">{{ server.host }}</span>
                  </div>
                </div>
                <StatusBadge
                  :label="serverStatusLabel(server)"
                  :tone="serverStatusTone(server)"
                  :pulse="serverOnline(server)"
                />
              </div>
            </template>
          </div>

          <EmptyState
            v-else
            title="Серверов пока нет"
            text="После добавления VPS панель покажет detect и предложит import или install."
          />
        </div>

        <aside class="side-stack">
          <div class="panel compact-panel">
            <h2>События</h2>
            <OperationTimeline v-if="events.length" :events="events" />
            <p v-else class="empty-note">Нет предупреждений — всё спокойно.</p>
          </div>

          <div class="panel compact-panel">
            <h2>Статусы</h2>
            <div class="status-row">
              <StatusBadge label="Панель online" tone="ok" :pulse="true" />
              <span>API отвечает</span>
            </div>
            <div class="status-row">
              <StatusBadge :label="awgLabel" :tone="awgTone" :pulse="awgOnline" />
              <span>{{ awgHint }}</span>
            </div>
            <div class="status-row">
              <StatusBadge :label="`${summary?.clients.active ?? 0} клиентов`" tone="info" />
              <span>{{ summary?.clients.online ?? 0 }} онлайн сейчас</span>
            </div>
          </div>
        </aside>
      </section>
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import { Plus } from '@lucide/vue'
import { NButton } from 'naive-ui'
import { computed, onMounted, ref } from 'vue'

import { api } from '@/api/client'
import EmptyState from '@/components/EmptyState.vue'
import MetricStrip from '@/components/MetricStrip.vue'
import OperationTimeline from '@/components/OperationTimeline.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import AppShell from '@/layouts/AppShell.vue'
import { useAuthStore } from '@/stores/auth'
import { formatBytes } from '@/utils/format'

const auth = useAuthStore()
const isAdmin = computed(() => auth.user?.role === 'admin')

type DashboardSummary = {
  servers: { online: number; total: number }
  clients: { active: number; online: number; expiring_soon: number }
  traffic_total_bytes: number
  alerts: Array<{ level: string; code: string; message: string }>
}

type ServerListItem = {
  id: string
  name: string
  host: string
  ssh_port: number
  status: string
  active_peers: number
  awg2_imported: boolean
}

const summary = ref<DashboardSummary | null>(null)
const servers = ref<ServerListItem[]>([])

onMounted(load)

async function load() {
  const serversEndpoint = isAdmin.value ? '/servers' : '/servers/minimal'
  const [summaryRes, serversRes] = await Promise.all([
    api.get<DashboardSummary>('/dashboard/summary'),
    api.get<ServerListItem[]>(serversEndpoint)
  ])
  summary.value = summaryRes.data
  servers.value = serversRes.data
}

const metricItems = computed(() => {
  const s = summary.value
  return [
    {
      label: 'Серверы online',
      value: s ? `${s.servers.online} / ${s.servers.total}` : '—',
      hint: s?.servers.total ? 'по SSH' : 'нет данных'
    },
    {
      label: 'Клиенты active',
      value: String(s?.clients.active ?? 0),
      hint: s?.clients.online ? `${s.clients.online} онлайн` : 'после import/создания'
    },
    {
      label: 'Истекают скоро',
      value: String(s?.clients.expiring_soon ?? 0),
      hint: 'за 7 дней'
    },
    {
      label: 'Трафик всего',
      value: formatBytes(s?.traffic_total_bytes ?? 0),
      hint: 'по всем клиентам'
    }
  ]
})

const events = computed(() => {
  const alerts = summary.value?.alerts ?? []
  const toneMap: Record<string, 'ok' | 'info' | 'warning' | 'danger' | 'neutral'> = {
    info: 'info',
    warning: 'warning',
    danger: 'danger'
  }
  return alerts.map((alert) => ({
    code: alert.code,
    label: alert.level === 'warning' ? 'Внимание' : 'Инфо',
    message: alert.message,
    tone: toneMap[alert.level] ?? 'neutral'
  }))
})

const awgOnline = computed(() => (summary.value?.servers.online ?? 0) > 0)
const awgLabel = computed(() => (servers.value.length ? 'AWG2' : 'AWG2'))
const awgTone = computed(() => {
  if (!servers.value.length) return 'neutral'
  return awgOnline.value ? 'ok' : 'danger'
})
const awgHint = computed(() => {
  if (!servers.value.length) return 'Серверы не добавлены'
  const imported = servers.value.filter((s) => s.awg2_imported).length
  return `${imported} из ${servers.value.length} с AWG2`
})

function serverOnline(server: ServerListItem) {
  return server.status === 'online'
}

function serverStatusLabel(server: ServerListItem) {
  return server.status === 'online' ? 'online' : server.status
}

function serverStatusTone(server: ServerListItem) {
  return server.status === 'online' ? 'ok' : 'danger'
}
</script>

<style scoped>
.dashboard {
  display: grid;
  gap: 18px;
}

.main-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 360px;
  gap: 18px;
}

.server-list {
  overflow: hidden;
}

.section-head {
  min-height: 64px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  padding: 16px;
  border-bottom: 1px solid var(--color-border);
}

h2 {
  margin: 0;
  font-size: 16px;
  letter-spacing: 0;
}

p {
  margin: 4px 0 0;
  color: var(--color-muted);
}

.server-rows {
  display: grid;
}

.server-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 110px auto;
  gap: 14px;
  align-items: center;
  padding: 12px 16px;
  color: inherit;
  text-decoration: none;
  border-bottom: 1px solid var(--color-border);
  transition: background-color 0.14s ease;
}

.server-row:hover {
  background: var(--color-surface-2);
}

.server-row:last-child {
  border-bottom: 0;
}

.server-main {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.server-main strong {
  display: block;
  font-size: 14px;
}

.server-main span {
  display: block;
  margin-top: 2px;
  color: var(--color-dim);
  font-size: 12px;
}

.server-meta {
  color: var(--color-muted);
  font-size: 12px;
}

.side-stack {
  display: grid;
  gap: 18px;
  align-content: start;
}

.compact-panel {
  padding: 16px;
}

.compact-panel :deep(.timeline) {
  margin-top: 12px;
  border-top: 1px solid var(--color-border);
  border-bottom: 1px solid var(--color-border);
}

.compact-panel :deep(.event) {
  grid-template-columns: 1fr;
  gap: 8px;
  align-items: start;
}

.empty-note {
  margin: 12px 0 0;
  color: var(--color-muted);
  font-size: 13px;
}

.status-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 42px;
  border-top: 1px solid var(--color-border);
  color: var(--color-muted);
  font-size: 13px;
}

@media (max-width: 1100px) {
  .main-grid {
    grid-template-columns: 1fr;
  }

  .server-row {
    grid-template-columns: 1fr auto;
  }

  .server-meta {
    display: none;
  }
}
</style>
