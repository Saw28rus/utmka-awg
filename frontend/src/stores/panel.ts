import { defineStore } from 'pinia'

import { api } from '@/api/client'

export const usePanelStore = defineStore('panel', {
  state: () => ({
    appName: 'UTMka+AWG',
    loaded: false
  }),
  actions: {
    async loadPublicName() {
      try {
        const { data } = await api.get<{ app_name?: string }>('/settings/public')
        if (data.app_name) this.appName = data.app_name
      } catch {
        /* keep default */
      } finally {
        this.loaded = true
      }
    },
    setAppName(name: string) {
      this.appName = name
    }
  }
})
