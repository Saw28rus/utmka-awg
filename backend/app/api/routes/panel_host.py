"""HTTPS, harden :8080 и чат-домен на ХОСТЕ панели (не на VPN-узле)."""

from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import client_ip, require_admin
from app.db.session import get_db
from app.schemas.auth import CurrentUser
from app.schemas.servers import (
    ChatDomainInstallResult,
    ChatDomainStatus,
    ChatDomainVerifyRequest,
    ChatDomainVerifyResult,
    PanelHardenApplyRequest,
    PanelHardenResult,
    PanelHardenStatus,
    PanelSslAutoInstallRequest,
    PanelSslInstallRequest,
    PanelSslInstallResult,
    PanelSslStatus,
    PanelSslVerifyRequest,
    PanelSslVerifyResult,
)
from app.services.audit_service import AuditService
from app.services.chat_domain import (
    ChatDomainError,
    disable_chat_domain,
    get_chat_domain_state,
    install_chat_domain,
    install_chat_domain_auto,
    verify_chat_domain,
)
from app.services.panel_harden import (
    PanelHardenError,
    apply_harden,
    disable_harden,
    get_harden_state,
)
from app.services.panel_host import PANEL_HOST_ID, ensure_panel_host_record
from app.services.panel_settings_service import PanelSettingsService
from app.services.panel_ssl import (
    PanelSslError,
    get_panel_ssl_status,
    install_panel_ssl,
    install_panel_ssl_auto,
    rollback_panel_ssl,
    verify_panel_domain,
)

router = APIRouter()


def _ensure() -> None:
    ensure_panel_host_record()


@router.get("/ssl/status", response_model=PanelSslStatus)
async def panel_host_ssl_status(_: CurrentUser = Depends(require_admin)) -> PanelSslStatus:
    _ensure()
    try:
        status = await asyncio.to_thread(get_panel_ssl_status, PANEL_HOST_ID)
    except PanelSslError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return PanelSslStatus(**status.__dict__)


@router.post("/ssl/verify", response_model=PanelSslVerifyResult)
async def panel_host_ssl_verify(
    payload: PanelSslVerifyRequest,
    _: CurrentUser = Depends(require_admin),
) -> PanelSslVerifyResult:
    _ensure()
    try:
        result = await asyncio.to_thread(verify_panel_domain, PANEL_HOST_ID, payload.domain)
    except PanelSslError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return PanelSslVerifyResult(**result.__dict__)


@router.post("/ssl/install", response_model=PanelSslInstallResult)
async def panel_host_ssl_install(
    payload: PanelSslInstallRequest,
    _: CurrentUser = Depends(require_admin),
) -> PanelSslInstallResult:
    _ensure()
    try:
        result = await asyncio.to_thread(
            install_panel_ssl, PANEL_HOST_ID, payload.domain, email=payload.email
        )
    except PanelSslError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return PanelSslInstallResult(**result.__dict__)


@router.post("/ssl/install-auto", response_model=PanelSslInstallResult)
async def panel_host_ssl_install_auto(
    payload: PanelSslAutoInstallRequest,
    _: CurrentUser = Depends(require_admin),
) -> PanelSslInstallResult:
    _ensure()
    try:
        result = await asyncio.to_thread(install_panel_ssl_auto, PANEL_HOST_ID, email=payload.email)
    except PanelSslError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return PanelSslInstallResult(**result.__dict__)


@router.post("/ssl/rollback")
async def panel_host_ssl_rollback(_: CurrentUser = Depends(require_admin)) -> dict:
    _ensure()
    try:
        message = await asyncio.to_thread(rollback_panel_ssl, PANEL_HOST_ID)
    except PanelSslError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "ok", "message": message}


@router.get("/harden/status", response_model=PanelHardenStatus)
async def panel_host_harden_status(
    request: Request, _: CurrentUser = Depends(require_admin)
) -> PanelHardenStatus:
    _ensure()
    try:
        state = await asyncio.to_thread(get_harden_state, PANEL_HOST_ID)
    except PanelHardenError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return PanelHardenStatus(**state.__dict__, your_ip=client_ip(request))


@router.post("/harden/apply", response_model=PanelHardenResult)
async def panel_host_harden_apply(
    payload: PanelHardenApplyRequest,
    request: Request,
    _: CurrentUser = Depends(require_admin),
) -> PanelHardenResult:
    _ensure()
    try:
        result = await asyncio.to_thread(
            apply_harden,
            PANEL_HOST_ID,
            payload.allowed_ips,
            caller_ip=client_ip(request),
            force=payload.force,
        )
    except PanelHardenError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return PanelHardenResult(**result.__dict__)


@router.post("/harden/disable", response_model=PanelHardenResult)
async def panel_host_harden_disable(_: CurrentUser = Depends(require_admin)) -> PanelHardenResult:
    _ensure()
    try:
        result = await asyncio.to_thread(disable_harden, PANEL_HOST_ID)
    except PanelHardenError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return PanelHardenResult(**result.__dict__)


@router.get("/chat-domain/status", response_model=ChatDomainStatus)
async def panel_host_chat_status(_: CurrentUser = Depends(require_admin)) -> ChatDomainStatus:
    _ensure()
    try:
        state = await asyncio.to_thread(get_chat_domain_state, PANEL_HOST_ID)
    except ChatDomainError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return ChatDomainStatus(**state.__dict__)


@router.post("/chat-domain/verify", response_model=ChatDomainVerifyResult)
async def panel_host_chat_verify(
    payload: ChatDomainVerifyRequest,
    _: CurrentUser = Depends(require_admin),
) -> ChatDomainVerifyResult:
    _ensure()
    try:
        result = await asyncio.to_thread(verify_chat_domain, PANEL_HOST_ID, payload.domain)
    except ChatDomainError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return ChatDomainVerifyResult(**result.__dict__)


async def _enable_chat_settings(db: AsyncSession, result, admin: CurrentUser, request: Request) -> None:
    settings = PanelSettingsService(db)
    await settings.set_many(
        {
            "chat_domain": result.domain,
            "chat_enabled": "true",
            "chat_ssl_status": "cert_active",
            "chat_public_url": result.public_url,
        }
    )
    await AuditService(db).log(
        "chat_enabled",
        user_id=uuid.UUID(admin.id),
        user_email=admin.email,
        target_type="panel_host",
        target_id=PANEL_HOST_ID,
        detail={"domain": result.domain, "public_url": result.public_url},
        ip=client_ip(request),
    )


@router.post("/chat-domain/install", response_model=ChatDomainInstallResult)
async def panel_host_chat_install(
    payload: ChatDomainVerifyRequest,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ChatDomainInstallResult:
    _ensure()
    try:
        result = await asyncio.to_thread(install_chat_domain, PANEL_HOST_ID, payload.domain)
    except ChatDomainError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await _enable_chat_settings(db, result, admin, request)
    return ChatDomainInstallResult(**result.__dict__)


@router.post("/chat-domain/install-auto", response_model=ChatDomainInstallResult)
async def panel_host_chat_install_auto(
    request: Request,
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ChatDomainInstallResult:
    _ensure()
    try:
        result = await asyncio.to_thread(install_chat_domain_auto, PANEL_HOST_ID)
    except ChatDomainError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await _enable_chat_settings(db, result, admin, request)
    return ChatDomainInstallResult(**result.__dict__)


@router.post("/chat-domain/disable")
async def panel_host_chat_disable(
    request: Request,
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    _ensure()
    try:
        message = await asyncio.to_thread(disable_chat_domain, PANEL_HOST_ID)
    except ChatDomainError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    settings = PanelSettingsService(db)
    await settings.set_many({"chat_enabled": "false", "chat_ssl_status": "disabled"})
    await AuditService(db).log(
        "chat_disabled",
        user_id=uuid.UUID(admin.id),
        user_email=admin.email,
        target_type="panel_host",
        target_id=PANEL_HOST_ID,
        detail={},
        ip=client_ip(request),
    )
    return {"status": "ok", "message": message}
