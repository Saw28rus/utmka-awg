<template>
  <AppShell title="Чат" eyebrow="Поддержка клиентов">
    <div class="chat-layout">
      <!-- Список диалогов -->
      <aside class="panel chat-side">
        <div class="chat-side-head">
          <strong>Диалоги</strong>
          <div class="chat-side-actions">
            <n-button v-if="isAdmin" size="tiny" tertiary @click="openCreateFolder">
              <template #icon><FolderPlus :size="14" /></template>
              Папка
            </n-button>
            <n-button v-if="isAdmin" size="tiny" tertiary @click="openAccounts">
              <template #icon><UserPlus :size="14" /></template>
              Аккаунты
            </n-button>
          </div>
        </div>

        <!-- Папки: вертикальный список (как в веб-чатах) -->
        <div v-if="folders.length" class="chat-folder-list">
          <button
            type="button"
            class="chat-folder-row"
            :class="{ active: activeFolderId === 'all' }"
            @click="activeFolderId = 'all'"
          >
            <Inbox :size="15" class="chat-folder-ico" />
            <span class="chat-folder-name">Все</span>
            <span class="chat-folder-count">{{ threads.length }}</span>
          </button>
          <button
            v-for="f in folders"
            :key="f.id"
            type="button"
            class="chat-folder-row"
            :class="{ active: activeFolderId === f.id }"
            @click="activeFolderId = f.id"
          >
            <Folder :size="15" class="chat-folder-ico" :style="f.color ? { color: f.color } : undefined" />
            <span class="chat-folder-name">{{ f.name }}</span>
            <span class="chat-folder-count">{{ f.count }}</span>
            <span
              v-if="isAdmin"
              class="chat-folder-x"
              title="Удалить папку"
              @click.stop="confirmDeleteFolder(f)"
            >×</span>
          </button>
          <button
            type="button"
            class="chat-folder-row"
            :class="{ active: activeFolderId === 'none' }"
            @click="activeFolderId = 'none'"
          >
            <FolderOpen :size="15" class="chat-folder-ico" />
            <span class="chat-folder-name">Без папки</span>
            <span class="chat-folder-count">{{ noFolderCount }}</span>
          </button>
        </div>

        <div v-if="threadsLoading && !threads.length" class="chat-empty">
          <n-spin size="small" />
        </div>
        <div v-else-if="!threads.length" class="chat-empty">
          <MessagesSquare :size="22" />
          <p>Диалогов пока нет.</p>
          <p v-if="isAdmin" class="chat-empty-hint">
            Создайте клиенту аккаунт чата (кнопка «Аккаунты») и передайте ему логин, пароль и адрес
            <a v-if="status?.public_url" :href="status.public_url" target="_blank" rel="noopener">{{ status.public_url }}</a>.
          </p>
        </div>

        <button
          v-for="t in filteredThreads"
          :key="t.id"
          type="button"
          class="chat-thread"
          :class="{ active: t.id === activeThreadId, unread: t.unread_count > 0 }"
          @click="selectThread(t.id)"
        >
          <div class="chat-thread-top">
            <span class="chat-thread-name">
              {{ t.display_name || t.username }}
            </span>
            <span v-if="t.unread_count > 0" class="chat-thread-unread">
              {{ t.unread_count > 99 ? '99+' : t.unread_count }}
            </span>
            <span v-else-if="t.status === 'resolved'" class="chat-thread-resolved">решён</span>
          </div>
          <div class="chat-thread-preview">
            <template v-if="t.last_preview">
              <span v-if="t.last_sender === 'admin'" class="muted">Вы: </span>{{ t.last_preview }}
            </template>
            <span v-else class="muted">нет сообщений</span>
          </div>
        </button>
      </aside>

      <!-- Активный диалог -->
      <section class="panel chat-main">
        <div v-if="!activeThread" class="chat-empty tall">
          <MessagesSquare :size="26" />
          <p>Выберите диалог слева.</p>
        </div>

        <template v-else>
          <div class="chat-main-head">
            <div class="chat-head-info">
              <div>
                <strong>{{ activeThread.display_name || activeThread.username }}</strong>
                <span class="muted mono"> @{{ activeThread.username }}</span>
                <span v-if="!activeThread.user_is_active" class="chat-thread-resolved">отключён</span>
              </div>
              <div class="chat-head-link">
                <template v-if="activeThread.client_missing">
                  <span class="chat-link-chip warn">VPN-клиент удалён — перепривяжите</span>
                </template>
                <template v-else-if="activeThread.client_name">
                  <span class="chat-link-chip">VPN: {{ activeThread.client_name }}</span>
                </template>
                <span v-else class="chat-link-chip muted-chip">VPN-клиент не привязан</span>
                <n-button v-if="isAdmin" size="tiny" quaternary @click="openLink(activeThread)">
                  {{ activeThread.client_id ? 'изменить' : 'привязать' }}
                </n-button>
              </div>
            </div>
            <div class="chat-head-actions">
              <n-button
                v-if="activeThread.client_id && !activeThread.client_missing"
                size="tiny"
                tertiary
                :loading="keyBusy"
                @click="confirmSendKey"
              >
                <template #icon><KeyRound :size="14" /></template>
                Выдать ключ
              </n-button>
              <n-button
                v-if="canProvision"
                size="tiny"
                tertiary
                @click="openProvision"
              >
                <template #icon><Plus :size="14" /></template>
                Создать ключ
              </n-button>
              <n-button
                v-if="activeThread.client_id && !activeThread.client_missing"
                size="tiny"
                tertiary
                @click="openInvoices"
              >
                <template #icon><Receipt :size="14" /></template>
                Счёт
              </n-button>
              <n-button size="tiny" tertiary :loading="statusBusy" @click="toggleResolved">
                {{ activeThread.status === 'resolved' ? 'Открыть снова' : 'Пометить решённым' }}
              </n-button>
              <n-dropdown
                v-if="isAdmin"
                trigger="click"
                :options="adminMenuOptions"
                @select="onAdminMenu"
              >
                <n-button size="tiny" tertiary title="Управление аккаунтом">
                  <template #icon><Settings :size="14" /></template>
                </n-button>
              </n-dropdown>
            </div>
          </div>

          <div ref="messagesBox" class="chat-messages">
            <div v-if="messagesLoading && !messages.length" class="chat-empty">
              <n-spin size="small" />
            </div>
            <div
              v-for="m in messages"
              :key="m.id"
              class="chat-msg"
              :class="m.sender === 'client' ? 'from-client' : 'from-admin'"
            >
              <div class="chat-msg-body">{{ m.body }}</div>
              <div v-if="m.attachment" class="chat-msg-att" :class="{ expired: m.attachment.expired }">
                <KeyRound :size="13" />
                <span>{{ m.attachment.filename }}</span>
                <span v-if="m.attachment.expired" class="muted">— срок истёк</span>
              </div>
              <div class="chat-msg-time">{{ formatTime(m.created_at) }}</div>
            </div>
          </div>

          <div class="chat-compose">
            <n-input
              v-model:value="draft"
              type="textarea"
              :autosize="{ minRows: 1, maxRows: 5 }"
              placeholder="Сообщение клиенту…"
              :disabled="sending"
              @keydown="onComposeKeydown"
            />
            <n-button type="primary" :loading="sending" :disabled="!draft.trim()" @click="sendMessage">
              <template #icon><Send :size="15" /></template>
            </n-button>
          </div>
        </template>
      </section>
    </div>

    <!-- Аккаунты чата -->
    <n-modal v-model:show="showAccounts">
      <div class="panel modal-card chat-accounts">
        <div class="acc-head">
          <h3>Аккаунты чата</h3>
          <div class="acc-head-actions">
            <n-button size="small" tertiary :disabled="!accounts.length" @click="exportAccounts">Экспорт</n-button>
            <n-button size="small" type="primary" @click="showCreateForm = !showCreateForm">
              {{ showCreateForm ? 'Свернуть' : 'Создать' }}
            </n-button>
          </div>
        </div>

        <p class="hint">
          Отдельный логин/пароль для клиента (не VPN-ключ и не пользователь панели).
          Адрес для входа: <span class="mono">{{ status?.public_url || '—' }}</span>.
        </p>

        <transition name="acc-fold">
          <div v-if="showCreateForm" class="acc-create">
            <n-input v-model:value="newUsername" placeholder="логин: латиница, цифры, _" :disabled="creating" @keyup.enter="createAccount" />
            <n-input v-model:value="newDisplayName" placeholder="Имя (необязательно)" :disabled="creating" @keyup.enter="createAccount" />
            <n-button type="primary" :loading="creating" @click="createAccount">Создать</n-button>
          </div>
        </transition>

        <n-input
          v-if="accounts.length > 5"
          v-model:value="accountSearch"
          placeholder="Поиск по имени, логину или VPN"
          clearable
        >
          <template #prefix><Search :size="15" /></template>
        </n-input>

        <div class="acc-table">
          <div class="acc-tr acc-thead">
            <span>Клиент</span>
            <span>Логин</span>
            <span>VPN</span>
            <span class="acc-th-act">Действия</span>
          </div>
          <div class="acc-scroll">
            <div v-for="u in filteredAccounts" :key="u.id" class="acc-tr acc-row" :class="{ blocked: !u.is_active }">
              <div class="acc-client">
                <span class="entity-avatar entity-avatar--md">
                  {{ (u.display_name || u.username).slice(0, 1).toUpperCase() }}
                </span>
                <span class="acc-name">
                  <strong>{{ u.display_name || u.username }}</strong>
                  <span v-if="!u.is_active" class="chat-acc-badge">блок</span>
                </span>
              </div>
              <span class="acc-login mono">@{{ u.username }}</span>
              <span class="acc-vpn">
                <span v-if="u.client_name" class="chat-link-chip">{{ u.client_name }}</span>
                <span v-else class="dim">—</span>
              </span>
              <span class="acc-act">
                <n-dropdown
                  trigger="click"
                  placement="bottom-end"
                  to="body"
                  :show-arrow="false"
                  :options="accountMenuOptions(u)"
                  @select="(k: string) => onAccountMenu(k, u)"
                >
                  <n-button size="small" tertiary circle title="Действия">
                    <template #icon><MoreHorizontal :size="16" /></template>
                  </n-button>
                </n-dropdown>
              </span>
            </div>
            <p v-if="!accounts.length" class="muted acc-empty">Аккаунтов пока нет — нажмите «Создать».</p>
            <p v-else-if="!filteredAccounts.length" class="muted acc-empty">Ничего не найдено.</p>
          </div>
        </div>

        <div class="acc-foot">
          <span class="muted">{{ accounts.length }} {{ plural(accounts.length, 'аккаунт', 'аккаунта', 'аккаунтов') }}</span>
          <n-button size="small" @click="showAccounts = false">Закрыть</n-button>
        </div>
      </div>
    </n-modal>

    <!-- Привязка VPN-клиента -->
    <n-modal v-model:show="showLink">
      <div class="panel modal-card">
        <h3>Привязка VPN-клиента</h3>
        <p class="hint">
          Привязка позволяет вставлять в диалог счета этого клиента (а далее — ключ/QR).
          Аккаунт чата: <span class="mono">@{{ linkTarget?.username }}</span>
        </p>
        <n-select
          v-model:value="linkClientId"
          :options="clientOptions"
          filterable
          clearable
          placeholder="Выберите VPN-клиента (пусто — отвязать)"
          :loading="clientsLoading"
        />
        <n-checkbox
          v-if="linkFromThreadId && linkClientId"
          v-model:checked="linkSendAfter"
          style="margin-top: 12px"
        >
          Сразу отправить ключ в чат
        </n-checkbox>
        <div class="modal-actions">
          <n-button @click="showLink = false">Отмена</n-button>
          <n-button type="primary" :loading="linkBusy" @click="saveLink">Сохранить</n-button>
        </div>
      </div>
    </n-modal>

    <!-- Создание VPN-клиента из чата (provision-client) -->
    <n-modal v-model:show="showProvision">
      <div class="panel modal-card">
        <h3>Создать ключ и отправить в чат</h3>
        <p class="hint">
          Новый VPN-клиент будет создан, привязан к аккаунту
          <span class="mono">@{{ activeThread?.username }}</span> и отправлен в диалог.
          Если был привязан другой клиент — он отвяжется, но останется в «Клиенты».
        </p>
        <n-spin :show="provisionLoading">
          <div class="prov-grid">
            <label class="prov-field">
              <span>Сервер</span>
              <n-select
                v-model:value="provForm.server_id"
                :options="provisionServerOptions"
                filterable
                placeholder="Выберите сервер"
              />
            </label>
            <label class="prov-field">
              <span>Протокол</span>
              <n-select
                v-model:value="provForm.protocol"
                :options="provisionProtocolOptions"
                placeholder="Протокол"
              />
            </label>
            <label class="prov-field">
              <span>Имя клиента</span>
              <n-input v-model:value="provForm.name" placeholder="Имя клиента" />
            </label>
            <label v-if="provIsXray" class="prov-field">
              <span>Отпечаток TLS (fingerprint)</span>
              <n-select v-model:value="provForm.fingerprint" :options="provFingerprintOptions" />
            </label>
            <label class="prov-field">
              <span>Лимит трафика, ГБ (пусто — без лимита)</span>
              <n-input v-model:value="provForm.traffic_gb" placeholder="например, 100" />
            </label>
            <label class="prov-field">
              <span>Действует до (пусто — бессрочно)</span>
              <input v-model="provForm.expires_at" type="date" class="prov-date" />
            </label>
          </div>
          <p v-if="provCascadeHint" class="hint cascade-hint">{{ provCascadeHint }}</p>
        </n-spin>
        <div class="modal-actions">
          <n-button @click="showProvision = false">Отмена</n-button>
          <n-button
            type="primary"
            :loading="provisionBusy"
            :disabled="!provForm.server_id || !provForm.protocol"
            @click="submitProvision"
          >
            Создать и отправить
          </n-button>
        </div>
      </div>
    </n-modal>

    <!-- Счета привязанного клиента -->
    <n-modal v-model:show="showInvoices">
      <div class="panel modal-card chat-accounts">
        <h3>Вставить счёт в чат</h3>
        <p class="hint">
          Счета клиента <strong>{{ activeThread?.client_name }}</strong>. Новый счёт создаётся в разделе
          «Счета и оплата», после чего появится здесь.
        </p>
        <div v-if="invoicesLoading" class="chat-empty"><n-spin size="small" /></div>
        <div v-else class="chat-acc-list">
          <div v-for="inv in invoices" :key="inv.id" class="chat-acc-row">
            <div class="chat-acc-info chat-inv-info">
              <strong>{{ inv.amount_rub }} ₽</strong>
              <span class="muted">{{ inv.description || 'без описания' }}</span>
              <span class="chat-link-chip" :class="{ warn: inv.status !== 'pending' && inv.status !== 'succeeded' }">
                {{ invoiceStatusLabel(inv.status) }}
              </span>
            </div>
            <n-button size="tiny" type="primary" tertiary :loading="insertBusy === inv.id" @click="insertInvoice(inv)">
              Вставить
            </n-button>
          </div>
          <p v-if="!invoices.length" class="muted">У клиента пока нет счетов.</p>
        </div>
        <div class="modal-actions">
          <n-button @click="showInvoices = false">Закрыть</n-button>
        </div>
      </div>
    </n-modal>

    <!-- Создание папки -->
    <n-modal v-model:show="showFolder">
      <div class="panel modal-card">
        <h3>Новая папка</h3>
        <p class="hint">Сгруппируйте диалоги: «Друзья», «Семья», «NL каскад» и т.п.</p>
        <n-input
          v-model:value="folderName"
          placeholder="Название папки"
          maxlength="64"
          :disabled="folderBusy"
          @keyup.enter="doCreateFolder"
        />
        <div class="modal-actions">
          <n-button @click="showFolder = false">Отмена</n-button>
          <n-button type="primary" :loading="folderBusy" @click="doCreateFolder">Создать</n-button>
        </div>
      </div>
    </n-modal>

    <!-- Сброс пароля -->
    <n-modal v-model:show="showReset">
      <div class="panel modal-card">
        <h3>Сбросить пароль</h3>
        <p class="hint">
          Старый пароль перестанет работать, активные сессии клиента сбросятся.
          Оставьте поле пустым — сгенерируем случайный пароль.
        </p>
        <n-input
          v-model:value="resetCustomPassword"
          type="password"
          show-password-on="click"
          placeholder="Свой пароль (необязательно, мин. 8 символов)"
          :disabled="resetBusy"
        />
        <div class="modal-actions">
          <n-button @click="showReset = false">Отмена</n-button>
          <n-button type="primary" :loading="resetBusy" @click="doResetPassword">Сбросить</n-button>
        </div>
      </div>
    </n-modal>

    <!-- Удаление аккаунта -->
    <n-modal v-model:show="showDelete">
      <div class="panel modal-card">
        <h3>Удалить аккаунт навсегда</h3>
        <p class="hint">
          Будут удалены аккаунт <strong>@{{ deleteUsername }}</strong>, вся переписка, ключи и push-подписки.
          VPN-клиент и оплаченные счета не затрагиваются. Действие необратимо.
        </p>
        <p class="hint">Для подтверждения введите <strong>@{{ deleteUsername }}</strong>:</p>
        <n-input
          v-model:value="deleteConfirmText"
          :placeholder="'@' + deleteUsername"
          :disabled="deleteBusy"
        />
        <div class="modal-actions">
          <n-button @click="showDelete = false">Отмена</n-button>
          <n-button
            type="error"
            :loading="deleteBusy"
            :disabled="deleteConfirmText.trim() !== '@' + deleteUsername"
            @click="doDeleteAccount"
          >
            Удалить навсегда
          </n-button>
        </div>
      </div>
    </n-modal>

    <!-- Пароль (показывается один раз) -->
    <n-modal v-model:show="showPassword" :mask-closable="false">
      <div class="panel modal-card">
        <h3>Данные для входа клиента</h3>
        <p class="hint">Пароль показывается <strong>один раз</strong> — скопируйте и отправьте клиенту.</p>
        <div class="chat-cred">
          <div class="chat-cred-row"><span>Адрес</span><span class="mono">{{ status?.public_url || '—' }}</span></div>
          <div class="chat-cred-row"><span>Логин</span><span class="mono">{{ issuedLogin }}</span></div>
          <div class="chat-cred-row"><span>Пароль</span><span class="mono">{{ issuedPassword }}</span></div>
        </div>
        <div class="modal-actions">
          <n-button tertiary @click="copyCredentials">
            <template #icon><Copy :size="14" /></template>
            Скопировать всё
          </n-button>
          <n-button type="primary" @click="showPassword = false">Готово</n-button>
        </div>
      </div>
    </n-modal>
  </AppShell>
</template>

<script setup lang="ts">
import {
  Copy,
  Folder,
  FolderOpen,
  FolderPlus,
  Inbox,
  KeyRound,
  MessagesSquare,
  MoreHorizontal,
  Plus,
  Receipt,
  Search,
  Send,
  Settings,
  UserPlus
} from '@lucide/vue'
import {
  NButton,
  NCheckbox,
  NDropdown,
  NInput,
  NModal,
  NSelect,
  NSpin,
  useDialog,
  useMessage
} from 'naive-ui'
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'

import { api } from '@/api/client'
import AppShell from '@/layouts/AppShell.vue'
import { useAuthStore } from '@/stores/auth'
import { useChatUnreadStore } from '@/stores/chatUnread'

type ChatStatus = {
  enabled: boolean
  domain: string | null
  public_url: string | null
  moderator_access: boolean
  unread_messages?: number
  unread_threads?: number
}

type ThreadRow = {
  id: string
  status: string
  folder_id: string | null
  last_message_at: string | null
  username: string
  display_name: string | null
  user_is_active: boolean
  client_id: string | null
  client_name: string | null
  client_missing: boolean
  chat_user_id: string | null
  last_preview: string | null
  last_sender: string | null
  unread_count: number
}

type FolderRow = { id: string; name: string; color: string | null; sort_order: number; count: number }

type AttachmentInfo = { id: string; kind: string; filename: string; expires_at: string; expired: boolean }

type MessageRow = {
  id: number
  sender: string
  body: string
  created_at: string
  attachment?: AttachmentInfo | null
}

type AccountRow = {
  id: string
  username: string
  display_name: string | null
  client_id: string | null
  client_name: string | null
  is_active: boolean
  last_login_at: string | null
}

type ClientOption = { id: string; name: string; server_name?: string | null }

type ServerRow = {
  id: string
  name: string
  host?: string | null
  client_protocols?: string[]
  awg2_imported?: boolean
  xray_cascade_active?: boolean
  xray_cascade_exit_name?: string | null
}

type InvoiceRow = {
  id: string
  description: string | null
  amount_rub: string
  status: string
  pay_url: string | null
  created_at: string
  expires_at: string | null
}

const message = useMessage()
const dialog = useDialog()
const auth = useAuthStore()
const chatUnread = useChatUnreadStore()
const isAdmin = computed(() => auth.user?.role === 'admin')
// Операторские действия с клиентами (выдать/привязать/создать) доступны
// admin и moderator — как и доступ к чату на бэкенде (require_chat_access).
const canProvision = computed(() => auth.user?.role === 'admin' || auth.user?.role === 'moderator')

const status = ref<ChatStatus | null>(null)
const threads = ref<ThreadRow[]>([])
const threadsLoading = ref(false)
const activeThreadId = ref('')
const activeThread = computed(() => threads.value.find((t) => t.id === activeThreadId.value) || null)

const folders = ref<FolderRow[]>([])
const activeFolderId = ref<'all' | 'none' | string>('all')
const noFolderCount = computed(() => threads.value.filter((t) => !t.folder_id).length)
const filteredThreads = computed(() => {
  if (activeFolderId.value === 'all') return threads.value
  if (activeFolderId.value === 'none') return threads.value.filter((t) => !t.folder_id)
  return threads.value.filter((t) => t.folder_id === activeFolderId.value)
})

const showFolder = ref(false)
const folderName = ref('')
const folderBusy = ref(false)

const messages = ref<MessageRow[]>([])
const messagesLoading = ref(false)
const messagesBox = ref<HTMLElement | null>(null)
const draft = ref('')
const sending = ref(false)
const statusBusy = ref(false)

const showAccounts = ref(false)
const accounts = ref<AccountRow[]>([])
const accountSearch = ref('')
const filteredAccounts = computed(() => {
  const q = accountSearch.value.trim().toLowerCase()
  if (!q) return accounts.value
  return accounts.value.filter((u) =>
    [u.display_name, u.username, u.client_name].some((v) => (v || '').toLowerCase().includes(q))
  )
})
const newUsername = ref('')
const newDisplayName = ref('')
const showCreateForm = ref(false)
const creating = ref(false)
const showPassword = ref(false)
const issuedLogin = ref('')
const issuedPassword = ref('')

const showLink = ref(false)
const linkTarget = ref<{ id: string; username: string } | null>(null)
const linkClientId = ref<string | null>(null)
const linkBusy = ref(false)
const clientsLoading = ref(false)
const clientOptions = ref<{ label: string; value: string }[]>([])
// Привязка из диалога умеет сразу выдать ключ в чат (link-and-send-key).
const linkFromThreadId = ref<string | null>(null)
const linkSendAfter = ref(true)

// --- создание клиента из чата (provision-client) -------------------------------
const LAST_PROVISION_SERVER = 'chat.provision.lastServer'
const showProvision = ref(false)
const provisionBusy = ref(false)
const provisionLoading = ref(false)
const provisionServers = ref<ServerRow[]>([])
const provForm = reactive({
  server_id: '',
  protocol: 'awg2',
  name: '',
  format: 'both',
  fingerprint: 'chrome',
  expires_at: '',
  traffic_gb: ''
})
const PROVISION_PROTOCOL_LABELS: Record<string, string> = {
  awg2: 'AmneziaWG',
  awg_legacy: 'AmneziaWG (legacy)',
  xray: 'Xray (VLESS-Reality)'
}
const provFingerprintOptions = [
  { value: 'chrome', label: 'Chrome (рекомендуется)' },
  { value: 'safari', label: 'Safari' },
  { value: 'ios', label: 'iOS' },
  { value: 'firefox', label: 'Firefox' },
  { value: 'android', label: 'Android' },
  { value: 'edge', label: 'Edge' },
  { value: 'random', label: 'Случайный' }
]
const provisionServerOptions = computed(() =>
  provisionServers.value
    .filter((s) => (s.client_protocols?.length || (s.awg2_imported ? 1 : 0)) > 0)
    .map((s) => ({ label: s.name, value: s.id }))
)
const provisionSelectedServer = computed(() =>
  provisionServers.value.find((s) => s.id === provForm.server_id)
)
const provisionProtocolOptions = computed(() => {
  const s = provisionSelectedServer.value
  const ids = s?.client_protocols?.length
    ? [...s.client_protocols]
    : s?.awg2_imported
      ? ['awg2']
      : []
  return ids.map((id) => ({ label: PROVISION_PROTOCOL_LABELS[id] || id, value: id }))
})
const provIsXray = computed(() => provForm.protocol === 'xray')
const provCascadeHint = computed(() => {
  const s = provisionSelectedServer.value
  if (!provIsXray.value || !s?.xray_cascade_active) return ''
  return `Xray-каскад → ${s.xray_cascade_exit_name || 'exit'}: РФ-трафик выходит на этом сервере, остальное уходит на exit (правило на сервере).`
})

watch(
  () => provForm.server_id,
  () => syncProvisionProtocol()
)

const showInvoices = ref(false)
const invoices = ref<InvoiceRow[]>([])
const invoicesLoading = ref(false)
const insertBusy = ref('')
const keyBusy = ref(false)

const showReset = ref(false)
const resetTargetId = ref('')
const resetCustomPassword = ref('')
const resetBusy = ref(false)

const showDelete = ref(false)
const deleteTargetId = ref('')
const deleteUsername = ref('')
const deleteConfirmText = ref('')
const deleteBusy = ref(false)

const adminMenuOptions = computed(() => {
  const moveChildren: any[] = folders.value.map((f) => ({
    label: f.name,
    key: 'move:' + f.id,
    disabled: activeThread.value?.folder_id === f.id
  }))
  moveChildren.push({
    label: 'Без папки',
    key: 'move:none',
    disabled: !activeThread.value?.folder_id
  })
  return [
    { label: 'Сбросить пароль', key: 'reset' },
    {
      label: activeThread.value?.user_is_active ? 'Заблокировать вход' : 'Разблокировать',
      key: 'toggle'
    },
    { label: 'Переместить в папку', key: 'move', children: moveChildren },
    { type: 'divider', key: 'd1' },
    {
      label: 'Удалить аккаунт',
      key: 'delete',
      props: { style: 'color: var(--error-color, #e88080)' }
    }
  ]
})

function onAdminMenu(key: string) {
  const t = activeThread.value
  if (!t || !t.chat_user_id) return
  if (key === 'reset') openResetPassword(t.chat_user_id)
  else if (key === 'toggle') toggleActiveById(t.chat_user_id, !t.user_is_active)
  else if (key === 'delete') openDeleteAccount(t.chat_user_id, t.display_name || t.username, t.username)
  else if (key.startsWith('move:')) {
    const fid = key.slice(5)
    moveThreadToFolder(t.id, fid === 'none' ? null : fid)
  }
}

function plural(n: number, one: string, few: string, many: string): string {
  const mod10 = n % 10
  const mod100 = n % 100
  if (mod10 === 1 && mod100 !== 11) return one
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return few
  return many
}

function accountMenuOptions(u: AccountRow): any[] {
  return [
    { label: u.client_id ? 'VPN-клиент' : 'Привязать VPN', key: 'link' },
    { label: 'Новый пароль', key: 'reset' },
    { label: u.is_active ? 'Заблокировать вход' : 'Разблокировать', key: 'toggle' },
    { type: 'divider', key: 'd1' },
    { label: 'Удалить аккаунт', key: 'delete', props: { style: 'color: var(--error-color, #e88080)' } }
  ]
}

function onAccountMenu(key: string, u: AccountRow) {
  if (key === 'link') openLinkAccount(u)
  else if (key === 'reset') openResetPassword(u.id)
  else if (key === 'toggle') toggleActive(u)
  else if (key === 'delete') openDeleteAccount(u.id, u.display_name || u.username, u.username)
}

function openResetPassword(userId: string) {
  resetTargetId.value = userId
  resetCustomPassword.value = ''
  showReset.value = true
}

async function doResetPassword() {
  if (!resetTargetId.value) return
  resetBusy.value = true
  try {
    const pwd = resetCustomPassword.value.trim()
    const { data } = await api.post<{ user: AccountRow; password: string }>(
      `/chat/admin/users/${resetTargetId.value}/reset-password`,
      { password: pwd || null }
    )
    showReset.value = false
    issuedLogin.value = data.user.username
    issuedPassword.value = data.password
    showPassword.value = true
    await loadAccounts()
  } catch (error: any) {
    message.error(error?.response?.data?.detail || 'Не удалось сбросить пароль.')
  } finally {
    resetBusy.value = false
  }
}

function openDeleteAccount(userId: string, _display: string, username: string) {
  deleteTargetId.value = userId
  deleteUsername.value = username
  deleteConfirmText.value = ''
  showDelete.value = true
}

async function doDeleteAccount() {
  if (!deleteTargetId.value) return
  deleteBusy.value = true
  try {
    await api.delete(`/chat/admin/users/${deleteTargetId.value}`)
    showDelete.value = false
    message.success('Аккаунт удалён.')
    if (activeThreadId.value && activeThread.value?.chat_user_id === deleteTargetId.value) {
      activeThreadId.value = ''
    }
    await Promise.all([loadThreads(), loadAccounts()])
  } catch (error: any) {
    message.error(error?.response?.data?.detail || 'Не удалось удалить аккаунт.')
  } finally {
    deleteBusy.value = false
  }
}

async function toggleActiveById(userId: string, _next: boolean) {
  try {
    await api.post(`/chat/admin/users/${userId}/toggle-active`)
    await Promise.all([loadThreads(), loadAccounts()])
  } catch (error: any) {
    message.error(error?.response?.data?.detail || 'Не удалось изменить статус.')
  }
}

let threadsTimer: number | undefined
let messagesTimer: number | undefined
let pollBusy = false

function appendMessages(incoming: MessageRow[]): boolean {
  if (!incoming.length) return false
  const seen = new Set(messages.value.map((m) => m.id))
  const fresh = incoming.filter((m) => !seen.has(m.id))
  if (!fresh.length) return false
  messages.value.push(...fresh)
  return true
}

onMounted(async () => {
  await Promise.all([loadStatus(), loadThreads(), loadFolders()])
  threadsTimer = window.setInterval(loadThreads, 10_000)
  messagesTimer = window.setInterval(pollMessages, 4_000)
})

onBeforeUnmount(() => {
  if (threadsTimer) clearInterval(threadsTimer)
  if (messagesTimer) clearInterval(messagesTimer)
})

async function loadStatus() {
  try {
    const { data } = await api.get<ChatStatus>('/chat/admin/status')
    status.value = data
    chatUnread.unread = data.unread_messages || 0
    chatUnread.threads = data.unread_threads || 0
  } catch {
    status.value = null
  }
}

async function loadThreads() {
  threadsLoading.value = true
  try {
    const { data } = await api.get<ThreadRow[]>('/chat/admin/threads')
    threads.value = data
    void chatUnread.refresh()
  } catch {
    /* поллинг — не шумим */
  } finally {
    threadsLoading.value = false
  }
}

async function loadFolders() {
  try {
    const { data } = await api.get<FolderRow[]>('/chat/admin/folders')
    folders.value = data
  } catch {
    folders.value = []
  }
}

function openCreateFolder() {
  folderName.value = ''
  showFolder.value = true
}

async function doCreateFolder() {
  const name = folderName.value.trim()
  if (!name) return
  folderBusy.value = true
  try {
    await api.post('/chat/admin/folders', { name })
    showFolder.value = false
    await loadFolders()
  } catch (error: any) {
    message.error(error?.response?.data?.detail || 'Не удалось создать папку.')
  } finally {
    folderBusy.value = false
  }
}

function confirmDeleteFolder(folder: FolderRow) {
  dialog.warning({
    title: 'Удалить папку',
    content: `Папка «${folder.name}» будет удалена. Диалоги останутся и переедут в «Без папки».`,
    positiveText: 'Удалить',
    negativeText: 'Отмена',
    onPositiveClick: async () => {
      try {
        await api.delete(`/chat/admin/folders/${folder.id}`)
        if (activeFolderId.value === folder.id) activeFolderId.value = 'all'
        await Promise.all([loadFolders(), loadThreads()])
      } catch (error: any) {
        message.error(error?.response?.data?.detail || 'Не удалось удалить папку.')
      }
    }
  })
}

async function moveThreadToFolder(threadId: string, folderId: string | null) {
  try {
    await api.post(`/chat/admin/threads/${threadId}/move`, { folder_id: folderId })
    await Promise.all([loadThreads(), loadFolders()])
  } catch (error: any) {
    message.error(error?.response?.data?.detail || 'Не удалось переместить диалог.')
  }
}

async function selectThread(id: string) {
  if (activeThreadId.value === id) return
  activeThreadId.value = id
  messages.value = []
  messagesLoading.value = true
  try {
    const { data } = await api.get<{ messages: MessageRow[] }>(
      `/chat/admin/threads/${id}/messages`,
      { params: { limit: 200 } }
    )
    messages.value = data.messages
    scrollToBottom()
    await loadThreads()
  } catch {
    message.error('Не удалось загрузить сообщения.')
  } finally {
    messagesLoading.value = false
  }
}

async function pollMessages() {
  if (!activeThreadId.value || sending.value || pollBusy) return
  pollBusy = true
  const threadAtStart = activeThreadId.value
  const lastId = messages.value.reduce((max, m) => (m.id > max ? m.id : max), 0)
  try {
    const { data } = await api.get<{ messages: MessageRow[] }>(
      `/chat/admin/threads/${threadAtStart}/messages`,
      { params: { after_id: lastId, limit: 100 } }
    )
    if (threadAtStart !== activeThreadId.value) return
    if (appendMessages(data.messages)) {
      scrollToBottom()
      void loadThreads()
    }
  } catch {
    /* поллинг — не шумим */
  } finally {
    pollBusy = false
  }
}

function onComposeKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    void sendMessage()
  }
}

async function sendMessage() {
  const body = draft.value.trim()
  if (!body || !activeThreadId.value) return
  sending.value = true
  try {
    const { data } = await api.post<MessageRow>(
      `/chat/admin/threads/${activeThreadId.value}/messages`,
      { body }
    )
    appendMessages([data])
    draft.value = ''
    scrollToBottom()
    void loadThreads()
  } catch (error: any) {
    message.error(error?.response?.data?.detail || 'Не удалось отправить.')
  } finally {
    sending.value = false
  }
}

async function toggleResolved() {
  if (!activeThread.value) return
  statusBusy.value = true
  const next = activeThread.value.status === 'resolved' ? 'open' : 'resolved'
  try {
    await api.post(`/chat/admin/threads/${activeThreadId.value}/status`, { status: next })
    await loadThreads()
  } catch (error: any) {
    message.error(error?.response?.data?.detail || 'Не удалось изменить статус.')
  } finally {
    statusBusy.value = false
  }
}

function scrollToBottom() {
  void nextTick(() => {
    const el = messagesBox.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString('ru-RU', {
      day: '2-digit',
      month: '2-digit',
      hour: '2-digit',
      minute: '2-digit'
    })
  } catch {
    return iso
  }
}

// --- аккаунты ---------------------------------------------------------------

async function openAccounts() {
  showAccounts.value = true
  await loadAccounts()
}

async function loadAccounts() {
  try {
    const { data } = await api.get<AccountRow[]>('/chat/admin/users')
    accounts.value = data
  } catch (error: any) {
    message.error(error?.response?.data?.detail || 'Не удалось загрузить аккаунты.')
  }
}

function exportAccounts() {
  if (!accounts.value.length) return
  const url = status.value?.public_url || ''
  const rows = [['Имя', 'Логин', 'VPN-клиент', 'Статус', 'Адрес']]
  for (const u of accounts.value) {
    rows.push([
      u.display_name || u.username,
      u.username,
      u.client_name || '',
      u.is_active ? 'активен' : 'заблокирован',
      url
    ])
  }
  const csv = rows
    .map((r) => r.map((c) => `"${String(c).replace(/"/g, '""')}"`).join(','))
    .join('\r\n')
  const blob = new Blob(['\uFEFF' + csv], { type: 'text/csv;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `chat-accounts-${new Date().toISOString().slice(0, 10)}.csv`
  a.click()
  URL.revokeObjectURL(a.href)
  message.success('Список выгружен (без паролей — они не хранятся).')
}

async function createAccount() {
  const username = newUsername.value.trim().toLowerCase()
  if (!username) {
    message.warning('Укажите логин.')
    return
  }
  creating.value = true
  try {
    const { data } = await api.post<{ user: AccountRow; password: string }>('/chat/admin/users', {
      username,
      display_name: newDisplayName.value.trim() || null
    })
    issuedLogin.value = data.user.username
    issuedPassword.value = data.password
    showPassword.value = true
    newUsername.value = ''
    newDisplayName.value = ''
    showCreateForm.value = false
    await loadAccounts()
  } catch (error: any) {
    message.error(error?.response?.data?.detail || 'Не удалось создать аккаунт.')
  } finally {
    creating.value = false
  }
}

async function toggleActive(u: AccountRow) {
  try {
    await api.post(`/chat/admin/users/${u.id}/toggle-active`)
    await loadAccounts()
    await loadThreads()
  } catch (error: any) {
    message.error(error?.response?.data?.detail || 'Не удалось изменить статус.')
  }
}

// --- привязка VPN-клиента -----------------------------------------------------

async function loadClientOptions() {
  clientsLoading.value = true
  try {
    const { data } = await api.get<ClientOption[]>('/clients')
    clientOptions.value = data.map((c) => ({
      label: c.server_name ? `${c.name} · ${c.server_name}` : c.name,
      value: c.id
    }))
  } catch {
    message.error('Не удалось загрузить список VPN-клиентов.')
  } finally {
    clientsLoading.value = false
  }
}

function openLink(thread: ThreadRow) {
  if (!thread.chat_user_id) return
  linkTarget.value = { id: thread.chat_user_id, username: thread.username }
  linkClientId.value = thread.client_id
  linkFromThreadId.value = thread.id
  linkSendAfter.value = true
  showLink.value = true
  void loadClientOptions()
}

function openLinkAccount(account: AccountRow) {
  linkTarget.value = { id: account.id, username: account.username }
  linkClientId.value = account.client_id
  linkFromThreadId.value = null
  linkSendAfter.value = false
  showLink.value = true
  void loadClientOptions()
}

async function saveLink() {
  if (!linkTarget.value) return
  linkBusy.value = true
  try {
    // Из диалога с выбранным клиентом и галкой «отправить» — одним шагом
    // привязываем и сразу выдаём ключ в чат (link-and-send-key).
    if (linkFromThreadId.value && linkClientId.value && linkSendAfter.value) {
      const { data } = await api.post<MessageRow>(
        `/chat/admin/threads/${linkFromThreadId.value}/link-and-send-key`,
        { client_id: linkClientId.value, replace: true }
      )
      appendMessages([data])
      scrollToBottom()
      message.success('VPN-клиент привязан, ключ отправлен в чат.')
    } else {
      await api.post(`/chat/admin/users/${linkTarget.value.id}/link`, {
        client_id: linkClientId.value || null
      })
      message.success(linkClientId.value ? 'VPN-клиент привязан.' : 'Привязка снята.')
    }
    showLink.value = false
    await Promise.all([loadThreads(), showAccounts.value ? loadAccounts() : Promise.resolve()])
  } catch (error: any) {
    message.error(error?.response?.data?.detail || 'Не удалось сохранить привязку.')
  } finally {
    linkBusy.value = false
  }
}

// --- создание клиента из чата --------------------------------------------------

async function loadProvisionServers() {
  provisionLoading.value = true
  try {
    const endpoint = auth.user?.role === 'moderator' ? '/servers/minimal' : '/servers'
    const { data } = await api.get<ServerRow[]>(endpoint)
    provisionServers.value = data
  } catch (error: any) {
    message.error(error?.response?.data?.detail || 'Не удалось загрузить серверы.')
  } finally {
    provisionLoading.value = false
  }
}

function syncProvisionProtocol() {
  const opts = provisionProtocolOptions.value
  if (!opts.length) {
    provForm.protocol = ''
    return
  }
  if (!opts.some((o) => o.value === provForm.protocol)) provForm.protocol = opts[0].value
}

async function openProvision() {
  if (!activeThread.value) return
  provForm.name = activeThread.value.display_name || activeThread.value.username || ''
  provForm.protocol = 'awg2'
  provForm.format = 'both'
  provForm.fingerprint = 'chrome'
  provForm.expires_at = ''
  provForm.traffic_gb = ''
  showProvision.value = true
  await loadProvisionServers()
  const remembered = localStorage.getItem(LAST_PROVISION_SERVER)
  const options = provisionServerOptions.value
  if (remembered && options.some((o) => o.value === remembered)) provForm.server_id = remembered
  else if (options.length) provForm.server_id = options[0].value
  syncProvisionProtocol()
}

async function submitProvision() {
  if (!activeThread.value || !provForm.server_id || !provForm.protocol) {
    message.error('Выберите сервер и протокол.')
    return
  }
  provisionBusy.value = true
  try {
    let traffic: number | null = null
    const gb = parseFloat(String(provForm.traffic_gb).replace(',', '.'))
    if (provForm.traffic_gb && !Number.isNaN(gb) && gb > 0) traffic = Math.round(gb * 1024 ** 3)
    const payload: Record<string, unknown> = {
      server_id: provForm.server_id,
      protocol: provForm.protocol,
      name: provForm.name.trim() || null,
      format: provForm.format,
      traffic_limit_bytes: traffic,
      expires_at: provForm.expires_at || null,
      fingerprint: provIsXray.value ? provForm.fingerprint : null,
      replace: true
    }
    const { data } = await api.post<MessageRow>(
      `/chat/admin/threads/${activeThreadId.value}/provision-client`,
      payload
    )
    appendMessages([data])
    scrollToBottom()
    localStorage.setItem(LAST_PROVISION_SERVER, provForm.server_id)
    showProvision.value = false
    message.success('Клиент создан и ключ отправлен в чат.')
    void loadThreads()
  } catch (error: any) {
    message.error(error?.response?.data?.detail || 'Не удалось создать клиента.')
  } finally {
    provisionBusy.value = false
  }
}

// --- выдача ключа ----------------------------------------------------------------

function confirmSendKey() {
  if (!activeThread.value) return
  dialog.info({
    title: 'Выдать ключ подключения?',
    content:
      `Клиент «${activeThread.value.client_name}» получит в чате защищённую ссылку на файл ` +
      'конфигурации и QR-код. Ссылка работает только после входа в чат и истекает через 72 часа.',
    positiveText: 'Выдать',
    negativeText: 'Отмена',
    onPositiveClick: async () => {
      keyBusy.value = true
      try {
        const { data } = await api.post<MessageRow>(`/chat/admin/threads/${activeThreadId.value}/send-key`)
        appendMessages([data])
        scrollToBottom()
        message.success('Ключ отправлен в чат.')
        void loadThreads()
      } catch (error: any) {
        message.error(error?.response?.data?.detail || 'Не удалось выдать ключ.')
      } finally {
        keyBusy.value = false
      }
    }
  })
}

// --- счета ---------------------------------------------------------------------

function invoiceStatusLabel(status: string): string {
  switch (status) {
    case 'pending': return 'ожидает оплаты'
    case 'succeeded': return 'оплачен'
    case 'canceled': return 'отменён'
    case 'expired': return 'просрочен'
    default: return status
  }
}

async function openInvoices() {
  showInvoices.value = true
  invoicesLoading.value = true
  try {
    const { data } = await api.get<InvoiceRow[]>(`/chat/admin/threads/${activeThreadId.value}/invoices`)
    invoices.value = data
  } catch (error: any) {
    message.error(error?.response?.data?.detail || 'Не удалось загрузить счета.')
  } finally {
    invoicesLoading.value = false
  }
}

async function insertInvoice(inv: InvoiceRow) {
  insertBusy.value = inv.id
  try {
    const { data } = await api.post<MessageRow>(
      `/chat/admin/threads/${activeThreadId.value}/insert-invoice`,
      { invoice_id: inv.id }
    )
    appendMessages([data])
    scrollToBottom()
    showInvoices.value = false
    message.success('Счёт отправлен в чат.')
    void loadThreads()
  } catch (error: any) {
    message.error(error?.response?.data?.detail || 'Не удалось вставить счёт.')
  } finally {
    insertBusy.value = ''
  }
}

async function copyCredentials() {
  const text = `Чат поддержки: ${status.value?.public_url || ''}\nЛогин: ${issuedLogin.value}\nПароль: ${issuedPassword.value}`
  try {
    await navigator.clipboard.writeText(text)
    message.success('Скопировано — отправьте клиенту.')
  } catch {
    message.error('Не удалось скопировать.')
  }
}
</script>

<style scoped>
.prov-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin-top: 8px;
}

.prov-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 13px;
}

.prov-field > span {
  color: var(--text-muted, #8a8f98);
}

.prov-date {
  padding: 6px 10px;
  border-radius: 6px;
  border: 1px solid var(--border, #2a2f3a);
  background: var(--input-bg, #1b1f27);
  color: inherit;
  font: inherit;
}

.cascade-hint {
  margin-top: 10px;
}

@media (max-width: 560px) {
  .prov-grid {
    grid-template-columns: 1fr;
  }
}

.chat-layout {
  display: grid;
  grid-template-columns: 300px 1fr;
  gap: 16px;
  align-items: stretch;
  /* фиксированная высота: переписка скроллится внутри, а не растит страницу */
  height: calc(100vh - 210px);
  min-height: 420px;
}

.chat-side {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 14px;
  overflow-y: auto;
  min-height: 0;
}

.chat-side-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}

.chat-side-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}

.chat-folder-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-bottom: 8px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--color-border);
}

.chat-folder-row {
  display: flex;
  align-items: center;
  gap: 9px;
  width: 100%;
  padding: 8px 10px;
  border: 0;
  border-radius: 9px;
  background: none;
  color: var(--color-muted);
  font: inherit;
  font-size: 13.5px;
  text-align: left;
  cursor: pointer;
  transition: background 0.14s ease, color 0.14s ease;
}

.chat-folder-row:hover { background: var(--color-surface-2, rgba(127, 127, 127, 0.08)); color: var(--color-text); }
.chat-folder-row.active { background: color-mix(in srgb, var(--color-accent) 16%, transparent); color: var(--color-text); }
.chat-folder-ico { flex: none; opacity: 0.85; }
.chat-folder-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.chat-folder-count { flex: none; opacity: 0.6; font-size: 12px; }
.chat-folder-x {
  flex: none;
  font-size: 15px;
  line-height: 1;
  opacity: 0;
  padding: 0 2px;
}
.chat-folder-row:hover .chat-folder-x { opacity: 0.5; }
.chat-folder-x:hover { opacity: 1 !important; color: var(--color-danger, #e88080); }

.chat-thread {
  display: block;
  width: 100%;
  text-align: left;
  background: none;
  border: 1px solid transparent;
  border-radius: 10px;
  padding: 10px 12px;
  cursor: pointer;
  color: inherit;
  font: inherit;
}

.chat-thread:hover {
  background: rgba(255, 255, 255, 0.04);
}

.chat-thread.active {
  border-color: var(--color-border);
  background: rgba(255, 255, 255, 0.05);
}

.chat-thread-top {
  display: flex;
  align-items: center;
  gap: 8px;
}

.chat-thread-name {
  font-weight: 600;
  font-size: 13.5px;
}

.chat-thread.unread {
  background: color-mix(in srgb, var(--color-accent) 10%, transparent);
}

.chat-thread.unread .chat-thread-name {
  font-weight: 700;
}

.chat-thread-unread {
  flex-shrink: 0;
  min-width: 18px;
  height: 18px;
  padding: 0 6px;
  border-radius: 999px;
  background: #e88080;
  color: #fff;
  font-size: 11px;
  font-weight: 700;
  line-height: 18px;
  text-align: center;
}

.chat-thread-resolved {
  font-size: 11px;
  color: var(--color-muted);
  border: 1px solid var(--color-border);
  border-radius: 999px;
  padding: 1px 8px;
}

.chat-thread-preview {
  margin-top: 3px;
  font-size: 12.5px;
  color: var(--color-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chat-main {
  display: flex;
  flex-direction: column;
  padding: 0;
  overflow: hidden;
  min-height: 0;
}

.chat-main-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--color-border);
}

.chat-head-info {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.chat-head-link {
  display: flex;
  align-items: center;
  gap: 6px;
}

.chat-head-actions {
  display: flex;
  gap: 6px;
  flex: none;
}

.chat-link-chip {
  font-size: 11.5px;
  color: var(--color-accent);
  border: 1px solid var(--color-border);
  border-radius: 999px;
  padding: 1px 8px;
  white-space: nowrap;
}

.chat-link-chip.warn {
  color: var(--color-warning);
}

.chat-link-chip.muted-chip {
  color: var(--color-muted);
}

.chat-inv-info {
  flex-direction: row;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.chat-messages {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  scrollbar-width: none; /* Firefox */
}

.chat-messages::-webkit-scrollbar,
.chat-side::-webkit-scrollbar,
.chat-acc-list::-webkit-scrollbar {
  display: none; /* Chrome/Safari: скролл работает, полоса скрыта */
}

.chat-side,
.chat-acc-list {
  scrollbar-width: none;
}

.chat-msg {
  max-width: 72%;
  border-radius: 12px;
  padding: 8px 12px;
  font-size: 13.5px;
  line-height: 1.45;
}

.chat-msg.from-admin {
  align-self: flex-end;
  background: rgba(99, 226, 183, 0.12);
  border: 1px solid rgba(99, 226, 183, 0.25);
}

.chat-msg.from-client {
  align-self: flex-start;
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid var(--color-border);
}

.chat-msg-body {
  white-space: pre-wrap;
  word-break: break-word;
}

.chat-msg-att {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 6px;
  padding: 6px 10px;
  border: 1px dashed var(--color-border);
  border-radius: 8px;
  font-size: 12.5px;
  color: var(--color-accent);
}

.chat-msg-att.expired {
  color: var(--color-muted);
}

.chat-msg-time {
  margin-top: 4px;
  font-size: 11px;
  color: var(--color-muted);
  text-align: right;
}

.chat-compose {
  display: flex;
  gap: 8px;
  align-items: flex-end;
  padding: 12px 16px;
  border-top: 1px solid var(--color-border);
}

.chat-compose :deep(.n-input) {
  flex: 1;
}

.chat-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 28px 12px;
  color: var(--color-muted);
  font-size: 13px;
  text-align: center;
}

.chat-empty.tall {
  flex: 1;
}

.chat-empty-hint {
  max-width: 240px;
  font-size: 12.5px;
}

.muted {
  color: var(--color-muted);
}

/* окно «Аккаунты чата» — фиксированная карточка, прокручивается только список,
   само окно без полос прокрутки; меню действий (n-dropdown) всплывает поверх. */
.chat-accounts {
  width: min(600px, 94vw);
  max-height: min(86dvh, 640px);
  overflow: hidden;
  gap: 12px;
}

.acc-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.acc-head h3 {
  margin: 0;
}

.acc-head-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}

.acc-create {
  display: grid;
  grid-template-columns: 1fr 1fr auto;
  gap: 8px;
  overflow: hidden;
}

.acc-fold-enter-active,
.acc-fold-leave-active {
  transition: opacity 0.18s ease, transform 0.18s ease;
}

.acc-fold-enter-from,
.acc-fold-leave-to {
  opacity: 0;
  transform: translateY(-6px);
}

.acc-table {
  display: flex;
  flex-direction: column;
  min-height: 0;
  flex: 1;
  border: 1px solid var(--color-border);
  border-radius: 12px;
  overflow: hidden;
}

.acc-tr {
  display: grid;
  grid-template-columns: minmax(0, 1.5fr) minmax(0, 1fr) minmax(0, 1fr) 52px;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
}

.acc-thead {
  font-size: 11.5px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--color-muted);
  background: var(--color-surface-2);
  border-bottom: 1px solid var(--color-border);
}

.acc-th-act {
  text-align: right;
}

.acc-scroll {
  overflow-y: auto;
  min-height: 0;
  scrollbar-width: thin;
}

.acc-row {
  border-top: 1px solid var(--color-border);
  transition: background 0.12s ease;
}

.acc-scroll .acc-row:first-child {
  border-top: 0;
}

.acc-row:hover {
  background: var(--color-accent-soft);
}

.acc-row.blocked {
  opacity: 0.6;
}

.acc-client {
  display: flex;
  align-items: center;
  gap: 9px;
  min-width: 0;
}

.acc-client .entity-avatar {
  flex-shrink: 0;
}

.acc-name {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.acc-name strong {
  font-size: 13.5px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.acc-login {
  font-size: 12.5px;
  color: var(--color-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.acc-vpn {
  min-width: 0;
  font-size: 12.5px;
}

.acc-vpn .chat-link-chip {
  display: inline-block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: bottom;
}

.acc-act {
  display: flex;
  justify-content: flex-end;
}

.chat-acc-badge {
  flex-shrink: 0;
  font-size: 10px;
  font-weight: 700;
  padding: 1px 6px;
  border-radius: 999px;
  color: #e0a04a;
  background: rgba(224, 160, 74, 0.14);
  border: 1px solid rgba(224, 160, 74, 0.3);
}

.acc-empty {
  text-align: center;
  padding: 26px 12px;
}

.acc-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  font-size: 12px;
}

.chat-cred {
  display: flex;
  flex-direction: column;
  gap: 6px;
  border: 1px solid var(--color-border);
  border-radius: 10px;
  padding: 12px;
}

.chat-cred-row {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  font-size: 13px;
}

.chat-cred-row span:first-child {
  color: var(--color-muted);
}

@media (max-width: 900px) {
  .chat-layout {
    grid-template-columns: 1fr;
    grid-template-rows: 220px 1fr;
    height: calc(100vh - 180px);
  }
}

@media (max-width: 560px) {
  .acc-create {
    grid-template-columns: 1fr;
  }
  .acc-thead {
    display: none;
  }
  .acc-tr {
    grid-template-columns: 1fr auto;
    grid-template-areas:
      "client act"
      "login  act"
      "vpn    act";
    row-gap: 3px;
  }
  .acc-client {
    grid-area: client;
  }
  .acc-login {
    grid-area: login;
    padding-left: 39px;
  }
  .acc-vpn {
    grid-area: vpn;
    padding-left: 39px;
  }
  .acc-act {
    grid-area: act;
    align-self: center;
  }
}
</style>
