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
          <n-button tertiary circle title="Новая папка" @click="startCreateFolder">
            <template #icon><FolderPlus :size="16" /></template>
          </n-button>
          <n-button type="primary" circle title="Добавить клиента" @click="showAddClient = true">
            <template #icon><Plus :size="16" /></template>
          </n-button>
        </div>
      </div>

      <div v-if="showFolderBar" class="folder-bar">
        <button
          type="button"
          class="folder-chip"
          :class="{ active: folderFilter === 'all' }"
          @click="folderFilter = 'all'"
        >
          Все
          <span>{{ clients.length }}</span>
        </button>
        <button
          type="button"
          class="folder-chip"
          :class="{ active: folderFilter === 'none' }"
          @click="folderFilter = 'none'"
        >
          Без папки
          <span>{{ unfiledCount }}</span>
        </button>
        <template v-for="folder in folders" :key="folder.id">
          <form
            v-if="editingFolderId === folder.id"
            class="folder-create"
            @submit.prevent="saveFolderRename(folder.id)"
          >
            <input
              v-model="folderDraft"
              class="folder-input"
              maxlength="40"
              @keydown.esc="editingFolderId = ''"
            />
            <button type="submit" class="folder-chip active">Ок</button>
          </form>
          <button
            v-else
            type="button"
            class="folder-chip"
            :class="{ active: folderFilter === folder.id }"
            @click="folderFilter = folder.id"
          >
            {{ folder.name }}
            <span>{{ folderCount(folder.id) }}</span>
            <span v-if="folderFilter === folder.id" class="folder-tools" @click.stop>
              <span class="folder-tool" title="Переименовать" @click="startRenameFolder(folder)">✎</span>
              <span class="folder-tool" title="Удалить папку" @click="removeFolder(folder)">✕</span>
            </span>
          </button>
        </template>
        <form v-if="creatingFolder" class="folder-create" @submit.prevent="createFolder">
          <input
            ref="folderNameInput"
            v-model="newFolderName"
            class="folder-input"
            maxlength="40"
            placeholder="Например, друзья"
            @keydown.esc="creatingFolder = false"
          />
          <button type="submit" class="folder-chip active">Создать</button>
        </form>
      </div>

      <div v-if="visibleClients.length" class="client-list" :class="{ 'client-list--paid': hasPaidClients }">
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
          </template>
          <span class="center">Статус</span>
        </div>
        <div v-for="client in visibleClients" :key="client.id" class="client-row">
          <div class="client-identity">
            <RouterLink
              :to="{ name: 'client-detail', params: { id: client.id } }"
              class="row-cell client-cell"
            >
              <span class="entity-avatar entity-avatar--sm">{{ client.name.charAt(0).toUpperCase() }}</span>
              <strong class="client-name">{{ client.name }}</strong>
            </RouterLink>
            <select
              v-if="folders.length"
              class="folder-pick"
              :value="knownFolderId(client)"
              title="Переместить в папку"
              @click.stop
              @change="moveClient(client, $event)"
            >
              <option value="">Без папки</option>
              <option v-for="folder in folders" :key="folder.id" :value="folder.id">{{ folder.name }}</option>
            </select>
          </div>
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
            <CascadePath
              :entry="client.server_name"
              :exit="client.cascade_exit_name"
            />
          </RouterLink>
          <RouterLink
            :to="{ name: 'client-detail', params: { id: client.id } }"
            class="row-cell proto-cell"
          >
            <span class="proto-badge" :class="`proto-${client.protocol || 'awg2'}`">
              {{ protocolBadge(client) }}
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

      <p v-else-if="clients.length" class="folder-empty">В этой папке пока никого. Выберите её в строке клиента.</p>

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
import { Download, ArrowDownUp, FolderPlus, Pencil, Plus, RefreshCw, Upload } from '@lucide/vue'
import { NButton, NSwitch, useMessage } from 'naive-ui'
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { api } from '@/api/client'
import AddClientModal from '@/components/AddClientModal.vue'
import EditClientLimitsModal, { type ClientLimitsSource } from '@/components/EditClientLimitsModal.vue'
import ExportClientsModal from '@/components/ExportClientsModal.vue'
import ImportClientsModal from '@/components/ImportClientsModal.vue'
import CascadePath from '@/components/CascadePath.vue'
import EmptyState from '@/components/EmptyState.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import {
  applyTrafficPatch,
  useClientTrafficPoll,
  type ClientTrafficSnapshot
} from '@/composables/useClientTrafficPoll'
import { onRevisit } from '@/composables/useRevisit'
import AppShell from '@/layouts/AppShell.vue'
import { formatBytes } from '@/utils/format'

defineOptions({ name: 'ClientsView' })

type ClientListItem = {
  id: string
  name: string
  server_id: string
  server_name?: string | null
  cascade_exit_name?: string | null
  cascade_active?: boolean
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
  fallback_client_id?: string | null
  fallback_of_client_id?: string | null
  folder_id?: string | null
}

type ClientFolder = {
  id: string
  name: string
  sort_order: number
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
const folders = ref<ClientFolder[]>([])
const folderFilter = ref<'all' | 'none' | string>('all')
const creatingFolder = ref(false)
const newFolderName = ref('')
const editingFolderId = ref('')
const folderDraft = ref('')
const folderNameInput = ref<HTMLInputElement | null>(null)

const showFolderBar = computed(() => folders.value.length > 0 || creatingFolder.value)

const visibleClients = computed(() =>
  clients.value.filter((client) => {
    const folderId = knownFolderId(client)
    if (folderFilter.value === 'all') return true
    if (folderFilter.value === 'none') return !folderId
    return folderId === folderFilter.value
  })
)

const unfiledCount = computed(() => clients.value.filter((client) => !knownFolderId(client)).length)

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
  void loadFolders()
  void syncTrafficNow()
})

onRevisit(() => {
  void loadClients(false)
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
    await loadFolders()
    await syncTrafficNow()
  } finally {
    loading.value = false
  }
}

function knownFolderId(client: ClientListItem) {
  const id = client.folder_id || ''
  return folders.value.some((folder) => folder.id === id) ? id : ''
}

function folderCount(folderId: string) {
  return clients.value.filter((client) => client.folder_id === folderId).length
}

async function loadFolders() {
  try {
    const { data } = await api.get<ClientFolder[]>('/clients/folders')
    folders.value = data
    if (
      folderFilter.value !== 'all' &&
      folderFilter.value !== 'none' &&
      !data.some((folder) => folder.id === folderFilter.value)
    ) {
      folderFilter.value = 'all'
    }
  } catch {
    folders.value = []
  }
}

async function startCreateFolder() {
  creatingFolder.value = true
  newFolderName.value = ''
  await nextTick()
  folderNameInput.value?.focus()
}

async function createFolder() {
  const name = newFolderName.value.trim()
  if (!name) return
  try {
    const { data } = await api.post<ClientFolder>('/clients/folders', { name })
    folders.value = [...folders.value, data]
    creatingFolder.value = false
    newFolderName.value = ''
    folderFilter.value = data.id
    message.success(`Папка «${data.name}» создана.`)
  } catch (err: any) {
    message.error(err?.response?.data?.detail || 'Не удалось создать папку.')
  }
}

function startRenameFolder(folder: ClientFolder) {
  editingFolderId.value = folder.id
  folderDraft.value = folder.name
}

async function saveFolderRename(folderId: string) {
  const name = folderDraft.value.trim()
  editingFolderId.value = ''
  if (!name) return
  try {
    const { data } = await api.patch<ClientFolder>(`/clients/folders/${folderId}`, { name })
    const idx = folders.value.findIndex((folder) => folder.id === folderId)
    if (idx !== -1) folders.value[idx] = data
  } catch (err: any) {
    message.error(err?.response?.data?.detail || 'Не удалось переименовать папку.')
  }
}

async function removeFolder(folder: ClientFolder) {
  if (!window.confirm(`Удалить папку «${folder.name}»? Клиенты останутся в общем списке.`)) return
  try {
    await api.delete(`/clients/folders/${folder.id}`)
    folders.value = folders.value.filter((item) => item.id !== folder.id)
    for (const client of clients.value) {
      if (client.folder_id === folder.id) client.folder_id = null
    }
    if (folderFilter.value === folder.id) folderFilter.value = 'all'
    message.success('Папка удалена.')
  } catch (err: any) {
    message.error(err?.response?.data?.detail || 'Не удалось удалить папку.')
  }
}

async function moveClient(client: ClientListItem, event: Event) {
  const folderId = (event.target as HTMLSelectElement).value || null
  const prev = client.folder_id || null
  client.folder_id = folderId
  try {
    await api.patch(`/clients/${client.id}/folder`, { folder_id: folderId })
  } catch (err: any) {
    client.folder_id = prev
    message.error(err?.response?.data?.detail || 'Не удалось переместить клиента.')
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

function protocolBadge(client: ClientListItem) {
  if (client.protocol === 'xray') {
    return client.fallback_of_client_id ? 'Xray · запас' : 'Xray'
  }
  if (client.protocol === 'awg31') {
    return client.fallback_client_id ? 'AWG 3.1+R' : 'AWG 3.1'
  }
  return client.fallback_client_id ? 'AWG+R' : 'AWG'
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
  grid-template-columns: minmax(150px, 1.1fr) 72px minmax(160px, 1.3fr) 64px minmax(110px, 1fr) 84px 92px 96px;
  gap: 14px;
  align-items: center;
  padding: 0 18px;
}

.client-list--paid .list-head,
.client-list--paid .client-row {
  grid-template-columns: minmax(140px, 1fr) 72px minmax(150px, 1.2fr) 64px minmax(100px, 0.9fr) 80px 88px 76px 96px;
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

.client-identity {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.client-cell {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.folder-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 12px 18px;
  border-bottom: 1px solid var(--color-border);
}

.folder-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 30px;
  padding: 0 10px;
  border: 1px solid var(--color-border);
  border-radius: 999px;
  background: transparent;
  color: var(--color-muted);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
}

.folder-chip.active {
  color: var(--color-text);
  border-color: color-mix(in srgb, var(--color-accent) 45%, var(--color-border));
  background: var(--color-accent-soft);
}

.folder-chip span {
  color: var(--color-dim);
  font-variant-numeric: tabular-nums;
}

.folder-tools {
  display: inline-flex;
  gap: 2px;
}

.folder-tool {
  padding: 0 3px;
  color: var(--color-muted);
}

.folder-create {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.folder-input {
  width: 160px;
  height: 30px;
  padding: 0 10px;
  border: 1px solid var(--color-border);
  border-radius: 999px;
  background: var(--color-surface);
  color: var(--color-text);
  font-size: 12px;
}

.folder-pick {
  max-width: 160px;
  height: 24px;
  padding: 0 6px;
  border: 1px solid var(--color-border);
  border-radius: 7px;
  background: var(--color-surface);
  color: var(--color-muted);
  font-size: 11px;
}

.folder-empty {
  margin: 0;
  padding: 28px 18px;
  color: var(--color-muted);
  font-size: 13px;
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

.proto-badge.proto-awg31 {
  color: #7ee0c3;
  border-color: rgba(126, 224, 195, 0.35);
  background: rgba(126, 224, 195, 0.08);
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
  min-width: 0;
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

  .client-identity {
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
