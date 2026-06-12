"""HTTPS для панели на VPS: проверка DNS, certbot, nginx (+ passthrough Xray)."""

from __future__ import annotations

import ipaddress
import re
import shlex
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.services.server_store import server_store
from app.ssh import exec as ssh_exec

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "data" / "panel_ssl"
PANEL_INSTALL_DIR = "/opt/utmka-awg"
PANEL_UPSTREAM = "http://127.0.0.1:8080"
PANEL_HTTPS_INTERNAL = 8443
XRAY_CONTAINER = "amnezia-xray"
XRAY_LOCAL_PORT = 1443
WEBROOT = "/var/www/utmka-acme"
NGINX_SITE = "/etc/nginx/sites-available/utmka-panel"
NGINX_SITE_ENABLED = "/etc/nginx/sites-enabled/utmka-panel"
STREAM_CONF = "/etc/nginx/stream.d/utmka-xray.conf"
SSL_BACKUP_ROOT = "/opt/utmka/ssl-backup"
CERT_DIR = "/etc/letsencrypt/live"

_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.(?!-)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)


class PanelSslError(Exception):
    pass


@dataclass
class PanelSslStatus:
    domain: Optional[str]
    url: Optional[str]
    status: str
    panel_detected: bool
    xray_on_443: bool
    nginx_installed: bool
    cert_present: bool
    cert_expires_at: Optional[str]
    public_ip: Optional[str]
    fallback_url: Optional[str]
    message: Optional[str]


@dataclass
class PanelSslVerifyResult:
    ok: bool
    domain: str
    resolved_ips: list[str]
    server_public_ip: Optional[str]
    panel_detected: bool
    xray_on_443: bool
    port_80_available: bool
    message: str


@dataclass
class PanelSslInstallResult:
    ok: bool
    domain: str
    url: str
    fallback_url: str
    xray_passthrough: bool
    message: str


def get_panel_ssl_status(server_id: str) -> PanelSslStatus:
    record = server_store.get_record(server_id)
    if not record:
        raise PanelSslError("Сервер не найден.")

    stored = record.get("panel_ssl") or {}
    target = server_store.ssh_target(server_id)
    if not target:
        raise PanelSslError("SSH-доступ не настроен.")

    try:
        ssh = _connect(target)
    except Exception as exc:  # noqa: BLE001
        return PanelSslStatus(
            domain=stored.get("domain"),
            url=stored.get("url"),
            status=stored.get("status", "unknown"),
            panel_detected=False,
            xray_on_443=False,
            nginx_installed=False,
            cert_present=False,
            cert_expires_at=stored.get("cert_expires_at"),
            public_ip=record.get("host"),
            fallback_url=stored.get("fallback_url"),
            message=f"SSH не отвечает: {exc}",
        )

    try:
        public_ip = _server_public_ip(ssh) or record.get("host")
        panel_detected = _panel_running(ssh)
        xray_on_443 = _xray_published_on_443(ssh)
        nginx_installed = _nginx_installed(ssh)
        domain = stored.get("domain")
        cert_present = bool(domain and _cert_exists(ssh, domain))
        return PanelSslStatus(
            domain=domain,
            url=stored.get("url"),
            status=stored.get("status", "not_configured"),
            panel_detected=panel_detected,
            xray_on_443=xray_on_443,
            nginx_installed=nginx_installed,
            cert_present=cert_present,
            cert_expires_at=stored.get("cert_expires_at"),
            public_ip=public_ip,
            fallback_url=stored.get("fallback_url") or _fallback_url(record.get("host")),
            message=None,
        )
    finally:
        ssh.close()


def verify_panel_domain(server_id: str, domain: str) -> PanelSslVerifyResult:
    domain = _normalize_domain(domain)
    record = server_store.get_record(server_id)
    target = server_store.ssh_target(server_id)
    if not record or not target:
        raise PanelSslError("Сервер не найден.")

    resolved = _resolve_domain_ips(domain)
    ssh = _connect(target)
    try:
        public_ip = _server_public_ip(ssh) or record.get("host")
        panel_detected = _panel_running(ssh)
        xray_on_443 = _xray_published_on_443(ssh)
        port_80_ok = _port_80_available(ssh)

        if not resolved:
            return PanelSslVerifyResult(
                ok=False,
                domain=domain,
                resolved_ips=[],
                server_public_ip=public_ip,
                panel_detected=panel_detected,
                xray_on_443=xray_on_443,
                port_80_available=port_80_ok,
                message="Домен не резолвится. Добавь A-запись и подожди 5–30 минут.",
            )

        if public_ip and public_ip not in resolved:
            return PanelSslVerifyResult(
                ok=False,
                domain=domain,
                resolved_ips=resolved,
                server_public_ip=public_ip,
                panel_detected=panel_detected,
                xray_on_443=xray_on_443,
                port_80_available=port_80_ok,
                message=f"DNS указывает на {', '.join(resolved)}, а IP сервера — {public_ip}.",
            )

        if not panel_detected:
            return PanelSslVerifyResult(
                ok=False,
                domain=domain,
                resolved_ips=resolved,
                server_public_ip=public_ip,
                panel_detected=False,
                xray_on_443=xray_on_443,
                port_80_available=port_80_ok,
                message="Панель не найдена на сервере. Сначала установи её: scripts/install-panel.sh",
            )

        if not port_80_ok:
            holder = _port_80_holder_name(ssh)
            holder_text = f" ({holder})" if holder else ""
            return PanelSslVerifyResult(
                ok=False,
                domain=domain,
                resolved_ips=resolved,
                server_public_ip=public_ip,
                panel_detected=panel_detected,
                xray_on_443=xray_on_443,
                port_80_available=False,
                message=f"Порт 80 занят другим процессом{holder_text}. "
                "Освободи его для Let's Encrypt (nginx — не помеха, его мастер перенастроит сам).",
            )

        hint = ""
        if xray_on_443:
            hint = " Xray на :443 — настроим passthrough, VPN не отключится."

        return PanelSslVerifyResult(
            ok=True,
            domain=domain,
            resolved_ips=resolved,
            server_public_ip=public_ip,
            panel_detected=True,
            xray_on_443=xray_on_443,
            port_80_available=True,
            message=f"DNS в порядке, можно выпускать сертификат.{hint}",
        )
    finally:
        ssh.close()


def install_panel_ssl(server_id: str, domain: str, *, email: Optional[str] = None) -> PanelSslInstallResult:
    verify = verify_panel_domain(server_id, domain)
    if not verify.ok:
        raise PanelSslError(verify.message)

    domain = verify.domain
    record = server_store.get_record(server_id)
    target = server_store.ssh_target(server_id)
    if not record or not target:
        raise PanelSslError("Сервер не найден.")

    ssh = _connect(target)
    try:
        backup_dir = f"{SSL_BACKUP_ROOT}/{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
        script = _build_install_script(
            domain=domain,
            email=email or "",
            backup_dir=backup_dir,
            xray_on_443=verify.xray_on_443,
            public_ip=verify.server_public_ip or record.get("host"),
        )
        result = ssh_exec.run(ssh, f"bash -s <<'UTMKA_SSL_EOF'\n{script}\nUTMKA_SSL_EOF", timeout=600)
        output = (result.stdout + "\n" + result.stderr).strip()
        if result.exit_code != 0 or "UTMKA_SSL_OK" not in output:
            tail = output[-1200:] if output else "нет вывода"
            raise PanelSslError(f"Установка HTTPS не удалась:\n{tail}")

        url = f"https://{domain}"
        fallback = _fallback_url(record.get("host"))
        cert_expires = _cert_expiry(ssh, domain)
        server_store.update_runtime(
            server_id,
            panel_ssl={
                "domain": domain,
                "url": url,
                "status": "active",
                "fallback_url": fallback,
                "cert_expires_at": cert_expires,
                "xray_passthrough": verify.xray_on_443,
                "installed_at": datetime.now(timezone.utc).isoformat(),
                "backup_dir": backup_dir,
            },
        )
        return PanelSslInstallResult(
            ok=True,
            domain=domain,
            url=url,
            fallback_url=fallback,
            xray_passthrough=verify.xray_on_443,
            message="HTTPS для панели настроен. Сохрани запасной адрес на случай проблем с DNS.",
        )
    finally:
        ssh.close()


def rollback_panel_ssl(server_id: str) -> str:
    record = server_store.get_record(server_id)
    stored = (record or {}).get("panel_ssl") or {}
    backup_dir = stored.get("backup_dir")
    if not backup_dir:
        raise PanelSslError("Нет сохранённого бэкапа для отката.")

    target = server_store.ssh_target(server_id)
    if not target:
        raise PanelSslError("Сервер не найден.")

    ssh = _connect(target)
    try:
        script = f"""
set -euo pipefail
if [ -d {shlex.quote(backup_dir)} ]; then
  cp -a {shlex.quote(backup_dir)}/. /etc/nginx/ 2>/dev/null || true
  rm -f {shlex.quote(NGINX_SITE_ENABLED)}
  nginx -t && systemctl reload nginx
fi
echo UTMKA_SSL_ROLLBACK_OK
"""
        result = ssh_exec.run(ssh, script, timeout=120)
        if result.exit_code != 0:
            raise PanelSslError("Откат nginx не удался.")
        server_store.update_runtime(server_id, panel_ssl={"status": "rolled_back"})
        return "Конфигурация nginx восстановлена из бэкапа."
    finally:
        ssh.close()


def panel_https_security_check(server_id: str) -> dict:
    """Данные для строки аудита «HTTPS панели»."""
    try:
        status = get_panel_ssl_status(server_id)
    except PanelSslError:
        return {"status": "unknown", "value": "—", "recommendation": None}

    if status.status == "active" and status.cert_present:
        return {"status": "ok", "value": status.url or "HTTPS", "recommendation": None}

    if not status.panel_detected:
        return {
            "status": "warning",
            "value": "Панель не установлена",
            "recommendation": "Установи панель на VPS (scripts/install-panel.sh), затем привяжи домен.",
        }

    return {
        "status": "warning",
        "value": "Только HTTP",
        "recommendation": "Укажи домен и выпусти Let's Encrypt-сертификат — вход будет по HTTPS.",
    }


def _connect(target) -> object:
    return ssh_exec.connect(
        host=target.host,
        port=target.port,
        username=target.username,
        password=target.password,
        key=target.key,
    )


def _normalize_domain(domain: str) -> str:
    value = domain.strip().lower().rstrip(".")
    value = re.sub(r"^https?://", "", value)
    value = value.split("/")[0].split(":")[0]
    if not _DOMAIN_RE.match(value):
        raise PanelSslError("Некорректный домен. Пример: panel.example.com")
    return value


def _resolve_domain_ips(domain: str) -> list[str]:
    ips: list[str] = []
    try:
        for family, _, _, _, sockaddr in socket.getaddrinfo(domain, None):
            if family == socket.AF_INET:
                ips.append(sockaddr[0])
            elif family == socket.AF_INET6:
                ip = sockaddr[0]
                if ip.startswith("::ffff:"):
                    ips.append(ip.split(":")[-1])
                else:
                    try:
                        if not ipaddress.ip_address(ip).is_private:
                            ips.append(ip)
                    except ValueError:
                        pass
    except socket.gaierror:
        return []
    return sorted(set(ips))


def _server_public_ip(ssh) -> Optional[str]:
    out = ssh_exec.run(
        ssh,
        "curl -4 -s --max-time 5 ifconfig.me 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}'",
        timeout=15,
    ).stdout.strip()
    return out.split()[0] if out else None


def _panel_running(ssh) -> bool:
    checks = [
        f"test -f {PANEL_INSTALL_DIR}/docker-compose.yml",
        "curl -sf --max-time 3 http://127.0.0.1:8080/ >/dev/null 2>&1",
        "docker ps --format '{{.Names}}' 2>/dev/null | grep -qE 'utmka-(frontend|panel)'",
    ]
    for cmd in checks:
        code = ssh_exec.run(ssh, cmd, timeout=10).exit_code
        if code == 0:
            return True
    return False


def _xray_published_on_443(ssh) -> bool:
    out = ssh_exec.run(
        ssh,
        f"docker port {shlex.quote(XRAY_CONTAINER)} 443/tcp 2>/dev/null || true",
        timeout=10,
    ).stdout.strip()
    if not out:
        return False
    return "0.0.0.0:443" in out or "[::]:443" in out


def _nginx_installed(ssh) -> bool:
    return ssh_exec.run(ssh, "command -v nginx >/dev/null 2>&1", timeout=5).exit_code == 0


def _port_80_listener(ssh) -> str:
    """Кто слушает :80 (с именами процессов — флаг -p)."""
    return ssh_exec.run(
        ssh, "ss -tlnpH '( sport = :80 )' 2>/dev/null || true", timeout=10
    ).stdout.strip()


def _port_80_available(ssh) -> bool:
    """Порт 80 свободен или занят nginx (его мастер переиспользует и перенастроит)."""
    out = _port_80_listener(ssh)
    if not out:
        return True
    if "nginx" in out.lower():
        return True
    # ss мог не показать имя процесса (нет прав) — если nginx активен, порт почти наверняка его:
    # nginx с дефолтным конфигом не стартовал бы при занятом другим процессом :80.
    if 'users:' not in out:
        code = ssh_exec.run(ssh, "systemctl is-active --quiet nginx", timeout=10).exit_code
        if code == 0:
            return True
    return False


def _port_80_holder_name(ssh) -> Optional[str]:
    """Имя процесса на :80 для понятного сообщения об ошибке."""
    out = _port_80_listener(ssh)
    match = re.search(r'users:\(\("([^"]+)"', out)
    return match.group(1) if match else None


def _cert_exists(ssh, domain: str) -> bool:
    path = f"{CERT_DIR}/{domain}/fullchain.pem"
    return ssh_exec.run(ssh, f"test -f {shlex.quote(path)}", timeout=5).exit_code == 0


def _cert_expiry(ssh, domain: str) -> Optional[str]:
    path = f"{CERT_DIR}/{domain}/fullchain.pem"
    out = ssh_exec.run(
        ssh,
        f"openssl x509 -enddate -noout -in {shlex.quote(path)} 2>/dev/null | cut -d= -f2",
        timeout=10,
    ).stdout.strip()
    return out or None


def _fallback_url(host: Optional[str]) -> str:
    if not host:
        return f"http://127.0.0.1:8080"
    return f"http://{host}:8080"


def _render_template(name: str, **kwargs: str) -> str:
    text = (TEMPLATE_DIR / name).read_text(encoding="utf-8")
    for key, value in kwargs.items():
        text = text.replace(f"{{{{{key}}}}}", value)
    return text


def _build_install_script(
    *,
    domain: str,
    email: str,
    backup_dir: str,
    xray_on_443: bool,
    public_ip: str,
) -> str:
    http_initial_conf = _render_template(
        "nginx-panel-http-initial.conf",
        DOMAIN=domain,
        WEBROOT=WEBROOT,
        UPSTREAM=PANEL_UPSTREAM,
    )
    http_final_conf = _render_template(
        "nginx-panel-http.conf",
        DOMAIN=domain,
        WEBROOT=WEBROOT,
        UPSTREAM=PANEL_UPSTREAM,
    )
    https_conf = _render_template(
        "nginx-panel-https.conf",
        DOMAIN=domain,
        UPSTREAM=PANEL_UPSTREAM,
        CERT_FULLCHAIN=f"{CERT_DIR}/{domain}/fullchain.pem",
        CERT_PRIVKEY=f"{CERT_DIR}/{domain}/privkey.pem",
    )
    stream_conf = ""
    if xray_on_443:
        stream_conf = _render_template(
            "nginx-stream-xray.conf",
            DOMAIN=domain,
            XRAY_LOCAL_PORT=str(XRAY_LOCAL_PORT),
        )

    email_flag = f"-m {shlex.quote(email)}" if email else "--register-unsafely-without-email"

    # shell script — heredocs escaped for embedding in Python
    return f"""set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

mkdir -p {shlex.quote(SSL_BACKUP_ROOT)} {shlex.quote(backup_dir)} {shlex.quote(WEBROOT)}
mkdir -p /etc/nginx/stream.d /etc/nginx/sites-enabled

if ! command -v nginx >/dev/null 2>&1; then
  apt-get update -qq
  apt-get install -y -qq nginx certbot
fi
if ! command -v certbot >/dev/null 2>&1; then
  apt-get install -y -qq certbot
fi

if [ -d /etc/nginx ]; then
  cp -a /etc/nginx/sites-available {shlex.quote(backup_dir)}/ 2>/dev/null || true
  cp -a /etc/nginx/sites-enabled {shlex.quote(backup_dir)}/ 2>/dev/null || true
  cp -a /etc/nginx/nginx.conf {shlex.quote(backup_dir)}/ 2>/dev/null || true
  cp -a /etc/nginx/stream.d {shlex.quote(backup_dir)}/ 2>/dev/null || true
fi

# Блок stream нужен только для SNI passthrough (Xray на :443). В Ubuntu stream —
# отдельный пакет libnginx-mod-stream; без него "stream" ломает nginx -t.
if [ "{'1' if xray_on_443 else '0'}" = "1" ]; then
  if [ ! -e /usr/lib/nginx/modules/ngx_stream_module.so ]; then
    apt-get install -y -qq libnginx-mod-stream || apt-get update -qq && apt-get install -y -qq libnginx-mod-stream
  fi
  grep -q 'stream.d' /etc/nginx/nginx.conf 2>/dev/null || {{
    sed -i '/^http {{/i stream {{\\n    include /etc/nginx/stream.d/*.conf;\\n}}\\n' /etc/nginx/nginx.conf
  }}
fi

XRAY_MOVED=0
if [ "{'1' if xray_on_443 else '0'}" = "1" ]; then
  if docker ps -a --format '{{{{.Names}}}}' | grep -qx {shlex.quote(XRAY_CONTAINER)}; then
    echo "Перенос Xray на 127.0.0.1:{XRAY_LOCAL_PORT} для совместного :443..."
    IMG=$(docker inspect -f '{{{{.Config.Image}}}}' {shlex.quote(XRAY_CONTAINER)} 2>/dev/null || true)
    docker stop {shlex.quote(XRAY_CONTAINER)} >/dev/null 2>&1 || true
    docker rm {shlex.quote(XRAY_CONTAINER)} >/dev/null 2>&1 || true
    if [ -n "$IMG" ]; then
      docker run -d --privileged --log-driver none --restart always --cap-add=NET_ADMIN \\
        -p 127.0.0.1:{XRAY_LOCAL_PORT}:443/tcp \\
        --name {shlex.quote(XRAY_CONTAINER)} "$IMG"
      docker network connect amnezia-dns-net {shlex.quote(XRAY_CONTAINER)} 2>/dev/null || true
      docker exec -i {shlex.quote(XRAY_CONTAINER)} bash -c 'mkdir -p /dev/net; if [ ! -c /dev/net/tun ]; then mknod /dev/net/tun c 10 200; fi' 2>/dev/null || true
      docker exec -d {shlex.quote(XRAY_CONTAINER)} /opt/amnezia/start.sh 2>/dev/null || true
      XRAY_MOVED=1
    fi
  fi
fi

cat > {shlex.quote(NGINX_SITE)} <<'NGINX_HTTP_EOF'
{http_initial_conf}
NGINX_HTTP_EOF

ln -sf {shlex.quote(NGINX_SITE)} {shlex.quote(NGINX_SITE_ENABLED)}
rm -f /etc/nginx/sites-enabled/default 2>/dev/null || true

if [ "$XRAY_MOVED" = "1" ]; then
cat > {shlex.quote(STREAM_CONF)} <<'NGINX_STREAM_EOF'
{stream_conf}
NGINX_STREAM_EOF
fi

nginx -t
systemctl enable nginx
systemctl reload nginx || systemctl start nginx

certbot certonly --webroot -w {shlex.quote(WEBROOT)} -d {shlex.quote(domain)} \\
  --agree-tos --non-interactive {email_flag} || certbot certonly --standalone -d {shlex.quote(domain)} \\
  --agree-tos --non-interactive {email_flag}

if [ "$XRAY_MOVED" = "1" ]; then
cat > {shlex.quote(NGINX_SITE)} <<'NGINX_HTTPS_EOF'
{http_final_conf}

{https_conf}
NGINX_HTTPS_EOF
  sed -i 's/listen 443 ssl/listen {PANEL_HTTPS_INTERNAL} ssl/g' {shlex.quote(NGINX_SITE)}
  sed -i 's/listen \\[::\\]:443 ssl/listen [::]:{PANEL_HTTPS_INTERNAL} ssl/g' {shlex.quote(NGINX_SITE)}
else
cat > {shlex.quote(NGINX_SITE)} <<'NGINX_HTTPS_EOF'
{http_final_conf}

{https_conf}
NGINX_HTTPS_EOF
fi

nginx -t
systemctl reload nginx

curl -sf --max-time 10 -H "Host: {domain}" http://127.0.0.1/api/v1/health >/dev/null \\
  || curl -sf --max-time 10 https://{shlex.quote(domain)}/api/v1/health >/dev/null \\
  || curl -sf --max-time 10 -k https://127.0.0.1:{PANEL_HTTPS_INTERNAL}/api/v1/health >/dev/null

echo UTMKA_SSL_OK domain={domain} ip={public_ip}
"""
