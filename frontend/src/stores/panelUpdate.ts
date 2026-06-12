import { defineStore } from 'pinia'

import { api } from '@/api/client'

type UpdateJob = {
  id: string
  status: string
  progress: number
  message?: string | null
}

const TERMINAL = new Set(['success', 'failed_manual', 'rolled_back'])

function stepLabel(progress: number, message?: string | null): string {
  if (message) return message
  if (progress < 10) return 'Запуск обновления…'
  if (progress < 30) return 'Резервная копия данных…'
  if (progress < 55) return 'Загрузка новой версии…'
  if (progress < 80) return 'Сборка и перезапуск контейнеров…'
  if (progress < 100) return 'Проверка работоспособности…'
  return 'Завершение…'
}

export const usePanelUpdateStore = defineStore('panelUpdate', {
  state: () => ({
    active: false,
    jobId: null as string | null,
    status: 'running' as string,
    progress: 0,
    message: 'Запуск обновления…',
    pollTimer: null as ReturnType<typeof setInterval> | null
  }),

  getters: {
    isRunning: (s) => s.active && s.status === 'running',
    stepText: (s) => stepLabel(s.progress, s.message)
  },

  actions: {
    _applyJob(job: UpdateJob) {
      this.jobId = job.id
      this.status = job.status
      this.progress = Math.max(0, Math.min(100, job.progress ?? 0))
      this.message = stepLabel(this.progress, job.message)
      this.active = job.status === 'running'
    },

    startPolling() {
      this.stopPolling()
      this.pollTimer = setInterval(() => {
        void this.poll()
      }, 1000)
    },

    stopPolling() {
      if (this.pollTimer) {
        clearInterval(this.pollTimer)
        this.pollTimer = null
      }
    },

    async poll() {
      if (!this.jobId) return
      try {
        const { data } = await api.get<UpdateJob>(`/settings/updates/status/${this.jobId}`)
        this._applyJob(data)
        if (TERMINAL.has(data.status)) {
          this.stopPolling()
          this.active = false
          return data.status
        }
      } catch {
        /* backend может кратко перезапускаться */
      }
      return null
    },

    async resume() {
      try {
        const { data } = await api.get<UpdateJob | null>('/settings/updates/latest')
        if (data?.status === 'running') {
          this._applyJob(data)
          this.startPolling()
        }
      } catch {
        /* API недоступен на старой версии */
      }
    },

    async apply(): Promise<boolean> {
      const { data } = await api.post<UpdateJob>('/settings/updates/apply')
      this._applyJob(data)
      this.startPolling()
      return true
    },

    async cancel(): Promise<boolean> {
      try {
        const { data } = await api.post<UpdateJob | null>('/settings/updates/cancel')
        this.stopPolling()
        this.active = false
        if (data) this._applyJob(data)
        return true
      } catch {
        return false
      }
    },

    reset() {
      this.stopPolling()
      this.active = false
      this.jobId = null
      this.status = 'running'
      this.progress = 0
      this.message = 'Запуск обновления…'
    }
  }
})
