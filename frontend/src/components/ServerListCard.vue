<template>
  <RouterLink
    :to="to"
    class="server-card panel"
    :class="{ 'server-card--cascade-active': cascadeRole?.is_active }"
  >
    <header class="card-head">
      <div class="card-id">
        <span class="entity-avatar entity-avatar--lg">{{ server.name.charAt(0).toUpperCase() }}</span>
        <div class="server-text">
          <div class="name-row">
            <strong>{{ server.name }}</strong>
            <span v-if="roleLabel" class="role-pill" :class="`role-pill--${roleLabel}`">{{ roleLabelText }}</span>
          </div>
          <span class="mono">{{ server.host }}:{{ server.ssh_port }}</span>
        </div>
      </div>
      <StatusBadge :label="statusLabel" :tone="statusTone" :pulse="metrics?.online" />
    </header>

    <div v-if="cascadeRole && !roleLabel" class="cascade-peer">
      <span class="cascade-peer-chip" :class="cascadeChipClass">
        <Network :size="13" />
        <template v-if="cascadeRole.role === 'entry'">
          Каскад → <strong>{{ cascadeRole.peer_name }}</strong>
        </template>
        <template v-else>
          Выход ← <strong>{{ cascadeRole.peer_name }}</strong>
        </template>
      </span>
      <StatusBadge
        :label="cascadeStateLabel"
        :tone="cascadeStateTone"
        :pulse="cascadeRole.is_active"
      />
    </div>

    <div v-if="healthProblem" class="health-line" :class="`health-${health?.state}`">
      <AlertTriangle :size="13" />
      <span class="health-text">{{ healthText }}</span>
    </div>

    <div class="protocols">
      <template v-if="server.protocols.length">
        <span v-for="proto in server.protocols" :key="proto" class="proto-chip">
          <ShieldCheck :size="13" />
          {{ proto }}
        </span>
      </template>
      <span v-else class="proto-chip muted-chip">Протокол не подтверждён</span>
    </div>

    <div class="metrics">
      <template v-if="metrics?.online">
        <MetricBar label="CPU" :icon="Cpu" :percent="metrics.cpu_percent" :value-text="cpuText" />
        <MetricBar label="ОЗУ" :icon="MemoryStick" :percent="memPercent" :value-text="memText" />
        <MetricBar label="Диск" :icon="HardDrive" :percent="diskPercent" :value-text="diskText" />
      </template>
      <div v-else-if="metricsLoading" class="metrics-state">
        <n-spin size="small" />
        <span>Считываю метрики…</span>
      </div>
      <div v-else class="metrics-state offline">
        <WifiOff :size="16" />
        <span>{{ metrics?.message || 'Нет данных по SSH' }}</span>
      </div>
    </div>

    <footer class="card-foot">
      <span class="foot-item">
        <Users :size="13" />
        {{ peersText }}
      </span>
      <span class="foot-item">
        <ArrowDownUp :size="13" />
        {{ trafficText }}
      </span>
      <span class="foot-item">
        <Clock :size="13" />
        {{ uptimeText }}
      </span>
      <button
        class="foot-btn health-btn"
        :class="`health-${health?.state || 'unknown'}`"
        :title="healthBtnTitle"
        @click.prevent.stop="$emit('check')"
      >
        <n-spin v-if="healthChecking" :size="12" />
        <Activity v-else :size="14" />
      </button>
      <button class="foot-btn delete-btn" title="Удалить из панели" @click.prevent.stop="$emit('delete')">
        <Trash2 :size="14" />
      </button>
    </footer>
  </RouterLink>
</template>

<script setup lang="ts">
import { Activity, AlertTriangle, ArrowDownUp, Clock, Cpu, HardDrive, MemoryStick, Network, ShieldCheck, Trash2, Users, WifiOff } from '@lucide/vue'
import { NSpin } from 'naive-ui'
import { computed } from 'vue'
import { RouterLink, type RouteLocationRaw } from 'vue-router'

import MetricBar from '@/components/MetricBar.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { labelCascadeRole, labelCascadeState, toneCascadeState } from '@/utils/cascadeLabels'
import { formatBytes, formatUptime, percentOf } from '@/utils/format'

export type ServerListItem = {
  id: string
  name: string
  host: string
  ssh_port: number
  status: string
  protocols: string[]
  active_peers: number
}

export type ServerMetrics = {
  server_id?: string
  status?: string
  online: boolean
  cpu_percent: number | null
  mem_used_bytes: number | null
  mem_total_bytes: number | null
  disk_used_bytes: number | null
  disk_total_bytes: number | null
  uptime_seconds: number | null
  active_peers: number
  total_traffic_bytes: number
  message?: string | null
}

export type CascadePeerRole = {
  role: 'entry' | 'exit'
  peer_name: string
  state: string
  is_active: boolean
}

export type NodeHealth = {
  state: 'ok' | 'degraded' | 'down' | 'unknown'
  containers: Record<string, string>
  alerts: Array<{ level: string; code: string; message: string }>
  checked_at?: string | null
}

const props = defineProps<{
  server: ServerListItem
  to: RouteLocationRaw
  metrics?: ServerMetrics | null
  metricsLoading?: boolean
  cascadeRole?: CascadePeerRole | null
  roleLabel?: 'entry' | 'exit' | null
  health?: NodeHealth | null
  healthChecking?: boolean
}>()

defineEmits<{ delete: []; check: [] }>()

const healthProblem = computed(
  () => props.health && (props.health.state === 'degraded' || props.health.state === 'down')
)

const healthText = computed(() => {
  const h = props.health
  if (!h) return ''
  if (h.state === 'down') return 'Узел недоступен по SSH'
  const bad = Object.entries(h.containers || {})
    .filter(([, s]) => s !== 'running' && s !== 'missing')
    .map(([name, s]) => `${name}: ${s}`)
  if (bad.length) return bad.join(', ')
  return h.alerts?.[0]?.message || 'Есть проблемы'
})

const healthBtnTitle = computed(() => {
  const h = props.health
  if (!h || h.state === 'unknown') return 'Проверить здоровье'
  const map: Record<string, string> = {
    ok: 'Здоровье: в норме',
    degraded: 'Здоровье: есть проблемы',
    down: 'Здоровье: недоступен'
  }
  return `${map[h.state] || 'Проверить здоровье'} — нажмите для проверки`
})

const statusLabel = computed(() => {
  if (props.metricsLoading) return 'проверка'
  if (!props.metrics) return props.server.status
  return props.metrics.online ? 'online' : 'offline'
})

const statusTone = computed(() => {
  if (!props.metrics) return 'neutral' as const
  return props.metrics.online ? ('ok' as const) : ('danger' as const)
})

const roleLabelText = computed(() =>
  props.roleLabel ? labelCascadeRole(props.roleLabel) : ''
)

const cascadeStateLabel = computed(() =>
  labelCascadeState(props.cascadeRole?.state || '')
)

const cascadeStateTone = computed(() =>
  toneCascadeState(props.cascadeRole?.state || '')
)

const cascadeChipClass = computed(() =>
  props.cascadeRole?.is_active ? 'cascade-peer-chip--active' : 'cascade-peer-chip--idle'
)

const cpuText = computed(() => {
  const v = props.metrics?.cpu_percent
  return v == null ? '—' : `${Math.round(v)}%`
})

const memPercent = computed(() =>
  percentOf(props.metrics?.mem_used_bytes, props.metrics?.mem_total_bytes)
)

const memText = computed(() => {
  const m = props.metrics
  if (!m?.mem_total_bytes) return '—'
  return `${formatBytes(m.mem_used_bytes)} / ${formatBytes(m.mem_total_bytes)}`
})

const diskPercent = computed(() =>
  percentOf(props.metrics?.disk_used_bytes, props.metrics?.disk_total_bytes)
)

const diskText = computed(() => {
  const m = props.metrics
  if (!m?.disk_total_bytes) return '—'
  return `${formatBytes(m.disk_used_bytes)} / ${formatBytes(m.disk_total_bytes)}`
})

const peersText = computed(() => {
  const count = props.metrics?.active_peers ?? props.server.active_peers
  return `${count} клиентов`
})

const uptimeText = computed(() => formatUptime(props.metrics?.uptime_seconds))

const trafficText = computed(() => formatBytes(props.metrics?.total_traffic_bytes))
</script>

<style scoped>
.server-card {
  display: grid;
  gap: 13px;
  padding: 14px;
  color: inherit;
  text-decoration: none;
  transition: border-color 0.15s ease;
  min-width: 0;
}

.server-card:hover {
  border-color: var(--color-border-hover);
}

.server-card--cascade-active {
  border-color: var(--color-cascade-border-active);
}

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.card-id {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.server-text {
  min-width: 0;
}

.name-row {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.name-row strong {
  font-size: 14px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.role-pill {
  flex-shrink: 0;
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.role-pill--entry {
  color: var(--color-pill-entry-text);
  background: var(--color-pill-entry-bg);
  border: 1px solid var(--color-pill-entry-border);
}

.role-pill--exit {
  color: var(--color-pill-exit-text);
  background: var(--color-pill-exit-bg);
  border: 1px solid var(--color-pill-exit-border);
}

.server-text .mono {
  display: block;
  margin-top: 1px;
  color: var(--color-muted);
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cascade-peer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
}

.cascade-peer-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: 999px;
  border: 1px solid var(--color-border);
  font-size: 12px;
  color: var(--color-muted);
}

.cascade-peer-chip--active {
  color: var(--color-accent);
  border-color: var(--color-cascade-border-active);
  background: var(--color-accent-soft);
}

.cascade-peer-chip--idle {
  color: var(--color-dim);
}

.health-line {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  border-radius: 8px;
  font-size: 12px;
  background: var(--color-surface-2);
}

.health-line.health-degraded {
  color: #fbbf24;
}

.health-line.health-down {
  color: #f87171;
}

.health-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.protocols {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.proto-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 9px;
  border: 1px solid var(--color-border);
  border-radius: 999px;
  color: var(--color-muted);
  font-size: 12px;
}

.proto-chip svg {
  color: var(--color-accent);
}

.muted-chip {
  color: var(--color-dim);
}

.metrics {
  display: grid;
  gap: 10px;
  min-height: 84px;
  align-content: center;
}

.metrics-state {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--color-muted);
  font-size: 13px;
}

.metrics-state.offline {
  color: var(--color-danger);
}

.card-foot {
  display: flex;
  align-items: center;
  gap: 14px;
  padding-top: 11px;
  border-top: 1px solid var(--color-border);
  color: var(--color-muted);
  font-size: 12px;
}

.foot-item {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

.foot-btn {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  border: 1px solid transparent;
  border-radius: 7px;
  background: transparent;
  color: var(--color-dim);
  cursor: pointer;
  transition:
    color 0.15s ease,
    background-color 0.15s ease;
}

.health-btn {
  margin-left: auto;
}

.health-btn.health-ok {
  color: #4ade80;
}

.health-btn.health-degraded {
  color: #fbbf24;
}

.health-btn.health-down {
  color: #f87171;
}

.health-btn:hover {
  background: var(--color-surface-2);
}

.delete-btn:hover {
  color: var(--color-danger);
  background: var(--color-accent-soft);
}
</style>
