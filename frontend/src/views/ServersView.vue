<template>
  <AppShell title="Серверы" eyebrow="VPS и AWG2">
    <div class="servers-head">
      <div>
        <h2>{{ servers.length ? `${servers.length} VPS в панели` : 'Список серверов' }}</h2>
        <p>Состояние, нагрузка и протоколы по каждому серверу.</p>
      </div>
      <div class="head-actions">
        <n-button tertiary :loading="refreshing" @click="refreshAll">
          <template #icon><RefreshCw :size="16" /></template>
          Обновить
        </n-button>
        <n-button type="primary" @click="showAddServer = true">
          <template #icon><Plus :size="16" /></template>
          Добавить сервер
        </n-button>
      </div>
    </div>

    <div v-if="servers.length" class="server-grid">
      <template v-for="item in layoutItems" :key="item.key">
        <div
          v-if="item.kind === 'cascade'"
          class="cascade-group panel"
          :class="{ 'cascade-group--active': item.link.is_active }"
        >
          <div class="cascade-group-head">
            <span class="cascade-group-title">
              <Network :size="14" />
              Каскад
            </span>
            <StatusBadge
              :label="labelCascadeState(item.link.state)"
              :tone="toneCascadeState(item.link.state)"
              :pulse="item.link.is_active"
            />
          </div>

          <div class="cascade-group-body">
            <ServerListCard
              :server="item.entry"
              :to="{ name: 'server-detail', params: { id: item.entry.id }, query: { tab: 'cascade' } }"
              :metrics="metricFor(item.entry.id) ?? null"
              :metrics-loading="!!metricsLoading[item.entry.id]"
              :cascade-role="cascadeRoleFor(item.entry.id) ?? null"
              :health="healthFor(item.entry.id)"
              :health-checking="!!healthChecking[item.entry.id]"
              role-label="entry"
              @delete="confirmDelete(item.entry)"
              @check="checkHealth(item.entry.id)"
              @migrate-node="openMigrateNode"
            />

            <div class="cascade-link" :class="{ live: item.link.is_active }" aria-hidden="true">
              <span class="cascade-link-line" />
              <ArrowRight :size="16" />
              <span class="cascade-link-line" />
            </div>

            <ServerListCard
              :server="item.exit"
              :to="{ name: 'server-detail', params: { id: item.exit.id } }"
              :metrics="metricFor(item.exit.id) ?? null"
              :metrics-loading="!!metricsLoading[item.exit.id]"
              :cascade-role="cascadeRoleFor(item.exit.id) ?? null"
              :health="healthFor(item.exit.id)"
              :health-checking="!!healthChecking[item.exit.id]"
              role-label="exit"
              @delete="confirmDelete(item.exit)"
              @check="checkHealth(item.exit.id)"
            />
          </div>
        </div>

        <ServerListCard
          v-else
          :server="item.server"
          :to="{ name: 'server-detail', params: { id: item.server.id } }"
          :metrics="metricFor(item.server.id) ?? null"
          :metrics-loading="!!metricsLoading[item.server.id]"
          :cascade-role="cascadeRoleFor(item.server.id) ?? null"
          :health="healthFor(item.server.id)"
          :health-checking="!!healthChecking[item.server.id]"
          @delete="confirmDelete(item.server)"
          @check="checkHealth(item.server.id)"
        />
      </template>
    </div>

    <div v-else class="panel">
      <EmptyState
        title="Список пуст"
        text="Нажми «Добавить сервер», введи SSH-доступы, а панель проверит Amnezia/AWG2 без изменений на VPS."
      />
    </div>

    <AddServerWizard v-model:show="showAddServer" @created="onServerCreated" />

    <MigrateNodeModal v-model:show="migrateVisible" @migrated="onNodeMigrated" />
  </AppShell>
</template>

<script setup lang="ts">
import { ArrowRight, Network, Plus, RefreshCw } from '@lucide/vue'
import { NButton, useDialog, useMessage } from 'naive-ui'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { api } from '@/api/client'
import AddServerWizard from '@/components/AddServerWizard.vue'
import EmptyState from '@/components/EmptyState.vue'
import MigrateNodeModal from '@/components/MigrateNodeModal.vue'
import ServerListCard, { type NodeHealth, type ServerListItem, type ServerMetrics } from '@/components/ServerListCard.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import AppShell from '@/layouts/AppShell.vue'
import { labelCascadeState, toneCascadeState } from '@/utils/cascadeLabels'

type CascadeLinkSummary = {
  entry_server_id: string
  entry_name: string
  exit_server_id: string
  exit_name: string
  state: string
  is_active: boolean
}

type CascadePeerRole = {
  role: 'entry' | 'exit'
  peer_id: string
  peer_name: string
  state: string
  is_active: boolean
}

type LayoutItem =
  | { kind: 'cascade'; key: string; link: CascadeLinkSummary; entry: ServerListItem; exit: ServerListItem }
  | { kind: 'solo'; key: string; server: ServerListItem }

const route = useRoute()
const router = useRouter()
const dialog = useDialog()
const message = useMessage()

const showAddServer = ref(false)
const refreshing = ref(false)
const migrateVisible = ref(false)
const servers = ref<ServerListItem[]>([])
const cascadeLinks = ref<CascadeLinkSummary[]>([])
const metrics = reactive<Record<string, ServerMetrics>>({})
const metricsLoading = reactive<Record<string, boolean>>({})
const health = reactive<Record<string, NodeHealth>>({})
const healthChecking = reactive<Record<string, boolean>>({})

type HealthNodeApi = {
  server_id: string
  state: NodeHealth['state']
  containers: Record<string, string>
  alerts: Array<{ level: string; code: string; message: string }>
  checked_at: string | null
}

const cascadePeerMap = computed(() => {
  const map = new Map<string, CascadePeerRole>()
  for (const link of cascadeLinks.value) {
    map.set(link.entry_server_id, {
      role: 'entry',
      peer_id: link.exit_server_id,
      peer_name: link.exit_name,
      state: link.state,
      is_active: link.is_active
    })
    map.set(link.exit_server_id, {
      role: 'exit',
      peer_id: link.entry_server_id,
      peer_name: link.entry_name,
      state: link.state,
      is_active: link.is_active
    })
  }
  return map
})

const layoutItems = computed((): LayoutItem[] => {
  const used = new Set<string>()
  const items: LayoutItem[] = []

  for (const link of cascadeLinks.value) {
    const entry = servers.value.find((s) => s.id === link.entry_server_id)
    const exit = servers.value.find((s) => s.id === link.exit_server_id)
    if (!entry || !exit) continue
    used.add(entry.id)
    used.add(exit.id)
    items.push({
      kind: 'cascade',
      key: `cascade-${link.entry_server_id}`,
      link,
      entry,
      exit
    })
  }

  for (const server of servers.value) {
    if (!used.has(server.id)) {
      items.push({ kind: 'solo', key: server.id, server })
    }
  }

  return items
})

onMounted(async () => {
  await loadServers()
  if (route.query.add === '1') {
    showAddServer.value = true
    router.replace({ path: '/servers' })
  }
})

watch(
  () => route.query.add,
  (value) => {
    if (value === '1') showAddServer.value = true
  }
)

async function loadCascadeLinks(live = false) {
  try {
    const { data } = await api.get<CascadeLinkSummary[]>('/servers/cascade/links', {
      params: { live }
    })
    cascadeLinks.value = data
  } catch {
    cascadeLinks.value = []
  }
}

async function loadHealth() {
  try {
    const { data } = await api.get<HealthNodeApi[]>('/health/nodes')
    for (const node of data) {
      health[node.server_id] = {
        state: node.state,
        containers: node.containers || {},
        alerts: node.alerts || [],
        checked_at: node.checked_at
      }
    }
  } catch {
    /* health-движок недоступен на старой версии */
  }
}

async function checkHealth(serverId: string) {
  healthChecking[serverId] = true
  try {
    const { data } = await api.post<HealthNodeApi>(
      `/health/nodes/${serverId}/check`,
      {},
      { timeout: 60_000 }
    )
    health[serverId] = {
      state: data.state,
      containers: data.containers || {},
      alerts: data.alerts || [],
      checked_at: data.checked_at
    }
    if (data.state === 'ok') message.success('Узел в норме.')
    else if (data.state === 'degraded') message.warning('Есть проблемы с контейнерами.')
    else if (data.state === 'down') message.error('Узел недоступен по SSH.')
  } catch (error: any) {
    message.error(error?.response?.data?.detail || 'Не удалось проверить узел.')
  } finally {
    healthChecking[serverId] = false
  }
}

function healthFor(serverId: string): NodeHealth | null {
  return health[serverId] ?? null
}

function applyMetricsBatch(items: ServerMetrics[]) {
  for (const item of items) {
    const serverId = item.server_id
    if (!serverId) continue
    metrics[serverId] = item
    const server = servers.value.find((row) => row.id === serverId)
    if (server && item.status) server.status = item.status
  }
}

async function loadServers(opts: { refresh?: boolean; liveCascade?: boolean } = {}) {
  const refresh = opts.refresh ?? false
  const liveCascade = opts.liveCascade ?? false

  const [{ data }] = await Promise.all([
    api.get<ServerListItem[]>('/servers'),
    loadCascadeLinks(liveCascade),
    loadHealth()
  ])
  servers.value = data

  for (const server of data) {
    metricsLoading[server.id] = true
  }

  try {
    const { data: batch } = await api.get<ServerMetrics[]>('/servers/metrics', {
      params: { refresh }
    })
    applyMetricsBatch(batch)
  } catch {
    await Promise.all(data.map((server) => loadMetrics(server.id, refresh)))
  } finally {
    for (const server of data) {
      metricsLoading[server.id] = false
    }
  }
}

async function loadMetrics(serverId: string, refresh = false) {
  metricsLoading[serverId] = true
  try {
    const { data } = await api.get<ServerMetrics & { server_id: string; status: string }>(
      `/servers/${serverId}/metrics`,
      { params: { refresh } }
    )
    metrics[serverId] = data
    const server = servers.value.find((item) => item.id === serverId)
    if (server) server.status = data.status
  } catch {
    metrics[serverId] = {
      server_id: serverId,
      online: false,
      cpu_percent: null,
      mem_used_bytes: null,
      mem_total_bytes: null,
      disk_used_bytes: null,
      disk_total_bytes: null,
      uptime_seconds: null,
      active_peers: 0,
      total_traffic_bytes: 0,
      message: 'Не удалось получить метрики'
    }
  } finally {
    metricsLoading[serverId] = false
  }
}

async function refreshAll() {
  refreshing.value = true
  try {
    await loadServers({ refresh: true, liveCascade: true })
  } finally {
    refreshing.value = false
  }
}

function onServerCreated() {
  void loadServers()
}

function openMigrateNode() {
  migrateVisible.value = true
}

function onNodeMigrated() {
  void loadServers({ refresh: true, liveCascade: true })
}

function cascadeRoleFor(serverId: string): CascadePeerRole | undefined {
  return cascadePeerMap.value.get(serverId)
}

function metricFor(serverId: string): ServerMetrics | undefined {
  return metrics[serverId]
}

function confirmDelete(server: ServerListItem) {
  dialog.warning({
    title: 'Удалить сервер из панели?',
    content: `«${server.name}» будет удалён только из панели. На самом VPS ничего не изменится.`,
    positiveText: 'Удалить',
    negativeText: 'Отмена',
    onPositiveClick: async () => {
      try {
        await api.delete(`/servers/${server.id}`)
        message.success('Сервер удалён из панели.')
        await loadServers()
      } catch (error: any) {
        message.error(error?.response?.data?.detail || 'Не удалось удалить сервер.')
        return false
      }
    }
  })
}
</script>

<style scoped>
.servers-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 18px;
}

.head-actions {
  display: flex;
  gap: 10px;
}

h2 {
  margin: 0;
  font-size: 18px;
}

p {
  margin: 4px 0 0;
  color: var(--color-muted);
  font-size: 13px;
}

.server-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 300px), 1fr));
  gap: 14px;
}

.server-grid > * {
  min-width: 0;
}

.cascade-group {
  grid-column: 1 / -1;
  display: grid;
  gap: 12px;
  padding: 14px;
  min-width: 0;
  border-color: var(--color-cascade-border);
  background: var(--color-cascade-bg);
}

.cascade-group--active {
  border-color: var(--color-cascade-border-active);
  background: var(--color-cascade-bg-active);
}

.cascade-group-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 0 4px;
}

.cascade-group-title {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--color-muted);
  font-size: 12px;
  font-weight: 600;
}

.cascade-group-title svg {
  color: var(--color-accent);
}

.cascade-group-body {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 40px minmax(0, 1fr);
  align-items: stretch;
  gap: 10px;
  min-width: 0;
}

.cascade-group-body :deep(.server-card) {
  min-width: 0;
  overflow: hidden;
  border-color: var(--color-border);
  background: var(--color-cascade-card-bg);
}

.cascade-link {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  width: 36px;
  color: var(--color-dim);
  flex-shrink: 0;
}

.cascade-link-line {
  width: 2px;
  flex: 1;
  min-height: 24px;
  border-radius: 1px;
  background: var(--color-border);
}

.cascade-link.live {
  color: var(--color-accent);
}

.cascade-link.live .cascade-link-line {
  background: linear-gradient(180deg, transparent, var(--color-accent), transparent);
}

@media (max-width: 1100px) {
  .cascade-group-body {
    grid-template-columns: 1fr;
    gap: 8px;
  }

  .cascade-link {
    flex-direction: row;
    width: auto;
    height: 32px;
    padding: 0 16px;
  }

  .cascade-link-line {
    width: auto;
    height: 2px;
    min-height: 0;
    flex: 1;
  }

  .cascade-link.live .cascade-link-line {
    background: linear-gradient(90deg, transparent, var(--color-accent), transparent);
  }
}

@media (max-width: 640px) {
  .servers-head {
    flex-direction: column;
    align-items: flex-start;
  }

  .head-actions {
    width: 100%;
  }

  .server-grid {
    grid-template-columns: 1fr;
  }
}
</style>
