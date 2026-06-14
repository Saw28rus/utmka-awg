import { defineStore } from 'pinia'

import { api } from '@/api/client'

export type NotificationItem = {
  id: string
  created_at: string
  level: 'info' | 'warning' | 'danger'
  code: string
  title: string
  message: string
  server_id?: string | null
  read: boolean
}

export const useNotificationsStore = defineStore('notifications', {
  state: () => ({
    items: [] as NotificationItem[],
    unread: 0,
    pollTimer: null as ReturnType<typeof setInterval> | null
  }),

  actions: {
    async load() {
      try {
        const { data } = await api.get<{ items: NotificationItem[]; unread: number }>('/notifications')
        this.items = data.items || []
        this.unread = data.unread || 0
      } catch {
        /* API недоступен на старой версии */
      }
    },

    async markRead(ids?: string[]) {
      try {
        const { data } = await api.post<{ unread: number }>('/notifications/read', ids ? { ids } : {})
        this.unread = data.unread || 0
        this.items = this.items.map((i) =>
          !ids || ids.includes(i.id) ? { ...i, read: true } : i
        )
      } catch {
        /* ignore */
      }
    },

    startPolling() {
      this.stopPolling()
      void this.load()
      this.pollTimer = setInterval(() => {
        void this.load()
      }, 15_000)
    },

    stopPolling() {
      if (this.pollTimer) {
        clearInterval(this.pollTimer)
        this.pollTimer = null
      }
    }
  }
})
