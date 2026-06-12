export type CascadeStateTone = 'ok' | 'info' | 'warning' | 'danger' | 'neutral'

const STATE_LABELS: Record<string, string> = {
  active: 'Работает',
  rolled_back: 'Выключен',
  preflight_ok: 'Готов',
  preflight_failed: 'Не готов',
  rollback_failed: 'Ошибка',
  down: 'Остановлен',
  draft: 'Черновик',
  none: 'Не настроен'
}

export function labelCascadeState(state: string): string {
  return STATE_LABELS[state] || 'Не настроен'
}

export function toneCascadeState(state: string): CascadeStateTone {
  if (state === 'active') return 'ok'
  if (state === 'preflight_ok' || state === 'draft') return 'info'
  if (state === 'rolled_back' || state === 'down') return 'warning'
  if (state === 'preflight_failed' || state === 'rollback_failed') return 'danger'
  return 'neutral'
}

export function labelCascadeRole(role: 'entry' | 'exit'): string {
  return role === 'entry' ? 'Вход' : 'Выход'
}
