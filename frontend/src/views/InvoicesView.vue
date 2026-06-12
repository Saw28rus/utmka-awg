<template>
  <AppShell title="Счета и оплата" eyebrow="ЮKassa">
    <div class="invoice-tabs panel">
      <button
        v-for="tab in tabs"
        :key="tab.id"
        type="button"
        class="invoice-tab"
        :class="{ active: activeTab === tab.id }"
        @click="switchTab(tab.id)"
      >
        <component :is="tab.icon" :size="15" />
        <span>{{ tab.label }}</span>
      </button>
    </div>

    <!-- Главная: выставление счетов -->
    <div v-if="activeTab === 'home'" class="layout-2col">
      <div class="panel form-panel">
        <h2>Выставить счёт</h2>
        <p class="hint">Сформируйте ссылку на оплату для одного клиента или сразу для нескольких.</p>

        <label class="field">
          <span>Шаблон сообщения</span>
          <n-select
            v-model:value="form.templateId"
            :options="templateOptions"
            placeholder="Без шаблона (текст по умолчанию)"
            clearable
            @update:value="applyTemplate"
          />
        </label>

        <div class="row-2">
          <label class="field">
            <span>Услуга / за что</span>
            <n-input v-model:value="form.service" placeholder="Например, Synker" />
          </label>
          <label class="field">
            <span>Сумма, ₽</span>
            <n-input-number v-model:value="form.amount" :min="1" :precision="2" :step="50" style="width: 100%" />
          </label>
        </div>

        <div class="row-2">
          <label class="field">
            <span>Счёт действителен</span>
            <n-select v-model:value="form.expiresDays" :options="expiresOptions" />
          </label>
          <label class="field">
            <span>Продлить после оплаты</span>
            <n-select v-model:value="form.extendMonths" :options="extendOptions" />
          </label>
        </div>

        <label class="field">
          <span>Текст сообщения</span>
          <n-input
            v-model:value="form.body"
            type="textarea"
            :autosize="{ minRows: 3, maxRows: 7 }"
            placeholder="Здравствуйте, {{имя}}! ..."
          />
        </label>
        <div class="tokens">
          <button v-for="t in tokens" :key="t" type="button" class="token" @click="insertToken(t)">{{ t }}</button>
        </div>

        <div class="preview">
          <span class="preview-label">Предпросмотр</span>
          <p class="preview-text">{{ previewText }}</p>
        </div>

        <div class="actions">
          <n-button
            type="primary"
            :loading="creating"
            :disabled="selectedCount === 0"
            @click="createInvoices"
          >
            {{ selectedCount > 1 ? `Выставить массово (${selectedCount})` : 'Выставить счёт' }}
          </n-button>
        </div>

        <div v-if="batchStore.hasBatch" class="results batch-fallback">
          <n-button type="primary" @click="openBatchPage">Просмотр счетов</n-button>
          <span class="batch-fallback-hint">Создано {{ batchStore.items.length }} счетов — откройте полный список сообщений.</span>
        </div>

        <div v-else-if="results.length" class="results">
          <h3>Созданные счета</h3>
          <div v-for="r in results" :key="r.client_id" class="result-row" :class="{ failed: !r.ok }">
            <div class="result-main">
              <strong>{{ r.client_name }}</strong>
              <p v-if="r.ok && r.message_text" class="result-message">{{ r.message_text }}</p>
              <span v-else class="result-error">{{ r.error }}</span>
            </div>
          </div>
        </div>
      </div>

      <div class="panel clients-panel">
        <div class="clients-head">
          <h2>Клиенты</h2>
          <span class="count-pill">{{ selectedCount }} / {{ clients.length }}</span>
        </div>
        <n-input v-model:value="clientSearch" placeholder="Поиск по имени…" clearable size="small" />
        <div class="select-actions">
          <n-button size="tiny" tertiary @click="selectAll">Выбрать всех</n-button>
          <n-button size="tiny" tertiary @click="clearSelection">Снять</n-button>
        </div>
        <div v-if="loadingClients" class="placeholder"><n-spin size="small" /></div>
        <div v-else-if="filteredClients.length" class="clients-list">
          <label v-for="c in filteredClients" :key="c.id" class="client-pick" :class="{ picked: selected.has(c.id) }">
            <n-checkbox :checked="selected.has(c.id)" @update:checked="(v: boolean) => toggleClient(c.id, v)" />
            <span class="entity-avatar entity-avatar--sm">{{ c.name.charAt(0).toUpperCase() }}</span>
            <span class="client-pick-name">{{ c.name }}</span>
            <span class="client-pick-meta">{{ c.server_name || '—' }}</span>
          </label>
        </div>
        <EmptyState v-else title="Нет клиентов" text="Создайте клиента на странице «Клиенты»." />
      </div>
    </div>

    <!-- Шаблоны -->
    <div v-else-if="activeTab === 'templates'" class="layout-2col">
      <div class="panel form-panel">
        <h2>{{ editingTemplate.id ? 'Редактирование шаблона' : 'Новый шаблон' }}</h2>
        <label class="field">
          <span>Название</span>
          <n-input v-model:value="editingTemplate.title" placeholder="Счёт за доступ" />
        </label>
        <div class="row-2">
          <label class="field">
            <span>Услуга по умолчанию</span>
            <n-input v-model:value="editingTemplate.default_service" placeholder="Synker" />
          </label>
          <label class="field">
            <span>Сумма по умолчанию, ₽</span>
            <n-input-number v-model:value="editingTemplate.default_amount" :min="0" :precision="2" style="width: 100%" />
          </label>
        </div>
        <label class="field">
          <span>Текст</span>
          <n-input
            v-model:value="editingTemplate.body"
            type="textarea"
            :autosize="{ minRows: 3, maxRows: 8 }"
          />
        </label>
        <div class="tokens">
          <button v-for="t in tokens" :key="t" type="button" class="token" @click="insertTokenTemplate(t)">{{ t }}</button>
        </div>
        <div class="preview">
          <span class="preview-label">Предпросмотр</span>
          <p class="preview-text">{{ templatePreview }}</p>
        </div>
        <div class="actions">
          <n-button type="primary" :loading="savingTemplate" @click="saveTemplate">
            {{ editingTemplate.id ? 'Сохранить' : 'Создать шаблон' }}
          </n-button>
          <n-button v-if="editingTemplate.id" tertiary @click="resetTemplateEditor">Новый</n-button>
        </div>
      </div>

      <div class="panel clients-panel">
        <div class="clients-head">
          <h2>Шаблоны</h2>
          <span class="count-pill">{{ templates.length }}</span>
        </div>
        <div v-if="templates.length" class="template-list">
          <div v-for="tpl in templates" :key="tpl.id" class="template-item">
            <div class="template-info">
              <strong>{{ tpl.title }}</strong>
              <p class="template-body">{{ tpl.body }}</p>
            </div>
            <div class="template-actions">
              <n-button size="tiny" tertiary @click="editTemplate(tpl)">Изменить</n-button>
              <n-popconfirm @positive-click="deleteTemplate(tpl.id)">
                <template #trigger><n-button size="tiny" tertiary type="error">Удалить</n-button></template>
                Удалить шаблон?
              </n-popconfirm>
            </div>
          </div>
        </div>
        <EmptyState v-else title="Шаблонов нет" text="Создайте первый шаблон слева." />
      </div>
    </div>

    <!-- Списки счетов по статусам -->
    <div v-else class="panel list-panel">
      <div class="section-head">
        <div class="title-row">
          <h2>{{ activeMeta.label }}</h2>
          <span v-if="invoices.length" class="count-pill">{{ invoices.length }}</span>
        </div>
        <n-button tertiary circle :loading="refreshing" title="Обновить статусы" @click="refreshStatuses">
          <template #icon><RefreshCw :size="16" /></template>
        </n-button>
      </div>

      <div v-if="loadingList" class="placeholder"><n-spin size="small" /><span>Загрузка…</span></div>

      <div v-else-if="invoices.length" class="invoice-list">
        <div class="list-head">
          <span>Клиент</span>
          <span>Услуга</span>
          <span>Сумма</span>
          <span>Создан</span>
          <span>{{ activeTab === 'paid' ? 'Оплачен' : 'Действует до' }}</span>
          <span class="center">Статус</span>
          <span class="center">Действия</span>
        </div>
        <div v-for="inv in invoices" :key="inv.id" class="invoice-row">
          <span class="inv-client">
            <span class="entity-avatar entity-avatar--sm">{{ inv.client_name.charAt(0).toUpperCase() }}</span>
            {{ inv.client_name }}
          </span>
          <span class="inv-muted">{{ inv.service || '—' }}</span>
          <span class="inv-amount">{{ formatMoney(inv.amount) }}</span>
          <span class="inv-muted">{{ formatDate(inv.created_at) }}</span>
          <span class="inv-muted">{{ activeTab === 'paid' ? formatDateTime(inv.paid_at) : formatDate(inv.expires_at) }}</span>
          <span class="center">
            <StatusBadge :label="statusMeta(inv.status).label" :tone="statusMeta(inv.status).tone" :pulse="false" />
          </span>
          <span class="center inv-actions">
            <n-button v-if="inv.pay_url" size="tiny" tertiary title="Скопировать ссылку" @click="copyText(inv.pay_url, 'Ссылка')">
              <template #icon><LinkIcon :size="13" /></template>
            </n-button>
            <n-button v-if="inv.message_text" size="tiny" tertiary title="Скопировать сообщение" @click="copyText(inv.message_text, 'Сообщение')">
              <template #icon><Copy :size="13" /></template>
            </n-button>
            <n-button v-if="activeTab === 'deleted'" size="tiny" tertiary title="Восстановить" @click="restoreInvoice(inv.id)">
              <template #icon><Undo2 :size="13" /></template>
            </n-button>
            <n-popconfirm v-else @positive-click="deleteInvoice(inv.id)">
              <template #trigger>
                <n-button size="tiny" tertiary type="error" title="Удалить">
                  <template #icon><Trash2 :size="13" /></template>
                </n-button>
              </template>
              Переместить счёт в «Удалены»?
            </n-popconfirm>
          </span>
        </div>
      </div>

      <EmptyState v-else title="Здесь пусто" :text="emptyText" />
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import {
  Copy,
  FileClock,
  FilePlus2,
  FileText,
  Link as LinkIcon,
  RefreshCw,
  Trash2,
  Undo2,
  Wallet
} from '@lucide/vue'
import { CheckCircle2 } from '@lucide/vue'
import {
  NButton,
  NCheckbox,
  NInput,
  NInputNumber,
  NPopconfirm,
  NSelect,
  NSpin,
  useMessage
} from 'naive-ui'
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import { api } from '@/api/client'
import EmptyState from '@/components/EmptyState.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import AppShell from '@/layouts/AppShell.vue'
import { useInvoiceBatchStore } from '@/stores/invoiceBatch'
import { copyToClipboard } from '@/utils/clipboard'

type TabId = 'home' | 'issued' | 'paid' | 'overdue' | 'deleted' | 'templates'

type ClientItem = {
  id: string
  name: string
  server_name?: string | null
  status: string
}

type InvoiceItem = {
  id: string
  client_id?: string | null
  client_name: string
  service?: string | null
  amount: number
  status: string
  pay_url?: string | null
  message_text?: string | null
  expires_at?: string | null
  paid_at?: string | null
  created_at?: string | null
}

type TemplateItem = {
  id: string
  title: string
  body: string
  default_service?: string | null
  default_amount?: number | null
}

type CreateResultItem = {
  client_id: string
  client_name: string
  ok: boolean
  invoice_id?: string | null
  pay_url?: string | null
  message_text?: string | null
  error?: string | null
}

const DEFAULT_BODY =
  'Здравствуйте, {{имя}}! Оплата за {{услуга}} на сумму {{сумма}}. Ссылка для оплаты: {{ссылка}}'

const tabs: { id: TabId; label: string; icon: object }[] = [
  { id: 'home', label: 'Главная', icon: FilePlus2 },
  { id: 'issued', label: 'Выставленные', icon: FileClock },
  { id: 'paid', label: 'Оплаченные', icon: CheckCircle2 },
  { id: 'overdue', label: 'Просроченные', icon: Wallet },
  { id: 'deleted', label: 'Удалены', icon: Trash2 },
  { id: 'templates', label: 'Шаблоны', icon: FileText }
]

const tokens = ['{{имя}}', '{{услуга}}', '{{сумма}}', '{{ссылка}}', '{{период}}']

const expiresOptions = [
  { label: '1 день', value: 1 },
  { label: '3 дня', value: 3 },
  { label: '7 дней', value: 7 },
  { label: '14 дней', value: 14 },
  { label: '30 дней', value: 30 }
]

const extendOptions = [
  { label: 'на 1 месяц', value: 1 },
  { label: 'на 2 месяца', value: 2 },
  { label: 'на 3 месяца', value: 3 },
  { label: 'на 6 месяцев', value: 6 },
  { label: 'на 12 месяцев', value: 12 }
]

const message = useMessage()
const router = useRouter()
const batchStore = useInvoiceBatchStore()
const activeTab = ref<TabId>('home')

const clients = ref<ClientItem[]>([])
const loadingClients = ref(false)
const clientSearch = ref('')
const selected = ref<Set<string>>(new Set())

const templates = ref<TemplateItem[]>([])
const invoices = ref<InvoiceItem[]>([])
const loadingList = ref(false)
const refreshing = ref(false)
const creating = ref(false)
const savingTemplate = ref(false)
const results = ref<CreateResultItem[]>([])

const form = reactive({
  templateId: null as string | null,
  service: '',
  amount: 200 as number | null,
  expiresDays: 3,
  extendMonths: 1,
  body: DEFAULT_BODY
})

const editingTemplate = reactive({
  id: '' as string,
  title: '',
  body: DEFAULT_BODY,
  default_service: '' as string,
  default_amount: null as number | null
})

const activeMeta = computed(() => tabs.find((t) => t.id === activeTab.value) ?? tabs[0])
const selectedCount = computed(() => selected.value.size)

const templateOptions = computed(() =>
  templates.value.map((t) => ({ label: t.title, value: t.id }))
)

const filteredClients = computed(() => {
  const q = clientSearch.value.trim().toLowerCase()
  if (!q) return clients.value
  return clients.value.filter((c) => c.name.toLowerCase().includes(q))
})

const emptyText = computed(() => {
  if (activeTab.value === 'issued') return 'Выставленные неоплаченные счета появятся здесь.'
  if (activeTab.value === 'paid') return 'Оплаченные счета появятся здесь.'
  if (activeTab.value === 'overdue') return 'Просроченные и отменённые счета появятся здесь.'
  if (activeTab.value === 'deleted') return 'Удалённые счета появятся здесь.'
  return 'Пусто.'
})

const previewText = computed(() =>
  renderTokens(form.body, {
    name: 'Иван Иванов',
    service: form.service,
    amount: form.amount,
    link: 'https://yookassa.ru/my/i/example/l'
  })
)

const templatePreview = computed(() =>
  renderTokens(editingTemplate.body, {
    name: 'Иван Иванов',
    service: editingTemplate.default_service,
    amount: editingTemplate.default_amount,
    link: 'https://yookassa.ru/my/i/example/l'
  })
)

onMounted(async () => {
  await Promise.all([loadClients(), loadTemplates()])
})

function renderTokens(
  body: string,
  data: { name: string; service?: string | null; amount?: number | null; link: string }
): string {
  return body
    .replaceAll('{{имя}}', data.name)
    .replaceAll('{{услуга}}', data.service || '')
    .replaceAll('{{сумма}}', data.amount != null ? formatMoney(data.amount) : '')
    .replaceAll('{{ссылка}}', data.link)
    .replaceAll('{{период}}', '')
}

function formatMoney(rub: number): string {
  return Number.isInteger(rub) ? `${rub} ₽` : `${rub.toFixed(2)} ₽`
}

function formatDate(value?: string | null): string {
  if (!value) return '—'
  try {
    return new Date(value).toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: '2-digit' })
  } catch {
    return value
  }
}

function formatDateTime(value?: string | null): string {
  if (!value) return '—'
  try {
    return new Date(value).toLocaleString('ru-RU', {
      day: '2-digit',
      month: '2-digit',
      hour: '2-digit',
      minute: '2-digit'
    })
  } catch {
    return value
  }
}

function statusMeta(status: string): { label: string; tone: 'ok' | 'warning' | 'danger' | 'neutral' } {
  switch (status) {
    case 'succeeded':
      return { label: 'оплачен', tone: 'ok' }
    case 'pending':
      return { label: 'выставлен', tone: 'warning' }
    case 'expired':
      return { label: 'просрочен', tone: 'danger' }
    case 'canceled':
      return { label: 'отменён', tone: 'neutral' }
    default:
      return { label: status, tone: 'neutral' }
  }
}

async function switchTab(tab: TabId) {
  activeTab.value = tab
  if (tab === 'home') {
    await loadClients()
  } else if (tab === 'templates') {
    await loadTemplates()
  } else {
    await loadInvoices(tab)
  }
}

async function loadClients() {
  loadingClients.value = true
  try {
    const { data } = await api.get<ClientItem[]>('/clients')
    clients.value = data
  } finally {
    loadingClients.value = false
  }
}

async function loadTemplates() {
  const { data } = await api.get<TemplateItem[]>('/invoices/templates')
  templates.value = data
}

async function loadInvoices(tab: TabId) {
  loadingList.value = true
  try {
    const { data } = await api.get<InvoiceItem[]>('/invoices', { params: { tab } })
    invoices.value = data
  } finally {
    loadingList.value = false
  }
}

function toggleClient(id: string, checked: boolean) {
  const next = new Set(selected.value)
  if (checked) next.add(id)
  else next.delete(id)
  selected.value = next
}

function selectAll() {
  selected.value = new Set(filteredClients.value.map((c) => c.id))
}

function clearSelection() {
  selected.value = new Set()
}

function applyTemplate(id: string | null) {
  if (!id) {
    form.body = DEFAULT_BODY
    return
  }
  const tpl = templates.value.find((t) => t.id === id)
  if (!tpl) return
  form.body = tpl.body
  if (tpl.default_service) form.service = tpl.default_service
  if (tpl.default_amount != null) form.amount = tpl.default_amount
}

function insertToken(token: string) {
  form.body = `${form.body}${form.body && !form.body.endsWith(' ') ? ' ' : ''}${token}`
}

function insertTokenTemplate(token: string) {
  editingTemplate.body = `${editingTemplate.body}${editingTemplate.body && !editingTemplate.body.endsWith(' ') ? ' ' : ''}${token}`
}

async function createInvoices() {
  if (selectedCount.value === 0) return
  if (!form.amount || form.amount <= 0) {
    message.warning('Укажите сумму.')
    return
  }
  creating.value = true
  results.value = []
  try {
    const { data } = await api.post<{ created: number; failed: number; items: CreateResultItem[] }>('/invoices', {
      client_ids: Array.from(selected.value),
      amount: form.amount,
      service: form.service || null,
      template_id: null,
      message_override: form.body,
      expires_days: form.expiresDays,
      extend_months: form.extendMonths
    })
    if (data.items.length > 10) {
      batchStore.setBatch(data.items)
      results.value = []
      if (data.created) message.success(`Создано счетов: ${data.created}.`)
      if (data.failed) message.warning(`Не удалось: ${data.failed}.`)
      clearSelection()
      await router.push({ name: 'invoices-created' })
    } else {
      batchStore.clear()
      results.value = data.items
      if (data.created) message.success(`Создано счетов: ${data.created}.`)
      if (data.failed) message.warning(`Не удалось: ${data.failed}.`)
      clearSelection()
    }
  } catch (e: unknown) {
    const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    message.error(typeof detail === 'string' ? detail : 'Не удалось выставить счета.')
  } finally {
    creating.value = false
  }
}

async function copyText(text: string, label: string) {
  const ok = await copyToClipboard(text)
  if (ok) message.success(`${label} скопирована.`)
  else message.error('Не удалось скопировать.')
}

function openBatchPage() {
  router.push({ name: 'invoices-created' })
}

async function deleteInvoice(id: string) {
  try {
    await api.delete(`/invoices/${id}`)
    invoices.value = invoices.value.filter((i) => i.id !== id)
    message.success('Счёт перемещён в «Удалены».')
  } catch {
    message.error('Не удалось удалить.')
  }
}

async function restoreInvoice(id: string) {
  try {
    await api.post(`/invoices/${id}/restore`)
    invoices.value = invoices.value.filter((i) => i.id !== id)
    message.success('Счёт восстановлен.')
  } catch {
    message.error('Не удалось восстановить.')
  }
}

async function refreshStatuses() {
  refreshing.value = true
  try {
    await api.post('/invoices/refresh')
    await loadInvoices(activeTab.value)
    message.success('Статусы обновлены.')
  } catch {
    message.error('Не удалось обновить статусы.')
  } finally {
    refreshing.value = false
  }
}

function resetTemplateEditor() {
  editingTemplate.id = ''
  editingTemplate.title = ''
  editingTemplate.body = DEFAULT_BODY
  editingTemplate.default_service = ''
  editingTemplate.default_amount = null
}

function editTemplate(tpl: TemplateItem) {
  editingTemplate.id = tpl.id
  editingTemplate.title = tpl.title
  editingTemplate.body = tpl.body
  editingTemplate.default_service = tpl.default_service || ''
  editingTemplate.default_amount = tpl.default_amount ?? null
}

async function saveTemplate() {
  if (!editingTemplate.title.trim() || !editingTemplate.body.trim()) {
    message.warning('Заполните название и текст.')
    return
  }
  savingTemplate.value = true
  const payload = {
    title: editingTemplate.title,
    body: editingTemplate.body,
    default_service: editingTemplate.default_service || null,
    default_amount: editingTemplate.default_amount
  }
  try {
    if (editingTemplate.id) {
      await api.put(`/invoices/templates/${editingTemplate.id}`, payload)
      message.success('Шаблон сохранён.')
    } else {
      await api.post('/invoices/templates', payload)
      message.success('Шаблон создан.')
    }
    await loadTemplates()
    resetTemplateEditor()
  } catch {
    message.error('Не удалось сохранить шаблон.')
  } finally {
    savingTemplate.value = false
  }
}

async function deleteTemplate(id: string) {
  try {
    await api.delete(`/invoices/templates/${id}`)
    await loadTemplates()
    if (editingTemplate.id === id) resetTemplateEditor()
    message.success('Шаблон удалён.')
  } catch {
    message.error('Не удалось удалить шаблон.')
  }
}
</script>

<style scoped>
.invoice-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 8px;
  margin-bottom: 16px;
}

.invoice-tab {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 8px 14px;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-muted);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.15s ease, border-color 0.15s ease, color 0.15s ease;
}

.invoice-tab:hover {
  background: var(--color-surface-2);
  color: var(--color-text);
}

.invoice-tab.active {
  background: var(--color-accent-soft);
  border-color: var(--color-accent);
  color: var(--color-text);
}

.invoice-tab svg {
  color: var(--color-accent);
}

.layout-2col {
  display: grid;
  grid-template-columns: minmax(0, 1.3fr) minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}

.form-panel,
.clients-panel,
.list-panel {
  padding: 18px;
}

h2 {
  margin: 0;
  font-size: 16px;
}

h3 {
  margin: 18px 0 8px;
  font-size: 13px;
  color: var(--color-muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.hint {
  margin: 4px 0 14px;
  color: var(--color-muted);
  font-size: 13px;
}

.field {
  display: grid;
  gap: 6px;
  margin-bottom: 12px;
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

.tokens {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: -4px 0 12px;
}

.token {
  padding: 3px 8px;
  border: 1px solid var(--color-border);
  border-radius: 999px;
  background: var(--color-surface-2);
  color: var(--color-accent);
  font-size: 11px;
  font-family: var(--font-mono, monospace);
  cursor: pointer;
}

.token:hover {
  border-color: var(--color-accent);
}

.preview {
  display: grid;
  gap: 6px;
  padding: 12px 14px;
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface-2);
  margin-bottom: 14px;
}

.preview-label {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--color-dim);
}

.preview-text {
  margin: 0;
  font-size: 13px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
}

.actions {
  display: flex;
  gap: 8px;
}

.results {
  margin-top: 4px;
}

.batch-fallback {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
}

.batch-fallback-hint {
  font-size: 13px;
  color: var(--color-muted);
}

.result-row {
  padding: 10px 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  margin-bottom: 8px;
}

.result-row.failed {
  border-color: var(--color-danger);
}

.result-main {
  display: grid;
  gap: 2px;
  min-width: 0;
}

.result-message {
  margin: 4px 0 0;
  font-size: 13px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--color-text);
}

.result-error {
  font-size: 12px;
  color: var(--color-danger);
}

.clients-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.count-pill {
  padding: 1px 9px;
  border-radius: 999px;
  background: var(--color-surface-2);
  border: 1px solid var(--color-border);
  color: var(--color-muted);
  font-size: 12px;
}

.select-actions {
  display: flex;
  gap: 6px;
  margin: 10px 0;
}

.clients-list {
  display: grid;
  gap: 2px;
  max-height: 460px;
  overflow-y: auto;
}

.client-pick {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 7px 8px;
  border-radius: var(--radius-sm);
  cursor: pointer;
}

.client-pick:hover {
  background: var(--color-surface-2);
}

.client-pick.picked {
  background: var(--color-accent-soft);
}

.client-pick-name {
  font-size: 14px;
  font-weight: 500;
  flex: 1;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.client-pick-meta {
  font-size: 12px;
  color: var(--color-dim);
  flex-shrink: 0;
}

.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
}

.title-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.placeholder {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 24px;
  color: var(--color-muted);
}

.invoice-list {
  display: grid;
}

.list-head,
.invoice-row {
  display: grid;
  grid-template-columns: minmax(140px, 1.3fr) minmax(90px, 1fr) 90px 84px 110px 110px 120px;
  gap: 12px;
  align-items: center;
  padding: 0 4px;
}

.list-head {
  min-height: 34px;
  color: var(--color-dim);
  font-size: 12px;
  border-bottom: 1px solid var(--color-border);
}

.invoice-row {
  min-height: 52px;
  border-bottom: 1px solid var(--color-border);
}

.invoice-row:last-child {
  border-bottom: 0;
}

.inv-client {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 14px;
  font-weight: 500;
  min-width: 0;
}

.inv-muted {
  color: var(--color-muted);
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}

.inv-amount {
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.center {
  display: flex;
  align-items: center;
  justify-content: center;
}

.inv-actions {
  gap: 4px;
}

.template-list {
  display: grid;
  gap: 8px;
}

.template-item {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 14px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface-2);
}

.template-info {
  min-width: 0;
}

.template-body {
  margin: 4px 0 0;
  font-size: 12.5px;
  color: var(--color-muted);
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.template-actions {
  display: flex;
  gap: 6px;
  flex-shrink: 0;
}

@media (max-width: 980px) {
  .layout-2col {
    grid-template-columns: 1fr;
  }

  .list-head {
    display: none;
  }

  .invoice-row {
    grid-template-columns: 1fr 1fr;
    grid-auto-rows: auto;
    gap: 6px 12px;
    padding: 12px 4px;
  }

  .inv-client {
    grid-column: 1 / -1;
  }
}
</style>
