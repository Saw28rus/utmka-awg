"""Создание клиентов Xray (VLESS-Reality) на сервере Amnezia."""

from __future__ import annotations

import json
import shlex
import time
import uuid
from typing import Optional

from app.schemas.clients import ClientDetail
from app.services.amnezia_link import build_vless_uri, build_xray_native_config, build_xray_vpn_link
from app.services.amnezia_ssh import container_exists, read_container_file, run_container_script
from app.services.client_store import client_store
from app.services.server_store import server_store
from app.services.xray_install import CONTAINER_NAME
from app.services.xray_server_config import (
    SERVER_CONFIG_PATH,
    ensure_monitoring_config,
    make_client_entry,
)
from app.ssh import exec as ssh_exec
PUBLIC_KEY_PATH = "/opt/amnezia/xray/xray_public.key"
SHORT_ID_PATH = "/opt/amnezia/xray/xray_short_id.key"
DEFAULT_FLOW = "xtls-rprx-vision"


class ClientCreateError(Exception):
    pass


def create_xray_client(
    server_id: str,
    name: str,
    *,
    format: str = "both",
    traffic_limit_bytes: Optional[int] = None,
    expires_at: Optional[str] = None,
) -> ClientDetail:
    record = server_store.get_record(server_id)
    target = server_store.ssh_target(server_id)
    if not record or not target:
        raise ClientCreateError("Сервер не найден.")

    try:
        ssh = ssh_exec.connect(
            host=target.host,
            port=target.port,
            username=target.username,
            password=target.password,
            key=target.key,
        )
    except Exception as exc:  # noqa: BLE001
        raise ClientCreateError(f"SSH не отвечает: {exc}") from exc

    try:
        if not container_exists(ssh, CONTAINER_NAME):
            raise ClientCreateError("Xray не установлен на этом сервере. Сначала установи протокол на странице сервера.")
        _ensure_xray_registered(server_id, record, ssh)
        record = server_store.get_record(server_id) or record
        if not _container_running(ssh):
            raise ClientCreateError("Контейнер amnezia-xray не запущен.")

        installed = (record.get("installed_protocols") or {}).get("xray") or {}
        server_config = _read_server_config(ssh)
        inbound = server_config["inbounds"][0]
        stream = inbound.get("streamSettings") or {}
        reality = stream.get("realitySettings") or {}
        site = (reality.get("serverNames") or [None])[0] or reality.get("serverName") or "www.googletagmanager.com"
        port = int(inbound.get("port") or installed.get("port") or 443)
        flow = _client_flow(inbound)

        public_key = read_container_file(ssh, CONTAINER_NAME, PUBLIC_KEY_PATH)
        short_id = read_container_file(ssh, CONTAINER_NAME, SHORT_ID_PATH)
        if not public_key or not short_id:
            raise ClientCreateError("Не найдены Reality-ключи на сервере. Переустанови Xray.")

        client_uuid = str(uuid.uuid4())
        _append_client_to_server(ssh, server_config, client_uuid, flow)

        native_config = build_xray_native_config(
            host=target.host,
            port=port,
            client_uuid=client_uuid,
            flow=flow,
            site=site,
            public_key=public_key,
            short_id=short_id,
        )
        vless_uri = build_vless_uri(
            host=target.host,
            port=port,
            client_uuid=client_uuid,
            flow=flow,
            site=site,
            public_key=public_key,
            short_id=short_id,
            name=name,
        )

        want_config = format in {"both", "config", "json", "awg"}
        want_vpn = format in {"both", "vpn"}
        config_text = vless_uri if want_config else None
        vpn_link = (
            build_xray_vpn_link(host=target.host, native_config_json=native_config, description=record["name"])
            if want_vpn
            else None
        )

        detail = client_store.add_client(
            server_id=server_id,
            server_name=record["name"],
            name=name,
            protocol="xray",
            client_ip="—",
            public_key=client_uuid,
            private_key=None,
            preshared_key=None,
            config_text=config_text,
            vpn_link=vpn_link,
            endpoint=f"{target.host}:{port}",
            imported=False,
            traffic_limit_bytes=traffic_limit_bytes,
            expires_at=expires_at,
        )
        server_store.update_runtime(server_id, active_peers=client_store.count_for_server(server_id))
        return detail
    finally:
        ssh.close()


def delete_xray_client(server_id: str, client_uuid: str) -> bool:
    """Удаляет клиента (UUID) из server.json Xray и горячо перечитывает конфиг.

    Идемпотентно: если контейнера нет или UUID уже отсутствует — успех.
    """
    if not client_uuid:
        return True

    record = server_store.get_record(server_id)
    target = server_store.ssh_target(server_id)
    if not record or not target:
        raise ClientCreateError("Сервер не найден.")

    try:
        ssh = ssh_exec.connect(
            host=target.host,
            port=target.port,
            username=target.username,
            password=target.password,
            key=target.key,
        )
    except Exception as exc:  # noqa: BLE001
        raise ClientCreateError(f"SSH не отвечает: {exc}") from exc

    try:
        if not container_exists(ssh, CONTAINER_NAME):
            return True
        if not _container_running(ssh):
            raise ClientCreateError("Контейнер amnezia-xray не запущен.")

        server_config = _read_server_config(ssh)
        inbound = server_config["inbounds"][0]
        settings = inbound.get("settings") or {}
        clients = list(settings.get("clients") or [])
        new_clients = [
            c for c in clients if not (isinstance(c, dict) and c.get("id") == client_uuid)
        ]
        if len(new_clients) == len(clients):
            return True

        settings["clients"] = new_clients
        inbound["settings"] = settings
        server_config["inbounds"][0] = inbound
        ensure_monitoring_config(server_config)

        payload = json.dumps(server_config, ensure_ascii=False, indent=4)
        script = f"cat > {shlex.quote(SERVER_CONFIG_PATH)} <<'EOF'\n{payload}\nEOF"
        result = run_container_script(ssh, CONTAINER_NAME, script, timeout=30)
        if result.exit_code != 0:
            raise ClientCreateError("Не удалось обновить server.json на сервере.")
        _reload_xray(ssh)
        return True
    finally:
        ssh.close()


def _ensure_xray_registered(server_id: str, record: dict, ssh) -> None:
    if server_store.has_xray(record):
        return
    names = list(record.get("container_names") or [])
    if CONTAINER_NAME not in names:
        names.append(CONTAINER_NAME)
    server_store.update_runtime(
        server_id,
        container_names=names,
        installed_protocols={"xray": {"port": 443, "container": CONTAINER_NAME}},
    )


def _container_running(ssh) -> bool:
    status = ssh_exec.run(
        ssh,
        f"docker inspect -f '{{{{.State.Status}}}}' {shlex.quote(CONTAINER_NAME)} 2>/dev/null || true",
    ).stdout.strip()
    return status == "running"


def _read_server_config(ssh) -> dict:
    raw = read_container_file(ssh, CONTAINER_NAME, SERVER_CONFIG_PATH)
    if not raw:
        raise ClientCreateError("server.json не найден в контейнере Xray.")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ClientCreateError("server.json повреждён.") from exc
    inbounds = data.get("inbounds") or []
    if not inbounds:
        raise ClientCreateError("В server.json нет inbound-конфигурации.")
    return data


def _client_flow(inbound: dict) -> str:
    clients = (inbound.get("settings") or {}).get("clients") or []
    if clients and isinstance(clients[0], dict):
        return clients[0].get("flow") or DEFAULT_FLOW
    return DEFAULT_FLOW


def _append_client_to_server(ssh, server_config: dict, client_uuid: str, flow: str) -> None:
    inbound = server_config["inbounds"][0]
    settings = inbound.setdefault("settings", {})
    clients = list(settings.get("clients") or [])
    clients.append(make_client_entry(client_uuid, flow))
    settings["clients"] = clients
    inbound["settings"] = settings
    server_config["inbounds"][0] = inbound
    ensure_monitoring_config(server_config)

    payload = json.dumps(server_config, ensure_ascii=False, indent=4)
    script = f"cat > {shlex.quote(SERVER_CONFIG_PATH)} <<'EOF'\n{payload}\nEOF"
    result = run_container_script(ssh, CONTAINER_NAME, script, timeout=30)
    if result.exit_code != 0:
        raise ClientCreateError("Не удалось обновить server.json на сервере.")
    _reload_xray(ssh)


def _xray_running(ssh) -> bool:
    proc = ssh_exec.run(
        ssh,
        f"docker exec {shlex.quote(CONTAINER_NAME)} sh -c "
        f"'pgrep -x xray >/dev/null && echo ok || echo fail' 2>/dev/null || true",
        timeout=20,
    ).stdout.strip()
    return proc.endswith("ok")


def _reload_xray(ssh) -> None:
    """Перечитать server.json без рестарта контейнера (XR1 — минимум разрывов).

    Перезапускаем ТОЛЬКО процесс xray внутри живого контейнера: сеть и iptables
    не трогаются, разрыв сокращается с нескольких секунд (full docker restart) до
    долей секунды. Если процесс не поднялся — fail-safe полный docker restart.
    """
    inner = f"killall -KILL xray 2>/dev/null; sleep 1; exec xray -config {shlex.quote(SERVER_CONFIG_PATH)}"
    reload_cmd = f"docker exec -d {shlex.quote(CONTAINER_NAME)} sh -c {shlex.quote(inner)}"
    ssh_exec.run(ssh, reload_cmd, timeout=30)

    time.sleep(2)
    if _xray_running(ssh):
        return
    # Процесс не поднялся (например, гонка) — подстраховываемся полным рестартом.
    _restart_xray(ssh)


def _restart_xray(ssh) -> None:
    """Полный docker restart контейнера (fail-safe и совместимость с Amnezia)."""
    cmd = f"docker restart {shlex.quote(CONTAINER_NAME)}"
    result = ssh_exec.run(ssh, cmd, timeout=90)
    if result.exit_code != 0:
        raise ClientCreateError("Не удалось перезапустить контейнер amnezia-xray.")
    status = ssh_exec.run(
        ssh,
        f"docker inspect -f '{{{{.State.Status}}}}' {shlex.quote(CONTAINER_NAME)}",
        timeout=15,
    ).stdout.strip()
    if status != "running":
        raise ClientCreateError("Контейнер amnezia-xray не запустился после перезагрузки.")


