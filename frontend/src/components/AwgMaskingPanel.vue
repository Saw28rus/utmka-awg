<template>
  <div class="masking">
    <div class="panel block">
      <div class="masking-head">
        <div class="masking-title">
          <h3>Маскировка</h3>
        </div>
        <n-button size="small" tertiary :loading="loading" @click="reload">
          <template #icon><RefreshCw :size="15" /></template>
          Обновить
        </n-button>
      </div>
      <p class="masking-hint">
        У 2.0 и 3.1 разная маскировка — это нормально. Цифры вручную лучше не трогать:
        ключи клиентов к ним привязаны.
      </p>

      <div v-if="loading && !data" class="masking-placeholder">
        <n-spin size="small" />
        <span>Читаю сервер…</span>
      </div>
      <div v-else-if="readError" class="masking-error">{{ readError }}</div>

      <div v-else class="profile-grid">
        <article
          v-for="p in profiles"
          :key="p.protocol"
          class="profile-card"
          :class="`tone-${scoreTone(p.score.status)}`"
        >
          <header class="profile-head">
            <strong>{{ p.label }}</strong>
            <StatusBadge :label="p.score.label" :tone="scoreTone(p.score.status)" :pulse="false" />
          </header>
          <p class="profile-summary">{{ p.summary }}</p>
          <dl class="profile-meta">
            <div>
              <dt>Порт</dt>
              <dd>UDP {{ p.listen_port ?? '—' }}</dd>
            </div>
            <div>
              <dt>Клиентов</dt>
              <dd>{{ p.clients_total }}</dd>
            </div>
            <div v-if="p.protocol === 'awg31'">
              <dt>Защита заголовков</dt>
              <dd>{{ p.header_protection ? 'включена' : 'нет' }}</dd>
            </div>
            <div v-if="p.protocol === 'awg2' && rotationAgeText">
              <dt>Обновляли</dt>
              <dd>{{ rotationAgeText }}</dd>
            </div>
          </dl>
          <div v-if="cardWarnings(p).length" class="masking-warnings">
            <div v-for="w in cardWarnings(p)" :key="w.code" class="warn-item" :class="w.level">
              <component :is="warnIcon(w.level)" :size="15" />
              <span>{{ w.message }}</span>
            </div>
          </div>
          <div v-if="p.can_rotate" class="profile-actions">
            <n-button size="small" type="primary" :loading="applyLoading || previewLoading" @click="confirmRotate">
              Обновить маскировку 2.0
            </n-button>
            <n-button
              v-if="snapshots.length"
              size="small"
              tertiary
              :loading="rollbackLoading"
              @click="confirmRollback"
            >
              Вернуть как было
            </n-button>
          </div>
        </article>
      </div>

      <div v-if="applyResult" class="apply-steps">
        <div
          v-for="(s, i) in applyResult.steps"
          :key="i"
          class="apply-step"
          :class="s.status"
        >
          <component :is="stepIcon(s.status)" :size="14" />
          <span>{{ stepLabel(s.name) }}<template v-if="s.detail"> — {{ s.detail }}</template></span>
        </div>
        <div v-if="applyResult.error" class="masking-error">{{ applyResult.error }}</div>
      </div>
    </div>

    <div v-if="canRotate" class="panel block auto-rotation">
      <div class="masking-head">
        <div class="masking-title">
          <h3>Авто-обновление 2.0</h3>
          <StatusBadge
            :label="policy.enabled ? 'Включено' : 'Выключено'"
            :tone="policy.enabled ? 'ok' : 'neutral'"
            :pulse="false"
          />
        </div>
        <n-switch v-model:value="policy.enabled" :loading="policyLoading" @update:value="savePolicy" />
      </div>
      <p class="masking-hint">
        Панель сама сменит маскировку 2.0 и перевыпустит ключи. Клиенты 3.1 не затрагиваются.
      </p>
      <label class="rot-field rot-field-inline">
        <span>Раз в</span>
        <n-input-number
          v-model:value="policy.interval_days"
          size="small"
          :min="7"
          :max="90"
          :disabled="!policy.enabled || policyLoading"
          @update:value="savePolicy"
        />
        <span>дней</span>
      </label>
      <p v-if="policyStatusText" class="rotation-age">{{ policyStatusText }}</p>
    </div>

    <div class="panel block fallback">
      <div class="masking-head">
        <div class="masking-title">
          <h3>Запасной канал (если режут UDP)</h3>
          <StatusBadge
            v-if="fallback"
            :label="fallbackBadge.label"
            :tone="fallbackBadge.tone"
            :pulse="false"
          />
        </div>
      </div>
      <p class="masking-hint">
        AmneziaWG идёт по UDP. Если оператор режет UDP целиком, нужен Reality по TCP/443.
        Это не замена 2.0/3.1, а запасной ключ.
      </p>
      <template v-if="fallback && fallback.installed">
        <p class="fallback-line">
          {{ fallback.running ? 'Работает' : 'Остановлен' }}
          · порт {{ fallback.port ?? '—' }}
          · ключей Reality: {{ fallback.clients_total }}
        </p>
        <div v-if="fallback.warnings.length" class="masking-warnings">
          <div v-for="w in fallback.warnings" :key="w.code" class="warn-item" :class="w.level">
            <component :is="warnIcon(w.level)" :size="15" />
            <span>{{ w.message }}</span>
          </div>
        </div>
      </template>
      <template v-else-if="fallback">
        <n-button size="small" type="primary" secondary @click="emit('goto-protocols')">
          Поставить Reality на вкладке «Протоколы»
        </n-button>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { AlertTriangle, CheckCircle2, Info, RefreshCw, XCircle } from '@lucide/vue'
import { NButton, NInputNumber, NSpin, NSwitch, useDialog, useMessage } from 'naive-ui'
import { computed, onMounted, reactive, ref } from 'vue'

import { api } from '@/api/client'
import StatusBadge from '@/components/StatusBadge.vue'

const props = defineProps<{ serverId: string }>()
const emit = defineEmits<{ 'goto-protocols': [] }>()

type MaskingWarning = { level: string; code: string; message: string }
type MaskingScore = { status: string; label: string }

type MaskingProfile = {
  protocol: string
  label: string
  listen_port?: number | null
  clients_total: number
  score: MaskingScore
  summary: string
  warnings: MaskingWarning[]
  can_rotate: boolean
  header_protection?: boolean | null
  random_trailers_off?: boolean | null
}

type MaskingState = {
  version: string
  listen_port: number | null
  i_present: string[]
}

type RealityFallback = {
  installed: boolean
  running: boolean | null
  port: number | null
  sni: string | null
  clients_total: number
  warnings: MaskingWarning[]
}

type MaskingResponse = {
  ok: boolean
  state: MaskingState | null
  score: MaskingScore
  warnings: MaskingWarning[]
  read_error: string | null
  last_rotation_at?: string | null
  rotation_age_days?: number | null
  fallback?: RealityFallback | null
  profiles?: MaskingProfile[]
}

type MaskingPreview = {
  ok: boolean
  preset: string
  params: Record<string, string>
  clients_reissuable: number
  clients_skipped: number
  error: string | null
}

type MaskingStep = { name: string; status: string; detail: string | null }

type MaskingApplyResult = {
  ok: boolean
  steps: MaskingStep[]
  reissued: number
  chat_delivered?: number
  error: string | null
  masking: MaskingResponse | null
}

type SnapshotInfo = { id: string; created_at: string; label: string }

type RotationPolicy = {
  enabled: boolean
  preset: string
  interval_days: number
  window_start: number
  window_end: number
  trigger_on_dpi: boolean
  last_rotated_at: string | null
  last_status: string | null
  last_error: string | null
}

const STEP_LABELS: Record<string, string> = {
  'Валидация параметров': 'Проверка',
  'Чтение текущего конфига': 'Чтение сервера',
  'Snapshot конфига (зашифрован)': 'Запасная копия',
  'Dry-run рендера': 'Проверка конфига',
  'Запись нового конфига': 'Запись',
  'Перезапуск интерфейса': 'Перезапуск VPN',
  'Проверка UDP-порта': 'Проверка порта',
  'Перевыпуск клиентских конфигов': 'Обновление ключей 2.0',
  'Ключи в чат': 'Отправка в чат',
  'Каскад': 'Каскад',
  'Автооткат на snapshot': 'Откат'
}

const message = useMessage()
const dialog = useDialog()
const loading = ref(false)
const data = ref<MaskingResponse | null>(null)
const snapshots = ref<SnapshotInfo[]>([])
const applyLoading = ref(false)
const previewLoading = ref(false)
const rollbackLoading = ref(false)
const applyResult = ref<MaskingApplyResult | null>(null)

const policy = reactive<RotationPolicy>({
  enabled: false,
  preset: 'amnezia',
  interval_days: 14,
  window_start: 3,
  window_end: 6,
  trigger_on_dpi: true,
  last_rotated_at: null,
  last_status: null,
  last_error: null
})
const policyLoading = ref(false)

const profiles = computed<MaskingProfile[]>(() => data.value?.profiles || [])
const readError = computed(() => data.value?.read_error ?? null)
const fallback = computed(() => data.value?.fallback ?? null)
const canRotate = computed(() => profiles.value.some((p) => p.can_rotate))

const rotationAgeText = computed(() => {
  const days = data.value?.rotation_age_days
  if (days === null || days === undefined) return ''
  if (days === 0) return 'сегодня'
  return `${days} дн. назад`
})

const fallbackBadge = computed<{ label: string; tone: 'ok' | 'warning' | 'danger' | 'neutral' }>(() => {
  const fb = fallback.value
  if (!fb || !fb.installed) return { label: 'Нет', tone: 'neutral' }
  if (!fb.running) return { label: 'Остановлен', tone: 'danger' }
  if (fb.warnings.some((w) => w.level === 'warning' || w.level === 'danger')) {
    return { label: 'Проверьте', tone: 'warning' }
  }
  return { label: 'Готов', tone: 'ok' }
})

const policyStatusText = computed(() => {
  if (!policy.last_rotated_at) return ''
  const when = new Date(policy.last_rotated_at).toLocaleString('ru-RU')
  if (policy.last_status === 'ok') return `Последний раз: ${when}`
  if (policy.last_status === 'rolled_back') return `Откат ${when}`
  if (policy.last_status === 'failed') return `Ошибка ${when}`
  return when
})

function scoreTone(status?: string): 'ok' | 'warning' | 'danger' | 'neutral' {
  if (status === 'strong') return 'ok'
  if (status === 'basic' || status === 'weak') return 'warning'
  if (status === 'invalid') return 'danger'
  return 'neutral'
}

function cardWarnings(p: MaskingProfile) {
  return (p.warnings || []).filter((w) => w.level === 'warning' || w.level === 'danger')
}

function warnIcon(level: string) {
  if (level === 'info') return Info
  return AlertTriangle
}

function stepIcon(status: string) {
  if (status === 'ok') return CheckCircle2
  if (status === 'failed') return XCircle
  return Info
}

function stepLabel(name: string) {
  return STEP_LABELS[name] || name
}

async function load(check: boolean) {
  loading.value = true
  try {
    const url = check
      ? `/servers/${props.serverId}/awg/masking/check`
      : `/servers/${props.serverId}/awg/masking`
    const { data: resp } = check
      ? await api.post<MaskingResponse>(url)
      : await api.get<MaskingResponse>(url)
    data.value = resp
  } catch {
    message.error('Не удалось получить маскировку.')
  } finally {
    loading.value = false
  }
}

async function loadRotationMeta() {
  try {
    const { data: snapList } = await api.get<SnapshotInfo[]>(
      `/servers/${props.serverId}/awg/masking/snapshots`
    )
    snapshots.value = snapList
  } catch {
    snapshots.value = []
  }
}

async function loadPolicy() {
  try {
    const { data: p } = await api.get<RotationPolicy>(
      `/servers/${props.serverId}/awg/masking/rotation`
    )
    Object.assign(policy, p)
  } catch {
    /* дефолты */
  }
}

async function savePolicy() {
  policyLoading.value = true
  try {
    const { data: p } = await api.put<RotationPolicy>(
      `/servers/${props.serverId}/awg/masking/rotation`,
      {
        enabled: policy.enabled,
        preset: 'amnezia',
        interval_days: Math.min(90, Math.max(7, policy.interval_days || 14)),
        window_start: policy.window_start,
        window_end: policy.window_end,
        trigger_on_dpi: policy.trigger_on_dpi
      }
    )
    Object.assign(policy, p)
  } catch (err: any) {
    message.error(err?.response?.data?.detail || 'Не удалось сохранить авто-обновление.')
    void loadPolicy()
  } finally {
    policyLoading.value = false
  }
}

function confirmRotate() {
  dialog.warning({
    title: 'Обновить маскировку 2.0?',
    content:
      'Ключи AmneziaWG 2.0 перевыпустятся, клиенты 2.0 нужно будет подключить заново ' +
      '(в чат уйдёт само, если есть привязка). Клиенты 3.1 не меняются.',
    positiveText: 'Обновить',
    negativeText: 'Отмена',
    onPositiveClick: () => {
      void doRotate()
    }
  })
}

async function doRotate() {
  previewLoading.value = true
  applyResult.value = null
  try {
    const { data: preview } = await api.post<MaskingPreview>(
      `/servers/${props.serverId}/awg/masking/preview`,
      { preset: 'amnezia', include_cps: false }
    )
    if (!preview.ok) {
      message.error(preview.error || 'Не удалось подготовить новые параметры.')
      return
    }
    applyLoading.value = true
    const { data: resp } = await api.post<MaskingApplyResult>(
      `/servers/${props.serverId}/awg/masking/apply`,
      { preset: 'amnezia', params: preview.params, notify_chat: true }
    )
    applyResult.value = resp
    if (resp.ok) {
      const chat = resp.chat_delivered || 0
      message.success(
        chat
          ? `Маскировка 2.0 обновлена. Ключей: ${resp.reissued}, в чат: ${chat}.`
          : `Маскировка 2.0 обновлена. Ключей: ${resp.reissued}.`
      )
      if (resp.masking) data.value = resp.masking
      void loadRotationMeta()
    } else {
      message.error(resp.error || 'Не удалось обновить маскировку.')
      void load(true)
    }
  } catch {
    message.error('Не удалось обновить маскировку — проверьте сервер.')
    void load(true)
  } finally {
    previewLoading.value = false
    applyLoading.value = false
    void loadPolicy()
  }
}

function confirmRollback() {
  const latest = snapshots.value[0]
  if (!latest) return
  dialog.warning({
    title: 'Вернуть предыдущую маскировку 2.0?',
    content: 'Ключи 2.0 перевыпустятся под прошлые параметры. 3.1 не трогаем.',
    positiveText: 'Вернуть',
    negativeText: 'Отмена',
    onPositiveClick: () => {
      void doRollback()
    }
  })
}

async function doRollback() {
  rollbackLoading.value = true
  applyResult.value = null
  try {
    const { data: resp } = await api.post<MaskingApplyResult>(
      `/servers/${props.serverId}/awg/masking/rollback`,
      {}
    )
    applyResult.value = resp
    if (resp.ok) {
      message.success('Маскировку 2.0 вернули.')
      if (resp.masking) data.value = resp.masking
      void loadRotationMeta()
    } else {
      message.error(resp.error || 'Откат не удался.')
      void load(true)
    }
  } catch {
    message.error('Откат не удался — проверьте сервер.')
    void load(true)
  } finally {
    rollbackLoading.value = false
  }
}

function reload() {
  void load(true)
}

onMounted(() => {
  void load(false)
  void loadRotationMeta()
  void loadPolicy()
})
</script>

<style scoped>
.masking-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.masking-title {
  display: flex;
  align-items: center;
  gap: 10px;
}

.masking-title h3 {
  margin: 0;
  font-size: 16px;
}

.masking-hint {
  margin: 6px 0 14px;
  color: var(--color-muted);
  font-size: 13px;
  line-height: 1.45;
}

.masking-placeholder {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 18px 0;
  color: var(--color-muted);
}

.masking-error {
  padding: 12px 14px;
  border: 1px solid var(--color-danger);
  border-radius: var(--radius-sm);
  color: var(--color-danger);
  font-size: 13px;
}

.profile-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 12px;
}

.profile-card {
  display: grid;
  gap: 10px;
  padding: 14px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  background: var(--color-surface-2);
}

.profile-card.tone-ok {
  border-color: var(--color-cascade-border-active);
}

.profile-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.profile-head strong {
  font-size: 15px;
}

.profile-summary {
  margin: 0;
  color: var(--color-muted);
  font-size: 13px;
  line-height: 1.4;
}

.profile-meta {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px 12px;
  margin: 0;
}

.profile-meta dt {
  font-size: 11px;
  color: var(--color-dim);
}

.profile-meta dd {
  margin: 1px 0 0;
  font-size: 13px;
}

.profile-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.masking-warnings {
  display: grid;
  gap: 8px;
}

.warn-item {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 9px 12px;
  border-radius: var(--radius-sm);
  font-size: 13px;
  line-height: 1.4;
}

.warn-item.warning {
  background: color-mix(in srgb, var(--color-warning, #d8a657) 16%, transparent);
}

.warn-item.danger {
  background: color-mix(in srgb, var(--color-danger) 16%, transparent);
}

.apply-steps {
  display: grid;
  gap: 6px;
  margin-top: 14px;
}

.apply-step {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  font-size: 13px;
  line-height: 1.4;
}

.apply-step.ok {
  color: var(--color-muted);
}

.apply-step.failed {
  color: var(--color-danger);
}

.auto-rotation,
.fallback {
  margin-top: 16px;
}

.rot-field-inline {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  font-size: 13px;
}

.rotation-age {
  margin: 0;
  font-size: 12px;
  color: var(--color-muted);
}

.fallback-line {
  margin: 0 0 10px;
  font-size: 13px;
}
</style>
