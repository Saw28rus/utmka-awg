<template>
  <AppShell title="Карта серверов" eyebrow="Топология entry→exit">
    <div class="map-head">
      <div class="map-totals">
        <span><strong>{{ data?.totals.nodes ?? 0 }}</strong> узлов</span>
        <span><strong>{{ data?.totals.cascades ?? 0 }}</strong> каскадов</span>
        <span><strong>{{ data?.totals.clients ?? 0 }}</strong> клиентов</span>
        <span v-if="data?.totals.degraded" class="t-warn">{{ data.totals.degraded }} с проблемами</span>
        <span v-if="data?.totals.down" class="t-down">{{ data.totals.down }} недоступны</span>
      </div>
      <n-button tertiary :loading="loading" @click="load">
        <template #icon><RefreshCw :size="16" /></template>
        Обновить
      </n-button>
    </div>

    <div v-if="loading && !data" class="map-empty">
      <n-spin size="small" />
      <span>Загружаю карту…</span>
    </div>

    <div v-else-if="!data?.nodes.length" class="map-empty">
      Узлов пока нет. Добавь сервер и установи протокол.
    </div>

    <template v-else>
      <section v-if="data.edges.length" class="map-section">
        <h3>Каскады</h3>
        <div class="lanes">
          <div v-for="edge in data.edges" :key="edge.id" class="lane">
            <div class="lane-node" :class="`h-${nodeHealth(edge.entry_server_id)}`">
              <span class="role-tag">entry</span>
              <strong>{{ nodeName(edge.entry_server_id) }}</strong>
              <span class="node-host">{{ nodeHost(edge.entry_server_id) || '—' }}</span>
            </div>

            <div class="lane-link">
              <span class="proto-badge" :class="`p-${edge.protocol}`">{{ protoLabel(edge.protocol) }}</span>
              <ArrowRight :size="18" :class="`arrow s-${edge.state}`" />
              <span class="lane-meta">
                {{ edge.clients }} кл.
                <template v-if="edge.split_ru"> · split РФ</template>
              </span>
            </div>

            <div class="lane-node" :class="`h-${nodeHealth(edge.exit_server_id)}`">
              <span class="role-tag exit">exit</span>
              <strong>{{ nodeName(edge.exit_server_id) }}</strong>
              <span class="node-host">{{ nodeHost(edge.exit_server_id) || '—' }}</span>
            </div>
          </div>
        </div>
      </section>

      <section class="map-section">
        <h3>Узлы</h3>
        <div class="nodes-grid">
          <div v-for="node in data.nodes" :key="node.id" class="node-card" :class="`h-${node.health}`">
            <div class="node-card-top">
              <span class="node-dot" :class="`h-${node.health}`" />
              <strong>{{ node.name }}</strong>
            </div>
            <span class="node-host">{{ node.host || '—' }}</span>
            <div class="node-roles">
              <span v-for="r in node.roles" :key="r" class="role-chip">{{ roleLabel(r) }}</span>
            </div>
            <div class="node-foot">
              <span class="node-clients"><KeyRound :size="13" /> {{ node.clients }}</span>
              <span class="node-health-label">{{ healthLabel(node.health) }}</span>
            </div>
          </div>
        </div>
      </section>
    </template>
  </AppShell>
</template>

<script setup lang="ts">
import { ArrowRight, KeyRound, RefreshCw } from '@lucide/vue'
import { NButton, NSpin } from 'naive-ui'
import { computed, onMounted, ref } from 'vue'

import { api } from '@/api/client'
import AppShell from '@/layouts/AppShell.vue'

type Node = {
  id: string
  name: string
  host?: string | null
  missing: boolean
  protocols: string[]
  health: 'ok' | 'degraded' | 'down' | 'unknown'
  roles: string[]
  clients: number
}

type Edge = {
  id: string
  protocol: string
  entry_server_id?: string | null
  exit_server_id?: string | null
  state?: string | null
  clients: number
  split_ru?: boolean
}

type MapData = {
  nodes: Node[]
  edges: Edge[]
  totals: { nodes: number; cascades: number; clients: number; degraded: number; down: number }
}

const data = ref<MapData | null>(null)
const loading = ref(false)

const nodeById = computed(() => {
  const m = new Map<string, Node>()
  for (const n of data.value?.nodes ?? []) m.set(n.id, n)
  return m
})

function nodeName(id?: string | null) {
  return (id && nodeById.value.get(id)?.name) || '(удалён)'
}
function nodeHost(id?: string | null) {
  return (id && nodeById.value.get(id)?.host) || null
}
function nodeHealth(id?: string | null) {
  return (id && nodeById.value.get(id)?.health) || 'unknown'
}

function protoLabel(proto: string) {
  if (proto === 'xray') return 'Xray'
  if (proto.startsWith('awg')) return 'AWG2'
  return proto
}

function roleLabel(role: string) {
  if (role === 'entry') return 'entry'
  if (role === 'exit') return 'exit'
  return 'direct'
}

function healthLabel(h: string) {
  if (h === 'ok') return 'OK'
  if (h === 'degraded') return 'Проблемы'
  if (h === 'down') return 'Недоступен'
  return 'Нет данных'
}

async function load() {
  loading.value = true
  try {
    const { data: res } = await api.get<MapData>('/channels/map/topology')
    data.value = res
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.map-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 20px;
}

.map-totals {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  color: var(--color-muted);
  font-size: 14px;
}

.map-totals strong {
  color: var(--color-text);
}

.t-warn {
  color: #f5a623;
}

.t-down {
  color: #e5484d;
}

.map-empty {
  display: flex;
  align-items: center;
  gap: 8px;
  justify-content: center;
  color: var(--color-muted);
  padding: 40px 0;
}

.map-section {
  margin-bottom: 28px;
}

.map-section h3 {
  margin: 0 0 12px;
  font-size: 14px;
  color: var(--color-muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.lanes {
  display: grid;
  gap: 12px;
}

.lane {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(120px, 200px) minmax(0, 1fr);
  align-items: center;
  gap: 14px;
  padding: 14px 16px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 12px;
}

.lane-node {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  padding: 8px 12px;
  border-radius: 9px;
  background: var(--color-surface-2);
  border-left: 3px solid var(--color-border);
}

.lane-node.h-ok {
  border-left-color: #30a46c;
}
.lane-node.h-degraded {
  border-left-color: #f5a623;
}
.lane-node.h-down {
  border-left-color: #e5484d;
}

.role-tag {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--color-dim);
}

.role-tag.exit {
  color: var(--color-accent);
}

.node-host {
  font-size: 12px;
  color: var(--color-muted);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}

.lane-link {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
}

.proto-badge {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--color-surface-2);
  color: var(--color-muted);
}

.proto-badge.p-xray {
  background: rgba(99, 102, 241, 0.16);
  color: #818cf8;
}

.proto-badge.p-awg2 {
  background: rgba(48, 164, 108, 0.16);
  color: #4ade80;
}

.arrow {
  color: var(--color-dim);
}
.arrow.s-active {
  color: #30a46c;
}
.arrow.s-down,
.arrow.s-error {
  color: #e5484d;
}

.lane-meta {
  font-size: 11px;
  color: var(--color-muted);
}

.nodes-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 12px;
}

.node-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 14px;
  border-radius: 12px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
}

.node-card.h-degraded {
  border-color: rgba(245, 166, 35, 0.5);
}
.node-card.h-down {
  border-color: rgba(229, 72, 77, 0.5);
}

.node-card-top {
  display: flex;
  align-items: center;
  gap: 8px;
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

.node-roles {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}

.role-chip {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  padding: 2px 7px;
  border-radius: 6px;
  background: var(--color-surface-2);
  color: var(--color-muted);
}

.node-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-top: 1px solid var(--color-border);
  padding-top: 8px;
  margin-top: 2px;
}

.node-clients {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 13px;
  color: var(--color-muted);
}

.node-health-label {
  font-size: 12px;
  color: var(--color-muted);
}

@media (max-width: 720px) {
  .lane {
    grid-template-columns: 1fr;
    gap: 10px;
  }
  .lane-link {
    flex-direction: row;
    justify-content: center;
  }
}
</style>
