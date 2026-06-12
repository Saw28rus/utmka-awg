<template>
  <div class="timeline">
    <div v-for="event in events" :key="event.code" class="event">
      <StatusBadge :label="event.label" :tone="event.tone" />
      <p>{{ event.message }}</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import StatusBadge from '@/components/StatusBadge.vue'

defineProps<{
  events: Array<{
    code: string
    label: string
    message: string
    tone?: 'ok' | 'info' | 'warning' | 'danger' | 'neutral'
  }>
}>()
</script>

<style scoped>
.timeline {
  display: grid;
  gap: 0;
  overflow: hidden;
}

.event {
  display: grid;
  grid-template-columns: 160px minmax(0, 1fr);
  gap: 16px;
  align-items: center;
  padding: 13px 16px;
  border-bottom: 1px solid var(--color-border);
}

.event:last-child {
  border-bottom: 0;
}

p {
  margin: 0;
  color: var(--color-muted);
}

@media (max-width: 680px) {
  .event {
    grid-template-columns: 1fr;
    gap: 8px;
  }
}
</style>
