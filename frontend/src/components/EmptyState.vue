<template>
  <div class="empty-state">
    <component :is="resolvedIcon" :size="24" />
    <h2>{{ title }}</h2>
    <p>{{ text }}</p>
    <slot />
  </div>
</template>

<script setup lang="ts">
import { Server } from '@lucide/vue'
import { computed } from 'vue'

const props = defineProps<{
  title: string
  text: string
  icon?: unknown
}>()

const resolvedIcon = computed(() => props.icon ?? Server)
</script>

<style scoped>
.empty-state {
  min-height: 220px;
  display: grid;
  place-items: center;
  align-content: center;
  gap: 10px;
  padding: 28px;
  text-align: center;
}

svg {
  color: var(--color-info);
}

h2 {
  margin: 4px 0 0;
  font-size: 18px;
  letter-spacing: 0;
}

p {
  max-width: 440px;
  margin: 0;
  color: var(--color-muted);
}
</style>
