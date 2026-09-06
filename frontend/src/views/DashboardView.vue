<template>
  <AppShell title="Дашборд" eyebrow="Обзор инфраструктуры">
    <div class="dashboard" :aria-busy="loading">
      <section class="overview panel">
        <div class="overview-copy">
          <span class="kicker">ВАША СЕТЬ · ОБЗОР</span>
          <h2>{{ headline }}</h2>
          <p>{{ subtitle }}</p>
        </div>
        <div class="overview-actions">
          <span class="updated" role="status">{{ loading ? 'Обновляем данные…' : updatedAt ? `Обновлено в ${updatedAt}` : 'Данные ещё не загружены' }}</span>
          <button class="action" :disabled="loading" @click="load"><RefreshCw :size="15" :class="{ spinning: loading }" />Обновить</button>
        </div>
      </section>

      <div v-if="error" class="error-box" role="alert"><TriangleAlert :size="18" /><span>{{ error }}</span><button class="text-link" :disabled="loading" @click="load">Повторить</button></div>

      <section class="metrics" aria-label="Ключевые показатели">
        <article v-for="metric in metrics" :key="metric.label" class="metric panel" :class="metric.tone">
          <div class="metric-top"><span>{{ metric.label }}</span><component :is="metric.icon" :size="18" /></div>
          <strong class="metric-value">{{ metric.value }}<small v-if="metric.suffix">{{ metric.suffix }}</small></strong>
          <span class="metric-hint">{{ metric.hint }}</span>
        </article>
      </section>

      <div class="main-grid">
        <section class="fleet panel">
          <header class="section-head"><div><h2><Server :size="18" />Серверы <span class="count">{{ serversLoaded ? servers.length : '—' }}</span></h2><p>Доступность и подключённые клиенты</p></div><RouterLink v-if="isAdmin" class="text-link" to="/servers">Управление <ArrowUpRight :size="15" /></RouterLink></header>
          <div class="fleet-tools"><div class="filters" aria-label="Фильтр серверов"><button v-for="item in filters" :key="item.value" :class="{ selected: filter === item.value }" :aria-pressed="filter === item.value" @click="filter = item.value">{{ item.label }}</button></div><label class="search"><Search :size="15" /><input v-model="search" aria-label="Найти сервер" placeholder="Найти сервер" type="search" /></label></div>
          <div v-if="!serversLoaded" class="empty"><Server :size="28" /><h3>{{ loading ? 'Загружаем серверы' : 'Список недоступен' }}</h3><p>{{ loading ? 'Это займёт несколько секунд.' : 'Повторите обновление данных.' }}</p></div>
          <div v-else-if="!servers.length" class="empty"><Server :size="28" /><h3>Начните с первого сервера</h3><p>{{ isAdmin ? 'Подключите VPS, чтобы управлять вашей сетью.' : 'Администратор пока не добавил серверы.' }}</p><RouterLink v-if="isAdmin" class="action" to="/servers">Добавить сервер <ArrowUpRight :size="15" /></RouterLink></div>
          <div v-else-if="!visibleServers.length" class="empty"><Search :size="26" /><h3>Ничего не найдено</h3><p>Измените название или фильтр.</p><button class="text-link" @click="search = ''; filter = 'all'">Сбросить фильтры</button></div>
          <div v-else class="server-rows">
            <component :is="isAdmin ? RouterLink : 'div'" v-for="server in visibleServers" :key="server.id" v-bind="isAdmin ? { to: { name: 'server-detail', params: { id: server.id } } } : {}" class="server-row">
              <span class="server-icon" :class="{ offline: server.status !== 'online' }"><Server :size="19" /></span>
              <div class="server-info"><strong>{{ server.name }}</strong><span class="host">{{ server.host }}{{ isAdmin && server.ssh_port ? `:${server.ssh_port}` : '' }}</span><div class="protocols"><span v-for="protocol in server.protocols || []" :key="protocol">{{ protocol }}</span></div></div>
              <div v-if="isAdmin" class="peer-count"><strong>{{ server.active_peers ?? '—' }}</strong><span>клиентов</span></div>
              <StatusBadge :label="statusLabel(server.status)" :tone="server.status === 'online' ? 'ok' : server.status === 'offline' || server.status === 'error' ? 'danger' : 'neutral'" />
              <ChevronRight v-if="isAdmin" class="row-arrow" :size="16" />
            </component>
          </div>
          <footer v-if="serversLoaded && servers.length" class="fleet-footer"><span>Показано {{ visibleServers.length }} из {{ servers.length }}</span><span>Состояние по последней проверке</span></footer>
        </section>

        <aside class="side-stack">
          <section class="attention panel">
            <header class="section-head"><h2><TriangleAlert :size="18" />Требует внимания</h2></header>
            <div v-if="!summary" class="empty compact"><p>{{ loading ? 'Проверяем события…' : 'События недоступны' }}</p></div>
            <template v-else>
              <div v-if="unavailable > 0" class="notice"><span class="notice-dot danger" /><div><strong>Серверы не в сети: {{ unavailable }}</strong><p>Проверьте доступность и подключение.</p><button class="text-link" @click="filter = 'attention'; search = ''">Показать серверы <ArrowUpRight :size="14" /></button></div></div>
              <div v-if="!summary.alerts.length && !unavailable" class="all-clear"><ShieldCheck :size="30" /><strong>{{ summary.servers.total ? 'Всё под контролем' : 'Пока без событий' }}</strong><p>{{ summary.servers.total ? 'Нет предупреждений о серверах и клиентах.' : 'Здесь появятся важные события вашей сети.' }}</p></div>
              <div v-for="(alert, index) in summary.alerts" :key="`${alert.code}-${index}`" class="notice"><span class="notice-dot" :class="alert.level" /><div><p class="notice-message">{{ alert.message }}</p></div></div>
              <RouterLink v-if="summary.alerts.length" class="attention-link text-link" to="/clients">Перейти к клиентам <ArrowUpRight :size="15" /></RouterLink>
            </template>
          </section>
          <section class="quick panel"><h2>Быстрый доступ</h2><RouterLink to="/clients"><span class="quick-icon"><Users :size="18" /></span><span><strong>Клиенты</strong><small>Доступ, сроки и лимиты</small></span><ArrowUpRight :size="16" /></RouterLink><RouterLink v-if="isAdmin" to="/servers"><span class="quick-icon"><Server :size="18" /></span><span><strong>Серверы</strong><small>Подключение и настройка</small></span><ArrowUpRight :size="16" /></RouterLink></section>
        </aside>
      </div>
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { Activity, ArrowDownUp, ArrowUpRight, ChevronRight, Clock, RefreshCw, Search, Server, ShieldCheck, TriangleAlert, Users } from '@lucide/vue'
import { api } from '@/api/client'
import StatusBadge from '@/components/StatusBadge.vue'
import { onRevisit } from '@/composables/useRevisit'
import AppShell from '@/layouts/AppShell.vue'
import { useAuthStore } from '@/stores/auth'
import { formatBytes } from '@/utils/format'

defineOptions({ name: 'DashboardView' })
type DashboardSummary = {
  servers: { online: number; total: number }
  clients: { active: number; online: number; expiring_soon: number }
  traffic_total_bytes: number
  alerts: Array<{ level: string; code: string; message: string }>
}
type ServerListItem = { id: string; name: string; host: string; ssh_port?: number; status: string; active_peers?: number; protocols?: string[] }
const auth = useAuthStore()
const isAdmin = computed(() => auth.user?.role === 'admin')
const summary = ref<DashboardSummary | null>(null)
const servers = ref<ServerListItem[]>([])
const serversLoaded = ref(false)
const loading = ref(false)
const error = ref('')
const updatedAt = ref('')
const search = ref('')
const filter = ref('all')
const filters = [{ value: 'all', label: 'Все' }, { value: 'online', label: 'В сети' }, { value: 'attention', label: 'Не в сети' }]
const unavailable = computed(() => Math.max(0, (summary.value?.servers.total ?? 0) - (summary.value?.servers.online ?? 0)))
const headline = computed(() => error.value ? 'Не удалось обновить обзор' : !summary.value ? 'Ваша сеть — на одном экране' : !summary.value.servers.total ? 'Всё начинается с подключения' : unavailable.value ? 'Сеть требует внимания' : 'Все серверы в сети')
const subtitle = computed(() => error.value ? 'Проверьте соединение и повторите обновление.' : !summary.value ? 'Серверы, клиенты и важные события.' : !summary.value.servers.total ? 'Добавьте сервер и создайте первый клиентский доступ.' : `${summary.value.clients.online} клиентов онлайн · ${summary.value.clients.active} активных доступов`)
const metrics = computed(() => {
  const s = summary.value
  return [
    { label: 'Серверы в сети', value: s ? String(s.servers.online) : '—', suffix: s ? `/ ${s.servers.total}` : '', hint: s ? unavailable.value ? `${unavailable.value} не в сети` : s.servers.total ? 'Все серверы доступны' : 'Серверы пока не добавлены' : 'Ожидаем данные', icon: Server, tone: unavailable.value ? 'warning' : 'accent' },
    { label: 'Клиенты онлайн', value: s ? String(s.clients.online) : '—', suffix: '', hint: 'Подключались за последние 5 минут', icon: Activity, tone: 'info' },
    { label: 'Истекают за 7 дней', value: s ? String(s.clients.expiring_soon) : '—', suffix: '', hint: s ? s.clients.expiring_soon ? 'Пора продлить доступ' : 'Продление пока не требуется' : 'Ожидаем данные', icon: Clock, tone: s?.clients.expiring_soon ? 'warning' : '' },
    { label: 'Общий трафик', value: s ? formatBytes(s.traffic_total_bytes) : '—', suffix: '', hint: 'Накоплено по всем клиентам', icon: ArrowDownUp, tone: '' }
  ]
})
const visibleServers = computed(() => servers.value.filter(server => (filter.value === 'all' || (filter.value === 'online' ? server.status === 'online' : server.status !== 'online')) && `${server.name} ${server.host}`.toLowerCase().includes(search.value.trim().toLowerCase())).sort((a, b) => Number(a.status === 'online') - Number(b.status === 'online') || a.name.localeCompare(b.name, 'ru')))
function statusLabel(status: string) {
  return ({ online: 'В сети', offline: 'Не в сети', error: 'Ошибка', pending: 'Ожидание', unknown: 'Не проверен', installing: 'Установка' } as Record<string, string>)[status] || 'Не проверен'
}
async function load() {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    const [summaryRes, serversRes] = await Promise.all([
      api.get<DashboardSummary>('/dashboard/summary'),
      api.get<ServerListItem[]>(isAdmin.value ? '/servers' : '/servers/minimal')
    ])
    summary.value = summaryRes.data
    servers.value = serversRes.data
    serversLoaded.value = true
    updatedAt.value = new Date().toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
  } catch {
    error.value = summary.value ? 'Показаны последние загруженные данные. Их актуальность не подтверждена.' : 'Не удалось загрузить данные дашборда.'
  } finally {
    loading.value = false
  }
}
onMounted(load)
onRevisit(() => void load())
</script>

<style scoped>
.dashboard { display: grid; gap: 18px; max-width: 1500px; margin: 0 auto; }
.overview { display: flex; justify-content: space-between; align-items: center; gap: 24px; padding: 26px 28px; border-radius: 16px; background: radial-gradient(ellipse at 90% 0%, var(--color-accent-soft), transparent 65%), var(--color-surface); position: relative; overflow: hidden; }
.overview::before { content: ''; position: absolute; inset: 22px auto 22px 0; width: 3px; background: var(--color-accent); border-radius: 4px; }
.kicker { font-size: 10px; font-weight: 700; letter-spacing: 1.8px; color: var(--color-accent); }
.overview h2 { font-size: clamp(22px, 2.4vw, 30px); letter-spacing: -.8px; margin: 8px 0; line-height: 1.2; }
p { margin: 0; color: var(--color-muted); font-size: 13px; line-height: 1.55; }
.overview-actions { display: grid; justify-items: end; gap: 10px; flex-shrink: 0; }
.updated { font-size: 11px; color: var(--color-muted); }
button, input { font: inherit; }
button { cursor: pointer; }
button:disabled { cursor: wait; opacity: .55; }
.action { display: inline-flex; align-items: center; justify-content: center; gap: 8px; padding: 9px 13px; border: 1px solid var(--color-border-hover); border-radius: 8px; background: var(--color-surface); color: var(--color-text); font-size: 12px; font-weight: 600; }
.action:hover { border-color: var(--color-accent); background: var(--color-accent-soft); }
.metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; }
.metric { padding: 18px 20px; border-radius: 12px; min-width: 0; }
.metric-top { display: flex; justify-content: space-between; gap: 8px; align-items: center; font-size: 12px; color: var(--color-muted); }
.metric-top svg { color: var(--color-dim); }
.metric.accent .metric-top svg, .metric.accent .metric-value { color: var(--color-accent); }
.metric.info .metric-top svg { color: var(--color-info); }
.metric.warning .metric-top svg, .metric.warning .metric-value { color: var(--color-warning); }
.metric-value { display: block; margin: 12px 0 6px; font-size: clamp(26px, 2.5vw, 34px); font-weight: 650; letter-spacing: -1px; line-height: 1.2; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.metric-value small { color: var(--color-dim); font-size: 19px; font-weight: 400; margin-left: 7px; }
.metric-hint { font-size: 11px; color: var(--color-muted); }
.main-grid { display: grid; grid-template-columns: minmax(0, 1fr) 320px; gap: 18px; align-items: start; }
.fleet, .attention, .quick { border-radius: 12px; overflow: hidden; }
.section-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 19px 20px; border-bottom: 1px solid var(--color-border); }
h2 { margin: 0; font-size: 14px; font-weight: 650; }
.section-head h2 { display: flex; align-items: center; gap: 8px; }
.section-head h2 > svg { color: var(--color-muted); }
.section-head p { font-size: 11px; margin-top: 5px; }
.count { color: var(--color-muted); background: var(--color-surface-2); padding: 1px 7px; border-radius: 5px; font-size: 11px; }
.text-link { display: inline-flex; align-items: center; gap: 5px; border: 0; background: none; padding: 0; color: var(--color-accent); font-size: 12px; font-weight: 550; }
.text-link:hover { text-decoration: underline; }
.fleet-tools { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 13px 20px; border-bottom: 1px solid var(--color-border); }
.filters { display: flex; gap: 3px; }
.filters button { border: 0; border-radius: 6px; background: transparent; color: var(--color-muted); font-size: 11px; padding: 7px 9px; white-space: nowrap; }
.filters button.selected { background: var(--color-accent-soft); color: var(--color-accent); }
.search { display: flex; align-items: center; gap: 7px; color: var(--color-dim); max-width: 170px; }
.search input { width: 100%; min-width: 0; border: 0; background: transparent; color: var(--color-text); font-size: 12px; padding: 6px 0; }
.search input::placeholder { color: var(--color-dim); }
.server-rows { max-height: 420px; overflow-y: auto; }
.server-row { display: flex; align-items: center; gap: 12px; padding: 16px 20px; border-bottom: 1px solid var(--color-border); }
a.server-row:hover { background: var(--color-surface-2); }
.server-row:last-child { border-bottom: 0; }
.server-icon { display: grid; place-items: center; width: 38px; height: 38px; border-radius: 10px; background: var(--color-accent-soft); color: var(--color-accent); flex-shrink: 0; }
.server-icon.offline { color: var(--color-muted); background: var(--color-surface-2); }
.server-info { min-width: 0; flex: 1; }
.server-info > strong { display: block; font-size: 13px; overflow-wrap: anywhere; }
.host { display: block; color: var(--color-dim); font-size: 11px; margin-top: 3px; overflow-wrap: anywhere; }
.protocols { display: flex; gap: 4px; flex-wrap: wrap; }
.protocols span { font-size: 9px; text-transform: uppercase; color: var(--color-muted); background: var(--color-surface-2); border-radius: 3px; padding: 1px 5px; margin-top: 5px; }
.peer-count { display: grid; gap: 2px; text-align: right; margin-right: 8px; }
.peer-count strong { font-size: 14px; font-variant-numeric: tabular-nums; }
.peer-count span { font-size: 10px; color: var(--color-dim); }
.row-arrow { color: var(--color-dim); flex-shrink: 0; }
.fleet-footer { display: flex; justify-content: space-between; gap: 10px; border-top: 1px solid var(--color-border); padding: 12px 20px; color: var(--color-dim); font-size: 10px; }
.side-stack { display: grid; gap: 16px; }
.attention { max-height: 420px; overflow-y: auto; }
.notice { display: flex; gap: 10px; padding: 13px 20px; border-bottom: 1px solid var(--color-border); }
.notice-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--color-warning); flex-shrink: 0; margin-top: 7px; }
.notice-dot.info { background: var(--color-info); }
.notice-dot.danger { background: var(--color-danger); }
.notice strong { font-size: 12px; }
.notice p { font-size: 12px; overflow-wrap: anywhere; }
.notice .text-link { margin-top: 7px; font-size: 11px; }
.notice-message { color: var(--color-text); }
.attention-link { margin: 15px 20px; }
.all-clear { display: grid; justify-items: center; gap: 9px; padding: 26px 25px; text-align: center; }
.all-clear svg { color: var(--color-accent); }
.all-clear strong { font-size: 13px; }
.all-clear p { font-size: 12px; }
.quick { padding: 18px 20px 5px; }
.quick h2 { margin-bottom: 6px; }
.quick a { display: flex; gap: 10px; align-items: center; padding: 13px 0; }
.quick a + a { border-top: 1px solid var(--color-border); }
.quick a > span:nth-child(2) { flex: 1; }
.quick strong, .quick small { display: block; }
.quick strong { font-size: 12px; font-weight: 550; }
.quick small { font-size: 11px; color: var(--color-dim); margin-top: 3px; }
.quick-icon { color: var(--color-accent); background: var(--color-accent-soft); border-radius: 8px; padding: 9px; display: flex; }
.quick a > svg { color: var(--color-dim); }
.quick a:hover strong { color: var(--color-accent); }
.empty { display: grid; justify-items: center; gap: 10px; padding: 44px 20px; text-align: center; }
.empty > svg { color: var(--color-dim); }
.empty h3 { margin: 0; font-size: 14px; }
.empty .action { margin-top: 5px; }
.compact { padding: 22px; }
.error-box { display: flex; align-items: center; gap: 10px; padding: 13px 16px; border: 1px solid var(--color-warning); border-radius: 10px; color: var(--color-warning); font-size: 12px; }
.error-box span { flex: 1; }
:where(button, a, input):focus-visible { outline: 2px solid var(--color-accent); outline-offset: 3px; }
.spinning { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .spinning { animation: none; } }
@media (max-width: 1150px) { .main-grid { grid-template-columns: minmax(0, 1fr) 280px; } .metric { padding: 16px; } .fleet-tools { flex-wrap: wrap; } }
@media (max-width: 960px) { .main-grid { grid-template-columns: 1fr; } .side-stack { grid-template-columns: repeat(2, minmax(0, 1fr)); align-items: start; } .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 560px) { .dashboard { gap: 12px; } .overview { padding: 20px; align-items: start; flex-direction: column; gap: 16px; } .overview-actions { display: flex; flex-direction: row-reverse; align-items: center; justify-content: space-between; width: 100%; } .side-stack { grid-template-columns: 1fr; } .metrics { gap: 10px; } .metric { padding: 14px; } .metric-top { font-size: 11px; } .metric-top svg { width: 15px; flex-shrink: 0; } .metric-hint { font-size: 10px; } .section-head, .fleet-tools { padding: 14px; } .server-row { padding: 14px; gap: 9px; } .peer-count, .row-arrow { display: none; } .server-icon { width: 30px; height: 34px; } .fleet-footer { flex-wrap: wrap; padding: 12px 14px; } .search { max-width: none; width: 100%; } }
</style>
