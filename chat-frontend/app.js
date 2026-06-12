/* Чат поддержки — клиентский mini-app.
   Без сборки: login -> polling -> отправка, PWA-установка и push-уведомления. */

(function () {
  'use strict';

  var API = '/api/v1/chat/client';
  var POLL_MS = 4000;

  var els = {
    loginScreen: document.getElementById('login-screen'),
    chatScreen: document.getElementById('chat-screen'),
    form: document.getElementById('login-form'),
    username: document.getElementById('login-username'),
    password: document.getElementById('login-password'),
    loginBtn: document.getElementById('login-btn'),
    loginError: document.getElementById('login-error'),
    connStatus: document.getElementById('conn-status'),
    notifBtn: document.getElementById('notif-btn'),
    installBtn: document.getElementById('install-btn'),
    logoutBtn: document.getElementById('logout-btn'),
    banner: document.getElementById('banner'),
    bannerText: document.getElementById('banner-text'),
    bannerAction: document.getElementById('banner-action'),
    bannerClose: document.getElementById('banner-close'),
    messages: document.getElementById('messages'),
    messagesEmpty: document.getElementById('messages-empty'),
    scrollDown: document.getElementById('scroll-down'),
    draft: document.getElementById('draft'),
    sendBtn: document.getElementById('send-btn'),
    iosHelp: document.getElementById('ios-help'),
    iosHelpClose: document.getElementById('ios-help-close'),
    vpnBtn: document.getElementById('vpn-btn'),
    vpnSheet: document.getElementById('vpn-sheet'),
    vpnClose: document.getElementById('vpn-close'),
    vpnBody: document.getElementById('vpn-body'),
    themeBtn: document.getElementById('theme-btn')
  };

  var state = {
    access: localStorage.getItem('chat_access') || '',
    refresh: localStorage.getItem('chat_refresh') || '',
    profile: null,
    lastId: 0,
    pollTimer: null,
    sending: false,
    online: true,
    unread: 0,
    nearBottom: true,
    lastDateKey: '',
    vapidKey: '',
    pushSupported: false,
    deferredPrompt: null
  };

  // --- утилиты ----------------------------------------------------------------

  var isIos = /iphone|ipad|ipod/i.test(navigator.userAgent) && !window.MSStream;
  function isStandalone() {
    return (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches) ||
      navigator.standalone === true;
  }

  function urlB64ToUint8Array(base64) {
    var padding = '='.repeat((4 - base64.length % 4) % 4);
    var b64 = (base64 + padding).replace(/-/g, '+').replace(/_/g, '/');
    var raw = atob(b64);
    var out = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  }

  // --- высота под клавиатуру (iPhone) -----------------------------------------
  // Экран чата фиксирован; реальную видимую высоту берём из visualViewport,
  // чтобы поле ввода всегда было над клавиатурой без зазоров.
  function syncAppHeight() {
    var vv = window.visualViewport;
    var h = (vv && vv.height) || window.innerHeight;
    var top = (vv && vv.offsetTop) || 0;
    var s = document.documentElement.style;
    s.setProperty('--app-height', Math.round(h) + 'px');
    s.setProperty('--vv-top', Math.round(top) + 'px');
  }
  syncAppHeight();
  if (window.visualViewport) {
    window.visualViewport.addEventListener('resize', syncAppHeight);
    window.visualViewport.addEventListener('scroll', syncAppHeight);
  }
  window.addEventListener('resize', syncAppHeight);
  window.addEventListener('orientationchange', function () { setTimeout(syncAppHeight, 250); });
  // Клавиатура на iOS появляется/скрывается с задержкой — досчитываем после события фокуса.
  document.addEventListener('focusin', function () { setTimeout(syncAppHeight, 100); });
  document.addEventListener('focusout', function () { setTimeout(syncAppHeight, 100); });

  // --- тема -------------------------------------------------------------------
  function effectiveTheme() {
    var saved = localStorage.getItem('chat_theme');
    if (saved === 'light' || saved === 'dark') return saved;
    return (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches) ? 'light' : 'dark';
  }
  function applyThemeIcon() {
    var light = effectiveTheme() === 'light';
    var moon = els.themeBtn && els.themeBtn.querySelector('.ic-moon');
    var sun = els.themeBtn && els.themeBtn.querySelector('.ic-sun');
    // В тёмной теме показываем солнце (тап → светлая); в светлой — луну.
    if (moon) moon.hidden = !light;
    if (sun) sun.hidden = light;
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', light ? '#f6f8f7' : '#131618');
  }
  function toggleTheme() {
    var next = effectiveTheme() === 'dark' ? 'light' : 'dark';
    localStorage.setItem('chat_theme', next);
    document.documentElement.setAttribute('data-theme', next);
    applyThemeIcon();
  }
  if (els.themeBtn) els.themeBtn.addEventListener('click', toggleTheme);
  applyThemeIcon();

  // --- api --------------------------------------------------------------------

  function req(method, path, body, withAuth, retry) {
    var headers = { 'Content-Type': 'application/json' };
    if (withAuth && state.access) headers.Authorization = 'Bearer ' + state.access;
    return fetch(API + path, {
      method: method,
      headers: headers,
      body: body ? JSON.stringify(body) : undefined
    }).then(function (res) {
      if (res.status === 401 && withAuth && state.refresh && !retry) {
        return refreshTokens().then(function (ok) {
          if (!ok) { doLogout(); throw new Error('session'); }
          return req(method, path, body, withAuth, true);
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
    return fetch(API + '/refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: state.refresh })
    }).then(function (res) {
      if (!res.ok) return false;
      return res.json().then(function (data) { saveTokens(data); return true; });
    }).catch(function () { return false; });
  }

  function saveTokens(data) {
    state.access = data.access_token;
    state.refresh = data.refresh_token;
    state.profile = data.profile || state.profile;
    localStorage.setItem('chat_access', state.access);
    localStorage.setItem('chat_refresh', state.refresh);
  }

  // --- вход / выход -----------------------------------------------------------

  els.form.addEventListener('submit', function (e) {
    e.preventDefault();
    els.loginError.hidden = true;
    els.loginBtn.disabled = true;
    els.loginBtn.textContent = 'Вхожу…';
    req('POST', '/login', {
      username: els.username.value.trim(),
      password: els.password.value
    }, false).then(function (data) {
      saveTokens(data);
      els.password.value = '';
      enterChat();
    }).catch(function (err) {
      els.loginError.textContent = err.message || 'Не удалось войти.';
      els.loginError.hidden = false;
    }).finally(function () {
      els.loginBtn.disabled = false;
      els.loginBtn.textContent = 'Войти';
    });
  });

  els.logoutBtn.addEventListener('click', function () {
    disablePush();
    if (state.refresh) {
      req('POST', '/logout', { refresh_token: state.refresh }, false).catch(function () {});
    }
    doLogout();
  });

  // --- кабинет «Мой VPN» ------------------------------------------------------

  els.vpnBtn.addEventListener('click', openVpnSheet);
  els.vpnClose.addEventListener('click', closeVpnSheet);
  els.vpnSheet.addEventListener('click', function (e) {
    if (e.target === els.vpnSheet) closeVpnSheet();
  });

  function closeVpnSheet() { els.vpnSheet.hidden = true; }

  function openVpnSheet() {
    els.vpnSheet.hidden = false;
    els.vpnBody.innerHTML = '<div class="vpn-loading"><span class="muted">Загрузка…</span></div>';
    req('GET', '/me/vpn', null, true).then(renderVpn).catch(function () {
      els.vpnBody.innerHTML = '<p class="muted">Не удалось загрузить данные. Попробуйте позже.</p>';
    });
  }

  function fmtBytes(n) {
    n = Number(n || 0);
    if (n < 1024) return n + ' Б';
    var u = ['КБ', 'МБ', 'ГБ', 'ТБ'], i = -1;
    do { n /= 1024; i++; } while (n >= 1024 && i < u.length - 1);
    return (n >= 100 ? Math.round(n) : n.toFixed(1)) + ' ' + u[i];
  }

  function fmtDate(iso) {
    if (!iso) return null;
    try {
      return new Date(iso).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' });
    } catch (e) { return null; }
  }

  function fmtRub(kop) {
    if (!kop) return '';
    var r = kop / 100;
    return (kop % 100 === 0 ? String(r) : r.toFixed(2)) + ' ₽';
  }

  function renderVpn(d) {
    if (!d || !d.linked) {
      els.vpnBody.innerHTML =
        '<p class="muted">VPN-доступ ещё не привязан к вашему аккаунту. Обратитесь в поддержку — напишите сообщение в чат.</p>';
      return;
    }
    var rows = [];
    var expDate = fmtDate(d.expires_at);
    if (expDate) {
      var left = (d.days_left != null)
        ? (d.days_left === 0 ? 'срок истёк' : 'осталось ' + d.days_left + ' ' + plural(d.days_left, 'день', 'дня', 'дней'))
        : '';
      rows.push(vpnRow('Срок действия', expDate + (left ? ' · ' + left : '')));
    } else {
      rows.push(vpnRow('Срок действия', 'бессрочно'));
    }

    var trafficLine, bar = '';
    if (d.traffic_limit_bytes) {
      var pct = Math.min(100, Math.round((d.traffic_used_bytes / d.traffic_limit_bytes) * 100));
      trafficLine = fmtBytes(d.traffic_used_bytes) + ' из ' + fmtBytes(d.traffic_limit_bytes) + ' · ' + pct + '%';
      bar = '<div class="vpn-bar"><span style="width:' + pct + '%"></span></div>';
    } else {
      trafficLine = fmtBytes(d.traffic_used_bytes) + ' (без лимита)';
    }
    rows.push(vpnRow('Трафик', trafficLine) + bar);

    var html = '<div class="vpn-rows">' + rows.join('') + '</div>';

    if (d.billing_mode === 'paid' && d.billing_amount_kopecks) {
      var per = d.billing_period_months === 1 ? 'мес.' : (d.billing_period_months + ' мес.');
      html += '<div class="vpn-tariff">';
      html += '<div class="vpn-tariff-row"><span>Тариф</span><strong>' + fmtRub(d.billing_amount_kopecks) + ' / ' + per + '</strong></div>';
      if (d.yookassa_available && d.can_self_pay) {
        html += '<button id="vpn-pay" type="button" class="vpn-pay-btn">Продлить за ' + fmtRub(d.billing_amount_kopecks) + '</button>';
        html += '<p class="vpn-note muted">Самостоятельных оплат в этом месяце: ' + (3 - d.self_pay_remaining) + ' из 3</p>';
      } else if (d.yookassa_available && d.self_pay_remaining <= 0) {
        html += '<p class="vpn-note muted">Лимит самостоятельных оплат на этот месяц исчерпан. Обратитесь в поддержку.</p>';
      } else if (!d.yookassa_available) {
        html += '<p class="vpn-note muted">Для продления напишите в поддержку.</p>';
      }
      html += '</div>';
    }

    els.vpnBody.innerHTML = html;
    var payBtn = document.getElementById('vpn-pay');
    if (payBtn) payBtn.addEventListener('click', function () { requestPayment(payBtn); });
  }

  function vpnRow(label, value) {
    return '<div class="vpn-row"><span class="vpn-label">' + label + '</span><span class="vpn-val">' + value + '</span></div>';
  }

  function plural(n, one, few, many) {
    var m10 = n % 10, m100 = n % 100;
    if (m10 === 1 && m100 !== 11) return one;
    if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return few;
    return many;
  }

  function requestPayment(btn) {
    btn.disabled = true;
    btn.textContent = 'Создаю счёт…';
    req('POST', '/me/request-payment', {}, true).then(function (res) {
      closeVpnSheet();
      poll(false);
      if (res && res.pay_url) window.open(res.pay_url, '_blank', 'noopener');
    }).catch(function (err) {
      btn.disabled = false;
      btn.textContent = 'Продлить';
      alert(err && err.message ? err.message : 'Не удалось создать счёт.');
    });
  }

  function doLogout() {
    localStorage.removeItem('chat_access');
    localStorage.removeItem('chat_refresh');
    state.access = '';
    state.refresh = '';
    state.profile = null;
    state.lastId = 0;
    state.lastDateKey = '';
    if (state.pollTimer) clearInterval(state.pollTimer);
    clearBadge();
    els.messages.querySelectorAll('.msg, .day').forEach(function (n) { n.remove(); });
    els.banner.hidden = true;
    els.vpnSheet.hidden = true;
    els.chatScreen.hidden = true;
    els.loginScreen.hidden = false;
  }

  function enterChat() {
    els.loginScreen.hidden = true;
    els.chatScreen.hidden = false;
    state.lastId = 0;
    state.lastDateKey = '';
    state.nearBottom = true;
    clearBadge();
    poll(true);
    if (state.pollTimer) clearInterval(state.pollTimer);
    state.pollTimer = setInterval(function () { poll(false); }, POLL_MS);
    els.draft.focus();
    refreshPwaUi();
  }

  // --- сообщения --------------------------------------------------------------

  function setConn(ok) {
    state.online = ok;
    els.connStatus.textContent = ok ? 'в сети' : 'нет соединения…';
    els.connStatus.classList.toggle('off', !ok);
  }

  function poll(initial) {
    req('GET', '/messages?after_id=' + state.lastId + '&limit=200', null, true)
      .then(function (data) {
        setConn(true);
        if (data.messages && data.messages.length) {
          var hadAdmin = false;
          data.messages.forEach(function (m) {
            appendMessage(m);
            if (m.sender !== 'client') hadAdmin = true;
          });
          if (state.nearBottom || initial) {
            scrollDown(true);
          } else {
            updateScrollBtn();
          }
          if (hadAdmin && document.hidden) bumpBadge(data.messages.filter(function (m) {
            return m.sender !== 'client';
          }).length);
        }
        updateEmpty();
      })
      .catch(function (err) {
        if (err && err.message === 'session') return;
        setConn(false);
      });
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
    state.lastId = Math.max(state.lastId, m.id);

    var when = null;
    try { when = new Date(m.created_at); } catch (e) { when = null; }
    if (when && !isNaN(when.getTime())) {
      var key = when.toDateString();
      if (key !== state.lastDateKey) {
        state.lastDateKey = key;
        var sep = document.createElement('div');
        sep.className = 'day';
        var chip = document.createElement('span');
        chip.textContent = dayLabel(when);
        sep.appendChild(chip);
        els.messages.appendChild(sep);
      }
    }

    var div = document.createElement('div');
    div.className = 'msg ' + (m.sender === 'client' ? 'client' : m.sender);
    var text = document.createElement('div');
    text.className = 'msg-text';
    linkify(text, m.body);
    div.appendChild(text);
    if (m.attachment) div.appendChild(buildAttachment(m.attachment));
    var time = document.createElement('span');
    time.className = 'time';
    try {
      time.textContent = when.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
    } catch (e2) { time.textContent = ''; }
    div.appendChild(time);
    els.messages.appendChild(div);
  }

  // Безопасно вставляет текст и делает http(s)-ссылки кликабельными.
  function linkify(node, body) {
    var str = String(body == null ? '' : body);
    var re = /(https?:\/\/[^\s<>"']+)/g;
    var last = 0, match;
    while ((match = re.exec(str)) !== null) {
      if (match.index > last) {
        node.appendChild(document.createTextNode(str.slice(last, match.index)));
      }
      var url = match[0];
      var tail = '';
      // не «съедаем» завершающую пунктуацию ссылки
      while (/[.,!?)\]}»]$/.test(url)) { tail = url.slice(-1) + tail; url = url.slice(0, -1); }
      var a = document.createElement('a');
      a.href = url;
      a.textContent = url;
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      node.appendChild(a);
      if (tail) node.appendChild(document.createTextNode(tail));
      last = match.index + match[0].length;
    }
    if (last < str.length) node.appendChild(document.createTextNode(str.slice(last)));
  }

  function updateEmpty() {
    els.messagesEmpty.hidden = !!els.messages.querySelector('.msg');
  }

  // --- прокрутка --------------------------------------------------------------

  function atBottom() {
    var gap = els.messages.scrollHeight - els.messages.scrollTop - els.messages.clientHeight;
    return gap < 80;
  }

  function scrollDown(smooth) {
    els.messages.scrollTo({ top: els.messages.scrollHeight, behavior: smooth ? 'smooth' : 'auto' });
    state.nearBottom = true;
    state.unread = 0;
    updateScrollBtn();
  }

  function updateScrollBtn() {
    els.scrollDown.hidden = state.nearBottom;
  }

  els.messages.addEventListener('scroll', function () {
    state.nearBottom = atBottom();
    updateScrollBtn();
  });
  els.scrollDown.addEventListener('click', function () { scrollDown(true); });

  // --- бейдж непрочитанных ----------------------------------------------------

  function bumpBadge(n) {
    state.unread += (n || 1);
    if (navigator.setAppBadge) navigator.setAppBadge(state.unread).catch(function () {});
  }
  function clearBadge() {
    state.unread = 0;
    if (navigator.clearAppBadge) navigator.clearAppBadge().catch(function () {});
    // Закрываем уже показанные push-уведомления — иначе на Android значок
    // на иконке приложения остаётся, даже когда чат прочитан.
    if (navigator.serviceWorker && navigator.serviceWorker.ready) {
      navigator.serviceWorker.ready.then(function (reg) {
        if (reg && reg.getNotifications) {
          reg.getNotifications().then(function (list) {
            list.forEach(function (n) { n.close(); });
          }).catch(function () {});
        }
      }).catch(function () {});
    }
  }
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) clearBadge();
  });
  window.addEventListener('focus', function () {
    if (!els.chatScreen.hidden) clearBadge();
  });

  // --- отправка ---------------------------------------------------------------

  function send() {
    var body = els.draft.value.trim();
    if (!body || state.sending) return;
    state.sending = true;
    els.sendBtn.disabled = true;
    req('POST', '/messages', { body: body }, true).then(function (m) {
      els.draft.value = '';
      autosize();
      appendMessage(m);
      updateEmpty();
      scrollDown(true);
    }).catch(function (err) {
      alert(err.message || 'Не удалось отправить.');
    }).finally(function () {
      state.sending = false;
      els.sendBtn.disabled = false;
      els.draft.focus();
    });
  }

  els.sendBtn.addEventListener('click', send);
  els.draft.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  });

  function autosize() {
    els.draft.style.height = 'auto';
    els.draft.style.height = Math.min(els.draft.scrollHeight, 130) + 'px';
  }
  els.draft.addEventListener('input', autosize);

  // --- вложения (ключ подключения) --------------------------------------------

  function attButton(label, primary, handler) {
    var b = document.createElement('button');
    b.type = 'button';
    b.className = 'att-btn' + (primary ? ' primary' : '');
    b.textContent = label;
    b.addEventListener('click', function () { handler(b); });
    return b;
  }

  function buildAttachment(att) {
    var box = document.createElement('div');
    box.className = 'att';
    if (att.expired) {
      box.classList.add('expired');
      box.textContent = '🔑 ' + att.filename + ' — срок действия истёк, попросите ключ заново';
      return box;
    }
    var title = document.createElement('div');
    title.className = 'att-title';
    title.textContent = '🔑 Ключ подключения · ' + att.filename;
    box.appendChild(title);

    var row = document.createElement('div');
    row.className = 'att-actions';
    row.appendChild(attButton('Просмотр', false, function (btn) { showText(att, btn); }));
    row.appendChild(attButton('Скачать', false, function (btn) { downloadAttachment(att, btn); }));
    row.appendChild(attButton('QR', false, function (btn) { showQr(att, btn); }));
    box.appendChild(row);
    return box;
  }

  function authFetch(path) {
    return fetch(API + path, {
      headers: { Authorization: 'Bearer ' + state.access }
    }).then(function (res) {
      if (res.status === 401 && state.refresh) {
        return refreshTokens().then(function (ok) {
          if (!ok) { doLogout(); throw new Error('session'); }
          return fetch(API + path, { headers: { Authorization: 'Bearer ' + state.access } });
        });
      }
      return res;
    });
  }

  function fetchAttachmentFile(att) {
    return authFetch('/attachments/' + att.id + '/file').then(function (res) {
      if (!res.ok) {
        return res.json().catch(function () { return {}; }).then(function (d) {
          throw new Error(d.detail || 'Не удалось получить файл.');
        });
      }
      return res.arrayBuffer();
    }).then(function (buf) {
      return new File([buf], att.filename, { type: 'application/octet-stream' });
    });
  }

  function downloadAttachment(att, btn) {
    btn.disabled = true;
    fetchAttachmentFile(att).then(function (file) {
      var a = document.createElement('a');
      a.href = URL.createObjectURL(file);
      a.download = att.filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(function () { URL.revokeObjectURL(a.href); }, 5000);
    }).catch(function (err) {
      alert(err.message || 'Не удалось скачать.');
    }).finally(function () { btn.disabled = false; });
  }

  function showQr(att, btn) {
    btn.disabled = true;
    authFetch('/attachments/' + att.id + '/view').then(function (res) {
      return res.json().then(function (data) {
        if (!res.ok) throw new Error(data.detail || 'Не удалось открыть.');
        openQrOverlay(data);
      });
    }).catch(function (err) {
      alert(err.message || 'Не удалось открыть.');
    }).finally(function () { btn.disabled = false; });
  }

  function showText(att, btn) {
    btn.disabled = true;
    authFetch('/attachments/' + att.id + '/view').then(function (res) {
      return res.json().then(function (data) {
        if (!res.ok) throw new Error(data.detail || 'Не удалось открыть.');
        openTextOverlay(att, data);
      });
    }).catch(function (err) {
      alert(err.message || 'Не удалось открыть.');
    }).finally(function () { btn.disabled = false; });
  }

  function openTextOverlay(att, data) {
    var variants = [];
    if (data.config_text) variants.push({ id: 'awg', label: 'AmneziaWG', text: data.config_text, ext: '.txt' });
    if (data.vpn_link) variants.push({ id: 'vpn', label: 'AmneziaVPN', text: data.vpn_link, ext: '-vpn.txt' });
    if (!variants.length) { alert('Текст конфигурации недоступен для этого ключа.'); return; }

    var current = variants[0];
    var overlay = document.createElement('div');
    overlay.className = 'qr-overlay';
    var card = document.createElement('div');
    card.className = 'qr-card text-card';

    var h = document.createElement('strong');
    card.appendChild(h);

    var tabs = document.createElement('div');
    tabs.className = 'att-actions qr-tabs';
    card.appendChild(tabs);
    if (variants.length < 2) tabs.style.display = 'none';

    var pre = document.createElement('pre');
    pre.className = 'conf-text';
    card.appendChild(pre);

    function activate(v) {
      current = v;
      h.textContent = v.id === 'awg' ? 'Конфиг для AmneziaWG / WireGuard' : 'Ссылка для AmneziaVPN';
      pre.textContent = v.text;
      tabs.querySelectorAll('button').forEach(function (b) {
        b.classList.toggle('primary', b.dataset.mode === v.id);
      });
    }

    variants.forEach(function (v) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'att-btn';
      b.dataset.mode = v.id;
      b.textContent = v.label;
      b.addEventListener('click', function () { activate(v); });
      tabs.appendChild(b);
    });
    activate(current);

    var row = document.createElement('div');
    row.className = 'att-actions';

    var copy = document.createElement('button');
    copy.type = 'button';
    copy.className = 'att-btn primary';
    copy.textContent = 'Скопировать';
    copy.addEventListener('click', function () {
      var done = function () {
        copy.textContent = 'Скопировано ✓';
        setTimeout(function () { copy.textContent = 'Скопировать'; }, 2000);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(current.text).then(done).catch(function () { fallbackCopy(pre); done(); });
      } else { fallbackCopy(pre); done(); }
    });
    row.appendChild(copy);

    var save = document.createElement('button');
    save.type = 'button';
    save.className = 'att-btn';
    save.textContent = 'Сохранить как текст';
    save.addEventListener('click', function () {
      var blob = new Blob([current.text], { type: 'text/plain;charset=utf-8' });
      var a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = att.filename.replace(/\.conf$/, '') + current.ext;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(function () { URL.revokeObjectURL(a.href); }, 5000);
    });
    row.appendChild(save);

    var close = document.createElement('button');
    close.type = 'button';
    close.className = 'att-btn';
    close.textContent = 'Закрыть';
    close.addEventListener('click', function () { overlay.remove(); });
    row.appendChild(close);

    card.appendChild(row);
    overlay.addEventListener('click', function (e) { if (e.target === overlay) overlay.remove(); });
    overlay.appendChild(card);
    document.body.appendChild(overlay);
  }

  function fallbackCopy(pre) {
    var range = document.createRange();
    range.selectNodeContents(pre);
    var sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    try { document.execCommand('copy'); } catch (e) { /* ignore */ }
    sel.removeAllRanges();
  }

  function openQrOverlay(data) {
    var qrAwg = data.qr_awg_data_url || (data.has_conf ? data.qr_data_url : null);
    var qrVpn = data.qr_vpn_data_url || (!data.has_conf ? data.qr_data_url : null);

    var overlay = document.createElement('div');
    overlay.className = 'qr-overlay';
    var card = document.createElement('div');
    card.className = 'qr-card';

    var h = document.createElement('strong');
    card.appendChild(h);

    var tabs = document.createElement('div');
    tabs.className = 'att-actions qr-tabs';
    card.appendChild(tabs);

    var img = document.createElement('img');
    img.alt = 'QR';
    card.appendChild(img);

    var hint = document.createElement('span');
    hint.className = 'qr-hint';
    card.appendChild(hint);

    function activate(mode) {
      tabs.querySelectorAll('button').forEach(function (b) {
        b.classList.toggle('primary', b.dataset.mode === mode);
      });
      if (mode === 'awg') {
        h.textContent = 'QR для AmneziaWG / WireGuard';
        img.src = qrAwg;
        hint.textContent = 'AmneziaWG → «+» → «Сканировать QR-код»';
      } else {
        h.textContent = 'QR для AmneziaVPN';
        img.src = qrVpn;
        hint.textContent = 'AmneziaVPN → «+» → «QR-код»';
      }
    }

    function addTab(label, mode) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'att-btn';
      b.dataset.mode = mode;
      b.textContent = label;
      b.addEventListener('click', function () { activate(mode); });
      tabs.appendChild(b);
    }

    if (qrAwg) addTab('AmneziaWG', 'awg');
    if (qrVpn) addTab('AmneziaVPN', 'vpn');
    if (qrAwg) activate('awg');
    else if (qrVpn) activate('vpn');
    if ((qrAwg && !qrVpn) || (!qrAwg && qrVpn)) tabs.style.display = 'none';

    var row = document.createElement('div');
    row.className = 'att-actions';
    var close = document.createElement('button');
    close.type = 'button';
    close.className = 'att-btn';
    close.textContent = 'Закрыть';
    close.addEventListener('click', function () { overlay.remove(); });
    row.appendChild(close);
    card.appendChild(row);

    overlay.addEventListener('click', function (e) { if (e.target === overlay) overlay.remove(); });
    overlay.appendChild(card);
    document.body.appendChild(overlay);
  }

  // --- PWA: установка ---------------------------------------------------------

  window.addEventListener('beforeinstallprompt', function (e) {
    e.preventDefault();
    state.deferredPrompt = e;
    refreshPwaUi();
  });

  window.addEventListener('appinstalled', function () {
    state.deferredPrompt = null;
    dismiss('install');
    refreshPwaUi();
  });

  function triggerInstall() {
    if (state.deferredPrompt) {
      state.deferredPrompt.prompt();
      state.deferredPrompt.userChoice.finally(function () {
        state.deferredPrompt = null;
        refreshPwaUi();
      });
    } else if (isIos) {
      els.iosHelp.hidden = false;
    }
  }

  els.installBtn.addEventListener('click', triggerInstall);
  els.iosHelpClose.addEventListener('click', function () { els.iosHelp.hidden = true; });
  els.iosHelp.addEventListener('click', function (e) { if (e.target === els.iosHelp) els.iosHelp.hidden = true; });

  // --- PWA: уведомления -------------------------------------------------------

  function pushPermission() {
    return (typeof Notification !== 'undefined') ? Notification.permission : 'unsupported';
  }

  els.notifBtn.addEventListener('click', function () {
    if (pushPermission() === 'denied') {
      alert('Уведомления отключены в настройках браузера/телефона. Включите их для этого сайта.');
      return;
    }
    enablePush().then(function (ok) {
      if (ok) dismiss('notif');
      refreshPwaUi();
    });
  });

  function enablePush() {
    if (!state.pushSupported || !state.vapidKey) return Promise.resolve(false);
    return Notification.requestPermission().then(function (perm) {
      if (perm !== 'granted') return false;
      return navigator.serviceWorker.ready.then(function (reg) {
        return reg.pushManager.getSubscription().then(function (existing) {
          if (existing) return existing;
          return reg.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: urlB64ToUint8Array(state.vapidKey)
          });
        });
      }).then(function (sub) {
        var j = sub.toJSON();
        return req('POST', '/push/subscribe', {
          endpoint: sub.endpoint,
          p256dh: j.keys ? j.keys.p256dh : '',
          auth: j.keys ? j.keys.auth : ''
        }, true).then(function () { return true; });
      });
    }).catch(function () { return false; });
  }

  function disablePush() {
    if (!('serviceWorker' in navigator)) return;
    navigator.serviceWorker.ready.then(function (reg) {
      return reg.pushManager.getSubscription();
    }).then(function (sub) {
      if (!sub) return;
      var endpoint = sub.endpoint;
      sub.unsubscribe().catch(function () {});
      req('POST', '/push/unsubscribe', { endpoint: endpoint }, false).catch(function () {});
    }).catch(function () {});
  }

  // --- баннер-подсказка -------------------------------------------------------

  function dismissed(kind) { return localStorage.getItem('chat_dismiss_' + kind) === '1'; }
  function dismiss(kind) { localStorage.setItem('chat_dismiss_' + kind, '1'); els.banner.hidden = true; }

  els.bannerClose.addEventListener('click', function () {
    if (els.banner.dataset.kind) dismiss(els.banner.dataset.kind);
  });

  function showBanner(kind, text, actionLabel, handler) {
    els.banner.dataset.kind = kind;
    els.bannerText.textContent = text;
    els.bannerAction.textContent = actionLabel;
    els.bannerAction.onclick = handler;
    els.banner.hidden = false;
  }

  function refreshPwaUi() {
    var standalone = isStandalone();
    var canInstall = !standalone && (!!state.deferredPrompt || isIos);

    els.installBtn.hidden = !canInstall;

    var perm = pushPermission();
    var notifPossible = state.pushSupported && !!state.vapidKey;
    // На iOS push доступен только в установленном PWA
    if (isIos && !standalone) notifPossible = false;
    // Колокольчик нужен только чтобы ВКЛЮЧИТЬ уведомления.
    // Если уже разрешены — прячем (клиенту больше нечего нажимать).
    els.notifBtn.hidden = !notifPossible || perm === 'granted';

    // Контекстный баннер: сначала установка, затем уведомления
    if (canInstall && !dismissed('install')) {
      showBanner('install',
        'Установите приложение на телефон — быстрый доступ и уведомления.',
        'Установить', triggerInstall);
    } else if (notifPossible && perm === 'default' && !dismissed('notif')) {
      showBanner('notif',
        'Включите уведомления о новых сообщениях от поддержки.',
        'Включить', function () {
          enablePush().then(function (ok) { if (ok) dismiss('notif'); refreshPwaUi(); });
        });
    } else {
      els.banner.hidden = true;
    }
  }

  // --- service worker ---------------------------------------------------------

  function initServiceWorker() {
    if (!('serviceWorker' in navigator)) return;
    navigator.serviceWorker.register('/sw.js?v=12').catch(function () {});
    navigator.serviceWorker.addEventListener('message', function (e) {
      if (!e.data) return;
      if (e.data.type === 'open-chat') {
        clearBadge();
        if (!els.chatScreen.hidden) scrollDown(true);
      } else if (e.data.type === 'push-msg') {
        // Пришло новое сообщение, а чат открыт — подтянем сразу.
        if (!els.chatScreen.hidden) {
          poll(false);
          if (!document.hidden) clearBadge();
        }
      }
    });
    state.pushSupported = ('PushManager' in window) && (typeof Notification !== 'undefined');
  }

  function loadConfig() {
    return fetch(API + '/config').then(function (r) { return r.json(); })
      .then(function (cfg) {
        state.vapidKey = (cfg && cfg.push_enabled && cfg.vapid_public_key) ? cfg.vapid_public_key : '';
      }).catch(function () {});
  }

  // --- старт ------------------------------------------------------------------

  initServiceWorker();
  loadConfig();

  if (state.refresh) {
    refreshTokens().then(function (ok) {
      if (ok) {
        req('GET', '/me', null, true).then(function (profile) {
          state.profile = profile;
          enterChat();
        }).catch(doLogout);
      } else {
        doLogout();
      }
    });
  }
})();
