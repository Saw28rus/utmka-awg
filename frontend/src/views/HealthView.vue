<template>
  <AppShell title="Здоровье" eyebrow="Мониторинг узлов">
    <div class="health-head">
      <div>
        <h2>{{ summary }}</h2>
        <p>Автопроверка каждые 2 минуты: статус контейнеров и авто-перезапуск упавших.</p>
      </div>
      <n-button tertiary :loading="loading" @click="load">
        <template #icon><RefreshCw :size="16" /></template>
        Обновить
      </n-button>
    </div>

    <div v-if="loading && !nodes.length" class="health-empty">
      <n-spin size="small" />
      <span>Загружаю состояние…</span>
    </div>

    <div v-else-if="!nodes.length" class="health-empty">Узлов пока нет.</div>

    <div v-else class="health-grid">
      <div
        v-for="node in nodes"
        :key="node.server_id"
        class="health-card panel"
        :class="`state-${node.state}`"
      >
        <div class="health-top">
          <div class="health-name">
            <strong>{{ node.server_name }}</strong>
            <span class="mono host">{{ node.host }}</span>
          </div>
          <StatusBadge :label="stateLabel(node.state)" :tone="stateTone(node.state)" :pulse="node.state === 'degraded'" />
        </div>

        <div v-if="Object.keys(node.containers).length" class="containers">
          <div v-for="(status, name) in node.containers" :key="name" class="container-row">
            <component :is="status === 'running' ? CheckCircle2 : XCircle" :size="14" :class="status === 'running' ? 'ok' : 'bad'" />
            <span class="mono cname">{{ name }}</span>
            <span class="cstatus">{{ status }}</span>
          </div>
        </div>

        <ul v-if="node.alerts.length" class="alerts">
          <li v-for="(a, i) in node.alerts" :key="i" :class="`alert-${a.level}`">{{ a.message }}</li>
        </ul>

        <div class="health-foot">
          <span class="checked">{{ node.checked_at ? `проверено ${formatTime(node.checked_at)}` : 'ещё не проверялось' }}</span>
          <n-button size="tiny" tertiary :loading="checking === node.server_id" @click="checkOne(node.server_id)">
            Проверить
          </n-button>
        </div>
      </div>
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import { CheckCircle2, RefreshCw, XCircle } from '@lucide/vue'
import { NButton, NSpin, useMessage } from 'naive-ui'
import { computed, onMounted, ref } from 'vue'

import { api } from '@/api/client'
import StatusBadge from '@/components/StatusBadge.vue'
import AppShell from '@/layouts/AppShell.vue'

type Alert = { level: string; code: string; message: string }
type HealthNode = {
  server_id: string
  server_name: string
  host: string
  state: string
  online: boolean
  checked_at: string | null
  containers: Record<string, string>
  alerts: Alert[]
  restarted: string[]
}

const message = useMessage()
const nodes = ref<HealthNode[]>([])
const loading = ref(false)
const checking = ref('')

const summary = computed(() => {
  if (!nodes.value.length) return 'Здоровье узлов'
  const degraded = nodes.value.filter((n) => n.state === 'degraded').length
  const down = nodes.value.filter((n) => n.state === 'down').length
  if (down) return `${down} недоступно, ${degraded} с проблемами`
  if (degraded) return `${degraded} узлов с проблемами`
  return 'Все узлы в норме'
})

function stateLabel(state: string) {
  return { ok: 'В норме', degraded: 'Проблемы', down: 'Недоступен', unknown: 'Неизвестно' }[state] || 'Неизвестно'
}
function stateTone(state: string): 'ok' | 'warning' | 'danger' | 'neutral' {
  if (state === 'ok') return 'ok'
  if (state === 'degraded') return 'warning'
  if (state === 'down') return 'danger'
  return 'neutral'
}
function formatTime(iso: string) {
  try {
    return new Date(iso).toLocaleTimeString()
  } catch {
    return iso
  }
}

async function load() {
  loading.value = true
  try {
    const { data } = await api.get<HealthNode[]>('/health/nodes')
    nodes.value = data
  } finally {
    loading.value = false
  }
}

async function checkOne(serverId: string) {
  checking.value = serverId
  try {
    await api.post(`/health/nodes/${serverId}/check`, {}, { timeout: 60_000 })
    await load()
    message.success('Проверка выполнена.')
  } catch (error: any) {
    message.error(error?.response?.data?.detail || 'Не удалось проверить узел.')
  } finally {
    checking.value = ''
  }
}

onMounted(load)
</script>

<style scoped>
.health-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 20px;
}
.health-head h2 {
  margin: 0 0 4px;
}
.health-head p {
  margin: 0;
  color: var(--color-muted);
}
.health-empty {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--color-muted);
  padding: 40px 0;
  justify-content: center;
}
.health-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 16px;
}
.health-card {
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  border-left: 3px solid var(--color-border);
}
.state-ok {
  border-left-color: #4ade80;
}
.state-degraded {
  border-left-color: #fbbf24;
}
.state-down {
  border-left-color: #f87171;
}
.health-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
.health-name {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.host {
  font-size: 12px;
  color: var(--color-muted);
}
.containers {
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.container-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}
.container-row .ok {
  color: #4ade80;
}
.container-row .bad {
  color: #f87171;
}
.cname {
  flex: 1;
  min-width: 0;
}
.cstatus {
  color: var(--color-muted);
  font-size: 12px;
}
.alerts {
  margin: 0;
  padding-left: 16px;
  font-size: 12px;
}
.alert-danger {
  color: #f87171;
}
.alert-warning {
  color: #fbbf24;
}
.alert-info {
  color: var(--color-muted);
}
.health-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-top: 1px solid var(--color-border);
  padding-top: 10px;
}
.checked {
  font-size: 12px;
  color: var(--color-muted);
}
</style>
