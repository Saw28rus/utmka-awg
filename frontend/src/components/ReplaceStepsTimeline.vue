<template>
  <ul class="steps">
    <li v-for="(s, i) in steps" :key="i" class="step" :class="s.status">
      <span class="dot" />
      <div class="step-body">
        <span class="step-name">{{ label(s.name) }}</span>
        <span v-if="s.detail" class="step-detail">{{ s.detail }}</span>
      </div>
      <span class="step-status">{{ statusLabel(s.status) }}</span>
    </li>
  </ul>
</template>

<script setup lang="ts">
defineProps<{
  steps: Array<{ name: string; status: string; detail?: string | null }>
}>()

const NAMES: Record<string, string> = {
  ssh: 'SSH-подключение',
  root: 'Root-доступ',
  public_ip: 'Публичный IP',
  clean: 'Чистота сервера',
  port: 'Свободный порт',
  docker: 'Docker',
  snapshot: 'Слепок входа',
  restore: 'Развёртывание AWG2',
  rollback: 'Откат',
  swap: 'Переключение записи',
  cascade: 'Каскад'
}

function label(name: string): string {
  return NAMES[name] || name
}

function statusLabel(status: string): string {
  if (status === 'ok') return 'готово'
  if (status === 'fail') return 'ошибка'
  if (status === 'running') return '…'
  return status
}
</script>

<style scoped>
.steps {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 8px;
}

.step {
  display: grid;
  grid-template-columns: auto 1fr auto;
  align-items: center;
  gap: 10px;
  font-size: 12px;
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--color-dim);
}

.step.ok .dot {
  background: var(--color-accent);
}

.step.fail .dot {
  background: #e88080;
}

.step.running .dot {
  background: #e8c280;
}

.step-body {
  display: grid;
  gap: 2px;
}

.step-name {
  font-weight: 500;
}

.step-detail {
  color: var(--color-dim);
  font-size: 11px;
  word-break: break-all;
}

.step-status {
  color: var(--color-muted);
  font-size: 11px;
}

.step.fail .step-status {
  color: #e88080;
}
</style>
