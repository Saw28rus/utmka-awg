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
          <label class="field">
            <span>Сервер</span>
            <select v-model="form.server_id">
              <option v-for="server in eligibleServers" :key="server.id" :value="server.id">
                {{ server.name }} ({{ server.host }})
              </option>
            </select>
          </label>
          <label class="field">
            <span>Протокол</span>
            <select v-model="form.protocol">
              <option v-for="proto in availableProtocols" :key="proto.id" :value="proto.id">
                {{ proto.label }}
              </option>
            </select>
          </label>
          <label class="field field-wide">
            <span>Формат подключения</span>
            <select v-model="form.format">
              <option v-for="opt in formatOptions" :key="opt.value" :value="opt.value">
                {{ opt.label }}
              </option>
            </select>
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
              <option value="paid">Платный (самооплата в чате)</option>
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

type ServerListItem = {
  id: string
  name: string
  host: string
  awg2_imported: boolean
  protocols: string[]
  client_protocols?: string[]
}

const PROTOCOL_LABELS: Record<string, string> = {
  awg2: 'AmneziaWG 2.0',
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
  trafficLimitGb: '',
  expiresAt: '',
  billingMode: 'free',
  billingAmountRub: '',
  billingPeriodMonths: 1
})

const visible = computed({
  get: () => props.show,
  set: (value: boolean) => emit('update:show', value)
})

const eligibleServers = computed(() =>
  servers.value.filter((server) => (server.client_protocols?.length || (server.awg2_imported ? 1 : 0)) > 0)
)

const selectedServer = computed(() => servers.value.find((s) => s.id === form.server_id))

const availableProtocols = computed(() => {
  const ids = selectedServer.value?.client_protocols?.length
    ? selectedServer.value.client_protocols
    : selectedServer.value?.awg2_imported
      ? ['awg2']
      : []
  return ids.map((id) => ({ id, label: PROTOCOL_LABELS[id] || id }))
})

const formatOptions = computed(() => {
  if (form.protocol === 'xray') {
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

const savingText = computed(() =>
  form.protocol === 'xray'
    ? 'Добавляю клиента в Xray и генерирую конфиг…'
    : 'Генерирую ключи и добавляю peer на сервере…'
)

watch(visible, async (open) => {
  if (open) {
    error.value = ''
    form.name = ''
    form.protocol = 'awg2'
    form.format = 'both'
    form.trafficLimitGb = ''
    form.expiresAt = ''
    form.billingMode = 'free'
    form.billingAmountRub = ''
    form.billingPeriodMonths = 1
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
    if (form.protocol === 'xray' && form.format === 'awg') form.format = 'both'
    if (form.protocol === 'awg2' && form.format === 'config') form.format = 'both'
  }
)

function syncProtocolForServer() {
  const protos = availableProtocols.value
  if (!protos.length) return
  if (!protos.some((p) => p.id === form.protocol)) {
    form.protocol = protos[0].id
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
      traffic_limit_bytes: trafficLimitBytes,
      expires_at: form.expiresAt || null,
      billing_mode: form.billingMode,
      billing_amount_kopecks: billingAmountKopecks,
      billing_period_months: form.billingPeriodMonths
    })
    message.success('Клиент создан. Конфиг и QR готовы.')
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
  width: min(640px, calc(100vw - 32px));
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
