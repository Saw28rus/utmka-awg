<template>
  <n-modal v-model:show="visible" :mask-closable="false">
    <div class="panel replace-modal">
      <header class="modal-head">
        <div>
          <h3>{{ stage === 'intro' ? 'Замена входного сервера' : 'Заменить вход' }}</h3>
          <p v-if="stage !== 'intro'">
            Новый VPS с теми же ключами, peers и маскировкой. Клиентам конфиги менять не нужно.
          </p>
        </div>
        <n-button circle tertiary size="small" @click="close">
          <template #icon><X :size="15" /></template>
        </n-button>
      </header>

      <!-- ШАГ 0: краткое объяснение -->
      <div v-if="stage === 'intro'" class="intro">
        <div class="intro-icon">
          <ArrowRightLeft :size="28" />
        </div>
        <p class="intro-lead">
          Если входной сервер заблокировали или он недоступен — поднимите новый VPS
          <strong>без перевыпуска конфигов клиентам</strong>.
        </p>
        <ol class="intro-steps">
          <li>Выберите заранее добавленный одиночный сервер или введите IP и SSH нового <strong>чистого</strong> VPS</li>
          <li>Панель развернёт на нём копию входа (ключи, peers, маскировка, порт)</li>
          <li>Меняете <strong>A-запись домена</strong> у регистратора на новый IP</li>
          <li>Нажимаете «Активировать» — клиенты снова работают на старых конфигах</li>
        </ol>
        <p class="hint intro-note">
          Старый вход не трогается, пока вы не активируете замену. При любой ошибке новый сервер очищается автоматически.
        </p>
      </div>

      <!-- ШАГ 1: выбор сервера -->
      <div v-if="stage === 'form'" class="form">
        <div v-if="candidates.length" class="field span2">
          <span>Способ выбора сервера</span>
          <n-radio-group v-model:value="inputMode">
            <n-radio value="panel">Из панели (одиночный сервер)</n-radio>
            <n-radio value="manual">Ввести вручную</n-radio>
          </n-radio-group>
        </div>

        <div v-if="inputMode === 'panel' && candidates.length" class="panel-pick">
          <label class="field span2">
            <span>Сервер из панели</span>
            <n-select
              v-model:value="selectedCandidateId"
              placeholder="Выберите заранее добавленный VPS"
              :options="candidateOptions"
              :loading="loadingCandidates"
            />
          </label>
          <p v-if="selectedCandidate" class="hint">
            Будет использован SSH: <span class="mono">{{ selectedCandidate.host }}:{{ selectedCandidate.ssh_port }}</span>
          </p>
          <p class="hint">
            Подходит одиночный сервер без каскада — например, заранее добавленный «запасной» VPS.
          </p>
        </div>

        <div v-else class="form-grid">
          <label class="field span2">
            <span>IP нового сервера</span>
            <n-input v-model:value="form.host" placeholder="например, 195.201.10.20" />
          </label>
          <label class="field">
            <span>SSH-порт</span>
            <n-input-number v-model:value="form.port" :min="1" :max="65535" :show-button="false" />
          </label>
          <label class="field">
            <span>Пользователь</span>
            <n-input v-model:value="form.username" placeholder="root" />
          </label>
          <label class="field span2">
            <span>Способ авторизации</span>
            <n-radio-group v-model:value="form.authType">
              <n-radio value="password">Пароль</n-radio>
              <n-radio value="key">SSH-ключ</n-radio>
            </n-radio-group>
          </label>
          <label v-if="form.authType === 'password'" class="field span2">
            <span>Пароль root</span>
            <n-input v-model:value="form.password" type="password" show-password-on="click" placeholder="••••••••" />
          </label>
          <label v-else class="field span2">
            <span>Приватный SSH-ключ</span>
            <n-input
              v-model:value="form.key"
              type="textarea"
              :autosize="{ minRows: 3, maxRows: 6 }"
              placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"
            />
          </label>
        </div>
        <p v-if="inputMode === 'manual' || !candidates.length" class="hint">
          Сервер должен быть <strong>чистым</strong> (без AmneziaWG). Панель проверит это сама.
        </p>
      </div>

      <!-- РЕЗУЛЬТАТ PREFLIGHT -->
      <div v-if="stage === 'preflight'" class="result">
        <div v-if="preflightBlockers.length" class="block-list danger">
          <strong>Нельзя продолжить:</strong>
          <ul><li v-for="(b, i) in preflightBlockers" :key="'b' + i">{{ b }}</li></ul>
        </div>
        <div v-else class="block-list ok">
          <strong>Сервер готов к развёртыванию.</strong>
          <p>Публичный IP: <span class="mono">{{ rec?.new_public_ip || '—' }}</span></p>
        </div>
        <div v-if="preflightWarnings.length" class="block-list warn">
          <strong>Предупреждения:</strong>
          <ul><li v-for="(w, i) in preflightWarnings" :key="'w' + i">{{ w }}</li></ul>
        </div>
        <StepsTimeline v-if="rec?.steps?.length" :steps="rec.steps" />
      </div>

      <!-- ПРОВИЖН / ОЖИДАНИЕ -->
      <div v-if="['provisioning', 'waiting', 'ready', 'active'].includes(stage)" class="result">
        <div class="status-line" :class="statusTone">
          <n-spin v-if="stage === 'provisioning'" size="small" />
          <span>{{ statusText }}</span>
        </div>

        <StepsTimeline v-if="rec?.steps?.length" :steps="rec.steps" />

        <!-- Ожидание DNS -->
        <div v-if="stage === 'waiting' || stage === 'ready'" class="dns-block">
          <p class="dns-title">Смените A-запись домена у регистратора</p>
          <div class="dns-grid">
            <span class="dns-k">Домен</span>
            <span class="dns-v mono">{{ rec?.expected_domain || '— (домен не задан)' }}</span>
            <span class="dns-k">Должен указывать на</span>
            <span class="dns-v mono strong">{{ rec?.new_public_ip || '—' }}</span>
            <span class="dns-k">Сейчас резолвится в</span>
            <span class="dns-v mono">{{ (rec?.dns_resolved_ips || []).join(', ') || '—' }}</span>
          </div>
          <div class="dns-flags">
            <StatusBadge :label="rec?.dns_ok ? 'DNS совпал' : 'DNS не совпал'" :tone="rec?.dns_ok ? 'ok' : 'neutral'" />
            <StatusBadge :label="rec?.health_ok ? 'Новый вход жив' : 'Новый вход не отвечает'" :tone="rec?.health_ok ? 'ok' : 'neutral'" />
          </div>
          <p class="hint">
            После смены A-записи переключение идёт по мере истечения TTL (от пары минут до часа).
            Это нормально, не баг.
          </p>
        </div>

        <div v-if="stage === 'active'" class="block-list ok">
          <strong>Готово. Вход работает на новом сервере.</strong>
          <p>Клиентам ничего пересылать не нужно — старые конфиги работают.</p>
        </div>

        <div v-if="rec?.error" class="block-list danger">
          <strong>Ошибка:</strong> {{ rec.error }}
        </div>
      </div>

      <footer class="modal-foot">
        <n-button v-if="stage !== 'active' && stage !== 'intro'" tertiary :disabled="busy" @click="onCancel">
          {{ stage === 'form' ? 'Отмена' : 'Прервать замену' }}
        </n-button>

        <n-button v-if="stage === 'intro'" tertiary @click="close">Отмена</n-button>
        <n-button v-if="stage === 'intro'" type="primary" @click="goToForm">Я понял, продолжить</n-button>

        <n-button v-if="stage === 'form'" type="primary" :loading="busy" @click="runPreflight">
          Проверить сервер
        </n-button>

        <n-button
          v-if="stage === 'preflight'"
          type="primary"
          :loading="busy"
          :disabled="preflightBlockers.length > 0"
          @click="runProvision"
        >
          Развернуть на новом VPS
        </n-button>

        <n-button
          v-if="stage === 'waiting' || stage === 'ready'"
          tertiary
          :loading="busy"
          @click="runCheckDns"
        >
          Проверить DNS
        </n-button>
        <n-button
          v-if="stage === 'waiting' || stage === 'ready'"
          type="primary"
          :loading="activating"
          :disabled="!rec?.can_activate"
          @click="runActivate"
        >
          Активировать
        </n-button>

        <n-button v-if="stage === 'active'" type="primary" @click="finish">Закрыть</n-button>
      </footer>
    </div>
  </n-modal>
</template>

<script setup lang="ts">
import { ArrowRightLeft, X } from '@lucide/vue'
import {
  NButton,
  NInput,
  NInputNumber,
  NModal,
  NRadio,
  NRadioGroup,
  NSelect,
  NSpin,
  useDialog,
  useMessage
} from 'naive-ui'
import { computed, onUnmounted, ref, watch } from 'vue'

import { api } from '@/api/client'
import StatusBadge from '@/components/StatusBadge.vue'
import StepsTimeline from '@/components/ReplaceStepsTimeline.vue'

type ReplaceCandidate = {
  id: string
  name: string
  host: string
  ssh_port: number
  has_awg2: boolean
}

type Replacement = {
  status?: string
  new_host?: string
  new_public_ip?: string | null
  expected_domain?: string | null
  port?: number | null
  snapshot_source?: string | null
  dns_ok?: boolean
  dns_resolved_ips?: string[]
  health_ok?: boolean
  health_detail?: string | null
  can_activate?: boolean
  error?: string | null
  steps?: Array<{ name: string; status: string; detail?: string | null }>
}

type Stage = 'intro' | 'form' | 'preflight' | 'provisioning' | 'waiting' | 'ready' | 'active'

const props = defineProps<{
  show: boolean
  serverId: string
  serverName?: string
}>()

const emit = defineEmits<{
  'update:show': [value: boolean]
  replaced: []
}>()

const message = useMessage()
const dialog = useDialog()
const visible = ref(props.show)
const stage = ref<Stage>('intro')
const busy = ref(false)
const activating = ref(false)
const rec = ref<Replacement | null>(null)
const preflightBlockers = ref<string[]>([])
const preflightWarnings = ref<string[]>([])
const candidates = ref<ReplaceCandidate[]>([])
const loadingCandidates = ref(false)
const inputMode = ref<'panel' | 'manual'>('manual')
const selectedCandidateId = ref<string | null>(null)
let pollTimer: ReturnType<typeof setInterval> | null = null

const form = ref({
  host: '',
  port: 22,
  username: 'root',
  authType: 'password' as 'password' | 'key',
  password: '',
  key: ''
})

const statusText = computed(() => {
  switch (stage.value) {
    case 'provisioning':
      return 'Разворачиваю вход на новом сервере… это занимает 1–3 минуты, не закрывайте окно.'
    case 'waiting':
      return 'Ожидаю смену A-записи домена.'
    case 'ready':
      return 'Домен указывает на новый сервер — можно активировать.'
    case 'active':
      return 'Активно.'
    default:
      return ''
  }
})

const statusTone = computed(() => {
  if (stage.value === 'ready' || stage.value === 'active') return 'ok'
  if (rec.value?.error) return 'danger'
  return 'neutral'
})

const candidateOptions = computed(() =>
  candidates.value.map((c) => ({
    label: `${c.name} (${c.host}:${c.ssh_port})`,
    value: c.id
  }))
)

const selectedCandidate = computed(() =>
  candidates.value.find((c) => c.id === selectedCandidateId.value) ?? null
)

async function loadCandidates() {
  loadingCandidates.value = true
  try {
    const { data } = await api.get<ReplaceCandidate[]>(`/servers/${props.serverId}/replace/candidates`)
    candidates.value = data || []
    if (candidates.value.length && !selectedCandidateId.value) {
      selectedCandidateId.value = candidates.value[0].id
    }
  } catch {
    candidates.value = []
  } finally {
    loadingCandidates.value = false
  }
}

async function goToForm() {
  stage.value = 'form'
  await loadCandidates()
  inputMode.value = candidates.value.length ? 'panel' : 'manual'
}

function stageFromStatus(status?: string): Stage {
  switch (status) {
    case 'provisioning':
      return 'provisioning'
    case 'waiting_dns':
      return 'waiting'
    case 'ready':
      return 'ready'
    case 'active':
      return 'active'
    case 'preflight':
    case 'preflight_failed':
      return 'preflight'
    default:
      return stage.value
  }
}

function applyRec(data: Replacement | null) {
  rec.value = data
  if (data) stage.value = stageFromStatus(data.status)
}

function startPolling() {
  stopPolling()
  pollTimer = setInterval(async () => {
    try {
      const { data } = await api.get<Replacement | null>(`/servers/${props.serverId}/replace/status`)
      if (data) applyRec(data)
      if (['active', 'failed', 'aborted'].includes(data?.status || '')) stopPolling()
    } catch {
      /* ignore */
    }
  }, 4000)
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

async function runPreflight() {
  const usePanel = inputMode.value === 'panel' && candidates.value.length
  if (usePanel) {
    if (!selectedCandidateId.value) {
      message.warning('Выберите сервер из панели.')
      return
    }
  } else if (!form.value.host.trim()) {
    message.warning('Укажите IP нового сервера.')
    return
  }
  busy.value = true
  try {
    const payload = usePanel
      ? { source_server_id: selectedCandidateId.value }
      : {
          new_host: form.value.host.trim(),
          ssh_port: form.value.port || 22,
          ssh_username: form.value.username.trim() || 'root',
          ssh_password: form.value.authType === 'password' ? form.value.password : undefined,
          ssh_key: form.value.authType === 'key' ? form.value.key : undefined
        }
    const { data } = await api.post(`/servers/${props.serverId}/replace/preflight`, payload)
    preflightBlockers.value = data.blockers || []
    preflightWarnings.value = data.warnings || []
    rec.value = data.replacement || null
    stage.value = 'preflight'
  } catch (error: any) {
    message.error(error?.response?.data?.detail || 'Не удалось проверить сервер.')
  } finally {
    busy.value = false
  }
}

async function runProvision() {
  busy.value = true
  stage.value = 'provisioning'
  startPolling()
  try {
    const { data } = await api.post<Replacement>(
      `/servers/${props.serverId}/replace/provision`,
      {},
      { timeout: 600_000 }
    )
    applyRec(data)
  } catch (error: any) {
    message.error(error?.response?.data?.detail || 'Развёртывание не удалось.')
    // подтянем актуальный статус (там будет ошибка/откат)
    await refreshStatus()
  } finally {
    busy.value = false
  }
}

async function runCheckDns() {
  busy.value = true
  try {
    const { data } = await api.post<Replacement>(`/servers/${props.serverId}/replace/check-dns`)
    applyRec(data)
    if (!data.dns_ok) message.info('Домен ещё не указывает на новый IP.')
  } catch (error: any) {
    message.error(error?.response?.data?.detail || 'Проверка DNS не удалась.')
  } finally {
    busy.value = false
  }
}

async function runActivate() {
  activating.value = true
  try {
    const { data } = await api.post<Replacement>(`/servers/${props.serverId}/replace/activate`)
    applyRec(data)
    stopPolling()
    message.success('Вход активирован на новом сервере.')
    emit('replaced')
  } catch (error: any) {
    message.error(error?.response?.data?.detail || 'Активация не удалась.')
    await refreshStatus()
  } finally {
    activating.value = false
  }
}

async function refreshStatus() {
  try {
    const { data } = await api.get<Replacement | null>(`/servers/${props.serverId}/replace/status`)
    if (data) applyRec(data)
  } catch {
    /* ignore */
  }
}

function onCancel() {
  if (stage.value === 'form') {
    close()
    return
  }
  dialog.warning({
    title: 'Прервать замену?',
    content: 'Новый сервер будет очищен. Старый вход останется нетронутым.',
    positiveText: 'Прервать',
    negativeText: 'Назад',
    onPositiveClick: async () => {
      try {
        await api.post(`/servers/${props.serverId}/replace/abort`)
      } catch {
        /* ignore */
      }
      stopPolling()
      close()
    }
  })
}

function finish() {
  stopPolling()
  emit('replaced')
  close()
}

function close() {
  visible.value = false
}

function reset() {
  stage.value = 'intro'
  busy.value = false
  activating.value = false
  rec.value = null
  preflightBlockers.value = []
  preflightWarnings.value = []
  candidates.value = []
  inputMode.value = 'manual'
  selectedCandidateId.value = null
  form.value = { host: '', port: 22, username: 'root', authType: 'password', password: '', key: '' }
}

watch(
  () => props.show,
  async (value) => {
    visible.value = value
    if (value) {
      reset()
      // Возобновляем только незавершённую замену; active/failed/aborted — начинаем заново.
      await refreshStatus()
      const st = rec.value?.status
      const resumable = ['provisioning', 'waiting_dns', 'ready'].includes(st || '')
      if (resumable) {
        stage.value = stageFromStatus(st)
        startPolling()
      } else {
        rec.value = null
        stage.value = 'intro'
      }
    } else {
      stopPolling()
    }
  }
)

watch(visible, (value) => emit('update:show', value))

onUnmounted(stopPolling)
</script>

<style scoped>
.replace-modal {
  width: min(600px, 94vw);
  padding: 18px 20px;
  display: grid;
  gap: 16px;
}

.modal-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.modal-head h3 {
  margin: 0 0 4px;
  font-size: 16px;
}

.modal-head p {
  margin: 0;
  color: var(--color-muted);
  font-size: 12px;
  line-height: 1.5;
}

.form {
  display: grid;
  gap: 12px;
}

.panel-pick {
  display: grid;
  gap: 10px;
}

.form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.field {
  display: grid;
  gap: 6px;
}

.field.span2 {
  grid-column: 1 / -1;
}

.field span {
  font-size: 13px;
}

.result {
  display: grid;
  gap: 14px;
}

.block-list {
  border-radius: 10px;
  padding: 12px 14px;
  font-size: 13px;
  border: 1px solid var(--color-border);
}

.block-list ul {
  margin: 6px 0 0;
  padding-left: 18px;
}

.block-list.danger {
  background: color-mix(in srgb, #e88080 12%, transparent);
  border-color: color-mix(in srgb, #e88080 40%, transparent);
}

.block-list.warn {
  background: color-mix(in srgb, #e8c280 14%, transparent);
  border-color: color-mix(in srgb, #e8c280 45%, transparent);
}

.block-list.ok {
  background: color-mix(in srgb, var(--color-accent) 10%, transparent);
  border-color: color-mix(in srgb, var(--color-accent) 35%, transparent);
}

.status-line {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
}

.status-line.ok {
  color: var(--color-accent);
}

.dns-block {
  border: 1px solid var(--color-border);
  border-radius: 10px;
  padding: 12px 14px;
  display: grid;
  gap: 10px;
}

.dns-title {
  margin: 0;
  font-weight: 700;
  font-size: 13px;
}

.dns-grid {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 6px 12px;
  font-size: 12px;
}

.dns-k {
  color: var(--color-dim);
}

.dns-v.strong {
  font-weight: 700;
}

.dns-flags {
  display: flex;
  gap: 8px;
}

.hint {
  color: var(--color-muted);
  font-size: 12px;
  margin: 0;
}

.mono {
  font-family: var(--font-mono, monospace);
  word-break: break-all;
}

.modal-foot {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.intro {
  display: grid;
  gap: 14px;
}

.intro-icon {
  display: grid;
  place-items: center;
  width: 52px;
  height: 52px;
  border-radius: 14px;
  background: color-mix(in srgb, var(--color-accent) 14%, transparent);
  border: 1px solid color-mix(in srgb, var(--color-accent) 35%, transparent);
  color: var(--color-accent);
}

.intro-lead {
  margin: 0;
  font-size: 14px;
  line-height: 1.55;
  color: var(--color-text);
}

.intro-steps {
  margin: 0;
  padding-left: 20px;
  display: grid;
  gap: 8px;
  font-size: 13px;
  line-height: 1.45;
  color: var(--color-muted);
}

.intro-steps strong {
  color: var(--color-text);
  font-weight: 600;
}

.intro-note {
  padding: 10px 12px;
  border-radius: 8px;
  background: var(--color-surface-2);
  border: 1px solid var(--color-border);
}
</style>
