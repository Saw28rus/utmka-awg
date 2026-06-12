import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import client_ip, require_admin
from app.db.session import get_db
from app.schemas.auth import CurrentUser
from app.schemas.users import ResetPasswordRequest, UserCreate, UserRead, UserUpdate
from app.services.audit_service import AuditService
from app.services.user_service import UserService


router = APIRouter()


def _user_read(user) -> UserRead:
    return UserRead(
        id=str(user.id),
        email=user.email,
        name=user.name,
        role=user.role,
        is_active=user.is_active,
        theme=user.theme,
        last_login_at=user.last_login_at.isoformat() if user.last_login_at else None,
        created_at=user.created_at.isoformat() if user.created_at else None,
    )


@router.get("", response_model=list[UserRead])
async def list_users(
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[UserRead]:
    users = UserService(db)
    return [_user_read(u) for u in await users.list_users()]


@router.post("", response_model=UserRead)
async def create_user(
    payload: UserCreate,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> UserRead:
    users = UserService(db)
    try:
        user = await users.create_user(payload.email, payload.password, payload.name, payload.role)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit = AuditService(db)
    await audit.log(
        "user_created",
        user_id=uuid.UUID(admin.id),
        user_email=admin.email,
        target_type="user",
        target_id=str(user.id),
        detail={"email": user.email, "role": user.role},
        ip=client_ip(request),
    )
    return _user_read(user)


@router.patch("/{user_id}", response_model=UserRead)
async def update_user(
    user_id: str,
    payload: UserUpdate,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> UserRead:
    users = UserService(db)
    try:
        user = await users.update_user(
            uuid.UUID(user_id),
            name=payload.name,
            role=payload.role,
            is_active=payload.is_active,
            theme=payload.theme,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit = AuditService(db)
    await audit.log(
        "user_updated",
        user_id=uuid.UUID(admin.id),
        user_email=admin.email,
        target_type="user",
        target_id=user_id,
        detail=payload.model_dump(exclude_unset=True),
        ip=client_ip(request),
    )
    return _user_read(user)


@router.delete("/{user_id}")
async def delete_user(
    user_id: str,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    users = UserService(db)
    try:
        await users.delete_user(uuid.UUID(user_id), uuid.UUID(admin.id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit = AuditService(db)
    await audit.log(
        "user_deleted",
        user_id=uuid.UUID(admin.id),
        user_email=admin.email,
        target_type="user",
        target_id=user_id,
        ip=client_ip(request),
    )
    return {"status": "ok"}


@router.post("/{user_id}/reset-password", response_model=UserRead)
async def reset_password(
    user_id: str,
    payload: ResetPasswordRequest,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> UserRead:
    users = UserService(db)
    try:
        user = await users.reset_password(uuid.UUID(user_id), payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit = AuditService(db)
    await audit.log(
        "user_password_reset",
        user_id=uuid.UUID(admin.id),
        user_email=admin.email,
        target_type="user",
        target_id=user_id,
        ip=client_ip(request),
    )
    return _user_read(user)
