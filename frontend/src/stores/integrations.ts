import { defineStore } from 'pinia'

import { api } from '@/api/client'

export const useIntegrationsStore = defineStore('integrations', {
  state: () => ({
    yookassaConnected: false,
    chatEnabled: false,
    loaded: false
  }),
  actions: {
    async load() {
      try {
        const { data } = await api.get<{ yookassa_connected: boolean; chat_enabled?: boolean }>(
          '/settings/integrations-status'
        )
        this.yookassaConnected = Boolean(data.yookassa_connected)
        this.chatEnabled = Boolean(data.chat_enabled)
      } catch {
        this.yookassaConnected = false
        this.chatEnabled = false
      } finally {
        this.loaded = true
      }
    },
    setYookassaConnected(value: boolean) {
      this.yookassaConnected = value
    }
  }
})
