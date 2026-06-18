import { onActivated } from 'vue'

/**
 * Вызывает callback при каждом повторном показе закэшированной (<keep-alive>)
 * страницы, пропуская самую первую активацию — она совпадает с первичной
 * загрузкой в onMounted, чтобы не дублировать запрос.
 *
 * Паттерн stale-while-revalidate: данные показываются мгновенно из кэша,
 * а callback в фоне сверяет их с сервером («сервер всегда прав»).
 */
export function onRevisit(callback: () => void): void {
  let first = true
  onActivated(() => {
    if (first) {
      first = false
      return
    }
    callback()
  })
}
