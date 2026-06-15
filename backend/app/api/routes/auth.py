from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import client_ip, get_current_user
from app.core.security import create_access_token, create_refresh_token, decode_token
from app.db.session import get_db
from app.schemas.auth import ChangePasswordRequest, CurrentUser, LoginRequest, RefreshRequest, TokenPair
from app.schemas.settings import ThemeUpdateRequest
from app.services.audit_service import AuditService
from app.services.panel_settings_service import PanelSettingsService
from app.services.user_service import UserService, user_to_current


router = APIRouter()


async def _session_ttl(db: AsyncSession) -> tuple[int, int]:
    settings = await PanelSettingsService(db).get_all()
    return settings["access_token_minutes"], settings["refresh_token_days"]


@router.post("/login", response_model=TokenPair)
async def login(
    payload: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TokenPair:
    users = UserService(db)
    user = await users.authenticate(payload.email, payload.password)
    audit = AuditService(db)
    if not user:
        await audit.log(
            "login_failed",
            user_email=str(payload.email).lower(),
            ip=client_ip(request),
            detail={"reason": "invalid_credentials"},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный email или пароль.",
        )

    await audit.log(
        "login_success",
        user_id=user.id,
        user_email=user.email,
        ip=client_ip(request),
    )
    access_min, refresh_days = await _session_ttl(db)
    access_token = create_access_token(
        user.email, {"role": user.role}, expires_minutes=access_min
    )
    refresh_token = create_refresh_token(
        user.email, {"role": user.role}, expires_days=refresh_days
    )
    return TokenPair(access_token=access_token, refresh_token=refresh_token)


@router.post("/refresh", response_model=TokenPair)
async def refresh(
    payload: RefreshRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenPair:
    try:
        token_payload = decode_token(payload.refresh_token)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Сессия истекла, войдите заново.",
        ) from exc

    if token_payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Сессия истекла, войдите заново.",
        )

    email = token_payload.get("sub")
    if not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Сессия истекла, войдите заново.",
        )

    users = UserService(db)
    db_user = await users.get_by_email(email)
    if not db_user or not db_user.is_active:
        raise HTTPException(status_code=401, detail="Учётная запись недоступна.")

    access_min, refresh_days = await _session_ttl(db)
    access_token = create_access_token(
        db_user.email, {"role": db_user.role}, expires_minutes=access_min
    )
    refresh_token = create_refresh_token(
        db_user.email, {"role": db_user.role}, expires_days=refresh_days
    )
    return TokenPair(access_token=access_token, refresh_token=refresh_token)


@router.post("/logout")
async def logout(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    audit = AuditService(db)
    await audit.log("logout", user_id=user.id, user_email=user.email, ip=client_ip(request))
    return {"status": "ok"}


@router.get("/me", response_model=CurrentUser)
async def me(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    return user


@router.patch("/me/theme", response_model=CurrentUser)
async def update_theme(
    payload: ThemeUpdateRequest,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CurrentUser:
    import uuid

    users = UserService(db)
    updated = await users.update_user(uuid.UUID(user.id), theme=payload.theme)
    return user_to_current(updated)


@router.post("/change-password")
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    import uuid

    users = UserService(db)
    db_user = await users.get_by_id(uuid.UUID(user.id))
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
