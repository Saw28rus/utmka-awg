<template>
  <n-modal :show="show" @update:show="$emit('update:show', $event)">
    <div class="edit-card panel modal-card" role="dialog" aria-modal="true">
      <header class="edit-head">
        <h3>Лимит и срок действия</h3>
        <p class="edit-hint">
          Ссылку клиента не меняет. При превышении лимита или окончании срока peer блокируется на сервере.
        </p>
      </header>

      <div class="edit-grid">
        <label class="field">
          <span>Лимит трафика, ГБ</span>
          <input
            v-model="form.trafficLimitGb"
            type="number"
            min="0"
            step="0.5"
            placeholder="Без лимита"
          />
        </label>
        <label class="field">
          <span>Действует до</span>
          <div class="date-row">
            <input v-model="form.expiresAt" type="date" :min="todayStr" placeholder="Бессрочно" />
            <button
              v-if="form.expiresAt"
              type="button"
              class="clear-date"
              title="Бессрочно"
              @click="form.expiresAt = ''"
            >
              ∞
            </button>
          </div>
        </label>
      </div>

      <section class="billing-block">
        <div class="billing-head">
          <span class="section-label">Тариф</span>
          <div class="billing-toggle" role="group" aria-label="Тариф">
            <button
              type="button"
              class="billing-opt"
              :class="{ active: form.billingMode === 'free' }"
              @click="form.billingMode = 'free'"
            >
              Бесплатный
            </button>
            <button
              type="button"
              class="billing-opt"
              :class="{ active: form.billingMode === 'paid' }"
              @click="form.billingMode = 'paid'"
            >
              Платный
            </button>
          </div>
        </div>

        <div v-if="form.billingMode === 'paid'" class="edit-grid billing-grid">
          <label class="field">
            <span>Сумма, ₽</span>
            <input v-model="form.billingAmountRub" type="number" min="1" step="1" placeholder="300" />
          </label>
          <label class="field">
            <span>Период</span>
            <select v-model.number="form.billingPeriodMonths">
              <option :value="1">Раз в месяц</option>
              <option :value="3">Раз в 3 месяца</option>
            </select>
          </label>
        </div>
      </section>

      <div class="modal-actions edit-actions">
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
  width: min(480px, calc(100vw - 32px));
}

.edit-head {
  margin-bottom: 2px;
}

.edit-head h3 {
  margin: 0;
}

.edit-hint {
  margin: 0;
}

.edit-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.billing-block {
  padding: 12px;
  border-radius: var(--radius);
  border: 1px solid var(--color-border);
  background: var(--color-surface-2);
}

.billing-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.section-label {
  color: var(--color-dim);
  font-size: 12px;
  font-weight: 600;
}

.billing-toggle {
  display: inline-flex;
  padding: 3px;
  border-radius: 999px;
  border: 1px solid var(--color-border);
  background: var(--color-surface);
}

.billing-opt {
  padding: 5px 12px;
  border: 0;
  border-radius: 999px;
  background: transparent;
  color: var(--color-muted);
  font-size: 12.5px;
  font-weight: 600;
  cursor: pointer;
  transition:
    color 0.15s ease,
    background-color 0.15s ease;
}

.billing-opt.active {
  background: var(--color-accent-soft);
  color: var(--color-accent);
}

.billing-grid {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--color-border);
}

.field {
  display: grid;
  gap: 5px;
  min-width: 0;
}

.field > span {
  color: var(--color-dim);
  font-size: 11.5px;
  font-weight: 600;
  letter-spacing: 0.02em;
}

.field input[type='number'],
.field input[type='date'],
.field select {
  width: 100%;
  height: 36px;
  padding: 0 10px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  color: var(--color-text);
  font-size: 13.5px;
  transition: border-color 0.15s ease;
}

.field input:focus,
.field select:focus {
  outline: none;
  border-color: var(--color-border-hover);
}

.date-row {
  display: flex;
  align-items: center;
  gap: 6px;
}

.date-row input {
  flex: 1;
  min-width: 0;
}

.clear-date {
  flex-shrink: 0;
  width: 36px;
  height: 36px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  color: var(--color-accent);
  font-size: 14px;
  font-weight: 700;
  cursor: pointer;
  transition:
    border-color 0.15s ease,
    background-color 0.15s ease;
}

.clear-date:hover {
  border-color: var(--color-border-hover);
  background: var(--color-accent-soft);
}

.edit-actions {
  margin-top: 2px;
}

@media (max-width: 520px) {
  .edit-grid,
  .billing-grid {
    grid-template-columns: 1fr;
  }

  .billing-head {
    flex-direction: column;
    align-items: stretch;
  }

  .billing-toggle {
    width: 100%;
    justify-content: stretch;
  }

  .billing-opt {
    flex: 1;
    text-align: center;
  }
}
</style>
