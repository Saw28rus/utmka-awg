<template>
  <AppShell title="Дашборд" eyebrow="Обзор инфраструктуры">
    <div class="dashboard">
      <MetricStrip :metrics="metricItems" />

      <section v-if="isAdmin && nodes.length" class="panel load-panel">
        <div class="section-head">
          <div>
            <h2>Нагрузка узлов</h2>
            <p>CPU / RAM / диск · uptime · трафик · по кэшу метрик</p>
          </div>
          <div class="load-summary">
            <span class="ch-chip"><Network :size="13" /> {{ channels.total }} каналов</span>
            <span class="ch-chip">{{ channels.cascade }} каскадов · {{ channels.direct }} direct</span>
            <span v-if="health.degraded" class="ch-chip warn">{{ health.degraded }} с проблемами</span>
            <span v-if="health.down" class="ch-chip down">{{ health.down }} недоступны</span>
          </div>
        </div>
        <div class="load-grid">
          <div v-for="node in nodes" :key="node.server_id" class="load-card">
            <div class="load-card-top">
              <span class="node-dot" :class="`h-${node.health}`" />
              <strong>{{ node.name }}</strong>
              <span class="load-uptime">{{ formatUptime(node.uptime_seconds) }}</span>
            </div>
            <div class="meter">
              <div class="meter-label"><span>CPU</span><span>{{ pct(node.cpu_percent) }}</span></div>
              <div class="bar"><div class="bar-fill" :class="barTone(node.cpu_percent)" :style="{ width: pctWidth(node.cpu_percent) }" /></div>
            </div>
            <div class="meter">
              <div class="meter-label"><span>RAM</span><span>{{ ratio(node.mem_used_bytes, node.mem_total_bytes) }}</span></div>
              <div class="bar"><div class="bar-fill" :class="barTone(ratioPct(node.mem_used_bytes, node.mem_total_bytes))" :style="{ width: pctWidth(ratioPct(node.mem_used_bytes, node.mem_total_bytes)) }" /></div>
            </div>
            <div class="meter">
              <div class="meter-label"><span>Диск</span><span>{{ ratio(node.disk_used_bytes, node.disk_total_bytes) }}</span></div>
              <div class="bar"><div class="bar-fill" :class="barTone(ratioPct(node.disk_used_bytes, node.disk_total_bytes))" :style="{ width: pctWidth(ratioPct(node.disk_used_bytes, node.disk_total_bytes)) }" /></div>
            </div>
            <div class="load-foot">
              <span>{{ node.active_peers }} клиентов</span>
              <span>{{ formatBytes(node.traffic_bytes || 0) }}</span>
            </div>
          </div>
        </div>
      </section>

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
import { Network, Plus } from '@lucide/vue'
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

type NodeLoad = {
  server_id: string
  name: string
  host?: string | null
  online: boolean
  health: 'ok' | 'degraded' | 'down' | 'unknown'
  cpu_percent?: number | null
  mem_used_bytes?: number | null
  mem_total_bytes?: number | null
  disk_used_bytes?: number | null
  disk_total_bytes?: number | null
  uptime_seconds?: number | null
  active_peers?: number | null
  traffic_bytes?: number | null
}

type ChannelSummary = { total: number; direct: number; cascade: number; awg: number; xray: number }

const summary = ref<DashboardSummary | null>(null)
const servers = ref<ServerListItem[]>([])
const nodes = ref<NodeLoad[]>([])
const channels = ref<ChannelSummary>({ total: 0, direct: 0, cascade: 0, awg: 0, xray: 0 })
const health = ref<{ degraded: number; down: number }>({ degraded: 0, down: 0 })

onMounted(load)

async function load() {
  const serversEndpoint = isAdmin.value ? '/servers' : '/servers/minimal'
  const [summaryRes, serversRes] = await Promise.all([
    api.get<DashboardSummary>('/dashboard/summary'),
    api.get<ServerListItem[]>(serversEndpoint)
  ])
  summary.value = summaryRes.data
  servers.value = serversRes.data
  if (isAdmin.value) void loadOverview()
}

async function loadOverview() {
  try {
    const { data } = await api.get<{
      nodes: NodeLoad[]
      channels: ChannelSummary
      health: { degraded: number; down: number }
    }>('/dashboard/overview')
    nodes.value = data.nodes || []
    channels.value = data.channels
    health.value = data.health
  } catch {
    /* API недоступен на старой версии */
  }
}

function pct(v?: number | null): string {
  if (v == null) return '—'
  return `${Math.round(v)}%`
}

function ratioPct(used?: number | null, total?: number | null): number | null {
  if (!used || !total || total <= 0) return null
  return (used / total) * 100
}

function ratio(used?: number | null, total?: number | null): string {
  if (!total) return '—'
  return `${formatBytes(used || 0)} / ${formatBytes(total)}`
}

function pctWidth(v?: number | null): string {
  if (v == null) return '0%'
  return `${Math.max(0, Math.min(100, v))}%`
}

function barTone(v?: number | null): string {
  if (v == null) return ''
  if (v >= 90) return 'crit'
  if (v >= 75) return 'warn'
  return 'ok'
}

function formatUptime(seconds?: number | null): string {
  if (!seconds || seconds <= 0) return '—'
  const d = Math.floor(seconds / 86400)
  const h = Math.floor((seconds % 86400) / 3600)
  if (d > 0) return `${d}д ${h}ч`
  const m = Math.floor((seconds % 3600) / 60)
  if (h > 0) return `${h}ч ${m}м`
  return `${m}м`
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

.load-panel {
  overflow: hidden;
}

.load-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.ch-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  padding: 3px 9px;
  border-radius: 999px;
  background: var(--color-surface-2);
  color: var(--color-muted);
}

.ch-chip.warn {
  color: #f5a623;
}

.ch-chip.down {
  color: #e5484d;
}

.load-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 14px;
  padding: 16px;
}

.load-card {
  display: flex;
  flex-direction: column;
  gap: 9px;
  padding: 14px;
  border: 1px solid var(--color-border);
  border-radius: 12px;
  background: var(--color-surface);
}

.load-card-top {
  display: flex;
  align-items: center;
  gap: 8px;
}

.load-card-top strong {
  font-size: 14px;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.load-uptime {
  margin-left: auto;
  font-size: 11px;
  color: var(--color-dim);
  flex-shrink: 0;
}

.node-dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: var(--color-dim);
  flex-shrink: 0;
}
.node-dot.h-ok {
  background: #30a46c;
}
.node-dot.h-degraded {
  background: #f5a623;
}
.node-dot.h-down {
  background: #e5484d;
}

.meter {
  display: grid;
  gap: 4px;
}

.meter-label {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  color: var(--color-muted);
}

.bar {
  height: 6px;
  border-radius: 4px;
  background: var(--color-surface-2);
  overflow: hidden;
}

.bar-fill {
  height: 100%;
  border-radius: 4px;
  background: var(--color-accent);
  transition: width 0.3s ease;
}
.bar-fill.ok {
  background: #30a46c;
}
.bar-fill.warn {
  background: #f5a623;
}
.bar-fill.crit {
  background: #e5484d;
}

.load-foot {
  display: flex;
  justify-content: space-between;
  border-top: 1px solid var(--color-border);
  padding-top: 8px;
  font-size: 12px;
  color: var(--color-muted);
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
