export type CascadeAwareServer = {
  id: string
  name: string
  host?: string | null
  awg_cascade_active?: boolean
  awg_cascade_role?: string | null
  awg_cascade_exit_name?: string | null
  awg_cascade_peer_name?: string | null
  xray_cascade_active?: boolean
  xray_cascade_exit_name?: string | null
}

export function cascadeExitName(server: CascadeAwareServer): string {
  if (server.awg_cascade_role === 'entry' && server.awg_cascade_exit_name) {
    return server.awg_cascade_exit_name
  }
  if (server.xray_cascade_active && server.xray_cascade_exit_name) {
    return server.xray_cascade_exit_name
  }
  return ''
}

export function isCascadeEntry(server: CascadeAwareServer): boolean {
  return server.awg_cascade_role === 'entry' || !!server.xray_cascade_active
}

export function pickerKind(server: CascadeAwareServer): 'entry' | 'exit' | 'solo' {
  if (isCascadeEntry(server) && cascadeExitName(server)) return 'entry'
  if (server.awg_cascade_role === 'exit') return 'exit'
  return 'solo'
}

export function serverRouteLabel(server: CascadeAwareServer): string {
  const kind = pickerKind(server)
  if (kind === 'entry') return `${server.name} → ${cascadeExitName(server)}`
  if (kind === 'exit') return `${server.name} · прямой ключ`
  return server.name
}

export function clientRouteLabel(
  serverName?: string | null,
  exitName?: string | null
): string {
  const entry = (serverName || '').trim()
  const exit = (exitName || '').trim()
  if (entry && exit) return `${entry} → ${exit}`
  return entry
}

export function sortServersForClientPicker<T extends CascadeAwareServer>(servers: T[]): T[] {
  const rank = (server: T) => {
    const kind = pickerKind(server)
    if (kind === 'entry') return 0
    if (kind === 'solo') return 1
    return 2
  }
  return [...servers].sort(
    (a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name, 'ru')
  )
}
