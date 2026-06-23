// Флаг страны из ISO-3166 alpha-2 кода через regional indicator symbols.
// Эмодзи-флаги рендерятся на macOS/iOS/Android/Linux; на Windows возможен
// фолбэк в виде букв кода — для этого рядом показываем текстовый код в title.

export function flagEmoji(code?: string | null): string {
  const cc = (code || '').trim().toUpperCase()
  if (cc.length !== 2 || !/^[A-Z]{2}$/.test(cc)) return ''
  const base = 0x1f1e6 // regional indicator 'A'
  const a = cc.charCodeAt(0) - 65
  const b = cc.charCodeAt(1) - 65
  return String.fromCodePoint(base + a) + String.fromCodePoint(base + b)
}
