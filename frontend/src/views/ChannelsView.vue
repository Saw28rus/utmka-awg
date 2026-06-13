<template>
  <AppShell title="Каналы" eyebrow="Топология выдачи">
    <div class="channels-head">
      <div>
        <h2>{{ channels.length ? `${channels.length} каналов` : 'Каналы выдачи' }}</h2>
        <p>Как клиенты получают доступ: одиночные узлы (direct) и каскады entry→exit.</p>
      </div>
      <n-button tertiary :loading="loading" @click="load">
        <template #icon><RefreshCw :size="16" /></template>
        Обновить
      </n-button>
    </div>

    <div v-if="loading && !channels.length" class="channels-empty">
      <n-spin size="small" />
      <span>Загружаю каналы…</span>
    </div>

    <div v-else-if="!channels.length" class="channels-empty">
      Каналов пока нет. Установи протокол на сервере или собери каскад.
    </div>

    <div v-else class="channels-grid">
      <div
        v-for="ch in channels"
        :key="ch.id"
        class="channel-card panel"
        :class="ch.kind === 'cascade' ? 'channel-cascade' : 'channel-direct'"
      >
        <div class="channel-top">
          <span class="channel-kind">
            <Network v-if="ch.kind === 'cascade'" :size="14" />
            <Server v-else :size="14" />
            {{ ch.kind === 'cascade' ? 'Каскад' : 'Direct' }}
          </span>
          <StatusBadge :label="protocolLabel(ch.protocol)" tone="info" />
        </div>

        <div v-if="ch.kind === 'cascade'" class="channel-path">
          <div class="node">
            <span class="node-label">entry</span>
            <strong>{{ ch.entry_name }}</strong>
            <span class="node-host">{{ ch.entry_host || '—' }}</span>
          </div>
          <ArrowRight :size="16" class="path-arrow" />
          <div class="node">
            <span class="node-label">exit</span>
            <strong>{{ ch.exit_name }}</strong>
            <span class="node-host">{{ ch.exit_host || '—' }}</span>
          </div>
        </div>
        <div v-else class="channel-path single">
          <div class="node">
            <strong>{{ ch.server_name }}</strong>
            <span class="node-host">{{ ch.host || '—' }}</span>
          </div>
        </div>

        <div v-if="ch.kind === 'cascade' && ch.transit_subnet" class="channel-transit">
          транзит {{ ch.transit_subnet }} · UDP {{ ch.transit_port }} · слот {{ ch.transit_slot }}
        </div>

        <div class="channel-foot">
          <StatusBadge
            v-if="ch.kind === 'cascade'"
            :label="labelCascadeState(ch.state)"
            :tone="toneCascadeState(ch.state)"
          />
          <StatusBadge v-else :label="stateLabel(ch.state)" :tone="ch.state === 'online' ? 'ok' : 'neutral'" />
          <span class="channel-clients">
            <KeyRound :size="14" />
            {{ ch.clients }} {{ pluralClients(ch.clients) }}
          </span>
        </div>
      </div>
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import { ArrowRight, KeyRound, Network, RefreshCw, Server } from '@lucide/vue'
import { onMounted, ref } from 'vue'

import { api } from '@/api/client'
import StatusBadge from '@/components/StatusBadge.vue'
import AppShell from '@/layouts/AppShell.vue'
import { labelCascadeState, toneCascadeState } from '@/utils/cascadeLabels'

type Channel = {
  id: string
  kind: 'direct' | 'cascade'
  protocol: string
  state: string
  clients: number
  server_name?: string
  host?: string | null
  entry_name?: string
  entry_host?: string | null
  exit_name?: string
  exit_host?: string | null
  transit_slot?: number
  transit_subnet?: string
  transit_port?: number
}

const channels = ref<Channel[]>([])
const loading = ref(false)

function protocolLabel(proto: string) {
  if (proto === 'xray') return 'Xray (Reality)'
  if (proto.startsWith('awg')) return 'AmneziaWG 2.0'
  return proto
}

function stateLabel(state: string) {
  if (state === 'online') return 'Онлайн'
  if (state === 'offline') return 'Офлайн'
  return 'Неизвестно'
}

function pluralClients(n: number) {
  const mod10 = n % 10
  const mod100 = n % 100
  if (mod10 === 1 && mod100 !== 11) return 'клиент'
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return 'клиента'
  return 'клиентов'
}

async function load() {
  loading.value = true
  try {
    const { data } = await api.get<Channel[]>('/channels')
    channels.value = data
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.channels-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 20px;
}

.channels-head h2 {
  margin: 0 0 4px;
}

.channels-head p {
  margin: 0;
  color: var(--color-muted);
}

.channels-empty {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--color-muted);
  padding: 40px 0;
  justify-content: center;
}

.channels-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 16px;
}

.channel-card {
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.channel-cascade {
  border-left: 3px solid var(--color-accent);
}

.channel-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.channel-kind {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  font-size: 14px;
}

.channel-path {
  display: flex;
  align-items: center;
  gap: 10px;
}

.channel-path.single {
  justify-content: flex-start;
}

.node {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
  flex: 1;
}

.node-label {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--color-muted);
}

.node-host {
  font-size: 12px;
  color: var(--color-muted);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}

.path-arrow {
  color: var(--color-accent);
  flex-shrink: 0;
}

.channel-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-top: 1px solid var(--color-border);
  padding-top: 10px;
}

.channel-clients {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--color-muted);
}

.channel-transit {
  font-size: 12px;
  color: var(--color-muted);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
</style>
