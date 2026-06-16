<template>
  <n-modal v-model:show="visible" :mask-closable="false">
    <div class="panel migrate-modal">
      <header class="modal-head">
        <div>
          <h3>{{ stage === 'intro' ? 'Полная миграция узла' : 'Перенос панели на новый сервер' }}</h3>
          <p v-if="stage !== 'intro'">
            Переезжает весь стек: VPN, панель, база, клиенты, настройки и секреты. Старый сервер остаётся как резерв.
          </p>
        </div>
        <n-button circle tertiary size="small" @click="close">
          <template #icon><X :size="15" /></template>
        </n-button>
      </header>

      <!-- ШАГ 0: объяснение -->
      <div v-if="stage === 'intro'" class="intro">
        <div class="intro-icon"><ServerCog :size="28" /></div>
        <p class="intro-lead">
          Переносит <strong>всю панель целиком</strong> на новый сервер: базу данных, клиентов,
          конфиги, маскировки, сертификаты и секретный ключ. После переезда домен указывает на новый сервер.
        </p>
        <ol class="intro-steps">
          <li>Выберите сервер из панели или введите SSH чистого VPS</li>
          <li>Панель развернёт на нём копию и зальёт все данные (новый узел в режиме ожидания)</li>
          <li>Меняете <strong>A-запись домена</strong> у регистратора на новый IP</li>
          <li>Нажимаете «Активировать» — старая панель замораживается, новая становится главной</li>
        </ol>
        <p class="hint intro-note">
          Старый сервер <strong>не удаляется</strong> — это резерв на случай отката. Снесёте его вручную,
          когда убедитесь, что всё работает на новом.
        </p>
      </div>

      <!-- ШАГ 1: выбор сервера -->
      <div v-if="stage === 'form'" class="form">
        <div v-if="serverOptions.length" class="field span2">
          <span>Способ выбора сервера</span>
          <n-radio-group v-model:value="inputMode">
            <n-radio value="panel">Из панели</n-radio>
            <n-radio value="manual">Ввести вручную</n-radio>
          </n-radio-group>
        </div>

        <div v-if="inputMode === 'panel' && serverOptions.length" class="panel-pick">
          <label class="field span2">
            <span>Сервер-получатель</span>
            <n-select
              v-model:value="selectedServerId"
              placeholder="Выберите чистый VPS из панели"
              :options="serverOptions"
              :loading="loadingServers"
            />
          </label>
          <p class="hint">Должен быть чистый VPS без работающей панели/VPN — на нём развернётся копия.</p>
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

        <label class="field span2">
          <span>Домен панели (для смены A-записи)</span>
          <n-input v-model:value="form.domain" placeholder="например, uchi-it.ru (можно оставить пустым)" />
        </label>
      </div>

      <!-- PREFLIGHT / провижн / ожидание -->
      <div v-if="['preflight', 'provisioning', 'waiting', 'ready', 'activating', 'active'].includes(stage)" class="result">
        <div class="status-line" :class="statusTone">
          <n-spin v-if="stage === 'provisioning' || stage === 'activating'" size="small" />
          <span>{{ statusText }}</span>
        </div>

        <div v-if="stage === 'preflight'" class="block-list ok">
          <strong>Сервер готов к развёртыванию.</strong>
          <p>Публичный IP: <span class="mono">{{ rec?.new_public_ip || '—' }}</span></p>
        </div>

        <StepsTimeline v-if="rec?.steps?.length" :steps="rec.steps" />

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
            <StatusBadge :label="rec?.health_ok ? 'Новая панель жива' : 'Новая панель не отвечает'" :tone="rec?.health_ok ? 'ok' : 'neutral'" />
          </div>
          <p class="hint">
            После смены A-записи переключение идёт по мере истечения TTL (от пары минут до часа).
          </p>
          <n-checkbox v-model:checked="forceActivate" class="force-box">
            Активировать принудительно (не дожидаясь DNS) — на свой риск
          </n-checkbox>
        </div>

        <div v-if="stage === 'active'" class="block-list ok">
          <strong>Готово. Панель работает на новом сервере.</strong>
          <p>Старый сервер оставлен резервом — снесите его вручную, когда убедитесь, что всё работает.</p>
        </div>

        <div v-if="rec?.error" class="block-list danger">
          <strong>Ошибка:</strong> {{ rec.error }}
        </div>
      </div>

      <footer class="modal-foot">
        <n-button v-if="stage !== 'active' && stage !== 'intro'" tertiary :disabled="busy" @click="onCancel">
          {{ stage === 'form' ? 'Отмена' : 'Прервать миграцию' }}
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
          :disabled="!!rec?.error"
          @click="runProvision"
        >
          Развернуть и перенести данные
        </n-button>

        <n-button v-if="stage === 'waiting' || stage === 'ready'" tertiary :loading="busy" @click="runCheckDns">
          Проверить DNS
        </n-button>
        <n-button
          v-if="stage === 'waiting' || stage === 'ready'"
          type="primary"
          :loading="activating"
          :disabled="!canActivate"
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
import { ServerCog, X } from '@lucide/vue'
import {
  NButton,
  NCheckbox,
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

type Migration = {
  status?: string
  new_host?: string
  new_public_ip?: string | null
  expected_domain?: string | null
  dns_ok?: boolean
  dns_resolved_ips?: string[]
  health_ok?: boolean
  provision_ok?: boolean
  error?: string | null
  steps?: Array<{ name: string; status: string; detail?: string | null }>
}

type ServerOption = { label: string; value: string }
type Stage = 'intro' | 'form' | 'preflight' | 'provisioning' | 'waiting' | 'ready' | 'activating' | 'active'

const props = defineProps<{ show: boolean }>()
const emit = defineEmits<{ 'update:show': [value: boolean]; migrated: [] }>()

const message = useMessage()
const dialog = useDialog()
const visible = ref(props.show)
const stage = ref<Stage>('intro')
const busy = ref(false)
const activating = ref(false)
const forceActivate = ref(false)
const rec = ref<Migration | null>(null)
const serverOptions = ref<ServerOption[]>([])
const loadingServers = ref(false)
const inputMode = ref<'panel' | 'manual'>('manual')
const selectedServerId = ref<string | null>(null)
let pollTimer: ReturnType<typeof setInterval> | null = null

const form = ref({
  host: '',
  port: 22,
  username: 'root',
  authType: 'password' as 'password' | 'key',
  password: '',
  key: '',
  domain: ''
})

const statusText = computed(() => {
  switch (stage.value) {
    case 'preflight':
      return 'Сервер проверен. Можно разворачивать копию панели.'
    case 'provisioning':
      return 'Разворачиваю панель и переношу данные… это может занять несколько минут, не закрывайте окно.'
    case 'waiting':
      return 'Ожидаю смену A-записи домена на новый сервер.'
    case 'ready':
      return 'Домен указывает на новый сервер — можно активировать.'
    case 'activating':
      return 'Активирую новый узел: финальная синхронизация и переключение…'
    case 'active':
      return 'Миграция завершена.'
    default:
      return ''
  }
})

const statusTone = computed(() => {
  if (stage.value === 'ready' || stage.value === 'active') return 'ok'
  if (rec.value?.error) return 'danger'
  return 'neutral'
})

const canActivate = computed(() => forceActivate.value || (!!rec.value?.dns_ok && !!rec.value?.health_ok))

async function loadServers() {
  loadingServers.value = true
  try {
    const { data } = await api.get<Array<{ id: string; name: string; host: string; ssh_port?: number }>>('/servers')
    serverOptions.value = (data || []).map((s) => ({
      label: `${s.name} (${s.host}${s.ssh_port ? ':' + s.ssh_port : ''})`,
      value: s.id
    }))
  } catch {
    serverOptions.value = []
  } finally {
    loadingServers.value = false
  }
}

async function goToForm() {
  stage.value = 'form'
  await loadServers()
  inputMode.value = serverOptions.value.length ? 'panel' : 'manual'
}

function stageFromStatus(status?: string): Stage {
  switch (status) {
    case 'preflight':
    case 'preflight_failed':
      return 'preflight'
    case 'provisioning':
      return 'provisioning'
    case 'waiting_dns':
      return 'waiting'
    case 'ready':
      return 'ready'
    case 'activating':
      return 'activating'
    case 'active':
      return 'active'
    default:
      return stage.value
  }
}

function applyRec(data: Migration | null) {
  rec.value = data
  if (data) stage.value = stageFromStatus(data.status)
}

function startPolling() {
  stopPolling()
  pollTimer = setInterval(async () => {
    try {
      const { data } = await api.get<Migration | null>('/node-migration/status')
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
  const usePanel = inputMode.value === 'panel' && serverOptions.value.length
  if (usePanel && !selectedServerId.value) {
    message.warning('Выберите сервер из панели.')
    return
  }
  if (!usePanel && !form.value.host.trim()) {
    message.warning('Укажите IP нового сервера.')
    return
  }
  busy.value = true
  try {
    const payload: Record<string, unknown> = { expected_domain: form.value.domain.trim() || undefined }
    if (usePanel) {
      payload.source_server_id = selectedServerId.value
    } else {
      payload.new_host = form.value.host.trim()
      payload.ssh_port = form.value.port || 22
      payload.ssh_username = form.value.username.trim() || 'root'
      payload.ssh_password = form.value.authType === 'password' ? form.value.password : undefined
      payload.ssh_key = form.value.authType === 'key' ? form.value.key : undefined
    }
    const { data } = await api.post<Migration>('/node-migration/preflight', payload)
    applyRec(data)
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
    const { data } = await api.post<Migration>('/node-migration/provision', {})
    applyRec(data)
  } catch (error: any) {
    message.error(error?.response?.data?.detail || 'Развёртывание не удалось.')
    await refreshStatus()
  } finally {
    busy.value = false
  }
}

async function runCheckDns() {
  busy.value = true
  try {
    const { data } = await api.post<Migration>('/node-migration/check-dns', {})
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
  stage.value = 'activating'
  startPolling()
  try {
    const { data } = await api.post<Migration>('/node-migration/activate', { force: forceActivate.value })
    applyRec(data)
    message.success('Активация запущена.')
    emit('migrated')
  } catch (error: any) {
    message.error(error?.response?.data?.detail || 'Активация не удалась.')
    await refreshStatus()
  } finally {
    activating.value = false
  }
}

async function refreshStatus() {
  try {
    const { data } = await api.get<Migration | null>('/node-migration/status')
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
    title: 'Прервать миграцию?',
    content: 'Процесс будет сброшен. Старая панель продолжит работать как прежде. Новый сервер придётся очистить вручную.',
    positiveText: 'Прервать',
    negativeText: 'Назад',
    onPositiveClick: async () => {
      try {
        await api.post('/node-migration/abort', {})
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
  emit('migrated')
  close()
}

function close() {
  visible.value = false
}

function reset() {
  stage.value = 'intro'
  busy.value = false
  activating.value = false
  forceActivate.value = false
  rec.value = null
  serverOptions.value = []
  inputMode.value = 'manual'
  selectedServerId.value = null
  form.value = { host: '', port: 22, username: 'root', authType: 'password', password: '', key: '', domain: '' }
}

watch(
  () => props.show,
  async (value) => {
    visible.value = value
    if (value) {
      reset()
      await refreshStatus()
      const st = rec.value?.status
      const resumable = ['preflight', 'provisioning', 'waiting_dns', 'ready', 'activating'].includes(st || '')
      if (resumable) {
        stage.value = stageFromStatus(st)
        if (['provisioning', 'activating'].includes(st || '')) startPolling()
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
.migrate-modal {
  width: min(620px, 94vw);
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

.block-list.danger {
  background: color-mix(in srgb, #e88080 12%, transparent);
  border-color: color-mix(in srgb, #e88080 40%, transparent);
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

.force-box {
  font-size: 12px;
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
