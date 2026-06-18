<template>
  <AppShell title="Клиенты" eyebrow="VPN-пользователи">
    <div class="panel page-panel">
      <div class="section-head">
        <div>
          <div class="title-row">
            <h2>Все клиенты</h2>
            <span v-if="clients.length" class="count-pill">{{ clients.length }}</span>
          </div>
          <p>Появляются после import существующей Amnezia или создания нового.</p>
        </div>
        <div class="head-actions">
          <div v-if="clients.length" class="total-traffic" title="Суммарный трафик всех клиентов">
            <span class="total-traffic-icon" aria-hidden="true">
              <ArrowDownUp :size="14" />
            </span>
            <span class="total-traffic-value">
              <strong>{{ totalTraffic.value }}</strong>
              <em v-if="totalTraffic.unit">{{ totalTraffic.unit }}</em>
            </span>
          </div>
          <n-button
            tertiary
            circle
            :loading="loading"
            title="Обновить"
            @click="refreshClients"
          >
            <template #icon><RefreshCw :size="16" /></template>
          </n-button>
          <n-button
            v-if="clients.length"
            tertiary
            circle
            title="Экспорт клиентов"
            @click="showExport = true"
          >
            <template #icon><Download :size="16" /></template>
          </n-button>
          <n-button tertiary circle title="Импорт клиентов" @click="showImport = true">
            <template #icon><Upload :size="16" /></template>
          </n-button>
          <n-button type="primary" circle title="Добавить клиента" @click="showAddClient = true">
            <template #icon><Plus :size="16" /></template>
          </n-button>
        </div>
      </div>

      <div v-if="clients.length" class="client-list" :class="{ 'client-list--paid': hasPaidClients }">
        <div class="list-head">
          <span>Клиент</span>
          <span class="actions-head" />
          <span>Сервер</span>
          <span>Протокол</span>
          <span>Трафик</span>
          <span>Создан</span>
          <span>Действует до</span>
          <template v-if="hasPaidClients">
            <span>Сумма</span>
            <span>Дата оплаты</span>
          </template>
          <span class="center">Статус</span>
        </div>
        <div v-for="client in clients" :key="client.id" class="client-row">
          <RouterLink
            :to="{ name: 'client-detail', params: { id: client.id } }"
            class="row-cell client-cell"
          >
            <span class="entity-avatar entity-avatar--sm">{{ client.name.charAt(0).toUpperCase() }}</span>
            <strong class="client-name">{{ client.name }}</strong>
          </RouterLink>
          <div class="row-actions" @click.stop>
            <n-switch
              size="small"
              class="client-switch"
              :value="isClientEnabled(client)"
              :loading="togglingId === client.id"
              :disabled="isToggleLocked(client)"
              :title="toggleTitle(client)"
              @update:value="(enabled) => toggleClient(client, enabled)"
            />
            <button
              class="edit-btn"
              title="Изменить лимит и срок"
              @click="openEdit(client)"
            >
              <Pencil :size="14" />
            </button>
          </div>
          <RouterLink
            :to="{ name: 'client-detail', params: { id: client.id } }"
            class="row-cell server-name"
          >
            {{ client.server_name || '—' }}
          </RouterLink>
          <RouterLink
            :to="{ name: 'client-detail', params: { id: client.id } }"
            class="row-cell proto-cell"
          >
            <span class="proto-badge" :class="`proto-${client.protocol || 'awg2'}`">
              {{ protocolBadge(client.protocol) }}
            </span>
          </RouterLink>
          <RouterLink
            :to="{ name: 'client-detail', params: { id: client.id } }"
            class="row-cell traffic-cell"
            :class="{ live: client.online }"
          >
            {{ trafficText(client) }}
          </RouterLink>
          <RouterLink
            :to="{ name: 'client-detail', params: { id: client.id } }"
            class="row-cell date-cell"
          >
            {{ formatDate(client.created_at) }}
          </RouterLink>
          <RouterLink
            :to="{ name: 'client-detail', params: { id: client.id } }"
            class="row-cell date-cell"
            :class="{ expiring: isExpiringSoon(client) }"
          >
            {{ expiryText(client) }}
          </RouterLink>
          <template v-if="hasPaidClients">
            <RouterLink
              :to="{ name: 'client-detail', params: { id: client.id } }"
              class="row-cell billing-cell"
            >
              {{ billingAmountText(client) }}
            </RouterLink>
            <RouterLink
              :to="{ name: 'client-detail', params: { id: client.id } }"
              class="row-cell date-cell"
            >
              {{ lastPaidText(client) }}
            </RouterLink>
          </template>
          <RouterLink
            :to="{ name: 'client-detail', params: { id: client.id } }"
            class="row-cell status-cell"
          >
            <StatusBadge
              :label="presence(client).label"
              :tone="presence(client).tone"
              :pulse="presence(client).pulse"
            />
          </RouterLink>
        </div>
      </div>

      <EmptyState
        v-else
        title="Клиентов пока нет"
        text="Создай клиента кнопкой выше или подключи сервер с веткой import — панель прочитает peers из awg0.conf."
      />
    </div>

    <AddClientModal v-model:show="showAddClient" @created="onClientCreated" />
    <EditClientLimitsModal
      v-model:show="editVisible"
      :client="editClient"
      @saved="onClientSaved"
    />
    <ExportClientsModal v-model:show="showExport" />
    <ImportClientsModal v-model:show="showImport" @imported="refreshClients" />
  </AppShell>
</template>

<script setup lang="ts">
import { Download, ArrowDownUp, Pencil, Plus, RefreshCw, Upload } from '@lucide/vue'
import { NButton, NSwitch, useMessage } from 'naive-ui'
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { api } from '@/api/client'
import AddClientModal from '@/components/AddClientModal.vue'
import EditClientLimitsModal, { type ClientLimitsSource } from '@/components/EditClientLimitsModal.vue'
import ExportClientsModal from '@/components/ExportClientsModal.vue'
import ImportClientsModal from '@/components/ImportClientsModal.vue'
import EmptyState from '@/components/EmptyState.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import {
  applyTrafficPatch,
  useClientTrafficPoll,
  type ClientTrafficSnapshot
} from '@/composables/useClientTrafficPoll'
import AppShell from '@/layouts/AppShell.vue'
import { formatBytes } from '@/utils/format'

type ClientListItem = {
  id: string
  name: string
  server_id: string
  server_name?: string | null
  protocol?: string
  status: string
  client_ip: string
  imported: boolean
  traffic_used_bytes: number
  traffic_up_bytes: number
  traffic_down_bytes: number
  traffic_limit_bytes?: number | null
  expires_at?: string | null
  created_at?: string | null
  online: boolean
  blocked: boolean
  billing_mode?: string
  billing_amount_kopecks?: number | null
  billing_period_months?: number
  last_paid_at?: string | null
}

const router = useRouter()
const message = useMessage()
const loading = ref(false)
const togglingId = ref<string | null>(null)
const showAddClient = ref(false)
const showExport = ref(false)
const showImport = ref(false)
const editVisible = ref(false)
const editClient = ref<ClientLimitsSource | null>(null)
const clients = ref<ClientListItem[]>([])

useClientTrafficPoll(clients)

const hasPaidClients = computed(() =>
  clients.value.some((c) => c.billing_mode === 'paid' && c.billing_amount_kopecks)
)

const totalTraffic = computed(() => {
  const totalBytes = clients.value.reduce((sum, c) => sum + (c.traffic_used_bytes || 0), 0)
  const raw = formatBytes(totalBytes)
  const match = raw.match(/^([\d.,]+)\s*(.+)$/)
  const unitMap: Record<string, string> = {
    GB: 'Гб',
    MB: 'Мб',
    KB: 'КБ',
    TB: 'Тб',
    PB: 'ПБ',
    B: 'Б'
  }
  if (!match) return { value: raw, unit: '' }
  return { value: match[1], unit: unitMap[match[2]] ?? match[2] }
})

onMounted(() => {
  void loadClients()
  void syncTrafficNow()
})

async function loadClients(showSpinner = false) {
  if (showSpinner) loading.value = true
  try {
    const { data } = await api.get<ClientListItem[]>('/clients')
    clients.value = data
  } finally {
    if (showSpinner) loading.value = false
  }
}

async function syncTrafficNow() {
  try {
    const { data } = await api.post<ClientTrafficSnapshot[]>('/clients/sync-traffic')
    const byId = new Map(data.map((snap) => [snap.id, snap]))
    for (const client of clients.value) {
      const patch = byId.get(client.id)
      if (patch) applyTrafficPatch(client, patch)
    }
  } catch {
    // фоновое обновление трафика
  }
}

async function refreshClients() {
  loading.value = true
  try {
    await loadClients(false)
    await syncTrafficNow()
  } finally {
    loading.value = false
  }
}

function isClientEnabled(client: ClientListItem) {
  return client.status !== 'disabled'
}

function isToggleLocked(client: ClientListItem) {
  return client.status === 'expired' || client.status === 'over_limit'
}

function toggleTitle(client: ClientListItem) {
  if (client.status === 'expired') return 'Срок истёк — продли дату'
  if (client.status === 'over_limit') return 'Лимит исчерпан — увеличь лимит'
  return isClientEnabled(client) ? 'Выключить клиента' : 'Включить клиента'
}

async function toggleClient(client: ClientListItem, enabled: boolean) {
  if (isToggleLocked(client)) return
  togglingId.value = client.id
  try {
    const { data } = await api.patch<ClientListItem>(`/clients/${client.id}`, {
      status: enabled ? 'active' : 'disabled'
    })
    const idx = clients.value.findIndex((c) => c.id === client.id)
    if (idx !== -1) {
      clients.value[idx] = { ...clients.value[idx], ...data }
    }
    message.success(enabled ? 'Клиент включён.' : 'Клиент выключен.')
  } catch (err: any) {
    message.error(err?.response?.data?.detail || 'Не удалось изменить статус.')
  } finally {
    togglingId.value = null
  }
}

function openEdit(client: ClientListItem) {
  editClient.value = client
  editVisible.value = true
}

function onClientSaved(data: Record<string, unknown>) {
  const idx = clients.value.findIndex((c) => c.id === data.id)
  if (idx === -1) return
  const prev = clients.value[idx]
  clients.value[idx] = {
    ...prev,
    traffic_limit_bytes: data.traffic_limit_bytes as number | null | undefined,
    expires_at: data.expires_at as string | null | undefined,
    status: data.status as string,
    blocked: data.blocked as boolean,
    online: data.online as boolean
  }
}

function onClientCreated(payload: { clientId: string; format: string }) {
  router.push({ name: 'client-detail', params: { id: payload.clientId }, query: { format: payload.format } })
}

function protocolBadge(protocol?: string) {
  if (protocol === 'xray') return 'Xray'
  return 'AWG'
}

function trafficText(client: ClientListItem) {
  const used = formatBytes(client.traffic_used_bytes)
  if (client.traffic_limit_bytes) {
    return `${used} / ${formatBytes(client.traffic_limit_bytes)}`
  }
  return used
}

function formatDate(value?: string | null) {
  if (!value) return '—'
  try {
    return new Date(value).toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: '2-digit' })
  } catch {
    return value
  }
}

function expiryText(client: ClientListItem) {
  if (!client.expires_at) return '∞'
  return formatDate(client.expires_at)
}

function isPaidClient(client: ClientListItem) {
  return client.billing_mode === 'paid' && !!client.billing_amount_kopecks
}

function billingAmountText(client: ClientListItem) {
  if (!isPaidClient(client)) return '—'
  const rub = (client.billing_amount_kopecks ?? 0) / 100
  const formatted = rub.toLocaleString('ru-RU', { maximumFractionDigits: 0 })
  const period = client.billing_period_months === 3 ? '/3 мес' : '/мес'
  return `${formatted} ₽${period}`
}

function lastPaidText(client: ClientListItem) {
  if (!isPaidClient(client)) return '—'
  if (!client.last_paid_at) return '—'
  return formatDate(client.last_paid_at)
}

function isExpiringSoon(client: ClientListItem) {
  if (!client.expires_at) return false
  const expires = new Date(client.expires_at).getTime()
  const now = Date.now()
  const week = 7 * 24 * 3600 * 1000
  return expires > now && expires - now < week
}

function presence(client: ClientListItem): {
  label: string
  tone: 'ok' | 'warning' | 'danger' | 'neutral'
  pulse: boolean
} {
  if (client.status === 'expired') return { label: 'истёк', tone: 'danger', pulse: false }
  if (client.status === 'over_limit') return { label: 'лимит', tone: 'warning', pulse: false }
  if (client.status === 'disabled') return { label: 'выключен', tone: 'neutral', pulse: false }
  if (client.online) return { label: 'онлайн', tone: 'ok', pulse: true }
  return { label: 'не в сети', tone: 'neutral', pulse: false }
}
</script>

<style scoped>
.page-panel {
  overflow: hidden;
}

.section-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  padding: 16px 18px;
  border-bottom: 1px solid var(--color-border);
}

.head-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.total-traffic {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 34px;
  padding: 0 12px 0 8px;
  border-radius: 999px;
  border: 1px solid var(--color-border);
  background: linear-gradient(
    135deg,
    var(--color-surface-2),
    color-mix(in srgb, var(--color-accent) 6%, var(--color-surface-2))
  );
  box-shadow: inset 0 1px 0 color-mix(in srgb, var(--color-text) 4%, transparent);
}

.total-traffic-icon {
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  border-radius: 999px;
  background: var(--color-accent-soft);
  color: var(--color-accent);
  flex-shrink: 0;
}

.total-traffic-value {
  display: inline-flex;
  align-items: baseline;
  gap: 4px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.total-traffic-value strong {
  font-size: 15px;
  font-weight: 700;
  line-height: 1;
  color: var(--color-text);
  letter-spacing: -0.02em;
}

.total-traffic-value em {
  font-style: normal;
  font-size: 11px;
  font-weight: 700;
  line-height: 1;
  color: var(--color-accent);
  letter-spacing: 0.02em;
}

.title-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

h2 {
  margin: 0;
  font-size: 16px;
}

p {
  margin: 4px 0 0;
  color: var(--color-muted);
  font-size: 13px;
}

.client-list {
  display: grid;
}

.list-head,
.client-row {
  display: grid;
  grid-template-columns: minmax(160px, 1.2fr) 72px minmax(100px, 0.9fr) 64px minmax(120px, 1fr) 84px 92px 96px;
  gap: 14px;
  align-items: center;
  padding: 0 18px;
}

.client-list--paid .list-head,
.client-list--paid .client-row {
  grid-template-columns: minmax(150px, 1.1fr) 72px minmax(90px, 0.8fr) 64px minmax(110px, 0.9fr) 80px 88px 76px 88px 96px;
}

.list-head {
  min-height: 36px;
  color: var(--color-dim);
  font-size: 12px;
  border-bottom: 1px solid var(--color-border);
}

.client-row {
  min-height: 52px;
  border-bottom: 1px solid var(--color-border);
  transition: background-color 0.14s ease;
}

.row-cell {
  min-width: 0;
  color: inherit;
  text-decoration: none;
}

.client-cell {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.proto-cell {
  display: flex;
  align-items: center;
}

.proto-badge {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  border-radius: 999px;
  border: 1px solid var(--color-border);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.02em;
  white-space: nowrap;
}

.proto-badge.proto-awg2 {
  color: var(--color-accent);
}

.proto-badge.proto-xray {
  color: #8eb4ff;
  border-color: rgba(142, 180, 255, 0.25);
  background: rgba(142, 180, 255, 0.06);
}

.traffic-cell {
  font-size: 12.5px;
  font-variant-numeric: tabular-nums;
  color: var(--color-muted);
  transition: color 0.2s ease;
}

.traffic-cell.live {
  color: var(--color-text);
}

.date-cell {
  font-size: 12.5px;
  color: var(--color-muted);
  font-variant-numeric: tabular-nums;
}

.date-cell.expiring {
  color: var(--color-warning);
}

.billing-cell {
  font-size: 12.5px;
  color: var(--color-muted);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.center {
  text-align: center;
}

.status-cell {
  display: flex;
  justify-content: center;
}

.actions-head {
  /* колонка под переключатель и карандаш */
}

.row-actions {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
}

.client-switch {
  flex-shrink: 0;
}

.edit-btn {
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
    border-color 0.15s ease,
    background-color 0.15s ease;
}

.edit-btn:hover {
  color: var(--color-accent);
  border-color: var(--color-border);
  background: var(--color-surface-2);
}

.client-row:hover {
  background: var(--color-surface-2);
}

.client-row:last-child {
  border-bottom: 0;
}

.client-name {
  font-size: 14px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.server-name {
  color: var(--color-muted);
  font-size: 13px;
}

@media (max-width: 900px) {
  .list-head {
    display: none;
  }

  .client-row {
    grid-template-columns: minmax(0, 1fr) auto;
    grid-template-rows: auto auto;
    gap: 8px 10px;
    padding: 12px 18px;
  }

  .client-cell {
    grid-column: 1;
    grid-row: 1;
  }

  .row-actions {
    grid-column: 2;
    grid-row: 1;
    justify-content: flex-end;
  }

  .server-name,
  .traffic-cell,
  .date-cell,
  .status-cell {
    grid-column: 1 / -1;
  }
}
</style>
