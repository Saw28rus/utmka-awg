<template>
  <AppShell title="Пользователи" eyebrow="Роли и доступ">
    <div class="users-head">
      <p class="hint">Администратор — полный доступ. Модератор — только клиенты.</p>
      <n-button type="primary" @click="openCreate">Создать пользователя</n-button>
    </div>

    <div class="panel table-wrap">
      <n-data-table :columns="columns" :data="users" :loading="loading" :bordered="false" />
    </div>

    <n-modal v-model:show="showForm">
      <div class="panel modal-card">
        <h3>{{ editing ? 'Редактировать' : 'Новый пользователь' }}</h3>
        <label v-if="!editing" class="field">
          <span>Email</span>
          <n-input v-model:value="form.email" placeholder="user@example.com" />
        </label>
        <label class="field">
          <span>Имя</span>
          <n-input v-model:value="form.name" />
        </label>
        <label v-if="!editing" class="field">
          <span>Пароль</span>
          <div class="row">
            <n-input v-model:value="form.password" type="password" show-password-on="click" />
            <n-button tertiary @click="generatePassword">Сгенерировать</n-button>
          </div>
        </label>
        <label class="field">
          <span>Роль</span>
          <n-select v-model:value="form.role" :options="roleOptions" />
        </label>
        <label v-if="editing" class="field row-check">
          <n-checkbox v-model:checked="form.is_active">Активен</n-checkbox>
        </label>
        <div class="modal-actions">
          <n-button @click="showForm = false">Отмена</n-button>
          <n-button type="primary" :loading="saving" @click="saveUser">Сохранить</n-button>
        </div>
      </div>
    </n-modal>
  </AppShell>
</template>

<script setup lang="ts">
import { NButton, NCheckbox, NDataTable, NInput, NModal, NSelect, useMessage } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { h, onMounted, ref } from 'vue'

import { api } from '@/api/client'
import AppShell from '@/layouts/AppShell.vue'

type UserRow = {
  id: string
  email: string
  name: string
  role: string
  is_active: boolean
  last_login_at?: string | null
}

const message = useMessage()
const loading = ref(false)
const saving = ref(false)
const users = ref<UserRow[]>([])
const showForm = ref(false)
const editing = ref<UserRow | null>(null)

const form = ref({
  email: '',
  name: '',
  password: '',
  role: 'moderator',
  is_active: true
})

const roleOptions = [
  { label: 'Администратор', value: 'admin' },
  { label: 'Модератор', value: 'moderator' }
]

const columns: DataTableColumns<UserRow> = [
  { title: 'Email', key: 'email' },
  { title: 'Имя', key: 'name' },
  {
    title: 'Роль',
    key: 'role',
    render: (row) => (row.role === 'admin' ? 'Администратор' : 'Модератор')
  },
  {
    title: 'Активен',
    key: 'is_active',
    render: (row) => (row.is_active ? 'Да' : 'Нет')
  },
  {
    title: 'Последний вход',
    key: 'last_login_at',
    render: (row) => row.last_login_at?.replace('T', ' ').slice(0, 16) || '—'
  },
  {
    title: '',
    key: 'actions',
    render: (row) =>
      h('div', { class: 'row-actions' }, [
        h(NButton, { size: 'small', tertiary: true, onClick: () => openEdit(row) }, () => 'Изменить'),
        h(
          NButton,
          { size: 'small', tertiary: true, onClick: () => resetPassword(row) },
          () => 'Сброс пароля'
        ),
        h(
          NButton,
          { size: 'small', tertiary: true, type: 'error', onClick: () => removeUser(row) },
          () => 'Удалить'
        )
      ])
  }
]

onMounted(loadUsers)

async function loadUsers() {
  loading.value = true
  try {
    const { data } = await api.get<UserRow[]>('/users')
    users.value = data
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editing.value = null
  form.value = { email: '', name: '', password: '', role: 'moderator', is_active: true }
  showForm.value = true
}

function openEdit(row: UserRow) {
  editing.value = row
  form.value = {
    email: row.email,
    name: row.name,
    password: '',
    role: row.role,
    is_active: row.is_active
  }
  showForm.value = true
}

function generatePassword() {
  const chars = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789'
  form.value.password = Array.from({ length: 14 }, () => chars[Math.floor(Math.random() * chars.length)]).join('')
}

async function saveUser() {
  saving.value = true
  try {
    if (editing.value) {
      await api.patch(`/users/${editing.value.id}`, {
        name: form.value.name,
        role: form.value.role,
        is_active: form.value.is_active
      })
      message.success('Пользователь обновлён.')
    } else {
      await api.post('/users', form.value)
      message.success('Пользователь создан.')
    }
    showForm.value = false
    await loadUsers()
  } catch (e: unknown) {
    const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    message.error(typeof detail === 'string' ? detail : 'Не удалось сохранить.')
  } finally {
    saving.value = false
  }
}

async function resetPassword(row: UserRow) {
  const password = prompt('Новый пароль (мин. 8 символов):')
  if (!password) return
  try {
    await api.post(`/users/${row.id}/reset-password`, { password })
    message.success('Пароль сброшен.')
  } catch (e: unknown) {
    const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    message.error(typeof detail === 'string' ? detail : 'Ошибка сброса пароля.')
  }
}

async function removeUser(row: UserRow) {
  if (!confirm(`Удалить пользователя ${row.email}?`)) return
  try {
    await api.delete(`/users/${row.id}`)
    message.success('Пользователь удалён.')
    await loadUsers()
  } catch (e: unknown) {
    const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    message.error(typeof detail === 'string' ? detail : 'Не удалось удалить.')
  }
}
</script>

<style scoped>
.users-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}

.hint {
  margin: 0;
  color: var(--color-muted);
}

.table-wrap {
  padding: 8px;
}

.modal-card {
  width: min(480px, 92vw);
  padding: 20px;
  display: grid;
  gap: 12px;
}

h3 {
  margin: 0;
}

.field {
  display: grid;
  gap: 6px;
}

.field span {
  color: var(--color-muted);
  font-size: 13px;
}

.row {
  display: flex;
  gap: 8px;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 8px;
}

:deep(.row-actions) {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
</style>
