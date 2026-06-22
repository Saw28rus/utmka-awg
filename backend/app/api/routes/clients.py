import asyncio
from typing import Optional

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import client_ip, get_current_user, require_client_manager
from app.schemas.auth import CurrentUser
from app.schemas.clients import (
    ClientCreate,
    ClientDetail,
    ClientExportRequest,
    ClientImportRequest,
    ClientImportResult,
    ClientListItem,
    ClientTrafficSnapshot,
    ClientUpdate,
    KeepaliveUpdate,
    TransportReissueResult,
)
from app.services.awg_client import ClientCreateError
from app.services.awg_transport import apply_keepalive
from app.services.xray_client import ClientCreateError as XrayClientCreateError
from app.services.xray_cascade import XrayCascadeError
from app.services.awg_enforce import enforce_server_by_id
from app.services.client_store import client_store
from app.services.protocol_engine import ClientSpec, get_engine
from app.services.server_store import server_store
from app.db.session import get_db
from app.services.audit_service import AuditService
from app.services.traffic_sync import sync_online_traffic


router = APIRouter()


@router.get("", response_model=list[ClientListItem])
async def list_clients(
    server_id: Optional[str] = Query(default=None),
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ClientListItem]:
    items = client_store.list_all(server_id=server_id)
    if not items:
        return items
    from app.services.invoice_service import last_paid_at_by_client_ids

    paid_map = await last_paid_at_by_client_ids(db, [c.id for c in items])
    if not paid_map:
        return items
    return [item.model_copy(update={"last_paid_at": paid_map.get(item.id)}) for item in items]


@router.post("/sync-traffic", response_model=list[ClientTrafficSnapshot])
async def sync_traffic(_: CurrentUser = Depends(require_client_manager)) -> list[ClientTrafficSnapshot]:
    """Лёгкое обновление трафика для онлайн-клиентов (без полных метрик сервера)."""
    return await asyncio.to_thread(sync_online_traffic)


@router.post("/export")
async def export_clients(
    payload: ClientExportRequest,
    request: Request,
    user: CurrentUser = Depends(require_client_manager),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """UX1: экспорт клиентов (конфиги/ссылки/QR) для бэкапа и массовой раздачи.

    Содержит чувствительные данные (приватные ключи AWG в конфиге) — только для
    менеджеров клиентов и с записью в audit.
    """
    from datetime import datetime, timezone

    items = client_store.list_all(server_id=payload.server_id)
    wanted = set(payload.ids) if payload.ids else None

    exported: list[dict] = []
    for item in items:
        if wanted is not None and item.id not in wanted:
            continue
        detail = client_store.get_detail(item.id)
        if not detail:
            continue
        entry = {
            "id": detail.id,
            "name": detail.name,
            "server_id": detail.server_id,
            "server_name": detail.server_name,
            "protocol": detail.protocol,
            "client_ip": detail.client_ip,
            "endpoint": detail.endpoint,
            "config_text": detail.config_text,
            "vpn_link": detail.vpn_link,
            "traffic_limit_bytes": detail.traffic_limit_bytes,
            "expires_at": detail.expires_at,
            "created_at": detail.created_at,
            "keepalive": detail.keepalive,
        }
        if payload.include_qr:
            entry["qr_awg"] = detail.qr_awg
            entry["qr_vpn"] = detail.qr_vpn
        exported.append(entry)

    await AuditService(db).log(
        "clients_exported",
        user_id=uuid.UUID(user.id),
        user_email=user.email,
        target_type="client",
        target_id=payload.server_id or "all",
        detail={"count": len(exported), "include_qr": payload.include_qr},
        ip=client_ip(request),
    )
    return {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(exported),
        "clients": exported,
    }


@router.post("/import", response_model=ClientImportResult)
async def import_clients(
    payload: ClientImportRequest,
    request: Request,
    user: CurrentUser = Depends(require_client_manager),
    db: AsyncSession = Depends(get_db),
) -> ClientImportResult:
    """UX1: импорт клиентов из JSON-бандла (парный к export).

    Пересоздаёт клиентов через движок (новые ключи) на исходном или указанном
    сервере. dry_run=true — только план и конфликты, без изменений.
    """
    from app.services.client_import import run_import

    try:
        result = await asyncio.to_thread(
            run_import,
            payload.bundle,
            target_server_id=payload.target_server_id,
            dry_run=payload.dry_run,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not payload.dry_run:
        await AuditService(db).log(
            "clients_imported",
            user_id=uuid.UUID(user.id),
            user_email=user.email,
            target_type="client",
            target_id=payload.target_server_id or "bundle",
            detail={
                "created": result.to_create,
                "skipped": result.to_skip,
                "errors": result.errors,
                "total": result.total,
            },
            ip=client_ip(request),
        )
    return result


@router.post("", response_model=ClientDetail)
async def create_client(
    payload: ClientCreate,
    request: Request,
    user: CurrentUser = Depends(require_client_manager),
    db: AsyncSession = Depends(get_db),
) -> ClientDetail:
    protocol = (payload.protocol or "awg2").lower()
    try:
        if protocol == "xray_cascade":
            # Каскадная выдача (chain): server_id — это entry (РФ) со своим Xray.
            # Клиент обычный (UUID/ключи на entry, конфиг на entry:443), а раздвоение
            # РФ/заграница делает серверный routing на entry. Прогоняем через тот же
            # эндпоинт, чтобы переиспользовать тариф/лимиты/аудит ниже.
            from app.services.xray_cascade import create_xray_cascade_client

            detail = await asyncio.to_thread(
                create_xray_cascade_client,
                payload.server_id,
                payload.name.strip(),
                format=payload.format,
                traffic_limit_bytes=payload.traffic_limit_bytes,
                expires_at=payload.expires_at,
            )
        else:
            spec = ClientSpec(
                server_id=payload.server_id,
                name=payload.name.strip(),
                protocol=protocol,
                format=payload.format,
                traffic_limit_bytes=payload.traffic_limit_bytes,
                expires_at=payload.expires_at,
                keepalive=payload.keepalive,
                link_host=(payload.link_host or "").strip() or None,
            )
            detail = await asyncio.to_thread(get_engine(protocol).create_client, spec)
    except (ClientCreateError, XrayClientCreateError, XrayCascadeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if payload.billing_mode == "paid" and not payload.billing_amount_kopecks:
        raise HTTPException(status_code=400, detail="Для платного тарифа укажите сумму.")
    billing_changes = {
        "billing_mode": payload.billing_mode,
        "billing_amount_kopecks": payload.billing_amount_kopecks
        if payload.billing_mode == "paid"
        else None,
        "billing_period_months": payload.billing_period_months,
    }
    refreshed = client_store.update_limits(detail.id, changes=billing_changes)
    if refreshed:
        detail = refreshed

    audit = AuditService(db)
    await audit.log(
        "client_created",
        user_id=uuid.UUID(user.id),
        user_email=user.email,
        target_type="client",
        target_id=detail.id,
        detail={"name": payload.name.strip(), "server_id": payload.server_id},
        ip=client_ip(request),
    )
    return detail


@router.get("/{client_id}", response_model=ClientDetail)
async def get_client(client_id: str, _: CurrentUser = Depends(get_current_user)) -> ClientDetail:
    detail = client_store.get_detail(client_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Клиент не найден.")
    return detail


@router.post("/{client_id}/keepalive", response_model=ClientDetail)
async def change_keepalive(
    client_id: str,
    payload: KeepaliveUpdate,
    request: Request,
    user: CurrentUser = Depends(require_client_manager),
    db: AsyncSession = Depends(get_db),
) -> ClientDetail:
    if not client_store.get_detail(client_id):
        raise HTTPException(status_code=404, detail="Клиент не найден.")
    result: TransportReissueResult = await asyncio.to_thread(
        apply_keepalive, client_id, payload.keepalive
    )
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.error or "Не удалось применить keepalive.")
    audit = AuditService(db)
    await audit.log(
        "keepalive_change",
        user_id=uuid.UUID(user.id),
        user_email=user.email,
        target_type="client",
        target_id=client_id,
        detail={"keepalive": payload.keepalive, "reissued": result.reissued},
        ip=client_ip(request),
    )
    refreshed = client_store.get_detail(client_id)
    if not refreshed:
        raise HTTPException(status_code=404, detail="Клиент не найден.")
    return refreshed


@router.patch("/{client_id}", response_model=ClientDetail)
async def update_client(
    client_id: str,
    payload: ClientUpdate,
    request: Request,
    user: CurrentUser = Depends(require_client_manager),
    db: AsyncSession = Depends(get_db),
) -> ClientDetail:
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("billing_mode") == "paid":
        existing = client_store.get_detail(client_id)
        amount = changes.get("billing_amount_kopecks")
        if not amount and not (existing and existing.billing_amount_kopecks):
            raise HTTPException(status_code=400, detail="Для платного тарифа укажите сумму.")
    detail = client_store.update_limits(client_id, changes=changes)
    if not detail:
        raise HTTPException(status_code=404, detail="Клиент не найден.")
    # сразу применяем блокировку/разблокировку на сервере (best-effort)
    try:
        await asyncio.to_thread(enforce_server_by_id, detail.server_id)
    except Exception:  # noqa: BLE001
        pass
    refreshed = client_store.get_detail(client_id)
    audit = AuditService(db)
    await audit.log(
        "client_updated",
        user_id=uuid.UUID(user.id),
        user_email=user.email,
        target_type="client",
        target_id=client_id,
        detail=changes,
        ip=client_ip(request),
    )
    return refreshed or detail


@router.delete("/{client_id}")
async def delete_client(
    client_id: str,
    request: Request,
    force: bool = Query(default=False),
    user: CurrentUser = Depends(require_client_manager),
    db: AsyncSession = Depends(get_db),
) -> dict:
    record = client_store.get_record_raw(client_id)
    if not record:
        raise HTTPException(status_code=404, detail="Клиент не найден.")

    server_id = record.get("server_id")
    protocol = (record.get("protocol") or "awg2").lower()
    public_key = record.get("public_key")

    server_cleaned = True
    cleanup_error: Optional[str] = None
    if public_key:
        try:
            server_cleaned = await asyncio.to_thread(
                get_engine(protocol).delete_client, server_id, public_key
            )
        except Exception as exc:  # noqa: BLE001
            server_cleaned = False
            cleanup_error = str(exc)

    if not server_cleaned and not force:
        raise HTTPException(
            status_code=502,
            detail=(
                f"Не удалось удалить клиента с сервера: {cleanup_error}. "
                "Повторите позже или удалите принудительно (force=true) — "
                "тогда клиент исчезнет из панели, но peer/UUID на сервере останется."
            ),
        )

    client_store.delete(client_id)
    if server_id:
        server_store.update_runtime(
            server_id, active_peers=client_store.count_for_server(server_id)
        )

    audit = AuditService(db)
    await audit.log(
        "client_deleted",
        user_id=uuid.UUID(user.id),
        user_email=user.email,
        target_type="client",
        target_id=client_id,
        detail={"server_cleaned": server_cleaned, "forced": force and not server_cleaned},
        ip=client_ip(request),
    )
    return {"status": "ok", "server_cleaned": server_cleaned, "forced": force and not server_cleaned}
