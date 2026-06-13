<template>
  <div class="panel block xray-cascade">
    <div class="xc-head">
      <div class="xc-title">
        <h3>Xray-каскад (TCP-relay)</h3>
        <StatusBadge :label="stateLabel" :tone="stateTone" :pulse="status?.live_active" />
      </div>
      <n-button size="small" tertiary :loading="loading" @click="loadStatus">
        <template #icon><RefreshCw :size="15" /></template>
        Обновить
      </n-button>
    </div>

    <p class="xc-hint">
      Клиент → <strong>этот узел (entry)</strong> → exit с Xray-Reality → интернет.
      На entry поднимается прозрачный TCP-relay, маскировка/ключи остаются на exit.
    </p>

    <div v-if="status && status.state !== 'none' && status.state !== 'down'" class="xc-route">
      <div class="xc-node">
        <span class="xc-node-label">Entry</span>
        <span class="mono">{{ entryName }}</span>
        <span class="xc-sub mono">:{{ status.relay_port }}</span>
      </div>
      <ArrowRight :size="16" class="xc-arrow" />
      <div class="xc-node">
        <span class="xc-node-label">Exit</span>
        <span class="mono">{{ status.exit_name || '—' }}</span>
        <span class="xc-sub mono">SNI {{ status.sni || '—' }}</span>
      </div>
    </div>

    <div v-if="status?.message" class="xc-message">{{ status.message }}</div>
    <div v-if="status?.last_healed_at" class="xc-sub xc-heal">
      Авто-восстановление relay включено (последнее: {{ formatTime(status.last_healed_at) }}).
    </div>

    <div class="xc-form">
      <label class="xc-field">
        <span>Exit-сервер (с установленным Xray)</span>
        <n-select
          v-model:value="exitId"
          :options="exitOptions"
          placeholder="Выберите exit"
          :disabled="isActive || !!busy"
        />
      </label>
      <label class="xc-field xc-field--port">
        <span>Relay-порт на entry</span>
        <n-input-number
          v-model:value="relayPort"
          :min="1"
          :max="65535"
          :disabled="isActive || !!busy"
          placeholder="443"
        />
      </label>
    </div>

    <div v-if="exitOptions.length === 0" class="xc-empty">
      Нет серверов с установленным Xray. Сначала установите Xray на exit-узле.
    </div>

    <div v-if="preflight" class="xc-checks">
      <div
        v-for="check in preflight.checks"
        :key="check.id"
        class="xc-check"
        :class="`xc-check--${check.status}`"
      >
        <CheckCircle2 v-if="check.status === 'ok'" :size="15" />
        <AlertTriangle v-else-if="check.status === 'warning'" :size="15" />
        <XCircle v-else :size="15" />
        <span class="xc-check-label">{{ check.label }}</span>
        <span class="xc-check-value mono">{{ check.value }}</span>
      </div>
      <ul v-if="preflight.blockers?.length" class="xc-blockers">
        <li v-for="(b, i) in preflight.blockers" :key="i">{{ b }}</li>
      </ul>
    </div>

    <div class="xc-actions">
      <n-button
        v-if="!isActive"
        secondary
        :loading="busy === 'preflight'"
        :disabled="!exitId || !!busy"
        @click="runPreflight"
      >
        Проверить (preflight)
      </n-button>
      <n-button
        v-if="!isActive"
        type="primary"
        :loading="busy === 'apply'"
        :disabled="!canApply || !!busy"
        @click="runApply"
      >
        Включить relay
      </n-button>
      <n-button
        v-if="isActive"
        type="error"
        secondary
        :loading="busy === 'rollback'"
        :disabled="!!busy"
        @click="runRollback"
      >
        Выключить каскад
      </n-button>
    </div>

    <div v-if="isActive" class="xc-split">
      <div class="xc-split-text">
        <h4>Split-правило: РФ напрямую</h4>
        <span class="xc-sub">
          Российские сайты/IP идут мимо каскада (быстрее), остальное — через exit.
          Применяется к конфигам AmneziaVPN.
        </span>
      </div>
      <n-switch
        :value="splitRu"
        :loading="busy === 'rules'"
        :disabled="!!busy"
        @update:value="toggleSplit"
      />
    </div>

    <div v-if="isActive" class="xc-clients">
      <div class="xc-clients-head">
        <h4>Выдать клиента в каскад</h4>
        <span class="xc-sub">Конфиг указывает на entry, ключи живут на exit.</span>
      </div>
      <div class="xc-clients-form">
        <n-input v-model:value="clientName" placeholder="Имя клиента" :disabled="!!busy" />
        <n-button
          type="primary"
          :loading="busy === 'client'"
          :disabled="!clientName.trim() || !!busy"
          @click="createClient"
        >
          Создать
        </n-button>
      </div>
      <div v-if="lastClientLink" class="xc-client-result">
        <span class="xc-sub">Ссылка VLESS (нажмите, чтобы скопировать):</span>
        <code class="xc-link mono" @click="copyLink">{{ lastClientLink }}</code>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { AlertTriangle, ArrowRight, CheckCircle2, RefreshCw, XCircle } from '@lucide/vue'
import { NButton, NInput, NInputNumber, NSelect, NSwitch, useMessage } from 'naive-ui'
import { computed, onMounted, ref } from 'vue'

import { api } from '@/api/client'
import StatusBadge from '@/components/StatusBadge.vue'

const props = defineProps<{ serverId: string; serverName?: string }>()

const message = useMessage()
const loading = ref(false)
const busy = ref<'' | 'preflight' | 'apply' | 'rollback' | 'client' | 'rules'>('')
const status = ref<any>(null)
const preflight = ref<any>(null)
const exitId = ref<string | null>(null)
const relayPort = ref<number | null>(null)
const servers = ref<any[]>([])
const clientName = ref('')
const lastClientLink = ref('')

const entryName = computed(() => props.serverName || props.serverId.slice(0, 8))

const exitOptions = computed(() =>
  servers.value
    .filter((s) => s.id !== props.serverId && (s.client_protocols || []).includes('xray'))
    .map((s) => ({ label: `${s.name} (${s.host})`, value: s.id })),
)

const isActive = computed(() => status.value?.state === 'active')
const splitRu = computed(() => !!status.value?.split_ru)

const canApply = computed(
  () => preflight.value?.ok && preflight.value?.entry_server_id === props.serverId,
)

const stateLabel = computed(() => {
  const map: Record<string, string> = {
    active: 'Работает',
    down: 'Выключен',
    preflight_ok: 'Проверка пройдена',
    preflight_failed: 'Проверка не пройдена',
    rolled_back: 'Откат',
    none: 'Не настроен',
  }
  return map[status.value?.state || 'none'] || 'Не настроен'
})

const stateTone = computed(() => {
  const s = status.value?.state
  if (s === 'active') return 'ok'
  if (s === 'preflight_failed' || s === 'rolled_back') return 'danger'
  if (s === 'preflight_ok') return 'info'
  return 'neutral'
})

async function loadServers() {
  try {
    const { data } = await api.get('/servers')
    servers.value = data
  } catch {
    servers.value = []
  }
}

async function loadStatus() {
  loading.value = true
  try {
    const { data } = await api.get(`/servers/${props.serverId}/xray-cascade/status`)
    status.value = data
    if (data.exit_server_id && !exitId.value) exitId.value = data.exit_server_id
    if (data.relay_port && !relayPort.value) relayPort.value = data.relay_port
  } catch {
    status.value = null
  } finally {
    loading.value = false
  }
}

async function runPreflight() {
  if (!exitId.value) return
  busy.value = 'preflight'
  preflight.value = null
  try {
    const { data } = await api.post(
      `/servers/${props.serverId}/xray-cascade/preflight`,
      { exit_server_id: exitId.value, relay_port: relayPort.value || undefined },
      { timeout: 120_000 },
    )
    preflight.value = data
    if (data.ok) message.success('Проверка пройдена — можно включать relay.')
    else message.warning('Проверка выявила блокеры.')
    await loadStatus()
  } catch (error: any) {
    message.error(error?.response?.data?.detail || 'Не удалось выполнить проверку.')
  } finally {
    busy.value = ''
  }
}

async function runApply() {
  busy.value = 'apply'
  try {
    await api.post(`/servers/${props.serverId}/xray-cascade/apply`, {}, { timeout: 180_000 })
    message.success('Xray-каскад включён.')
    await loadStatus()
  } catch (error: any) {
    message.error(error?.response?.data?.detail || 'Не удалось включить каскад.')
    await loadStatus()
  } finally {
    busy.value = ''
  }
}

async function runRollback() {
  busy.value = 'rollback'
  try {
    await api.post(`/servers/${props.serverId}/xray-cascade/rollback`, {}, { timeout: 120_000 })
    message.success('Xray-каскад выключен.')
    preflight.value = null
    await loadStatus()
  } catch (error: any) {
    message.error(error?.response?.data?.detail || 'Не удалось выключить каскад.')
  } finally {
    busy.value = ''
  }
}

async function createClient() {
  const name = clientName.value.trim()
  if (!name) return
  busy.value = 'client'
  try {
    const { data } = await api.post(
      `/servers/${props.serverId}/xray-cascade/clients`,
      { name },
      { timeout: 180_000 },
    )
    lastClientLink.value = data.config_text || data.vpn_link || ''
    clientName.value = ''
    message.success('Клиент создан и добавлен в каскад.')
  } catch (error: any) {
    message.error(error?.response?.data?.detail || 'Не удалось создать клиента.')
  } finally {
    busy.value = ''
  }
}

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

async function toggleSplit(value: boolean) {
  busy.value = 'rules'
  try {
    const { data } = await api.put(
      `/servers/${props.serverId}/xray-cascade/rules`,
      { enabled: value },
      { timeout: 180_000 },
    )
    message.success(data.message || 'Split-правило обновлено.')
    await loadStatus()
  } catch (error: any) {
    message.error(error?.response?.data?.detail || 'Не удалось изменить split-правило.')
  } finally {
    busy.value = ''
  }
}

async function copyLink() {
  if (!lastClientLink.value) return
  try {
    await navigator.clipboard.writeText(lastClientLink.value)
    message.success('Скопировано.')
  } catch {
    message.warning('Не удалось скопировать — выделите вручную.')
  }
}

onMounted(async () => {
  await Promise.all([loadServers(), loadStatus()])
})
</script>

<style scoped>
.xray-cascade {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.xc-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.xc-title {
  display: flex;
  align-items: center;
  gap: 10px;
}
.xc-title h3 {
  margin: 0;
}
.xc-hint {
  margin: 0;
  font-size: 13px;
  opacity: 0.8;
  line-height: 1.5;
}
.xc-route {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 12px 14px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.03);
}
.xc-node {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.xc-node-label {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  opacity: 0.6;
}
.xc-sub {
  font-size: 12px;
  opacity: 0.7;
}
.xc-arrow {
  opacity: 0.5;
}
.xc-message {
  font-size: 13px;
  opacity: 0.85;
}
.xc-heal {
  color: #4ade80;
}
.xc-form {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}
.xc-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  flex: 1;
  min-width: 220px;
}
.xc-field--port {
  flex: 0 0 160px;
  min-width: 140px;
}
.xc-field span {
  font-size: 12px;
  opacity: 0.7;
}
.xc-empty {
  font-size: 13px;
  opacity: 0.7;
  padding: 8px 0;
}
.xc-checks {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.xc-check {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}
.xc-check--ok {
  color: #4ade80;
}
.xc-check--warning {
  color: #fbbf24;
}
.xc-check--danger {
  color: #f87171;
}
.xc-check-label {
  flex: 1;
  color: inherit;
  opacity: 0.95;
}
.xc-check-value {
  opacity: 0.75;
  font-size: 12px;
}
.xc-blockers {
  margin: 4px 0 0;
  padding-left: 18px;
  font-size: 12px;
  color: #f87171;
}
.xc-actions {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}
.xc-split {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  padding-top: 12px;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
}
.xc-split-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.xc-split-text h4 {
  margin: 0;
}
.xc-clients {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding-top: 12px;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
}
.xc-clients-head {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.xc-clients-head h4 {
  margin: 0;
}
.xc-clients-form {
  display: flex;
  gap: 10px;
  align-items: center;
}
.xc-client-result {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.xc-link {
  display: block;
  padding: 8px 10px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.05);
  word-break: break-all;
  cursor: pointer;
  font-size: 12px;
}
</style>
