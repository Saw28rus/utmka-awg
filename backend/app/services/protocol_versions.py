"""Манифест версий протоколов (UP1).

Единый источник правды о том, какие версии движков ПАНЕЛЬ сейчас шипит
(`PINNED` — должен совпадать с Dockerfile'ами в `data/amnezia_scripts/*`),
плюс файл-манифест `protocol_versions.json` в `panel_data`: что реально
установлено на каждом узле (пишется при install, сверяется reconcile).

Принцип «калашникова»: версию НЕ парсим хрупкими `--version`. Протоколы
собираются из наших pinned-Dockerfile, поэтому `installed_version` — это то,
что мы сами установили. `reconcile_node` лишь подтверждает наличие/работу
контейнера и ловит расхождения (например, панель переустановили и манифест
потерян → `needs_adopt`), ничего не затирая молча.

См. _dev-docs/MULTI_PROTOCOL_RESILIENCE_PLAN.md §5.4.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from app.services.persistence import read_json, write_json

MANIFEST_FILE = "protocol_versions.json"

# Должно совпадать с pinned-версиями в Dockerfile'ах (Defect D2 уже закрыт).
PINNED: dict[str, dict[str, str]] = {
    "awg2": {
        "version": "0.2.18",
        "image": "amneziavpn/amneziawg-go:0.2.18",
    },
    "awg_legacy": {
        "version": "amnezia-wg@sha256:ea050861",
        "image": "amneziavpn/amnezia-wg@sha256:ea050861bd2012a6265817636ce7c0c15764ef955782d953cef42e05c1381250",
    },
    "xray": {
        "version": "v25.8.3",
        "image": "XTLS/Xray-core v25.8.3 (build from source)",
    },
    "telemt": {
        "version": "3.4.18",
        "image": "telemt 3.4.18 (build from source)",
    },
}

# Имя docker-контейнера на узле для каждого протокола.
CONTAINERS: dict[str, str] = {
    "awg2": "amnezia-awg2",
    "awg_legacy": "amnezia-awg",
    "xray": "amnezia-xray",
    "telemt": "amnezia-telemt",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def pinned(protocol: str) -> Optional[dict]:
    """Что панель шипит для протокола (version/image) или None."""
    return PINNED.get((protocol or "").lower())


def _read() -> dict:
    return read_json(MANIFEST_FILE, {})


def _write(data: dict) -> None:
    write_json(MANIFEST_FILE, data)


def record_install(server_id: str, protocol: str) -> None:
    """Зафиксировать в манифесте, что на узле установлена pinned-версия.

    Вызывается после успешной установки протокола. Для протоколов без пиннинга
    (например, wireguard) — no-op.
    """
    pid = (protocol or "").lower()
    info = PINNED.get(pid)
    if not info:
        return
    data = _read()
    node = data.setdefault(server_id, {})
    node[pid] = {
        "installed_version": info["version"],
        "image_ref": info["image"],
        "installed_at": _now(),
    }
    _write(data)


def forget_node(server_id: str) -> None:
    """Удалить запись узла из манифеста (при удалении сервера)."""
    data = _read()
    if server_id in data:
        del data[server_id]
        _write(data)


def _present_protocols(record: dict) -> list[str]:
    """Какие протоколы реально присутствуют на узле (по store-данным)."""
    containers = set(record.get("container_names") or [])
    installed = record.get("installed_protocols") or {}
    present: list[str] = []
    if "xray" in installed or "amnezia-xray" in containers:
        present.append("xray")
    if "awg2" in installed or "amnezia-awg2" in containers:
        present.append("awg2")
    if "amnezia-awg" in containers and "awg2" not in present:
        present.append("awg_legacy")
    if "telemt" in installed or "amnezia-telemt" in containers:
        present.append("telemt")
    return present


def reconcile_node(server_id: str) -> list[dict]:
    """Сверить узел: что присутствует, что запущено, актуальна ли версия.

    Возвращает список записей по протоколам. Никогда не бросает наружу —
    сетевые/SSH-сбои деградируют в `status="unreachable"`.
    """
    from app.services.amnezia_ssh import connect_target
    from app.services.server_store import server_store
    from app.ssh import exec as ssh_exec

    record = server_store.get_record(server_id)
    if not record:
        return []

    present = _present_protocols(record)
    manifest = _read()
    node_manifest = manifest.get(server_id, {})

    statuses: dict[str, str] = {}
    if present:
        ssh = None
        try:
            _rec, _tgt, ssh = connect_target(server_id)
        except Exception:  # noqa: BLE001
            ssh = None
        if ssh is not None:
            try:
                for pid in present:
                    container = CONTAINERS.get(pid)
                    if not container:
                        continue
                    out = ssh_exec.run(
                        ssh,
                        f"docker inspect -f '{{{{.State.Status}}}}' {container} 2>/dev/null || true",
                    ).stdout.strip()
                    statuses[pid] = out or "absent"
            except Exception:  # noqa: BLE001
                pass
            finally:
                ssh.close()
        else:
            statuses = {pid: "unreachable" for pid in present}

    result: list[dict] = []
    for pid in present:
        info = PINNED.get(pid, {})
        pinned_version = info.get("version")
        rec = node_manifest.get(pid)
        installed_version = rec.get("installed_version") if rec else None
        status = statuses.get(pid, "absent")
        running = status == "running"
        needs_adopt = rec is None
        if installed_version is None or pinned_version is None:
            up_to_date: Optional[bool] = None
        else:
            up_to_date = installed_version == pinned_version

        entry = node_manifest.setdefault(pid, {})
        entry["running"] = running
        entry["status"] = status
        entry["reconciled_at"] = _now()

        result.append(
            {
                "protocol": pid,
                "container": CONTAINERS.get(pid),
                "running": running,
                "status": status,
                "pinned_version": pinned_version,
                "installed_version": installed_version,
                "up_to_date": up_to_date,
                "needs_adopt": needs_adopt,
            }
        )

    manifest[server_id] = node_manifest
    _write(manifest)
    return result
