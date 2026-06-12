<template>
  <AppShell title="Созданные счета" eyebrow="ЮKassa">
    <div class="panel batch-panel">
      <div class="batch-head">
        <div>
          <h2>Готово: {{ successCount }} из {{ items.length }}</h2>
          <p class="hint">Скопируйте сообщения и отправьте клиентам. Позже счета будут на вкладке «Выставленные».</p>
        </div>
        <n-button tertiary @click="goBack">К выставлению</n-button>
      </div>

      <div v-if="items.length" class="batch-list">
        <div v-for="item in items" :key="item.client_id" class="batch-row" :class="{ failed: !item.ok }">
          <strong class="batch-name">{{ item.client_name }}</strong>
          <p v-if="item.ok && item.message_text" class="batch-message">{{ item.message_text }}</p>
          <p v-else class="batch-error">{{ item.error || 'Не удалось создать счёт.' }}</p>
        </div>
      </div>

      <EmptyState v-else title="Список пуст" text="Вернитесь на главную и выставьте счета снова." />
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import { NButton } from 'naive-ui'
import { computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'

import EmptyState from '@/components/EmptyState.vue'
import AppShell from '@/layouts/AppShell.vue'
import { useInvoiceBatchStore } from '@/stores/invoiceBatch'

const router = useRouter()
const batchStore = useInvoiceBatchStore()

const items = computed(() => batchStore.items)
const successCount = computed(() => items.value.filter((i) => i.ok).length)

function goBack() {
  router.push({ name: 'invoices' })
}

onMounted(() => {
  if (!items.value.length) {
    router.replace({ name: 'invoices' })
  }
})
</script>

<style scoped>
.batch-panel {
  padding: 18px;
}

.batch-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}

h2 {
  margin: 0;
  font-size: 16px;
}

.hint {
  margin: 4px 0 0;
  color: var(--color-muted);
  font-size: 13px;
}

.batch-list {
  display: grid;
  gap: 8px;
}

.batch-row {
  padding: 12px 14px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface-2);
}

.batch-row.failed {
  border-color: var(--color-danger);
}

.batch-name {
  display: block;
  margin-bottom: 6px;
  font-size: 14px;
}

.batch-message {
  margin: 0;
  font-size: 13px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
}

.batch-error {
  margin: 0;
  font-size: 13px;
  color: var(--color-danger);
}
</style>
