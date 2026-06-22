"""Xray-каскад (CX1) — preflight / apply / rollback / статус.

Модель (план §5.8):

    Клиент --[VLESS-Reality TCP]--> Entry (RU) --[TCP relay]--> Exit --> интернет

На exit — обычный Xray+Reality (тут терминируется TLS/Reality, тут ключи/SNI).
На entry — прозрачный TCP-relay (socat) entry:relay_port → exit:exit_port.
Клиентский конфиг: address = IP entry, Reality pbk/sid/sni = от exit.

Калашников: preflight fail-closed (exit healthy, порт свободен, entry достаёт
exit), apply ставит relay аддитивно (rollback = снять relay), health = реальный
TLS-handshake через entry обязан вернуть сертификат сайта маскировки exit.
"""

from __future__ import annotations

import json
import re
import shlex
from datetime import datetime, timezone
from typing import Optional

from app.services.amnezia_ssh import port_busy, read_container_file
from app.services.server_store import server_store
from app.services.xray_install import CONTAINER_NAME as XRAY_CONTAINER
from app.services.xray_server_config import SERVER_CONFIG_PATH
from app.ssh import exec as ssh_exec
from app.services.xray_cascade_store import DEFAULT_RELAY_PORT, xray_cascade_store

RELAY_LABEL = "utmka-xray-cascade"
RELAY_LOG = "/tmp/utmka-xray-cascade.log"


class XrayCascadeError(Exception):
    pass


def _connect(server_id: str):
    target = server_store.ssh_target(server_id)
    if not target:
        raise XrayCascadeError("SSH-доступ к серверу не настроен.")
    try:
        return ssh_exec.connect(
            host=target.host, port=target.port, username=target.username,
            password=target.password, key=target.key, timeout=15,
        )
    except Exception as exc:  # noqa: BLE001
        raise XrayCascadeError(f"SSH не отвечает: {exc}") from exc


def _xray_running(ssh) -> bool:
    res = ssh_exec.run(
        ssh,
        f"docker exec {shlex.quote(XRAY_CONTAINER)} sh -c 'pgrep -x xray >/dev/null && echo ok || echo no' 2>/dev/null || echo no",
        timeout=20,
    )
    return res.stdout.strip().endswith("ok")


def _exit_reality(ssh) -> dict:
    """SNI + порт + наличие Reality на exit (для relay и выдачи клиентов)."""
    raw = read_container_file(ssh, XRAY_CONTAINER, SERVER_CONFIG_PATH)
    if not raw:
        raise XrayCascadeError("На exit не найден server.json Xray.")
    try:
        data = json.loads(raw)
        inbound = (data.get("inbounds") or [])[0]
        reality = (inbound.get("streamSettings") or {}).get("realitySettings") or {}
    except (json.JSONDecodeError, IndexError, KeyError) as exc:
        raise XrayCascadeError("server.json на exit повреждён или не Reality.") from exc
    sni = ((reality.get("serverNames") or [""])[0]) or ""
    port = int(inbound.get("port") or DEFAULT_RELAY_PORT)
    return {"sni": sni, "exit_port": port}


def _tcp_reachable(ssh, host: str, port: int) -> bool:
    res = ssh_exec.run(
        ssh,
        f"timeout 6 bash -c 'cat < /dev/null > /dev/tcp/{shlex.quote(host)}/{int(port)}' 2>/dev/null && echo ok || echo no",
        timeout=12,
    )
    return res.stdout.strip().endswith("ok")


def _relay_alive(ssh, relay_port: int) -> bool:
    res = ssh_exec.run(
        ssh,
        f"pgrep -f 'socat .*TCP4-LISTEN:{int(relay_port)},' >/dev/null && echo ok || echo no",
        timeout=15,
    )
    return res.stdout.strip().endswith("ok")


# ---------------------------------------------------------------------------
# nginx-режим каскада (entry на :443 через SNI-ветку резерва)
#
# Резерв :443 (app.services.panel_ssl.STREAM_CONF) — это уже SNI-роутер. Каскад
# добавляет в него ещё одну ветку: SNI маскировки exit → upstream на exit:443.
# Тогда клиент заходит на entry:443 (классический HTTPS), а nginx прозрачно
# проксирует TLS на exit, где терминируется Reality. Никакого socat и нестандартных
# портов — лучшая маскировка (РКН видит entry:443 + обычный SNI).
# ---------------------------------------------------------------------------

# Пути совпадают с app.services.panel_ssl (там источник правды для резерва :443).
ENTRY_STREAM_CONF = "/etc/nginx/stream.d/utmka-xray.conf"
ENTRY_STREAM_MAP_DIR = "/etc/nginx/stream.d/cascade-maps"
ENTRY_STREAM_INCLUDE_GLOB = "/etc/nginx/stream.d/cascade-maps/*.map"


def _safe_id(value: str) -> str:
    """Детерминированный nginx-безопасный идентификатор upstream/файла из id сервера."""
    return "c" + re.sub(r"[^0-9a-zA-Z]", "", value or "")[:24]


def _nginx_route_files(safe: str) -> tuple[str, str]:
    """(map-фрагмент в cascade-maps, upstream-файл в stream.d)."""
    map_file = f"{ENTRY_STREAM_MAP_DIR}/{safe}.map"
    upstream_file = f"/etc/nginx/stream.d/utmka-cascade-{safe}.conf"
    return map_file, upstream_file


def _entry_has_reservation(ssh) -> bool:
    """На entry активен nginx-резерв :443 (есть STREAM_CONF) → доступен nginx-режим."""
    res = ssh_exec.run(
        ssh,
        f"test -f {shlex.quote(ENTRY_STREAM_CONF)} && echo yes || echo no",
        timeout=15,
    )
    return res.stdout.strip().endswith("yes")


def _nginx_apply_script(safe: str, sni: str, exit_host: str, exit_port: int) -> str:
    map_file, upstream_file = _nginx_route_files(safe)
    conf = ENTRY_STREAM_CONF
    return f"""
set -e
mkdir -p {shlex.quote(ENTRY_STREAM_MAP_DIR)}

cat > {shlex.quote(upstream_file)} <<'EOF'
upstream cascade_{safe} {{
    server {exit_host}:{int(exit_port)};
}}
EOF

cat > {shlex.quote(map_file)} <<'EOF'
{sni} cascade_{safe};
EOF

# Идемпотентно вставляем include map-фрагментов в map-блок резерва (перед default).
if ! grep -qF 'cascade-maps/*.map' {shlex.quote(conf)} 2>/dev/null; then
  sed -i 's#^\\([[:space:]]*\\)default \\(.*\\);#\\1include /etc/nginx/stream.d/cascade-maps/*.map;\\n\\1default \\2;#' {shlex.quote(conf)}
fi

nginx -t
systemctl reload nginx
echo OK_NGINX_RELAY
"""


def _nginx_remove_script(safe: str) -> str:
    map_file, upstream_file = _nginx_route_files(safe)
    return (
        f"rm -f {shlex.quote(map_file)} {shlex.quote(upstream_file)}; "
        f"nginx -t && systemctl reload nginx; echo DOWN_NGINX_RELAY\n"
    )


def _nginx_route_alive(ssh, safe: str) -> bool:
    map_file, upstream_file = _nginx_route_files(safe)
    res = ssh_exec.run(
        ssh,
        f"test -f {shlex.quote(map_file)} && test -f {shlex.quote(upstream_file)} "
        f"&& systemctl is-active --quiet nginx && echo ok || echo no",
        timeout=15,
    )
    return res.stdout.strip().endswith("ok")


def _sni_conflicts_entry(entry_rec: dict, sni: str) -> bool:
    """SNI exit не должен совпадать с доменом панели/чата entry (иначе дубль ключа в map)."""
    sni_l = (sni or "").strip().lower()
    if not sni_l:
        return False
    names: set[str] = set()
    panel_domain = ((entry_rec.get("panel_ssl") or {}).get("domain") or "").strip().lower()
    if panel_domain:
        names.add(panel_domain)
    chat_domain = ((entry_rec.get("chat_domain") or {}).get("domain") or "").strip().lower()
    if chat_domain:
        names.add(chat_domain)
    return sni_l in names


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


def xray_cascade_preflight(entry_id: str, exit_id: str, relay_port: Optional[int] = None) -> dict:
    if entry_id == exit_id:
        raise XrayCascadeError("Entry и exit не могут быть одним сервером.")
    entry_rec = server_store.get_record(entry_id)
    exit_rec = server_store.get_record(exit_id)
    if not entry_rec:
        raise XrayCascadeError("Entry-сервер не найден.")
    if not exit_rec:
        raise XrayCascadeError("Exit-сервер не найден.")
    if not server_store.has_xray(exit_rec):
        raise XrayCascadeError("На exit-сервере не установлен Xray (VLESS-Reality).")

    checks: list[dict] = []
    blockers: list[str] = []

    exit_ssh = _connect(exit_id)
    try:
        running = _xray_running(exit_ssh)
        checks.append({"id": "exit_xray", "label": "Exit: Xray запущен",
                       "status": "ok" if running else "danger",
                       "value": "running" if running else "не запущен"})
        if not running:
            blockers.append("Xray на exit не запущен.")
        info = _exit_reality(exit_ssh)
        exit_port = info["exit_port"]
        sni = info["sni"]
        checks.append({"id": "exit_reality", "label": "Exit: Reality",
                       "status": "ok" if sni else "warning",
                       "value": f"SNI {sni or '—'}, порт {exit_port}"})
    finally:
        exit_ssh.close()

    exit_host = exit_rec.get("host")
    user_port = int(relay_port) if relay_port else None

    entry_ssh = _connect(entry_id)
    try:
        # Режим: если на entry активен резерв :443 и юзер не навязал иной порт —
        # вход живёт на :443 как SNI-ветка nginx (лучшая маскировка). Иначе socat.
        has_reservation = _entry_has_reservation(entry_ssh)
        use_nginx = has_reservation and (user_port is None or user_port == 443)

        if use_nginx:
            mode = "nginx"
            port = 443
            checks.append({"id": "entry_reserve", "label": "Entry: резерв :443 (nginx)",
                           "status": "ok", "value": "активен"})
            conflict = _sni_conflicts_entry(entry_rec, sni)
            checks.append({"id": "entry_sni", "label": "Entry: SNI-ветка",
                           "status": "danger" if (conflict or not sni) else "ok",
                           "value": (f"SNI {sni} конфликтует с доменом панели/чата"
                                     if conflict else f"SNI {sni or '—'} → exit:{exit_port}")})
            if not sni:
                blockers.append("У exit не задан Reality-SNI — nginx-режим каскада требует SNI.")
            if conflict:
                blockers.append(
                    f"SNI exit ({sni}) совпадает с доменом панели/чата на entry — "
                    "выбери другой маскировочный домен на exit."
                )
        else:
            mode = "socat"
            port = user_port or exit_port or DEFAULT_RELAY_PORT
            busy = port_busy(entry_ssh, port, proto="tcp")
            checks.append({"id": "relay_port", "label": f"Entry: TCP-порт {port}",
                           "status": "danger" if busy else "ok",
                           "value": "занят" if busy else "свободен"})
            if busy:
                blockers.append(f"TCP-порт {port} на entry занят — выбери другой relay-порт.")

        reach = _tcp_reachable(entry_ssh, exit_host, exit_port)
        checks.append({"id": "entry_to_exit", "label": "Entry → Exit (TCP)",
                       "status": "ok" if reach else "danger",
                       "value": f"{exit_host}:{exit_port} {'доступен' if reach else 'недоступен'}"})
        if not reach:
            blockers.append(f"Entry не достаёт exit {exit_host}:{exit_port} по TCP.")
    finally:
        entry_ssh.close()

    ok = not blockers
    state = "preflight_ok" if ok else "preflight_failed"
    _msg_ok = (
        "Проверка пройдена — вход встанет на :443 (nginx)." if (ok and mode == "nginx")
        else "Проверка пройдена — можно включать relay."
    )
    xray_cascade_store.upsert_link(
        entry_id,
        exit_server_id=exit_id,
        relay_port=port,
        exit_port=exit_port,
        exit_host=exit_host,
        sni=sni,
        mode=mode,
        state=state,
        last_preflight_at=datetime.now(timezone.utc).isoformat(),
        last_preflight_ok=ok,
        message=_msg_ok if ok else "Проверка выявила блокеры.",
    )
    return {
        "ok": ok, "entry_server_id": entry_id, "exit_server_id": exit_id,
        "relay_port": port, "exit_port": exit_port, "sni": sni, "mode": mode,
        "checks": checks, "blockers": blockers,
        "message": "Preflight пройден." if ok else "Preflight выявил блокеры.",
    }


# ---------------------------------------------------------------------------
# Apply / Rollback
# ---------------------------------------------------------------------------


def _relay_up_script(relay_port: int, exit_host: str, exit_port: int) -> str:
    p = int(relay_port)
    xp = int(exit_port)
    xh = shlex.quote(exit_host)
    return f"""
set -e
command -v socat >/dev/null 2>&1 || (apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq socat)
pkill -f 'socat .*TCP4-LISTEN:{p},' 2>/dev/null || true
sleep 1
nohup socat TCP4-LISTEN:{p},fork,reuseaddr TCP4:{xh}:{xp} >{RELAY_LOG} 2>&1 &
sleep 1
pgrep -f 'socat .*TCP4-LISTEN:{p},' >/dev/null
echo OK_RELAY
"""


def _relay_down_script(relay_port: int) -> str:
    return f"pkill -f 'socat .*TCP4-LISTEN:{int(relay_port)},' 2>/dev/null || true; echo DOWN_RELAY\n"


def _health_handshake(ssh, relay_port: int, sni: str) -> bool:
    """Реальный TLS-handshake через relay должен вернуть сертификат сайта exit."""
    sni_q = shlex.quote(sni or "")
    script = (
        f"timeout 10 bash -c \"echo | openssl s_client -connect 127.0.0.1:{int(relay_port)} "
        f"-servername {sni_q} 2>/dev/null\" | grep -qiE 'BEGIN CERTIFICATE|subject=|Verify return' "
        f"&& echo ok || echo no"
    )
    res = ssh_exec.run(ssh, script, timeout=20)
    return res.stdout.strip().endswith("ok")


def xray_cascade_apply(entry_id: str) -> dict:
    link = xray_cascade_store.get_link(entry_id)
    if not link:
        raise XrayCascadeError("Сначала выполните проверку (preflight).")
    if not link.get("last_preflight_ok"):
        raise XrayCascadeError("Проверка не пройдена — включение заблокировано.")
    exit_host = link.get("exit_host")
    exit_port = int(link.get("exit_port") or DEFAULT_RELAY_PORT)
    relay_port = int(link.get("relay_port") or DEFAULT_RELAY_PORT)
    sni = link.get("sni") or ""
    mode = link.get("mode") or "socat"
    if not exit_host:
        raise XrayCascadeError("Не задан exit-сервер.")

    steps: list[dict] = []
    entry_ssh = _connect(entry_id)
    try:
        if mode == "nginx":
            if not sni:
                raise XrayCascadeError("nginx-режим каскада требует Reality-SNI exit.")
            safe = _safe_id(link.get("exit_server_id") or exit_host)
            res = ssh_exec.run(entry_ssh, _nginx_apply_script(safe, sni, exit_host, exit_port), timeout=120)
            if res.exit_code != 0 or "OK_NGINX_RELAY" not in res.stdout:
                ssh_exec.run(entry_ssh, _nginx_remove_script(safe), timeout=60)
                raise XrayCascadeError(
                    f"Не удалось включить SNI-ветку nginx на entry: "
                    f"{res.stderr.strip() or res.stdout.strip()[-300:]}"
                )
            steps.append({"name": "Entry: SNI-ветка nginx :443 → exit", "status": "ok"})
            healthy = _health_handshake(entry_ssh, 443, sni)
            if not healthy:
                ssh_exec.run(entry_ssh, _nginx_remove_script(safe), timeout=60)
                steps.append({"name": "Health: Reality-handshake через entry", "status": "failed"})
                xray_cascade_store.upsert_link(entry_id, state="rolled_back",
                                               message="Health не пройден — SNI-ветка снята (откат).")
                raise XrayCascadeError(
                    "Health-check провален: TLS-handshake через entry:443 не дошёл до Reality exit. Ветка снята."
                )
            steps.append({"name": "Health: Reality-handshake через entry", "status": "ok",
                          "detail": f"TLS к {sni} через entry:443 → exit OK"})
        else:
            res = ssh_exec.run(entry_ssh, _relay_up_script(relay_port, exit_host, exit_port), timeout=120)
            if res.exit_code != 0 or "OK_RELAY" not in res.stdout:
                ssh_exec.run(entry_ssh, _relay_down_script(relay_port), timeout=30)
                raise XrayCascadeError(
                    f"Не удалось поднять relay на entry: {res.stderr.strip() or res.stdout.strip()[-300:]}"
                )
            steps.append({"name": "Entry: TCP-relay на exit", "status": "ok"})
            healthy = _health_handshake(entry_ssh, relay_port, sni)
            if not healthy:
                ssh_exec.run(entry_ssh, _relay_down_script(relay_port), timeout=30)
                steps.append({"name": "Health: Reality-handshake через entry", "status": "failed"})
                xray_cascade_store.upsert_link(entry_id, state="rolled_back",
                                               message="Health не пройден — relay снят (откат).")
                raise XrayCascadeError(
                    "Health-check провален: TLS-handshake через entry не дошёл до Reality exit. Relay снят."
                )
            steps.append({"name": "Health: Reality-handshake через entry", "status": "ok",
                          "detail": f"TLS к {sni} через entry:{relay_port} → exit OK"})
    finally:
        entry_ssh.close()

    xray_cascade_store.upsert_link(
        entry_id, state="active", desired="up",
        last_applied_at=datetime.now(timezone.utc).isoformat(),
        message=("Xray-каскад активен (вход :443 через nginx): клиент → entry → exit."
                 if mode == "nginx" else "Xray-каскад активен: клиент → entry → exit."),
    )
    return {"ok": True, "state": "active", "entry_server_id": entry_id,
            "exit_server_id": link.get("exit_server_id"), "relay_port": relay_port,
            "mode": mode, "steps": steps, "message": "Xray-каскад включён."}


def xray_cascade_rollback(entry_id: str) -> dict:
    link = xray_cascade_store.get_link(entry_id)
    if not link:
        raise XrayCascadeError("Xray-каскад для этого сервера не найден.")
    relay_port = int(link.get("relay_port") or DEFAULT_RELAY_PORT)
    mode = link.get("mode") or "socat"
    entry_ssh = _connect(entry_id)
    try:
        if mode == "nginx":
            safe = _safe_id(link.get("exit_server_id") or link.get("exit_host") or "")
            ssh_exec.run(entry_ssh, _nginx_remove_script(safe), timeout=60)
        else:
            ssh_exec.run(entry_ssh, _relay_down_script(relay_port), timeout=30)
    finally:
        entry_ssh.close()
    xray_cascade_store.upsert_link(
        entry_id, state="down", desired="down",
        message=("SNI-ветка снята. Каскад выключен." if mode == "nginx"
                 else "Relay снят. Каскад выключен."),
    )
    return {"ok": True, "state": "down", "entry_server_id": entry_id,
            "message": "Xray-каскад выключен."}


# ---------------------------------------------------------------------------
# Статус
# ---------------------------------------------------------------------------


def _desired_up(link: dict) -> bool:
    desired = link.get("desired")
    if desired:
        return desired == "up"
    # back-compat: звенья без поля desired — намерение из state.
    return (link.get("state") or "") == "active"


def reconcile_xray_cascade(entry_id: str, *, heal: bool = True) -> dict:
    """Сверяет relay с намерением и (при heal) поднимает упавший relay.

    Self-healing: если каскад должен быть активен (desired=up), а socat не жив
    (перезагрузка entry и т.п.) — автоматически перезапускаем relay.
    """
    link = xray_cascade_store.get_link(entry_id)
    if not link or not link.get("relay_port") or not link.get("exit_host"):
        return {"entry": entry_id, "skipped": True}
    if not _desired_up(link):
        return {"entry": entry_id, "skipped": True}

    relay_port = int(link.get("relay_port") or DEFAULT_RELAY_PORT)
    exit_host = link.get("exit_host")
    exit_port = int(link.get("exit_port") or DEFAULT_RELAY_PORT)
    mode = link.get("mode") or "socat"
    sni = link.get("sni") or ""
    safe = _safe_id(link.get("exit_server_id") or exit_host or "")

    try:
        ssh = _connect(entry_id)
    except XrayCascadeError:
        return {"entry": entry_id, "ok": False, "error": "ssh-unreachable"}
    try:
        alive = _nginx_route_alive(ssh, safe) if mode == "nginx" else _relay_alive(ssh, relay_port)
        if alive:
            if (link.get("state") or "") != "active":
                xray_cascade_store.upsert_link(entry_id, state="active",
                                               message="Каскад активен.")
            return {"entry": entry_id, "ok": True, "healed": False, "state": "active"}
        if not heal:
            xray_cascade_store.upsert_link(entry_id, state="down",
                                           message="Каскад не обнаружен на entry.")
            return {"entry": entry_id, "ok": False, "healed": False, "state": "down"}

        if mode == "nginx" and sni:
            res = ssh_exec.run(ssh, _nginx_apply_script(safe, sni, exit_host, exit_port), timeout=120)
            healed = (res.exit_code == 0 and "OK_NGINX_RELAY" in res.stdout
                      and _nginx_route_alive(ssh, safe))
        else:
            res = ssh_exec.run(ssh, _relay_up_script(relay_port, exit_host, exit_port), timeout=120)
            healed = (res.exit_code == 0 and "OK_RELAY" in res.stdout
                      and _relay_alive(ssh, relay_port))
        if healed:
            xray_cascade_store.upsert_link(
                entry_id, state="active",
                last_healed_at=datetime.now(timezone.utc).isoformat(),
                message="Каскад восстановлен автоматически (self-heal).",
            )
            return {"entry": entry_id, "ok": True, "healed": True, "state": "active"}
        xray_cascade_store.upsert_link(entry_id, state="down",
                                       message="Каскад упал, авто-восстановление не удалось.")
        return {"entry": entry_id, "ok": False, "healed": False, "state": "down"}
    finally:
        ssh.close()


def reconcile_all_xray_cascades() -> dict:
    """Фоновый проход по всем каскадам с намерением up (планировщик)."""
    healed = 0
    checked = 0
    for link in xray_cascade_store.list_links():
        if not _desired_up(link):
            continue
        entry_id = link.get("entry_server_id")
        if not entry_id:
            continue
        checked += 1
        try:
            result = reconcile_xray_cascade(entry_id, heal=True)
            if result.get("healed"):
                healed += 1
        except Exception:  # noqa: BLE001
            continue
    return {"checked": checked, "healed": healed}


def xray_cascade_status(entry_id: str) -> dict:
    link = xray_cascade_store.get_link(entry_id) or {}
    exit_id = link.get("exit_server_id")
    exit_rec = server_store.get_record(exit_id) if exit_id else None
    state = link.get("state") or "none"
    live_active = False
    if _desired_up(link) and link.get("relay_port") and link.get("exit_host"):
        # Открытие страницы — хороший момент для self-heal.
        result = reconcile_xray_cascade(entry_id, heal=True)
        live_active = bool(result.get("ok") and result.get("state") == "active")
        link = xray_cascade_store.get_link(entry_id) or link
        state = link.get("state") or state
    return {
        "entry_server_id": entry_id,
        "exit_server_id": exit_id,
        "exit_name": exit_rec.get("name") if exit_rec else None,
        "relay_port": link.get("relay_port"),
        "exit_port": link.get("exit_port"),
        "sni": link.get("sni"),
        "mode": link.get("mode") or "socat",
        "split_ru": bool(link.get("split_ru")),
        "state": state,
        "live_active": live_active,
        "last_preflight_ok": link.get("last_preflight_ok", False),
        "last_applied_at": link.get("last_applied_at"),
        "last_healed_at": link.get("last_healed_at"),
        "message": link.get("message"),
    }


def create_xray_cascade_client(
    entry_id: str,
    name: str,
    *,
    format: str = "both",
    traffic_limit_bytes: Optional[int] = None,
    expires_at: Optional[str] = None,
):
    """Выдать клиента в Xray-каскад (CX1-2).

    UUID/ключи живут на exit, конфиг адресован на entry:relay_port.
    """
    link = xray_cascade_store.get_link(entry_id)
    if not link:
        raise XrayCascadeError("Xray-каскад для этого узла не настроен.")
    if (link.get("state") or "") != "active":
        raise XrayCascadeError("Каскад не активен — сначала включите relay.")
    exit_id = link.get("exit_server_id")
    relay_port = int(link.get("relay_port") or DEFAULT_RELAY_PORT)
    if not exit_id:
        raise XrayCascadeError("В каскаде не задан exit-сервер.")

    entry_rec = server_store.get_record(entry_id)
    if not entry_rec or not entry_rec.get("host"):
        raise XrayCascadeError("Entry-сервер не найден.")
    entry_host = entry_rec["host"]

    from app.services.xray_client import ClientCreateError, create_xray_client

    try:
        return create_xray_client(
            exit_id,
            name,
            format=format,
            traffic_limit_bytes=traffic_limit_bytes,
            expires_at=expires_at,
            link_host=entry_host,
            link_port=relay_port,
            channel_entry_id=entry_id,
            split_ru=bool(link.get("split_ru")),
        )
    except ClientCreateError as exc:
        raise XrayCascadeError(str(exc)) from exc


def _exit_profile(ssh) -> dict:
    """Reality-профиль exit для переиздания клиентских конфигов каскада."""
    from app.services.xray_client import (
        PUBLIC_KEY_PATH,
        SHORT_ID_PATH,
        transport_from_inbound,
    )

    raw = read_container_file(ssh, XRAY_CONTAINER, SERVER_CONFIG_PATH)
    if not raw:
        raise XrayCascadeError("На exit не найден server.json Xray.")
    try:
        data = json.loads(raw)
        inbound = (data.get("inbounds") or [])[0]
        reality = (inbound.get("streamSettings") or {}).get("realitySettings") or {}
    except (json.JSONDecodeError, IndexError, KeyError) as exc:
        raise XrayCascadeError("server.json на exit повреждён.") from exc
    network, flow, service_name, path = transport_from_inbound(inbound)
    pbk = read_container_file(ssh, XRAY_CONTAINER, PUBLIC_KEY_PATH)
    sid = read_container_file(ssh, XRAY_CONTAINER, SHORT_ID_PATH)
    if not pbk or not sid:
        raise XrayCascadeError("На exit не найдены Reality-ключи.")
    return {
        "sni": ((reality.get("serverNames") or [""])[0]) or "",
        "public_key": pbk,
        "short_id": sid,
        "flow": flow,
        "network": network,
        "service_name": service_name,
        "path": path,
    }


def set_xray_cascade_rules(entry_id: str, enabled: bool) -> dict:
    """Split-правила Xray-каскада: РФ напрямую, остальное через каскад.

    Реализуется client-side routing в конфигах. Переиздаём конфиги всех клиентов
    каскада (ключи/UUID не трогаем — меняется только выданный конфиг).
    """
    link = xray_cascade_store.get_link(entry_id)
    if not link:
        raise XrayCascadeError("Xray-каскад для этого узла не настроен.")
    if (link.get("state") or "") != "active":
        raise XrayCascadeError("Каскад не активен.")
    exit_id = link.get("exit_server_id")
    relay_port = int(link.get("relay_port") or DEFAULT_RELAY_PORT)
    entry_rec = server_store.get_record(entry_id)
    if not entry_rec or not entry_rec.get("host"):
        raise XrayCascadeError("Entry-сервер не найден.")
    entry_host = entry_rec["host"]

    from app.services.amnezia_link import (
        build_vless_uri,
        build_xray_native_config,
        build_xray_vpn_link,
    )
    from app.services.client_store import client_store

    targets = client_store.cascade_reissue_targets(entry_id)
    reissued = 0
    if targets:
        exit_ssh = _connect(exit_id)
        try:
            profile = _exit_profile(exit_ssh)
        finally:
            exit_ssh.close()
        for tgt in targets:
            uuid_val = tgt.get("public_key")
            if not uuid_val:
                continue
            native = build_xray_native_config(
                host=entry_host, port=relay_port, client_uuid=uuid_val,
                flow=profile["flow"], site=profile["sni"],
                public_key=profile["public_key"], short_id=profile["short_id"],
                split_ru=enabled,
                network=profile["network"], service_name=profile["service_name"],
                path=profile["path"],
            )
            uri = build_vless_uri(
                host=entry_host, port=relay_port, client_uuid=uuid_val,
                flow=profile["flow"], site=profile["sni"],
                public_key=profile["public_key"], short_id=profile["short_id"],
                name=tgt["name"],
                network=profile["network"], service_name=profile["service_name"],
                path=profile["path"],
            )
            config_text = uri if tgt["has_config"] else None
            vpn_link = (
                build_xray_vpn_link(host=entry_host, native_config_json=native,
                                    description=entry_rec.get("name") or entry_host)
                if tgt["has_vpn"] else None
            )
            client_store.update_issued_config(
                tgt["id"], config_text=config_text, vpn_link=vpn_link,
                endpoint=f"{entry_host}:{relay_port}",
            )
            reissued += 1

    xray_cascade_store.upsert_link(entry_id, split_ru=bool(enabled))
    return {
        "ok": True,
        "split_ru": bool(enabled),
        "reissued": reissued,
        "message": (
            f"Split включён: РФ напрямую. Переиздано конфигов: {reissued}."
            if enabled else
            f"Split выключен: весь трафик через каскад. Переиздано конфигов: {reissued}."
        ),
    }


def active_entry_map() -> dict[str, dict]:
    """entry_id → инфо активного Xray-каскада (для выдачи клиентов из общей формы).

    Только state == active: каскад реально работает и `create_xray_cascade_client`
    не упадёт. Используется страницей «Серверы»/«Клиенты», чтобы предложить выдачу
    Xray-клиента через каскад на узлах-входах, где локального Xray нет.
    """
    out: dict[str, dict] = {}
    for link in xray_cascade_store.list_links():
        if (link.get("state") or "") != "active":
            continue
        entry_id = link.get("entry_server_id")
        if not entry_id:
            continue
        exit_id = link.get("exit_server_id")
        exit_rec = server_store.get_record(exit_id) if exit_id else None
        out[entry_id] = {
            "exit_server_id": exit_id,
            "exit_name": (exit_rec.get("name") if exit_rec else None),
        }
    return out


def list_xray_cascades() -> list[dict]:
    out: list[dict] = []
    for link in xray_cascade_store.list_links():
        if (link.get("state") or "none") == "none":
            continue
        out.append(link)
    return out
