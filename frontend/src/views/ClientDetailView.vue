<template>
  <AppShell title="Клиент" eyebrow="QR и конфиг">
    <div v-if="loading" class="panel placeholder">
      <n-spin size="small" />
      <span>Загружаю клиента…</span>
    </div>

    <template v-else-if="client">
      <div class="detail-head">
        <n-button tertiary @click="router.back()">
          <template #icon><ArrowLeft :size="16" /></template>
          Назад
        </n-button>
        <button class="delete-btn" @click="confirmDelete">
          <Trash2 :size="15" />
          Удалить
        </button>
      </div>

      <div class="detail-grid">
        <div class="panel info-panel">
          <div class="client-id">
            <div class="client-id-left">
              <span class="entity-avatar entity-avatar--xl">{{ client.name.charAt(0).toUpperCase() }}</span>
              <div class="client-id-text">
                <h2>{{ client.name }}</h2>
                <div class="badges">
                  <span class="proto-badge" :class="`proto-${client.protocol || 'awg2'}`">
                    {{ protocolLabel }}
                  </span>
                  <StatusBadge
                    :label="presence.label"
                    :tone="presence.tone"
                    :pulse="presence.pulse"
                  />
                  <StatusBadge v-if="client.blocked" label="заблокирован на сервере" tone="danger" />
                </div>
              </div>
            </div>
            <div class="client-id-actions">
              <n-switch
                size="small"
                :value="isEnabled"
                :loading="toggling"
                :disabled="toggleLocked"
                :title="powerHint"
                @update:value="toggleClient"
              />
              <n-button circle tertiary class="edit-btn" title="Изменить лимит и срок" @click="openEdit">
                <template #icon><Pencil :size="15" /></template>
              </n-button>
            </div>
          </div>

          <dl class="kv">
            <div>
              <dt>Сервер</dt>
              <dd>{{ client.server_name || '—' }}</dd>
            </div>
            <div v-if="client.protocol !== 'xray'">
              <dt>IP в сети</dt>
              <dd class="mono">{{ client.client_ip }}</dd>
            </div>
            <div v-else>
              <dt>UUID</dt>
              <dd class="mono key">{{ client.public_key || '—' }}</dd>
            </div>
            <div>
              <dt>Трафик</dt>
              <dd :class="{ live: client.online }">{{ trafficText }}</dd>
            </div>
            <div>
              <dt>Действует до</dt>
              <dd :class="{ warn: expiringSoon }">{{ expiryText }}</dd>
            </div>
            <div>
              <dt>{{ isXray ? 'Последняя активность' : 'Last handshake' }}</dt>
              <dd>{{ handshakeText }}</dd>
            </div>
            <div>
              <dt>Endpoint</dt>
              <dd class="mono">{{ client.endpoint || '—' }}</dd>
            </div>
            <div v-if="showKeepalive">
              <dt>Keepalive</dt>
              <dd class="keepalive-cell">
                <n-select
                  size="small"
                  :value="client.keepalive ?? 25"
                  :options="keepaliveOptions"
                  :loading="savingKeepalive"
                  :disabled="savingKeepalive"
                  style="max-width: 200px"
                  @update:value="changeKeepalive"
                />
              </dd>
            </div>
            <div>
              <dt>Создан</dt>
              <dd>{{ formatDateTime(client.created_at) }}</dd>
            </div>
            <div v-if="client.protocol !== 'xray'">
              <dt>Public key</dt>
              <dd class="mono key">{{ client.public_key || '—' }}</dd>
            </div>
          </dl>
        </div>

        <div class="panel share-panel">
          <div v-if="hasShare" class="format-tabs">
            <button
              v-if="client.config_text"
              class="tab"
              :class="{ active: activeFormat === 'config' }"
              @click="activeFormat = 'config'"
            >
              {{ configTabLabel }}
            </button>
            <button
              v-if="client.vpn_link"
              class="tab"
              :class="{ active: activeFormat === 'vpn' }"
              @click="activeFormat = 'vpn'"
            >
              {{ vpnTabLabel }}
            </button>
          </div>

          <template v-if="hasShare">
            <img v-if="currentQr" class="qr" :src="currentQr" :alt="`QR ${activeFormat}`" />
            <p class="qr-hint">{{ formatHint }}</p>
            <div class="share-actions">
              <n-button size="small" tertiary @click="copyShare">
                <template #icon><Copy :size="15" /></template>
                Копировать
              </n-button>
              <n-button size="small" tertiary @click="downloadShare">
                <template #icon><Download :size="15" /></template>
                {{ downloadLabel }}
              </n-button>
            </div>
          </template>

          <div v-else class="no-key">
            <KeyRound :size="22" />
            <h3>Ключ на устройстве</h3>
            <p>Клиент импортирован без приватного ключа. QR и конфиг недоступны — экспортируй из приложения клиента.</p>
          </div>
        </div>
      </div>

      <div v-if="currentText" class="panel config-panel">
        <div class="config-head">
          <h3>{{ configPanelTitle }}</h3>
        </div>
        <pre class="config-text">{{ currentText }}</pre>
      </div>
    </template>

    <div v-else class="panel placeholder">
      <StatusBadge label="Нет данных" />
      <h2>Клиент не найден</h2>
      <p>Возможно, он был удалён.</p>
    </div>

    <EditClientLimitsModal
      v-model:show="editVisible"
      :client="client"
      @saved="onClientSaved"
    />
  </AppShell>
</template>

<script setup lang="ts">
import { ArrowLeft, Copy, Download, KeyRound, Pencil, Trash2 } from '@lucide/vue'
import { NButton, NSelect, NSpin, NSwitch, useDialog, useMessage } from 'naive-ui'
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { api } from '@/api/client'
import EditClientLimitsModal from '@/components/EditClientLimitsModal.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { useClientTrafficPoll } from '@/composables/useClientTrafficPoll'
import AppShell from '@/layouts/AppShell.vue'
import { copyToClipboard } from '@/utils/clipboard'
import { formatBytes } from '@/utils/format'

type ClientDetail = {
  id: string
  name: string
  server_id: string
  server_name?: string | null
  protocol?: string
  status: string
  client_ip: string
  imported: boolean
  public_key?: string | null
  created_at?: string | null
  config_text?: string | null
  vpn_link?: string | null
  qr_awg?: string | null
  qr_vpn?: string | null
  endpoint?: string | null
  has_private_key: boolean
  traffic_used_bytes: number
  traffic_limit_bytes?: number | null
  expires_at?: string | null
  last_handshake_at?: string | null
  online: boolean
  blocked: boolean
  keepalive?: number
}

const keepaliveOptions = [
  { label: '25 сек — стандарт (надёжно за NAT)', value: 25 },
  { label: '15 сек — частый NAT/мобильный', value: 15 },
  { label: '0 — выключен (тише, экономит батарею)', value: 0 }
]

const route = useRoute()
const router = useRouter()
const dialog = useDialog()
const message = useMessage()

const loading = ref(true)
const toggling = ref(false)
const savingKeepalive = ref(false)
const client = ref<ClientDetail | null>(null)
const activeFormat = ref<'config' | 'vpn'>('config')
const editVisible = ref(false)

const showKeepalive = computed(
  () => client.value?.protocol !== 'xray' && client.value?.has_private_key === true
)

useClientTrafficPoll(client)

const isEnabled = computed(() => client.value?.status !== 'disabled')

const toggleLocked = computed(() => {
  const status = client.value?.status
  return status === 'expired' || status === 'over_limit'
})

const powerHint = computed(() => {
  const c = client.value
  if (!c) return ''
  if (c.status === 'expired') return 'Срок действия истёк'
  if (c.status === 'over_limit') return 'Превышен лимит трафика'
  if (c.status === 'disabled') return 'Peer снят с сервера'
  return 'Клиент активен на сервере'
})

const presence = computed<{
  label: string
  tone: 'ok' | 'warning' | 'danger' | 'neutral'
  pulse: boolean
}>(() => {
  const c = client.value
  if (!c) return { label: '—', tone: 'neutral', pulse: false }
  if (c.status === 'expired') return { label: 'истёк', tone: 'danger', pulse: false }
  if (c.status === 'over_limit') return { label: 'лимит исчерпан', tone: 'warning', pulse: false }
  if (c.status === 'disabled') return { label: 'выключен', tone: 'neutral', pulse: false }
  if (c.online) return { label: 'онлайн', tone: 'ok', pulse: true }
  return { label: 'не в сети', tone: 'neutral', pulse: false }
})

function openEdit() {
  if (!client.value) return
  editVisible.value = true
}

async function toggleClient(enabled: boolean) {
  const c = client.value
  if (!c || toggleLocked.value) return
  toggling.value = true
  try {
    const { data } = await api.patch<ClientDetail>(`/clients/${c.id}`, {
      status: enabled ? 'active' : 'disabled'
    })
    client.value = { ...c, ...data }
    message.success(enabled ? 'Клиент включён.' : 'Клиент выключен.')
  } catch (err: any) {
    message.error(err?.response?.data?.detail || 'Не удалось изменить статус.')
  } finally {
    toggling.value = false
  }
}

function onClientSaved(data: Record<string, unknown>) {
  if (!client.value || client.value.id !== data.id) return
  client.value = { ...client.value, ...(data as ClientDetail) }
}

async function changeKeepalive(value: number) {
  const c = client.value
  if (!c || value === c.keepalive) return
  savingKeepalive.value = true
  try {
    const { data } = await api.post<ClientDetail>(`/clients/${c.id}/keepalive`, {
      keepalive: value
    })
    client.value = { ...c, ...data }
    message.success('Keepalive обновлён. Раздайте новый конфиг клиенту.')
  } catch (err: any) {
    message.error(err?.response?.data?.detail || 'Не удалось изменить keepalive.')
  } finally {
    savingKeepalive.value = false
  }
}

onMounted(load)

async function load() {
  loading.value = true
  try {
    const { data } = await api.get<ClientDetail>(`/clients/${route.params.id}`)
    client.value = data
    const requested = route.query.format
    const wantVpn = requested === 'vpn' || requested === 'both'
    if ((wantVpn || !data.config_text) && data.vpn_link) {
      activeFormat.value = 'vpn'
    } else {
      activeFormat.value = 'config'
    }
  } catch {
    client.value = null
  } finally {
    loading.value = false
  }
}

const hasShare = computed(() => Boolean(client.value?.config_text || client.value?.vpn_link))

const isXray = computed(() => client.value?.protocol === 'xray')

const protocolLabel = computed(() => (isXray.value ? 'Xray' : 'AWG'))

const configTabLabel = computed(() => (isXray.value ? 'VLESS' : 'AmneziaWG'))

const vpnTabLabel = computed(() => (isXray.value ? 'VPN-ключ' : 'AmneziaVPN'))

const configPanelTitle = computed(() =>
  activeFormat.value === 'vpn' ? 'VPN-ключ' : isXray.value ? 'Конфиг VLESS' : 'Конфигурация AmneziaWG'
)

const downloadLabel = computed(() => {
  if (activeFormat.value === 'vpn') return 'Скачать .vpn'
  return isXray.value ? 'Скачать .txt' : 'Скачать .conf'
})

const currentQr = computed(() =>
  activeFormat.value === 'config' ? client.value?.qr_awg : client.value?.qr_vpn
)

const currentText = computed(() =>
  activeFormat.value === 'config' ? client.value?.config_text : client.value?.vpn_link
)

const formatHint = computed(() => {
  if (activeFormat.value === 'vpn') {
    return isXray.value
      ? 'Только для AmneziaVPN: добавь из QR или вставь vpn:// ключ (не путай с vless://).'
      : 'Открой AmneziaVPN → добавить конфигурацию из QR или вставь vpn:// ссылку.'
  }
  return isXray.value
    ? 'vless:// — для v2rayNG, Hiddify и ручного импорта в AmneziaVPN. Для QR в Amnezia используй вкладку VPN-ключ.'
    : 'Открой AmneziaWG → добавить из QR или импортировать .conf.'
})

const trafficText = computed(() => {
  const c = client.value
  if (!c) return '—'
  const used = formatBytes(c.traffic_used_bytes)
  return c.traffic_limit_bytes ? `${used} / ${formatBytes(c.traffic_limit_bytes)}` : `${used} (без лимита)`
})

const expiryText = computed(() => {
  const c = client.value
  if (!c?.expires_at) return 'бессрочно (∞)'
  return formatDateTime(c.expires_at)
})

const expiringSoon = computed(() => {
  const c = client.value
  if (!c?.expires_at) return false
  const expires = new Date(c.expires_at).getTime()
  const now = Date.now()
  return expires > now && expires - now < 7 * 24 * 3600 * 1000
})

const handshakeText = computed(() => {
  const c = client.value
  if (!c?.last_handshake_at) return 'нет данных'
  return formatDateTime(c.last_handshake_at)
})

async function copyShare() {
  const text = currentText.value
  if (!text) return
  const ok = await copyToClipboard(text)
  if (ok) message.success('Скопировано.')
  else message.error('Не удалось скопировать.')
}

function downloadShare() {
  const text = currentText.value
  if (!text || !client.value) return
  const safeName = client.value.name.replace(/[^a-zA-Z0-9_-]+/g, '_') || 'client'
  const ext = activeFormat.value === 'vpn' ? 'vpn' : isXray.value ? 'txt' : 'conf'
  const blob = new Blob([text], { type: 'text/plain' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `${safeName}.${ext}`
  link.click()
  URL.revokeObjectURL(url)
}

function formatDateTime(value?: string | null) {
  if (!value) return '—'
  try {
    return new Date(value).toLocaleString('ru-RU')
  } catch {
    return value
  }
}

async function deleteClient(force: boolean) {
  const url = force
    ? `/clients/${client.value!.id}?force=true`
    : `/clients/${client.value!.id}`
  await api.delete(url)
  message.success(
    force
      ? 'Клиент удалён из панели. Peer/UUID на сервере остался — уберите вручную.'
      : 'Клиент удалён, peer/UUID убран с сервера.'
  )
  router.push({ name: 'clients' })
}

function offerForceDelete() {
  dialog.error({
    title: 'Сервер недоступен',
    content:
      'Не удалось убрать peer/UUID с сервера. Удалить клиента только из панели? ' +
      'Запись на сервере останется, её придётся убрать вручную.',
    positiveText: 'Удалить из панели',
    negativeText: 'Отмена',
    onPositiveClick: async () => {
      try {
        await deleteClient(true)
      } catch (error: any) {
        message.error(error?.response?.data?.detail || 'Не удалось удалить клиента.')
      }
    }
  })
}

function confirmDelete() {
  if (!client.value) return
  dialog.warning({
    title: 'Удалить клиента?',
    content: 'Клиент будет удалён из панели, а его peer/UUID — убран с сервера.',
    positiveText: 'Удалить',
    negativeText: 'Отмена',
    onPositiveClick: async () => {
      try {
        await deleteClient(false)
      } catch (error: any) {
        const detail = error?.response?.data?.detail || 'Не удалось удалить клиента.'
        message.error(detail)
        offerForceDelete()
      }
    }
  })
}
</script>

<style scoped>
.placeholder {
  display: grid;
  gap: 10px;
  place-items: center;
  padding: 32px;
  color: var(--color-muted);
}

.detail-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.delete-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border: 1px solid var(--color-border);
  border-radius: 7px;
  background: transparent;
  color: var(--color-muted);
  cursor: pointer;
  font-size: 13px;
}

.delete-btn:hover {
  color: var(--color-danger);
  border-color: #3a2326;
}

.detail-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 320px;
  gap: 16px;
  margin-bottom: 16px;
}

.info-panel {
  padding: 18px;
}

.client-id {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 18px;
}

.client-id-left {
  display: flex;
  align-items: center;
  gap: 14px;
  min-width: 0;
  flex: 1;
}

.client-id-text {
  min-width: 0;
}

.client-id-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}

.badges {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.proto-badge {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  border-radius: 999px;
  border: 1px solid var(--color-border);
  font-size: 11px;
  font-weight: 600;
}

.proto-badge.proto-awg2 {
  color: var(--color-accent);
}

.proto-badge.proto-xray {
  color: #8eb4ff;
  border-color: rgba(142, 180, 255, 0.25);
  background: rgba(142, 180, 255, 0.06);
}

.edit-btn {
  flex-shrink: 0;
}

dd.live {
  color: var(--color-text);
  font-variant-numeric: tabular-nums;
  transition: color 0.2s ease;
}

.client-id h2 {
  margin: 0 0 6px;
  font-size: 18px;
}

.kv {
  display: grid;
  gap: 0;
  margin: 0;
}

.kv > div {
  display: grid;
  grid-template-columns: 130px minmax(0, 1fr);
  gap: 12px;
  padding: 11px 0;
  border-top: 1px solid var(--color-border);
}

dt {
  color: var(--color-dim);
  font-size: 13px;
}

dd {
  margin: 0;
  min-width: 0;
  overflow-wrap: anywhere;
  font-size: 13px;
}

dd.warn {
  color: var(--color-warning);
}

.key {
  font-size: 12px;
}

.share-panel {
  display: grid;
  place-items: center;
  align-content: start;
  gap: 12px;
  padding: 18px;
  text-align: center;
}

.format-tabs {
  display: inline-flex;
  gap: 4px;
  padding: 3px;
  border: 1px solid var(--color-border);
  border-radius: 9px;
  background: #0d0f10;
}

.tab {
  padding: 6px 14px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--color-muted);
  font-size: 13px;
  cursor: pointer;
  transition:
    background-color 0.15s ease,
    color 0.15s ease;
}

.tab.active {
  background: var(--color-surface-2);
  color: var(--color-text);
}

.qr {
  width: 220px;
  height: 220px;
  border-radius: 10px;
  background: #fff;
  padding: 8px;
}

.qr-hint {
  margin: 0;
  color: var(--color-muted);
  font-size: 12px;
  max-width: 250px;
}

.share-actions {
  display: flex;
  gap: 8px;
}

.no-key {
  display: grid;
  gap: 8px;
  justify-items: center;
  color: var(--color-muted);
  padding: 12px 0;
}

.no-key svg {
  color: var(--color-warning);
}

.no-key h3 {
  margin: 0;
  color: var(--color-text);
  font-size: 15px;
}

.no-key p {
  margin: 0;
  font-size: 13px;
}

.config-panel {
  padding: 18px;
}

.config-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.config-head h3 {
  margin: 0;
  font-size: 15px;
}

.config-text {
  margin: 0;
  padding: 14px;
  border-radius: 8px;
  background: #0d0f10;
  border: 1px solid var(--color-border);
  color: var(--color-text);
  font-family: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12.5px;
  line-height: 1.6;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

@media (max-width: 820px) {
  .detail-grid {
    grid-template-columns: 1fr;
  }

  .client-id {
    flex-direction: column;
    align-items: stretch;
  }

  .client-id-actions {
    justify-content: flex-end;
  }
}
</style>
