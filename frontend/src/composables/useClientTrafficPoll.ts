import { onUnmounted, watch, type Ref } from 'vue'

import { api } from '@/api/client'

export type ClientTrafficSnapshot = {
  id: string
  traffic_used_bytes: number
  traffic_up_bytes: number
  traffic_down_bytes: number
  last_handshake_at?: string | null
  online: boolean
  status: string
  blocked: boolean
}

type TrafficTarget = {
  id: string
  online: boolean
  traffic_used_bytes?: number
  traffic_up_bytes?: number
  traffic_down_bytes?: number
  last_handshake_at?: string | null
  status?: string
  blocked?: boolean
}

export function applyTrafficPatch(target: TrafficTarget, patch: ClientTrafficSnapshot): void {
  target.traffic_used_bytes = patch.traffic_used_bytes
  target.traffic_up_bytes = patch.traffic_up_bytes
  target.traffic_down_bytes = patch.traffic_down_bytes
  target.last_handshake_at = patch.last_handshake_at
  target.online = patch.online
  target.status = patch.status
  target.blocked = patch.blocked
}

export function useClientTrafficPoll(
  targets: Ref<TrafficTarget[] | TrafficTarget | null>,
  intervalMs = 10_000
) {
  let timer: ReturnType<typeof setInterval> | null = null
  let syncing = false

  // Поллим при наличии клиентов, а не только «когда кто-то online»:
  // иначе после рестарта интерфейса (ротация маскировки) все offline
  // и статус «в сети» никогда не оживёт без перезахода.
  function hasTargets(): boolean {
    const value = targets.value
    if (!value) return false
    if (Array.isArray(value)) return value.length > 0
    return true
  }

  async function syncTraffic() {
    if (!hasTargets() || syncing) return
    syncing = true
    try {
      const { data } = await api.post<ClientTrafficSnapshot[]>('/clients/sync-traffic')
      const value = targets.value
      if (!value) return

      if (Array.isArray(value)) {
        const byId = new Map(data.map((snap) => [snap.id, snap]))
        for (const client of value) {
          const patch = byId.get(client.id)
          if (patch) applyTrafficPatch(client, patch)
        }
        return
      }

      const patch = data.find((snap) => snap.id === value.id)
      if (patch) applyTrafficPatch(value, patch)
    } catch {
      // фоновое обновление — тихо игнорируем
    } finally {
      syncing = false
    }
  }

  function start() {
    stop()
    if (!hasTargets()) return
    timer = setInterval(() => void syncTraffic(), intervalMs)
  }

  function stop() {
    if (timer) clearInterval(timer)
    timer = null
  }

  watch(
    () => hasTargets(),
    (active) => {
      if (active) start()
      else stop()
    },
    { immediate: true }
  )

  onUnmounted(stop)

  return { syncTraffic, start, stop }
}
