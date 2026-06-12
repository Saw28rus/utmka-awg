from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_password
from app.models.panel_settings import PanelSetting
from app.models.user import User
from app.services.panel_settings_service import DEFAULT_SETTINGS


async def bootstrap_database(session: AsyncSession) -> None:
    await _seed_settings(session)
    await _seed_admin(session)


async def _seed_settings(session: AsyncSession) -> None:
    for key, value in DEFAULT_SETTINGS.items():
        existing = await session.get(PanelSetting, key)
        if existing is None:
            session.add(PanelSetting(key=key, value=value))
    await session.commit()


async def _seed_admin(session: AsyncSession) -> None:
    email = settings.admin_email.lower()
    result = await session.execute(select(User).where(User.email == email))
    existing = result.scalar_one_or_none()

    if existing is None:
        count = await session.scalar(select(func.count()).select_from(User))
        if count and count > 0:
            return
        session.add(
            User(
                email=email,
                password_hash=hash_password(settings.admin_password),
                name="Администратор",
                role="admin",
                is_active=True,
                theme="dark",
            )
        )
        await session.commit()
        return

    # Существующий admin: пароль не трогаем (смена через UI или scripts/reset-admin.py).
