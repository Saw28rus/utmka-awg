<template>
  <n-modal
    :show="show"
    preset="card"
    title="Импорт клиентов"
    style="max-width: 560px"
    @update:show="(v: boolean) => emit('update:show', v)"
  >
    <div class="import-body">
      <p class="import-hint">
        Загрузите JSON-бандл из экспорта. Клиенты будут <strong>пересозданы</strong> на исходном
        (или выбранном) сервере с новыми ключами.
        <strong>Старые конфиги/QR не подойдут</strong> — раздайте новые после импорта.
      </p>

      <div class="import-file">
        <n-button tertiary @click="pickFile">
          <template #icon><Upload :size="16" /></template>
          Выбрать файл…
        </n-button>
        <span v-if="fileName" class="file-name">{{ fileName }}</span>
        <input ref="fileInput" type="file" accept="application/json,.json" hidden @change="onFile" />
      </div>

      <label class="import-opt">
        <span>Перенести всех на сервер (опц.):</span>
        <n-select
          v-model:value="targetServerId"
          size="small"
          clearable
          placeholder="как в бандле"
          :options="serverOptions"
          style="min-width: 200px"
        />
      </label>

      <div v-if="result" class="import-summary" :class="{ applied: !result.dry_run }">
        <span>Всего: <strong>{{ result.total }}</strong></span>
        <span class="ok">создать: <strong>{{ result.to_create }}</strong></span>
        <span class="skip">пропустить: <strong>{{ result.to_skip }}</strong></span>
        <span v-if="result.errors" class="err">ошибки: <strong>{{ result.errors }}</strong></span>
      </div>

      <div v-if="result" class="import-list">
        <div v-for="(it, i) in result.items" :key="i" class="import-item" :class="it.action">
          <component :is="actionIcon(it.action)" :size="14" />
          <span class="item-name">{{ it.name }}</span>
          <span class="item-proto">{{ it.protocol }}</span>
          <span class="item-reason">{{ it.reason || (it.action === 'create' ? (result.dry_run ? 'будет создан' : 'создан') : '') }}</span>
        </div>
      </div>

      <div class="import-actions">
        <n-button :disabled="!bundle || loading" tertiary :loading="loading && pendingDry" @click="analyze">
          Проверить (предпросмотр)
        </n-button>
        <n-button
          type="primary"
          :disabled="!canApply"
          :loading="loading && !pendingDry"
          @click="confirmApply"
        >
          <template #icon><Upload :size="16" /></template>
          Импортировать {{ result ? `(${result.to_create})` : '' }}
        </n-button>
      </div>
    </div>
  </n-modal>
</template>

<script setup lang="ts">
import { AlertTriangle, CheckCircle2, MinusCircle, Upload } from '@lucide/vue'
import { NButton, NModal, NSelect, useDialog, useMessage } from 'naive-ui'
import { computed, ref, watch } from 'vue'

import { api } from '@/api/client'

type ImportItem = {
  name: string
  source_server_id?: string | null
  target_server_id?: string | null
  protocol: string
  action: string
  reason?: string | null
  client_id?: string | null
}

type ImportResult = {
  dry_run: boolean
  total: number
  to_create: number
  to_skip: number
  errors: number
  items: ImportItem[]
}

type ServerOption = { label: string; value: string }

const props = defineProps<{ show: boolean }>()
const emit = defineEmits<{ (e: 'update:show', value: boolean): void; (e: 'imported'): void }>()

const message = useMessage()
const dialog = useDialog()
const loading = ref(false)
const pendingDry = ref(true)
const bundle = ref<Record<string, unknown> | null>(null)
const fileName = ref('')
const targetServerId = ref<string | null>(null)
const result = ref<ImportResult | null>(null)
const serverOptions = ref<ServerOption[]>([])
const fileInput = ref<HTMLInputElement | null>(null)

const canApply = computed(() => !!result.value && result.value.to_create > 0 && !loading.value)

watch(
  () => props.show,
  (open) => {
    if (open) {
      void loadServers()
    } else {
      bundle.value = null
      fileName.value = ''
      result.value = null
      targetServerId.value = null
    }
  }
)

watch(targetServerId, () => {
  if (bundle.value) void analyze()
})

async function loadServers() {
  try {
    const { data } = await api.get<Array<{ id: string; name: string }>>('/servers')
    serverOptions.value = data.map((s) => ({ label: s.name, value: s.id }))
  } catch {
    /* список серверов необязателен */
  }
}

function actionIcon(action: string) {
  if (action === 'create') return CheckCircle2
  if (action === 'skip') return MinusCircle
  return AlertTriangle
}

function pickFile() {
  fileInput.value?.click()
}

function onFile(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0]
  if (!file) return
  fileName.value = file.name
  const reader = new FileReader()
  reader.onload = () => {
    try {
      const parsed = JSON.parse(String(reader.result))
      if (!parsed || !Array.isArray(parsed.clients)) {
        message.error('В файле нет списка clients.')
        return
      }
      bundle.value = parsed
      void analyze()
    } catch {
      message.error('Не удалось прочитать JSON.')
    }
  }
  reader.readAsText(file)
}

async function analyze() {
  if (!bundle.value) return
  loading.value = true
  pendingDry.value = true
  try {
    const { data } = await api.post<ImportResult>('/clients/import', {
      bundle: bundle.value,
      target_server_id: targetServerId.value || null,
      dry_run: true
    })
    result.value = data
  } catch (err: any) {
    message.error(err?.response?.data?.detail || 'Не удалось проанализировать бандл.')
  } finally {
    loading.value = false
  }
}

function confirmApply() {
  if (!canApply.value) return
  dialog.warning({
    title: 'Импортировать клиентов?',
    content:
      `Будет создано клиентов: ${result.value?.to_create}. ` +
      'Каждый получит новые ключи — старые конфиги не подойдут, нужно раздать новые.',
    positiveText: 'Импортировать',
    negativeText: 'Отмена',
    onPositiveClick: () => {
      void apply()
    }
  })
}

async function apply() {
  if (!bundle.value) return
  loading.value = true
  pendingDry.value = false
  try {
    const { data } = await api.post<ImportResult>('/clients/import', {
      bundle: bundle.value,
      target_server_id: targetServerId.value || null,
      dry_run: false
    })
    result.value = data
    if (data.errors) {
      message.warning(`Импортировано: ${data.to_create}, с ошибками: ${data.errors}.`)
    } else {
      message.success(`Импортировано клиентов: ${data.to_create}.`)
    }
    emit('imported')
  } catch (err: any) {
    message.error(err?.response?.data?.detail || 'Импорт не удался.')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.import-body {
  display: grid;
  gap: 14px;
}

.import-hint {
  margin: 0;
  font-size: 13px;
  color: var(--color-muted);
  line-height: 1.5;
}

.import-file {
  display: flex;
  align-items: center;
  gap: 10px;
}

.file-name {
  font-size: 13px;
  color: var(--color-muted);
  overflow-wrap: anywhere;
}

.import-opt {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  font-size: 13px;
  color: var(--color-muted);
}

.import-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  padding: 10px 12px;
  border-radius: var(--radius-sm);
  background: var(--color-surface-2);
  font-size: 13px;
}

.import-summary .ok {
  color: var(--color-primary, #63e2b7);
}

.import-summary .skip {
  color: var(--color-muted);
}

.import-summary .err {
  color: var(--color-danger);
}

.import-list {
  display: grid;
  gap: 4px;
  max-height: 320px;
  overflow-y: auto;
}

.import-item {
  display: grid;
  grid-template-columns: 16px 1fr auto 1.4fr;
  align-items: center;
  gap: 8px;
  padding: 5px 8px;
  border-radius: var(--radius-sm);
  font-size: 12px;
}

.import-item.create {
  background: color-mix(in srgb, var(--color-primary, #63e2b7) 10%, transparent);
}

.import-item.error {
  background: color-mix(in srgb, var(--color-danger) 12%, transparent);
}

.item-name {
  font-weight: 600;
  overflow-wrap: anywhere;
}

.item-proto {
  color: var(--color-muted);
  font-family: var(--font-mono, monospace);
}

.item-reason {
  color: var(--color-muted);
  text-align: right;
  overflow-wrap: anywhere;
}

.import-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  justify-content: flex-end;
}
</style>
