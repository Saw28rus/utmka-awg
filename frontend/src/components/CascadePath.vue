<template>
  <span
    v-if="exitName"
    class="cascade-path"
    :title="hint"
  >
    <span class="hop hop-entry">{{ entryName }}</span>
    <span class="arrow" aria-hidden="true">→</span>
    <span class="hop hop-exit">{{ exitName }}</span>
  </span>
  <span v-else class="cascade-path cascade-path--solo">{{ entryName || '—' }}</span>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  entry?: string | null
  exit?: string | null
}>()

const entryName = computed(() => (props.entry || '').trim())
const exitName = computed(() => (props.exit || '').trim())
const hint = computed(() =>
  exitName.value
    ? `Каскад: ключ на «${entryName.value}», интернет через «${exitName.value}»`
    : entryName.value
)
</script>

<style scoped>
.cascade-path {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  min-width: 0;
  max-width: 100%;
  font-size: 12.5px;
  font-weight: 600;
  line-height: 1.2;
}

.cascade-path:not(.cascade-path--solo) {
  padding: 3px 8px;
  border: 1px solid var(--color-cascade-border-active);
  border-radius: 999px;
  background: var(--color-cascade-bg-active);
}

.cascade-path--solo {
  color: var(--color-muted);
  font-weight: 500;
}

.hop {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.hop-entry {
  color: var(--color-pill-entry-text);
}

.hop-exit {
  color: var(--color-pill-exit-text);
}

.arrow {
  flex-shrink: 0;
  color: var(--color-dim);
  font-weight: 500;
}
</style>
