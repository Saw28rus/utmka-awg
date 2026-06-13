"""Xray Masking Center — инкремент 1: инспектор Reality (read-only).

Читает server.json с управляемого сервера, достаёт параметры маскировки Reality
(`dest`, `serverNames`/SNI, `shortIds`, порт, `flow`), делает лёгкую проверку
достижимости dest и считает «Reality posture» (strong/basic/weak/invalid). Ничего
на сервере не меняет.

Apply (смена dest/SNI/shortId/порта с reissue клиентов + health + rollback) —
следующий инкремент. См. _dev-docs/MULTI_PROTOCOL_RESILIENCE_PLAN.md §5.6.

ВАЖНО: статус — это качество настройки маскировки, а НЕ гарантия обхода DPI.
"""

from __future__ import annotations

import json
import re
import shlex
from typing import Optional

from app.services.amnezia_ssh import read_container_file
from app.services.server_store import server_store
from app.services.xray_install import CONTAINER_NAME
from app.services.xray_server_config import SERVER_CONFIG_PATH
from app.ssh import exec as ssh_exec

STATUS_STRONG = "strong"
STATUS_BASIC = "basic"
STATUS_WEAK = "weak"
STATUS_INVALID = "invalid"
STATUS_UNKNOWN = "unknown"

_LABELS = {
    STATUS_STRONG: "Сильная маскировка Reality",
    STATUS_BASIC: "Reality базовая",
    STATUS_WEAK: "Слабая маскировка Reality",
    STATUS_INVALID: "Конфиг Reality невалиден",
    STATUS_UNKNOWN: "Неизвестно",
}

DEFAULT_FLOW = "xtls-rprx-vision"
_IP_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def _is_ip(host: str) -> bool:
    return bool(_IP_RE.match(host or ""))


def _is_domain(host: str) -> bool:
    return bool(host) and "." in host and not _is_ip(host)


def inspect_xray_masking(server_id: str, *, probe_dest: bool = True) -> dict:
    """Инспектор Reality-маскировки узла (read-only)."""
    record = server_store.get_record(server_id)
    target = server_store.ssh_target(server_id)
    if not record or not target:
        return {"present": False, "status": STATUS_UNKNOWN, "message": "Сервер не найден."}

    if not server_store.has_xray(record):
        return {"present": False, "status": STATUS_UNKNOWN, "message": "Xray не установлен на сервере."}

    try:
        ssh = ssh_exec.connect(
            host=target.host,
            port=target.port,
            username=target.username,
            password=target.password,
            key=target.key,
        )
    except Exception as exc:  # noqa: BLE001
        return {"present": True, "status": STATUS_UNKNOWN, "message": f"SSH не отвечает: {exc}"}

    try:
        status_raw = ssh_exec.run(
            ssh,
            f"docker inspect -f '{{{{.State.Status}}}}' {shlex.quote(CONTAINER_NAME)} 2>/dev/null || true",
        ).stdout.strip()
        running = status_raw == "running"

        raw = read_container_file(ssh, CONTAINER_NAME, SERVER_CONFIG_PATH)
        if not raw:
            return {"present": True, "running": running, "status": STATUS_INVALID,
                    "label": _LABELS[STATUS_INVALID], "message": "server.json не найден."}
        try:
            config = json.loads(raw)
        except json.JSONDecodeError:
            return {"present": True, "running": running, "status": STATUS_INVALID,
                    "label": _LABELS[STATUS_INVALID], "message": "server.json повреждён."}

        inbound = (config.get("inbounds") or [{}])[0]
        stream = inbound.get("streamSettings") or {}
        security = stream.get("security")
        reality = stream.get("realitySettings") or {}

        port = inbound.get("port")
        dest = reality.get("dest") or ""
        dest_host = dest.split(":")[0] if dest else ""
        server_names = list(reality.get("serverNames") or [])
        short_ids = list(reality.get("shortIds") or [])
        sni = server_names[0] if server_names else None

        clients = (inbound.get("settings") or {}).get("clients") or []
        flow = None
        if clients and isinstance(clients[0], dict):
            flow = clients[0].get("flow")

        if security != "reality":
            return {
                "present": True, "running": running, "status": STATUS_INVALID,
                "label": _LABELS[STATUS_INVALID],
                "message": f"streamSettings.security = {security or 'нет'} (ожидался reality).",
                "port": port,
            }

        dest_reachable: Optional[bool] = None
        if probe_dest and dest_host:
            dest_reachable = _probe_tcp(ssh, dest_host, 443)

        critical: list[str] = []
        notes: list[str] = []

        if not server_names:
            critical.append("Не задан serverNames (SNI) — Reality не маскируется.")
        if not dest_host:
            critical.append("Не задан dest — некуда проксировать TLS-хендшейк.")
        else:
            if _is_ip(dest_host):
                critical.append("dest указывает на IP, а не на реальный TLS-сайт.")
            elif dest_host == target.host:
                critical.append("dest указывает на наш сервер — это узнаваемо для DPI.")
            elif not _is_domain(dest_host):
                notes.append("dest не похож на домен реального сайта.")
        if sni and dest_host and sni != dest_host:
            notes.append("serverName (SNI) не совпадает с доменом dest.")
        if not short_ids:
            critical.append("Нет shortIds — Reality не примет клиентов.")
        elif len(short_ids) == 1:
            notes.append("Один shortId — можно добавить несколько для ротации.")
        if (flow or DEFAULT_FLOW) != DEFAULT_FLOW:
            notes.append(f"flow = {flow!r}; рекомендуется {DEFAULT_FLOW}.")
        if port and int(port) != 443:
            notes.append(f"Нестандартный порт {port} — убедись в nginx SNI-split.")
        if dest_reachable is False:
            notes.append("dest не отвечает по TCP:443 — проверь домен маскировки.")

        if critical:
            status = STATUS_WEAK
        elif notes:
            status = STATUS_BASIC
        else:
            status = STATUS_STRONG

        return {
            "present": True,
            "running": running,
            "status": status,
            "label": _LABELS[status],
            "port": port,
            "dest": dest,
            "dest_host": dest_host,
            "dest_reachable": dest_reachable,
            "server_names": server_names,
            "sni": sni,
            "short_ids": short_ids,
            "short_id_count": len(short_ids),
            "flow": flow or DEFAULT_FLOW,
            "clients_count": len([c for c in clients if isinstance(c, dict) and c.get("id")]),
            "critical": critical,
            "notes": notes,
        }
    finally:
        ssh.close()


def _probe_tcp(ssh, host: str, port: int) -> Optional[bool]:
    """Лёгкая проверка достижимости host:port по TCP с самого VPS (без TLS)."""
    cmd = (
        f"timeout 5 bash -c 'exec 3<>/dev/tcp/{shlex.quote(host)}/{port}' "
        f">/dev/null 2>&1 && echo ok || echo fail"
    )
    out = ssh_exec.run(ssh, cmd, timeout=15).stdout.strip()
    if out.endswith("ok"):
        return True
    if out.endswith("fail"):
        return False
    return None
