import { defineStore } from 'pinia'

import { api } from '@/api/client'

type User = {
  id: string
  email: string
  name: string
  role: string
  is_active: boolean
  theme: string
}

export const useAuthStore = defineStore('auth', {
  state: () => ({
    user: null as User | null,
    accessToken: localStorage.getItem('utmka_access_token')
  }),
  getters: {
    isAuthenticated: (state) => Boolean(state.accessToken)
  },
  actions: {
    async login(email: string, password: string) {
      const { data } = await api.post('/auth/login', { email, password })
      this.accessToken = data.access_token
      localStorage.setItem('utmka_access_token', data.access_token)
      await this.loadMe()
    },
    async loadMe() {
      const { data } = await api.get<User>('/auth/me')
      this.user = data
      const { useThemeStore } = await import('@/stores/theme')
      useThemeStore().initFromUser()
    },
    logout() {
      this.user = null
      this.accessToken = null
      localStorage.removeItem('utmka_access_token')
    }
  }
})
