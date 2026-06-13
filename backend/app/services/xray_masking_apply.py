"""Xray Masking Center — инкремент 2: apply Reality-маскировки.

Управляемая смена параметров маскировки Reality с защитой («калашников»):
- snapshot server.json (в память для мгновенного отката + персист через
  protocol_backup);
- смена домена маскировки (dest + serverNames/SNI) валидируется и проверяется на
  достижимость dest:443 ДО применения (fail-closed — не ломаем рабочий сетап);
- запись server.json → горячий reload xray (XR1) → health (процесс xray поднялся);
- при сбое — откат server.json из снапшота и reload;
- после успешной смены домена — reissue (переиздание конфигов) всех Xray-клиентов
  узла, т.к. SNI зашит в клиентском конфиге (инвариант §5.6).

Добавление shortId reissue не требует (существующие клиенты не затрагиваются).
"""

from __future__ import annotations

import copy
import json
import secrets

from app.services.amnezia_link import (
    build_vless_uri,
    build_xray_native_config,
    build_xray_vpn_link,
)
from app.services.amnezia_ssh import read_container_file
from app.services.client_store import client_store
from app.services.server_store import server_store
from app.services.xray_client import (
    DEFAULT_FLOW,
    PUBLIC_KEY_PATH,
    SHORT_ID_PATH,
    _reload_xray,
    _xray_running,
)
from app.services.xray_install import CONTAINER_NAME
from app.services.xray_masking import _is_domain, _probe_tcp
from app.services.xray_server_config import (
    SERVER_CONFIG_PATH,
    ensure_monitoring_config,
    write_server_config,
)
from app.ssh import exec as ssh_exec


class MaskingApplyError(Exception):
    pass


def _connect(server_id: str):
    record = server_store.get_record(server_id)
    target = server_store.ssh_target(server_id)
    if not record or not target:
        raise MaskingApplyError("Сервер не найден.")
    if not server_store.has_xray(record):
        raise MaskingApplyError("Xray не установлен на сервере.")
    try:
        ssh = ssh_exec.connect(
            host=target.host,
            port=target.port,
            username=target.username,
            password=target.password,
            key=target.key,
        )
    except Exception as exc:  # noqa: BLE001
        raise MaskingApplyError(f"SSH не отвечает: {exc}") from exc
    return record, target, ssh


def _read_config_raw(ssh) -> str:
    raw = read_container_file(ssh, CONTAINER_NAME, SERVER_CONFIG_PATH)
    if not raw:
        raise MaskingApplyError("server.json не найден в контейнере Xray.")
    return raw


def _parse(raw: str) -> dict:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MaskingApplyError("server.json повреждён.") from exc
    if not (data.get("inbounds") or []):
        raise MaskingApplyError("В server.json нет inbound-конфигурации.")
    return data


def _persist_snapshot(server_id: str) -> None:
    """Персистентный зашифрованный снапшот (best-effort, не блокирует apply)."""
    try:
        from app.services.protocol_backup import snapshot_protocol

        snapshot_protocol(server_id, "xray")
    except Exception:  # noqa: BLE001
        pass


def _restore(ssh, snapshot: dict) -> None:
    try:
        write_server_config(ssh, snapshot)
        _reload_xray(ssh)
    except Exception:  # noqa: BLE001
        pass


def _apply_and_health(ssh, config: dict, snapshot: dict) -> None:
    ensure_monitoring_config(config)
    if not write_server_config(ssh, config):
        raise MaskingApplyError("Не удалось записать server.json на сервере.")
    _reload_xray(ssh)
    if not _xray_running(ssh):
        _restore(ssh, snapshot)
        raise MaskingApplyError(
            "Xray не поднялся с новым конфигом — выполнен откат на прежние настройки."
        )


def _reality(config: dict) -> tuple[dict, dict, dict]:
    inbound = config["inbounds"][0]
    stream = inbound.setdefault("streamSettings", {})
    reality = stream.setdefault("realitySettings", {})
    return inbound, stream, reality


def set_masking_domain(server_id: str, site: str) -> dict:
    """Сменить домен маскировки Reality (dest + SNI) и переиздать клиентов."""
    site = (site or "").strip().rstrip("/")
    if "://" in site:
        site = site.split("://", 1)[1]
    site = site.split("/")[0]
    if not _is_domain(site):
        raise MaskingApplyError("Укажи домен реального TLS-сайта (например, www.microsoft.com).")

    record, target, ssh = _connect(server_id)
    try:
        if site == target.host:
            raise MaskingApplyError("Домен маскировки не должен указывать на наш сервер.")
        if _probe_tcp(ssh, site, 443) is False:
            raise MaskingApplyError(
                f"Домен {site} не отвечает по TCP:443. Выбери доступный TLS-сайт."
            )

        _persist_snapshot(server_id)
        config = _parse(_read_config_raw(ssh))
        snapshot = copy.deepcopy(config)

        inbound, _stream, reality = _reality(config)
        reality["dest"] = f"{site}:443"
        reality["serverNames"] = [site]

        _apply_and_health(ssh, config, snapshot)

        port = int(inbound.get("port") or 443)
        flow = _flow(inbound)
        reissued = _reissue_clients(ssh, server_id, record, site=site, port=port, flow=flow)

        return {
            "status": "ok",
            "site": site,
            "reissued": reissued,
            "message": f"Домен маскировки изменён на {site}. Переиздано конфигов: {reissued}.",
        }
    finally:
        ssh.close()


def add_short_id(server_id: str) -> dict:
    """Добавить новый shortId в Reality (существующие клиенты не затрагиваются)."""
    record, _target, ssh = _connect(server_id)
    try:
        _persist_snapshot(server_id)
        config = _parse(_read_config_raw(ssh))
        snapshot = copy.deepcopy(config)
        _inbound, _stream, reality = _reality(config)

        short_ids = [s for s in (reality.get("shortIds") or []) if isinstance(s, str)]
        new_id = secrets.token_hex(8)
        while new_id in short_ids:
            new_id = secrets.token_hex(8)
        short_ids.append(new_id)
        reality["shortIds"] = short_ids

        _apply_and_health(ssh, config, snapshot)
        return {
            "status": "ok",
            "short_id": new_id,
            "short_id_count": len(short_ids),
            "message": f"Добавлен shortId. Всего: {len(short_ids)}.",
        }
    finally:
        ssh.close()


def _flow(inbound: dict) -> str:
    clients = (inbound.get("settings") or {}).get("clients") or []
    if clients and isinstance(clients[0], dict):
        return clients[0].get("flow") or DEFAULT_FLOW
    return DEFAULT_FLOW


def _reissue_clients(ssh, server_id: str, record: dict, *, site: str, port: int, flow: str) -> int:
    host = record["host"]
    pbk = read_container_file(ssh, CONTAINER_NAME, PUBLIC_KEY_PATH)
    sid = read_container_file(ssh, CONTAINER_NAME, SHORT_ID_PATH)
    if not pbk or not sid:
        return 0

    from app.services.xray_cascade_store import xray_cascade_store

    count = 0
    for tgt in client_store.reissue_targets(server_id, "xray"):
        uuid = tgt.get("public_key")
        if not uuid:
            continue
        # Каскадные клиенты адресованы на entry:relay_port (а не на exit/прямой host).
        entry_id = tgt.get("channel_entry_id")
        c_host, c_port, split_ru, descr = host, port, False, record["name"]
        if entry_id:
            link = xray_cascade_store.get_link(entry_id)
            entry_rec = server_store.get_record(entry_id)
            if link and entry_rec and entry_rec.get("host"):
                c_host = entry_rec["host"]
                c_port = int(link.get("relay_port") or port)
                split_ru = bool(link.get("split_ru"))
                descr = entry_rec.get("name") or c_host
        native = build_xray_native_config(
            host=c_host, port=c_port, client_uuid=uuid, flow=flow, site=site,
            public_key=pbk, short_id=sid, split_ru=split_ru,
        )
        uri = build_vless_uri(
            host=c_host, port=c_port, client_uuid=uuid, flow=flow, site=site,
            public_key=pbk, short_id=sid, name=tgt["name"],
        )
        config_text = uri if tgt["has_config"] else None
        vpn_link = (
            build_xray_vpn_link(host=c_host, native_config_json=native, description=descr)
            if tgt["has_vpn"]
            else None
        )
        client_store.update_issued_config(
            tgt["id"], config_text=config_text, vpn_link=vpn_link, endpoint=f"{c_host}:{c_port}"
        )
        count += 1
    return count
