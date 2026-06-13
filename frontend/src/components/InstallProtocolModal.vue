<template>
  <n-modal v-model:show="visible">
    <div class="panel install-modal">
      <header class="modal-head">
        <div>
          <h3>Установить {{ protocol?.name }}</h3>
          <p>{{ protocol?.description }}</p>
        </div>
        <n-button circle tertiary size="small" @click="visible = false">
          <template #icon><X :size="15" /></template>
        </n-button>
      </header>

      <div v-if="!result" class="form">
        <label class="field">
          <span>{{ portLabel }}</span>
          <n-input-number v-model:value="port" :min="1" :max="65535" :show-button="false" :placeholder="String(defaultPort)" />
          <small>{{ portHint }}</small>
        </label>

        <label v-if="protocol?.id === 'xray'" class="field">
          <span>Домен маскировки (SNI)</span>
          <n-input v-model:value="siteName" placeholder="www.googletagmanager.com" />
          <small>Reality будет имитировать трафик к этому сайту. Как в Amnezia — googletagmanager.com.</small>
        </label>

        <label v-if="protocol?.id === 'xray'" class="field">
          <span>Транспорт</span>
          <n-select v-model:value="transport" :options="transportOptions" />
          <small>{{ transportHint }}</small>
        </label>

        <label v-if="protocol?.id === 'telemt'" class="field">
          <span>Домен для маскировки TLS</span>
          <n-input v-model:value="tlsDomain" placeholder="www.cloudflare.com" />
          <small>Прокси будет имитировать TLS-трафик к этому сайту. Подойдёт любой популярный домен.</small>
        </label>

        <p class="hint">
          Установка собирает Docker-образ на сервере и может занять 1–3 минуты — не закрывай окно.
        </p>
      </div>

      <div v-if="result" class="result panel-inner">
        <StatusBadge label="установлен" tone="ok" />
        <p>{{ result.message }}</p>
        <dl class="kv">
          <div v-if="result.port">
            <dt>Порт</dt>
            <dd class="mono">{{ result.port }}</dd>
          </div>
          <div v-if="result.site_name">
            <dt>SNI</dt>
            <dd class="mono">{{ result.site_name }}</dd>
          </div>
          <div v-if="result.transport">
            <dt>Транспорт</dt>
            <dd class="mono">{{ result.transport }}</dd>
          </div>
          <div v-if="result.client_uuid">
            <dt>UUID клиента</dt>
            <dd class="mono">{{ result.client_uuid }}</dd>
          </div>
          <div v-if="result.public_key">
            <dt>Публичный ключ сервера</dt>
            <dd class="mono">{{ result.public_key }}</dd>
          </div>
          <div v-if="result.short_id">
            <dt>Short ID</dt>
            <dd class="mono">{{ result.short_id }}</dd>
          </div>
          <div v-if="result.secret">
            <dt>Secret</dt>
            <dd class="mono">{{ result.secret }}</dd>
          </div>
          <div v-if="result.tg_link" class="kv-wide">
            <dt>Ссылка для Telegram</dt>
            <dd class="mono">{{ result.tg_link }}</dd>
          </div>
        </dl>
        <p class="hint">{{ resultHint }}</p>
      </div>

      <footer class="modal-foot">
        <n-button tertiary @click="visible = false">{{ result ? 'Закрыть' : 'Отмена' }}</n-button>
        <n-button
          v-if="!result"
          type="primary"
          :loading="installing"
          @click="install"
        >
          Установить
        </n-button>
      </footer>
    </div>
  </n-modal>
</template>

<script setup lang="ts">
import { X } from '@lucide/vue'
import { NButton, NInput, NInputNumber, NModal, NSelect, useMessage } from 'naive-ui'
import { computed, ref, watch } from 'vue'

import { api } from '@/api/client'
import StatusBadge from '@/components/StatusBadge.vue'

type ProtocolInfo = {
  id: string
  name: string
  description: string
}

type InstallResult = {
  message: string
  container?: string | null
  port?: number | null
  site_name?: string | null
  client_uuid?: string | null
  public_key?: string | null
  short_id?: string | null
  secret?: string | null
  tg_link?: string | null
  transport?: string | null
}

const props = defineProps<{
  show: boolean
  serverId: string
  protocol: ProtocolInfo | null
}>()

const emit = defineEmits<{
  'update:show': [value: boolean]
  installed: []
}>()

const message = useMessage()
const visible = ref(props.show)
const installing = ref(false)
const port = ref(443)
const siteName = ref('www.googletagmanager.com')
const tlsDomain = ref('www.cloudflare.com')
const transport = ref('tcp')
const result = ref<InstallResult | null>(null)

const transportOptions = [
  { label: 'TCP + Vision (рекомендуется)', value: 'tcp' },
  { label: 'gRPC', value: 'grpc' },
  { label: 'XHTTP (современный)', value: 'xhttp' },
]

const transportHint = computed(() => {
  if (transport.value === 'grpc') return 'gRPC поверх Reality: мультиплексирование, устойчивее за CDN/прокси. Без flow.'
  if (transport.value === 'xhttp') return 'XHTTP поверх Reality: дробит трафик по HTTP-запросам, новейший транспорт, лучше против анализа трафика.'
  return 'TCP + XTLS-Vision: классика, максимальная производительность. Совместимо со всеми клиентами.'
})

const DEFAULT_PORTS: Record<string, number> = {
  xray: 443,
  telemt: 443,
  awg2: 39547,
  awg_legacy: 39547,
  wireguard: 51820
}

const defaultPort = computed(() => DEFAULT_PORTS[props.protocol?.id ?? ''] ?? 443)

const portLabel = computed(() => {
  const id = props.protocol?.id
  if (id === 'xray' || id === 'telemt') return 'TCP-порт'
  return 'UDP-порт'
})

const portHint = computed(() => {
  const id = props.protocol?.id
  if (id === 'xray') return 'По умолчанию 443 — маскировка под HTTPS. Убедись, что порт свободен.'
  if (id === 'telemt') return 'По умолчанию 443 — маскировка под HTTPS. Убедись, что порт свободен.'
  if (id === 'wireguard') return 'UDP-порт для подключений WireGuard. По умолчанию 51820.'
  return 'UDP-порт для подключений AmneziaWG. Подойдёт любой свободный порт.'
})

const resultHint = computed(() => {
  const id = props.protocol?.id
  if (id === 'telemt') return 'Открой ссылку в Telegram или передай её клиенту — прокси подключится автоматически.'
  if (id === 'wireguard') return 'Сервер поднят. Добавь клиента по публичному ключу сервера и порту.'
  if (id === 'awg2' || id === 'awg_legacy') {
    return 'Готово. Добавляй клиентов во вкладке «Клиенты» — ключи генерируются на сервере.'
  }
  return 'Импортируй сервер в AmneziaVPN или добавь конфиг вручную — панель сохранила ключи на VPS.'
})

watch(
  () => props.show,
  (value) => {
    visible.value = value
    if (value) {
      result.value = null
      port.value = defaultPort.value
      siteName.value = 'www.googletagmanager.com'
      tlsDomain.value = 'www.cloudflare.com'
      transport.value = 'tcp'
    }
  }
)

watch(visible, (value) => emit('update:show', value))

async function install() {
  if (!props.protocol) return
  installing.value = true
  try {
    let site: string | undefined
    if (props.protocol.id === 'xray') site = siteName.value.trim() || undefined
    else if (props.protocol.id === 'telemt') site = tlsDomain.value.trim() || undefined

    const { data } = await api.post<InstallResult>(
      `/servers/${props.serverId}/protocols/${props.protocol.id}/install`,
      {
        port: port.value || defaultPort.value,
        site_name: site,
        transport: props.protocol.id === 'xray' ? transport.value : undefined
      },
      { timeout: 600_000 }
    )
    result.value = data
    message.success(data.message || 'Протокол установлен.')
    emit('installed')
  } catch (error: any) {
    message.error(error?.response?.data?.detail || 'Не удалось установить протокол.')
  } finally {
    installing.value = false
  }
}
</script>

<style scoped>
.install-modal {
  width: min(520px, 92vw);
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
  gap: 14px;
}

.field {
  display: grid;
  gap: 6px;
}

.field span {
  font-size: 13px;
}

.field small {
  color: var(--color-dim);
  font-size: 11px;
  line-height: 1.4;
}

.panel-inner {
  display: grid;
  gap: 10px;
  padding: 12px 14px;
  border: 1px solid var(--color-border);
  border-radius: 10px;
}

.panel-inner p {
  margin: 0;
  font-size: 13px;
}

.hint {
  color: var(--color-muted);
  font-size: 12px !important;
}

.kv {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px 12px;
  margin: 0;
}

.kv dt {
  color: var(--color-dim);
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  margin-bottom: 2px;
}

.kv dd {
  margin: 0;
  font-size: 12px;
  word-break: break-all;
}

.kv-wide {
  grid-column: 1 / -1;
}

.modal-foot {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
</style>
