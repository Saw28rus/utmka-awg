<template>
  <main class="login-page">
    <section class="login-panel panel">
      <div class="brand-row">
        <ShieldCheck :size="26" />
        <span>UTMka+AWG</span>
      </div>

      <div>
        <h1>Вход в панель</h1>
        <p>Управление AWG2-серверами</p>
      </div>

      <n-form class="login-form" @submit.prevent="submit">
        <n-form-item label="Email">
          <n-input v-model:value="email" autocomplete="email" placeholder="admin@utmka.app" />
        </n-form-item>
        <n-form-item label="Пароль">
          <n-input
            v-model:value="password"
            type="password"
            autocomplete="current-password"
            placeholder="Пароль"
          />
        </n-form-item>
        <n-button :loading="loading" type="primary" block @click="submit">
          Войти
        </n-button>
      </n-form>
    </section>
  </main>
</template>

<script setup lang="ts">
import { ShieldCheck } from '@lucide/vue'
import { NButton, NForm, NFormItem, NInput, useMessage } from 'naive-ui'
import { ref } from 'vue'
import { useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const message = useMessage()
const auth = useAuthStore()
const email = ref('admin@utmka.app')
const password = ref('admin12345')
const loading = ref(false)

async function submit() {
  loading.value = true
  try {
    await auth.login(email.value, password.value)
    router.push({ name: 'dashboard' })
  } catch {
    message.error('Неверный email или пароль.')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 24px;
  background:
    linear-gradient(180deg, rgba(111, 191, 154, 0.06), transparent 30%),
    var(--color-bg);
}

.login-panel {
  width: min(100%, 392px);
  padding: 24px;
}

.brand-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 28px;
  font-weight: 700;
}

.brand-row svg {
  color: var(--color-accent);
}

h1 {
  margin: 0;
  font-size: 24px;
  letter-spacing: 0;
}

p {
  margin: 6px 0 0;
  color: var(--color-muted);
}

.login-form {
  margin-top: 24px;
}
</style>
