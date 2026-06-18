<template>
  <AppShell title="Дашборд" eyebrow="Обзор инфраструктуры">
    <div class="dashboard">
      <MetricStrip :metrics="metricItems" />

      <section class="main-grid">
        <div class="server-list panel">
          <div class="section-head">
            <h2>Серверы</h2>
          </div>

          <div v-if="servers.length" class="server-rows">
            <component
              :is="isAdmin ? RouterLink : 'div'"
              v-for="server in servers"
              :key="server.id"
              v-bind="isAdmin ? { to: { name: 'server-detail', params: { id: server.id } } } : {}"
              class="server-row"
            >
              <div class="server-main">
                <span class="entity-avatar entity-avatar--sm">{{ server.name.charAt(0).toUpperCase() }}</span>
                <div class="server-text">
                  <strong>{{ server.name }}</strong>
                  <span class="mono">{{ serverHost(server) }}</span>
                </div>
              </div>
              <div class="server-trailing">
                <span v-if="isAdmin" class="server-meta">{{ server.active_peers }} клиентов</span>
                <StatusBadge
                  :label="serverStatusLabel(server)"
                  :tone="serverStatusTone(server)"
                  :pulse="serverOnline(server)"
                />
              </div>
            </component>
          </div>

          <EmptyState
            v-else
            title="Серверов пока нет"
            text="Добавьте VPS на странице «Серверы»."
          />
        </div>

        <aside class="status-panel panel">
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
        </aside>
      </section>
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { api } from '@/api/client'
import EmptyState from '@/components/EmptyState.vue'
import MetricStrip from '@/components/MetricStrip.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { onRevisit } from '@/composables/useRevisit'
import AppShell from '@/layouts/AppShell.vue'
import { useAuthStore } from '@/stores/auth'
import { formatBytes } from '@/utils/format'

defineOptions({ name: 'DashboardView' })

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
onRevisit(() => void load())

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

function serverHost(server: ServerListItem) {
  return isAdmin.value ? `${server.host}:${server.ssh_port}` : server.host
}

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
  grid-template-columns: minmax(0, 1fr) 280px;
  gap: 18px;
  align-items: start;
}

.server-list {
  overflow: hidden;
}

.section-head {
  padding: 12px 14px;
  border-bottom: 1px solid var(--color-border);
}

h2 {
  margin: 0;
  font-size: 15px;
  letter-spacing: 0;
}

.server-rows {
  display: grid;
}

.server-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 14px;
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

.server-text {
  min-width: 0;
}

.server-text strong {
  display: block;
  font-size: 13px;
  line-height: 1.3;
}

.server-text .mono {
  display: block;
  margin-top: 1px;
  color: var(--color-dim);
  font-size: 11.5px;
  line-height: 1.3;
}

.server-trailing {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}

.server-meta {
  color: var(--color-muted);
  font-size: 12px;
  white-space: nowrap;
}

.status-panel {
  padding: 12px 14px;
}

.status-panel h2 {
  margin-bottom: 4px;
}

.status-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  min-height: 36px;
  border-top: 1px solid var(--color-border);
  color: var(--color-muted);
  font-size: 12.5px;
}

@media (max-width: 900px) {
  .main-grid {
    grid-template-columns: 1fr;
  }

  .server-meta {
    display: none;
  }
}
</style>
