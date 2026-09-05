<template>
  <n-modal
    v-model:show="visible"
    :mask-closable="!saving"
    class="client-modal"
    role="dialog"
    aria-modal="true"
    aria-labelledby="add-client-title"
  >
    <div class="wizard">
      <header class="wizard-head">
        <div>
          <p>Новый клиент</p>
          <h2 id="add-client-title">Создание конфигурации</h2>
        </div>
      </header>

      <div class="wizard-body">
        <n-alert v-if="error" type="error" :show-icon="false">{{ error }}</n-alert>

        <n-alert v-if="!eligibleServers.length" type="warning" :show-icon="false">
          Нет серверов с установленными протоколами. Подключи AWG2 (import) или установи Xray на странице сервера.
        </n-alert>

        <form v-else class="form-grid" @submit.prevent="submit">
          <label class="field field-wide">
            <span>Имя клиента</span>
            <input v-model="form.name" autocomplete="off" placeholder="Например, iPhone Ивана" />
          </label>
          <label class="field field-wide">
            <span>Маршрут</span>
            <div class="server-picker" role="listbox" aria-label="Маршрут">
              <button
                v-for="server in eligibleServers"
                :key="server.id"
                type="button"
                class="server-pick"
                :class="[
                  `server-pick--${pickerKind(server)}`,
                  { selected: form.server_id === server.id }
                ]"
                role="option"
                :aria-selected="form.server_id === server.id"
                @click="form.server_id = server.id"
              >
                <span class="pick-path">
                  <template v-if="pickerKind(server) === 'entry'">
                    <span class="hop hop-entry">{{ server.name }}</span>
                    <span class="pick-arrow" aria-hidden="true">→</span>
                    <span class="hop hop-exit">{{ cascadeExitName(server) }}</span>
                  </template>
                  <span v-else class="hop">{{ server.name }}</span>
                </span>
                <span class="pick-badge">{{ pickerBadge(server) }}</span>
                <span class="pick-meta">{{ pickerMeta(server) }}</span>
              </button>
            </div>
          </label>
          <label class="field">
            <span>Протокол</span>
            <select v-model="form.protocol">
              <option v-for="proto in availableProtocols" :key="proto.id" :value="proto.id">
                {{ proto.label }}
              </option>
            </select>
          </label>
          <p v-if="awg31ElsewhereHint" class="field-wide hint-text">{{ awg31ElsewhereHint }}</p>
          <label class="field">
            <span>Формат подключения</span>
            <select v-model="form.format">
              <option v-for="opt in formatOptions" :key="opt.value" :value="opt.value">
                {{ opt.label }}
              </option>
            </select>
          </label>
          <p v-if="cascadeHint" class="field-wide hint-text hint-cascade">{{ cascadeHint }}</p>
          <p v-if="form.protocol === 'awg31'" class="field-wide hint-text">
            AmneziaWG 3.1: нужен Amnezia VPN 5.0.1.5 или новее. Ставь ключ через вкладку
            AmneziaVPN (vpn://), не .conf и не приложение AmneziaWG. В файрволе VPS открой
            UDP-порт 3.1 (он другой, чем у 2.0).
          </p>
          <label v-if="showRealityFallback" class="field field-wide check-row">
            <input v-model="form.withRealityFallback" type="checkbox" />
            <span>
              Сразу выдать запасной Reality (TCP/443)
              <span class="hint-text">
                Второй ключ на тот же сервер. Если UDP режут — клиент подключается этим ключом.
                В списке появится «{{ form.name || 'имя' }} · Reality».
              </span>
            </span>
          </label>
          <label v-if="isXrayLike" class="field">
            <span>Отпечаток TLS (fingerprint)</span>
            <select v-model="form.fingerprint">
              <option v-for="opt in fingerprintOptions" :key="opt.value" :value="opt.value">
                {{ opt.label }}
              </option>
            </select>
            <span class="hint-text">
              Chrome — оптимально для всех устройств (лучшая маскировка). Совпадение с телефоном не требуется.
            </span>
          </label>
          <label v-if="showEndpointChoice" class="field field-wide">
            <span>Адрес подключения (Endpoint)</span>
            <select v-model="form.endpointHost">
              <option v-for="opt in endpointChoices" :key="opt.value" :value="opt.value">
                {{ opt.label }}
              </option>
            </select>
            <span class="hint-text">
              Домен панели уже указывает на этот сервер, ссылка выглядит аккуратнее. IP — запасной вариант.
            </span>
          </label>
          <label class="field">
            <span>Лимит трафика, ГБ</span>
            <input
              v-model="form.trafficLimitGb"
              inputmode="decimal"
              type="number"
              min="0"
              step="0.5"
              placeholder="Пусто = без лимита"
            />
          </label>
          <label class="field">
            <span>Действует до</span>
            <input v-model="form.expiresAt" type="date" :min="todayStr" />
          </label>
          <label class="field">
            <span>Тариф</span>
            <select v-model="form.billingMode">
              <option value="free">Бесплатный</option>
              <option value="paid">Платный</option>
            </select>
          </label>
          <template v-if="form.billingMode === 'paid'">
            <label class="field">
              <span>Сумма за период, ₽</span>
              <input
                v-model="form.billingAmountRub"
                inputmode="decimal"
                type="number"
                min="1"
                step="1"
                placeholder="Например, 300"
              />
            </label>
            <label class="field">
              <span>Период оплаты</span>
              <select v-model="form.billingPeriodMonths">
                <option :value="1">Раз в месяц</option>
                <option :value="3">Раз в 3 месяца</option>
              </select>
            </label>
            <p class="field-wide hint-text">
              Клиент сможет сам продлить доступ в чате (если подключена ЮKassa): ссылка на 1 день, не чаще 3 раз в месяц.
            </p>
          </template>
        </form>

        <div v-if="saving" class="saving-state">
          <n-spin size="small" />
          <span>{{ savingText }}</span>
        </div>
      </div>

      <footer class="wizard-actions">
        <n-button tertiary :disabled="saving" @click="close">Отмена</n-button>
        <n-button
          type="primary"
          :loading="saving"
          :disabled="!eligibleServers.length"
          @click="submit"
        >
          Создать клиента
        </n-button>
      </footer>
    </div>
  </n-modal>
</template>

<script setup lang="ts">
import { NAlert, NButton, NModal, NSpin, useMessage } from 'naive-ui'
import { computed, reactive, ref, watch } from 'vue'

import { api } from '@/api/client'
import {
  cascadeExitName,
  pickerKind,
  sortServersForClientPicker,
  type CascadeAwareServer
} from '@/utils/cascadePath'

type ServerListItem = CascadeAwareServer & {
  host: string
  awg2_imported: boolean
  protocols: string[]
  client_protocols?: string[]
  endpoint_host?: string | null
  panel_domain?: string | null
}

const PROTOCOL_LABELS: Record<string, string> = {
  awg31: 'AmneziaWG 3.1',
  awg2: 'AmneziaWG 2.0',
  awg_legacy: 'AmneziaWG Legacy',
  xray: 'Xray (VLESS-Reality)'
}

const props = defineProps<{
  show: boolean
}>()

const emit = defineEmits<{
  'update:show': [value: boolean]
  created: [payload: { clientId: string; format: string }]
}>()

const message = useMessage()
const saving = ref(false)
const error = ref('')
const servers = ref<ServerListItem[]>([])

const todayStr = new Date().toISOString().slice(0, 10)

const form = reactive({
  name: '',
  server_id: '',
  protocol: 'awg2',
  format: 'both',
  fingerprint: 'chrome',
  endpointHost: '',
  trafficLimitGb: '',
  expiresAt: '',
  billingMode: 'free',
  billingAmountRub: '',
  billingPeriodMonths: 1,
  withRealityFallback: true
})

const visible = computed({
  get: () => props.show,
  set: (value: boolean) => emit('update:show', value)
})

const eligibleServers = computed(() =>
  sortServersForClientPicker(servers.value.filter((server) => protocolIdsFor(server).length > 0))
)

const selectedServer = computed(() => servers.value.find((s) => s.id === form.server_id))

const availableProtocols = computed(() => {
  const server = selectedServer.value
  const ids = protocolIdsFor(server)
  return ids.map((id) => ({ id, label: PROTOCOL_LABELS[id] || id }))
})

const awg31ElsewhereHint = computed(() => {
  if (availableProtocols.value.some((p) => p.id === 'awg31')) return ''
  const other = eligibleServers.value.find((s) => s.id !== form.server_id && protocolIdsFor(s).includes('awg31'))
  if (!other) return ''
  return `AmneziaWG 3.1 стоит на «${other.name}», не на выбранном сервере. Выбери его выше — иначе ключ будет 2.0.`
})

const isXrayLike = computed(() => form.protocol === 'xray')

const fingerprintOptions = [
  { value: 'chrome', label: 'Chrome (рекомендуется)' },
  { value: 'safari', label: 'Safari' },
  { value: 'ios', label: 'iOS' },
  { value: 'firefox', label: 'Firefox' },
  { value: 'android', label: 'Android' },
  { value: 'edge', label: 'Edge' },
  { value: 'random', label: 'Случайный' }
]

const formatOptions = computed(() => {
  if (isXrayLike.value) {
    return [
      { value: 'both', label: 'Оба (VLESS + AmneziaVPN)' },
      { value: 'config', label: 'VLESS (vless://, сторонние клиенты)' },
      { value: 'vpn', label: 'AmneziaVPN (vpn://, для телефона)' }
    ]
  }
  return [
    { value: 'both', label: 'Оба (AmneziaWG + AmneziaVPN)' },
    { value: 'awg', label: 'AmneziaWG (.conf)' },
    { value: 'vpn', label: 'AmneziaVPN (vpn://)' }
  ]
})

const serverDomain = computed(() => {
  const s = selectedServer.value
  if (!s) return ''
  return (s.endpoint_host || s.panel_domain || '').trim()
})

const showEndpointChoice = computed(() => form.protocol === 'xray' && !!serverDomain.value)

const cascadeHint = computed(() => {
  const s = selectedServer.value
  if (!s) return ''
  const exit = cascadeExitName(s)
  if (form.protocol === 'xray' && s.xray_cascade_active) {
    return `Xray-каскад ${s.name} → ${exit || 'exit'}: обычный Xray-ключ. РФ-трафик выходит здесь, остальное уходит на выход.`
  }
  if ((form.protocol === 'awg2' || form.protocol === 'awg31') && pickerKind(s) === 'entry' && exit) {
    return `Каскад ${s.name} → ${exit}: ключ ставится на вход, интернет выходит через ${exit}.`
  }
  if ((form.protocol === 'awg2' || form.protocol === 'awg31') && pickerKind(s) === 'exit') {
    const entry = s.awg_cascade_peer_name
    return entry
      ? `«${s.name}» — выход каскада. Ключ будет прямым на этот сервер, без входа «${entry}».`
      : `«${s.name}» — выход каскада. Ключ будет прямым на этот сервер.`
  }
  return ''
})

const showRealityFallback = computed(() => {
  if (form.protocol !== 'awg2' && form.protocol !== 'awg31') return false
  const s = selectedServer.value
  if (!s) return false
  const protos = s.client_protocols || s.protocols || []
  return protos.includes('xray') || !!s.xray_cascade_active
})

const endpointChoices = computed(() => {
  const host = selectedServer.value?.host || ''
  const domain = serverDomain.value
  const opts: { value: string; label: string }[] = []
  if (domain) opts.push({ value: domain, label: `Домен — ${domain}` })
  if (host) opts.push({ value: host, label: `IP — ${host}` })
  return opts
})

const savingText = computed(() => {
  if (form.protocol === 'xray') return 'Добавляю клиента в Xray и генерирую конфиг…'
  return 'Генерирую ключи и добавляю peer на сервере…'
})

watch(visible, async (open) => {
  if (open) {
    error.value = ''
    form.name = ''
    form.protocol = 'awg2'
    form.format = 'both'
    form.fingerprint = 'chrome'
    form.endpointHost = ''
    form.trafficLimitGb = ''
    form.expiresAt = ''
    form.billingMode = 'free'
    form.billingAmountRub = ''
    form.billingPeriodMonths = 1
    form.withRealityFallback = true
    await loadServers()
    if (eligibleServers.value.length) {
      form.server_id = eligibleServers.value[0].id
      syncProtocolForServer()
    }
  }
})

watch(
  () => form.server_id,
  () => syncProtocolForServer()
)

watch(
  () => form.protocol,
  () => {
    if (isXrayLike.value && form.format === 'awg') form.format = 'both'
    if ((form.protocol === 'awg2' || form.protocol === 'awg31') && form.format === 'config') form.format = 'both'
    if (form.protocol === 'awg31') form.format = 'vpn'
  }
)

// Дефолт Endpoint для Xray — домен панели. Если выбор недоступен или
// текущее значение выпало из списка (сменили сервер/протокол) — сбрасываем.
watch(
  [showEndpointChoice, serverDomain, () => form.server_id],
  () => {
    if (!showEndpointChoice.value) {
      form.endpointHost = ''
      return
    }
    const valid = endpointChoices.value.some((o) => o.value === form.endpointHost)
    if (!valid) form.endpointHost = serverDomain.value
  },
  { immediate: true }
)

function protocolIdsFor(server?: ServerListItem | null): string[] {
  if (!server) return []
  const ids: string[] = []
  const listed = server.client_protocols?.length
    ? [...server.client_protocols]
    : server.awg2_imported
      ? ['awg2']
      : []
  for (const id of listed) {
    if (!ids.includes(id)) ids.push(id)
  }
  for (const label of server.protocols || []) {
    const mapped =
      label.includes('3.1') ? 'awg31' : label.includes('2.0') ? 'awg2' : label.includes('Xray') ? 'xray' : ''
    if (mapped && !ids.includes(mapped)) ids.push(mapped)
  }
  return ids
}

function serverProtocolSummary(server: ServerListItem): string {
  const ids = protocolIdsFor(server)
  if (!ids.length) return 'нет протокола'
  return ids.map((id) => PROTOCOL_LABELS[id] || id).join(', ')
}

function pickerBadge(server: ServerListItem): string {
  const kind = pickerKind(server)
  if (kind === 'entry') return 'каскад'
  if (kind === 'exit') return 'прямой ключ'
  return 'сервер'
}

function pickerMeta(server: ServerListItem): string {
  const host = server.host || ''
  const protos = serverProtocolSummary(server)
  const kind = pickerKind(server)
  if (kind === 'entry') return `ключ на входе · ${host} · ${protos}`
  if (kind === 'exit') return `выход каскада · ${host} · ${protos}`
  return `${host} · ${protos}`
}

function syncProtocolForServer() {
  const protos = availableProtocols.value
  if (!protos.length) return
  if (!protos.some((p) => p.id === form.protocol)) {
    form.protocol = protos.some((p) => p.id === 'awg31')
      ? 'awg31'
      : protos[0].id
  }
}

async function loadServers() {
  const { useAuthStore } = await import('@/stores/auth')
  const auth = useAuthStore()
  const endpoint = auth.user?.role === 'moderator' ? '/servers/minimal' : '/servers'
  const { data } = await api.get<ServerListItem[]>(endpoint)
  servers.value = data
}

async function submit() {
  if (!form.name.trim()) {
    error.value = 'Укажи имя клиента.'
    return
  }
  if (!form.server_id) {
    error.value = 'Выбери сервер.'
    return
  }
  let billingAmountKopecks: number | null = null
  if (form.billingMode === 'paid') {
    const rub = parseFloat(form.billingAmountRub)
    if (!form.billingAmountRub || Number.isNaN(rub) || rub <= 0) {
      error.value = 'Укажи сумму тарифа.'
      return
    }
    billingAmountKopecks = Math.round(rub * 100)
  }
  saving.value = true
  error.value = ''
  try {
    const limitGb = parseFloat(form.trafficLimitGb)
    const trafficLimitBytes =
      form.trafficLimitGb && !Number.isNaN(limitGb) && limitGb > 0
        ? Math.round(limitGb * 1024 * 1024 * 1024)
        : null
    const { data } = await api.post('/clients', {
      name: form.name.trim(),
      server_id: form.server_id,
      protocol: form.protocol,
      format: form.format,
      fingerprint: isXrayLike.value ? form.fingerprint : null,
      link_host: showEndpointChoice.value ? form.endpointHost || null : null,
      traffic_limit_bytes: trafficLimitBytes,
      expires_at: form.expiresAt || null,
      billing_mode: form.billingMode,
      billing_amount_kopecks: billingAmountKopecks,
      billing_period_months: form.billingPeriodMonths,
      with_reality_fallback: showRealityFallback.value && form.withRealityFallback
    })
    message.success(
      showRealityFallback.value && form.withRealityFallback
        ? 'Клиент создан: AmneziaWG и запасной Reality.'
        : 'Клиент создан. Конфиг и QR готовы.'
    )
    emit('created', { clientId: data.id, format: form.format })
    visible.value = false
  } catch (err: any) {
    error.value = err?.response?.data?.detail || 'Не удалось создать клиента.'
  } finally {
    saving.value = false
  }
}

function close() {
  if (saving.value) return
  visible.value = false
}
</script>

<style scoped>
.client-modal {
  width: min(720px, calc(100vw - 32px));
}

.wizard {
  position: relative;
  z-index: 1;
  overflow: hidden;
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  background: var(--color-surface);
}

.wizard-head,
.wizard-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 16px 18px;
}

.wizard-head {
  border-bottom: 1px solid var(--color-border);
}

.wizard-head p {
  margin: 0;
  color: var(--color-muted);
}

.wizard-head h2 {
  margin: 3px 0 0;
  font-size: 18px;
}

.wizard-body {
  display: grid;
  gap: 16px;
  padding: 18px;
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}

.field {
  display: grid;
  gap: 7px;
  min-width: 0;
  color: var(--color-text);
  font-size: 14px;
  font-weight: 600;
}

.field-wide {
  grid-column: 1 / -1;
}

.hint-text {
  margin: 0;
  color: var(--color-muted);
  font-size: 12.5px;
  font-weight: 400;
}

.hint-cascade {
  padding: 9px 11px;
  border-left: 2px solid var(--color-accent);
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.03);
  color: var(--color-text);
}

.server-picker {
  display: grid;
  gap: 8px;
}

.server-pick {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  grid-template-areas:
    'path badge'
    'meta meta';
  gap: 2px 10px;
  width: 100%;
  padding: 10px 12px;
  border: 1px solid var(--color-border);
  border-radius: 10px;
  background: #101214;
  color: var(--color-text);
  text-align: left;
  cursor: pointer;
  transition:
    border-color 0.16s ease,
    background-color 0.16s ease;
}

.server-pick:hover {
  border-color: var(--color-border-hover);
}

.server-pick.selected {
  border-color: var(--color-accent);
  background: var(--color-accent-soft);
}

.server-pick--entry {
  border-color: var(--color-cascade-border);
  background: var(--color-cascade-bg);
}

.server-pick--entry.selected {
  border-color: var(--color-cascade-border-active);
  background: var(--color-cascade-bg-active);
}

.pick-path {
  grid-area: path;
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  font-size: 14px;
  font-weight: 650;
}

.hop {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.hop-entry {
  color: var(--color-pill-entry-text);
}

.hop-exit {
  color: var(--color-pill-exit-text);
}

.pick-arrow {
  flex-shrink: 0;
  color: var(--color-dim);
  font-weight: 500;
}

.pick-badge {
  grid-area: badge;
  align-self: center;
  padding: 2px 8px;
  border: 1px solid var(--color-border);
  border-radius: 999px;
  color: var(--color-muted);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.02em;
  white-space: nowrap;
}

.server-pick--entry .pick-badge {
  color: var(--color-pill-entry-text);
  border-color: var(--color-pill-entry-border);
  background: var(--color-pill-entry-bg);
}

.server-pick--exit .pick-badge {
  color: var(--color-pill-exit-text);
  border-color: var(--color-pill-exit-border);
  background: var(--color-pill-exit-bg);
}

.pick-meta {
  grid-area: meta;
  color: var(--color-muted);
  font-size: 12px;
  font-weight: 400;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.check-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  font-weight: 500;
}

.check-row input[type='checkbox'] {
  width: 16px;
  height: 16px;
  margin-top: 3px;
  flex-shrink: 0;
}

.field input,
.field select {
  width: 100%;
  min-width: 0;
  height: 36px;
  padding: 0 11px;
  border: 1px solid transparent;
  border-radius: 7px;
  background: #101214;
  color: var(--color-text);
  font: inherit;
  font-weight: 500;
  outline: none;
  transition: border-color 0.16s ease;
}

.field input:focus,
.field select:focus {
  border-color: var(--color-accent);
}

.saving-state {
  display: flex;
  align-items: center;
  gap: 10px;
  color: var(--color-muted);
  font-size: 13px;
}

.wizard-actions {
  border-top: 1px solid var(--color-border);
}

@media (max-width: 560px) {
  .form-grid {
    grid-template-columns: 1fr;
  }
}
</style>
