import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import client_ip, get_current_user, require_admin
from app.db.session import get_db
from app.schemas.auth import ChangePasswordRequest, CurrentUser
from app.schemas.settings import (
    PanelJobRead,
    PanelSettingsRead,
    PanelSettingsUpdate,
    UpdateCheckResponse,
    YooKassaConnectRequest,
    YooKassaStatusRead,
)
from app.services.audit_service import AuditService
from app.services.panel_backup import create_backup_zip, restore_backup_zip
from app.services.panel_settings_service import PanelSettingsService
from app.services.panel_update import (
    PANEL_VERSION_FILE,
    PanelUpdateService,
    check_for_updates,
    panel_update_capable,
    read_update_state,
)
from app.services.user_service import UserService
from app.services.yookassa import verify_credentials


router = APIRouter()


@router.get("/public")
async def public_settings(db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    svc = PanelSettingsService(db)
    app_name = await svc.get("app_name")
    from app.core.config import settings as cfg

    return {"app_name": app_name or cfg.app_name}


@router.get("/integrations-status")
async def integrations_status(
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, bool]:
    """Лёгкий статус интеграций для всех авторизованных (без секретов) — нужен для сайдбара."""
    svc = PanelSettingsService(db)
    status = await svc.get_yookassa_status()
    return {
        "yookassa_connected": bool(status.get("connected")),
        "chat_enabled": (await svc.get("chat_enabled")) == "true",
    }


def _job_read(job) -> PanelJobRead:
    message = None
    if job.status == "running":
        state = read_update_state()
        raw = state.get("message")
        if isinstance(raw, str) and raw.strip():
            message = raw.strip()
    return PanelJobRead(
        id=str(job.id),
        type=job.type,
        status=job.status,
        progress=job.progress,
        message=message,
        log=job.log,
        rollback_ref=job.rollback_ref,
        started_at=job.started_at.isoformat() if job.started_at else None,
        finished_at=job.finished_at.isoformat() if job.finished_at else None,
    )


@router.get("", response_model=PanelSettingsRead)
async def get_settings(
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> PanelSettingsRead:
    svc = PanelSettingsService(db)
    data = await svc.get_all()
    version = PANEL_VERSION_FILE.read_text(encoding="utf-8").strip() if PANEL_VERSION_FILE.exists() else "0.1.0"
    return PanelSettingsRead(panel_version=version, update_capable=panel_update_capable(), **data)


@router.patch("", response_model=PanelSettingsRead)
async def update_settings(
    payload: PanelSettingsUpdate,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> PanelSettingsRead:
    svc = PanelSettingsService(db)
    await svc.set_many(payload.model_dump(exclude_unset=True))
    audit = AuditService(db)
    await audit.log(
        "settings_updated",
        user_id=uuid.UUID(admin.id),
        user_email=admin.email,
        detail=payload.model_dump(exclude_unset=True, exclude={"github_token"}),
        ip=client_ip(request),
    )
    data = await svc.get_all()
    version = PANEL_VERSION_FILE.read_text(encoding="utf-8").strip() if PANEL_VERSION_FILE.exists() else "0.1.0"
    return PanelSettingsRead(panel_version=version, update_capable=panel_update_capable(), **data)


@router.post("/change-password")
async def change_password_settings(
    payload: ChangePasswordRequest,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    users = UserService(db)
    db_user = await users.get_by_id(uuid.UUID(admin.id))
    if not db_user:
        raise HTTPException(status_code=404, detail="Пользователь не найден.")
    try:
        await users.change_password(db_user, payload.old_password, payload.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit = AuditService(db)
    await audit.log(
        "password_changed",
        user_id=db_user.id,
        user_email=db_user.email,
        ip=client_ip(request),
    )
    return {"status": "ok"}


@router.get("/backup")
async def download_backup(
    request: Request,
    include_secrets: bool = False,
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    payload = await create_backup_zip(db, include_secrets=include_secrets)
    audit = AuditService(db)
    await audit.log(
        "backup_downloaded",
        user_id=uuid.UUID(admin.id),
        user_email=admin.email,
        detail={"include_secrets": include_secrets},
        ip=client_ip(request),
    )
    return Response(
        content=payload,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="utmka-panel-backup.zip"'},
    )


@router.post("/restore")
async def restore_backup(
    request: Request,
    file: UploadFile = File(...),
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    updates = PanelUpdateService(db)
    if await updates.get_running_job():
        raise HTTPException(status_code=409, detail="Нельзя восстанавливать во время обновления.")

    svc = PanelSettingsService(db)
    await svc.set("maintenance_mode", "true")
    try:
        content = await file.read()
        snapshot = await restore_backup_zip(db, content)
    except ValueError as exc:
        await svc.set("maintenance_mode", "false")
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        await svc.set("maintenance_mode", "false")
        raise HTTPException(status_code=500, detail=f"Ошибка восстановления: {exc}") from exc

    await svc.set("maintenance_mode", "false")
    audit = AuditService(db)
    await audit.log(
        "backup_restored",
        user_id=uuid.UUID(admin.id),
        user_email=admin.email,
        detail={"snapshot": snapshot},
        ip=client_ip(request),
    )
    return {"status": "ok", "snapshot": snapshot}


@router.get("/updates/check", response_model=UpdateCheckResponse)
async def updates_check(
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> UpdateCheckResponse:
    result = await check_for_updates(db)
    return UpdateCheckResponse(**result)


@router.post("/updates/apply", response_model=PanelJobRead)
async def updates_apply(
    request: Request,
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> PanelJobRead:
    svc = PanelSettingsService(db)
    if await svc.is_maintenance():
        raise HTTPException(status_code=409, detail="Панель в режиме обслуживания.")
    updates = PanelUpdateService(db)
    try:
        job = await updates.start_update()
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit = AuditService(db)
    await audit.log(
        "panel_update_started",
        user_id=uuid.UUID(admin.id),
        user_email=admin.email,
        target_type="job",
        target_id=str(job.id),
        ip=client_ip(request),
    )
    return _job_read(job)


@router.get("/updates/status/{job_id}", response_model=PanelJobRead)
async def updates_status(
    job_id: str,
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> PanelJobRead:
    updates = PanelUpdateService(db)
    job = await updates.get_job(uuid.UUID(job_id))
    if not job:
        raise HTTPException(status_code=404, detail="Задача не найдена.")
    return _job_read(job)


@router.get("/updates/latest", response_model=Optional[PanelJobRead])
async def updates_latest(
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Optional[PanelJobRead]:
    updates = PanelUpdateService(db)
    job = await updates.get_running_job()
    if not job:
        return None
    return _job_read(job)


@router.post("/updates/cancel", response_model=Optional[PanelJobRead])
async def updates_cancel(
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Optional[PanelJobRead]:
    updates = PanelUpdateService(db)
    job = await updates.cancel_running_job()
    if not job:
        return None
    return _job_read(job)


@router.get("/yookassa", response_model=YooKassaStatusRead)
async def yookassa_status(
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> YooKassaStatusRead:
    svc = PanelSettingsService(db)
    data = await svc.get_yookassa_status()
    return YooKassaStatusRead(**data)


@router.post("/yookassa/connect", response_model=YooKassaStatusRead)
async def yookassa_connect(
    payload: YooKassaConnectRequest,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> YooKassaStatusRead:
    result = await verify_credentials(payload.shop_id, payload.secret_key)
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.error or "Не удалось проверить ключи ЮKassa.")

    svc = PanelSettingsService(db)
    data = await svc.connect_yookassa(payload.shop_id, payload.secret_key)
    audit = AuditService(db)
    await audit.log(
        "yookassa_connected",
        user_id=uuid.UUID(admin.id),
        user_email=admin.email,
        detail={"shop_id": payload.shop_id.strip()},
        ip=client_ip(request),
    )
    return YooKassaStatusRead(**data)


@router.delete("/yookassa/disconnect", response_model=YooKassaStatusRead)
async def yookassa_disconnect(
    request: Request,
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> YooKassaStatusRead:
    svc = PanelSettingsService(db)
    prev = await svc.get_yookassa_status()
    data = await svc.disconnect_yookassa()
    audit = AuditService(db)
    await audit.log(
        "yookassa_disconnected",
        user_id=uuid.UUID(admin.id),
        user_email=admin.email,
        detail={"shop_id": prev.get("shop_id")},
        ip=client_ip(request),
    )
    return YooKassaStatusRead(**data)
