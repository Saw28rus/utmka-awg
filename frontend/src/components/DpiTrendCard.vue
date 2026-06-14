<template>
  <div class="panel block dpi-card">
    <div class="dpi-head">
      <h3>Связь (DPI-тренд)</h3>
      <StatusBadge :label="levelLabel" :tone="levelTone" :pulse="data?.level === 'degraded'" />
    </div>

    <p class="dpi-hint">
      Доля клиентов на связи (handshake success). Резкое падение — возможна блокировка/деградация.
    </p>

    <div v-if="points.length >= 2" class="dpi-spark">
      <svg :viewBox="`0 0 ${vbW} ${vbH}`" preserveAspectRatio="none" class="spark-svg">
        <polyline :points="sparkPoints" fill="none" :stroke="strokeColor" stroke-width="2" />
      </svg>
    </div>
    <div v-else class="dpi-empty">
      Недостаточно данных. Срез снимается раз в 5 минут.
    </div>

    <div class="dpi-stats">
      <div class="dpi-stat">
        <span class="dpi-stat-label">сейчас</span>
        <strong>{{ pct(data?.recent_rate) }}</strong>
      </div>
      <div class="dpi-stat">
        <span class="dpi-stat-label">норма</span>
        <strong>{{ pct(data?.baseline_rate) }}</strong>
      </div>
      <div class="dpi-stat">
        <span class="dpi-stat-label">на связи</span>
        <strong>{{ lastOnline }}</strong>
      </div>
    </div>

    <div class="dpi-foot">
      <span class="dpi-points">{{ points.length }} точек</span>
      <n-button size="tiny" tertiary :loading="loading" @click="sampleNow">Обновить срез</n-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { NButton, useMessage } from 'naive-ui'
import { computed, onMounted, ref } from 'vue'

import { api } from '@/api/client'
import StatusBadge from '@/components/StatusBadge.vue'

type Sample = { ts: string; online: number; active: number; rate: number | null }
type DpiData = {
  server_id: string
  level: 'ok' | 'degraded'
  since?: string | null
  recent_rate: number | null
  baseline_rate: number | null
  samples: Sample[]
}

const props = defineProps<{ serverId: string }>()
const message = useMessage()
const data = ref<DpiData | null>(null)
const loading = ref(false)

const vbW = 100
const vbH = 28

const points = computed(() =>
  (data.value?.samples || []).filter((s) => s.rate !== null) as Array<Sample & { rate: number }>
)

const sparkPoints = computed(() => {
  const pts = points.value
  if (pts.length < 2) return ''
  const n = pts.length
  return pts
    .map((s, i) => {
      const x = (i / (n - 1)) * vbW
      const y = vbH - s.rate * vbH
      return `${x.toFixed(2)},${y.toFixed(2)}`
    })
    .join(' ')
})

const strokeColor = computed(() => (data.value?.level === 'degraded' ? '#fbbf24' : '#4ade80'))

const levelLabel = computed(() => (data.value?.level === 'degraded' ? 'Возможна деградация' : 'В норме'))
const levelTone = computed(() => (data.value?.level === 'degraded' ? 'warning' : 'ok') as 'warning' | 'ok')

const lastOnline = computed(() => {
  const last = points.value[points.value.length - 1]
  if (!last) return '—'
  return `${last.online}/${last.active}`
})

function pct(v?: number | null): string {
  if (v == null) return '—'
  return `${Math.round(v * 100)}%`
}

async function load() {
  try {
    const { data: res } = await api.get<DpiData>(`/dpi/${props.serverId}`)
    data.value = res
  } catch {
    /* API недоступен на старой версии */
  }
}

async function sampleNow() {
  loading.value = true
  try {
    await api.post('/dpi/sample')
    await load()
    message.success('Срез обновлён.')
  } catch (error: any) {
    message.error(error?.response?.data?.detail || 'Не удалось обновить срез.')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.dpi-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.dpi-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.dpi-head h3 {
  margin: 0;
}

.dpi-hint {
  margin: 0;
  font-size: 12px;
  color: var(--color-muted);
  line-height: 1.5;
}

.dpi-spark {
  height: 40px;
}

.spark-svg {
  width: 100%;
  height: 100%;
}

.dpi-empty {
  font-size: 12px;
  color: var(--color-dim);
  padding: 10px 0;
}

.dpi-stats {
  display: flex;
  gap: 18px;
}

.dpi-stat {
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.dpi-stat-label {
  font-size: 11px;
  color: var(--color-muted);
}

.dpi-stat strong {
  font-size: 15px;
  font-variant-numeric: tabular-nums;
}

.dpi-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-top: 1px solid var(--color-border);
  padding-top: 8px;
}

.dpi-points {
  font-size: 12px;
  color: var(--color-dim);
}
</style>
