<template>
  <div class="masking">
    <div class="panel block">
      <div class="masking-head">
        <div class="masking-title">
          <h3>Маскировка AmneziaWG</h3>
          <StatusBadge :label="scoreLabel" :tone="scoreTone" :pulse="false" />
        </div>
        <n-button size="small" tertiary :loading="loading" @click="reload">
          <template #icon><RefreshCw :size="15" /></template>
          Проверить
        </n-button>
      </div>
      <p class="masking-hint">
        Оценка показывает качество настройки обфускации, а не гарантию обхода блокировок.
      </p>

      <div v-if="loading && !data" class="masking-placeholder">
        <n-spin size="small" />
        <span>Читаю конфигурацию сервера…</span>
      </div>

      <div v-else-if="readError" class="masking-error">
        {{ readError }}
      </div>

      <template v-else-if="state">
        <dl class="masking-kv">
          <div><dt>Версия</dt><dd>{{ versionLabel }}</dd></div>
          <div><dt>Контейнер</dt><dd class="mono">{{ state.container || '—' }}</dd></div>
          <div><dt>Интерфейс</dt><dd class="mono">{{ state.interface || '—' }}</dd></div>
          <div><dt>UDP-порт</dt><dd class="mono">{{ state.listen_port ?? '—' }}</dd></div>
          <div><dt>Endpoint</dt><dd class="mono">{{ state.endpoint || '—' }}</dd></div>
          <div><dt>MTU</dt><dd class="mono">{{ state.mtu ?? '—' }}</dd></div>
        </dl>

        <div class="masking-params">
          <div class="param-group">
            <span class="param-label">J (junk)</span>
            <div class="param-row">
              <span>Jc <b>{{ state.jc ?? '—' }}</b></span>
              <span>Jmin <b>{{ state.jmin ?? '—' }}</b></span>
              <span>Jmax <b>{{ state.jmax ?? '—' }}</b></span>
            </div>
          </div>
          <div class="param-group">
            <span class="param-label">S (sizes)</span>
            <div class="param-row">
              <span>S1 <b>{{ state.s1 ?? '—' }}</b></span>
              <span>S2 <b>{{ state.s2 ?? '—' }}</b></span>
              <span>S3 <b>{{ state.s3 ?? '—' }}</b></span>
              <span>S4 <b>{{ state.s4 ?? '—' }}</b></span>
            </div>
          </div>
          <div class="param-group">
            <span class="param-label">
              H (headers)
              <em>{{ state.h_is_ranges ? 'диапазоны' : 'одиночные' }}</em>
            </span>
            <div class="param-col mono">
              <span>H1 = {{ state.h1 ?? '—' }}</span>
              <span>H2 = {{ state.h2 ?? '—' }}</span>
              <span>H3 = {{ state.h3 ?? '—' }}</span>
              <span>H4 = {{ state.h4 ?? '—' }}</span>
            </div>
          </div>
          <div class="param-group">
            <span class="param-label">I (CPS)</span>
            <div class="param-row">
              <span v-if="state.i_present.length">{{ state.i_present.join(', ') }}</span>
              <span v-else class="muted">не заданы</span>
            </div>
          </div>
        </div>

        <div v-if="warnings.length" class="masking-warnings">
          <div v-for="w in warnings" :key="w.code" class="warn-item" :class="w.level">
            <component :is="warnIcon(w.level)" :size="15" />
            <span>{{ w.message }}</span>
          </div>
        </div>
        <div v-else class="masking-ok">
          <ShieldCheck :size="15" />
          <span>Предупреждений нет.</span>
        </div>
      </template>
    </div>

    <div v-if="rotationAvailable" class="panel block rotation">
      <div class="masking-head">
        <div class="masking-title">
          <h3>Ротация параметров</h3>
        </div>
        <n-button
          v-if="snapshots.length"
          size="small"
          tertiary
          type="warning"
          :loading="rollbackLoading"
          @click="confirmRollback"
        >
          <template #icon><Undo2 :size="15" /></template>
          Откатить на snapshot
        </n-button>
      </div>
      <p class="masking-hint">
        Генерация уникальных параметров обфускации под этот сервер. Перед применением создаётся
        зашифрованный snapshot, после — конфиги всех клиентов перевыпускаются автоматически,
        но их нужно заново раздать клиентам.
      </p>
      <p v-if="rotationAgeText" class="rotation-age">{{ rotationAgeText }}</p>

      <div class="preset-row">
        <button
          v-for="p in presets"
          :key="p.id"
          class="preset-card"
          :class="{ active: selectedPreset === p.id }"
          type="button"
          @click="selectedPreset = p.id"
        >
          <span class="preset-name">{{ p.label }}</span>
          <span class="preset-desc">{{ p.description }}</span>
        </button>
      </div>

      <div class="rotation-actions">
        <n-button size="small" type="primary" secondary :loading="previewLoading" @click="makePreview">
          <template #icon><Dices :size="15" /></template>
          Сгенерировать предпросмотр
        </n-button>
      </div>

      <div v-if="preview && preview.ok" class="preview-box">
        <div class="preview-grid mono">
          <div class="preview-col">
            <span class="param-label">Сейчас</span>
            <span v-for="k in rotatedKeys" :key="'c' + k">{{ k }} = {{ preview.current[k] ?? '—' }}</span>
          </div>
          <div class="preview-col">
            <span class="param-label">Будет</span>
            <span v-for="k in rotatedKeys" :key="'n' + k" class="preview-new">{{ k }} = {{ preview.params[k] }}</span>
          </div>
        </div>
        <div class="preview-meta">
          <span>Клиентов: {{ preview.clients_total }}, перевыпустятся: {{ preview.clients_reissuable }}<template v-if="preview.clients_skipped">, без ключей (вручную): {{ preview.clients_skipped }}</template></span>
          <span v-if="preview.cascade_entry" class="preview-cascade">
            Сервер — entry каскада: после ротации рекомендуется переприменить каскад.
          </span>
        </div>
        <n-button size="small" type="primary" :loading="applyLoading" @click="confirmApply">
          <template #icon><ShieldCheck :size="15" /></template>
          Применить с перевыпуском клиентов
        </n-button>
      </div>
      <div v-else-if="preview && !preview.ok" class="masking-error">
        {{ preview.error || preview.errors.join('; ') }}
      </div>

      <div v-if="applyResult" class="apply-steps">
        <div
          v-for="(s, i) in applyResult.steps"
          :key="i"
          class="apply-step"
          :class="s.status"
        >
          <component :is="stepIcon(s.status)" :size="14" />
          <span>{{ s.name }}<template v-if="s.detail"> — {{ s.detail }}</template></span>
        </div>
        <div v-if="applyResult.error" class="masking-error">{{ applyResult.error }}</div>
      </div>
    </div>

    <div v-if="rotationAvailable" class="panel block auto-rotation">
      <div class="masking-head">
        <div class="masking-title">
          <h3>Авто-ротация маскировки</h3>
          <StatusBadge
            :label="policy.enabled ? 'Включена' : 'Выключена'"
            :tone="policy.enabled ? 'ok' : 'neutral'"
            :pulse="false"
          />
        </div>
        <n-switch v-model:value="policy.enabled" :loading="policyLoading" @update:value="savePolicy" />
      </div>
      <p class="masking-hint">
        Панель сама обновит параметры обфускации (snapshot → применение → health-проверка →
        авто-откат при сбое). <b>Важно:</b> после ротации старые клиентские конфиги и QR перестают
        работать — клиенту нужно переимпортировать конфиг. Поэтому функция включается осознанно.
      </p>

      <div class="rotation-grid">
        <label class="rot-field">
          <span class="param-label">Пресет</span>
          <n-select
            v-model:value="policy.preset"
            size="small"
            :options="presetOptions"
            :disabled="!policy.enabled || policyLoading"
            @update:value="savePolicy"
          />
        </label>
        <label class="rot-field">
          <span class="param-label">Интервал, дней</span>
          <n-input-number
            v-model:value="policy.interval_days"
            size="small"
            :min="1"
            :max="90"
            :disabled="!policy.enabled || policyLoading"
            @update:value="savePolicy"
          />
        </label>
        <label class="rot-field">
          <span class="param-label">Окно (UTC) с</span>
          <n-input-number
            v-model:value="policy.window_start"
            size="small"
            :min="0"
            :max="23"
            :disabled="!policy.enabled || policyLoading"
            @update:value="savePolicy"
          />
        </label>
        <label class="rot-field">
          <span class="param-label">по</span>
          <n-input-number
            v-model:value="policy.window_end"
            size="small"
            :min="0"
            :max="23"
            :disabled="!policy.enabled || policyLoading"
            @update:value="savePolicy"
          />
        </label>
      </div>

      <label class="rot-toggle">
        <n-switch
          v-model:value="policy.trigger_on_dpi"
          size="small"
          :disabled="!policy.enabled || policyLoading"
          @update:value="savePolicy"
        />
        <span>
          Аварийная ротация по сигналу DPI (OBS3): если маскировку начали резать — ротировать сразу,
          игнорируя окно обслуживания.
        </span>
      </label>

      <div class="rotation-actions">
        <n-button size="small" tertiary :loading="runLoading" :disabled="applyLoading" @click="confirmRunNow">
          <template #icon><RefreshCw :size="15" /></template>
          Ротировать сейчас
        </n-button>
        <span v-if="policyStatusText" class="rotation-age">{{ policyStatusText }}</span>
      </div>
    </div>

    <div class="panel block fallback">
      <div class="masking-head">
        <div class="masking-title">
          <h3>План Б: Reality (при полном UDP-бане)</h3>
          <StatusBadge
            v-if="fallback"
            :label="fallbackBadge.label"
            :tone="fallbackBadge.tone"
            :pulse="false"
          />
        </div>
      </div>
      <p class="masking-hint">
        AmneziaWG работает по UDP. Если сеть полностью блокирует UDP, никакая маскировка не
        поможет — нужен запасной канал по TCP/443. Reality не заменяет AWG и не влияет на оценку
        маскировки: это отдельный fallback-профиль, который стоит выдать клиентам заранее.
      </p>

      <template v-if="fallback && fallback.installed">
        <dl class="masking-kv">
          <div><dt>Статус</dt><dd>{{ fallback.running ? 'Работает' : 'Остановлен' }}</dd></div>
          <div><dt>TCP-порт</dt><dd class="mono">{{ fallback.port ?? '—' }}</dd></div>
          <div><dt>Имитируемый сайт (SNI)</dt><dd class="mono">{{ fallback.sni || '—' }}</dd></div>
          <div><dt>Клиентов с Reality-профилем</dt><dd>{{ fallback.clients_total }}</dd></div>
        </dl>

        <div v-if="fallback.warnings.length" class="masking-warnings">
          <div v-for="w in fallback.warnings" :key="w.code" class="warn-item" :class="w.level">
            <component :is="warnIcon(w.level)" :size="15" />
            <span>{{ w.message }}</span>
          </div>
        </div>
        <div v-else class="masking-ok">
          <ShieldCheck :size="15" />
          <span>Запасной канал готов. Выдать профиль: Клиенты → Добавить → протокол Xray (Reality).</span>
        </div>
      </template>

      <template v-else-if="fallback">
        <div class="warn-item info">
          <Info :size="15" />
          <span>
            Reality не установлен на этом сервере. Без него при полном UDP-бане клиенты останутся
            без связи. Установка не трогает AmneziaWG и текущих клиентов.
          </span>
        </div>
        <div class="fallback-actions">
          <n-button size="small" type="primary" secondary @click="emit('goto-protocols')">
            <template #icon><LifeBuoy :size="15" /></template>
            Установить Xray (Reality) на вкладке «Протоколы»
          </n-button>
        </div>
      </template>

      <div v-else class="masking-placeholder">
        <span class="muted">Состояние запасного канала появится после проверки маскировки.</span>
      </div>
    </div>

    <div v-if="rotationAvailable" class="panel block endpoint">
      <h3>Endpoint-домен</h3>
      <p class="masking-hint">
        Если задать домен (A-запись на этот сервер), клиентские конфиги будут указывать на него
        вместо IP — потом можно сменить IP сервера без перевыпуска ключей. Это операционное удобство,
        а не маскировка. Пусто — используется IP сервера. После сохранения конфиги перевыпускаются.
      </p>
      <div class="endpoint-row">
        <n-input
          v-model:value="endpointHost"
          size="small"
          placeholder="vpn.example.com (пусто = IP сервера)"
          :disabled="endpointLoading"
          style="max-width: 360px"
        />
        <n-button size="small" type="primary" secondary :loading="endpointLoading" @click="applyEndpoint">
          Сохранить и перевыпустить
        </n-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { AlertTriangle, CheckCircle2, Dices, Info, LifeBuoy, RefreshCw, ShieldCheck, Undo2, XCircle } from '@lucide/vue'
import { NButton, NInput, NInputNumber, NSelect, NSpin, NSwitch, useDialog, useMessage } from 'naive-ui'
import { computed, onMounted, reactive, ref } from 'vue'

import { api } from '@/api/client'
import StatusBadge from '@/components/StatusBadge.vue'

const props = defineProps<{ serverId: string }>()
const emit = defineEmits<{ 'goto-protocols': [] }>()

type MaskingState = {
  version: string
  container: string | null
  interface: string | null
  config_path: string | null
  listen_port: number | null
  endpoint: string | null
  mtu: number | null
  keepalive: number | null
  jc: string | null
  jmin: string | null
  jmax: string | null
  s1: string | null
  s2: string | null
  s3: string | null
  s4: string | null
  h1: string | null
  h2: string | null
  h3: string | null
  h4: string | null
  h_is_ranges: boolean
  i_present: string[]
}

type MaskingWarning = { level: string; code: string; message: string }

type RealityFallback = {
  installed: boolean
  running: boolean | null
  container: string | null
  port: number | null
  sni: string | null
  clients_total: number
  warnings: MaskingWarning[]
}

type MaskingResponse = {
  ok: boolean
  server_id: string
  state: MaskingState | null
  score: { status: string; label: string }
  warnings: MaskingWarning[]
  read_error: string | null
  checked_at: string | null
  last_rotation_at?: string | null
  rotation_age_days?: number | null
  fallback?: RealityFallback | null
}

type MaskingPreset = { id: string; label: string; description: string }

type MaskingPreview = {
  ok: boolean
  preset: string
  params: Record<string, string>
  current: Record<string, string>
  errors: string[]
  clients_total: number
  clients_reissuable: number
  clients_skipped: number
  cascade_entry: boolean
  error: string | null
}

type MaskingStep = { name: string; status: string; detail: string | null }

type MaskingApplyResult = {
  ok: boolean
  steps: MaskingStep[]
  snapshot_id: string | null
  rolled_back: boolean
  reissued: number
  reissue_skipped: number
  error: string | null
  masking: MaskingResponse | null
}

type SnapshotInfo = { id: string; created_at: string; label: string; preset: string | null }

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

const rotatedKeys = ['Jc', 'Jmin', 'Jmax', 'S1', 'S2', 'S3', 'S4', 'H1', 'H2', 'H3', 'H4']

const message = useMessage()
const dialog = useDialog()
const loading = ref(false)
const data = ref<MaskingResponse | null>(null)

const presets = ref<MaskingPreset[]>([])
const snapshots = ref<SnapshotInfo[]>([])
const selectedPreset = ref('balance')
const preview = ref<MaskingPreview | null>(null)
const previewLoading = ref(false)
const applyLoading = ref(false)
const rollbackLoading = ref(false)
const applyResult = ref<MaskingApplyResult | null>(null)
const endpointHost = ref('')
const endpointLoading = ref(false)

const policy = reactive<RotationPolicy>({
  enabled: false,
  preset: 'balance',
  interval_days: 14,
  window_start: 3,
  window_end: 6,
  trigger_on_dpi: true,
  last_rotated_at: null,
  last_status: null,
  last_error: null
})
const policyLoading = ref(false)
const runLoading = ref(false)

const state = computed(() => data.value?.state ?? null)
const warnings = computed(() => data.value?.warnings ?? [])
const readError = computed(() => data.value?.read_error ?? null)
const scoreLabel = computed(() => data.value?.score.label ?? 'Неизвестно')

const scoreTone = computed<'ok' | 'warning' | 'danger' | 'neutral'>(() => {
  switch (data.value?.score.status) {
    case 'strong':
      return 'ok'
    case 'basic':
    case 'weak':
      return 'warning'
    case 'invalid':
      return 'danger'
    default:
      return 'neutral'
  }
})

const versionLabel = computed(() => {
  switch (state.value?.version) {
    case 'awg2':
      return 'AmneziaWG 2.0'
    case 'awg15':
      return 'AmneziaWG 1.5'
    case 'legacy':
      return 'Legacy'
    default:
      return 'Неизвестно'
  }
})

const rotationAvailable = computed(
  () => state.value?.version === 'awg2' && !readError.value
)

const fallback = computed(() => data.value?.fallback ?? null)

const fallbackBadge = computed<{ label: string; tone: 'ok' | 'warning' | 'danger' | 'neutral' }>(() => {
  const fb = fallback.value
  if (!fb || !fb.installed) return { label: 'Не установлен', tone: 'neutral' }
  if (!fb.running) return { label: 'Остановлен', tone: 'danger' }
  if (fb.warnings.some((w) => w.level === 'warning' || w.level === 'danger')) {
    return { label: 'Требует внимания', tone: 'warning' }
  }
  return { label: 'Готов', tone: 'ok' }
})

const rotationAgeText = computed(() => {
  const days = data.value?.rotation_age_days
  if (days === null || days === undefined) return 'Ротация маскировки из панели ещё не выполнялась.'
  if (days === 0) return 'Параметры маскировки обновлены сегодня.'
  return `Последняя ротация маскировки: ${days} дн. назад.`
})

const presetOptions = computed(() =>
  presets.value.map((p) => ({ label: p.label, value: p.id }))
)

const policyStatusText = computed(() => {
  if (!policy.last_rotated_at) return 'Авто-ротаций ещё не было.'
  const when = new Date(policy.last_rotated_at).toLocaleString('ru-RU')
  if (policy.last_status === 'ok') return `Последняя ротация: ${when} — успешно.`
  if (policy.last_status === 'rolled_back') return `Последняя ротация: ${when} — откат (${policy.last_error || 'сбой'}).`
  if (policy.last_status === 'failed') return `Последняя ротация: ${when} — ошибка (${policy.last_error || 'сбой'}).`
  return `Последняя ротация: ${when}.`
})

function warnIcon(level: string) {
  if (level === 'info') return Info
  return AlertTriangle
}

function stepIcon(status: string) {
  if (status === 'ok') return CheckCircle2
  if (status === 'failed') return XCircle
  return Info
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
    message.error('Не удалось получить состояние маскировки.')
  } finally {
    loading.value = false
  }
}

async function loadRotationMeta() {
  try {
    const [{ data: presetList }, { data: snapList }] = await Promise.all([
      api.get<MaskingPreset[]>(`/servers/${props.serverId}/awg/masking/presets`),
      api.get<SnapshotInfo[]>(`/servers/${props.serverId}/awg/masking/snapshots`)
    ])
    presets.value = presetList
    snapshots.value = snapList
  } catch {
    /* секция ротации просто не покажет пресеты */
  }
  try {
    const { data: server } = await api.get<{ endpoint_host?: string | null }>(
      `/servers/${props.serverId}`
    )
    endpointHost.value = server.endpoint_host ?? ''
  } catch {
    /* endpoint prefill необязателен */
  }
}

async function loadPolicy() {
  try {
    const { data: p } = await api.get<RotationPolicy>(
      `/servers/${props.serverId}/awg/masking/rotation`
    )
    Object.assign(policy, p)
  } catch {
    /* секция авто-ротации использует дефолты */
  }
}

async function savePolicy() {
  policyLoading.value = true
  try {
    const { data: p } = await api.put<RotationPolicy>(
      `/servers/${props.serverId}/awg/masking/rotation`,
      {
        enabled: policy.enabled,
        preset: policy.preset,
        interval_days: policy.interval_days,
        window_start: policy.window_start,
        window_end: policy.window_end,
        trigger_on_dpi: policy.trigger_on_dpi
      }
    )
    Object.assign(policy, p)
  } catch (err: any) {
    message.error(err?.response?.data?.detail || 'Не удалось сохранить настройки авто-ротации.')
    void loadPolicy()
  } finally {
    policyLoading.value = false
  }
}

function confirmRunNow() {
  dialog.warning({
    title: 'Ротировать маскировку сейчас?',
    content:
      'Параметры обфускации сменятся немедленно. Клиенты потеряют связь до переимпорта новых ' +
      'конфигов. Будет создан snapshot, при сбое выполнится авто-откат.',
    positiveText: 'Ротировать',
    negativeText: 'Отмена',
    onPositiveClick: () => {
      void runNow()
    }
  })
}

async function runNow() {
  runLoading.value = true
  applyResult.value = null
  try {
    const { data: resp } = await api.post<MaskingApplyResult>(
      `/servers/${props.serverId}/awg/masking/rotation/run`,
      {}
    )
    applyResult.value = resp
    if (resp.ok) {
      message.success(`Ротация выполнена. Перевыпущено конфигов: ${resp.reissued}.`)
      if (resp.masking) data.value = resp.masking
      void loadRotationMeta()
    } else {
      message.error(resp.error || 'Ротация не удалась.')
      void load(true)
    }
  } catch {
    message.error('Ротация не удалась — проверьте состояние сервера.')
    void load(true)
  } finally {
    runLoading.value = false
    void loadPolicy()
  }
}

async function applyEndpoint() {
  endpointLoading.value = true
  try {
    const { data } = await api.post<{ ok: boolean; reissued: number; skipped: number }>(
      `/servers/${props.serverId}/awg/endpoint`,
      { endpoint_host: endpointHost.value.trim() || null }
    )
    message.success(
      `Endpoint сохранён. Перевыпущено конфигов: ${data.reissued}` +
        (data.skipped ? `, пропущено: ${data.skipped}.` : '.')
    )
  } catch (err: any) {
    message.error(err?.response?.data?.detail || 'Не удалось применить endpoint.')
  } finally {
    endpointLoading.value = false
  }
}

async function makePreview() {
  previewLoading.value = true
  applyResult.value = null
  try {
    const { data: resp } = await api.post<MaskingPreview>(
      `/servers/${props.serverId}/awg/masking/preview`,
      { preset: selectedPreset.value }
    )
    preview.value = resp
    if (!resp.ok && resp.error) message.error(resp.error)
  } catch {
    message.error('Не удалось сгенерировать предпросмотр.')
  } finally {
    previewLoading.value = false
  }
}

function confirmApply() {
  if (!preview.value?.ok) return
  dialog.warning({
    title: 'Применить новые параметры маскировки?',
    content:
      'Все подключённые клиенты потеряют связь до получения новых конфигов. ' +
      'Панель перевыпустит конфиги автоматически, но раздать их клиентам нужно вручную. ' +
      'Перед изменением будет создан snapshot для отката.',
    positiveText: 'Применить',
    negativeText: 'Отмена',
    onPositiveClick: () => {
      void doApply()
    }
  })
}

async function doApply() {
  if (!preview.value) return
  applyLoading.value = true
  try {
    const { data: resp } = await api.post<MaskingApplyResult>(
      `/servers/${props.serverId}/awg/masking/apply`,
      { preset: preview.value.preset, params: preview.value.params }
    )
    applyResult.value = resp
    if (resp.ok) {
      message.success(`Маскировка обновлена. Перевыпущено конфигов: ${resp.reissued}.`)
      preview.value = null
      if (resp.masking) data.value = resp.masking
      void loadRotationMeta()
    } else {
      message.error(resp.error || 'Применение не удалось.')
      void load(true)
    }
  } catch {
    message.error('Применение не удалось — проверьте состояние сервера.')
    void load(true)
  } finally {
    applyLoading.value = false
  }
}

function confirmRollback() {
  const latest = snapshots.value[0]
  if (!latest) return
  dialog.warning({
    title: 'Откатить параметры маскировки?',
    content: `Конфиг сервера будет восстановлен из snapshot (${latest.label}), клиентские конфиги перевыпустятся под старые параметры.`,
    positiveText: 'Откатить',
    negativeText: 'Отмена',
    onPositiveClick: () => {
      void doRollback()
    }
  })
}

async function doRollback() {
  rollbackLoading.value = true
  try {
    const { data: resp } = await api.post<MaskingApplyResult>(
      `/servers/${props.serverId}/awg/masking/rollback`,
      {}
    )
    applyResult.value = resp
    if (resp.ok) {
      message.success('Откат выполнен.')
      preview.value = null
      if (resp.masking) data.value = resp.masking
      void loadRotationMeta()
    } else {
      message.error(resp.error || 'Откат не удался.')
      void load(true)
    }
  } catch {
    message.error('Откат не удался — проверьте состояние сервера.')
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

.masking-kv {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 12px;
  margin: 0 0 16px;
}

.masking-kv dt {
  font-size: 12px;
  color: var(--color-dim);
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

.masking-kv dd {
  margin: 2px 0 0;
  font-size: 14px;
}

.masking-params {
  display: grid;
  gap: 12px;
  padding: 14px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface-2);
  margin-bottom: 16px;
}

.param-label {
  font-size: 12px;
  color: var(--color-dim);
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

.param-label em {
  margin-left: 6px;
  font-style: normal;
  color: var(--color-muted);
  text-transform: none;
}

.param-row {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  margin-top: 4px;
  font-size: 14px;
}

.param-col {
  display: grid;
  gap: 2px;
  margin-top: 4px;
  font-size: 13px;
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

.warn-item.info {
  background: var(--color-surface-2);
  color: var(--color-muted);
}

.warn-item.warning {
  background: color-mix(in srgb, var(--color-warning, #d8a657) 16%, transparent);
  color: var(--color-text);
}

.warn-item.danger {
  background: color-mix(in srgb, var(--color-danger) 16%, transparent);
  color: var(--color-text);
}

.masking-ok {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--color-muted);
  font-size: 13px;
}

.muted {
  color: var(--color-dim);
}

.mono {
  font-family: var(--font-mono, monospace);
}

.rotation {
  margin-top: 16px;
}

.rotation-age {
  margin: 0 0 12px;
  font-size: 12px;
  color: var(--color-muted);
}

.preset-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 10px;
  margin-bottom: 14px;
}

.preset-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 12px 14px;
  text-align: left;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface-2);
  color: var(--color-text);
  cursor: pointer;
  transition: border-color 0.15s ease;
}

.preset-card:hover {
  border-color: var(--color-muted);
}

.preset-card.active {
  border-color: var(--color-primary, #63e2b7);
}

.preset-name {
  font-size: 14px;
  font-weight: 600;
}

.preset-desc {
  font-size: 12px;
  color: var(--color-muted);
  line-height: 1.4;
}

.rotation-actions {
  display: flex;
  gap: 10px;
  margin-bottom: 14px;
}

.preview-box {
  display: grid;
  gap: 12px;
  padding: 14px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface-2);
}

.preview-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  font-size: 13px;
}

.preview-col {
  display: grid;
  gap: 2px;
  min-width: 0;
  overflow-wrap: anywhere;
}

.preview-new {
  color: var(--color-primary, #63e2b7);
}

.preview-meta {
  display: grid;
  gap: 4px;
  font-size: 13px;
  color: var(--color-muted);
}

.preview-cascade {
  color: var(--color-warning, #d8a657);
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

.apply-step.info {
  color: var(--color-warning, #d8a657);
}

.fallback {
  margin-top: 16px;
}

.fallback-actions {
  display: flex;
  gap: 10px;
  margin-top: 12px;
}

.auto-rotation {
  margin-top: 16px;
}

.rotation-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 12px;
  margin-bottom: 14px;
}

.rot-field {
  display: grid;
  gap: 4px;
}

.rot-toggle {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin-bottom: 14px;
  font-size: 13px;
  color: var(--color-muted);
  line-height: 1.4;
  cursor: pointer;
}

.endpoint {
  margin-top: 16px;
}

.endpoint h3 {
  margin: 0 0 6px;
  font-size: 16px;
}

.endpoint-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
}
</style>
