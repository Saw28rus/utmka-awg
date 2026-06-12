<template>
  <span class="status-badge" :class="`status-${tone}`">
    <span class="dot" :class="{ pulse: pulse, 'dot-online': tone === 'ok' && pulse }" />
    {{ label }}
  </span>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  label: string
  tone?: 'ok' | 'info' | 'warning' | 'danger' | 'neutral'
  pulse?: boolean
}>()

const tone = computed(() => props.tone ?? 'neutral')
</script>

<style scoped>
.status-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 24px;
  padding: 0 9px;
  border: 1px solid var(--color-border);
  border-radius: 999px;
  color: var(--color-muted);
  font-size: 12px;
  white-space: nowrap;
}

.dot {
  position: relative;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}

.dot.dot-online {
  background: var(--color-accent);
}

.dot.dot-online.pulse::after {
  background: var(--color-accent);
}

.dot.pulse::after {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: 50%;
  background: currentColor;
  animation: pulse-ring 1.6s ease-out infinite;
}

@keyframes pulse-ring {
  0% {
    transform: scale(1);
    opacity: 0.6;
  }
  70% {
    transform: scale(3.2);
    opacity: 0;
  }
  100% {
    transform: scale(3.2);
    opacity: 0;
  }
}

.status-ok {
  color: var(--color-accent);
}

.status-info {
  color: var(--color-info);
}

.status-warning {
  color: var(--color-warning);
}

.status-danger {
  color: var(--color-danger);
}
</style>
