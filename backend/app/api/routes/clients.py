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
    ClientListItem,
    ClientTrafficSnapshot,
    ClientUpdate,
    KeepaliveUpdate,
    TransportReissueResult,
)
from app.services.awg_client import ClientCreateError, create_awg_client
from app.services.awg_transport import apply_keepalive
from app.services.xray_client import ClientCreateError as XrayClientCreateError
from app.services.xray_client import create_xray_client
from app.services.awg_enforce import enforce_server_by_id
from app.services.client_store import client_store
from app.db.session import get_db
from app.services.audit_service import AuditService
from app.services.traffic_sync import sync_online_traffic


router = APIRouter()


@router.get("", response_model=list[ClientListItem])
async def list_clients(
    server_id: Optional[str] = Query(default=None),
    _: CurrentUser = Depends(get_current_user),
) -> list[ClientListItem]:
    return client_store.list_all(server_id=server_id)


@router.post("/sync-traffic", response_model=list[ClientTrafficSnapshot])
async def sync_traffic(_: CurrentUser = Depends(require_client_manager)) -> list[ClientTrafficSnapshot]:
    """Лёгкое обновление трафика для онлайн-клиентов (без полных метрик сервера)."""
    return await asyncio.to_thread(sync_online_traffic)


@router.post("", response_model=ClientDetail)
async def create_client(
    payload: ClientCreate,
    request: Request,
    user: CurrentUser = Depends(require_client_manager),
    db: AsyncSession = Depends(get_db),
) -> ClientDetail:
    protocol = (payload.protocol or "awg2").lower()
    try:
        if protocol == "xray":
            detail = await asyncio.to_thread(
                create_xray_client,
                payload.server_id,
                payload.name.strip(),
                format=payload.format,
                traffic_limit_bytes=payload.traffic_limit_bytes,
                expires_at=payload.expires_at,
            )
        else:
            detail = await asyncio.to_thread(
                create_awg_client,
                payload.server_id,
                payload.name.strip(),
                protocol,
                format=payload.format,
                traffic_limit_bytes=payload.traffic_limit_bytes,
                expires_at=payload.expires_at,
                keepalive=payload.keepalive,
            )
    except (ClientCreateError, XrayClientCreateError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

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
    user: CurrentUser = Depends(require_client_manager),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not client_store.delete(client_id):
        raise HTTPException(status_code=404, detail="Клиент не найден.")
    audit = AuditService(db)
    await audit.log(
        "client_deleted",
        user_id=uuid.UUID(user.id),
        user_email=user.email,
        target_type="client",
        target_id=client_id,
        ip=client_ip(request),
    )
    return {"status": "ok"}
