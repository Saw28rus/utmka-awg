<template>
  <Teleport to="body">
    <div v-if="update.isRunning" class="update-overlay" role="dialog" aria-modal="true" aria-labelledby="update-title">
      <div class="update-backdrop" />
      <div class="update-card panel">
        <div class="update-icon-wrap" aria-hidden="true">
          <Download :size="28" class="update-icon" />
          <span class="update-pulse" />
        </div>

        <h2 id="update-title" class="update-title">Обновление панели</h2>
        <p class="update-subtitle">Пожалуйста, подождите. VPN на серверах продолжит работать.</p>

        <div class="update-progress-wrap">
          <div class="update-progress-track">
            <div class="update-progress-fill" :style="{ width: `${displayProgress}%` }">
              <span class="update-progress-shine" />
            </div>
          </div>
          <div class="update-progress-meta">
            <span class="update-step">{{ update.stepText }}</span>
            <span class="update-percent mono">{{ displayProgress }}%</span>
          </div>
        </div>

        <div class="update-steps">
          <span
            v-for="step in steps"
            :key="step.id"
            class="update-step-pill"
            :class="{ done: step.done, current: step.current }"
          >
            {{ step.label }}
          </span>
        </div>

        <n-button class="update-cancel" tertiary :loading="cancelling" @click="onCancel">
          Отменить
        </n-button>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { Download } from '@lucide/vue'
import { NButton, useMessage } from 'naive-ui'
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { usePanelUpdateStore } from '@/stores/panelUpdate'

const update = usePanelUpdateStore()
const message = useMessage()
const cancelling = ref(false)

// «Живой» прогресс: бэкенд отдаёт вехи (10/20/30/55/80/100) с долгими паузами
// между ними (особенно сборка на 55%). Чтобы полоса не выглядела зависшей, плавно
// подкручиваем её к следующей вехе, а при реальном скачке — догоняем мгновенно.
const MILESTONES = [10, 20, 30, 55, 80, 100]
const display = ref(4)

function tick() {
  const real = Math.max(update.progress, 4)
  if (update.status === 'success' || real >= 100) {
    display.value = 100
    return
  }
  if (!update.isRunning) return
  if (real > display.value) {
    // Догоняем реальную веху быстро.
    display.value = Math.min(real, display.value + Math.max(0.6, (real - display.value) * 0.3))
  } else {
    // Ползём к следующей вехе, не доходя до неё (эффект «trickle»).
    const next = MILESTONES.find((m) => m > real) ?? 100
    const ceiling = Math.max(real, next - 2)
    if (display.value < ceiling) {
      display.value = Math.min(ceiling, display.value + (ceiling - display.value) * 0.035 + 0.05)
    }
  }
}

const timer = window.setInterval(tick, 120)
onBeforeUnmount(() => window.clearInterval(timer))

watch(
  () => update.isRunning,
  (running, prev) => {
    if (running && !prev) display.value = 4
  }
)

const displayProgress = computed(() => Math.round(display.value))

const steps = computed(() => {
  const p = update.progress
  return [
    { id: 'backup', label: 'Бэкап', done: p >= 30, current: p >= 8 && p < 30 },
    { id: 'git', label: 'Загрузка', done: p >= 55, current: p >= 30 && p < 55 },
    { id: 'build', label: 'Сборка', done: p >= 80, current: p >= 55 && p < 80 },
    { id: 'health', label: 'Проверка', done: p >= 100, current: p >= 80 && p < 100 }
  ]
})

watch(
  () => update.status,
  (status, prev) => {
    if (prev !== 'running') return
    if (status === 'success') {
      message.success('Панель обновлена. Перезагружаю…')
      setTimeout(() => window.location.reload(), 1800)
    }
  }
)

async function onCancel() {
  if (!confirm('Прервать обновление? Панель останется на текущей версии.')) return
  cancelling.value = true
  try {
    const ok = await update.cancel()
    if (ok) message.warning('Обновление отменено.')
    else message.error('Не удалось отменить обновление.')
  } finally {
    cancelling.value = false
  }
}
</script>

<style scoped>
.update-overlay {
  position: fixed;
  inset: 0;
  z-index: 10000;
  display: grid;
  place-items: center;
  padding: 24px;
}

.update-backdrop {
  position: absolute;
  inset: 0;
  background: rgba(8, 10, 12, 0.72);
  backdrop-filter: blur(6px);
}

.update-card {
  position: relative;
  width: min(440px, 100%);
  padding: 28px 28px 24px;
  text-align: center;
  animation: card-in 0.35s ease;
}

@keyframes card-in {
  from {
    opacity: 0;
    transform: translateY(12px) scale(0.98);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

.update-icon-wrap {
  position: relative;
  width: 56px;
  height: 56px;
  margin: 0 auto 14px;
  display: grid;
  place-items: center;
  border-radius: 14px;
  background: var(--color-accent-soft);
  border: 1px solid var(--color-border);
}

.update-icon {
  color: var(--color-accent);
  animation: icon-bob 2s ease-in-out infinite;
}

@keyframes icon-bob {
  0%,
  100% {
    transform: translateY(0);
  }
  50% {
    transform: translateY(-3px);
  }
}

.update-pulse {
  position: absolute;
  inset: -6px;
  border-radius: 18px;
  border: 2px solid var(--color-accent);
  opacity: 0;
  animation: pulse-ring 2.2s ease-out infinite;
}

@keyframes pulse-ring {
  0% {
    transform: scale(0.92);
    opacity: 0.55;
  }
  100% {
    transform: scale(1.15);
    opacity: 0;
  }
}

.update-title {
  margin: 0;
  font-size: 20px;
  font-weight: 700;
}

.update-subtitle {
  margin: 8px 0 22px;
  color: var(--color-muted);
  font-size: 13px;
  line-height: 1.5;
}

.update-progress-wrap {
  display: grid;
  gap: 10px;
  margin-bottom: 16px;
}

.update-progress-track {
  height: 10px;
  border-radius: 999px;
  background: var(--color-surface-2);
  border: 1px solid var(--color-border);
  overflow: hidden;
}

.update-progress-fill {
  position: relative;
  height: 100%;
  border-radius: 999px;
  background: linear-gradient(90deg, #3d8f6a, #6fbf9a, #85d2ae);
  transition: width 0.35s cubic-bezier(0.4, 0, 0.2, 1);
  min-width: 8%;
}

.update-progress-shine {
  position: absolute;
  inset: 0;
  background: linear-gradient(
    90deg,
    transparent 0%,
    rgba(255, 255, 255, 0.35) 45%,
    transparent 90%
  );
  animation: shine 1.8s ease-in-out infinite;
}

@keyframes shine {
  0% {
    transform: translateX(-120%);
  }
  100% {
    transform: translateX(220%);
  }
}

.update-progress-meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  font-size: 13px;
}

.update-step {
  color: var(--color-text);
  text-align: left;
}

.update-percent {
  color: var(--color-accent);
  font-weight: 600;
}

.update-steps {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 6px;
  margin-bottom: 18px;
}

.update-step-pill {
  padding: 4px 10px;
  border-radius: 999px;
  border: 1px solid var(--color-border);
  color: var(--color-dim);
  font-size: 11px;
  font-weight: 500;
  transition: all 0.25s ease;
}

.update-step-pill.current {
  border-color: var(--color-accent);
  color: var(--color-accent);
  background: var(--color-accent-soft);
}

.update-step-pill.done {
  border-color: var(--color-border);
  color: var(--color-muted);
  background: var(--color-surface-2);
}

.update-cancel {
  min-width: 140px;
}
</style>
