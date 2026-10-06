<template>
  <div class="icmp">
    <p class="icmp-lead">
      2ip.ru пингует IP, с которого вы зашли. Домашний роутер обычно молчит, VPS отвечает —
      отсюда «Определение туннеля (двусторонний пинг): обнаружен».
    </p>
    <p class="icmp-sub">
      В каскаде сайты видят IP <strong>выходного</strong> сервера (NL) — его и нужно закрыть.
      Закрывается только echo-request, Path MTU не ломается.
    </p>

    <div v-if="loading" class="icmp-loading">
      <n-spin size="small" />
      <span>Проверяю серверы…</span>
    </div>

    <template v-else>
      <p v-if="!servers.length" class="icmp-empty">Нет VPN-серверов. Добавьте РУ2 и NL в список серверов.</p>

      <div v-else class="icmp-list">
        <div v-for="row in servers" :key="row.server_id" class="icmp-row">
          <div class="icmp-row-main">
            <strong>{{ row.name }}</strong>
            <span class="mono">{{ row.host }}</span>
            <span class="icmp-role" :class="row.role">{{ roleLabel(row.role) }}</span>
          </div>
          <p class="icmp-msg">{{ row.message || (row.enabled ? 'Пинг закрыт' : 'Сервер отвечает на ping') }}</p>
          <div class="icmp-row-actions">
            <n-button
              size="small"
              :type="row.enabled ? 'default' : 'primary'"
              :loading="busy === row.server_id"
              :disabled="!!busy"
              @click="toggleOne(row)"
            >
              {{ row.enabled ? 'Открыть ping' : 'Закрыть ping' }}
            </n-button>
          </div>
        </div>
      </div>

      <div v-if="servers.length" class="icmp-all">
        <n-button type="primary" :loading="busy === 'all-on'" :disabled="!!busy" @click="applyAll">
          Закрыть ping на всех
        </n-button>
        <n-button tertiary :loading="busy === 'all-off'" :disabled="!!busy" @click="disableAll">
          Снова открыть всем
        </n-button>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { NButton, NSpin, useMessage } from 'naive-ui'
import { onMounted, ref } from 'vue'

import { api } from '@/api/client'

type Row = {
  server_id: string
  name: string
  host: string
  enabled: boolean
  role: string
  ping_replies?: boolean | null
  message?: string | null
}

const message = useMessage()
const loading = ref(true)
const servers = ref<Row[]>([])
const busy = ref('')

function roleLabel(role: string) {
  if (role === 'exit') return 'выход каскада'
  if (role === 'entry') return 'вход каскада'
  return 'VPN-сервер'
}

function errText(error: unknown) {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (error instanceof Error) return error.message
  return 'Ошибка'
}

async function load() {
  loading.value = true
  try {
    const { data } = await api.get<{ servers: Row[] }>('/settings/icmp-stealth', { timeout: 120_000 })
    servers.value = data.servers || []
  } catch (error) {
    message.error(errText(error))
  } finally {
    loading.value = false
  }
}

async function toggleOne(row: Row) {
  busy.value = row.server_id
  try {
    const { data } = await api.post(
      `/servers/${row.server_id}/security/action`,
      { control: 'icmp_stealth', action: row.enabled ? 'disable' : 'enable' },
      { timeout: 90_000 }
    )
    message.success(data.message)
    await load()
  } catch (error) {
    message.error(errText(error))
  } finally {
    busy.value = ''
  }
}

async function applyAll() {
  busy.value = 'all-on'
  try {
    const { data } = await api.post('/settings/icmp-stealth/apply-all', {}, { timeout: 180_000 })
    message.success(data.message)
    await load()
  } catch (error) {
    message.error(errText(error))
  } finally {
    busy.value = ''
  }
}

async function disableAll() {
  if (!confirm('Снова отвечать на ping со всех VPN-серверов? 2ip снова увидит туннель.')) return
  busy.value = 'all-off'
  try {
    const { data } = await api.post('/settings/icmp-stealth/disable-all', {}, { timeout: 180_000 })
    message.success(data.message)
    await load()
  } catch (error) {
    message.error(errText(error))
  } finally {
    busy.value = ''
  }
}

onMounted(load)
</script>

<style scoped>
.icmp-lead,
.icmp-sub {
  margin: 0 0 10px;
  color: var(--color-muted);
  font-size: 13px;
  line-height: 1.45;
}
.icmp-lead { color: inherit; }
.icmp-loading,
.icmp-empty {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 0;
  color: var(--color-muted);
  font-size: 13px;
}
.icmp-list { display: grid; gap: 8px; }
.icmp-row {
  display: grid;
  gap: 6px;
  padding: 12px 14px;
  border: 1px solid var(--color-border);
  border-radius: 10px;
}
.icmp-row-main {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.icmp-role {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--color-muted);
}
.icmp-role.exit { color: var(--color-accent); }
.icmp-msg { margin: 0; font-size: 12.5px; color: var(--color-muted); }
.icmp-all { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
.mono { font-family: var(--font-mono, ui-monospace, monospace); font-size: 12.5px; }
</style>
