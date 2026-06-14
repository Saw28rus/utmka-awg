"""UX1 — импорт клиентов из JSON-бандла (парный к export).

Принцип «Калашникова»: пересоздаём клиентов через уже протестированный
`create_client` движка (новые ключи), а не инъектируем готовый peer (это
отдельный, более рискованный инкремент). Перенос: имя, протокол, лимит трафика,
срок, keepalive. Сначала dry-run (план + конфликты), затем apply с изоляцией
ошибок по каждому клиенту.

Честно: при пересоздании генерируются НОВЫЕ ключи, поэтому старые конфиги/QR не
подходят — клиентам нужно раздать новые. Это ожидаемо при восстановлении/миграции.
"""

from __future__ import annotations

from typing import Optional

from app.schemas.clients import ClientImportItem, ClientImportResult
from app.services.client_store import client_store
from app.services.protocol_engine import ClientSpec, get_engine
from app.services.server_store import server_store

SUPPORTED_PROTOCOLS = ("awg2", "awg_legacy", "xray")
MAX_IMPORT = 1000


def _existing_names(server_id: str) -> set[str]:
    return {
        (c.name or "").strip().lower()
        for c in client_store.list_all(server_id=server_id)
    }


def _plan(bundle: dict, target_server_id: Optional[str]) -> list[dict]:
    """Строит план без побочных эффектов: что создать, что пропустить, ошибки."""
    raw = bundle.get("clients")
    if not isinstance(raw, list):
        raise ValueError("Некорректный бандл: нет списка clients.")
    if len(raw) > MAX_IMPORT:
        raise ValueError(f"Слишком много клиентов в бандле (>{MAX_IMPORT}).")

    # Кэш имён по целевому серверу, чтобы не дёргать store на каждый элемент.
    names_cache: dict[str, set[str]] = {}
    plan: list[dict] = []

    for entry in raw:
        if not isinstance(entry, dict):
            plan.append({"name": "—", "protocol": "awg2", "action": "error", "reason": "битая запись"})
            continue
        name = str(entry.get("name") or "").strip()
        protocol = str(entry.get("protocol") or "awg2").lower()
        source = entry.get("server_id")
        target = (target_server_id or source) or None

        item = {
            "name": name or "—",
            "source_server_id": source,
            "target_server_id": target,
            "protocol": protocol,
            "raw": entry,
        }

        if not name:
            plan.append({**item, "action": "error", "reason": "нет имени"})
            continue
        if protocol not in SUPPORTED_PROTOCOLS:
            plan.append({**item, "action": "error", "reason": f"протокол {protocol} не поддерживается"})
            continue
        if not target:
            plan.append({**item, "action": "error", "reason": "не указан сервер"})
            continue
        if not server_store.get_record(target):
            plan.append({**item, "action": "error", "reason": "сервер не найден в панели"})
            continue

        if target not in names_cache:
            names_cache[target] = _existing_names(target)
        if name.lower() in names_cache[target]:
            plan.append({**item, "action": "skip", "reason": "клиент с таким именем уже есть"})
            continue

        # Резервируем имя в кэше, чтобы дубли внутри бандла не плодились.
        names_cache[target].add(name.lower())
        plan.append({**item, "action": "create"})

    return plan


def _to_item(p: dict) -> ClientImportItem:
    return ClientImportItem(
        name=p.get("name") or "—",
        source_server_id=p.get("source_server_id"),
        target_server_id=p.get("target_server_id"),
        protocol=p.get("protocol") or "awg2",
        action=p["action"],
        reason=p.get("reason"),
        client_id=p.get("client_id"),
    )


def run_import(bundle: dict, *, target_server_id: Optional[str], dry_run: bool) -> ClientImportResult:
    """Блокирующая (SSH при apply) — вызывать через asyncio.to_thread."""
    plan = _plan(bundle, target_server_id)

    if not dry_run:
        for p in plan:
            if p["action"] != "create":
                continue
            entry = p.get("raw") or {}
            spec = ClientSpec(
                server_id=p["target_server_id"],
                name=p["name"],
                protocol=p["protocol"],
                format="both",
                traffic_limit_bytes=entry.get("traffic_limit_bytes"),
                expires_at=entry.get("expires_at"),
                keepalive=int(entry.get("keepalive") or 25),
            )
            try:
                detail = get_engine(p["protocol"]).create_client(spec)
                p["client_id"] = detail.id
            except Exception as exc:  # noqa: BLE001
                p["action"] = "error"
                p["reason"] = str(exc)

    items = [_to_item(p) for p in plan]
    return ClientImportResult(
        dry_run=dry_run,
        total=len(items),
        to_create=sum(1 for i in items if i.action == "create"),
        to_skip=sum(1 for i in items if i.action == "skip"),
        errors=sum(1 for i in items if i.action == "error"),
        items=items,
    )
