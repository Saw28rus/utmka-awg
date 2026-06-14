<template>
  <n-modal
    :show="show"
    preset="card"
    title="Экспорт клиентов"
    style="max-width: 460px"
    @update:show="(v: boolean) => emit('update:show', v)"
  >
    <div class="export-body">
      <p class="export-hint">
        Выгрузка конфигов и ссылок для бэкапа и массовой раздачи.
        <strong>Файл содержит приватные ключи</strong> — храните его в надёжном месте.
      </p>

      <label class="export-opt">
        <n-checkbox v-model:checked="includeQr" />
        <span>Включить QR-коды (нужны для печати)</span>
      </label>

      <div v-if="bundle" class="export-stat">
        Готово к выгрузке: <strong>{{ bundle.count }}</strong> клиентов.
      </div>

      <div class="export-actions">
        <n-button :loading="loading" tertiary @click="downloadJson">
          <template #icon><Download :size="16" /></template>
          Скачать JSON
        </n-button>
        <n-button :loading="loading" :disabled="!includeQr" type="primary" @click="printQr">
          <template #icon><Printer :size="16" /></template>
          QR-коды для печати
        </n-button>
      </div>
    </div>
  </n-modal>
</template>

<script setup lang="ts">
import { Download, Printer } from '@lucide/vue'
import { NButton, NCheckbox, NModal, useMessage } from 'naive-ui'
import { ref, watch } from 'vue'

import { api } from '@/api/client'

type ExportClient = {
  id: string
  name: string
  server_name?: string | null
  protocol: string
  client_ip: string
  config_text?: string | null
  vpn_link?: string | null
  qr_awg?: string | null
  qr_vpn?: string | null
}

type Bundle = {
  version: number
  generated_at: string
  count: number
  clients: ExportClient[]
}

const props = defineProps<{ show: boolean; serverId?: string | null }>()
const emit = defineEmits<{ (e: 'update:show', value: boolean): void }>()

const message = useMessage()
const loading = ref(false)
const includeQr = ref(true)
const bundle = ref<Bundle | null>(null)

watch(
  () => props.show,
  (open) => {
    if (open) void fetchBundle()
    else bundle.value = null
  }
)

watch(includeQr, () => {
  if (props.show) void fetchBundle()
})

async function fetchBundle() {
  loading.value = true
  try {
    const { data } = await api.post<Bundle>('/clients/export', {
      server_id: props.serverId || null,
      include_qr: includeQr.value
    })
    bundle.value = data
  } catch (err: any) {
    message.error(err?.response?.data?.detail || 'Не удалось получить экспорт.')
  } finally {
    loading.value = false
  }
}

function downloadJson() {
  if (!bundle.value) return
  const blob = new Blob([JSON.stringify(bundle.value, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  const stamp = new Date().toISOString().slice(0, 10)
  a.href = url
  a.download = `clients-export-${stamp}.json`
  a.click()
  URL.revokeObjectURL(url)
  message.success(`Выгружено ${bundle.value.count} клиентов.`)
}

function printQr() {
  if (!bundle.value) return
  const cards = bundle.value.clients
    .map((c) => {
      const qr = c.qr_vpn || c.qr_awg
      if (!qr) return ''
      return `
        <div class="card">
          <img src="${qr}" alt="QR" />
          <div class="meta">
            <strong>${escapeHtml(c.name)}</strong>
            <span>${escapeHtml(c.server_name || '')} · ${escapeHtml(c.protocol)}</span>
          </div>
        </div>`
    })
    .join('')

  const win = window.open('', '_blank')
  if (!win) {
    message.error('Браузер заблокировал окно печати.')
    return
  }
  win.document.write(`
    <html><head><title>QR клиентов</title>
    <style>
      body { font-family: system-ui, sans-serif; margin: 16px; }
      .grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }
      .card { border: 1px solid #ddd; border-radius: 10px; padding: 10px; text-align: center; page-break-inside: avoid; }
      .card img { width: 100%; max-width: 180px; height: auto; }
      .meta { margin-top: 6px; }
      .meta strong { display: block; font-size: 13px; }
      .meta span { font-size: 11px; color: #666; }
      @media print { .card { border-color: #ccc; } }
    </style></head>
    <body><div class="grid">${cards}</div>
    <script>window.onload = () => setTimeout(() => window.print(), 300)<\/script>
    </body></html>`)
  win.document.close()
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (ch) => {
    const map: Record<string, string> = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }
    return map[ch]
  })
}
</script>

<style scoped>
.export-body {
  display: grid;
  gap: 14px;
}

.export-hint {
  margin: 0;
  font-size: 13px;
  color: var(--color-muted);
  line-height: 1.5;
}

.export-opt {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  cursor: pointer;
}

.export-stat {
  font-size: 13px;
  color: var(--color-muted);
}

.export-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  justify-content: flex-end;
}
</style>
