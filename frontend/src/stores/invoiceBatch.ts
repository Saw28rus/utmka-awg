import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

export type InvoiceBatchItem = {
  client_id: string
  client_name: string
  ok: boolean
  invoice_id?: string | null
  pay_url?: string | null
  message_text?: string | null
  error?: string | null
}

export const useInvoiceBatchStore = defineStore('invoiceBatch', () => {
  const items = ref<InvoiceBatchItem[]>([])

  const hasBatch = computed(() => items.value.length > 10)

  function setBatch(next: InvoiceBatchItem[]) {
    items.value = next
  }

  function clear() {
    items.value = []
  }

  return { items, hasBatch, setBatch, clear }
})
