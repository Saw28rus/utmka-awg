<template>
  <div class="ph">
    <p class="ph-lead">
      Эти шаги относятся к <strong>этому серверу</strong>, где крутится панель — не к РУ2 и не к NL.
      VPN на нём не ставится.
    </p>
    <p class="ph-sub">
      Нужны открытые порты <span class="mono">80</span> и <span class="mono">443</span> в кабинете хостера.
    </p>

    <div v-if="loading" class="ph-loading">
      <n-spin size="small" />
      <span>Проверяю состояние…</span>
    </div>

    <div v-else class="sec-steps">
      <div class="sec-step" :class="{ open: openStep === 'ssl' }">
        <button type="button" class="sec-step-head" @click="toggleStep('ssl')">
          <span class="sec-mark" :class="{ done: sslDone }">
            <Check v-if="sslDone" :size="13" />
            <template v-else>1</template>
          </span>
          <span class="sec-step-name">Вход в панель по HTTPS</span>
          <span class="sec-step-note" :class="{ ok: sslDone }">
            {{ sslDone ? sslStatus?.domain : 'домен не привязан' }}
          </span>
          <ChevronDown :size="15" class="sec-chev" />
        </button>
        <div v-show="openStep === 'ssl'" class="sec-step-body">
          <template v-if="sslDone">
            <div class="ssl-active">
              <Globe :size="15" />
              <a :href="sslStatus?.url || '#'" target="_blank" rel="noopener" class="ssl-link">{{ sslStatus?.url }}</a>
              <span v-if="sslStatus?.cert_expires_at" class="ssl-expiry mono">до {{ sslStatus.cert_expires_at }}</span>
            </div>
            <p class="sec-note">
              Запасной вход:
              <span class="mono">{{ sslStatus?.fallback_url || fallbackGuess }}</span>
            </p>
            <n-button size="small" tertiary type="warning" :loading="sslBusy === 'rollback'" :disabled="!!sslBusy" @click="rollbackSsl">
              Откатить HTTPS
            </n-button>
          </template>
          <template v-else>
            <ol class="sec-howto">
              <li>
                A-запись на IP панели
                <span class="mono">{{ publicIp || '…' }}</span>
                <n-button v-if="publicIp" size="tiny" quaternary @click="copyText(publicIp)">копировать</n-button>
              </li>
              <li>Введите домен и нажмите «Подключить» — сертификат Let's Encrypt выпустится сам.</li>
            </ol>
            <div class="sec-row">
              <n-input v-model:value="sslDomain" placeholder="panel.example.com" :disabled="!!sslBusy" />
              <n-button type="primary" :loading="sslBusy === 'verify' || sslBusy === 'install'" @click="connectSsl">
                Подключить
              </n-button>
            </div>
            <div class="ssl-nodomain">
              <span class="ssl-nodomain-or">или без своего домена</span>
              <n-button size="small" tertiary :loading="sslBusy === 'auto'" :disabled="!!sslBusy" @click="connectSslAuto">
                <template #icon><ShieldCheck :size="14" /></template>
                HTTPS без домена (sslip.io)
              </n-button>
              <p class="sec-note">
                Выпустит доверенный сертификат на
                <span class="mono">{{ sslipHost }}</span>
              </p>
            </div>
            <p v-if="sslBusy === 'install' || sslBusy === 'auto'" class="sec-note">Выпускаю сертификат — 1–2 минуты, не закрывайте страницу.</p>
            <p v-if="sslVerifyMessage && !sslVerifyOk" class="ssl-verify">{{ sslVerifyMessage }}</p>
            <p v-if="sslStatus?.message" class="ssl-verify">{{ sslStatus.message }}</p>
          </template>
        </div>
      </div>

      <div class="sec-step" :class="{ open: openStep === 'harden', locked: !sslDone }">
        <button type="button" class="sec-step-head" @click="toggleStep('harden')">
          <span class="sec-mark" :class="{ done: hardenDone }">
            <Check v-if="hardenDone" :size="13" />
            <Lock v-else-if="!sslDone" :size="12" />
            <template v-else>2</template>
          </span>
          <span class="sec-step-name">Закрыть запасной вход :8080</span>
          <span class="sec-step-note" :class="{ ok: hardenDone }">
            {{ !sslDone ? 'после шага 1' : hardenNote }}
          </span>
          <ChevronDown :size="15" class="sec-chev" />
        </button>
        <div v-show="openStep === 'harden'" class="sec-step-body">
          <p class="sec-note">
            Панель останется по HTTPS. Адрес <span class="mono">:8080</span> откроется только с указанных IP.
          </p>
          <div class="sec-row">
            <n-input v-model:value="hardenIpsText" :placeholder="hardenStatus?.your_ip || '203.0.113.10'" :disabled="!!hardenBusy" />
            <n-button type="primary" :loading="hardenBusy === 'apply'" :disabled="!!hardenBusy" @click="applyHarden">
              {{ hardenStatus?.enabled ? 'Обновить' : 'Закрыть' }}
            </n-button>
          </div>
          <p v-if="hardenStatus?.your_ip" class="sec-note">
            Ваш IP сейчас: <span class="mono">{{ hardenStatus.your_ip }}</span>
          </p>
          <n-button
            v-if="hardenStatus?.enabled"
            size="small"
            tertiary
            type="warning"
            :loading="hardenBusy === 'disable'"
            :disabled="!!hardenBusy"
            @click="disableHarden"
          >
            Снова открыть всем
          </n-button>
          <p v-if="hardenStatus?.message" class="ssl-verify">{{ hardenStatus.message }}</p>
        </div>
      </div>

      <div class="sec-step" :class="{ open: openStep === 'chat', locked: !hardenDone }">
        <button type="button" class="sec-step-head" @click="toggleStep('chat')">
          <span class="sec-mark" :class="{ done: chatDone }">
            <Check v-if="chatDone" :size="13" />
            <Lock v-else-if="!hardenDone" :size="12" />
            <template v-else>3</template>
          </span>
          <span class="sec-step-name">Чат с клиентами</span>
          <span class="sec-step-note" :class="{ ok: chatDone }">
            {{ !hardenDone ? 'после шага 2' : chatDone ? chatStatus?.domain : 'не подключён' }}
          </span>
          <ChevronDown :size="15" class="sec-chev" />
        </button>
        <div v-show="openStep === 'chat'" class="sec-step-body">
          <template v-if="chatDone">
            <div class="ssl-active">
              <Globe :size="15" />
              <a :href="chatStatus?.public_url || '#'" target="_blank" rel="noopener" class="ssl-link">{{ chatStatus?.public_url }}</a>
            </div>
            <p class="sec-note">Через этот адрес доступен только чат — админка закрыта.</p>
            <n-button size="small" tertiary type="warning" :loading="chatBusy === 'disable'" :disabled="!!chatBusy" @click="disableChat">
              Отключить чат-домен
            </n-button>
          </template>
          <template v-else>
            <p class="sec-note">
              Отдельный адрес, где клиент получит ключ, даже когда VPN не работает.
            </p>
            <ol class="sec-howto">
              <li>
                A-запись поддомена (например <span class="mono">chat</span>) на
                <span class="mono">{{ publicIp || '…' }}</span>
              </li>
              <li>Введите поддомен и нажмите «Подключить».</li>
            </ol>
            <div class="sec-row">
              <n-input v-model:value="chatDomain" placeholder="chat.example.com" :disabled="!!chatBusy" />
              <n-button type="primary" :loading="chatBusy === 'verify' || chatBusy === 'install'" @click="connectChat">
                Подключить
              </n-button>
            </div>
            <div class="ssl-nodomain">
              <span class="ssl-nodomain-or">или без своего домена</span>
              <n-button size="small" tertiary :loading="chatBusy === 'auto'" :disabled="!!chatBusy" @click="connectChatAuto">
                <template #icon><ShieldCheck :size="14" /></template>
                Чат без домена (sslip.io)
              </n-button>
              <p class="sec-note">
                Адрес вида <span class="mono">{{ chatSslipHost }}</span>
              </p>
            </div>
            <p v-if="chatBusy === 'install' || chatBusy === 'auto'" class="sec-note">Выпускаю сертификат — 1–2 минуты.</p>
            <p v-if="chatVerifyMessage && !chatVerifyOk" class="ssl-verify">{{ chatVerifyMessage }}</p>
          </template>
          <p v-if="chatStatus?.message" class="ssl-verify">{{ chatStatus.message }}</p>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { Check, ChevronDown, Globe, Lock, ShieldCheck } from '@lucide/vue'
import { NButton, NInput, NSpin, useMessage } from 'naive-ui'
import { computed, onMounted, ref } from 'vue'

import { api } from '@/api/client'
import { useIntegrationsStore } from '@/stores/integrations'

type SslStatus = {
  domain?: string | null
  url?: string | null
  status?: string
  public_ip?: string | null
  fallback_url?: string | null
  cert_expires_at?: string | null
  message?: string | null
}

type HardenStatus = {
  enabled?: boolean
  allowed_ips?: string[]
  your_ip?: string | null
  message?: string | null
}

type ChatStatus = {
  domain?: string | null
  enabled?: boolean
  public_url?: string | null
  server_public_ip?: string | null
  message?: string | null
}

const BASE = '/settings/panel-host'
const message = useMessage()
const integrations = useIntegrationsStore()

const loading = ref(true)
const sslStatus = ref<SslStatus | null>(null)
const hardenStatus = ref<HardenStatus | null>(null)
const chatStatus = ref<ChatStatus | null>(null)
const sslDomain = ref('')
const chatDomain = ref('')
const hardenIpsText = ref('')
const sslVerifyMessage = ref('')
const sslVerifyOk = ref(false)
const chatVerifyMessage = ref('')
const chatVerifyOk = ref(false)
const sslBusy = ref<'verify' | 'install' | 'auto' | 'rollback' | ''>('')
const hardenBusy = ref<'apply' | 'disable' | ''>('')
const chatBusy = ref<'verify' | 'install' | 'auto' | 'disable' | ''>('')
const openStep = ref<'' | 'ssl' | 'harden' | 'chat'>('')

const sslDone = computed(() => sslStatus.value?.status === 'active')
const hardenDone = computed(() => !!hardenStatus.value?.enabled)
const chatDone = computed(() => !!chatStatus.value?.enabled)
const publicIp = computed(() => sslStatus.value?.public_ip || chatStatus.value?.server_public_ip || '')
const sslipHost = computed(() => (publicIp.value ? `${publicIp.value}.sslip.io` : 'IP.sslip.io'))
const chatSslipHost = computed(() => (publicIp.value ? `chat.${publicIp.value}.sslip.io` : 'chat.IP.sslip.io'))
const fallbackGuess = computed(() => (publicIp.value ? `http://${publicIp.value}:8080` : 'http://IP:8080'))
const hardenNote = computed(() => {
  if (!hardenDone.value) return 'открыт всем'
  const ips = hardenStatus.value?.allowed_ips || []
  return ips.length ? `только ${ips.join(', ')}` : 'закрыт'
})

function errText(error: unknown) {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (error instanceof Error) return error.message
  return 'Ошибка'
}

function toggleStep(step: 'ssl' | 'harden' | 'chat') {
  if (step === 'harden' && !sslDone.value) {
    message.info('Сначала шаг 1 — HTTPS.')
    return
  }
  if (step === 'chat' && !hardenDone.value) {
    message.info('Сначала шаги 1 и 2.')
    return
  }
  openStep.value = openStep.value === step ? '' : step
}

function pickOpenStep() {
  if (!sslDone.value) openStep.value = 'ssl'
  else if (!hardenDone.value) openStep.value = 'harden'
  else if (!chatDone.value) openStep.value = 'chat'
  else openStep.value = ''
}

async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text)
    message.success('Скопировано')
  } catch {
    message.error('Не удалось скопировать')
  }
}

async function loadAll() {
  loading.value = true
  try {
    const [ssl, harden, chat] = await Promise.all([
      api.get<SslStatus>(`${BASE}/ssl/status`),
      api.get<HardenStatus>(`${BASE}/harden/status`),
      api.get<ChatStatus>(`${BASE}/chat-domain/status`)
    ])
    sslStatus.value = ssl.data
    hardenStatus.value = harden.data
    chatStatus.value = chat.data
    if (harden.data.allowed_ips?.length) hardenIpsText.value = harden.data.allowed_ips.join(', ')
    else if (harden.data.your_ip) hardenIpsText.value = harden.data.your_ip
    pickOpenStep()
  } catch (error) {
    message.error(errText(error))
  } finally {
    loading.value = false
  }
}

async function connectSsl() {
  sslBusy.value = 'verify'
  sslVerifyOk.value = false
  try {
    const { data } = await api.post(`${BASE}/ssl/verify`, { domain: sslDomain.value.trim() })
    sslVerifyMessage.value = data.message
    sslVerifyOk.value = data.ok
    if (!data.ok) return
    sslBusy.value = 'install'
    const installed = await api.post(`${BASE}/ssl/install`, { domain: sslDomain.value.trim() }, { timeout: 660_000 })
    message.success(installed.data.message)
    await loadAll()
  } catch (error) {
    sslVerifyMessage.value = errText(error)
    message.error(sslVerifyMessage.value)
  } finally {
    sslBusy.value = ''
  }
}

async function connectSslAuto() {
  sslBusy.value = 'auto'
  try {
    const { data } = await api.post(`${BASE}/ssl/install-auto`, {}, { timeout: 660_000 })
    message.success(data.message)
    await loadAll()
  } catch (error) {
    message.error(errText(error))
  } finally {
    sslBusy.value = ''
  }
}

async function rollbackSsl() {
  if (!confirm('Откатить HTTPS на хосте панели?')) return
  sslBusy.value = 'rollback'
  try {
    const { data } = await api.post(`${BASE}/ssl/rollback`)
    message.success(data.message || 'Откатил')
    await loadAll()
  } catch (error) {
    message.error(errText(error))
  } finally {
    sslBusy.value = ''
  }
}

async function applyHarden() {
  hardenBusy.value = 'apply'
  try {
    const ips = hardenIpsText.value.split(/[,\s]+/).map((s) => s.trim()).filter(Boolean)
    const { data } = await api.post(`${BASE}/harden/apply`, { allowed_ips: ips })
    message.success(data.message)
    await loadAll()
  } catch (error) {
    message.error(errText(error))
  } finally {
    hardenBusy.value = ''
  }
}

async function disableHarden() {
  if (!confirm('Открыть :8080 всем в интернете?')) return
  hardenBusy.value = 'disable'
  try {
    const { data } = await api.post(`${BASE}/harden/disable`)
    message.success(data.message)
    await loadAll()
  } catch (error) {
    message.error(errText(error))
  } finally {
    hardenBusy.value = ''
  }
}

async function connectChat() {
  chatBusy.value = 'verify'
  chatVerifyOk.value = false
  try {
    const { data } = await api.post(`${BASE}/chat-domain/verify`, { domain: chatDomain.value.trim() })
    chatVerifyMessage.value = data.message
    chatVerifyOk.value = data.ok
    if (!data.ok) return
    chatBusy.value = 'install'
    const installed = await api.post(
      `${BASE}/chat-domain/install`,
      { domain: chatDomain.value.trim() },
      { timeout: 660_000 }
    )
    message.success(installed.data.message)
    await integrations.load()
    await loadAll()
  } catch (error) {
    chatVerifyMessage.value = errText(error)
    message.error(chatVerifyMessage.value)
  } finally {
    chatBusy.value = ''
  }
}

async function connectChatAuto() {
  chatBusy.value = 'auto'
  try {
    const { data } = await api.post(`${BASE}/chat-domain/install-auto`, {}, { timeout: 660_000 })
    message.success(data.message)
    await integrations.load()
    await loadAll()
  } catch (error) {
    message.error(errText(error))
  } finally {
    chatBusy.value = ''
  }
}

async function disableChat() {
  if (!confirm('Отключить чат-домен?')) return
  chatBusy.value = 'disable'
  try {
    const { data } = await api.post(`${BASE}/chat-domain/disable`)
    message.success(data.message || 'Отключён')
    await integrations.load()
    await loadAll()
  } catch (error) {
    message.error(errText(error))
  } finally {
    chatBusy.value = ''
  }
}

onMounted(loadAll)
</script>

<style scoped>
.ph-lead,
.ph-sub {
  margin: 0 0 10px;
  color: var(--color-muted);
  font-size: 13px;
  line-height: 1.45;
}
.ph-lead { color: inherit; }
.ph-loading {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 16px 0;
  color: var(--color-muted);
}
.sec-steps { display: flex; flex-direction: column; gap: 8px; }
.sec-step {
  border: 1px solid var(--color-border);
  border-radius: 10px;
  overflow: hidden;
}
.sec-step-head {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 12px 14px;
  background: none;
  border: 0;
  cursor: pointer;
  color: inherit;
  font: inherit;
  text-align: left;
}
.sec-step.locked .sec-step-head { cursor: not-allowed; }
.sec-step.locked .sec-step-name,
.sec-step.locked .sec-mark { opacity: 0.5; }
.sec-mark {
  flex: none;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 600;
  border: 1px solid var(--color-border);
  color: var(--color-muted);
}
.sec-mark.done {
  border-color: var(--color-accent);
  color: var(--color-accent);
}
.sec-step-name { font-weight: 600; font-size: 14px; }
.sec-step-note {
  margin-left: auto;
  font-size: 12.5px;
  color: var(--color-muted);
  max-width: 42%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.sec-step-note.ok { color: var(--color-accent); }
.sec-chev { flex: none; opacity: 0.5; }
.sec-step.open .sec-chev { transform: rotate(180deg); }
.sec-step-body {
  padding: 0 14px 14px;
  display: grid;
  gap: 10px;
}
.sec-howto {
  margin: 0;
  padding-left: 18px;
  color: var(--color-muted);
  font-size: 13px;
  display: grid;
  gap: 6px;
}
.sec-row { display: flex; gap: 8px; }
.sec-note { margin: 0; color: var(--color-muted); font-size: 12.5px; }
.ssl-active {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.ssl-link { color: var(--color-accent); }
.ssl-expiry { font-size: 12px; color: var(--color-muted); }
.ssl-nodomain {
  display: grid;
  gap: 8px;
  padding-top: 4px;
}
.ssl-nodomain-or {
  font-size: 12px;
  color: var(--color-muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.ssl-verify { margin: 0; color: var(--color-warning); font-size: 13px; white-space: pre-wrap; }
.mono { font-family: var(--font-mono, ui-monospace, monospace); }
</style>
