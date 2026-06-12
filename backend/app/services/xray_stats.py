"""Сбор трафика и активности Xray-клиентов через Stats API."""

from __future__ import annotations

import json
import re
import shlex
import time

from app.services.awg_config import PeerTransfer
from app.services.amnezia_ssh import read_container_file
from app.services.server_store import server_store
from app.services.xray_client import _restart_xray
from app.services.xray_install import CONTAINER_NAME
from app.services.xray_server_config import SERVER_CONFIG_PATH, ensure_monitoring_config, write_server_config
from app.ssh import exec as ssh_exec

STATS_API = "127.0.0.1:10085"
_USER_STAT_RE = re.compile(
    r"^user>>>(?P<email>[^>]+)>>>traffic>>>(?P<direction>uplink|downlink)$"
)


def fetch_xray_stats(ssh, server_id: str) -> dict[str, PeerTransfer]:
    """Возвращает статистику по UUID клиента (email в server.json)."""
    record = server_store.get_record(server_id)
    if not record or not server_store.has_xray(record):
        return {}

    if not _container_running(ssh):
        return {}

    stats = _query_stats(ssh)
    if stats:
        return stats

    if _repair_monitoring(ssh):
        stats = _query_stats(ssh)
    return stats


def _container_running(ssh) -> bool:
    status = ssh_exec.run(
        ssh,
        f"docker inspect -f '{{{{.State.Status}}}}' {shlex.quote(CONTAINER_NAME)} 2>/dev/null || true",
    ).stdout.strip()
    return status == "running"


def _query_stats(ssh) -> dict[str, PeerTransfer]:
    cmd = (
        f"docker exec {shlex.quote(CONTAINER_NAME)} "
        f"xray api statsquery --server={STATS_API} -pattern 'user>>>' 2>/dev/null || true"
    )
    raw = ssh_exec.run(ssh, cmd, timeout=20).stdout.strip()
    if not raw or raw == "{}":
        return {}

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}

    partial: dict[str, dict[str, int]] = {}
    for item in payload.get("stat") or []:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not name:
            continue
        match = _USER_STAT_RE.match(name)
        if not match:
            continue
        email = match.group("email")
        direction = match.group("direction")
        try:
            value = int(item.get("value") or 0)
        except (TypeError, ValueError):
            value = 0
        partial.setdefault(email, {})[direction] = value

    result: dict[str, PeerTransfer] = {}
    for email, dirs in partial.items():
        uplink = dirs.get("uplink", 0)
        downlink = dirs.get("downlink", 0)
        # uplink = сервер → клиент (скачивание), downlink = клиент → сервер (отдача)
        result[email] = PeerTransfer(rx_bytes=downlink, tx_bytes=uplink, handshake_unix=0)
    return result


def _repair_monitoring(ssh) -> bool:
    raw = read_container_file(ssh, CONTAINER_NAME, SERVER_CONFIG_PATH)
    if not raw:
        return False
    try:
        server_config = json.loads(raw)
    except json.JSONDecodeError:
        return False
    if not ensure_monitoring_config(server_config):
        return False
    if not write_server_config(ssh, server_config):
        return False
    _restart_xray(ssh)
    return True
