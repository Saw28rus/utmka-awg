<template>
  <AppShell title="Настройки" eyebrow="Параметры панели">
    <div v-if="loading" class="panel placeholder">
      <n-spin size="small" />
      <span>Загружаю настройки…</span>
    </div>

    <div v-else class="settings-layout">
      <nav class="settings-nav panel">
        <button
          v-for="item in sections"
          :key="item.id"
          type="button"
          class="nav-item"
          :class="{ active: activeSection === item.id }"
          @click="activeSection = item.id"
        >
          <component :is="item.icon" :size="16" />
          <span>{{ item.label }}</span>
        </button>
        <p v-if="settings" class="nav-version mono">v{{ settings.panel_version }}</p>
      </nav>

      <div class="settings-content panel">
        <header class="section-head">
          <div>
            <h2>{{ activeMeta.title }}</h2>
            <p>{{ activeMeta.desc }}</p>
          </div>
        </header>

        <!-- Безопасность -->
        <section v-if="activeSection === 'security'" class="section-body">
          <div class="field-group">
            <h3>Смена пароля</h3>
            <label class="field">
              <span>Текущий пароль</span>
              <n-input v-model:value="pwd.old" type="password" show-password-on="click" />
            </label>
            <label class="field">
              <span>Новый пароль (мин. 12 символов)</span>
              <n-input v-model:value="pwd.new" type="password" show-password-on="click" />
            </label>
            <div class="actions">
              <n-button :loading="savingPwd" @click="changePassword">Сменить пароль</n-button>
            </div>
          </div>
          <div class="field-group">
            <h3>Сессии</h3>
            <div class="row-2">
              <label class="field">
                <span>Access token, мин</span>
                <n-input-number v-model:value="form.access_token_minutes" :min="5" :max="1440" />
              </label>
              <label class="field">
                <span>Refresh token, дней</span>
                <n-input-number v-model:value="form.refresh_token_days" :min="1" :max="90" />
              </label>
            </div>
            <div class="actions">
              <n-button :loading="savingGeneral" @click="saveGeneral">Сохранить</n-button>
            </div>
          </div>
        </section>

        <!-- AWG -->
        <section v-else-if="activeSection === 'awg'" class="section-body">
          <label class="field"><span>DNS</span><n-input v-model:value="form.default_dns" /></label>
          <label class="field"><span>Subnet</span><n-input v-model:value="form.default_subnet" /></label>
          <div class="row-2">
            <label class="field">
              <span>UDP min</span>
              <n-input-number v-model:value="form.default_udp_port_min" :min="1" :max="65535" />
            </label>
            <label class="field">
              <span>UDP max</span>
              <n-input-number v-model:value="form.default_udp_port_max" :min="1" :max="65535" />
            </label>
          </div>
          <div class="actions">
            <n-button type="primary" :loading="savingGeneral" @click="saveGeneral">Сохранить defaults</n-button>
          </div>
        </section>

        <!-- Интеграции -->
        <section v-else-if="activeSection === 'integrations'" class="section-body section-body--wide">
          <div v-if="integrationTabs.length > 1" class="integration-tabs">
            <button
              v-for="item in integrationTabs"
              :key="item.id"
              type="button"
              class="integration-tab"
              :class="{ active: activeIntegration === item.id }"
              @click="activeIntegration = item.id"
            >
              <component :is="item.icon" :size="15" />
              <span>{{ item.label }}</span>
              <span v-if="item.badge" class="integration-tab-badge">{{ item.badge }}</span>
            </button>
          </div>

          <div v-if="activeIntegration === 'yookassa'" class="integration-panel">
            <div class="yookassa-head" :class="{ connected: yookassaStatus?.connected && !yookassaEditing }">
              <div class="yookassa-head-icon">
                <CreditCard :size="28" />
              </div>
              <div>
                <h3>{{ yookassaStatus?.connected && !yookassaEditing ? 'ЮKassa подключена' : 'Подключите ЮKassa' }}</h3>
                <p class="hint">
                  {{
                    yookassaStatus?.connected && !yookassaEditing
                      ? 'Ключи проверены и сохранены в зашифрованном виде.'
                      : 'Введите shop ID и секретный ключ из личного кабинета ЮKassa.'
                  }}
                </p>
              </div>
            </div>

            <div
              v-if="yookassaStatus?.connected && !yookassaEditing"
              class="yookassa-connected"
            >
              <div class="yookassa-info-row">
                <span>Магазин (shop ID)</span>
                <strong class="mono">{{ yookassaStatus.shop_id }}</strong>
              </div>
              <div class="yookassa-info-row">
                <span>Секретный ключ</span>
                <strong class="mono">{{ yookassaStatus.secret_key_masked }}</strong>
              </div>
              <div class="actions">
                <n-button @click="startYookassaEdit">Изменить</n-button>
                <n-popconfirm @positive-click="disconnectYookassa">
                  <template #trigger>
                    <n-button type="error" tertiary :loading="disconnectingYookassa">Отключить</n-button>
                  </template>
                  Ключи будут удалены из панели. Подключить заново можно в любой момент.
                </n-popconfirm>
              </div>
            </div>

            <template v-else>
              <label class="field">
                <span>Shop ID</span>
                <n-input v-model:value="yookassaShopId" placeholder="123456" />
              </label>
              <label class="field">
                <span>Секретный ключ</span>
                <n-input
                  v-model:value="yookassaSecretKey"
                  type="password"
                  show-password-on="click"
                  placeholder="live_... или test_..."
                />
              </label>
              <div class="actions">
                <n-button
                  type="primary"
                  :loading="connectingYookassa"
                  @click="connectYookassa"
                >
                  Проверить и сохранить
                </n-button>
                <n-button
                  v-if="yookassaStatus?.connected && yookassaEditing"
                  tertiary
                  @click="cancelYookassaEdit"
                >
                  Отмена
                </n-button>
              </div>
            </template>

            <p class="hint yookassa-security">
              Секретный ключ хранится в базе в зашифрованном виде. Перед сохранением панель
              проверяет ключи запросом к API ЮKassa.
            </p>
          </div>
        </section>

        <!-- Данные -->
        <section v-else-if="activeSection === 'data'" class="section-body">
          <div class="action-list">
            <div class="action-row">
              <div>
                <strong>Резервная копия</strong>
                <p class="hint">Серверы, клиенты, каскад и пользователи в ZIP.</p>
              </div>
              <div class="action-btns">
                <n-button @click="downloadBackup(false)">Скачать</n-button>
                <n-button tertiary @click="downloadBackup(true)">С .env</n-button>
              </div>
            </div>
            <div class="action-row">
              <div>
                <strong>Восстановление</strong>
                <p class="hint">Заменит текущие данные. Перед этим сделай бэкап.</p>
              </div>
              <label class="upload">
                <input type="file" accept=".zip" @change="onRestoreFile" />
                <n-button :loading="restoring" type="warning">Восстановить из ZIP</n-button>
              </label>
            </div>
          </div>
        </section>

        <!-- Обновления -->
        <section v-else-if="activeSection === 'updates'" class="section-body">
          <p class="status-line">{{ updateInfo?.message || 'Нажми «Проверить обновление».' }}</p>
          <p v-if="settings" class="mono hint">
            Текущая: {{ updateInfo?.current || settings.panel_version }}
            <template v-if="updateInfo?.available && updateInfo?.latest"> → {{ updateInfo.latest }}</template>
          </p>
          <div class="actions">
            <n-button :loading="checkingUpdate" :disabled="panelUpdate.isRunning" @click="checkUpdate">
              Проверить обновление
            </n-button>
            <n-button
              type="primary"
              :disabled="!updateInfo?.available || !settings?.update_capable || panelUpdate.isRunning"
              :loading="applyingUpdate"
              @click="applyUpdate"
            >
              Обновить
            </n-button>
          </div>
          <p v-if="panelUpdate.isRunning" class="hint">
            Идёт обновление — панель заблокирована. Отменить можно в окне прогресса.
          </p>
          <p v-if="!settings?.update_capable" class="warn">
            Обновление из UI недоступно: нужны Docker на хосте, смонтированный docker.sock и CLI docker в backend.
            На VPS один раз: <code>cd /opt/utmka-awg && git pull && docker compose up -d</code>
          </p>
        </section>

        <!-- Журнал -->
        <section v-else-if="activeSection === 'audit'" class="section-body section-body--table">
          <n-data-table :columns="auditColumns" :data="auditItems" :bordered="false" size="small" />
        </section>
      </div>
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import {
  CreditCard,
  Database,
  Download,
  Link2,
  ScrollText,
  Shield,
  SlidersHorizontal
} from '@lucide/vue'
import {
  NButton,
  NDataTable,
  NInput,
  NInputNumber,
  NPopconfirm,
  NSpin,
  useMessage
} from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { computed, onMounted, ref, watch } from 'vue'

import { api } from '@/api/client'
import { onRevisit } from '@/composables/useRevisit'
import AppShell from '@/layouts/AppShell.vue'
import { useIntegrationsStore } from '@/stores/integrations'
import { usePanelStore } from '@/stores/panel'
import { usePanelUpdateStore } from '@/stores/panelUpdate'

defineOptions({ name: 'SettingsView' })

type SectionId =
  | 'security'
  | 'awg'
  | 'integrations'
  | 'data'
  | 'updates'
  | 'audit'

type IntegrationId = 'yookassa'

type SettingsData = {
  app_name: string
  default_dns: string
  default_subnet: string
  default_udp_port_min: number
  default_udp_port_max: number
  access_token_minutes: number
  refresh_token_days: number
  maintenance_mode: boolean
  panel_version: string
  update_capable: boolean
}

type UpdateCheck = {
  current: string
  latest?: string | null
  available?: boolean | null
  message: string
  capable: boolean
}

type AuditItem = {
  id: string
  user_email?: string
  action: string
  target_type?: string
  target_id?: string
  created_at: string
}

type YooKassaStatus = {
  connected: boolean
  shop_id?: string | null
  secret_key_masked?: string | null
}

const sections: { id: SectionId; label: string; title: string; desc: string; icon: object }[] = [
  { id: 'security', label: 'Безопасность', title: 'Безопасность', desc: 'Пароль и параметры сессий.', icon: Shield },
  { id: 'awg', label: 'AWG defaults', title: 'AWG по умолчанию', desc: 'Значения для wizard установки протокола.', icon: SlidersHorizontal },
  { id: 'integrations', label: 'Интеграции', title: 'Интеграции', desc: 'Платежи и подключение внешних сервисов.', icon: Link2 },
  { id: 'data', label: 'Данные', title: 'Резервные копии', desc: 'Экспорт и восстановление данных панели.', icon: Database },
  { id: 'updates', label: 'Обновления', title: 'Обновления', desc: 'Проверка и установка новой версии.', icon: Download },
  { id: 'audit', label: 'Журнал', title: 'Журнал действий', desc: 'Кто и что делал в панели.', icon: ScrollText }
]

const message = useMessage()
const panel = usePanelStore()
const panelUpdate = usePanelUpdateStore()
const integrations = useIntegrationsStore()

const integrationTabs = computed(() => [
  {
    id: 'yookassa' as IntegrationId,
    label: 'ЮKassa',
    icon: CreditCard,
    badge: yookassaStatus.value?.connected ? 'Подключено' : undefined
  }
])

const activeSection = ref<SectionId>('security')
const activeIntegration = ref<IntegrationId>('yookassa')
const loading = ref(true)
const settings = ref<SettingsData | null>(null)
const form = ref({
  default_dns: '',
  default_subnet: '',
  default_udp_port_min: 1024,
  default_udp_port_max: 9999,
  access_token_minutes: 15,
  refresh_token_days: 7
})
const pwd = ref({ old: '', new: '' })
const yookassaStatus = ref<YooKassaStatus | null>(null)
const yookassaShopId = ref('')
const yookassaSecretKey = ref('')
const yookassaEditing = ref(false)
const connectingYookassa = ref(false)
const disconnectingYookassa = ref(false)
const savingGeneral = ref(false)
const savingPwd = ref(false)
const restoring = ref(false)
const checkingUpdate = ref(false)
const applyingUpdate = ref(false)
const updateInfo = ref<UpdateCheck | null>(null)
const auditItems = ref<AuditItem[]>([])

const activeMeta = computed(() => {
  const found = sections.find((s) => s.id === activeSection.value)
  return found ?? sections[0]
})

const auditColumns: DataTableColumns<AuditItem> = [
  { title: 'Время', key: 'created_at', render: (r) => r.created_at.replace('T', ' ').slice(0, 19) },
  { title: 'Кто', key: 'user_email' },
  { title: 'Действие', key: 'action' },
  { title: 'Объект', key: 'target_id' }
]

onMounted(async () => {
  await loadAll()
  loading.value = false
})

onRevisit(() => void loadAll())

watch(
  () => panelUpdate.status,
  async (status, prev) => {
    if (prev !== 'running') return
    if (status === 'failed_manual') {
      message.error('Обновление не удалось. Попробуйте позже или через SSH.')
      await checkUpdate()
    }
    if (status === 'rolled_back') {
      message.warning('Обновление откатилось — панель на прежней версии.')
      await checkUpdate()
    }
  }
)

watch(activeSection, (section) => {
  if (section === 'integrations') {
    void loadYookassaStatus()
  }
})

async function loadYookassaStatus() {
  const { data } = await api.get<YooKassaStatus>('/settings/yookassa')
  yookassaStatus.value = data
}

function startYookassaEdit() {
  yookassaEditing.value = true
  yookassaShopId.value = yookassaStatus.value?.shop_id ?? ''
  yookassaSecretKey.value = ''
}

function cancelYookassaEdit() {
  yookassaEditing.value = false
  yookassaShopId.value = ''
  yookassaSecretKey.value = ''
}

async function connectYookassa() {
  const shopId = yookassaShopId.value.trim()
  const secretKey = yookassaSecretKey.value.trim()
  if (!shopId || !secretKey) {
    message.warning('Укажите shop ID и секретный ключ.')
    return
  }
  connectingYookassa.value = true
  try {
    const { data } = await api.post<YooKassaStatus>('/settings/yookassa/connect', {
      shop_id: shopId,
      secret_key: secretKey
    })
    yookassaStatus.value = data
    yookassaEditing.value = false
    yookassaSecretKey.value = ''
    integrations.setYookassaConnected(Boolean(data.connected))
    message.success('ЮKassa подключена.')
    const { data: audit } = await api.get<{ items: AuditItem[] }>('/audit', { params: { limit: 30 } })
    auditItems.value = audit.items
  } catch (e: unknown) {
    const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    message.error(typeof detail === 'string' ? detail : 'Не удалось подключить ЮKassa.')
  } finally {
    connectingYookassa.value = false
  }
}

async function disconnectYookassa() {
  disconnectingYookassa.value = true
  try {
    const { data } = await api.delete<YooKassaStatus>('/settings/yookassa/disconnect')
    yookassaStatus.value = data
    yookassaEditing.value = false
    yookassaShopId.value = ''
    yookassaSecretKey.value = ''
    integrations.setYookassaConnected(Boolean(data.connected))
    message.success('ЮKassa отключена.')
    const { data: audit } = await api.get<{ items: AuditItem[] }>('/audit', { params: { limit: 30 } })
    auditItems.value = audit.items
  } catch {
    message.error('Не удалось отключить ЮKassa.')
  } finally {
    disconnectingYookassa.value = false
  }
}

async function loadAll() {
  const [{ data: s }, { data: audit }] = await Promise.all([
    api.get<SettingsData>('/settings'),
    api.get<{ items: AuditItem[] }>('/audit', { params: { limit: 30 } })
  ])
  settings.value = s
  await panelUpdate.resume()
  form.value = {
    default_dns: s.default_dns,
    default_subnet: s.default_subnet,
    default_udp_port_min: s.default_udp_port_min,
    default_udp_port_max: s.default_udp_port_max,
    access_token_minutes: s.access_token_minutes,
    refresh_token_days: s.refresh_token_days
  }
  auditItems.value = audit.items
  panel.setAppName(s.app_name)
  if (activeSection.value === 'integrations') {
    await loadYookassaStatus()
  }
}

async function saveGeneral() {
  savingGeneral.value = true
  try {
    const { data } = await api.patch<SettingsData>('/settings', form.value)
    settings.value = data
    message.success('Настройки сохранены.')
  } finally {
    savingGeneral.value = false
  }
}

async function changePassword() {
  if (pwd.value.new.length < 12) {
    message.warning('Новый пароль — минимум 12 символов.')
    return
  }
  savingPwd.value = true
  try {
    await api.post('/settings/change-password', {
      old_password: pwd.value.old,
      new_password: pwd.value.new
    })
    pwd.value = { old: '', new: '' }
    message.success('Пароль изменён.')
  } catch (e: unknown) {
    const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    message.error(typeof detail === 'string' ? detail : 'Не удалось сменить пароль.')
  } finally {
    savingPwd.value = false
  }
}

async function downloadBackup(includeSecrets: boolean) {
  const { data } = await api.get('/settings/backup', {
    params: { include_secrets: includeSecrets },
    responseType: 'blob'
  })
  const url = URL.createObjectURL(data)
  const a = document.createElement('a')
  a.href = url
  a.download = 'utmka-panel-backup.zip'
  a.click()
  URL.revokeObjectURL(url)
}

async function onRestoreFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  if (!confirm('Восстановление заменит текущие данные. Продолжить?')) return
  restoring.value = true
  try {
    const body = new FormData()
    body.append('file', file)
    await api.post('/settings/restore', body, {
      headers: { 'Content-Type': 'multipart/form-data' }
    })
    message.success('Данные восстановлены. Перезагрузи страницу.')
    await loadAll()
  } catch (e: unknown) {
    const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    message.error(typeof detail === 'string' ? detail : 'Ошибка восстановления.')
  } finally {
    restoring.value = false
    input.value = ''
  }
}

async function checkUpdate() {
  checkingUpdate.value = true
  try {
    const { data } = await api.get<UpdateCheck>('/settings/updates/check')
    updateInfo.value = data
    message.info(data.message)
  } finally {
    checkingUpdate.value = false
  }
}

async function applyUpdate() {
  if (!confirm('Панель перезапустится на 1–3 минуты. VPN на серверах не остановится. Продолжить?')) return
  applyingUpdate.value = true
  try {
    await panelUpdate.apply()
  } catch (e: unknown) {
    const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    message.error(typeof detail === 'string' ? detail : 'Не удалось запустить обновление.')
  } finally {
    applyingUpdate.value = false
  }
}
</script>

<style scoped>
.settings-layout {
  display: grid;
  grid-template-columns: 220px minmax(0, 1fr);
  gap: 14px;
  align-items: start;
}

.settings-nav {
  display: grid;
  gap: 2px;
  padding: 8px;
  position: sticky;
  top: 12px;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 9px 12px;
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-muted);
  font-size: 13px;
  text-align: left;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}

.nav-item:hover {
  background: var(--color-surface-2);
  color: var(--color-text);
}

.nav-item.active {
  background: var(--color-accent-soft);
  color: var(--color-text);
}

.nav-item svg {
  flex-shrink: 0;
  color: var(--color-accent);
}

.nav-version {
  margin: 10px 4px 4px;
  color: var(--color-dim);
  font-size: 11px;
}

.settings-content {
  padding: 0;
  overflow: hidden;
}

.section-head {
  padding: 18px 22px 14px;
  border-bottom: 1px solid var(--color-border);
}

.section-head h2 {
  margin: 0;
  font-size: 17px;
  font-weight: 650;
}

.section-head p {
  margin: 4px 0 0;
  color: var(--color-muted);
  font-size: 13px;
}

.section-body {
  padding: 20px 22px 24px;
  display: grid;
  gap: 16px;
  max-width: 560px;
}

.section-body--table,
.section-body--wide {
  max-width: none;
}

.stub-hero {
  padding: 16px 18px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  background: var(--color-accent-soft);
}

.stub-hero-text p {
  margin: 10px 0 0;
  font-size: 14px;
  line-height: 1.5;
}

.stub-badge {
  display: inline-block;
  padding: 3px 10px;
  border-radius: 999px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  color: var(--color-accent);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.stub-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
}

.stub-card {
  display: grid;
  gap: 12px;
  padding: 16px 18px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  background: var(--color-surface-2);
}

.stub-card--muted {
  opacity: 0.92;
}

.stub-card h3 {
  margin: 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--color-muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.stub-list {
  margin: 0;
  padding-left: 18px;
  color: var(--color-text);
  font-size: 14px;
  line-height: 1.6;
}

.stub-foot {
  margin: 0;
}

.integration-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.integration-tab {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 8px 14px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-muted);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.15s ease, border-color 0.15s ease, color 0.15s ease;
}

.integration-tab:hover {
  background: var(--color-surface-2);
  color: var(--color-text);
}

.integration-tab.active {
  background: var(--color-accent-soft);
  border-color: var(--color-accent);
  color: var(--color-text);
}

.integration-tab svg {
  color: var(--color-accent);
  flex-shrink: 0;
}

.integration-tab-badge {
  padding: 1px 7px;
  border-radius: 999px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  color: var(--color-dim);
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

.integration-tab.active .integration-tab-badge {
  color: var(--color-accent);
  border-color: var(--color-accent);
}

.integration-panel {
  display: grid;
  gap: 16px;
}

.yookassa-head {
  display: flex;
  align-items: flex-start;
  gap: 14px;
  padding: 14px 16px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  background: var(--color-surface-2);
}

.yookassa-head.connected {
  border-color: color-mix(in srgb, var(--color-accent) 35%, var(--color-border));
}

.yookassa-head h3 {
  margin: 0 0 4px;
  font-size: 15px;
}

.yookassa-head-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 48px;
  height: 48px;
  border-radius: 12px;
  background: var(--color-accent-soft);
  color: var(--color-accent);
  flex-shrink: 0;
}

.yookassa-head.connected .yookassa-head-icon {
  background: var(--color-accent-soft);
  color: var(--color-accent);
}

.yookassa-connected {
  display: grid;
  gap: 12px;
  padding: 14px 16px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  background: var(--color-surface-2);
}

.yookassa-info-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  font-size: 13px;
}

.yookassa-info-row span {
  color: var(--color-muted);
}

.yookassa-security {
  padding: 10px 12px;
  border-radius: var(--radius-sm);
  background: var(--color-surface-2);
  border: 1px solid var(--color-border);
}

.integration-note {
  margin: 0;
  padding: 10px 12px;
  border-radius: var(--radius-sm);
  background: var(--color-surface-2);
  border: 1px solid var(--color-border);
}

.field-group {
  display: grid;
  gap: 12px;
  padding-bottom: 18px;
  border-bottom: 1px solid var(--color-border);
}

.field-group:last-child {
  padding-bottom: 0;
  border-bottom: 0;
}

.field-group h3 {
  margin: 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--color-muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.field {
  display: grid;
  gap: 6px;
}

.field span {
  color: var(--color-muted);
  font-size: 13px;
}

.row-2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.action-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 16px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  background: var(--color-surface-2);
}

.action-list {
  display: grid;
  gap: 10px;
}

.action-btns {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}

.upload input {
  display: none;
}

.hint {
  margin: 0;
  color: var(--color-muted);
  font-size: 12px;
}

.status-line {
  margin: 0;
  font-size: 14px;
}

.warn {
  margin: 0;
  color: var(--color-warning);
  font-size: 13px;
}

.placeholder {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 24px;
}

@media (max-width: 900px) {
  .settings-layout {
    grid-template-columns: 1fr;
  }

  .settings-nav {
    position: static;
    grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
    gap: 4px;
  }

  .nav-item {
    justify-content: center;
    flex-direction: column;
    gap: 4px;
    padding: 10px 8px;
    font-size: 11px;
  }

  .nav-version {
    grid-column: 1 / -1;
    text-align: center;
  }

  .section-body {
    max-width: none;
  }

  .action-row {
    flex-direction: column;
    align-items: flex-start;
  }

  .row-2,
  .stub-grid {
    grid-template-columns: 1fr;
  }
}
</style>
