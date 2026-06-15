import axios from 'axios'
import { createDiscreteApi } from 'naive-ui'

export const api = axios.create({
  baseURL: '/api/v1',
  timeout: 120_000
})

const { message } = createDiscreteApi(['message'])

const ACCESS_KEY = 'utmka_access_token'
const REFRESH_KEY = 'utmka_refresh_token'

let refreshPromise: Promise<boolean> | null = null

async function refreshAccessToken(): Promise<boolean> {
  const refresh = localStorage.getItem(REFRESH_KEY)
  if (!refresh) return false
  try {
    const { data } = await axios.post<{ access_token: string; refresh_token: string }>(
      '/api/v1/auth/refresh',
      { refresh_token: refresh }
    )
    localStorage.setItem(ACCESS_KEY, data.access_token)
    localStorage.setItem(REFRESH_KEY, data.refresh_token)
    return true
  } catch {
    return false
  }
}

function clearSession() {
  localStorage.removeItem(ACCESS_KEY)
  localStorage.removeItem(REFRESH_KEY)
}

function redirectToLogin() {
  clearSession()
  if (!window.location.pathname.startsWith('/login')) {
    window.location.href = '/login'
  }
}

api.interceptors.request.use((config) => {
  const token = localStorage.getItem(ACCESS_KEY)
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const status = error.response?.status
    const detail = error.response?.data?.detail
    const original = error.config as (typeof error.config & { _retry?: boolean }) | undefined

    if (
      status === 401 &&
      original &&
      !original._retry &&
      !original.url?.includes('/auth/login') &&
      !original.url?.includes('/auth/refresh')
    ) {
      original._retry = true
      if (!refreshPromise) {
        refreshPromise = refreshAccessToken().finally(() => {
          refreshPromise = null
        })
      }
      const ok = await refreshPromise
      if (ok) {
        const token = localStorage.getItem(ACCESS_KEY)
        if (token) {
          original.headers = original.headers || {}
          original.headers.Authorization = `Bearer ${token}`
        }
        return api(original)
      }
      redirectToLogin()
      return Promise.reject(error)
    }

    if (status === 401) {
      redirectToLogin()
    } else if (status === 403) {
      message.warning(typeof detail === 'string' ? detail : 'Недостаточно прав.')
    }
    return Promise.reject(error)
  }
)

export { ACCESS_KEY, REFRESH_KEY, clearSession }
