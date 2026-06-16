<template>
  <div class="metric-bar">
    <div class="metric-top">
      <span class="metric-label">
        <component :is="icon" :size="14" />
        {{ label }}
      </span>
      <span class="metric-value">{{ valueText }}</span>
    </div>
    <div class="track">
      <div class="fill" :class="tone" :style="{ width: `${clampedPercent}%` }" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  label: string
  icon: unknown
  percent: number | null
  valueText: string
}>()

const clampedPercent = computed(() => {
  if (props.percent == null || Number.isNaN(props.percent)) return 0
  return Math.max(0, Math.min(100, props.percent))
})

const tone = computed(() => {
  const p = clampedPercent.value
  if (p >= 90) return 'danger'
  if (p >= 70) return 'warning'
  return 'ok'
})
</script>

<style scoped>
.metric-bar {
  display: grid;
  gap: 6px;
  min-width: 0;
}

.metric-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-width: 0;
}

.metric-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--color-muted);
  font-size: 12px;
  flex-shrink: 0;
}

.metric-value {
  color: var(--color-text);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  text-align: right;
}

.track {
  height: 6px;
  border-radius: 999px;
  background: var(--color-metric-track);
  overflow: hidden;
}

.fill {
  height: 100%;
  border-radius: 999px;
  transition: width 0.3s ease;
}

.fill.ok {
  background: var(--color-accent);
}

.fill.warning {
  background: var(--color-warning);
}

.fill.danger {
  background: var(--color-danger);
}
</style>
