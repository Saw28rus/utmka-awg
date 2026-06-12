"""Блокировка/разблокировка клиентов на сервере по лимиту трафика и сроку.

Источник правды — файл конфигурации awg на сервере. Чтобы заблокировать клиента,
удаляем его [Peer]-блок из конфига (сохранив его текст для восстановления) и
делаем `awg syncconf` — peer пропадает с живого интерфейса. Разблокировка —
обратное добавление сохранённого блока. Ссылка/конфиг клиента при этом не меняются:
как только лимит поднят или срок продлён, peer возвращается на сервер.
"""

import posixpath
import re

from app.services.awg_client import (
    _backup_config,
    _overwrite_file,
    _sync_config,
)
from app.services.awg_detect import _container_names, _locate_config
from app.services.client_store import client_store
from app.services.server_store import server_store
from app.ssh import exec as ssh_exec

_PEER_SPLIT = re.compile(r"(?im)^[ \t]*\[Peer\][ \t]*$")
_PUBKEY = re.compile(r"(?im)^[ \t]*PublicKey[ \t]*=[ \t]*(.+?)[ \t]*$")


def enforce_server(ssh, record: dict, server_id: str) -> int:
    """Сверяет желаемое состояние клиентов с конфигом и применяет изменения.

    Возвращает число изменённых peer'ов.
    """
    view = client_store.enforcement_view(server_id)
    if not view:
        return 0

    containers = record.get("container_names") or _container_names(ssh)
    config_path, container, config_text = _locate_config(ssh, containers)
    if not container or not config_text.strip():
        return 0
    iface = posixpath.splitext(posixpath.basename(config_path))[0]

    head, blocks_by_pub, order = _parse_blocks(config_text)

    pending_flags: list[tuple[str, bool, str | None]] = []
    changed = False

    for item in view:
        pub = item["public_key"]
        in_config = pub in blocks_by_pub

        if item["should_block"]:
            if in_config:
                # сохраняем блок и убираем из конфига
                saved = blocks_by_pub.pop(pub)
                order.remove(pub)
                pending_flags.append((item["id"], True, saved))
                changed = True
            elif not item["blocked_on_server"]:
                pending_flags.append((item["id"], True, item.get("peer_block")))
        else:
            if not in_config and item["blocked_on_server"] and item.get("peer_block"):
                # возвращаем сохранённый блок обратно
                block = item["peer_block"].strip("\n")
                blocks_by_pub[pub] = block
                order.append(pub)
                pending_flags.append((item["id"], False, None))
                changed = True
            elif in_config and item["blocked_on_server"]:
                pending_flags.append((item["id"], False, None))

    if changed:
        new_config = _rebuild(head, blocks_by_pub, order)
        _backup_config(ssh, container, config_path)
        _overwrite_file(ssh, container, config_path, new_config)
        _sync_config(ssh, container, iface, config_path)

    for client_id, blocked, peer_block in pending_flags:
        client_store.set_blocked(client_id, blocked, peer_block)

    return len(pending_flags)


def enforce_server_by_id(server_id: str) -> int:
    record = server_store.get_record(server_id)
    target = server_store.ssh_target(server_id)
    if not record or not target:
        return 0
    try:
        ssh = ssh_exec.connect(
            host=target.host,
            port=target.port,
            username=target.username,
            password=target.password,
            key=target.key,
        )
    except Exception:  # noqa: BLE001
        return 0
    try:
        changed = enforce_server(ssh, record, server_id)
        try:
            from app.services.xray_enforce import enforce_xray_server

            changed += enforce_xray_server(ssh, server_id)
        except Exception:  # noqa: BLE001
            pass
        return changed
    finally:
        ssh.close()


def _parse_blocks(config_text: str) -> tuple[str, dict[str, str], list[str]]:
    parts = _PEER_SPLIT.split(config_text)
    head = parts[0]
    blocks_by_pub: dict[str, str] = {}
    order: list[str] = []
    for body in parts[1:]:
        match = _PUBKEY.search(body)
        if not match:
            continue
        pub = match.group(1).strip()
        blocks_by_pub[pub] = "[Peer]\n" + body.strip("\n")
        order.append(pub)
    return head, blocks_by_pub, order


def _rebuild(head: str, blocks_by_pub: dict[str, str], order: list[str]) -> str:
    chunks = [head.rstrip("\n")]
    for pub in order:
        block = blocks_by_pub.get(pub)
        if block:
            chunks.append(block.strip("\n"))
    return "\n\n".join(chunk for chunk in chunks if chunk) + "\n"
