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
    port = int(relay_port or exit_port or DEFAULT_RELAY_PORT)

    entry_ssh = _connect(entry_id)
    try:
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
    xray_cascade_store.upsert_link(
        entry_id,
        exit_server_id=exit_id,
        relay_port=port,
        exit_port=exit_port,
        exit_host=exit_host,
        sni=sni,
        state=state,
        last_preflight_at=datetime.now(timezone.utc).isoformat(),
        last_preflight_ok=ok,
        message="Проверка пройдена — можно включать relay." if ok
                else "Проверка выявила блокеры.",
    )
    return {
        "ok": ok, "entry_server_id": entry_id, "exit_server_id": exit_id,
        "relay_port": port, "exit_port": exit_port, "sni": sni,
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
    if not exit_host:
        raise XrayCascadeError("Не задан exit-сервер.")

    steps: list[dict] = []
    entry_ssh = _connect(entry_id)
    try:
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
        entry_id, state="active",
        last_applied_at=datetime.now(timezone.utc).isoformat(),
        message="Xray-каскад активен: клиент → entry → exit.",
    )
    return {"ok": True, "state": "active", "entry_server_id": entry_id,
            "exit_server_id": link.get("exit_server_id"), "relay_port": relay_port,
            "steps": steps, "message": "Xray-каскад включён."}


def xray_cascade_rollback(entry_id: str) -> dict:
    link = xray_cascade_store.get_link(entry_id)
    if not link:
        raise XrayCascadeError("Xray-каскад для этого сервера не найден.")
    relay_port = int(link.get("relay_port") or DEFAULT_RELAY_PORT)
    entry_ssh = _connect(entry_id)
    try:
        ssh_exec.run(entry_ssh, _relay_down_script(relay_port), timeout=30)
    finally:
        entry_ssh.close()
    xray_cascade_store.upsert_link(entry_id, state="down",
                                   message="Relay снят. Каскад выключен.")
    return {"ok": True, "state": "down", "entry_server_id": entry_id,
            "message": "Xray-каскад выключен."}


# ---------------------------------------------------------------------------
# Статус
# ---------------------------------------------------------------------------


def xray_cascade_status(entry_id: str) -> dict:
    link = xray_cascade_store.get_link(entry_id) or {}
    exit_id = link.get("exit_server_id")
    exit_rec = server_store.get_record(exit_id) if exit_id else None
    state = link.get("state") or "none"
    live_active = False
    if state == "active" and link.get("relay_port"):
        try:
            ssh = _connect(entry_id)
            try:
                live_active = _relay_alive(ssh, int(link["relay_port"]))
            finally:
                ssh.close()
        except XrayCascadeError:
            live_active = False
        if not live_active:
            state = "down"
            xray_cascade_store.upsert_link(entry_id, state="down",
                                           message="Relay не обнаружен на entry (возможно перезагрузка).")
    return {
        "entry_server_id": entry_id,
        "exit_server_id": exit_id,
        "exit_name": exit_rec.get("name") if exit_rec else None,
        "relay_port": link.get("relay_port"),
        "exit_port": link.get("exit_port"),
        "sni": link.get("sni"),
        "state": state,
        "live_active": live_active,
        "last_preflight_ok": link.get("last_preflight_ok", False),
        "last_applied_at": link.get("last_applied_at"),
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
        )
    except ClientCreateError as exc:
        raise XrayCascadeError(str(exc)) from exc


def list_xray_cascades() -> list[dict]:
    out: list[dict] = []
    for link in xray_cascade_store.list_links():
        if (link.get("state") or "none") == "none":
            continue
        out.append(link)
    return out
