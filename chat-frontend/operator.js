/* Операторский режим чата на chat-домене.
   Вход — данными панели (admin/moderator). Нижняя навигация: Чаты, Клиенты, Состояние.
   Использует открытые на chat-домене префиксы: /api/v1/auth/* и /api/v1/chat/admin/*. */

(function () {
  'use strict';

  var API = '/api/v1';
  var THREADS_POLL_MS = 8000;
  var MSG_POLL_MS = 4000;
  var STATUS_POLL_MS = 20000;
  var LAST_SERVER_KEY = 'op_last_server';

  var FINGERPRINTS = [
    ['chrome', 'Chrome (рекомендуется)'], ['safari', 'Safari'], ['ios', 'iOS'],
    ['firefox', 'Firefox'], ['android', 'Android'], ['edge', 'Edge'], ['random', 'Случайный']
  ];
  var PROTO_LABELS = { awg2: 'AmneziaWG', awg_legacy: 'AmneziaWG (legacy)', xray: 'Xray (VLESS-Reality)' };

  var state = {
    access: localStorage.getItem('op_access') || '',
    refresh: localStorage.getItem('op_refresh') || '',
    tab: 'chats',
    threads: [],
    activeId: '',
    activeThread: null,
    lastMsgId: 0,
    lastDateKey: '',
    threadsTimer: null,
    msgTimer: null,
    statusTimer: null,
    sending: false,
    servers: [],
    clients: [],
    allClients: [],
    clientFilter: ''
  };

  function $(id) { return document.getElementById(id); }

  // --- экраны / навигация ------------------------------------------------------
  var SCREENS = ['op-login', 'op-chats', 'op-clients', 'op-status', 'op-thread'];
  var TAB_SCREEN = { chats: 'op-chats', clients: 'op-clients', status: 'op-status' };

  function showScreen(id) {
    SCREENS.forEach(function (s) { $(s).classList.toggle('active', s === id); });
    var navTabs = ['op-chats', 'op-clients', 'op-status'];
    $('op-nav').classList.toggle('show', navTabs.indexOf(id) >= 0);
  }

  function switchTab(tab) {
    state.tab = tab;
    showScreen(TAB_SCREEN[tab]);
    [].forEach.call($('op-nav').querySelectorAll('button'), function (b) {
      b.classList.toggle('active', b.getAttribute('data-tab') === tab);
    });
    if (tab === 'chats') loadThreads();
    else if (tab === 'clients') loadAllClients();
    else if (tab === 'status') loadStatus();
  }

  [].forEach.call(document.querySelectorAll('#op-nav button'), function (b) {
    b.addEventListener('click', function () { switchTab(b.getAttribute('data-tab')); });
  });

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
    if (state.statusTimer) clearInterval(state.statusTimer);
    showScreen('op-login');
  }

  [].forEach.call(document.querySelectorAll('.op-act-logout'), function (b) { b.addEventListener('click', doLogout); });
  [].forEach.call(document.querySelectorAll('.op-act-theme'), function (b) { b.addEventListener('click', toggleTheme); });
  [].forEach.call(document.querySelectorAll('.op-act-refresh'), function (b) {
    b.addEventListener('click', function () {
      if (state.tab === 'chats') loadThreads();
      else if (state.tab === 'clients') loadAllClients();
      else if (state.tab === 'status') loadStatus();
    });
  });

  function enterApp() {
    switchTab('chats');
    if (state.threadsTimer) clearInterval(state.threadsTimer);
    state.threadsTimer = setInterval(function () {
      if ($('op-chats').classList.contains('active')) loadThreads(true);
    }, THREADS_POLL_MS);
    if (state.statusTimer) clearInterval(state.statusTimer);
    state.statusTimer = setInterval(function () {
      if ($('op-status').classList.contains('active')) loadStatus(true);
    }, STATUS_POLL_MS);
    // подгрузим серверы заранее (для форм создания клиента)
    loadServers();
  }

  function loadServers() {
    return req('GET', '/chat/admin/servers', null, true).then(function (list) {
      state.servers = list || [];
      return state.servers;
    }).catch(function () { return state.servers; });
  }

  // --- диалоги -----------------------------------------------------------------
  function loadThreads(silent) {
    return req('GET', '/chat/admin/threads', null, true).then(function (list) {
      state.threads = list || [];
      renderThreads();
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
      var prefix = t.last_sender === 'admin' ? 'Вы: ' : '';
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

  // --- вкладка КЛИЕНТЫ ----------------------------------------------------------
  function loadAllClients(silent) {
    return req('GET', '/chat/admin/clients', null, true).then(function (list) {
      state.allClients = list || [];
      state.clients = state.allClients;
      renderClients();
    }).catch(function (err) {
      if (err && err.message === 'session') return;
      if (!silent) toast(err.message || 'Не удалось загрузить клиентов.');
    });
  }

  function fmtBytes(n) {
    n = n || 0;
    if (n < 1024) return n + ' Б';
    var u = ['КБ', 'МБ', 'ГБ', 'ТБ'], i = -1;
    do { n /= 1024; i++; } while (n >= 1024 && i < u.length - 1);
    return (n >= 100 ? Math.round(n) : n.toFixed(1)) + ' ' + u[i];
  }

  function renderClients() {
    var box = $('op-client-list');
    box.innerHTML = '';
    var q = state.clientFilter.trim().toLowerCase();
    var list = !q ? state.allClients : state.allClients.filter(function (c) {
      return [c.name, c.server_name, c.protocol].some(function (v) { return (v || '').toLowerCase().indexOf(q) >= 0; });
    });
    if (!list.length) {
      var e = document.createElement('div');
      e.className = 'op-empty';
      e.textContent = state.allClients.length ? 'Ничего не найдено.' : 'Пока нет клиентов. Нажмите «+ Создать».';
      box.appendChild(e);
      return;
    }
    list.forEach(function (c) {
      var row = document.createElement('div');
      row.className = 'op-cli';
      row.addEventListener('click', function () { openClientDetail(c.id); });

      var dot = document.createElement('div');
      dot.className = 'dot ' + (c.online ? 'on' : (c.blocked ? 'off' : ''));
      row.appendChild(dot);

      var mid = document.createElement('div');
      mid.className = 'mid';
      var nm = document.createElement('div');
      nm.className = 'nm';
      nm.appendChild(document.createTextNode(c.name || '—'));
      if (c.blocked) { var bt = document.createElement('span'); bt.className = 'op-tag warn'; bt.textContent = 'блок'; nm.appendChild(bt); }
      mid.appendChild(nm);
      var sub = document.createElement('div');
      sub.className = 'sub2';
      sub.textContent = (c.server_name || '—') + ' · ' + (PROTO_LABELS[c.protocol] || c.protocol || '');
      mid.appendChild(sub);
      row.appendChild(mid);

      var rt = document.createElement('div');
      rt.className = 'rt';
      rt.textContent = fmtBytes(c.traffic_used_bytes) + (c.traffic_limit_bytes ? ' / ' + fmtBytes(c.traffic_limit_bytes) : '');
      row.appendChild(rt);

      box.appendChild(row);
    });
  }

  $('op-cli-search').addEventListener('input', function () { state.clientFilter = this.value; renderClients(); });
  $('op-cli-create').addEventListener('click', function () { openClientForm('create'); });

  function openClientDetail(clientId) {
    var overlay = buildOverlay(false);
    var modal = overlay.querySelector('.op-modal');
    modal.innerHTML = '<h3>Клиент</h3><p class="op-hint">Загрузка…</p>';
    document.body.appendChild(overlay);
    req('GET', '/chat/admin/clients/' + encodeURIComponent(clientId), null, true).then(function (c) {
      var linkedThread = state.threads.filter(function (t) { return t.client_id === c.id; })[0];
      var rows = '';
      function kv(k, v) { return '<div class="op-kv"><span class="k">' + k + '</span><span class="v">' + escapeHtml(v) + '</span></div>'; }
      rows += kv('Сервер', c.server_name || '—');
      rows += kv('Протокол', PROTO_LABELS[c.protocol] || c.protocol || '—');
      rows += kv('Статус', c.online ? 'онлайн' : (c.blocked ? 'заблокирован' : 'офлайн'));
      rows += kv('Трафик', fmtBytes(c.traffic_used_bytes) + (c.traffic_limit_bytes ? ' из ' + fmtBytes(c.traffic_limit_bytes) : ' (без лимита)'));
      if (c.expires_at) rows += kv('Действует до', fmtTime(c.expires_at));
      rows += kv('Чат', linkedThread ? ('@' + linkedThread.username) : 'не привязан');

      var conf = c.config_text || c.vpn_link || '';
      var html =
        '<h3>' + escapeHtml(c.name || 'Клиент') + '</h3>' +
        rows +
        (conf ? '<div class="op-config" id="cd-conf">' + escapeHtml(conf) + '</div>' : '<p class="op-hint" style="margin-top:10px">Конфигурация недоступна (импортированный клиент).</p>') +
        '<div class="row">' +
        '<button type="button" class="op-btn ghost" id="cd-close">Закрыть</button>' +
        (conf ? '<button type="button" class="op-btn" id="cd-copy">Копировать конфиг</button>' : '') +
        '</div>';
      modal.innerHTML = html;
      $('cd-close').addEventListener('click', function () { overlay.remove(); });
      if (conf) {
        $('cd-copy').addEventListener('click', function () {
          copyText(conf).then(function (ok) { toast(ok ? 'Конфиг скопирован.' : 'Не удалось скопировать.'); });
        });
      }
    }).catch(function (err) {
      modal.innerHTML = '<h3>Клиент</h3><p class="op-error">' + escapeHtml(err.message || 'Не удалось загрузить.') + '</p>' +
        '<div class="row"><button type="button" class="op-btn ghost" id="cd-close">Закрыть</button></div>';
      $('cd-close').addEventListener('click', function () { overlay.remove(); });
    });
  }

  // --- вкладка СОСТОЯНИЕ --------------------------------------------------------
  function countryFlag(cc) {
    if (!cc || cc.length !== 2) return '🖥';
    var base = 0x1F1E6;
    var a = cc.toUpperCase().charCodeAt(0) - 65;
    var b = cc.toUpperCase().charCodeAt(1) - 65;
    if (a < 0 || a > 25 || b < 0 || b > 25) return '🖥';
    return String.fromCodePoint(base + a) + String.fromCodePoint(base + b);
  }

  function statusOk(s) { return (s || '').toLowerCase() === 'online'; }

  function loadStatus(silent) {
    return req('GET', '/chat/admin/overview', null, true).then(function (list) {
      renderStatus(list || []);
    }).catch(function (err) {
      if (err && err.message === 'session') return;
      if (!silent) toast(err.message || 'Не удалось загрузить состояние.');
    });
  }

  function renderStatus(servers) {
    var box = $('op-status-body');
    box.innerHTML = '';
    if (!servers.length) {
      var e = document.createElement('div'); e.className = 'op-empty'; e.textContent = 'Серверов пока нет.'; box.appendChild(e);
      return;
    }
    var bad = servers.filter(function (s) { return !statusOk(s.status); }).length;
    var sum = document.createElement('div');
    sum.className = 'op-summary ' + (bad ? 'warn' : 'ok');
    sum.textContent = bad
      ? ('Внимание: проблемы на ' + bad + ' из ' + servers.length + ' серверов')
      : ('Все серверы в порядке (' + servers.length + ')');
    box.appendChild(sum);

    servers.forEach(function (s) {
      var row = document.createElement('div');
      row.className = 'op-srv';

      var fl = document.createElement('div');
      fl.className = 'flag';
      fl.textContent = countryFlag(s.country_code);
      row.appendChild(fl);

      var mid = document.createElement('div');
      mid.className = 'mid';
      var nm = document.createElement('div'); nm.className = 'nm'; nm.textContent = s.name; mid.appendChild(nm);
      var meta = document.createElement('div'); meta.className = 'meta';
      var parts = [s.host];
      if (s.country_name) parts.push(s.country_name);
      if (typeof s.active_peers === 'number') parts.push(s.active_peers + ' клиентов');
      if (typeof s.cpu_percent === 'number') parts.push('CPU ' + Math.round(s.cpu_percent) + '%');
      if (s.mem_used_mb && s.mem_total_mb) parts.push('RAM ' + Math.round(s.mem_used_mb / s.mem_total_mb * 100) + '%');
      if (s.xray_cascade_active) parts.push('каскад → ' + (s.xray_cascade_exit_name || '?'));
      meta.textContent = parts.filter(Boolean).join(' · ');
      mid.appendChild(meta);
      row.appendChild(mid);

      var st = document.createElement('div');
      var ok = statusOk(s.status);
      st.className = 'st ' + (ok ? 'ok' : 'bad');
      st.textContent = ok ? 'онлайн' : (s.status === 'unknown' ? '—' : 'офлайн');
      row.appendChild(st);

      box.appendChild(row);
    });
  }

  // --- открытый диалог ---------------------------------------------------------
  $('op-back').addEventListener('click', function () {
    state.activeId = ''; state.activeThread = null;
    if (state.msgTimer) clearInterval(state.msgTimer);
    switchTab('chats');
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

  // --- форма «создать клиента» (общая: из чата и из вкладки Клиенты) -----------
  $('op-act-create').addEventListener('click', function () { openClientForm('provision'); });

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

  // mode: 'provision' (привязать к открытому чату + отправить) | 'create' (просто создать)
  function openClientForm(mode) {
    if (mode === 'provision' && !state.activeThread) return;
    var overlay = buildOverlay(false);
    var modal = overlay.querySelector('.op-modal');
    var title = mode === 'provision' ? 'Создать ключ и отправить' : 'Создать клиента';
    var hint = mode === 'provision'
      ? 'Новый клиент будет создан, привязан к @' + escapeHtml(state.activeThread.username) + ' и отправлен в чат.'
      : 'Новый VPN-клиент появится в списке «Клиенты».';
    var submitLabel = mode === 'provision' ? 'Создать и отправить' : 'Создать';
    modal.innerHTML =
      '<h3>' + title + '</h3>' +
      '<p class="op-hint">' + hint + '</p>' +
      '<div class="op-field"><span>Сервер</span><select id="pv-server"></select></div>' +
      '<div class="op-field"><span>Протокол</span><select id="pv-proto"></select></div>' +
      '<div class="op-field"><span>Имя клиента</span><input id="pv-name" type="text"></div>' +
      '<div class="op-field" id="pv-fp-wrap"><span>Отпечаток TLS</span><select id="pv-fp"></select></div>' +
      '<div class="op-grid">' +
      '<div class="op-field"><span>Лимит, ГБ (пусто — без)</span><input id="pv-traffic" type="number" min="0" inputmode="decimal"></div>' +
      '<div class="op-field"><span>Действует до</span><input id="pv-exp" type="date"></div>' +
      '</div>' +
      '<div class="row"><button type="button" class="op-btn ghost" id="pv-cancel">Отмена</button>' +
      '<button type="button" class="op-btn" id="pv-submit">' + submitLabel + '</button></div>';
    document.body.appendChild(overlay);

    var fpSel = $('pv-fp');
    FINGERPRINTS.forEach(function (f) {
      var o = document.createElement('option'); o.value = f[0]; o.textContent = f[1]; fpSel.appendChild(o);
    });
    if (mode === 'provision') $('pv-name').value = state.activeThread.display_name || state.activeThread.username || '';
    $('pv-cancel').addEventListener('click', function () { overlay.remove(); });
    $('pv-server').addEventListener('change', syncProvProto);
    $('pv-proto').addEventListener('change', syncProvFp);
    $('pv-submit').addEventListener('click', function () { submitClientForm(mode, overlay, this); });

    $('pv-server').innerHTML = '<option>Загрузка…</option>';
    loadServers().then(function () {
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

  function submitClientForm(mode, overlay, btn) {
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
    var origLabel = btn.textContent;
    btn.disabled = true; btn.textContent = 'Создаю…';

    if (mode === 'provision') {
      req('POST', '/chat/admin/threads/' + state.activeId + '/provision-client', payload, true)
        .then(function (m) {
          localStorage.setItem(LAST_SERVER_KEY, serverId);
          overlay.remove();
          appendMessage(m);
          $('op-thread-messages').scrollTop = $('op-thread-messages').scrollHeight;
          toast('Клиент создан, ключ отправлен.');
          loadThreads(true);
        })
        .catch(function (err) { btn.disabled = false; btn.textContent = origLabel; toast(err.message || 'Не удалось создать клиента.'); });
    } else {
      req('POST', '/chat/admin/clients', payload, true)
        .then(function (c) {
          localStorage.setItem(LAST_SERVER_KEY, serverId);
          overlay.remove();
          toast('Клиент создан.');
          loadAllClients(true);
          openClientDetail(c.id);
        })
        .catch(function (err) { btn.disabled = false; btn.textContent = origLabel; toast(err.message || 'Не удалось создать клиента.'); });
    }
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

  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text).then(function () { return true; }).catch(function () { return fallbackCopy(text); });
    }
    return Promise.resolve(fallbackCopy(text));
  }

  function fallbackCopy(text) {
    try {
      var ta = document.createElement('textarea');
      ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta); ta.select();
      var ok = document.execCommand('copy');
      document.body.removeChild(ta);
      return ok;
    } catch (e) { return false; }
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
