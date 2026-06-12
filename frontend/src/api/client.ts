import axios from 'axios'
import { createDiscreteApi } from 'naive-ui'

export const api = axios.create({
  baseURL: '/api/v1',
  timeout: 120_000
})

const { message } = createDiscreteApi(['message'])

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('utmka_access_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

api.interceptors.response.use(
  (response) => response,
  (error) => {
    const status = error.response?.status
    const detail = error.response?.data?.detail
    if (status === 401) {
      localStorage.removeItem('utmka_access_token')
      if (!window.location.pathname.startsWith('/login')) {
        window.location.href = '/login'
      }
    } else if (status === 403) {
      message.warning(typeof detail === 'string' ? detail : 'Недостаточно прав.')
    }
    return Promise.reject(error)
  }
)
