"""CH1: домен чата + изоляция nginx (gate перед включением Chat Mini-App).

Наружу через chat-домен выходит ТОЛЬКО клиентский mini-app (статика) и узкий
префикс `/api/v1/chat/client/` (сам API появится в CH2). Админка, panel API,
docs через чат-домен недоступны (404). Без домена и HTTPS чат выключен.

Предусловия (fail-closed): HTTPS панели активен И :8080 ограничен (HARDEN).
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from app.services.panel_harden import CHAIN as HARDEN_CHAIN
from app.services.panel_ssl import (
    CERT_DIR,
    PANEL_HTTPS_INTERNAL,
    STREAM_CONF,
    WEBROOT,
    XRAY_LOCAL_PORT,
    PanelSslError,
    _cert_expiry,
    _connect,
    _normalize_domain,
    _port_80_available,
    _render_template,
    _resolve_domain_ips,
    _server_public_ip,
    magic_domain_for_ip,
)
from app.services.server_store import server_store
from app.ssh import exec as ssh_exec

CHAT_NGINX_SITE = "/etc/nginx/sites-available/utmka-chat"
CHAT_NGINX_SITE_ENABLED = "/etc/nginx/sites-enabled/utmka-chat"
CHAT_ROOT = "/opt/utmka-awg/chat-frontend"
CHAT_UPSTREAM = "http://127.0.0.1:8080"
CHAT_BACKUP_ROOT = "/opt/utmka/chat-ssl-backup"
# Когда :443 занят SNI-passthrough (Xray), чат-vhost слушает локальный порт,
# а stream-блок маршрутизирует чат-SNI сюда (по аналогии с панелью на 8443).
CHAT_HTTPS_INTERNAL = 8444

# Временная страница до CH2 (реальный mini-app). Создаётся, только если каталога нет.
PLACEHOLDER_HTML = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Чат поддержки</title>
<style>
  body { margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
         background:#131618; color:#e6e9e6; font:16px/1.5 -apple-system, system-ui, sans-serif; }
  .card { text-align:center; padding:40px; }
  h1 { font-size:20px; margin:0 0 8px; }
  p { color:#9aa39c; margin:0; }
</style>
</head>
<body>
  <div class="card">
    <h1>Чат поддержки</h1>
    <p>Сервис скоро будет доступен. Если у вас есть логин — зайдите позже.</p>
  </div>
</body>
</html>"""


class ChatDomainError(Exception):
    pass


@dataclass
class ChatDomainState:
    domain: Optional[str]
    enabled: bool
    ssl_status: str  # not_configured | dns_pending | cert_active | error | disabled
    public_url: Optional[str]
    cert_expires_at: Optional[str]
    panel_https_active: bool
    harden_active: bool
    server_public_ip: Optional[str]
    dns_record_hint: Optional[str]
    message: Optional[str] = None


@dataclass
class ChatDomainVerifyResult:
    ok: bool
    domain: str
    resolved_ips: list[str]
    server_public_ip: Optional[str]
    message: str


@dataclass
class ChatDomainInstallResult:
    ok: bool
    domain: str
    public_url: str
    isolation: list[dict]
    message: str


def get_chat_domain_state(server_id: str) -> ChatDomainState:
    record = server_store.get_record(server_id)
    target = server_store.ssh_target(server_id)
    if not record or not target:
        raise ChatDomainError("Сервер не найден.")

    stored = record.get("chat_domain") or {}
    panel_https = (record.get("panel_ssl") or {}).get("status") == "active"

    try:
        ssh = _connect(target)
    except Exception as exc:  # noqa: BLE001
        return ChatDomainState(
            domain=stored.get("domain"),
            enabled=bool(stored.get("enabled")),
            ssl_status=stored.get("ssl_status", "not_configured"),
            public_url=stored.get("public_url"),
            cert_expires_at=stored.get("cert_expires_at"),
            panel_https_active=panel_https,
            harden_active=False,
            server_public_ip=record.get("host"),
            dns_record_hint=None,
            message=f"SSH не отвечает: {exc}",
        )

    try:
        public_ip = _server_public_ip(ssh) or record.get("host")
        harden_active = _harden_active(ssh)
        vhost_present = (
            ssh_exec.run(ssh, f"test -f {shlex.quote(CHAT_NGINX_SITE_ENABLED)}", timeout=10).exit_code == 0
        )
        enabled = bool(stored.get("enabled")) and vhost_present
        return ChatDomainState(
            domain=stored.get("domain"),
            enabled=enabled,
            ssl_status=stored.get("ssl_status", "not_configured") if enabled or not stored.get("enabled") else "error",
            public_url=stored.get("public_url") if enabled else None,
            cert_expires_at=stored.get("cert_expires_at"),
            panel_https_active=panel_https,
            harden_active=harden_active,
            server_public_ip=public_ip,
            dns_record_hint=f"A-запись: <поддомен>  A  {public_ip}" if public_ip else None,
        )
    finally:
        ssh.close()


def verify_chat_domain(server_id: str, domain: str) -> ChatDomainVerifyResult:
    try:
        domain = _normalize_domain(domain)
    except PanelSslError as exc:
        raise ChatDomainError(str(exc))
    record = server_store.get_record(server_id)
    target = server_store.ssh_target(server_id)
    if not record or not target:
        raise ChatDomainError("Сервер не найден.")

    panel_domain = ((record.get("panel_ssl") or {}).get("domain") or "").lower()
    if panel_domain and domain == panel_domain:
        raise ChatDomainError(
            "Домен чата должен отличаться от домена панели — используйте поддомен, например "
            f"chat.{panel_domain}"
        )

    ssh = _connect(target)
    try:
        if (record.get("panel_ssl") or {}).get("status") != "active":
            return _verify_fail(domain, [], None, "Сначала включите HTTPS для панели (блок выше).")

        if not _harden_active(ssh):
            return _verify_fail(
                domain, [], None,
                "Сначала ограничьте :8080 (блок «Аварийный вход :8080») — это предусловие чата.",
            )

        public_ip = _server_public_ip(ssh) or record.get("host")
        resolved = _resolve_domain_ips(domain)
        if not resolved:
            return _verify_fail(
                domain, [], public_ip,
                f"Домен не резолвится. Добавьте у регистратора A-запись «{domain.split('.')[0]}  A  {public_ip}» "
                "и подождите 5–30 минут.",
            )
        if public_ip and public_ip not in resolved:
            return _verify_fail(
                domain, resolved, public_ip,
                f"DNS указывает на {', '.join(resolved)}, а IP сервера — {public_ip}.",
            )
        if not _port_80_available(ssh):
            return _verify_fail(domain, resolved, public_ip, "Порт 80 занят не-nginx процессом.")

        return ChatDomainVerifyResult(
            ok=True,
            domain=domain,
            resolved_ips=resolved,
            server_public_ip=public_ip,
            message="DNS в порядке — можно подключать чат-домен.",
        )
    finally:
        ssh.close()


def install_chat_domain(server_id: str, domain: str) -> ChatDomainInstallResult:
    verify = verify_chat_domain(server_id, domain)
    if not verify.ok:
        raise ChatDomainError(verify.message)
    domain = verify.domain

    record = server_store.get_record(server_id)
    target = server_store.ssh_target(server_id)
    if not record or not target:
        raise ChatDomainError("Сервер не найден.")

    panel_ssl = record.get("panel_ssl") or {}
    passthrough = bool(panel_ssl.get("xray_passthrough"))
    panel_domain = (panel_ssl.get("domain") or "").lower()
    if passthrough and not panel_domain:
        raise ChatDomainError(
            "На :443 включён passthrough Xray, но домен панели не определён — "
            "переустановите HTTPS панели и повторите."
        )

    ssh = _connect(target)
    try:
        backup_dir = f"{CHAT_BACKUP_ROOT}/{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
        script = _build_install_script(
            domain=domain,
            backup_dir=backup_dir,
            passthrough=passthrough,
            panel_domain=panel_domain,
        )
        result = ssh_exec.run(ssh, f"bash -s <<'UTMKA_CHAT_EOF'\n{script}\nUTMKA_CHAT_EOF", timeout=600)
        output = (result.stdout + "\n" + result.stderr).strip()
        if result.exit_code != 0 or "UTMKA_CHAT_OK" not in output:
            # nginx восстанавливается внутри скрипта (trap), здесь только честная ошибка
            raise ChatDomainError(f"Подключение чат-домена не удалось:\n{output[-1200:]}")

        isolation = _isolation_checks(ssh, domain)
        failed = [c for c in isolation if not c["ok"]]
        if failed:
            _remove_vhost(ssh, passthrough=passthrough, panel_domain=panel_domain)
            labels = "; ".join(c["label"] for c in failed)
            raise ChatDomainError(
                f"Проверка изоляции не пройдена ({labels}) — vhost чата отключён, всё откатил."
            )

        public_url = f"https://{domain}"
        cert_expires = _cert_expiry(ssh, domain)
        server_store.update_runtime(
            server_id,
            chat_domain={
                "domain": domain,
                "enabled": True,
                "ssl_status": "cert_active",
                "public_url": public_url,
                "cert_expires_at": cert_expires,
                "installed_at": datetime.now(timezone.utc).isoformat(),
                "backup_dir": backup_dir,
            },
        )
        return ChatDomainInstallResult(
            ok=True,
            domain=domain,
            public_url=public_url,
            isolation=isolation,
            message="Чат-домен подключён. Наружу отдаётся только mini-app; админка через него недоступна.",
        )
    finally:
        ssh.close()


def chat_magic_domain(public_ip: Optional[str]) -> str:
    """`chat.<ip>.sslip.io` — отдельное имя на том же IP (отличается от домена панели).

    sslip.io резолвит встроенный IP даже с префиксом, поэтому это валидный
    отдельный хост для чат-домена без покупки домена.
    """
    return f"chat.{magic_domain_for_ip(public_ip)}"


def install_chat_domain_auto(server_id: str) -> ChatDomainInstallResult:
    """Чат-домен без своего домена: `chat.<ip>.sslip.io` + обычный install-флоу."""
    record = server_store.get_record(server_id)
    target = server_store.ssh_target(server_id)
    if not record or not target:
        raise ChatDomainError("Сервер не найден.")
    ssh = _connect(target)
    try:
        public_ip = _server_public_ip(ssh) or record.get("host")
    finally:
        ssh.close()
    try:
        domain = chat_magic_domain(public_ip)
    except PanelSslError as exc:
        raise ChatDomainError(str(exc))
    return install_chat_domain(server_id, domain)


def disable_chat_domain(server_id: str) -> str:
    record = server_store.get_record(server_id)
    target = server_store.ssh_target(server_id)
    if not record or not target:
        raise ChatDomainError("Сервер не найден.")

    panel_ssl = record.get("panel_ssl") or {}
    passthrough = bool(panel_ssl.get("xray_passthrough"))
    panel_domain = (panel_ssl.get("domain") or "").lower()

    ssh = _connect(target)
    try:
        _remove_vhost(ssh, passthrough=passthrough, panel_domain=panel_domain)
        stored = dict(record.get("chat_domain") or {})
        stored.update({"enabled": False, "ssl_status": "disabled", "public_url": None})
        server_store.update_runtime(server_id, chat_domain=stored)
        return "Чат-домен отключён: vhost снят, сертификат сохранён (повторное включение быстрое)."
    finally:
        ssh.close()


# --- внутреннее ---------------------------------------------------------------


def _verify_fail(domain, resolved, ip, message) -> ChatDomainVerifyResult:
    return ChatDomainVerifyResult(
        ok=False, domain=domain, resolved_ips=resolved, server_public_ip=ip, message=message
    )


def _harden_active(ssh) -> bool:
    return (
        ssh_exec.run(
            ssh, f"sudo iptables -S INPUT 2>/dev/null | grep -q {shlex.quote(HARDEN_CHAIN)}", timeout=15
        ).exit_code
        == 0
    )


def _remove_vhost(ssh, *, passthrough: bool = False, panel_domain: str = "") -> None:
    if passthrough and panel_domain:
        # Снимаем чат-vhost и возвращаем stream-маршрутизацию к «панель + Xray».
        panel_stream = _render_template(
            "nginx-stream-xray.conf",
            DOMAIN=panel_domain,
            XRAY_LOCAL_PORT=str(XRAY_LOCAL_PORT),
        )
        script = f"""rm -f {shlex.quote(CHAT_NGINX_SITE_ENABLED)} || true
cat > {shlex.quote(STREAM_CONF)} <<'UTMKA_STREAM_EOF'
{panel_stream}
UTMKA_STREAM_EOF
nginx -t && systemctl reload nginx || true
"""
        ssh_exec.run(ssh, f"sudo bash -s <<'UTMKA_RM_EOF'\n{script}UTMKA_RM_EOF", timeout=60)
        return
    ssh_exec.run(
        ssh,
        f"sudo sh -c 'rm -f {shlex.quote(CHAT_NGINX_SITE_ENABLED)} && nginx -t && systemctl reload nginx' "
        "2>/dev/null || true",
        timeout=60,
    )


def _isolation_checks(ssh, domain: str) -> list[dict]:
    """Проверки §6.4: mini-app отдаётся, admin-поверхность через чат-домен закрыта."""
    q = shlex.quote(domain)
    checks = [
        ("mini-app отдаётся", f"curl -sk -o /dev/null -w '%{{http_code}}' --max-time 10 https://{q}/", "200"),
        ("admin API закрыт", f"curl -sk -o /dev/null -w '%{{http_code}}' --max-time 10 https://{q}/api/v1/servers", "404"),
        ("docs закрыт", f"curl -sk -o /dev/null -w '%{{http_code}}' --max-time 10 https://{q}/docs", "404"),
        ("openapi закрыт", f"curl -sk -o /dev/null -w '%{{http_code}}' --max-time 10 https://{q}/openapi.json", "404"),
    ]
    results = []
    for label, cmd, expected in checks:
        out = ssh_exec.run(ssh, cmd, timeout=25).stdout.strip()
        results.append({"label": label, "expected": expected, "actual": out or "—", "ok": out == expected})
    return results


def _build_install_script(
    *, domain: str, backup_dir: str, passthrough: bool = False, panel_domain: str = ""
) -> str:
    http_initial = _render_template("nginx-chat-http-initial.conf", DOMAIN=domain, WEBROOT=WEBROOT)
    https_final = _render_template(
        "nginx-chat-https.conf",
        DOMAIN=domain,
        WEBROOT=WEBROOT,
        UPSTREAM=CHAT_UPSTREAM,
        CHAT_ROOT=CHAT_ROOT,
        CERT_FULLCHAIN=f"{CERT_DIR}/{domain}/fullchain.pem",
        CERT_PRIVKEY=f"{CERT_DIR}/{domain}/privkey.pem",
    )
    placeholder = PLACEHOLDER_HTML

    # При passthrough :443 принадлежит stream-блоку: чат-vhost слушает локальный
    # порт, а SNI чата маршрутизируется в него (панель и Xray остаются как были).
    stream_block = ""
    listen_fix = ""
    restore_stream = ""
    if passthrough:
        combined_stream = _render_template(
            "nginx-stream-xray-chat.conf",
            DOMAIN=panel_domain,
            CHAT_DOMAIN=domain,
            PANEL_PORT=str(PANEL_HTTPS_INTERNAL),
            CHAT_PORT=str(CHAT_HTTPS_INTERNAL),
            XRAY_LOCAL_PORT=str(XRAY_LOCAL_PORT),
        )
        panel_only_stream = _render_template(
            "nginx-stream-xray.conf",
            DOMAIN=panel_domain,
            XRAY_LOCAL_PORT=str(XRAY_LOCAL_PORT),
        )
        listen_fix = (
            f"sed -i 's/listen 443 ssl/listen {CHAT_HTTPS_INTERNAL} ssl/g' {shlex.quote(CHAT_NGINX_SITE)}\n"
            f"sed -i 's/listen \\[::\\]:443 ssl/listen [::]:{CHAT_HTTPS_INTERNAL} ssl/g' {shlex.quote(CHAT_NGINX_SITE)}\n"
        )
        stream_block = f"""cat > {shlex.quote(STREAM_CONF)} <<'CHAT_STREAM_EOF'
{combined_stream}
CHAT_STREAM_EOF
"""
        # При откате вернуть stream к «панель + Xray», чтобы панель/VPN не легли.
        restore_stream = f"""cat > {shlex.quote(STREAM_CONF)} <<'CHAT_STREAM_RESTORE_EOF'
{panel_only_stream}
CHAT_STREAM_RESTORE_EOF
"""

    return f"""set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

mkdir -p {shlex.quote(backup_dir)} {shlex.quote(WEBROOT)} {shlex.quote(CHAT_ROOT)}
cp -a /etc/nginx/sites-available {shlex.quote(backup_dir)}/ 2>/dev/null || true
cp -a /etc/nginx/sites-enabled {shlex.quote(backup_dir)}/ 2>/dev/null || true
cp -a /etc/nginx/stream.d {shlex.quote(backup_dir)}/ 2>/dev/null || true

# Если nginx сломается на любом шаге — вернуть как было
restore() {{
  rm -f {shlex.quote(CHAT_NGINX_SITE_ENABLED)} {shlex.quote(CHAT_NGINX_SITE)}
{restore_stream}  nginx -t && systemctl reload nginx || true
}}
trap restore ERR

# Mini-app placeholder (не перезаписываем существующий бандл)
if [ ! -f {shlex.quote(CHAT_ROOT)}/index.html ]; then
cat > {shlex.quote(CHAT_ROOT)}/index.html <<'CHAT_HTML_EOF'
{placeholder}
CHAT_HTML_EOF
fi

# Шаг 1: HTTP-vhost только для ACME
cat > {shlex.quote(CHAT_NGINX_SITE)} <<'CHAT_HTTP_EOF'
{http_initial}
CHAT_HTTP_EOF
ln -sf {shlex.quote(CHAT_NGINX_SITE)} {shlex.quote(CHAT_NGINX_SITE_ENABLED)}
nginx -t
systemctl reload nginx

# Шаг 2: сертификат (webroot — nginx уже слушает :80)
if [ ! -f {CERT_DIR}/{domain}/fullchain.pem ]; then
  certbot certonly --webroot -w {shlex.quote(WEBROOT)} -d {shlex.quote(domain)} \\
    --agree-tos --non-interactive --register-unsafely-without-email
fi

# Шаг 3: финальный vhost — deny-by-default
cat > {shlex.quote(CHAT_NGINX_SITE)} <<'CHAT_HTTPS_EOF'
{https_final}
CHAT_HTTPS_EOF
{listen_fix}# Шаг 4: при passthrough — SNI чата → локальный порт чат-vhost
{stream_block}nginx -t
systemctl reload nginx

echo UTMKA_CHAT_OK domain={domain}
"""
