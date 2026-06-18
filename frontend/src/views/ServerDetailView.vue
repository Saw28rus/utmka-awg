<template>
  <AppShell :title="server?.name || 'Сервер'" eyebrow="Управление сервером">
    <div v-if="loading" class="panel placeholder">
      <n-spin size="small" />
      <span>Подключаюсь к серверу…</span>
    </div>

    <template v-else-if="server">
      <div class="detail-head">
        <n-button tertiary @click="router.back()">
          <template #icon><ArrowLeft :size="16" /></template>
          Назад
        </n-button>
        <div class="head-actions">
          <n-button circle tertiary :loading="refreshing" title="Обновить данные" @click="refreshAll">
            <template #icon><RefreshCw :size="15" /></template>
          </n-button>
          <button class="delete-btn" @click="confirmDeleteServer">
            <Trash2 :size="15" />
            Удалить
          </button>
        </div>
      </div>

      <div class="panel server-hero">
        <div class="hero-id">
          <span class="entity-avatar entity-avatar--xl">{{ server.name.charAt(0).toUpperCase() }}</span>
          <div class="hero-text">
            <h2>{{ server.name }}</h2>
            <span class="mono">{{ server.host }}:{{ server.ssh_port }}</span>
          </div>
          <StatusBadge
            :label="metrics?.online ? 'online' : metrics ? 'offline' : 'проверка'"
            :tone="metrics?.online ? 'ok' : metrics ? 'danger' : 'neutral'"
            :pulse="metrics?.online"
          />
        </div>
        <div class="hero-stats">
          <span class="hero-stat">
            <Users :size="13" />
            {{ metrics?.active_peers ?? server.active_peers }} клиентов
          </span>
          <span class="hero-stat">
            <ArrowDownUp :size="13" />
            {{ formatBytes(metrics?.total_traffic_bytes) }}
          </span>
          <span class="hero-stat">
            <Clock :size="13" />
            {{ formatUptime(metrics?.uptime_seconds) }}
          </span>
          <span v-if="overview?.system?.os" class="hero-stat">
            <Server :size="13" />
            {{ overview.system.os }}
          </span>
          <button
            v-if="cascadePeerInfo"
            type="button"
            class="hero-cascade"
            :class="{ 'hero-cascade--active': cascadePeerInfo.is_active }"
            @click="activeTab = 'cascade'"
          >
            <Network :size="13" />
            <template v-if="cascadePeerInfo.role === 'entry'">
              Каскад → {{ cascadePeerInfo.name }}
            </template>
            <template v-else>
              Выход ← {{ cascadePeerInfo.name }}
            </template>
            <StatusBadge
              :label="labelCascadeState(cascadePeerInfo.state)"
              :tone="toneCascadeState(cascadePeerInfo.state)"
              :pulse="cascadePeerInfo.is_active"
            />
          </button>
        </div>
      </div>

      <div class="tabs">
        <button
          v-for="tab in tabs"
          :key="tab.id"
          class="tab"
          :class="{ active: activeTab === tab.id }"
          @click="activeTab = tab.id"
        >
          <component :is="tab.icon" :size="15" />
          {{ tab.label }}
          <span v-if="tab.id === 'security' && securityWarnings > 0" class="tab-pill warn">{{ securityWarnings }}</span>
        </button>
      </div>

      <!-- Обзор -->
      <div v-show="activeTab === 'overview'" class="tab-body">
        <div class="overview-grid">
          <div class="panel block">
            <h3>Нагрузка</h3>
            <div class="load-metrics">
              <template v-if="metrics?.online">
                <MetricBar label="CPU" :icon="Cpu" :percent="metrics.cpu_percent" :value-text="cpuText" />
                <MetricBar label="ОЗУ" :icon="MemoryStick" :percent="memPercent" :value-text="memText" />
                <MetricBar label="Диск" :icon="HardDrive" :percent="diskPercent" :value-text="diskText" />
              </template>
              <div v-else class="muted-state">
                <WifiOff :size="15" />
                <span>{{ metrics?.message || 'Нет данных по SSH' }}</span>
              </div>
            </div>
          </div>

          <DpiTrendCard :server-id="serverId" />

          <div class="panel block">
            <h3>Система</h3>
            <dl class="kv">
              <div>
                <dt>ОС</dt>
                <dd>{{ overview?.system?.os || '—' }}</dd>
              </div>
              <div>
                <dt>Ядро</dt>
                <dd class="mono">{{ overview?.system?.kernel || '—' }}</dd>
              </div>
              <div>
                <dt>Архитектура</dt>
                <dd class="mono">{{ overview?.system?.arch || '—' }}</dd>
              </div>
              <div>
                <dt>Процессор</dt>
                <dd>{{ cpuModelText }}</dd>
              </div>
              <div>
                <dt>Docker</dt>
                <dd class="mono">{{ overview?.system?.docker_version || '—' }}</dd>
              </div>
              <div>
                <dt>IP сервера</dt>
                <dd class="mono">{{ overview?.system?.public_ip || server.host }}</dd>
              </div>
              <div>
                <dt>Порт VPN</dt>
                <dd class="mono">{{ server.vpn_port || '—' }}</dd>
              </div>
              <div>
                <dt>Добавлен</dt>
                <dd>{{ createdText }}</dd>
              </div>
            </dl>
          </div>
        </div>
      </div>

      <!-- Протоколы -->
      <div v-show="activeTab === 'protocols'" class="tab-body">
        <div v-if="overviewLoading" class="panel placeholder">
          <n-spin size="small" />
          <span>Сканирую протоколы…</span>
        </div>
        <div v-else class="protocol-grid">
          <div v-for="proto in overview?.protocols || []" :key="proto.id" class="panel proto-card">
            <header class="proto-head">
              <div class="proto-title">
                <span class="proto-icon"><ShieldCheck :size="16" /></span>
                <strong>{{ proto.name }}</strong>
              </div>
              <StatusBadge
                :label="protoStatusLabel(proto)"
                :tone="protoStatusTone(proto)"
                :pulse="proto.running"
              />
            </header>
            <p class="proto-desc">{{ proto.description }}</p>
            <div class="proto-meta">
              <span v-if="proto.container" class="meta-chip mono">{{ proto.container }}</span>
              <span v-if="proto.ports" class="meta-chip mono">{{ shortPorts(proto.ports) }}</span>
              <span v-if="proto.managed && proto.installed" class="meta-chip accent">
                {{ proto.clients_count }} клиентов в панели
              </span>
              <span
                v-if="proto.installed && protocolVersions[proto.id]?.installed_version"
                class="meta-chip mono"
                :class="{
                  ok: protocolVersions[proto.id]?.up_to_date === true,
                  warn: protocolVersions[proto.id]?.up_to_date === false,
                }"
                :title="
                  protocolVersions[proto.id]?.up_to_date === false
                    ? `Доступна версия ${protocolVersions[proto.id]?.pinned_version}`
                    : 'Версия зафиксирована панелью'
                "
              >
                {{ protocolVersions[proto.id]?.installed_version }}
                <template v-if="protocolVersions[proto.id]?.up_to_date === false">
                  → {{ protocolVersions[proto.id]?.pinned_version }}
                </template>
              </span>
              <span
                v-else-if="proto.installed && protocolVersions[proto.id]?.needs_adopt"
                class="meta-chip warn"
                title="Версия не зафиксирована в манифесте панели (например, после переустановки панели)"
              >
                версия не зафиксирована
              </span>
            </div>
            <footer class="proto-actions">
              <template v-if="proto.installed">
                <n-button
                  v-if="!proto.running"
                  size="tiny"
                  tertiary
                  :loading="isProtoBusy(proto.id, 'start')"
                  @click="protoAction(proto, 'start')"
                >
                  <template #icon><Play :size="13" /></template>
                  Запустить
                </n-button>
                <n-button
                  v-else
                  size="tiny"
                  tertiary
                  :loading="isProtoBusy(proto.id, 'restart')"
                  @click="protoAction(proto, 'restart')"
                >
                  <template #icon><RotateCcw :size="13" /></template>
                  Перезапуск
                </n-button>
                <n-button
                  v-if="proto.running"
                  size="tiny"
                  tertiary
                  :loading="isProtoBusy(proto.id, 'stop')"
                  @click="confirmProtoStop(proto)"
                >
                  <template #icon><Square :size="13" /></template>
                  Стоп
                </n-button>
                <n-button
                  v-if="canUpdate(proto)"
                  size="tiny"
                  type="primary"
                  :loading="isProtoBusy(proto.id, 'update')"
                  title="Обновить протокол до pinned-версии (снимок → пересборка → пересоздание → health → авто-откат при сбое)"
                  @click="confirmUpdateProtocol(proto)"
                >
                  <template #icon><RefreshCw :size="13" /></template>
                  Обновить
                </n-button>
                <n-button
                  v-if="proto.installed && proto.id === 'xray'"
                  size="tiny"
                  tertiary
                  :loading="isProtoBusy(proto.id, 'masking')"
                  title="Показать параметры маскировки Reality (dest/SNI/shortId/порт)"
                  @click="showXrayMasking(proto)"
                >
                  <template #icon><Fingerprint :size="13" /></template>
                  Reality
                </n-button>
                <n-button
                  v-if="canSnapshot(proto)"
                  size="tiny"
                  tertiary
                  :loading="isProtoBusy(proto.id, 'snapshot')"
                  title="Снять зашифрованный бэкап конфига и ключей (нужен перед обновлением)"
                  @click="snapshotProtocol(proto)"
                >
                  <template #icon><Save :size="13" /></template>
                  Снимок
                </n-button>
                <n-button
                  size="tiny"
                  tertiary
                  type="error"
                  :loading="isProtoBusy(proto.id, 'remove')"
                  @click="confirmProtoRemove(proto)"
                >
                  <template #icon><Trash2 :size="13" /></template>
                  Удалить
                </n-button>
              </template>
              <template v-else>
                <n-button
                  v-if="proto.can_install"
                  size="tiny"
                  tertiary
                  type="primary"
                  @click="openInstall(proto)"
                >
                  <template #icon><Download :size="13" /></template>
                  Установить
                </n-button>
                <n-button v-else size="tiny" tertiary disabled title="Установка этого протокола пока не поддерживается">
                  <template #icon><Download :size="13" /></template>
                  Скоро
                </n-button>
              </template>
            </footer>
          </div>
        </div>
      </div>

      <!-- Безопасность -->
      <div v-show="activeTab === 'security'" class="tab-body">
        <div class="panel block">
          <div class="security-head">
            <h3>Защита панели</h3>
            <span class="security-score" :class="secDoneCount === 3 ? 'ok' : 'warn'">
              {{ secDoneCount }} из 3 шагов
            </span>
          </div>
          <p class="sec-sub">
            Настраивается один раз, по порядку. Следующий шаг открывается после предыдущего.
          </p>

          <div v-if="sslLoading || hardenLoading || chatLoading" class="ssl-loading">
            <n-spin size="small" />
            <span>Проверяю состояние…</span>
          </div>

          <div v-else class="sec-steps">
            <!-- Шаг 1: HTTPS -->
            <div class="sec-step" :class="{ open: openStep === 'ssl' }">
              <button type="button" class="sec-step-head" @click="toggleStep('ssl')">
                <span class="sec-mark" :class="{ done: sslDone }">
                  <Check v-if="sslDone" :size="13" />
                  <template v-else>1</template>
                </span>
                <span class="sec-step-name">Вход в панель по HTTPS</span>
                <span class="sec-step-note" :class="{ ok: sslDone }">
                  {{ sslDone ? sslStatus?.domain : 'домен не привязан' }}
                </span>
                <ChevronDown :size="15" class="sec-chev" />
              </button>
              <div v-show="openStep === 'ssl'" class="sec-step-body">
                <template v-if="sslDone">
                  <div class="ssl-active">
                    <Globe :size="15" />
                    <a :href="sslStatus?.url || '#'" target="_blank" rel="noopener" class="ssl-link">{{ sslStatus?.url }}</a>
                    <span v-if="sslStatus?.cert_expires_at" class="ssl-expiry mono">сертификат до {{ sslStatus.cert_expires_at }}</span>
                  </div>
                  <p class="sec-note">Сертификат продлевается автоматически. Запасной вход: <span class="mono">{{ sslStatus?.fallback_url || fallbackGuess }}</span> (шаг 2 ограничивает его).</p>
                  <div class="ssl-actions">
                    <n-button size="small" tertiary type="warning" :loading="sslBusy === 'rollback'" :disabled="!!sslBusy" @click="confirmSslRollback">
                      Откатить HTTPS
                    </n-button>
                  </div>
                </template>
                <template v-else>
                  <ol class="sec-howto">
                    <li>
                      У регистратора домена добавьте A-запись на IP сервера
                      <span class="mono">{{ server?.host }}</span>
                      <n-button size="tiny" quaternary @click="copyText(server?.host || '')">
                        <template #icon><Copy :size="12" /></template>
                        копировать IP
                      </n-button>
                    </li>
                    <li>Введите этот домен ниже и нажмите «Подключить» — остальное панель сделает сама.</li>
                  </ol>
                  <div class="sec-row">
                    <n-input v-model:value="sslDomain" placeholder="panel.example.com" :disabled="!!sslBusy" />
                    <n-button type="primary" :loading="sslBusy === 'verify' || sslBusy === 'install'" @click="connectSsl">Подключить</n-button>
                  </div>
                  <div class="ssl-nodomain">
                    <span class="ssl-nodomain-or">или без своего домена</span>
                    <n-button size="small" tertiary :loading="sslBusy === 'auto'" :disabled="!!sslBusy" @click="connectSslAuto">
                      <template #icon><ShieldCheck :size="14" /></template>
                      HTTPS без домена (sslip.io)
                    </n-button>
                    <p class="sec-note">
                      Выпустит доверенный сертификат на адрес вида
                      <span class="mono">{{ server?.host }}.sslip.io</span> — это бесплатный публичный DNS,
                      который сам резолвится в IP сервера. Нужны открытые порты 80/443.
                    </p>
                  </div>
                  <p v-if="sslBusy === 'install' || sslBusy === 'auto'" class="sec-note">Выпускаю сертификат — обычно 1–2 минуты. Не закрывайте страницу.</p>
                  <p v-if="sslVerifyMessage && !sslVerifyOk" class="ssl-verify warn">{{ sslVerifyMessage }}</p>
                  <p v-if="sslStatus && !sslStatus.panel_detected" class="ssl-msg warn">
                    Панель на VPS не найдена. Сначала установите её: <span class="mono">scripts/install-panel.sh</span>
                  </p>
                </template>
              </div>
            </div>

            <!-- Шаг 2: ограничение :8080 -->
            <div class="sec-step" :class="{ open: openStep === 'harden', locked: !sslDone }">
              <button type="button" class="sec-step-head" @click="toggleStep('harden')">
                <span class="sec-mark" :class="{ done: hardenDone }">
                  <Check v-if="hardenDone" :size="13" />
                  <Lock v-else-if="!sslDone" :size="12" />
                  <template v-else>2</template>
                </span>
                <span class="sec-step-name">Закрыть запасной вход :8080</span>
                <span class="sec-step-note" :class="{ ok: hardenDone, warn: sslDone && !hardenDone }">
                  {{ !sslDone ? 'после шага 1' : hardenStepNote }}
                </span>
                <ChevronDown :size="15" class="sec-chev" />
              </button>
              <div v-show="openStep === 'harden'" class="sec-step-body">
                <template v-if="hardenStatus">
                  <p class="sec-note">
                    Панель останется доступна всем по HTTPS. Запасной адрес
                    <span class="mono">:8080</span> будет открываться только с IP из списка —
                    на случай проблем с доменом. VPN и SSH не затрагиваются.
                  </p>
                  <div class="sec-row">
                    <n-input
                      v-model:value="hardenIpsText"
                      :placeholder="hardenStatus.your_ip || '203.0.113.10'"
                      :disabled="!!hardenBusy"
                    />
                    <n-button type="primary" :loading="hardenBusy === 'apply'" :disabled="!!hardenBusy" @click="confirmHardenApply">
                      {{ hardenStatus.enabled ? 'Обновить' : 'Закрыть' }}
                    </n-button>
                  </div>
                  <p v-if="hardenStatus.your_ip" class="sec-note" :class="{ 'sec-warn': hardenYourIpMissing }">
                    Ваш текущий IP: <span class="mono">{{ hardenStatus.your_ip }}</span>
                    <template v-if="hardenYourIpMissing"> — его нет в списке: запасной вход будет вам недоступен (HTTPS продолжит работать).</template>
                    <template v-else> — в списке, всё в порядке.</template>
                  </p>
                  <p v-if="hardenStatus.enabled && !hardenStatus.persistent" class="ssl-msg warn">
                    Правила активны, но автозапуск после перезагрузки не подтверждён — нажмите «Обновить».
                  </p>
                  <div v-if="hardenStatus.enabled" class="ssl-actions">
                    <n-button size="small" tertiary type="warning" :loading="hardenBusy === 'disable'" :disabled="!!hardenBusy" @click="confirmHardenDisable">
                      Снова открыть всем
                    </n-button>
                  </div>
                  <p v-if="hardenStatus.message" class="ssl-msg warn">{{ hardenStatus.message }}</p>
                </template>
                <p v-else class="ssl-msg warn">Не удалось получить состояние файрвола — обновите страницу.</p>
              </div>
            </div>

            <!-- Шаг 3: домен чата -->
            <div class="sec-step" :class="{ open: openStep === 'chat', locked: !hardenDone }">
              <button type="button" class="sec-step-head" @click="toggleStep('chat')">
                <span class="sec-mark" :class="{ done: chatDone }">
                  <Check v-if="chatDone" :size="13" />
                  <Lock v-else-if="!hardenDone" :size="12" />
                  <template v-else>3</template>
                </span>
                <span class="sec-step-name">Чат с клиентами (поддомен)</span>
                <span class="sec-step-note" :class="{ ok: chatDone }">
                  {{ !hardenDone ? 'после шага 2' : chatDone ? chatStatus?.domain : 'не подключён' }}
                </span>
                <ChevronDown :size="15" class="sec-chev" />
              </button>
              <div v-show="openStep === 'chat'" class="sec-step-body">
                <template v-if="chatStatus">
                  <template v-if="chatDone">
                    <div class="ssl-active">
                      <Globe :size="15" />
                      <a :href="chatStatus.public_url || '#'" target="_blank" rel="noopener" class="ssl-link">{{ chatStatus.public_url }}</a>
                      <span v-if="chatStatus.cert_expires_at" class="ssl-expiry mono">сертификат до {{ chatStatus.cert_expires_at }}</span>
                    </div>
                    <p class="sec-note" :class="chatIsolationAllOk ? '' : 'sec-warn'">
                      <template v-if="!chatIsolation.length">Через этот адрес доступен только чат — админка и API панели закрыты.</template>
                      <template v-else-if="chatIsolationAllOk">Изоляция проверена: через чат-домен доступен только чат, админка и API закрыты.</template>
                      <template v-else>Часть проверок изоляции не прошла — см. ниже.</template>
                    </p>
                    <div v-if="chatIsolation.length && !chatIsolationAllOk" class="security-list">
                      <div v-for="(check, i) in chatIsolation" :key="i" class="security-row">
                        <span class="check-icon" :class="check.ok ? 'check-ok' : 'check-danger'">
                          <ShieldCheck v-if="check.ok" :size="16" />
                          <ShieldAlert v-else :size="16" />
                        </span>
                        <div class="check-text">
                          <div class="check-title">
                            <strong>{{ check.label }}</strong>
                            <StatusBadge :label="`HTTP ${check.actual}`" :tone="check.ok ? 'ok' : 'danger'" />
                          </div>
                        </div>
                      </div>
                    </div>
                    <div class="ssl-actions">
                      <n-button size="small" tertiary type="warning" :loading="chatBusy === 'disable'" :disabled="!!chatBusy" @click="confirmChatDisable">
                        Отключить чат-домен
                      </n-button>
                    </div>
                  </template>
                  <template v-else>
                    <p class="sec-note">
                      Отдельный адрес, где клиент сможет написать в поддержку и получить ключ —
                      даже когда VPN не работает. Доступа к панели через него нет.
                    </p>
                    <ol class="sec-howto">
                      <li>
                        У регистратора добавьте A-запись для поддомена (например <span class="mono">chat</span>)
                        на IP <span class="mono">{{ chatStatus.server_public_ip || server?.host }}</span>
                        <n-button size="tiny" quaternary @click="copyText(chatStatus.server_public_ip || server?.host || '')">
                          <template #icon><Copy :size="12" /></template>
                          копировать IP
                        </n-button>
                      </li>
                      <li>Введите полученный поддомен и нажмите «Подключить».</li>
                    </ol>
                    <div class="sec-row">
                      <n-input v-model:value="chatDomain" placeholder="chat.example.com" :disabled="!!chatBusy" />
                      <n-button type="primary" :loading="chatBusy === 'verify' || chatBusy === 'install'" @click="connectChat">Подключить</n-button>
                    </div>
                    <div class="ssl-nodomain">
                      <span class="ssl-nodomain-or">или без своего домена</span>
                      <n-button size="small" tertiary :loading="chatBusy === 'auto'" :disabled="!!chatBusy" @click="connectChatAuto">
                        <template #icon><ShieldCheck :size="14" /></template>
                        Чат без домена (sslip.io)
                      </n-button>
                      <p class="sec-note">
                        Подключит чат на адрес вида
                        <span class="mono">chat.{{ chatStatus.server_public_ip || server?.host }}.sslip.io</span>
                        — тот же IP, но отдельное имя (отличается от домена панели).
                      </p>
                    </div>
                    <p v-if="chatBusy === 'install' || chatBusy === 'auto'" class="sec-note">Выпускаю сертификат и проверяю изоляцию — 1–2 минуты.</p>
                    <p v-if="chatVerifyMessage && !chatVerifyOk" class="ssl-verify warn">{{ chatVerifyMessage }}</p>
                  </template>
                  <p v-if="chatStatus.message" class="ssl-msg warn">{{ chatStatus.message }}</p>
                </template>
                <p v-else class="ssl-msg warn">Не удалось получить состояние чат-домена — обновите страницу.</p>
              </div>
            </div>
          </div>
        </div>

        <div class="panel block">
          <button type="button" class="sec-audit-head" @click="auditOpen = !auditOpen">
            <h3>Аудит безопасности</h3>
            <span class="security-score" :class="securityWarnings ? 'warn' : 'ok'">
              {{ securityOkCount }} из {{ overview?.security?.length || 0 }} в порядке
            </span>
            <ChevronDown :size="15" class="sec-chev" :class="{ open: auditOpen }" />
          </button>
          <div v-if="overviewLoading && auditOpen" class="ssl-loading">
            <n-spin size="small" />
            <span>Проверяю…</span>
          </div>
          <div v-show="auditOpen && !overviewLoading" class="security-list" style="margin-top: 12px">
            <div v-for="check in overview?.security || []" :key="check.id" class="security-row">
              <span class="check-icon" :class="`check-${check.status}`">
                <ShieldCheck v-if="check.status === 'ok'" :size="16" />
                <ShieldAlert v-else-if="check.status === 'danger'" :size="16" />
                <Shield v-else :size="16" />
              </span>
              <div class="check-text">
                <div class="check-title">
                  <strong>{{ check.label }}</strong>
                  <StatusBadge :label="check.value || '—'" :tone="checkTone(check.status)" />
                </div>
                <p v-if="check.recommendation" class="check-rec">{{ check.recommendation }}</p>
              </div>
              <n-button
                v-if="check.actionable && check.control"
                size="tiny"
                :type="check.enabled ? 'default' : 'primary'"
                :loading="securityBusy === check.control"
                :disabled="!!securityBusy"
                class="check-action"
                @click="toggleSecurity(check)"
              >
                {{ check.enabled ? 'Выключить' : 'Включить' }}
              </n-button>
            </div>
          </div>
        </div>
      </div>

      <!-- Маскировка AWG -->
      <div v-if="activeTab === 'masking'" class="tab-body">
        <AwgMaskingPanel :server-id="serverId" @goto-protocols="activeTab = 'protocols'" />
      </div>

      <!-- Каскад -->
      <div v-show="activeTab === 'cascade'" class="tab-body cascade-page">
        <!-- Схема и статус -->
        <div class="panel block cascade-hero" :class="{ 'cascade-hero--on': cascadeActive }">
          <div class="cascade-hero-top">
            <div>
              <h3>Каскад VPN</h3>
              <p class="cascade-lead">
                Один профиль на телефоне — трафик идёт через два сервера.
                Сайты видят IP <strong>выходного</strong> сервера.
              </p>
            </div>
            <StatusBadge
              :label="cascadeActive ? 'Работает' : cascadeStateLabel"
              :tone="cascadeActive ? 'ok' : cascadeStateTone"
              :pulse="cascadeActive"
            />
          </div>

          <div class="cascade-route">
            <div class="route-node">
              <span class="route-icon"><Users :size="18" /></span>
              <span class="route-label">Клиент</span>
            </div>
            <ArrowRight :size="16" class="route-arrow" />
            <div class="route-node route-node--entry" :class="{ 'route-node--live': cascadeActive }">
              <span class="route-icon"><Server :size="18" /></span>
              <span class="route-label">{{ server?.name || 'Вход' }}</span>
              <span class="route-sub">Вход</span>
            </div>
            <ArrowRight :size="16" class="route-arrow" />
            <div class="route-node route-node--exit" :class="{ 'route-node--live': cascadeActive }">
              <span class="route-icon"><Globe :size="18" /></span>
              <span class="route-label">{{ cascadeExitLabel }}</span>
              <span v-if="cascadeExitHost" class="route-sub mono">{{ cascadeExitHost }}</span>
              <span v-else class="route-sub">Выход</span>
            </div>
            <ArrowRight :size="16" class="route-arrow" />
            <div class="route-node">
              <span class="route-icon"><Wifi :size="18" /></span>
              <span class="route-label">Интернет</span>
            </div>
          </div>

          <div v-if="cascadeActive && cascadeLink?.egress_ip" class="cascade-egress">
            <span class="cascade-egress-label">Ваш IP в интернете</span>
            <strong class="cascade-egress-ip mono">{{ cascadeLink.egress_ip }}</strong>
          </div>
        </div>

        <!-- Управление -->
        <div class="panel block cascade-manage">
          <div class="cascade-manage-actions">
            <n-button
              v-if="!cascadeActive"
              type="primary"
              size="large"
              :loading="cascadeApplyBusy === 'apply'"
              :disabled="!canApplyCascade || !!cascadeApplyBusy"
              @click="runApply"
            >
              Включить каскад
            </n-button>
            <n-button
              v-else
              type="error"
              size="large"
              :loading="cascadeApplyBusy === 'rollback'"
              :disabled="!!cascadeApplyBusy"
              @click="runRollback"
            >
              Выключить каскад
            </n-button>
            <p v-if="!canApplyCascade && !cascadeActive" class="cascade-hint">
              Сначала выберите выходной сервер и нажмите «Проверить».
            </p>
            <p v-else-if="cascadeActive" class="cascade-hint">
              Разделение трафика (РФ / зарубеж) — на вкладке
              <button type="button" class="cascade-link" @click="activeTab = 'rules'">Правило</button>.
            </p>
            <p v-else class="cascade-hint subtle">
              После перезагрузки сервера каскад нужно включить снова.
            </p>
          </div>

          <div v-if="cascadeApplyBusy" class="ssl-loading">
            <n-spin size="small" />
            <span>{{ cascadeApplyBusy === 'apply'
              ? 'Включаю каскад… Если на выходном сервере нет AmneziaWG, ставлю его — это может занять несколько минут. Не закрывайте страницу.'
              : 'Выключаю каскад…' }}</span>
          </div>

          <div v-if="cascadeApplyResult && !cascadeApplyBusy" class="cascade-result-banner" :class="cascadeApplyResult.ok ? 'ok' : 'warn'">
            <div class="cascade-result-msg">{{ cascadeApplyResult.message }}</div>
            <ul v-if="cascadeApplyResult.steps?.length" class="cascade-result-steps">
              <li v-for="(s, i) in cascadeApplyResult.steps" :key="i" :class="`step-${s.status}`">
                <span class="step-mark">{{ s.status === 'ok' ? '✓' : s.status === 'failed' ? '✕' : '•' }}</span>
                <span class="step-name">{{ s.name }}</span>
                <span v-if="s.detail" class="step-detail">— {{ s.detail }}</span>
              </li>
            </ul>
          </div>
        </div>

        <!-- Настройка (свёрнута, когда каскад уже работает) -->
        <details class="panel block cascade-setup" :open="!cascadeActive">
          <summary class="cascade-setup-summary">
            <span>Настройка связи</span>
            <StatusBadge
              v-if="cascadeActive"
              label="Работает"
              tone="ok"
              :pulse="true"
            />
            <StatusBadge
              v-else-if="cascadeResult?.ok"
              label="Готово"
              tone="ok"
            />
            <StatusBadge
              v-else-if="cascadeResult && !cascadeResult.ok"
              label="Есть проблемы"
              tone="danger"
            />
          </summary>

          <div class="cascade-setup-body">
            <div class="cascade-controls">
              <n-select
                v-model:value="cascadeExitId"
                :options="exitOptions"
                placeholder="Выходной сервер"
                :loading="cascadeServersLoading"
                clearable
                style="flex: 1; min-width: 200px; max-width: 360px"
              />
              <n-button
                :loading="cascadeBusy"
                :disabled="!cascadeExitId || cascadeBusy"
                @click="runPreflight"
              >
                Проверить
              </n-button>
            </div>

            <p class="cascade-hint subtle">
              {{ cascadeActive
                ? 'Каскад включён. Проверка подтверждает, что связь работает.'
                : 'Проверка ничего не меняет — только смотрит, готовы ли серверы.' }}
            </p>

            <div v-if="cascadeBusy" class="ssl-loading">
              <n-spin size="small" />
              <span>Проверяю серверы…</span>
            </div>

            <template v-else-if="cascadeResult || cascadeActive">
              <div
                v-if="setupBannerText"
                class="cascade-check-banner"
                :class="setupBannerTone"
              >
                {{ setupBannerText }}
              </div>

              <div v-if="setupExitIp" class="cascade-mini-kpi">
                <span>IP выходного сервера</span>
                <strong class="mono">{{ setupExitIp }}</strong>
              </div>

              <div v-if="cascadeResult?.blockers?.length" class="cascade-blockers">
                <strong>Что мешает:</strong>
                <ul>
                  <li v-for="(b, i) in cascadeResult.blockers" :key="i">{{ b }}</li>
                </ul>
              </div>

              <details v-if="cascadeResult?.checks?.length && !cascadeActive" class="cascade-tech-details">
                <summary>Технические детали проверки</summary>
                <div class="security-list">
                  <div v-for="check in cascadeResult.checks" :key="check.id" class="security-row">
                    <span class="check-icon" :class="`check-${check.status}`">
                      <ShieldCheck v-if="check.status === 'ok'" :size="16" />
                      <ShieldAlert v-else-if="check.status === 'danger'" :size="16" />
                      <Shield v-else :size="16" />
                    </span>
                    <div class="check-text">
                      <div class="check-title">
                        <strong>{{ check.label }}</strong>
                        <StatusBadge :label="check.value || '—'" :tone="checkTone(check.status)" />
                      </div>
                      <p v-if="check.detail" class="check-rec">{{ check.detail }}</p>
                    </div>
                  </div>
                </div>
              </details>
            </template>
          </div>
        </details>

        <XrayCascadePanel :server-id="serverId" :server-name="server?.name" />
      </div>

      <!-- Правило: разделение трафика РФ / зарубеж -->
      <div v-show="activeTab === 'rules'" class="tab-body rules-page">
        <div v-if="rulesLoading" class="panel placeholder">
          <n-spin size="small" />
          <span>Загружаю…</span>
        </div>

        <template v-else-if="!isEntryServer">
          <div class="panel block rules-hero">
            <h3>Разделение трафика</h3>
            <p class="rules-lead">
              <template v-if="cascadePeerInfo?.role === 'exit'">
                Настраивается на входном сервере «{{ cascadePeerInfo.name }}» → вкладка «Правило».
              </template>
              <template v-else>
                Сначала настройте каскад на вкладке «Каскад».
              </template>
            </p>
          </div>
        </template>

        <template v-else>
          <div class="panel block rules-hero" :class="{ 'rules-hero--on': rulesStatus?.applied }">
            <div class="rules-hero-top">
              <div>
                <h3>Разделение трафика</h3>
                <p class="rules-lead">
                  Российские сайты открываются с IP России, зарубежные — с IP выходного сервера.
                  Профиль на телефоне менять не нужно.
                </p>
              </div>
              <StatusBadge
                :label="rulesBadgeLabel"
                :tone="rulesBadgeTone"
                :pulse="rulesStatus?.applied"
              />
            </div>

            <div class="rules-examples">
              <div class="rules-example rules-example--ru">
                <Globe :size="15" />
                <div>
                  <strong>Из России</strong>
                  <span>Яндекс · VK · Госуслуги · Сбер</span>
                  <span class="rules-route">→ {{ rulesStatus?.entry_name || 'входной' }}</span>
                </div>
              </div>
              <div class="rules-example rules-example--abroad">
                <Network :size="15" />
                <div>
                  <strong>Из-за рубежа</strong>
                  <span>YouTube · Google · Instagram · Netflix</span>
                  <span class="rules-route">→ {{ rulesStatus?.exit_name || 'выходной' }}</span>
                </div>
              </div>
            </div>

            <div class="split-toggle-row">
              <n-switch
                :value="splitEnabled"
                :loading="rulesBusy === 'save'"
                :disabled="!!rulesBusy"
                @update:value="onToggleSplit"
              />
              <div class="split-toggle-text">
                <strong>{{ splitEnabled ? 'Включено' : 'Выключено' }}</strong>
                <span class="rules-hint">{{ rulesHintText }}</span>
              </div>
            </div>

            <p v-if="rulesStatus?.last_error" class="ssl-msg warn" style="margin-top: 10px">
              {{ rulesStatus.last_error }}
            </p>
          </div>

          <!-- Компактная проверка -->
          <div v-if="rulesStatus?.applied && rulesStatus?.health" class="panel block rules-check">
            <div class="rules-check-grid">
              <div class="rules-check-item" :class="rulesStatus.health.ru_in_set ? 'ok' : 'bad'">
                <ShieldCheck v-if="rulesStatus.health.ru_in_set" :size="18" />
                <ShieldAlert v-else :size="18" />
                <span>Россия — через {{ rulesStatus.entry_name || 'входной' }}</span>
              </div>
              <div class="rules-check-item" :class="rulesStatus.health.foreign_excluded ? 'ok' : 'bad'">
                <ShieldCheck v-if="rulesStatus.health.foreign_excluded" :size="18" />
                <ShieldAlert v-else :size="18" />
                <span>Зарубеж — через {{ rulesStatus.exit_name || 'выходной' }}</span>
              </div>
            </div>
          </div>

          <!-- Настройки списка -->
          <details class="panel block rules-setup" :open="!rulesStatus?.applied">
            <summary class="rules-setup-summary">
              <span>Список российских сайтов</span>
              <span class="rules-setup-meta">{{ rulesStatus?.direct_cidr_count || 0 }} адресов</span>
            </summary>

            <div class="rules-setup-body">
              <p class="rules-hint subtle">
                Панель сама определяет, какие адреса считать российскими. Обычно достаточно баз по умолчанию.
              </p>

              <div class="source-list">
                <label
                  v-for="src in rulesStatus?.sources || []"
                  :key="src.id"
                  class="source-row"
                >
                  <n-checkbox
                    :checked="selectedSources.includes(src.id)"
                    :disabled="!!rulesBusy"
                    @update:checked="(c: boolean) => toggleSource(src.id, c)"
                  />
                  <div class="source-text">
                    <strong>{{ src.label }}</strong>
                    <span class="rules-hint subtle">{{ src.description }}</span>
                  </div>
                </label>
              </div>

              <div class="custom-cidr">
                <label class="kpi-label">Свои адреса (необязательно)</label>
                <n-input
                  v-model:value="customCidrsText"
                  type="textarea"
                  :rows="2"
                  :disabled="!!rulesBusy"
                  placeholder="Один адрес в строке, например 95.163.0.0/16"
                />
              </div>

              <div class="cascade-controls" style="margin-top: 12px">
                <n-button
                  type="primary"
                  :loading="rulesBusy === 'save'"
                  :disabled="!!rulesBusy"
                  @click="saveRules(splitEnabled)"
                >
                  Сохранить
                </n-button>
                <n-button
                  tertiary
                  :loading="rulesBusy === 'refresh'"
                  :disabled="!!rulesBusy || !rulesStatus?.enabled"
                  @click="refreshRulesLists"
                >
                  <template #icon><RefreshCw :size="16" /></template>
                  Обновить базу
                </n-button>
                <span class="rules-hint subtle">Обновлено {{ rulesUpdatedLabel }}</span>
              </div>
            </div>
          </details>

          <div
            v-if="rulesResult"
            class="cascade-result-banner"
            :class="rulesResult.ok ? 'ok' : 'warn'"
          >
            {{ rulesResult.message }}
          </div>
        </template>
      </div>

      <!-- Контейнеры -->
      <div v-show="activeTab === 'containers'" class="tab-body">
        <div v-if="overviewLoading" class="panel placeholder">
          <n-spin size="small" />
          <span>Получаю список контейнеров…</span>
        </div>
        <div v-else-if="!overview?.containers?.length" class="panel placeholder">
          <span>Docker-контейнеры не найдены.</span>
        </div>
        <div v-else class="panel block containers-panel">
          <div v-for="item in overview.containers" :key="item.name" class="container-row">
            <span class="container-state" :class="item.state === 'running' ? 'on' : 'off'" />
            <div class="container-text">
              <strong class="mono">{{ item.name }}</strong>
              <span class="container-sub">{{ item.image }} · {{ item.status }}</span>
            </div>
            <div class="container-stats">
              <span v-if="item.cpu_percent != null" class="stat-chip mono">CPU {{ item.cpu_percent.toFixed(1) }}%</span>
              <span v-if="item.mem_usage" class="stat-chip mono">{{ item.mem_usage }}</span>
            </div>
            <div class="container-actions">
              <n-button
                circle
                tertiary
                size="tiny"
                title="Логи"
                @click="openLogs(item.name)"
              >
                <template #icon><FileText :size="13" /></template>
              </n-button>
              <n-button
                v-if="item.state === 'running'"
                circle
                tertiary
                size="tiny"
                title="Перезапустить"
                :loading="isContainerBusy(item.name)"
                @click="containerAction(item.name, 'restart')"
              >
                <template #icon><RotateCcw :size="13" /></template>
              </n-button>
              <n-button
                v-if="item.state === 'running'"
                circle
                tertiary
                size="tiny"
                title="Остановить"
                :loading="isContainerBusy(item.name)"
                @click="confirmContainerStop(item.name)"
              >
                <template #icon><Square :size="13" /></template>
              </n-button>
              <n-button
                v-else
                circle
                tertiary
                size="tiny"
                title="Запустить"
                :loading="isContainerBusy(item.name)"
                @click="containerAction(item.name, 'start')"
              >
                <template #icon><Play :size="13" /></template>
              </n-button>
            </div>
          </div>
        </div>
      </div>
    </template>

    <div v-else class="panel placeholder">
      <StatusBadge label="Не найден" tone="danger" />
      <span>Сервер не найден в панели.</span>
    </div>

    <InstallProtocolModal
      v-model:show="installVisible"
      :server-id="serverId"
      :protocol="installProtocol"
      @installed="onProtocolInstalled"
    />

    <n-modal v-model:show="xrayMaskingVisible">
      <div class="panel masking-modal">
        <header class="masking-head">
          <strong>Маскировка Reality</strong>
          <StatusBadge
            v-if="xrayMaskingData"
            :label="xrayMaskingData.label || 'Неизвестно'"
            :tone="maskingStatusTone"
          />
        </header>

        <div v-if="xrayMaskingLoading" class="masking-loading">
          <n-spin size="small" />
          <span>Читаю конфиг Reality…</span>
        </div>

        <template v-else-if="xrayMaskingData">
          <div class="masking-grid">
            <span class="mk-label">Порт</span>
            <span class="mk-val mono">{{ xrayMaskingData.port ?? '—' }}</span>
            <span class="mk-label">dest</span>
            <span class="mk-val mono">
              {{ xrayMaskingData.dest || '—' }}
              <template v-if="xrayMaskingData.dest_reachable === true"> (доступен)</template>
              <template v-else-if="xrayMaskingData.dest_reachable === false"> (не отвечает)</template>
            </span>
            <span class="mk-label">SNI</span>
            <span class="mk-val mono">{{ xrayMaskingData.sni || '—' }}</span>
            <span class="mk-label">shortId</span>
            <span class="mk-val mono">{{ xrayMaskingData.short_id_count ?? 0 }} шт.</span>
            <span class="mk-label">flow</span>
            <span class="mk-val mono">{{ xrayMaskingData.flow || '—' }}</span>
            <span class="mk-label">Клиентов</span>
            <span class="mk-val mono">{{ xrayMaskingData.clients_count ?? 0 }}</span>
          </div>

          <ul v-if="(xrayMaskingData.critical || []).length" class="masking-issues critical">
            <li v-for="(c, i) in xrayMaskingData.critical" :key="'c' + i">⚠ {{ c }}</li>
          </ul>
          <ul v-if="(xrayMaskingData.notes || []).length" class="masking-issues notes">
            <li v-for="(n, i) in xrayMaskingData.notes" :key="'n' + i">• {{ n }}</li>
          </ul>

          <div class="masking-actions">
            <label class="mk-field-label">Домен маскировки (dest + SNI)</label>
            <n-input
              v-model:value="xrayMaskingDomain"
              placeholder="например, www.microsoft.com"
              :disabled="xrayMaskingBusy"
            />
            <span class="mk-hint">
              Реальный TLS-сайт (не наш сервер). Смена домена переиздаёт всех Xray-клиентов —
              им нужно заново импортировать конфиг.
            </span>
            <div class="mk-buttons">
              <n-button
                type="primary"
                :loading="xrayMaskingBusy"
                @click="applyMaskingDomain"
              >
                Сменить домен
              </n-button>
              <n-button
                tertiary
                :loading="xrayMaskingBusy"
                title="Добавить ещё один shortId (существующие клиенты не затрагиваются)"
                @click="addShortIdAction"
              >
                + shortId
              </n-button>
              <n-button quaternary @click="xrayMaskingVisible = false">Закрыть</n-button>
            </div>
          </div>

          <p class="mk-disclaimer">
            Статус — это качество настройки маскировки, а не гарантия обхода DPI.
          </p>
        </template>
      </div>
    </n-modal>

    <n-modal v-model:show="logsVisible">
      <div class="panel logs-modal">
        <header class="logs-head">
          <h3 class="mono">{{ logsContainer }}</h3>
          <n-button circle tertiary size="small" @click="logsVisible = false">
            <template #icon><X :size="15" /></template>
          </n-button>
        </header>
        <div v-if="logsLoading" class="logs-loading">
          <n-spin size="small" />
          <span>Читаю логи…</span>
        </div>
        <pre v-else class="logs-pre mono">{{ logsText || 'Логи пустые.' }}</pre>
      </div>
    </n-modal>
  </AppShell>
</template>

<script setup lang="ts">
import {
  ArrowDownUp,
  ArrowLeft,
  ArrowRight,
  Boxes,
  Check,
  ChevronDown,
  Clock,
  Copy,
  Lock,
  Cpu,
  Download,
  FileText,
  Fingerprint,
  Gauge,
  Globe,
  HardDrive,
  MemoryStick,
  Network,
  Play,
  RefreshCw,
  RotateCcw,
  Route,
  Save,
  Server,
  Shield,
  ShieldAlert,
  ShieldCheck,
  Square,
  Trash2,
  Users,
  Wifi,
  WifiOff,
  X
} from '@lucide/vue'
import {
  NButton,
  NCheckbox,
  NInput,
  NModal,
  NSelect,
  NSpin,
  NSwitch,
  useDialog,
  useMessage
} from 'naive-ui'
import { computed, h, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { api } from '@/api/client'
import AwgMaskingPanel from '@/components/AwgMaskingPanel.vue'
import XrayCascadePanel from '@/components/XrayCascadePanel.vue'
import InstallProtocolModal from '@/components/InstallProtocolModal.vue'
import DpiTrendCard from '@/components/DpiTrendCard.vue'
import MetricBar from '@/components/MetricBar.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import AppShell from '@/layouts/AppShell.vue'
import { useServerDetailCache } from '@/stores/serverDetailCache'
import { labelCascadeState, toneCascadeState } from '@/utils/cascadeLabels'
import { formatBytes, formatUptime, percentOf } from '@/utils/format'

type ServerRead = {
  id: string
  name: string
  host: string
  ssh_port: number
  status: string
  active_peers: number
  vpn_port: number | null
  created_at: string | null
  awg2_imported?: boolean
  client_protocols?: string[]
}

type ServerMetrics = {
  online: boolean
  cpu_percent: number | null
  mem_used_bytes: number | null
  mem_total_bytes: number | null
  disk_used_bytes: number | null
  disk_total_bytes: number | null
  uptime_seconds: number | null
  active_peers: number
  total_traffic_bytes: number
  message?: string | null
}

type ProtocolInfo = {
  id: string
  name: string
  description: string
  installed: boolean
  running: boolean
  container: string | null
  ports: string
  managed: boolean
  can_install: boolean
  clients_count: number
}

type ProtocolVersion = {
  protocol: string
  container: string | null
  running: boolean
  status: string
  pinned_version: string | null
  installed_version: string | null
  up_to_date: boolean | null
  needs_adopt: boolean
}

type ContainerInfo = {
  name: string
  image: string
  state: string
  status: string
  ports: string
  cpu_percent: number | null
  mem_usage: string | null
}

type SecurityCheck = {
  id: string
  label: string
  status: 'ok' | 'warning' | 'danger' | 'unknown'
  value: string
  recommendation: string | null
  actionable?: boolean
  control?: string | null
  enabled?: boolean | null
}

type UfwPreview = {
  tcp_ports: number[]
  udp_ports: number[]
  ssh_port: number
}

type ChatIsolationCheck = { label: string; expected: string; actual: string; ok: boolean }

type ChatDomainStatus = {
  domain: string | null
  enabled: boolean
  ssl_status: string
  public_url: string | null
  cert_expires_at: string | null
  panel_https_active: boolean
  harden_active: boolean
  server_public_ip: string | null
  dns_record_hint: string | null
  message: string | null
}

type PanelHardenStatus = {
  enabled: boolean
  allowed_ips: string[]
  persistent: boolean
  https_active: boolean
  https_url: string | null
  your_ip: string | null
  message: string | null
}

type PanelSslStatus = {
  domain: string | null
  url: string | null
  status: string
  panel_detected: boolean
  xray_on_443: boolean
  nginx_installed: boolean
  cert_present: boolean
  cert_expires_at: string | null
  public_ip: string | null
  fallback_url: string | null
  message: string | null
}

type CascadeCheck = {
  id: string
  label: string
  status: 'ok' | 'warning' | 'danger' | 'unknown'
  value: string
  detail: string | null
}

type CascadePreflightResult = {
  ok: boolean
  entry_server_id: string
  exit_server_id: string
  entry_name: string | null
  exit_name: string | null
  client_subnet: string | null
  source_visibility: string
  recommended_hook: string
  amnezia_container: string | null
  amnezia_netns_pid: number | null
  exit_public_ip: string | null
  exit_awg_tooling: string
  transit_subnet: string | null
  transit_port: number | null
  checks: CascadeCheck[]
  blockers: string[]
  message: string
  live_active?: boolean
}

type CascadeLinkStatus = {
  entry_server_id: string
  exit_server_id: string | null
  exit_name: string | null
  state: string
  client_subnet: string | null
  transit_subnet: string | null
  transit_port: number | null
  recommended_hook: string | null
  last_preflight_at: string | null
  last_preflight_ok: boolean
  last_applied_at: string | null
  egress_ip: string | null
  message: string | null
  live_active: boolean
}

type CascadeStep = {
  name: string
  status: 'ok' | 'failed' | 'skipped'
  detail: string | null
}

type CascadeApplyResult = {
  ok: boolean
  state: string
  entry_server_id: string
  exit_server_id: string
  egress_ip: string | null
  expected_exit_ip: string | null
  transit_subnet: string | null
  transit_port: number | null
  steps: CascadeStep[]
  message: string
}

type SplitSourceInfo = {
  id: string
  label: string
  description: string
  default_enabled: boolean
  kind: string
}

type CascadeRulesStatus = {
  entry_server_id: string
  cascade_active: boolean
  enabled: boolean
  applied: boolean
  source_ids: string[]
  custom_cidrs: string[]
  direct_cidr_count: number
  list_updated_at: string | null
  sources: SplitSourceInfo[]
  entry_name: string | null
  exit_name: string | null
  entry_public_ip: string | null
  exit_public_ip: string | null
  health: Record<string, boolean> | null
  last_error: string | null
  message: string | null
}

type CascadeRulesApplyResult = {
  ok: boolean
  enabled: boolean
  applied: boolean
  direct_cidr_count: number
  steps: CascadeStep[]
  health: Record<string, boolean> | null
  invalid_cidrs: string[]
  message: string
}

type ServerOverview = {
  online: boolean
  message?: string | null
  system: {
    os: string | null
    kernel: string | null
    arch: string | null
    cpu_model: string | null
    cores: number | null
    docker_version: string | null
    public_ip: string | null
  }
  containers: ContainerInfo[]
  protocols: ProtocolInfo[]
  security: SecurityCheck[]
}

const route = useRoute()
const router = useRouter()
const dialog = useDialog()
const message = useMessage()

const serverId = route.params.id as string
const detailCache = useServerDetailCache()

const loading = ref(true)
const refreshing = ref(false)
const overviewLoading = ref(true)
const server = ref<ServerRead | null>(null)
const metrics = ref<ServerMetrics | null>(null)
const overview = ref<ServerOverview | null>(null)

type DetailTab = 'overview' | 'protocols' | 'security' | 'masking' | 'cascade' | 'rules' | 'containers'

const activeTab = ref<DetailTab>('overview')

const hasAwg2 = computed(() =>
  (overview.value?.protocols || []).some((p) => p.id === 'awg2' && p.installed)
)

type DetailTabItem = { id: DetailTab; label: string; icon: typeof Gauge }

const tabs = computed<DetailTabItem[]>(() => {
  const base: DetailTabItem[] = [
    { id: 'overview', label: 'Обзор', icon: Gauge },
    { id: 'protocols', label: 'Протоколы', icon: ShieldCheck },
    { id: 'security', label: 'Безопасность', icon: Shield }
  ]
  if (hasAwg2.value) {
    base.push({ id: 'masking', label: 'Маскировка', icon: Fingerprint })
  }
  base.push(
    { id: 'cascade', label: 'Каскад', icon: Network },
    { id: 'rules', label: 'Правило', icon: Route },
    { id: 'containers', label: 'Контейнеры', icon: Boxes }
  )
  return base
})

const protoBusy = reactive<Record<string, string>>({})
const containerBusy = reactive<Record<string, boolean>>({})

const installVisible = ref(false)
const installProtocol = ref<ProtocolInfo | null>(null)

const logsVisible = ref(false)
const logsLoading = ref(false)
const logsContainer = ref('')
const logsText = ref('')

const sslLoading = ref(false)
const sslStatus = ref<PanelSslStatus | null>(null)
const sslDomain = ref('')
const sslEmail = ref('')
const sslVerifyMessage = ref('')
const sslVerifyOk = ref(false)
const sslBusy = ref<'verify' | 'install' | 'auto' | 'rollback' | ''>('')

const hardenLoading = ref(false)
const hardenStatus = ref<PanelHardenStatus | null>(null)
const hardenIpsText = ref('')
const hardenBusy = ref<'apply' | 'disable' | ''>('')

const chatLoading = ref(false)
const chatStatus = ref<ChatDomainStatus | null>(null)
const chatDomain = ref('')
const chatVerifyMessage = ref('')
const chatVerifyOk = ref(false)
const chatBusy = ref<'verify' | 'install' | 'auto' | 'disable' | ''>('')
const chatIsolation = ref<ChatIsolationCheck[]>([])

const openStep = ref<'' | 'ssl' | 'harden' | 'chat'>('')
const auditOpen = ref(false)

const sslDone = computed(() => sslStatus.value?.status === 'active')
const hardenDone = computed(() => !!hardenStatus.value?.enabled)
const chatDone = computed(() => !!chatStatus.value?.enabled)
const secDoneCount = computed(
  () => [sslDone.value, hardenDone.value, chatDone.value].filter(Boolean).length
)

const hardenStepNote = computed(() => {
  if (!hardenDone.value) return 'открыт всем из интернета'
  const ips = hardenStatus.value?.allowed_ips || []
  return ips.length ? `только ${ips.join(', ')}` : 'закрыт полностью'
})

const hardenYourIpMissing = computed(() => {
  const your = hardenStatus.value?.your_ip
  if (!your) return false
  return !hardenIpsText.value
    .split(/[,\s]+/)
    .map((s) => s.trim())
    .includes(your)
})

const chatIsolationAllOk = computed(
  () => chatIsolation.value.length > 0 && chatIsolation.value.every((c) => c.ok)
)

function toggleStep(step: 'ssl' | 'harden' | 'chat') {
  if (step === 'harden' && !sslDone.value) {
    message.info('Сначала выполните шаг 1 — вход по HTTPS.')
    return
  }
  if (step === 'chat' && !hardenDone.value) {
    message.info(sslDone.value ? 'Сначала выполните шаг 2 — закройте :8080.' : 'Сначала выполните шаги 1 и 2.')
    return
  }
  openStep.value = openStep.value === step ? '' : step
}

function pickOpenStep() {
  if (!sslDone.value) openStep.value = 'ssl'
  else if (!hardenDone.value) openStep.value = 'harden'
  else if (!chatDone.value) openStep.value = 'chat'
  else openStep.value = ''
}

async function loadSecurityTab() {
  await Promise.all([loadSslStatus(), loadHardenStatus(), loadChatDomainStatus()])
  pickOpenStep()
}

async function copyText(text: string) {
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
    message.success('Скопировано')
  } catch {
    message.error('Не удалось скопировать')
  }
}

async function connectSsl() {
  await verifySsl()
  if (!sslVerifyOk.value) return
  await installSsl()
  pickOpenStep()
}

async function connectSslAuto() {
  sslBusy.value = 'auto'
  try {
    const { data } = await api.post(
      `/servers/${serverId}/panel-ssl/install-auto`,
      { email: sslEmail.value.trim() || null },
      { timeout: 660_000 }
    )
    message.success(data.message)
    await Promise.all([loadSslStatus(), loadOverview(), loadHardenStatus()])
    pickOpenStep()
  } catch (error: any) {
    message.error(error?.response?.data?.detail || 'Установка HTTPS без домена не удалась.')
  } finally {
    sslBusy.value = ''
  }
}

async function connectChat() {
  await verifyChatDomain()
  if (!chatVerifyOk.value) return
  await installChatDomain()
  if (chatDone.value) openStep.value = 'chat'
}

async function connectChatAuto() {
  chatBusy.value = 'auto'
  chatIsolation.value = []
  try {
    const { data } = await api.post<{ message: string; isolation: ChatIsolationCheck[] }>(
      `/servers/${serverId}/chat-domain/install-auto`,
      {},
      { timeout: 660_000 }
    )
    chatIsolation.value = data.isolation
    message.success(data.message)
    await loadChatDomainStatus()
    if (chatDone.value) openStep.value = 'chat'
  } catch (error: any) {
    message.error(error?.response?.data?.detail || 'Подключение чат-домена без домена не удалось.')
  } finally {
    chatBusy.value = ''
  }
}

const cascadeBusy = ref(false)
const cascadeServersLoading = ref(false)
const cascadeExitId = ref<string | null>(null)
const cascadeResult = ref<CascadePreflightResult | null>(null)
const cascadeLink = ref<CascadeLinkStatus | null>(null)
const otherServers = ref<ServerRead[]>([])
const cascadeApplyBusy = ref<'apply' | 'rollback' | ''>('')
const cascadeApplyResult = ref<CascadeApplyResult | null>(null)

type CascadeLinkSummary = {
  entry_server_id: string
  entry_name: string
  exit_server_id: string
  exit_name: string
  state: string
  is_active: boolean
  live_active?: boolean
}

const cascadeLinks = ref<CascadeLinkSummary[]>([])

const rulesStatus = ref<CascadeRulesStatus | null>(null)
const rulesLoading = ref(false)
const rulesBusy = ref<'save' | 'refresh' | ''>('')
const rulesResult = ref<CascadeRulesApplyResult | null>(null)
const splitEnabled = ref(false)
const selectedSources = ref<string[]>([])
const customCidrsText = ref('')

const isEntryServer = computed(() => cascadePeerInfo.value?.role === 'entry')

function syncRulesForm(s: CascadeRulesStatus) {
  splitEnabled.value = s.enabled
  selectedSources.value = [...s.source_ids]
  customCidrsText.value = (s.custom_cidrs || []).join('\n')
}

async function loadRulesStatus() {
  rulesLoading.value = true
  try {
    const { data } = await api.get<CascadeRulesStatus>(`/servers/${serverId}/cascade/rules`)
    rulesStatus.value = data
    syncRulesForm(data)
  } catch {
    rulesStatus.value = null
  } finally {
    rulesLoading.value = false
  }
}

function toggleSource(id: string, checked: boolean) {
  if (checked) selectedSources.value = [...new Set([...selectedSources.value, id])]
  else selectedSources.value = selectedSources.value.filter((s) => s !== id)
}

function parseCustomCidrs(): string[] {
  return customCidrsText.value
    .split(/[\s,;]+/)
    .map((s) => s.trim())
    .filter(Boolean)
}

async function saveRules(nextEnabled: boolean) {
  rulesBusy.value = 'save'
  rulesResult.value = null
  try {
    const { data } = await api.put<CascadeRulesApplyResult>(
      `/servers/${serverId}/cascade/rules`,
      {
        enabled: nextEnabled,
        source_ids: selectedSources.value,
        custom_cidrs: parseCustomCidrs()
      },
      { timeout: 180_000 }
    )
    rulesResult.value = data
    if (data.invalid_cidrs?.length) {
      message.warning(`Некорректные адреса пропущены: ${data.invalid_cidrs.join(', ')}`)
    }
    if (data.ok) message.success(data.message)
    else message.error(data.message)
    await loadRulesStatus()
  } catch (error: any) {
    message.error(error?.response?.data?.detail || 'Не удалось применить правила.')
    splitEnabled.value = rulesStatus.value?.enabled ?? false
  } finally {
    rulesBusy.value = ''
  }
}

async function refreshRulesLists() {
  rulesBusy.value = 'refresh'
  rulesResult.value = null
  try {
    const { data } = await api.post<CascadeRulesApplyResult>(
      `/servers/${serverId}/cascade/rules/refresh`,
      {},
      { timeout: 180_000 }
    )
    rulesResult.value = data
    if (data.ok) message.success(data.message || 'База обновлена.')
    else message.error(data.message)
    await loadRulesStatus()
  } catch (error: any) {
    message.error(error?.response?.data?.detail || 'Не удалось обновить базу.')
  } finally {
    rulesBusy.value = ''
  }
}

function onToggleSplit(value: boolean) {
  splitEnabled.value = value
  void saveRules(value)
}

const rulesUpdatedLabel = computed(() => {
  const ts = rulesStatus.value?.list_updated_at
  if (!ts) return 'никогда'
  try {
    return new Date(ts).toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' })
  } catch {
    return ts
  }
})

const rulesBadgeLabel = computed(() => {
  if (rulesStatus.value?.applied) return 'Работает'
  if (rulesStatus.value?.enabled) return 'Включено'
  return 'Выключено'
})

const rulesBadgeTone = computed(() => {
  if (rulesStatus.value?.applied) return 'ok' as const
  if (rulesStatus.value?.enabled) return 'warning' as const
  return 'neutral' as const
})

const rulesHintText = computed(() => {
  const s = rulesStatus.value
  if (!s) return ''
  if (!s.cascade_active) return 'Включите каскад — правило применится само.'
  if (s.applied) return 'Телефон менять не нужно — всё на сервере.'
  if (s.enabled) return 'Нажмите «Сохранить» в блоке ниже.'
  return 'Весь интернет идёт через выходной сервер.'
})

const cascadePeerInfo = computed(() => {
  for (const link of cascadeLinks.value) {
    if (link.entry_server_id === serverId) {
      return {
        role: 'entry' as const,
        name: link.exit_name,
        state: link.state,
        is_active: link.is_active,
        peer_id: link.exit_server_id
      }
    }
    if (link.exit_server_id === serverId) {
      return {
        role: 'exit' as const,
        name: link.entry_name,
        state: link.state,
        is_active: link.is_active,
        peer_id: link.entry_server_id
      }
    }
  }
  if (cascadeLink.value?.exit_server_id) {
    return {
      role: 'entry' as const,
      name: cascadeLink.value.exit_name || 'exit',
      state: cascadeLink.value.state,
      is_active: Boolean(cascadeLink.value.live_active) || cascadeLink.value.state === 'active',
      peer_id: cascadeLink.value.exit_server_id
    }
  }
  return null
})

function applyTabFromQuery() {
  const tab = route.query.tab
  if (typeof tab === 'string' && tabs.value.some((t) => t.id === tab)) {
    activeTab.value = tab as DetailTab
  }
}

onMounted(async () => {
  // Мгновенно показываем последний снимок из кэша, если он есть,
  // а затем в фоне сверяем с сервером (stale-while-revalidate).
  const cached = detailCache.snapshot(serverId)
  if (cached?.server) {
    server.value = cached.server as ServerRead
    loading.value = false
  }
  if (cached?.metrics) metrics.value = cached.metrics as ServerMetrics
  if (cached?.overview) {
    overview.value = cached.overview as ServerOverview
    overviewLoading.value = false
  }

  try {
    const { data } = await api.get<ServerRead>(`/servers/${serverId}`)
    server.value = data
    detailCache.patch(serverId, { server: data })
  } catch {
    if (!cached?.server) server.value = null
  } finally {
    loading.value = false
  }
  if (server.value) {
    void loadMetrics()
    void loadOverview()
    void loadCascadeLinks()
    applyTabFromQuery()
    if (activeTab.value === 'cascade') {
      if (!otherServers.value.length) void loadOtherServers()
      void loadCascadeStatus()
    }
    if (activeTab.value === 'rules') void loadRulesStatus()
    if (activeTab.value === 'protocols') void loadProtocolVersions()
  }
})

async function loadCascadeLinks() {
  try {
    const { data } = await api.get<CascadeLinkSummary[]>('/servers/cascade/links')
    cascadeLinks.value = data
  } catch {
    cascadeLinks.value = []
  }
}

async function loadMetrics(refresh = false) {
  try {
    const { data } = await api.get<ServerMetrics>(`/servers/${serverId}/metrics`, {
      params: { refresh }
    })
    metrics.value = data
    detailCache.patch(serverId, { metrics: data })
  } catch {
    if (!metrics.value) metrics.value = null
  }
}

async function loadOverview() {
  // Спиннер показываем только когда показывать ещё нечего; при наличии
  // кэша обновляем данные молча.
  if (!overview.value) overviewLoading.value = true
  try {
    const { data } = await api.get<ServerOverview>(`/servers/${serverId}/overview`)
    overview.value = data
    detailCache.patch(serverId, { overview: data })
    if (!data.online && data.message) message.warning(data.message)
  } catch {
    if (!overview.value) message.error('Не удалось получить данные сервера.')
  } finally {
    overviewLoading.value = false
  }
}

const protocolVersions = ref<Record<string, ProtocolVersion>>({})
const versionsLoading = ref(false)

async function loadProtocolVersions() {
  versionsLoading.value = true
  try {
    const { data } = await api.get<{ protocols: ProtocolVersion[] }>(
      `/servers/${serverId}/protocols/versions`,
    )
    const map: Record<string, ProtocolVersion> = {}
    for (const item of data.protocols || []) map[item.protocol] = item
    protocolVersions.value = map
  } catch {
    /* версии — вспомогательная инфа, тихо игнорируем сбой */
  } finally {
    versionsLoading.value = false
  }
}

async function refreshAll() {
  refreshing.value = true
  try {
    const tasks = [loadMetrics(true), loadOverview()]
    if (activeTab.value === 'security') tasks.push(loadSecurityTab())
    await Promise.all(tasks)
  } finally {
    refreshing.value = false
  }
}

const fallbackGuess = computed(() => {
  const host = server.value?.host
  return host ? `http://${host}:8080` : 'http://IP:8080'
})

async function loadSslStatus() {
  sslLoading.value = true
  try {
    const { data } = await api.get<PanelSslStatus>(`/servers/${serverId}/panel-ssl/status`)
    sslStatus.value = data
    if (data.domain && !sslDomain.value) sslDomain.value = data.domain
    sslVerifyOk.value = false
    sslVerifyMessage.value = ''
  } catch {
    sslStatus.value = null
  } finally {
    sslLoading.value = false
  }
}

async function loadChatDomainStatus() {
  chatLoading.value = true
  try {
    const { data } = await api.get<ChatDomainStatus>(`/servers/${serverId}/chat-domain/status`)
    chatStatus.value = data
    if (data.domain && !chatDomain.value) chatDomain.value = data.domain
    chatVerifyOk.value = false
    chatVerifyMessage.value = ''
  } catch {
    chatStatus.value = null
  } finally {
    chatLoading.value = false
  }
}

async function verifyChatDomain() {
  const domain = chatDomain.value.trim()
  if (!domain) {
    message.warning('Укажите поддомен чата.')
    return
  }
  chatBusy.value = 'verify'
  chatVerifyOk.value = false
  chatVerifyMessage.value = ''
  try {
    const { data } = await api.post<{ ok: boolean; message: string }>(
      `/servers/${serverId}/chat-domain/verify`,
      { domain }
    )
    chatVerifyMessage.value = data.message
    chatVerifyOk.value = data.ok
    if (data.ok) message.success(data.message)
    else message.warning(data.message)
  } catch (error: any) {
    chatVerifyMessage.value = error?.response?.data?.detail || 'Проверка не удалась.'
    message.error(chatVerifyMessage.value)
  } finally {
    chatBusy.value = ''
  }
}

async function installChatDomain() {
  const domain = chatDomain.value.trim()
  if (!domain || !chatVerifyOk.value) return
  chatBusy.value = 'install'
  chatIsolation.value = []
  try {
    const { data } = await api.post<{ message: string; isolation: ChatIsolationCheck[] }>(
      `/servers/${serverId}/chat-domain/install`,
      { domain },
      { timeout: 660_000 }
    )
    chatIsolation.value = data.isolation
    message.success(data.message)
    await loadChatDomainStatus()
  } catch (error: any) {
    message.error(error?.response?.data?.detail || 'Подключение чат-домена не удалось.')
  } finally {
    chatBusy.value = ''
  }
}

function confirmChatDisable() {
  dialog.warning({
    title: 'Отключить чат-домен?',
    content: 'Mini-app перестанет открываться по домену чата. Сертификат сохранится, повторное включение быстрое.',
    positiveText: 'Отключить',
    negativeText: 'Отмена',
    onPositiveClick: async () => {
      chatBusy.value = 'disable'
      try {
        const { data } = await api.post(`/servers/${serverId}/chat-domain/disable`, {}, { timeout: 120_000 })
        message.success(data.message)
        chatIsolation.value = []
        await loadChatDomainStatus()
      } catch (error: any) {
        message.error(error?.response?.data?.detail || 'Не удалось отключить чат-домен.')
      } finally {
        chatBusy.value = ''
      }
    }
  })
}

async function loadHardenStatus() {
  hardenLoading.value = true
  try {
    const { data } = await api.get<PanelHardenStatus>(`/servers/${serverId}/panel-harden/status`)
    hardenStatus.value = data
    if (!hardenIpsText.value) {
      hardenIpsText.value = data.allowed_ips.length
        ? data.allowed_ips.join(', ')
        : data.your_ip || ''
    }
  } catch {
    hardenStatus.value = null
  } finally {
    hardenLoading.value = false
  }
}

function hardenIpsList(): string[] {
  return hardenIpsText.value
    .split(/[,\s]+/)
    .map((s) => s.trim())
    .filter(Boolean)
}

function confirmHardenApply() {
  const ips = hardenIpsList()
  const closing = ips.length === 0
  dialog.warning({
    title: closing ? 'Закрыть :8080 полностью?' : 'Ограничить :8080?',
    content: closing
      ? 'Аварийный вход останется только через SSH-туннель (ssh -L 8080:127.0.0.1:8080). ' +
        'Основной вход по HTTPS продолжит работать.'
      : `Доступ к :8080 останется только с: ${ips.join(', ')}. ` +
        'Основной вход по HTTPS продолжит работать для всех. ' +
        'При провале проверки после применения правила откатятся автоматически.',
    positiveText: closing ? 'Закрыть' : 'Ограничить',
    negativeText: 'Отмена',
    onPositiveClick: () => {
      void doHardenApply(ips, closing)
    }
  })
}

async function doHardenApply(ips: string[], force: boolean) {
  hardenBusy.value = 'apply'
  try {
    const { data } = await api.post(
      `/servers/${serverId}/panel-harden/apply`,
      { allowed_ips: ips, force },
      { timeout: 180_000 }
    )
    message.success(data.message)
    await loadHardenStatus()
    pickOpenStep()
  } catch (error: any) {
    const detail = error?.response?.data?.detail || 'Не удалось применить ограничение.'
    if (!force && error?.response?.status === 400 && /подтвердите|не входит/i.test(detail)) {
      dialog.warning({
        title: 'Нужно подтверждение',
        content: detail,
        positiveText: 'Всё равно применить',
        negativeText: 'Отмена',
        onPositiveClick: () => {
          void doHardenApply(ips, true)
        }
      })
    } else {
      message.error(detail)
    }
  } finally {
    hardenBusy.value = ''
  }
}

function confirmHardenDisable() {
  dialog.warning({
    title: 'Снять ограничение :8080?',
    content: 'Порт :8080 снова станет доступен из интернета для всех. Делайте это только на время отладки.',
    positiveText: 'Снять',
    negativeText: 'Отмена',
    onPositiveClick: async () => {
      hardenBusy.value = 'disable'
      try {
        const { data } = await api.post(`/servers/${serverId}/panel-harden/disable`, {}, { timeout: 120_000 })
        message.success(data.message)
        await loadHardenStatus()
      } catch (error: any) {
        message.error(error?.response?.data?.detail || 'Не удалось снять ограничение.')
      } finally {
        hardenBusy.value = ''
      }
    }
  })
}

async function verifySsl() {
  const domain = sslDomain.value.trim()
  if (!domain) {
    message.warning('Укажи домен.')
    return
  }
  sslBusy.value = 'verify'
  sslVerifyOk.value = false
  sslVerifyMessage.value = ''
  try {
    const { data } = await api.post(`/servers/${serverId}/panel-ssl/verify`, { domain })
    sslVerifyMessage.value = data.message
    sslVerifyOk.value = data.ok
    if (!data.ok) message.warning(data.message)
    else message.success(data.message)
  } catch (error: any) {
    sslVerifyMessage.value = error?.response?.data?.detail || 'Проверка не удалась.'
    message.error(sslVerifyMessage.value)
  } finally {
    sslBusy.value = ''
  }
}

async function installSsl() {
  const domain = sslDomain.value.trim()
  if (!domain || !sslVerifyOk.value) return
  sslBusy.value = 'install'
  try {
    const { data } = await api.post(
      `/servers/${serverId}/panel-ssl/install`,
      { domain, email: sslEmail.value.trim() || null },
      { timeout: 660_000 }
    )
    message.success(data.message)
    await Promise.all([loadSslStatus(), loadOverview(), loadHardenStatus()])
  } catch (error: any) {
    message.error(error?.response?.data?.detail || 'Установка HTTPS не удалась.')
  } finally {
    sslBusy.value = ''
  }
}

function confirmSslRollback() {
  dialog.warning({
    title: 'Откатить nginx?',
    content: 'Конфигурация nginx вернётся из бэкапа. HTTPS может перестать работать.',
    positiveText: 'Откатить',
    negativeText: 'Отмена',
    onPositiveClick: async () => {
      sslBusy.value = 'rollback'
      try {
        const { data } = await api.post(`/servers/${serverId}/panel-ssl/rollback`)
        message.success(data.message)
        await Promise.all([loadSslStatus(), loadOverview()])
      } catch (error: any) {
        message.error(error?.response?.data?.detail || 'Откат не удался.')
      } finally {
        sslBusy.value = ''
      }
    }
  })
}

const exitOptions = computed(() =>
  otherServers.value.map((s) => ({ label: `${s.name} · ${s.host}`, value: s.id }))
)

const selectedExitName = computed(() => {
  if (!cascadeExitId.value) return ''
  return otherServers.value.find((s) => s.id === cascadeExitId.value)?.name || ''
})

const cascadeExitLabel = computed(
  () => selectedExitName.value || cascadeLink.value?.exit_name || 'Выходной сервер'
)

const cascadeExitHost = computed(() => {
  const id = cascadeExitId.value || cascadeLink.value?.exit_server_id
  if (id) {
    const rec = otherServers.value.find((s) => s.id === id)
    if (rec?.host) return rec.host
  }
  return cascadeResult.value?.exit_public_ip || ''
})

const cascadeStateLabel = computed(() => labelCascadeState(cascadeLink.value?.state || 'none'))

const cascadeStateTone = computed(() => toneCascadeState(cascadeLink.value?.state || 'none'))

async function loadOtherServers() {
  cascadeServersLoading.value = true
  try {
    const { data } = await api.get<ServerRead[]>('/servers')
    otherServers.value = data.filter((s) => s.id !== serverId)
  } catch {
    otherServers.value = []
  } finally {
    cascadeServersLoading.value = false
  }
}

async function loadCascadeStatus() {
  try {
    const { data } = await api.get<CascadeLinkStatus>(`/servers/${serverId}/cascade/status`)
    cascadeLink.value = data
    if (data.exit_server_id && !cascadeExitId.value) cascadeExitId.value = data.exit_server_id
  } catch {
    cascadeLink.value = null
  }
}

async function runPreflight() {
  if (!cascadeExitId.value) return
  cascadeBusy.value = true
  cascadeResult.value = null
  try {
    const { data } = await api.post<CascadePreflightResult>(
      `/servers/${serverId}/cascade/preflight`,
      { exit_server_id: cascadeExitId.value },
      { timeout: 120_000 }
    )
    cascadeResult.value = data
    await loadCascadeStatus()
    if (cascadeActive.value || data.live_active) {
      message.success('Каскад работает — всё в порядке.')
    } else if (data.ok) {
      message.success('Серверы готовы — можно включать каскад.')
    } else {
      message.warning('Есть проблемы — см. раздел «Настройка связи».')
    }
  } catch (error: any) {
    message.error(error?.response?.data?.detail || 'Проверка не удалась.')
  } finally {
    cascadeBusy.value = false
  }
}

const cascadeActive = computed(
  () => Boolean(cascadeLink.value?.live_active) || cascadeLink.value?.state === 'active'
)

const setupBannerText = computed(() => {
  if (cascadeActive.value) return 'Каскад работает — связь в порядке.'
  if (!cascadeResult.value) return ''
  if (cascadeResult.value.message) return cascadeResult.value.message
  return cascadeResult.value.ok
    ? 'Серверы готовы — можно включать каскад.'
    : 'Есть проблемы — каскад включить нельзя.'
})

const setupBannerTone = computed(() => {
  if (cascadeActive.value) return 'ok'
  if (!cascadeResult.value) return 'ok'
  return cascadeResult.value.ok ? 'ok' : 'warn'
})

const setupExitIp = computed(
  () => cascadeResult.value?.exit_public_ip || cascadeLink.value?.egress_ip || ''
)

const canApplyCascade = computed(() => {
  if (cascadeActive.value) return false
  const s = cascadeLink.value?.state
  return s === 'preflight_ok' || s === 'rolled_back' || (cascadeResult.value?.ok ?? false)
})

function runApply() {
  dialog.warning({
    title: 'Включить каскад?',
    content:
      'Трафик пойдёт через выходной сервер. Если на выходном сервере ещё нет AmneziaWG — ' +
      'панель установит его сама, это может занять несколько минут. ' +
      'Телефон может на несколько секунд переподключиться. Если что-то пойдёт не так — настройка откатится сама.',
    positiveText: 'Включить каскад',
    negativeText: 'Отмена',
    // Закрываем диалог сразу и показываем прогресс прямо на странице — иначе
    // окно подтверждения «висит» поверх спиннера, и кажется, что всё зависло.
    onPositiveClick: () => {
      void doApplyCascade()
      return true
    }
  })
}

async function doApplyCascade() {
  cascadeApplyBusy.value = 'apply'
  cascadeApplyResult.value = null
  try {
    const { data } = await api.post<CascadeApplyResult>(
      `/servers/${serverId}/cascade/apply`,
      {},
      { timeout: 600_000 }
    )
    cascadeApplyResult.value = data
    if (data.ok) message.success(data.message)
    else message.error(data.message)
    await loadCascadeStatus()
  } catch (error: any) {
    message.error(error?.response?.data?.detail || 'Не удалось включить каскад.')
  } finally {
    cascadeApplyBusy.value = ''
  }
}

function runRollback() {
  dialog.warning({
    title: 'Выключить каскад?',
    content: 'Интернет снова увидит IP этого сервера. Второй сервер в цепочке использоваться не будет.',
    positiveText: 'Выключить',
    negativeText: 'Отмена',
    onPositiveClick: async () => {
      cascadeApplyBusy.value = 'rollback'
      try {
        const { data } = await api.post<CascadeApplyResult>(
          `/servers/${serverId}/cascade/rollback`,
          {},
          { timeout: 180_000 }
        )
        cascadeApplyResult.value = data
        if (data.ok) message.success('Каскад выключен.')
        else message.error(data.message)
        await loadCascadeStatus()
      } catch (error: any) {
        message.error(error?.response?.data?.detail || 'Не удалось выключить каскад.')
      } finally {
        cascadeApplyBusy.value = ''
      }
    }
  })
}

watch(activeTab, (tab) => {
  if (tab === 'security' && server.value) {
    void loadSecurityTab()
  }
  if (tab === 'cascade' && server.value) {
    if (!otherServers.value.length) void loadOtherServers()
    void loadCascadeStatus()
  }
  if (tab === 'rules' && server.value) void loadRulesStatus()
  if (tab === 'protocols' && server.value) void loadProtocolVersions()
})

const cpuText = computed(() => {
  const v = metrics.value?.cpu_percent
  return v == null ? '—' : `${Math.round(v)}%`
})
const memPercent = computed(() => percentOf(metrics.value?.mem_used_bytes, metrics.value?.mem_total_bytes))
const memText = computed(() => {
  const m = metrics.value
  if (!m?.mem_total_bytes) return '—'
  return `${formatBytes(m.mem_used_bytes)} / ${formatBytes(m.mem_total_bytes)}`
})
const diskPercent = computed(() => percentOf(metrics.value?.disk_used_bytes, metrics.value?.disk_total_bytes))
const diskText = computed(() => {
  const m = metrics.value
  if (!m?.disk_total_bytes) return '—'
  return `${formatBytes(m.disk_used_bytes)} / ${formatBytes(m.disk_total_bytes)}`
})

const cpuModelText = computed(() => {
  const s = overview.value?.system
  if (!s?.cpu_model) return s?.cores ? `${s.cores} ядер` : '—'
  return s.cores ? `${s.cpu_model} · ${s.cores} ядер` : s.cpu_model
})

const createdText = computed(() => {
  const raw = server.value?.created_at
  if (!raw) return '—'
  const date = new Date(raw)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleDateString('ru-RU')
})

const securityBusy = ref<string>('')

function toggleSecurity(check: SecurityCheck) {
  if (!check.control) return
  const control = check.control
  const turningOn = !check.enabled
  if (control === 'ufw') {
    if (turningOn) void confirmUfwEnable()
    else confirmSecurityDisable(control, 'Выключить файрвол UFW?', 'Фильтрация портов на уровне ОС будет снята. VPN и панель продолжат работать.')
    return
  }
  if (control === 'fail2ban') {
    if (turningOn) void doSecurityAction(control, 'enable')
    else confirmSecurityDisable(control, 'Выключить Fail2ban?', 'Защита SSH от перебора паролей перестанет работать.')
    return
  }
  if (control === 'updates') {
    if (turningOn) void doSecurityAction(control, 'enable')
    else confirmSecurityDisable(control, 'Выключить автообновления?', 'Сервер перестанет автоматически получать патчи безопасности.')
    return
  }
}

async function confirmUfwEnable() {
  let preview: UfwPreview | null = null
  securityBusy.value = 'ufw'
  try {
    const { data } = await api.get<UfwPreview>(`/servers/${serverId}/security/ufw-preview`)
    preview = data
  } catch (error: any) {
    message.error(error?.response?.data?.detail || 'Не удалось получить список портов.')
    securityBusy.value = ''
    return
  }
  securityBusy.value = ''
  const tcp = preview.tcp_ports.join(', ') || '—'
  const udp = preview.udp_ports.join(', ') || '—'
  dialog.info({
    title: 'Включить файрвол UFW?',
    content: () =>
      h('div', { style: 'display:flex;flex-direction:column;gap:8px' }, [
        h('p', { style: 'margin:0' }, 'Панель сама откроет порты всех работающих сервисов, чтобы ничего не сломалось:'),
        h('p', { style: 'margin:0' }, [h('strong', 'TCP: '), `${tcp}`]),
        h('p', { style: 'margin:0' }, [h('strong', 'UDP: '), `${udp}`]),
        h(
          'p',
          { style: 'margin:0;color:var(--color-text-soft,#888)' },
          `SSH-порт ${preview.ssh_port} открывается в первую очередь. Если связь не восстановится — файрвол сам выключится через 2 минуты.`
        )
      ]),
    positiveText: 'Включить',
    negativeText: 'Отмена',
    onPositiveClick: () => {
      void doSecurityAction('ufw', 'enable')
    }
  })
}

function confirmSecurityDisable(control: string, title: string, content: string) {
  dialog.warning({
    title,
    content,
    positiveText: 'Выключить',
    negativeText: 'Отмена',
    onPositiveClick: () => {
      void doSecurityAction(control, 'disable')
    }
  })
}

async function doSecurityAction(control: string, action: 'enable' | 'disable') {
  securityBusy.value = control
  try {
    const { data } = await api.post<{ ok: boolean; message: string }>(
      `/servers/${serverId}/security/action`,
      { control, action },
      { timeout: 180_000 }
    )
    message.success(data.message)
    await loadOverview()
  } catch (error: any) {
    message.error(error?.response?.data?.detail || 'Не удалось применить настройку безопасности.')
  } finally {
    securityBusy.value = ''
  }
}

const securityWarnings = computed(
  () => (overview.value?.security || []).filter((c) => c.status === 'warning' || c.status === 'danger').length
)
const securityOkCount = computed(
  () => (overview.value?.security || []).filter((c) => c.status === 'ok').length
)

function checkTone(status: SecurityCheck['status']) {
  if (status === 'ok') return 'ok'
  if (status === 'danger') return 'danger'
  if (status === 'warning') return 'warning'
  return 'neutral'
}

function protoStatusLabel(proto: ProtocolInfo) {
  if (!proto.installed) return 'не установлен'
  return proto.running ? 'работает' : 'остановлен'
}

function protoStatusTone(proto: ProtocolInfo) {
  if (!proto.installed) return 'neutral'
  return proto.running ? 'ok' : 'warning'
}

function shortPorts(ports: string) {
  const first = ports.split(',')[0]?.trim() || ports
  return first.replace('0.0.0.0:', '').replace('[::]:', '')
}

function isProtoBusy(id: string, action: string) {
  return protoBusy[id] === action
}

async function protoAction(proto: ProtocolInfo, action: 'start' | 'stop' | 'restart' | 'remove') {
  protoBusy[proto.id] = action
  try {
    const { data } = await api.post(`/servers/${serverId}/protocols/${proto.id}/action`, { action })
    message.success(data.message || 'Готово.')
    await loadOverview()
  } catch (error: any) {
    message.error(error?.response?.data?.detail || 'Не удалось выполнить действие.')
  } finally {
    delete protoBusy[proto.id]
  }
}

const SNAPSHOT_PROTOCOLS = ['awg2', 'awg_legacy', 'xray']
const UPDATE_PROTOCOLS = ['awg2', 'awg_legacy', 'xray']

function canSnapshot(proto: ProtocolInfo) {
  return proto.installed && SNAPSHOT_PROTOCOLS.includes(proto.id)
}

function canUpdate(proto: ProtocolInfo) {
  return (
    proto.installed &&
    UPDATE_PROTOCOLS.includes(proto.id) &&
    protocolVersions.value[proto.id]?.up_to_date === false
  )
}

function confirmUpdateProtocol(proto: ProtocolInfo) {
  const v = protocolVersions.value[proto.id]
  dialog.warning({
    title: `Обновить ${proto.name} до ${v?.pinned_version || 'новой версии'}?`,
    content:
      'Будет снят снимок конфига и ключей, пересобран образ pinned-версии и пересоздан контейнер. ' +
      'Возможен короткий разрыв соединения у клиентов. При любом сбое — авто-откат на текущую версию. ' +
      'Ключи и параметры маскировки сохраняются, переподключать клиентов не нужно.',
    positiveText: 'Обновить',
    negativeText: 'Отмена',
    onPositiveClick: () => updateProtocol(proto)
  })
}

async function updateProtocol(proto: ProtocolInfo) {
  protoBusy[proto.id] = 'update'
  try {
    const { data } = await api.post(
      `/servers/${serverId}/protocols/${proto.id}/update`,
      {},
      { timeout: 900_000 }
    )
    message.success(data.message || 'Протокол обновлён.')
    await Promise.all([loadOverview(), loadProtocolVersions()])
  } catch (error: any) {
    const detail = error?.response?.data?.detail
    if (detail && typeof detail === 'object') {
      message.error(detail.message || 'Не удалось обновить протокол.')
    } else {
      message.error(detail || 'Не удалось обновить протокол.')
    }
  } finally {
    delete protoBusy[proto.id]
  }
}

const xrayMaskingVisible = ref(false)
const xrayMaskingLoading = ref(false)
const xrayMaskingBusy = ref(false)
const xrayMaskingData = ref<any>(null)
const xrayMaskingDomain = ref('')

const maskingStatusTone = computed<'ok' | 'warning' | 'danger' | 'neutral'>(() => {
  const s = xrayMaskingData.value?.status
  if (s === 'strong') return 'ok'
  if (s === 'basic') return 'warning'
  if (s === 'weak' || s === 'invalid') return 'danger'
  return 'neutral'
})

async function loadXrayMasking() {
  xrayMaskingLoading.value = true
  try {
    const { data } = await api.get(`/servers/${serverId}/xray/masking`)
    xrayMaskingData.value = data
    xrayMaskingDomain.value = data.sni || data.dest_host || ''
  } catch (error: any) {
    message.error(error?.response?.data?.detail || 'Не удалось получить статус маскировки.')
  } finally {
    xrayMaskingLoading.value = false
  }
}

async function showXrayMasking(proto: ProtocolInfo) {
  protoBusy[proto.id] = 'masking'
  try {
    await loadXrayMasking()
    xrayMaskingVisible.value = true
  } finally {
    delete protoBusy[proto.id]
  }
}

function applyMaskingDomain() {
  const site = (xrayMaskingDomain.value || '').trim()
  if (!site) {
    message.warning('Укажи домен маскировки.')
    return
  }
  dialog.warning({
    title: `Сменить домен маскировки на ${site}?`,
    content:
      'Будет снят снимок конфига, изменён dest/SNI и горячо перечитан Xray. ' +
      'Все Xray-клиенты будут переизданы (им нужно заново импортировать конфиг). ' +
      'При сбое — авто-откат на текущие настройки.',
    positiveText: 'Сменить',
    negativeText: 'Отмена',
    onPositiveClick: async () => {
      xrayMaskingBusy.value = true
      try {
        const { data } = await api.post(
          `/servers/${serverId}/xray/masking/domain`,
          { site },
          { timeout: 120_000 }
        )
        message.success(data.message || 'Домен маскировки изменён.')
        await loadXrayMasking()
      } catch (error: any) {
        message.error(error?.response?.data?.detail || 'Не удалось сменить домен.')
      } finally {
        xrayMaskingBusy.value = false
      }
    }
  })
}

async function addShortIdAction() {
  xrayMaskingBusy.value = true
  try {
    const { data } = await api.post(`/servers/${serverId}/xray/masking/short-id`, {}, { timeout: 60_000 })
    message.success(data.message || 'shortId добавлен.')
    await loadXrayMasking()
  } catch (error: any) {
    message.error(error?.response?.data?.detail || 'Не удалось добавить shortId.')
  } finally {
    xrayMaskingBusy.value = false
  }
}

async function snapshotProtocol(proto: ProtocolInfo) {
  protoBusy[proto.id] = 'snapshot'
  try {
    const { data } = await api.post(`/servers/${serverId}/protocols/${proto.id}/snapshot`)
    const kb = Math.max(1, Math.round((data?.snapshot?.size_bytes || 0) / 1024))
    message.success(`Снимок конфига и ключей создан (~${kb} КБ).`)
  } catch (error: any) {
    message.error(error?.response?.data?.detail || 'Не удалось снять снапшот.')
  } finally {
    delete protoBusy[proto.id]
  }
}

function confirmProtoStop(proto: ProtocolInfo) {
  dialog.warning({
    title: `Остановить ${proto.name}?`,
    content: proto.managed
      ? 'Все клиенты этого протокола потеряют соединение, пока контейнер остановлен.'
      : 'Контейнер протокола будет остановлен.',
    positiveText: 'Остановить',
    negativeText: 'Отмена',
    onPositiveClick: () => protoAction(proto, 'stop')
  })
}

function confirmProtoRemove(proto: ProtocolInfo) {
  dialog.error({
    title: `Удалить ${proto.name} с сервера?`,
    content: proto.managed
      ? `Контейнер ${proto.container} будет удалён. Все ${proto.clients_count} клиентов панели перестанут работать. Конфиги на диске сервера останутся.`
      : `Контейнер ${proto.container} будет удалён с сервера. Конфиги на диске останутся.`,
    positiveText: 'Удалить',
    negativeText: 'Отмена',
    onPositiveClick: () => protoAction(proto, 'remove')
  })
}

function isContainerBusy(name: string) {
  return !!containerBusy[name]
}

async function containerAction(name: string, action: 'start' | 'stop' | 'restart') {
  containerBusy[name] = true
  try {
    const { data } = await api.post(`/servers/${serverId}/containers/${name}/action`, { action })
    message.success(data.message || 'Готово.')
    await loadOverview()
  } catch (error: any) {
    message.error(error?.response?.data?.detail || 'Не удалось выполнить действие.')
  } finally {
    containerBusy[name] = false
  }
}

function confirmContainerStop(name: string) {
  dialog.warning({
    title: `Остановить ${name}?`,
    content: 'Сервис внутри контейнера станет недоступен до запуска.',
    positiveText: 'Остановить',
    negativeText: 'Отмена',
    onPositiveClick: () => containerAction(name, 'stop')
  })
}

async function openLogs(name: string) {
  logsContainer.value = name
  logsVisible.value = true
  logsLoading.value = true
  logsText.value = ''
  try {
    const { data } = await api.get(`/servers/${serverId}/containers/${name}/logs`, {
      params: { tail: 200 }
    })
    logsText.value = data.logs
  } catch (error: any) {
    logsText.value = error?.response?.data?.detail || 'Не удалось получить логи.'
  } finally {
    logsLoading.value = false
  }
}

function openInstall(proto: ProtocolInfo) {
  installProtocol.value = proto
  installVisible.value = true
}

async function onProtocolInstalled() {
  await loadOverview()
}

function confirmDeleteServer() {
  if (!server.value) return
  dialog.warning({
    title: 'Удалить сервер из панели?',
    content: `«${server.value.name}» будет удалён только из панели. На самом VPS ничего не изменится.`,
    positiveText: 'Удалить',
    negativeText: 'Отмена',
    onPositiveClick: async () => {
      await api.delete(`/servers/${serverId}`)
      message.success('Сервер удалён из панели.')
      router.push({ name: 'servers' })
    }
  })
}
</script>

<style scoped>
.placeholder {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 22px;
  color: var(--color-muted);
  font-size: 13px;
}

.detail-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
}

.head-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.delete-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 12px;
  border: 1px solid var(--color-border);
  border-radius: 9px;
  background: transparent;
  color: var(--color-muted);
  font-size: 13px;
  cursor: pointer;
  transition:
    color 0.15s ease,
    border-color 0.15s ease;
}

.delete-btn:hover {
  color: var(--color-danger);
  border-color: var(--color-danger);
}

/* hero */
.server-hero {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 14px;
  padding: 16px 18px;
  margin-bottom: 14px;
}

.hero-id {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.hero-text h2 {
  margin: 0;
  font-size: 17px;
}

.hero-text .mono {
  color: var(--color-muted);
  font-size: 12px;
}

.hero-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  color: var(--color-muted);
  font-size: 12px;
}

.hero-stat {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

.hero-cascade {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 4px 10px 4px 8px;
  border: 1px solid var(--color-border);
  border-radius: 999px;
  background: #14181a;
  color: var(--color-muted);
  font-size: 12px;
  cursor: pointer;
  transition:
    border-color 0.15s ease,
    color 0.15s ease;
}

.hero-cascade:hover {
  border-color: #3a423e;
  color: var(--color-text);
}

.hero-cascade--active {
  border-color: #2a4a3a;
  color: var(--color-accent);
}

.rules-placeholder {
  display: grid;
  justify-items: center;
  gap: 8px;
  margin-top: 16px;
  padding: 28px 16px;
  border: 1px dashed var(--color-border);
  border-radius: 10px;
  color: var(--color-muted);
  text-align: center;
}

.rules-placeholder svg {
  color: var(--color-dim);
}

.rules-placeholder strong {
  color: var(--color-text);
  font-size: 14px;
}

.rules-placeholder span {
  max-width: 360px;
  font-size: 13px;
  line-height: 1.45;
}

/* split-routing */
.split-flow {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  margin: 14px 0;
}

.split-leg {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 14px;
  border: 1px solid var(--color-border);
  border-radius: 10px;
  background: var(--color-surface-2, rgba(255, 255, 255, 0.02));
}

.split-leg svg {
  flex-shrink: 0;
}

.split-leg.direct {
  border-color: #2a4a3a;
}

.split-leg.direct svg {
  color: var(--color-accent);
}

.split-leg.cascade svg {
  color: #6aa3ff;
}

.split-leg div {
  display: grid;
  gap: 2px;
  min-width: 0;
}

.split-leg strong {
  font-size: 13px;
}

.split-leg .mono {
  font-size: 12px;
  color: var(--color-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.split-toggle-row {
  display: flex;
  align-items: flex-start;
  gap: 14px;
  padding: 12px 0 2px;
}

.split-toggle-text {
  display: grid;
  gap: 3px;
}

.split-toggle-text strong {
  font-size: 14px;
}

.source-list {
  display: grid;
  gap: 8px;
  margin: 12px 0;
}

.source-row {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 10px 12px;
  border: 1px solid var(--color-border);
  border-radius: 10px;
  cursor: pointer;
}

.source-row:hover {
  border-color: var(--color-accent);
}

.source-text {
  display: grid;
  gap: 2px;
}

.source-text strong {
  font-size: 13px;
}

.custom-cidr {
  display: grid;
  gap: 6px;
  margin-top: 8px;
}

@media (max-width: 640px) {
  .split-flow {
    grid-template-columns: 1fr;
  }
}

/* rules tab */
.rules-page {
  display: grid;
  gap: 12px;
}

.rules-hero--on {
  border-color: #2a4a3a;
  background: linear-gradient(135deg, rgba(42, 74, 58, 0.1) 0%, transparent 55%);
}

.rules-hero-top {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
}

.rules-hero-top h3 {
  margin: 0 0 6px;
}

.rules-lead {
  margin: 0;
  max-width: 520px;
  color: var(--color-muted);
  font-size: 13px;
  line-height: 1.5;
}

.rules-hint {
  margin: 0;
  font-size: 12px;
  color: var(--color-muted);
  line-height: 1.45;
}

.rules-hint.subtle {
  opacity: 0.75;
}

.rules-examples {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  margin-bottom: 14px;
}

.rules-example {
  display: flex;
  gap: 10px;
  padding: 12px;
  border-radius: 10px;
  border: 1px solid var(--color-border);
  background: var(--color-bg-subtle, rgba(255, 255, 255, 0.03));
}

.rules-example svg {
  flex-shrink: 0;
  margin-top: 2px;
}

.rules-example--ru {
  border-color: #2a4a3a;
}

.rules-example--ru svg {
  color: var(--color-accent);
}

.rules-example--abroad svg {
  color: #6aa3ff;
}

.rules-example div {
  display: grid;
  gap: 3px;
  min-width: 0;
}

.rules-example strong {
  font-size: 13px;
}

.rules-example span {
  font-size: 11px;
  color: var(--color-dim);
  line-height: 1.35;
}

.rules-route {
  font-size: 12px !important;
  color: var(--color-muted) !important;
  margin-top: 2px;
}

.rules-check-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}

.rules-check-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border-radius: 8px;
  font-size: 12px;
  border: 1px solid var(--color-border);
}

.rules-check-item.ok {
  border-color: #2a4a3a;
  background: rgba(42, 74, 58, 0.1);
  color: var(--color-text);
}

.rules-check-item.ok svg {
  color: var(--color-accent);
}

.rules-check-item.bad {
  border-color: var(--color-danger, #e5484d);
  background: rgba(229, 72, 77, 0.08);
}

.rules-setup {
  padding: 0;
}

.rules-setup-summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 14px 16px;
  cursor: pointer;
  font-size: 14px;
  font-weight: 600;
  list-style: none;
  user-select: none;
}

.rules-setup-summary::-webkit-details-marker {
  display: none;
}

.rules-setup-summary::before {
  content: '▸';
  margin-right: 8px;
  font-size: 12px;
  color: var(--color-dim);
  transition: transform 0.15s;
}

.rules-setup[open] .rules-setup-summary::before {
  transform: rotate(90deg);
}

.rules-setup-meta {
  font-size: 12px;
  font-weight: 400;
  color: var(--color-dim);
}

.rules-setup-body {
  padding: 0 16px 16px;
  border-top: 1px solid var(--color-border);
}

@media (max-width: 640px) {
  .rules-examples,
  .rules-check-grid {
    grid-template-columns: 1fr;
  }
}

/* tabs */
.tabs {
  display: flex;
  gap: 6px;
  margin-bottom: 14px;
  flex-wrap: wrap;
}

.tab {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 8px 14px;
  border: 1px solid var(--color-border);
  border-radius: 10px;
  background: transparent;
  color: var(--color-muted);
  font-size: 13px;
  cursor: pointer;
  transition:
    color 0.15s ease,
    border-color 0.15s ease,
    background-color 0.15s ease;
}

.tab:hover {
  color: var(--color-text);
}

.tab.active {
  color: var(--color-accent);
  border-color: var(--color-accent);
  background: rgba(99, 226, 161, 0.06);
}

.tab-pill {
  min-width: 18px;
  height: 18px;
  display: grid;
  place-items: center;
  padding: 0 5px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
}

.tab-pill.warn {
  background: rgba(240, 177, 90, 0.15);
  color: var(--color-warning);
}

.tab-body {
  display: grid;
  gap: 14px;
}

/* overview */
.overview-grid {
  display: grid;
  grid-template-columns: minmax(280px, 1fr) minmax(320px, 1.2fr);
  gap: 14px;
  align-items: start;
}

.block {
  padding: 16px 18px;
}

.block h3 {
  margin: 0 0 14px;
  font-size: 14px;
}

.load-metrics {
  display: grid;
  gap: 12px;
}

.muted-state {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--color-danger);
  font-size: 13px;
}

.kv {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px 18px;
  margin: 0;
}

.kv dt {
  color: var(--color-dim);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  margin-bottom: 3px;
}

.kv dd {
  margin: 0;
  font-size: 13px;
  word-break: break-word;
}

/* protocols */
.protocol-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(310px, 1fr));
  gap: 14px;
}

.proto-card {
  display: grid;
  gap: 10px;
  padding: 15px 16px;
  align-content: start;
}

.proto-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.proto-title {
  display: flex;
  align-items: center;
  gap: 9px;
  min-width: 0;
}

.proto-title strong {
  font-size: 14px;
}

.proto-icon {
  width: 28px;
  height: 28px;
  display: grid;
  place-items: center;
  border-radius: 8px;
  background: #1f2428;
  border: 1px solid var(--color-border);
  color: var(--color-accent);
  flex-shrink: 0;
}

.proto-desc {
  margin: 0;
  color: var(--color-muted);
  font-size: 12px;
  line-height: 1.5;
  min-height: 36px;
}

.proto-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  min-height: 22px;
}

.meta-chip {
  padding: 2px 8px;
  border: 1px solid var(--color-border);
  border-radius: 999px;
  color: var(--color-muted);
  font-size: 11px;
}

.meta-chip.accent {
  color: var(--color-accent);
}

.meta-chip.ok {
  color: #16a34a;
  border-color: rgba(22, 163, 74, 0.4);
}

.meta-chip.warn {
  color: #d97706;
  border-color: rgba(217, 119, 6, 0.45);
}

.masking-modal {
  width: min(560px, 92vw);
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.masking-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.masking-loading {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--color-muted);
}

.masking-grid {
  display: grid;
  grid-template-columns: 120px 1fr;
  gap: 4px 10px;
  align-items: baseline;
}

.mk-label {
  color: var(--color-muted);
  font-size: 13px;
}

.mk-val {
  word-break: break-all;
}

.masking-grid .mono {
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: 13px;
}

.masking-issues {
  margin: 0;
  padding-left: 4px;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 3px;
  font-size: 13px;
}

.masking-issues.critical li {
  color: #dc2626;
}

.masking-issues.notes li {
  color: #d97706;
}

.masking-actions {
  display: flex;
  flex-direction: column;
  gap: 8px;
  border-top: 1px solid var(--color-border);
  padding-top: 12px;
}

.mk-field-label {
  font-size: 13px;
  color: var(--color-muted);
}

.mk-hint {
  font-size: 12px;
  color: var(--color-muted);
}

.mk-buttons {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 4px;
}

.mk-disclaimer {
  margin: 0;
  font-size: 12px;
  color: var(--color-muted);
}

.proto-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  padding-top: 10px;
  border-top: 1px solid var(--color-border);
}

/* security */
.security-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
}

.security-head h3 {
  margin: 0;
}

.sec-sub {
  margin: -6px 0 14px;
  font-size: 13px;
  color: var(--color-muted);
}

.sec-steps {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.sec-step {
  border: 1px solid var(--color-border);
  border-radius: 10px;
  overflow: hidden;
}

.sec-step-head {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 12px 14px;
  background: none;
  border: 0;
  cursor: pointer;
  color: inherit;
  font: inherit;
  text-align: left;
}

.sec-step-head:hover {
  background: rgba(255, 255, 255, 0.03);
}

.sec-step.locked .sec-step-head {
  cursor: not-allowed;
}

.sec-step.locked .sec-step-name,
.sec-step.locked .sec-mark {
  opacity: 0.5;
}

.sec-mark {
  flex: none;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 600;
  border: 1px solid var(--color-border);
  color: var(--color-muted);
}

.sec-mark.done {
  border-color: var(--color-accent);
  color: var(--color-accent);
}

.sec-step-name {
  font-weight: 600;
  font-size: 14px;
}

.sec-step-note {
  margin-left: auto;
  font-size: 12.5px;
  color: var(--color-muted);
  max-width: 45%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sec-step-note.ok {
  color: var(--color-accent);
}

.sec-step-note.warn {
  color: var(--color-warning);
}

.sec-chev {
  flex: none;
  color: var(--color-muted);
  transition: transform 0.15s ease;
}

.sec-step.open .sec-chev,
.sec-chev.open {
  transform: rotate(180deg);
}

.sec-step-body {
  padding: 2px 14px 14px 46px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.sec-howto {
  margin: 0;
  padding-left: 18px;
  font-size: 13px;
  color: var(--color-muted);
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.sec-row {
  display: flex;
  gap: 8px;
  align-items: center;
  max-width: 480px;
}

.sec-row :deep(.n-input) {
  flex: 1;
}

.sec-note {
  margin: 0;
  font-size: 13px;
  color: var(--color-muted);
}

.sec-note.sec-warn {
  color: var(--color-warning);
}

.ssl-nodomain {
  display: grid;
  gap: 8px;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px dashed var(--color-border);
  max-width: 480px;
}

.ssl-nodomain-or {
  font-size: 12px;
  color: var(--color-dim);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.sec-audit-head {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  background: none;
  border: 0;
  padding: 0;
  cursor: pointer;
  color: inherit;
  font: inherit;
  text-align: left;
}

.sec-audit-head h3 {
  margin: 0;
  font-size: 15px;
  flex: 1;
}

.security-score {
  font-size: 12px;
  padding: 3px 10px;
  border-radius: 999px;
  border: 1px solid var(--color-border);
}

.security-score.ok {
  color: var(--color-accent);
}

.security-score.warn {
  color: var(--color-warning);
}

.security-list {
  display: grid;
  gap: 4px;
}

.security-row {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 11px 6px;
  border-bottom: 1px solid var(--color-border);
}

.security-row:last-child {
  border-bottom: none;
}

.check-icon {
  width: 30px;
  height: 30px;
  display: grid;
  place-items: center;
  border-radius: 8px;
  border: 1px solid var(--color-border);
  flex-shrink: 0;
}

.check-ok {
  color: var(--color-accent);
}

.check-warning,
.check-unknown {
  color: var(--color-warning);
}

.check-danger {
  color: var(--color-danger);
}

.ssl-panel {
  margin-top: 14px;
}

.ssl-hint {
  margin: 0 0 14px;
  color: var(--color-muted);
  font-size: 13px;
  line-height: 1.5;
}

.ssl-loading {
  display: flex;
  align-items: center;
  gap: 10px;
  color: var(--color-muted);
  font-size: 13px;
}

.ssl-active {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 14px;
  padding: 10px 12px;
  border-radius: 10px;
  border: 1px solid var(--color-border);
  background: rgba(99, 226, 161, 0.04);
}

.ssl-link {
  color: var(--color-accent);
  text-decoration: none;
}

.ssl-link:hover {
  text-decoration: underline;
}

.ssl-expiry {
  color: var(--color-muted);
  font-size: 12px;
}

.ssl-form {
  display: grid;
  gap: 12px;
  margin-bottom: 12px;
}

.ssl-field {
  display: grid;
  gap: 6px;
  font-size: 12px;
  color: var(--color-muted);
}

.ssl-verify {
  margin: 0 0 12px;
  font-size: 13px;
}

.ssl-verify.ok {
  color: var(--color-accent);
}

.ssl-verify.warn {
  color: var(--color-warning);
}

.ssl-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.ssl-msg {
  margin: 12px 0 0;
  font-size: 13px;
}

.ssl-msg.warn {
  color: var(--color-warning);
}

.ssl-hint.subtle {
  opacity: 0.7;
  font-size: 12px;
}

/* cascade tab */
.cascade-page {
  display: grid;
  gap: 12px;
}

.cascade-hero {
  overflow: hidden;
}

.cascade-hero--on {
  border-color: #2a4a3a;
  background: linear-gradient(135deg, rgba(42, 74, 58, 0.12) 0%, transparent 55%);
}

.cascade-hero-top {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 18px;
}

.cascade-hero-top h3 {
  margin: 0 0 6px;
}

.cascade-lead {
  margin: 0;
  max-width: 480px;
  color: var(--color-muted);
  font-size: 13px;
  line-height: 1.5;
}

.cascade-lead strong {
  color: var(--color-text);
  font-weight: 600;
}

.cascade-route {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  padding: 14px 12px;
  border-radius: 12px;
  background: var(--color-bg-subtle, rgba(255, 255, 255, 0.04));
}

.route-node {
  display: grid;
  justify-items: center;
  gap: 4px;
  min-width: 72px;
  padding: 10px 8px;
  border-radius: 10px;
  border: 1px solid transparent;
  text-align: center;
}

.route-node--entry,
.route-node--exit {
  border-color: var(--color-border);
  background: var(--color-surface, rgba(255, 255, 255, 0.02));
}

.route-node--live {
  border-color: #2a4a3a;
  background: rgba(42, 74, 58, 0.1);
}

.route-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background: var(--color-bg-subtle, rgba(255, 255, 255, 0.06));
  color: var(--color-muted);
}

.route-node--live .route-icon {
  color: var(--color-accent);
  background: rgba(42, 74, 58, 0.2);
}

.route-label {
  font-size: 12px;
  font-weight: 600;
  line-height: 1.25;
  max-width: 100px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.route-sub {
  font-size: 10px;
  color: var(--color-dim);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.route-arrow {
  flex-shrink: 0;
  color: var(--color-dim);
  opacity: 0.5;
}

.cascade-hero--on .route-arrow {
  color: var(--color-accent);
  opacity: 0.7;
}

.cascade-egress {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-top: 16px;
  padding: 14px 16px;
  border-radius: 10px;
  border: 1px solid #2a4a3a;
  background: rgba(42, 74, 58, 0.12);
}

.cascade-egress-label {
  font-size: 13px;
  color: var(--color-muted);
}

.cascade-egress-ip {
  font-size: 20px;
  font-weight: 700;
  color: var(--color-accent);
  letter-spacing: 0.02em;
}

.cascade-manage-actions {
  display: grid;
  gap: 10px;
}

.cascade-hint {
  margin: 0;
  font-size: 13px;
  color: var(--color-muted);
  line-height: 1.45;
}

.cascade-hint.subtle {
  font-size: 12px;
  opacity: 0.75;
}

.cascade-link {
  padding: 0;
  border: none;
  background: none;
  color: var(--color-accent);
  font: inherit;
  font-weight: 600;
  cursor: pointer;
  text-decoration: underline;
  text-underline-offset: 2px;
}

.cascade-link:hover {
  opacity: 0.85;
}

.cascade-result-banner,
.cascade-check-banner {
  margin-top: 12px;
  padding: 10px 14px;
  border-radius: 8px;
  font-size: 13px;
  line-height: 1.45;
}

.cascade-result-banner.ok,
.cascade-check-banner.ok {
  border: 1px solid #2a4a3a;
  background: rgba(42, 74, 58, 0.1);
  color: var(--color-text);
}

.cascade-result-banner.warn,
.cascade-check-banner.warn {
  border: 1px solid var(--color-warning, #e5a000);
  background: rgba(229, 160, 0, 0.08);
  color: var(--color-warning, #e5a000);
}

.cascade-result-steps {
  margin: 8px 0 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.cascade-result-steps li {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-size: 12.5px;
  color: var(--color-text-secondary, #9aa);
}

.cascade-result-steps .step-mark {
  flex: 0 0 auto;
  font-weight: 700;
}

.cascade-result-steps .step-ok .step-mark {
  color: #34c759;
}

.cascade-result-steps .step-failed .step-mark {
  color: var(--color-danger, #ff5a5a);
}

.cascade-result-steps .step-detail {
  opacity: 0.75;
}

.cascade-setup {
  padding: 0;
}

.cascade-setup-summary {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 16px;
  cursor: pointer;
  font-size: 14px;
  font-weight: 600;
  list-style: none;
  user-select: none;
}

.cascade-setup-summary::-webkit-details-marker {
  display: none;
}

.cascade-setup-summary::before {
  content: '▸';
  font-size: 12px;
  color: var(--color-dim);
  transition: transform 0.15s;
}

.cascade-setup[open] .cascade-setup-summary::before {
  transform: rotate(90deg);
}

.cascade-setup-body {
  padding: 0 16px 16px;
  border-top: 1px solid var(--color-border);
}

.cascade-controls {
  display: flex;
  gap: 12px;
  align-items: center;
  flex-wrap: wrap;
  margin: 14px 0 8px;
}

.cascade-mini-kpi {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin: 12px 0;
  padding: 10px 14px;
  border-radius: 8px;
  background: var(--color-bg-subtle, rgba(255, 255, 255, 0.04));
  font-size: 13px;
  color: var(--color-muted);
}

.cascade-mini-kpi strong {
  color: var(--color-text);
}

.cascade-blockers {
  margin: 12px 0;
  padding: 10px 14px;
  border-radius: 8px;
  border: 1px solid var(--color-danger, #e5484d);
  background: rgba(229, 72, 77, 0.08);
  font-size: 13px;
}

.cascade-blockers ul {
  margin: 6px 0 0;
  padding-left: 18px;
}

.cascade-tech-details {
  margin-top: 12px;
}

.cascade-tech-details summary {
  cursor: pointer;
  font-size: 12px;
  color: var(--color-dim);
  padding: 6px 0;
}

.cascade-tech-details .security-list {
  margin-top: 8px;
}

@media (max-width: 640px) {
  .cascade-route {
    justify-content: center;
  }

  .route-arrow {
    display: none;
  }

  .cascade-route {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
  }

  .route-node {
    min-width: 0;
    width: 100%;
  }

  .cascade-egress {
    flex-direction: column;
    align-items: flex-start;
  }
}

.check-text {
  min-width: 0;
  flex: 1;
}

.check-action {
  flex-shrink: 0;
  align-self: center;
}

.check-title {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 13px;
}

.check-rec {
  margin: 5px 0 0;
  color: var(--color-muted);
  font-size: 12px;
  line-height: 1.5;
}

/* containers */
.containers-panel {
  display: grid;
  gap: 2px;
  padding: 8px 14px;
}

.container-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 11px 4px;
  border-bottom: 1px solid var(--color-border);
}

.container-row:last-child {
  border-bottom: none;
}

.container-state {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.container-state.on {
  background: var(--color-accent);
  box-shadow: 0 0 6px rgba(99, 226, 161, 0.6);
}

.container-state.off {
  background: var(--color-dim);
}

.container-text {
  min-width: 0;
  flex: 1;
}

.container-text strong {
  display: block;
  font-size: 13px;
}

.container-sub {
  display: block;
  color: var(--color-dim);
  font-size: 11px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.container-stats {
  display: flex;
  gap: 6px;
}

.stat-chip {
  padding: 2px 8px;
  border: 1px solid var(--color-border);
  border-radius: 999px;
  color: var(--color-muted);
  font-size: 11px;
  white-space: nowrap;
}

.container-actions {
  display: flex;
  gap: 5px;
}

/* logs modal */
.logs-modal {
  width: min(760px, 92vw);
  padding: 16px 18px;
  display: grid;
  gap: 12px;
}

.logs-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.logs-head h3 {
  margin: 0;
  font-size: 14px;
}

.logs-loading {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--color-muted);
  font-size: 13px;
  padding: 18px 0;
}

.logs-pre {
  margin: 0;
  max-height: 56vh;
  overflow: auto;
  padding: 12px;
  border: 1px solid var(--color-border);
  border-radius: 10px;
  background: #14181b;
  font-size: 11.5px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
}

@media (max-width: 860px) {
  .overview-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 640px) {
  .kv {
    grid-template-columns: 1fr;
  }

  .container-stats {
    display: none;
  }
}
</style>
