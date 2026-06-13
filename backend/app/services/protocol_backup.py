"""Снапшоты конфигов/ключей протоколов (UP2, инкремент 1 — безопасный фундамент).

Snapshot — операция ТОЛЬКО НА ЧТЕНИЕ на узле (`docker exec tar`), сервису не
вредит. Это первый и обязательный шаг будущего обновления протокола: прежде
чем пересоздавать контейнер, мы снимаем зашифрованную копию конфига и ключей,
чтобы при любом сбое восстановить рабочее состояние (fail-closed).

Этот модуль НЕ пересоздаёт контейнеры и ничего не меняет на сервере. Apply-путь
(пересборка → пересоздание → restore → health → rollback) добавляется отдельным
инкрементом поверх проверенного снапшота.

См. _dev-docs/MULTI_PROTOCOL_RESILIENCE_PLAN.md §5.7.
"""

from __future__ import annotations

import shlex
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from app.core.crypto import encrypt
from app.services.persistence import read_json, write_json
from app.services.protocol_versions import CONTAINERS, pinned

SNAPSHOTS_FILE = "protocol_snapshots.json"

# Каталог внутри контейнера, где лежат конфиг + ключи протокола.
CONFIG_DIRS: dict[str, str] = {
    "awg2": "/opt/amnezia/awg",
    "awg_legacy": "/opt/amnezia/awg",
    "xray": "/opt/amnezia/xray",
}

# Сколько последних снапшотов держать на пару (узел, протокол).
MAX_PER_KEY = 3


class SnapshotError(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _key(server_id: str, protocol: str) -> str:
    return f"{server_id}:{(protocol or '').lower()}"


def _read() -> dict:
    return read_json(SNAPSHOTS_FILE, {})


def _write(data: dict) -> None:
    write_json(SNAPSHOTS_FILE, data)


def _public(entry: dict) -> dict:
    """Метаданные снапшота без зашифрованного содержимого."""
    return {k: v for k, v in entry.items() if k != "data"}


def supported(protocol: str) -> bool:
    return (protocol or "").lower() in CONFIG_DIRS


def snapshot_protocol(server_id: str, protocol: str) -> dict:
    """Снять зашифрованный снапшот конфига/ключей протокола с узла.

    Возвращает метаданные снапшота. Бросает SnapshotError при проблемах.
    """
    from app.services.amnezia_ssh import connect_target
    from app.ssh import exec as ssh_exec

    pid = (protocol or "").lower()
    config_dir = CONFIG_DIRS.get(pid)
    container = CONTAINERS.get(pid)
    if not config_dir or not container:
        raise SnapshotError(f"Снапшот для протокола «{protocol}» не поддержан.")

    try:
        _rec, _tgt, ssh = connect_target(server_id)
    except Exception as exc:  # noqa: BLE001
        raise SnapshotError(f"Не удалось подключиться к серверу: {exc}") from exc

    try:
        status = ssh_exec.run(
            ssh,
            f"docker inspect -f '{{{{.State.Status}}}}' {shlex.quote(container)} 2>/dev/null || true",
        ).stdout.strip()
        if status != "running":
            raise SnapshotError(f"Контейнер {container} не запущен (status={status or 'absent'}).")

        cmd = (
            f"docker exec {shlex.quote(container)} sh -c "
            f"{shlex.quote(f'tar czf - -C {config_dir} . 2>/dev/null | base64 -w0')}"
        )
        result = ssh_exec.run(ssh, cmd, timeout=180)
        blob_b64 = (result.stdout or "").strip()
        if result.exit_code != 0 or not blob_b64:
            raise SnapshotError("Не удалось снять снапшот конфига (пустой архив).")
    finally:
        ssh.close()

    # Размер исходного tar.gz ≈ 3/4 от длины base64.
    size_bytes = (len(blob_b64) * 3) // 4
    info = pinned(pid) or {}
    entry = {
        "id": str(uuid4()),
        "server_id": server_id,
        "protocol": pid,
        "container": container,
        "config_dir": config_dir,
        "version": info.get("version"),
        "size_bytes": size_bytes,
        "created_at": _now(),
        "data": encrypt(blob_b64) or "",
    }

    data = _read()
    key = _key(server_id, pid)
    items = data.get(key) or []
    items.insert(0, entry)
    data[key] = items[:MAX_PER_KEY]
    _write(data)
    return _public(entry)


def list_snapshots(server_id: str, protocol: str) -> list[dict]:
    data = _read()
    items = data.get(_key(server_id, protocol)) or []
    return [_public(e) for e in items]


def latest_snapshot(server_id: str, protocol: str) -> Optional[dict]:
    items = list_snapshots(server_id, protocol)
    return items[0] if items else None


def forget_node(server_id: str) -> None:
    """Удалить снапшоты узла (при удалении сервера)."""
    data = _read()
    changed = False
    for key in list(data.keys()):
        if key.startswith(f"{server_id}:"):
            del data[key]
            changed = True
    if changed:
        _write(data)


def update_plan(server_id: str, protocol: str) -> dict:
    """План обновления протокола: installed→pinned + статус снапшота + шаги.

    Только чтение. Делает live-reconcile, чтобы показать актуальную картину.
    """
    from app.services.protocol_versions import reconcile_node

    pid = (protocol or "").lower()
    info = pinned(pid) or {}
    items = reconcile_node(server_id)
    current = next((i for i in items if i.get("protocol") == pid), None)

    installed_version = current.get("installed_version") if current else None
    up_to_date = current.get("up_to_date") if current else None
    present = current is not None
    snapshot = latest_snapshot(server_id, pid)

    steps = [
        "Снимок конфига и ключей (зашифрованный бэкап)",
        "Бэкап текущего образа контейнера",
        f"Пересборка образа pinned-версии {info.get('version') or ''}".strip(),
        "Пересоздание контейнера",
        "Восстановление конфига и ключей",
        "Health-проверка протокола",
        "При сбое — авто-откат на прежний образ и конфиг",
    ]

    return {
        "protocol": pid,
        "supported": supported(pid),
        "present": present,
        "installed_version": installed_version,
        "pinned_version": info.get("version"),
        "pinned_image": info.get("image"),
        "up_to_date": up_to_date,
        "update_available": bool(present and up_to_date is False),
        "has_snapshot": snapshot is not None,
        "latest_snapshot": snapshot,
        "steps": steps,
        "note": "Обновление выполняется вручную по одному узлу с подтверждением.",
    }
