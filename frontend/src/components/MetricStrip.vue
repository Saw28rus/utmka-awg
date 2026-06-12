<template>
  <div class="metric-strip">
    <div v-for="metric in metrics" :key="metric.label" class="metric">
      <p>{{ metric.label }}</p>
      <strong>{{ metric.value }}</strong>
      <span v-if="metric.hint">{{ metric.hint }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  metrics: Array<{ label: string; value: string; hint?: string }>
}>()
</script>

<style scoped>
.metric-strip {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  background: var(--color-surface);
}

.metric {
  min-width: 0;
  padding: 16px;
  border-right: 1px solid var(--color-border);
}

.metric:last-child {
  border-right: 0;
}

p {
  margin: 0 0 8px;
  color: var(--color-dim);
  font-size: 12px;
}

strong {
  display: block;
  color: var(--color-text);
  font-size: 22px;
  font-weight: 650;
}

span {
  display: block;
  margin-top: 3px;
  color: var(--color-muted);
  font-size: 12px;
}

@media (max-width: 960px) {
  .metric-strip {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .metric:nth-child(2) {
    border-right: 0;
  }

  .metric:nth-child(-n + 2) {
    border-bottom: 1px solid var(--color-border);
  }
}

@media (max-width: 520px) {
  .metric-strip {
    grid-template-columns: 1fr;
  }

  .metric {
    border-right: 0;
    border-bottom: 1px solid var(--color-border);
  }

  .metric:last-child {
    border-bottom: 0;
  }
}
</style>
