/* Операторский режим чата на chat-домене.
   Вход — данными панели (admin/moderator), затем диалоги всех клиентов и
   действия: написать, выдать ключ, создать ключ, привязать ключ.
   Использует открытые на chat-домене префиксы: /api/v1/auth/* и /api/v1/chat/admin/*. */

(function () {
  'use strict';

  var API = '/api/v1';
  var THREADS_POLL_MS = 8000;
  var MSG_POLL_MS = 4000;
  var LAST_SERVER_KEY = 'op_last_server';

  var FINGERPRINTS = [
    ['chrome', 'Chrome (рекомендуется)'], ['safari', 'Safari'], ['ios', 'iOS'],
    ['firefox', 'Firefox'], ['android', 'Android'], ['edge', 'Edge'], ['random', 'Случайный']
  ];
  var PROTO_LABELS = { awg2: 'AmneziaWG', awg_legacy: 'AmneziaWG (legacy)', xray: 'Xray (VLESS-Reality)' };

  var state = {
    access: localStorage.getItem('op_access') || '',
    refresh: localStorage.getItem('op_refresh') || '',
    threads: [],
    activeId: '',
    activeThread: null,
    lastMsgId: 0,
    lastDateKey: '',
    threadsTimer: null,
    msgTimer: null,
    sending: false,
    servers: [],
    clients: []
  };

  function $(id) { return document.getElementById(id); }

  // --- экраны -----------------------------------------------------------------
  function showScreen(id) {
    ['op-login', 'op-threads', 'op-thread'].forEach(function (s) {
      $(s).classList.toggle('active', s === id);
    });
  }

  // --- тема --------------------------------------------------------------------
  function toggleTheme() {
    var cur = document.documentElement.getAttribute('data-theme');
    if (!cur) {
      cur = (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches) ? 'light' : 'dark';
    }
    var next = cur === 'dark' ? 'light' : 'dark';
    localStorage.setItem('chat_theme', next);
    document.documentElement.setAttribute('data-theme', next);
  }

  // --- api ---------------------------------------------------------------------
  function req(method, path, body, auth, retry) {
    var headers = { 'Content-Type': 'application/json' };
    if (auth && state.access) headers.Authorization = 'Bearer ' + state.access;
    return fetch(API + path, {
      method: method,
      headers: headers,
      body: body ? JSON.stringify(body) : undefined
    }).then(function (res) {
      if (res.status === 401 && auth && state.refresh && !retry) {
        return refreshTokens().then(function (ok) {
          if (!ok) { doLogout(); throw new Error('session'); }
          return req(method, path, body, auth, true);
        });
      }
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (!res.ok) {
          var err = new Error((data && data.detail) || 'Ошибка сети');
          err.status = res.status;
          throw err;
        }
        return data;
      });
    });
  }

  function refreshTokens() {
    if (!state.refresh) return Promise.resolve(false);
    return fetch(API + '/auth/refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: state.refresh })
    }).then(function (res) {
      if (!res.ok) return false;
      return res.json().then(function (d) { saveTokens(d); return true; });
    }).catch(function () { return false; });
  }

  function saveTokens(d) {
    state.access = d.access_token;
    state.refresh = d.refresh_token;
    localStorage.setItem('op_access', state.access);
    localStorage.setItem('op_refresh', state.refresh);
  }

  // --- вход / выход ------------------------------------------------------------
  $('op-login-form').addEventListener('submit', function (e) {
    e.preventDefault();
    var btn = $('op-login-btn');
    $('op-login-error').hidden = true;
    btn.disabled = true; btn.textContent = 'Вхожу…';
    req('POST', '/auth/login', {
      email: $('op-email').value.trim(),
      password: $('op-password').value
    }, false).then(function (d) {
      saveTokens(d);
      $('op-password').value = '';
      enterApp();
    }).catch(function (err) {
      $('op-login-error').textContent = err.status === 401
        ? 'Неверный email или пароль.'
        : (err.message || 'Не удалось войти.');
      $('op-login-error').hidden = false;
    }).finally(function () {
      btn.disabled = false; btn.textContent = 'Войти';
    });
  });

  function doLogout() {
    localStorage.removeItem('op_access');
    localStorage.removeItem('op_refresh');
    state.access = ''; state.refresh = '';
    state.activeId = ''; state.activeThread = null; state.threads = [];
    if (state.threadsTimer) clearInterval(state.threadsTimer);
    if (state.msgTimer) clearInterval(state.msgTimer);
    showScreen('op-login');
  }

  $('op-logout').addEventListener('click', doLogout);
  $('op-theme').addEventListener('click', toggleTheme);
  $('op-refresh').addEventListener('click', function () { loadThreads(); });

  function enterApp() {
    showScreen('op-threads');
    loadThreads();
    if (state.threadsTimer) clearInterval(state.threadsTimer);
    state.threadsTimer = setInterval(function () {
      if ($('op-threads').classList.contains('active')) loadThreads(true);
    }, THREADS_POLL_MS);
  }

  // --- диалоги -----------------------------------------------------------------
  function loadThreads(silent) {
    return req('GET', '/chat/admin/threads', null, true).then(function (list) {
      state.threads = list || [];
      renderThreads();
      // если открыт диалог — обновим его шапку (привязка VPN могла измениться)
      if (state.activeId) {
        var t = findThread(state.activeId);
        if (t) { state.activeThread = t; renderThreadHeader(t); }
      }
    }).catch(function (err) {
      if (err && err.message === 'session') return;
      if (!silent) toast(err.message || 'Не удалось загрузить диалоги.');
    });
  }

  function findThread(id) {
    for (var i = 0; i < state.threads.length; i++) if (state.threads[i].id === id) return state.threads[i];
    return null;
  }

  function initials(t) {
    var s = (t.display_name || t.username || '?').trim();
    return s.charAt(0).toUpperCase();
  }

  function fmtTime(iso) {
    if (!iso) return '';
    try {
      var d = new Date(iso), now = new Date();
      if (d.toDateString() === now.toDateString()) {
        return d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
      }
      return d.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' });
    } catch (e) { return ''; }
  }

  function renderThreads() {
    var box = $('op-thread-list');
    box.innerHTML = '';
    if (!state.threads.length) {
      var e = document.createElement('div');
      e.className = 'op-empty';
      e.textContent = 'Пока нет диалогов с клиентами.';
      box.appendChild(e);
      return;
    }
    state.threads.forEach(function (t) {
      var row = document.createElement('div');
      row.className = 'op-thread';
      row.addEventListener('click', function () { openThread(t.id); });

      var av = document.createElement('div');
      av.className = 'av';
      av.textContent = initials(t);
      row.appendChild(av);

      var mid = document.createElement('div');
      mid.className = 'mid';
      var nm = document.createElement('div');
      nm.className = 'nm';
      nm.appendChild(document.createTextNode(t.display_name || ('@' + t.username)));
      var chip = document.createElement('span');
      if (t.client_id && !t.client_missing) { chip.className = 'vpn-chip ok'; chip.textContent = 'VPN'; }
      else if (t.client_missing) { chip.className = 'vpn-chip none'; chip.textContent = 'VPN ?'; }
      else { chip.className = 'vpn-chip none'; chip.textContent = 'нет VPN'; }
      nm.appendChild(chip);
      mid.appendChild(nm);
      var pv = document.createElement('div');
      pv.className = 'pv';
      var prefix = t.last_sender === 'admin' ? 'Вы: ' : (t.last_sender === 'system' ? '' : '');
      pv.textContent = prefix + (t.last_preview || 'нет сообщений');
      mid.appendChild(pv);
      row.appendChild(mid);

      var rt = document.createElement('div');
      rt.className = 'rt';
      var tm = document.createElement('span');
      tm.className = 'tm';
      tm.textContent = fmtTime(t.last_message_at);
      rt.appendChild(tm);
      if (t.unread_count > 0) {
        var b = document.createElement('span');
        b.className = 'op-badge';
        b.textContent = t.unread_count > 99 ? '99+' : String(t.unread_count);
        rt.appendChild(b);
      }
      row.appendChild(rt);
      box.appendChild(row);
    });
  }

  // --- открытый диалог ---------------------------------------------------------
  $('op-back').addEventListener('click', function () {
    state.activeId = ''; state.activeThread = null;
    if (state.msgTimer) clearInterval(state.msgTimer);
    showScreen('op-threads');
    loadThreads(true);
  });

  function openThread(id) {
    var t = findThread(id);
    if (!t) return;
    state.activeId = id;
    state.activeThread = t;
    state.lastMsgId = 0;
    state.lastDateKey = '';
    $('op-thread-messages').innerHTML = '';
    renderThreadHeader(t);
    showScreen('op-thread');
    pollMessages(true);
    if (state.msgTimer) clearInterval(state.msgTimer);
    state.msgTimer = setInterval(function () {
      if ($('op-thread').classList.contains('active')) pollMessages(false);
    }, MSG_POLL_MS);
  }

  function renderThreadHeader(t) {
    $('op-thread-who').textContent = t.display_name || ('@' + t.username);
    var meta = '@' + t.username;
    if (t.client_id && !t.client_missing) meta += ' · VPN: ' + (t.client_name || 'привязан');
    else if (t.client_missing) meta += ' · VPN-клиент удалён';
    else meta += ' · VPN не привязан';
    $('op-thread-meta').textContent = meta;
    // «Выдать ключ» активна только если есть рабочая привязка
    $('op-act-sendkey').disabled = !(t.client_id && !t.client_missing);
  }

  function pollMessages(initial) {
    if (!state.activeId) return;
    req('GET', '/chat/admin/threads/' + state.activeId + '/messages?after_id=' + state.lastMsgId + '&limit=200', null, true)
      .then(function (data) {
        var box = $('op-thread-messages');
        var atBottom = (box.scrollHeight - box.scrollTop - box.clientHeight) < 100;
        (data.messages || []).forEach(appendMessage);
        if (initial || atBottom) box.scrollTop = box.scrollHeight;
      })
      .catch(function (err) { if (err && err.message !== 'session') {/* тихо */} });
  }

  function dayLabel(d) {
    var now = new Date();
    var today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    var that = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    var diff = Math.round((today - that) / 86400000);
    if (diff === 0) return 'Сегодня';
    if (diff === 1) return 'Вчера';
    return d.toLocaleDateString('ru-RU', { day: '2-digit', month: 'long', year: 'numeric' });
  }

  function appendMessage(m) {
    var box = $('op-thread-messages');
    if (box.querySelector('[data-mid="' + m.id + '"]')) return;
    state.lastMsgId = Math.max(state.lastMsgId, m.id);

    var when = null;
    try { when = new Date(m.created_at); } catch (e) { when = null; }
    if (when && !isNaN(when.getTime())) {
      var key = when.toDateString();
      if (key !== state.lastDateKey) {
        state.lastDateKey = key;
        var day = document.createElement('div');
        day.className = 'op-day';
        day.textContent = dayLabel(when);
        box.appendChild(day);
      }
    }

    var div = document.createElement('div');
    div.className = 'op-msg ' + (m.sender === 'client' ? 'client' : (m.sender === 'system' ? 'system' : 'admin'));
    div.dataset.mid = String(m.id);
    div.appendChild(document.createTextNode(m.body || ''));
    if (m.attachment) {
      var att = document.createElement('span');
      att.className = 'att';
      att.textContent = m.attachment.expired
        ? '🔑 ' + m.attachment.filename + ' — срок истёк'
        : '🔑 ключ: ' + m.attachment.filename;
      div.appendChild(att);
    }
    if (when) {
      var tm = document.createElement('span');
      tm.className = 'tm';
      try { tm.textContent = when.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' }); } catch (e2) {}
      div.appendChild(tm);
    }
    box.appendChild(div);
  }

  // --- отправка сообщения ------------------------------------------------------
  function sendMessage() {
    var ta = $('op-draft');
    var body = ta.value.trim();
    if (!body || state.sending || !state.activeId) return;
    state.sending = true;
    $('op-send').disabled = true;
    req('POST', '/chat/admin/threads/' + state.activeId + '/messages', { body: body }, true)
      .then(function (m) {
        ta.value = ''; autosize();
        appendMessage(m);
        $('op-thread-messages').scrollTop = $('op-thread-messages').scrollHeight;
      })
      .catch(function (err) { toast(err.message || 'Не удалось отправить.'); })
      .finally(function () { state.sending = false; $('op-send').disabled = false; });
  }
  $('op-send').addEventListener('click', sendMessage);
  $('op-draft').addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  });
  function autosize() {
    var ta = $('op-draft');
    ta.style.height = 'auto';
    ta.style.height = Math.min(ta.scrollHeight, 130) + 'px';
  }
  $('op-draft').addEventListener('input', autosize);

  // --- выдать ключ -------------------------------------------------------------
  $('op-act-sendkey').addEventListener('click', function () {
    var t = state.activeThread;
    if (!t || !t.client_id || t.client_missing) { toast('Сначала привяжите VPN-клиента.'); return; }
    var btn = this;
    btn.disabled = true;
    req('POST', '/chat/admin/threads/' + state.activeId + '/send-key', {}, true)
      .then(function (m) { appendMessage(m); $('op-thread-messages').scrollTop = $('op-thread-messages').scrollHeight; toast('Ключ отправлен в чат.'); loadThreads(true); })
      .catch(function (err) { toast(err.message || 'Не удалось выдать ключ.'); })
      .finally(function () { btn.disabled = false; renderThreadHeader(state.activeThread); });
  });

  // --- создать ключ (provision) ------------------------------------------------
  $('op-act-create').addEventListener('click', openProvision);

  function eligibleServers() {
    return state.servers.filter(function (s) {
      return (s.client_protocols && s.client_protocols.length) || s.awg2_imported;
    });
  }

  function serverProtocols(s) {
    if (!s) return [];
    if (s.client_protocols && s.client_protocols.length) return s.client_protocols.slice();
    return s.awg2_imported ? ['awg2'] : [];
  }

  function openProvision() {
    if (!state.activeThread) return;
    var overlay = buildOverlay(false);
    var modal = overlay.querySelector('.op-modal');
    modal.innerHTML =
      '<h3>Создать ключ и отправить</h3>' +
      '<p class="op-hint">Новый клиент будет создан, привязан к @' + escapeHtml(state.activeThread.username) +
      ' и отправлен в чат. Старый (если был) останется в «Клиенты».</p>' +
      '<div class="op-field"><span>Сервер</span><select id="pv-server"></select></div>' +
      '<div class="op-field"><span>Протокол</span><select id="pv-proto"></select></div>' +
      '<div class="op-field"><span>Имя клиента</span><input id="pv-name" type="text"></div>' +
      '<div class="op-field" id="pv-fp-wrap"><span>Отпечаток TLS</span><select id="pv-fp"></select></div>' +
      '<div class="op-grid">' +
      '<div class="op-field"><span>Лимит, ГБ (пусто — без)</span><input id="pv-traffic" type="number" min="0" inputmode="decimal"></div>' +
      '<div class="op-field"><span>Действует до</span><input id="pv-exp" type="date"></div>' +
      '</div>' +
      '<p class="op-hint" id="pv-cascade" hidden></p>' +
      '<div class="row"><button type="button" class="op-btn ghost" id="pv-cancel">Отмена</button>' +
      '<button type="button" class="op-btn" id="pv-submit">Создать и отправить</button></div>';
    document.body.appendChild(overlay);

    var fpSel = $('pv-fp');
    FINGERPRINTS.forEach(function (f) {
      var o = document.createElement('option'); o.value = f[0]; o.textContent = f[1]; fpSel.appendChild(o);
    });
    $('pv-name').value = state.activeThread.display_name || state.activeThread.username || '';
    $('pv-cancel').addEventListener('click', function () { overlay.remove(); });
    $('pv-server').addEventListener('change', syncProvProto);
    $('pv-proto').addEventListener('change', syncProvFp);
    $('pv-submit').addEventListener('click', function () { submitProvision(overlay, this); });

    // загрузка серверов
    $('pv-server').innerHTML = '<option>Загрузка…</option>';
    req('GET', '/chat/admin/servers', null, true).then(function (list) {
      state.servers = list || [];
      var sel = $('pv-server');
      sel.innerHTML = '';
      var elig = eligibleServers();
      if (!elig.length) { sel.innerHTML = '<option value="">нет доступных серверов</option>'; return; }
      elig.forEach(function (s) {
        var o = document.createElement('option'); o.value = s.id; o.textContent = s.name; sel.appendChild(o);
      });
      var remembered = localStorage.getItem(LAST_SERVER_KEY);
      if (remembered && elig.some(function (s) { return s.id === remembered; })) sel.value = remembered;
      syncProvProto();
    }).catch(function (err) {
      $('pv-server').innerHTML = '<option value="">ошибка загрузки</option>';
      toast(err.message || 'Не удалось загрузить серверы.');
    });
  }

  function syncProvProto() {
    var s = state.servers.filter(function (x) { return x.id === $('pv-server').value; })[0];
    var sel = $('pv-proto');
    sel.innerHTML = '';
    serverProtocols(s).forEach(function (id) {
      var o = document.createElement('option'); o.value = id; o.textContent = PROTO_LABELS[id] || id; sel.appendChild(o);
    });
    syncProvFp();
  }

  function syncProvFp() {
    $('pv-fp-wrap').hidden = $('pv-proto').value !== 'xray';
  }

  function submitProvision(overlay, btn) {
    var serverId = $('pv-server').value;
    var proto = $('pv-proto').value;
    if (!serverId || !proto) { toast('Выберите сервер и протокол.'); return; }
    var traffic = null;
    var gb = parseFloat(String($('pv-traffic').value).replace(',', '.'));
    if ($('pv-traffic').value && !isNaN(gb) && gb > 0) traffic = Math.round(gb * 1024 * 1024 * 1024);
    var payload = {
      server_id: serverId,
      protocol: proto,
      name: $('pv-name').value.trim() || null,
      format: 'both',
      traffic_limit_bytes: traffic,
      expires_at: $('pv-exp').value || null,
      fingerprint: proto === 'xray' ? $('pv-fp').value : null,
      replace: true
    };
    btn.disabled = true; btn.textContent = 'Создаю…';
    req('POST', '/chat/admin/threads/' + state.activeId + '/provision-client', payload, true)
      .then(function (m) {
        localStorage.setItem(LAST_SERVER_KEY, serverId);
        overlay.remove();
        appendMessage(m);
        $('op-thread-messages').scrollTop = $('op-thread-messages').scrollHeight;
        toast('Клиент создан, ключ отправлен.');
        loadThreads(true);
      })
      .catch(function (err) { btn.disabled = false; btn.textContent = 'Создать и отправить'; toast(err.message || 'Не удалось создать клиента.'); });
  }

  // --- привязать существующего (link-and-send) --------------------------------
  $('op-act-link').addEventListener('click', openLink);

  function openLink() {
    if (!state.activeThread) return;
    var overlay = buildOverlay(false);
    var modal = overlay.querySelector('.op-modal');
    var sel = { id: '' };
    modal.innerHTML =
      '<h3>Привязать ключ</h3>' +
      '<p class="op-hint">Выберите ранее созданного VPN-клиента — он привяжется к @' +
      escapeHtml(state.activeThread.username) + ' и отправится в чат. Старая привязка снимется (клиент не удаляется).</p>' +
      '<input id="lk-search" class="op-search" type="text" placeholder="Поиск по имени/серверу…">' +
      '<div id="lk-clients" class="op-clients"><p class="op-hint">Загрузка…</p></div>' +
      '<div class="row"><button type="button" class="op-btn ghost" id="lk-cancel">Отмена</button>' +
      '<button type="button" class="op-btn" id="lk-submit" disabled>Привязать и отправить</button></div>';
    document.body.appendChild(overlay);
    // стилизуем поиск как input
    $('lk-search').style.cssText = 'background:var(--bg2);border:1px solid var(--border);color:var(--text);border-radius:10px;padding:11px 12px;font:inherit;width:100%;';
    $('lk-cancel').addEventListener('click', function () { overlay.remove(); });

    function render(list) {
      var box = $('lk-clients');
      box.innerHTML = '';
      if (!list.length) { box.innerHTML = '<p class="op-hint">Ничего не найдено.</p>'; return; }
      list.forEach(function (c) {
        var row = document.createElement('div');
        row.className = 'op-client' + (sel.id === c.id ? ' sel' : '');
        var nm = document.createElement('div'); nm.className = 'nm'; nm.textContent = c.name;
        var sv = document.createElement('div'); sv.className = 'sv'; sv.textContent = (c.server_name || '') + ' · ' + (c.protocol || '');
        row.appendChild(nm); row.appendChild(sv);
        row.addEventListener('click', function () {
          sel.id = c.id;
          $('lk-submit').disabled = false;
          box.querySelectorAll('.op-client').forEach(function (n) { n.classList.remove('sel'); });
          row.classList.add('sel');
        });
        box.appendChild(row);
      });
    }

    function applyFilter() {
      var q = $('lk-search').value.trim().toLowerCase();
      var list = !q ? state.clients : state.clients.filter(function (c) {
        return [c.name, c.server_name, c.protocol].some(function (v) { return (v || '').toLowerCase().indexOf(q) >= 0; });
      });
      render(list);
    }
    $('lk-search').addEventListener('input', applyFilter);

    $('lk-submit').addEventListener('click', function () {
      if (!sel.id) return;
      var b = this; b.disabled = true; b.textContent = 'Привязываю…';
      req('POST', '/chat/admin/threads/' + state.activeId + '/link-and-send-key', { client_id: sel.id, replace: true }, true)
        .then(function (m) {
          overlay.remove();
          appendMessage(m);
          $('op-thread-messages').scrollTop = $('op-thread-messages').scrollHeight;
          toast('Привязано, ключ отправлен.');
          loadThreads(true);
        })
        .catch(function (err) { b.disabled = false; b.textContent = 'Привязать и отправить'; toast(err.message || 'Не удалось привязать.'); });
    });

    req('GET', '/chat/admin/clients', null, true).then(function (list) {
      state.clients = list || [];
      applyFilter();
    }).catch(function (err) {
      $('lk-clients').innerHTML = '<p class="op-hint">Ошибка загрузки.</p>';
      toast(err.message || 'Не удалось загрузить клиентов.');
    });
  }

  // --- утилиты UI --------------------------------------------------------------
  function buildOverlay(center) {
    var overlay = document.createElement('div');
    overlay.className = 'op-overlay' + (center ? ' center' : '');
    var modal = document.createElement('div');
    modal.className = 'op-modal';
    overlay.appendChild(modal);
    overlay.addEventListener('click', function (e) { if (e.target === overlay) overlay.remove(); });
    return overlay;
  }

  function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  var toastTimer = null;
  function toast(msg) {
    var prev = document.querySelector('.op-toast');
    if (prev) prev.remove();
    var t = document.createElement('div');
    t.className = 'op-toast';
    t.textContent = msg;
    document.body.appendChild(t);
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.remove(); }, 3200);
  }

  // --- старт -------------------------------------------------------------------
  if (state.refresh) {
    refreshTokens().then(function (ok) {
      if (ok) enterApp();
      else doLogout();
    });
  } else {
    showScreen('op-login');
  }
})();
