<template>
  <n-modal :show="show" @update:show="$emit('update:show', $event)">
    <div class="edit-card panel" role="dialog" aria-modal="true">
      <h3>Лимит и срок действия</h3>
      <p class="edit-hint">
        Не меняет ссылку клиента. При превышении лимита или окончании срока peer блокируется на сервере автоматически.
      </p>

      <label class="field">
        <span>Лимит трафика, ГБ</span>
        <input
          v-model="form.trafficLimitGb"
          type="number"
          min="0"
          step="0.5"
          placeholder="Пусто = без лимита"
        />
      </label>
      <label class="field">
        <span>Действует до</span>
        <div class="date-row">
          <input v-model="form.expiresAt" type="date" :min="todayStr" />
          <n-button v-if="form.expiresAt" size="tiny" tertiary @click="form.expiresAt = ''">
            Бессрочно
          </n-button>
        </div>
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
          <input v-model="form.billingAmountRub" type="number" min="1" step="1" placeholder="Например, 300" />
        </label>
        <label class="field">
          <span>Период оплаты</span>
          <select v-model.number="form.billingPeriodMonths">
            <option :value="1">Раз в месяц</option>
            <option :value="3">Раз в 3 месяца</option>
          </select>
        </label>
      </template>
      <div class="edit-actions">
        <n-button tertiary @click="$emit('update:show', false)">Отмена</n-button>
        <n-button type="primary" :loading="saving" @click="save">Сохранить</n-button>
      </div>
    </div>
  </n-modal>
</template>

<script setup lang="ts">
import { NButton, NModal, useMessage } from 'naive-ui'
import { reactive, ref, watch } from 'vue'

import { api } from '@/api/client'

export type ClientLimitsSource = {
  id: string
  traffic_limit_bytes?: number | null
  expires_at?: string | null
  status: string
  billing_mode?: string | null
  billing_amount_kopecks?: number | null
  billing_period_months?: number | null
}

const props = defineProps<{
  show: boolean
  client: ClientLimitsSource | null
}>()

const emit = defineEmits<{
  'update:show': [value: boolean]
  saved: [client: Record<string, unknown>]
}>()

const message = useMessage()
const saving = ref(false)
const todayStr = new Date().toISOString().slice(0, 10)

const form = reactive({
  trafficLimitGb: '',
  expiresAt: '',
  billingMode: 'free',
  billingAmountRub: '',
  billingPeriodMonths: 1
})

watch(
  () => [props.show, props.client?.id] as const,
  ([open]) => {
    if (!open || !props.client) return
    form.trafficLimitGb = props.client.traffic_limit_bytes
      ? String(+(props.client.traffic_limit_bytes / 1024 / 1024 / 1024).toFixed(2))
      : ''
    form.expiresAt = props.client.expires_at ? props.client.expires_at.slice(0, 10) : ''
    form.billingMode = props.client.billing_mode === 'paid' ? 'paid' : 'free'
    form.billingAmountRub = props.client.billing_amount_kopecks
      ? String(+(props.client.billing_amount_kopecks / 100).toFixed(2))
      : ''
    form.billingPeriodMonths = props.client.billing_period_months || 1
  }
)

async function save() {
  if (!props.client) return
  let billingAmountKopecks: number | null = null
  if (form.billingMode === 'paid') {
    const rub = parseFloat(form.billingAmountRub)
    if (!form.billingAmountRub || Number.isNaN(rub) || rub <= 0) {
      message.error('Укажи сумму тарифа.')
      return
    }
    billingAmountKopecks = Math.round(rub * 100)
  }
  saving.value = true
  try {
    const gb = parseFloat(form.trafficLimitGb)
    const trafficLimitBytes =
      form.trafficLimitGb && !Number.isNaN(gb) && gb > 0
        ? Math.round(gb * 1024 * 1024 * 1024)
        : null
    const { data } = await api.patch(`/clients/${props.client.id}`, {
      traffic_limit_bytes: trafficLimitBytes,
      expires_at: form.expiresAt || null,
      billing_mode: form.billingMode,
      billing_amount_kopecks: billingAmountKopecks,
      billing_period_months: form.billingPeriodMonths
    })
    message.success('Сохранено. Изменения применены на сервере.')
    emit('saved', data)
    emit('update:show', false)
  } catch (err: any) {
    message.error(err?.response?.data?.detail || 'Не удалось сохранить.')
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.edit-card {
  width: min(440px, calc(100vw - 32px));
  padding: 22px;
}

.edit-card h3 {
  margin: 0 0 6px;
  font-size: 16px;
}

.edit-hint {
  margin: 0 0 16px;
  color: var(--color-muted);
  font-size: 12.5px;
  line-height: 1.5;
}

.field {
  display: grid;
  gap: 6px;
  margin-bottom: 14px;
}

.field > span {
  color: var(--color-dim);
  font-size: 12.5px;
}

.field input[type='number'],
.field input[type='date'],
.field select {
  width: 100%;
  height: 38px;
  padding: 0 12px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #0d0f10;
  color: var(--color-text);
  font-size: 14px;
}

.date-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.edit-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 8px;
}
</style>
