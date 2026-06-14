import { defineStore } from 'pinia'

import { api } from '@/api/client'

export const useChatUnreadStore = defineStore('chatUnread', {
  state: () => ({
    unread: 0,
    threads: 0,
    pollTimer: null as ReturnType<typeof setInterval> | null
  }),

  actions: {
    async refresh() {
      try {
        const { data } = await api.get<{
          enabled?: boolean
          unread_messages?: number
          unread_threads?: number
        }>('/chat/admin/status')
        if (!data.enabled) {
          this.unread = 0
          this.threads = 0
          return
        }
        this.unread = data.unread_messages || 0
        this.threads = data.unread_threads || 0
      } catch {
        this.unread = 0
        this.threads = 0
      }
    },

    startPolling() {
      this.stopPolling()
      void this.refresh()
      this.pollTimer = setInterval(() => {
        void this.refresh()
      }, 10_000)
    },

    stopPolling() {
      if (this.pollTimer) {
        clearInterval(this.pollTimer)
        this.pollTimer = null
      }
    }
  }
})
