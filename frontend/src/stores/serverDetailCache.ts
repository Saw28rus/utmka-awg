import { defineStore } from 'pinia'

/**
 * Кэш данных детальной страницы сервера, ключ — id сервера.
 * Детальные страницы зависят от :id, поэтому не кэшируются через <keep-alive>;
 * вместо этого держим последний снимок здесь и показываем его мгновенно при
 * повторном заходе, пока страница в фоне сверяется с сервером.
 *
 * Типы снимка намеренно `unknown` — конкретные интерфейсы (ServerRead,
 * ServerMetrics, ServerOverview) объявлены внутри ServerDetailView и приводятся
 * там же при чтении из кэша.
 */
export type ServerDetailSnapshot = {
  server: unknown
  metrics: unknown
  overview: unknown
}

export const useServerDetailCache = defineStore('serverDetailCache', {
  state: () => ({
    byId: {} as Record<string, ServerDetailSnapshot>
  }),
  actions: {
    snapshot(id: string): ServerDetailSnapshot | undefined {
      return this.byId[id]
    },
    patch(id: string, patch: Partial<ServerDetailSnapshot>) {
      const prev = this.byId[id] ?? { server: null, metrics: null, overview: null }
      this.byId[id] = { ...prev, ...patch }
    }
  }
})
