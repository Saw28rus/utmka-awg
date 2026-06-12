<template>
  <n-modal
    v-model:show="visible"
    :mask-closable="!loading && !saving"
    class="server-modal"
    role="dialog"
    aria-modal="true"
    aria-labelledby="add-server-title"
  >
    <div class="wizard">
      <header class="wizard-head">
        <div>
          <p>Добавление VPS</p>
          <h2 id="add-server-title">SSH и detect AWG2</h2>
        </div>
        <StatusBadge :label="stepLabel" :tone="stepTone" />
      </header>

      <div class="wizard-body">
        <n-alert v-if="error" type="error" :show-icon="false">
          {{ error }}
        </n-alert>

        <form class="form-grid" @submit.prevent="runDetect">
          <label class="field">
            <span>Имя сервера</span>
            <input v-model="form.name" autocomplete="off" placeholder="NL-Exit" />
          </label>
          <label class="field">
            <span>IP или host</span>
            <input v-model="form.host" autocomplete="off" inputmode="url" placeholder="203.0.113.10" />
          </label>
          <label class="field">
            <span>SSH port</span>
            <input v-model.number="form.ssh_port" inputmode="numeric" min="1" max="65535" type="number" />
          </label>
          <label class="field">
            <span>SSH user</span>
            <input v-model="form.ssh_username" autocomplete="username" placeholder="root" />
          </label>
          <label class="field">
            <span>Пароль</span>
            <input v-model="form.ssh_password" autocomplete="current-password" placeholder="Пароль SSH" type="password" />
          </label>
          <label class="field field-wide">
            <span>SSH key</span>
            <textarea
              v-model="form.ssh_key"
              placeholder="Можно оставить пустым, если используешь пароль"
              rows="5"
              spellcheck="false"
            />
          </label>
        </form>

        <div v-if="loading" class="detect-loading">
          <n-spin size="small" />
          <span>Подключаюсь по SSH и проверяю Amnezia/AWG2. На сервере ничего не меняю.</span>
        </div>

        <section v-if="detectResult" class="detect-result">
          <div class="result-head">
            <div>
              <h3>{{ detectTitle }}</h3>
              <p>{{ detectResult.message }}</p>
            </div>
            <StatusBadge :label="branchLabel" :tone="branchTone" />
          </div>

          <div class="checks">
            <div v-for="check in detectResult.checks" :key="check.key" class="check-row">
              <StatusBadge :label="check.label" :tone="checkTone(check.status)" />
              <span>{{ check.message || 'Нет деталей' }}</span>
            </div>
          </div>

          <dl v-if="detectResult.config_path || detectResult.peers_count" class="result-meta">
            <div v-if="detectResult.config_path">
              <dt>Config</dt>
              <dd class="mono">{{ detectResult.config_path }}</dd>
            </div>
            <div>
              <dt>Peers</dt>
              <dd>{{ detectResult.peers_count }}</dd>
            </div>
          </dl>
        </section>
      </div>

      <footer class="wizard-actions">
        <n-button tertiary :disabled="loading" @click="close">Отмена</n-button>
        <n-button v-if="!detectResult" type="primary" :loading="loading" @click="runDetect">
          Проверить SSH и AWG2
        </n-button>
        <template v-else>
          <n-button tertiary :disabled="loading" @click="runDetect">Проверить еще раз</n-button>
          <n-button v-if="detectResult.branch === 'import'" type="primary" :loading="saving" @click="saveServer('import')">
            Подключить найденное
          </n-button>
          <n-button v-else-if="detectResult.branch === 'install'" type="primary" :loading="saving" @click="saveServer('install')">
            Создать с нуля
          </n-button>
          <n-button v-else type="primary" :loading="saving" @click="saveServer('needs_review')">
            Сохранить для проверки
          </n-button>
        </template>
      </footer>
    </div>
  </n-modal>
</template>

<script setup lang="ts">
import {
  NAlert,
  NButton,
  NModal,
  NSpin,
  useMessage
} from 'naive-ui'
import { computed, reactive, ref, watch } from 'vue'

import { api } from '@/api/client'
import StatusBadge from '@/components/StatusBadge.vue'

type DetectCheck = {
  key: string
  label: string
  status: string
  message?: string
}

type DetectResult = {
  confidence: string
  branch: 'import' | 'install' | 'needs_review'
  checks: DetectCheck[]
  message: string
  awg2_detected: boolean
  config_path?: string | null
  peers_count: number
  container_names: string[]
  docker_available: boolean
  os_release?: string | null
}

const props = defineProps<{
  show: boolean
}>()

const emit = defineEmits<{
  'update:show': [value: boolean]
  created: []
}>()

const message = useMessage()
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const detectResult = ref<DetectResult | null>(null)

const form = reactive({
  name: '',
  host: '',
  ssh_port: 22,
  ssh_username: 'root',
  ssh_password: '',
  ssh_key: ''
})

const visible = computed({
  get: () => props.show,
  set: (value: boolean) => emit('update:show', value)
})

watch(visible, (open) => {
  if (!open) resetWizard()
})

const stepLabel = computed(() => {
  if (loading.value) return 'Detect'
  if (!detectResult.value) return 'SSH'
  return branchLabel.value
})

const stepTone = computed(() => {
  if (!detectResult.value) return 'info'
  return branchTone.value
})

const branchLabel = computed(() => {
  if (!detectResult.value) return 'Не проверено'
  if (detectResult.value.branch === 'import') return 'AWG2 найден'
  if (detectResult.value.branch === 'install') return 'Чистый сервер'
  return 'Нужна проверка'
})

const branchTone = computed(() => {
  if (!detectResult.value) return 'neutral'
  if (detectResult.value.branch === 'import') return 'ok'
  if (detectResult.value.branch === 'install') return 'info'
  return 'warning'
})

const detectTitle = computed(() => {
  if (!detectResult.value) return ''
  if (detectResult.value.branch === 'import') return 'Можно подключить существующую Amnezia'
  if (detectResult.value.branch === 'install') return 'Amnezia/AWG2 не найдены'
  return 'Нужно уточнить вручную'
})

function checkTone(status: string) {
  if (status === 'ok') return 'ok'
  if (status === 'error') return 'danger'
  if (status === 'warning') return 'warning'
  return 'neutral'
}

function validate() {
  if (!form.name.trim()) return 'Укажи имя сервера.'
  if (!form.host.trim()) return 'Укажи IP или host.'
  if (!form.ssh_username.trim()) return 'Укажи SSH user.'
  if (!form.ssh_password.trim() && !form.ssh_key.trim()) return 'Укажи пароль или SSH key.'
  return ''
}

function payload() {
  return {
    name: form.name.trim(),
    host: form.host.trim(),
    ssh_port: Number(form.ssh_port || 22),
    ssh_username: form.ssh_username.trim(),
    ssh_password: form.ssh_password.trim() || null,
    ssh_key: form.ssh_key.trim() || null
  }
}

async function runDetect() {
  error.value = validate()
  if (error.value) return

  loading.value = true
  error.value = ''
  detectResult.value = null
  try {
    const { data } = await api.post<DetectResult>('/servers/detect-preview', payload())
    detectResult.value = data
  } catch (err: any) {
    error.value = err?.response?.data?.detail || 'Не удалось выполнить SSH detect.'
  } finally {
    loading.value = false
  }
}

async function saveServer(branch: 'import' | 'install' | 'needs_review') {
  if (!detectResult.value) return
  saving.value = true
  try {
    await api.post('/servers', {
      ...payload(),
      detect_branch: branch,
      awg2_detected: detectResult.value.awg2_detected,
      config_path: detectResult.value.config_path,
      active_peers: detectResult.value.peers_count,
      container_names: detectResult.value.container_names
    })
    message.success(branch === 'import' ? 'Найденная Amnezia подключена.' : 'Сервер добавлен.')
    emit('created')
    visible.value = false
  } catch (err: any) {
    error.value = err?.response?.data?.detail || 'Не удалось сохранить сервер.'
  } finally {
    saving.value = false
  }
}

function resetWizard() {
  detectResult.value = null
  error.value = ''
  form.name = ''
  form.host = ''
  form.ssh_port = 22
  form.ssh_username = 'root'
  form.ssh_password = ''
  form.ssh_key = ''
}

function close() {
  if (loading.value || saving.value) return
  visible.value = false
}
</script>

<style scoped>
.server-modal {
  width: min(920px, calc(100vw - 32px));
}

.wizard {
  position: relative;
  z-index: 1;
  overflow: hidden;
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  background: var(--color-surface);
}

.wizard-head,
.wizard-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 16px 18px;
}

.wizard-head {
  border-bottom: 1px solid var(--color-border);
}

.wizard-head p,
.result-head p {
  margin: 0;
  color: var(--color-muted);
}

.wizard-head h2,
.result-head h3 {
  margin: 3px 0 0;
  font-size: 18px;
  letter-spacing: 0;
}

.wizard-body {
  display: grid;
  gap: 16px;
  padding: 18px;
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}

.field {
  display: grid;
  gap: 7px;
  min-width: 0;
  color: var(--color-text);
  font-size: 14px;
  font-weight: 600;
}

.field-wide {
  grid-column: 1 / -1;
}

.field input,
.field textarea {
  width: 100%;
  min-width: 0;
  border: 1px solid transparent;
  border-radius: 7px;
  background: #101214;
  color: var(--color-text);
  font: inherit;
  font-weight: 500;
  outline: none;
  transition:
    border-color 0.16s ease,
    background-color 0.16s ease;
}

.field input {
  height: 36px;
  padding: 0 11px;
}

.field textarea {
  resize: vertical;
  padding: 10px 11px;
  font-family: "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 13px;
  line-height: 1.45;
}

.field input:focus,
.field textarea:focus {
  border-color: var(--color-accent);
  background: #0d0f10;
}

.field input::placeholder,
.field textarea::placeholder {
  color: var(--color-dim);
  font-weight: 500;
}

.detect-loading {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 42px;
  color: var(--color-muted);
}

.detect-result {
  display: grid;
  gap: 14px;
  border-top: 1px solid var(--color-border);
  padding-top: 16px;
}

.result-head {
  display: flex;
  justify-content: space-between;
  gap: 16px;
}

.checks {
  display: grid;
  border-top: 1px solid var(--color-border);
  border-bottom: 1px solid var(--color-border);
}

.check-row {
  display: grid;
  grid-template-columns: 150px minmax(0, 1fr);
  gap: 12px;
  align-items: center;
  min-height: 42px;
  border-bottom: 1px solid var(--color-border);
  color: var(--color-muted);
}

.check-row:last-child {
  border-bottom: 0;
}

.result-meta {
  display: grid;
  gap: 8px;
  margin: 0;
}

.result-meta > div {
  display: grid;
  grid-template-columns: 92px minmax(0, 1fr);
  gap: 12px;
}

dt {
  color: var(--color-dim);
}

dd {
  min-width: 0;
  margin: 0;
  overflow-wrap: anywhere;
}

.wizard-actions {
  border-top: 1px solid var(--color-border);
}

@media (max-width: 720px) {
  .form-grid,
  .check-row,
  .result-meta > div {
    grid-template-columns: 1fr;
  }

  .result-head,
  .wizard-head,
  .wizard-actions {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
