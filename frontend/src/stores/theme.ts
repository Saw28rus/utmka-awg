import { defineStore } from 'pinia'

import { api } from '@/api/client'
import { useAuthStore } from '@/stores/auth'

export type ThemeMode = 'dark' | 'light'

const STORAGE_KEY = 'utmka_theme'

function applyTheme(theme: ThemeMode) {
  document.documentElement.setAttribute('data-theme', theme)
}

export const useThemeStore = defineStore('theme', {
  state: () => ({
    mode: (localStorage.getItem(STORAGE_KEY) as ThemeMode) || 'dark'
  }),
  actions: {
    initFromUser() {
      const auth = useAuthStore()
      if (auth.user?.theme) {
        this.mode = auth.user.theme as ThemeMode
      }
      applyTheme(this.mode)
      localStorage.setItem(STORAGE_KEY, this.mode)
    },
    async setTheme(mode: ThemeMode) {
      this.mode = mode
      applyTheme(mode)
      localStorage.setItem(STORAGE_KEY, mode)
      const auth = useAuthStore()
      if (auth.isAuthenticated) {
        try {
          await api.patch('/auth/me/theme', { theme: mode })
          if (auth.user) auth.user.theme = mode
        } catch {
          /* local theme still applied */
        }
      }
    },
    toggle() {
      return this.setTheme(this.mode === 'dark' ? 'light' : 'dark')
    }
  }
})
