import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.models.user import User
from app.schemas.auth import CurrentUser


def user_to_current(user: User) -> CurrentUser:
    return CurrentUser(
        id=str(user.id),
        email=user.email,
        name=user.name,
        role=user.role,
        is_active=user.is_active,
        theme=user.theme,
    )


class UserService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_email(self, email: str) -> Optional[User]:
        result = await self.session.execute(select(User).where(User.email == email.lower()))
        return result.scalar_one_or_none()

    async def get_by_id(self, user_id: uuid.UUID) -> Optional[User]:
        return await self.session.get(User, user_id)

    async def list_users(self) -> list[User]:
        result = await self.session.execute(select(User).order_by(User.created_at))
        return list(result.scalars().all())

    async def authenticate(self, email: str, password: str) -> Optional[User]:
        user = await self.get_by_email(email)
        if not user or not user.is_active:
            return None
        if not verify_password(password, user.password_hash):
            return None
        user.last_login_at = datetime.now(timezone.utc)
        await self.session.commit()
        return user

    async def create_user(
        self,
        email: str,
        password: str,
        name: str,
        role: str,
    ) -> User:
        existing = await self.get_by_email(email)
        if existing:
            raise ValueError("Пользователь с таким email уже существует.")
        user = User(
            email=email.lower(),
            password_hash=hash_password(password),
            name=name,
            role=role,
            is_active=True,
        )
        self.session.add(user)
        await self.session.commit()
        await self.session.refresh(user)
        return user

    async def update_user(
        self,
        user_id: uuid.UUID,
        *,
        name: Optional[str] = None,
        role: Optional[str] = None,
        is_active: Optional[bool] = None,
        theme: Optional[str] = None,
    ) -> User:
        user = await self.get_by_id(user_id)
        if not user:
            raise ValueError("Пользователь не найден.")
        if name is not None:
            user.name = name
        if role is not None:
            if user.role == "admin" and role != "admin":
                await self._ensure_not_last_admin(user)
            user.role = role
        if is_active is not None:
            if not is_active and user.role == "admin":
                await self._ensure_not_last_admin(user)
            user.is_active = is_active
        if theme is not None:
            user.theme = theme
        await self.session.commit()
        await self.session.refresh(user)
        return user

    async def reset_password(self, user_id: uuid.UUID, new_password: str) -> User:
        user = await self.get_by_id(user_id)
        if not user:
            raise ValueError("Пользователь не найден.")
        user.password_hash = hash_password(new_password)
        await self.session.commit()
        await self.session.refresh(user)
        return user

    async def change_password(self, user: User, old_password: str, new_password: str) -> None:
        if not verify_password(old_password, user.password_hash):
            raise ValueError("Неверный текущий пароль.")
        user.password_hash = hash_password(new_password)
        await self.session.commit()

    async def delete_user(self, user_id: uuid.UUID, actor_id: uuid.UUID) -> None:
        if user_id == actor_id:
            raise ValueError("Нельзя удалить свою учётную запись.")
        user = await self.get_by_id(user_id)
        if not user:
            raise ValueError("Пользователь не найден.")
        if user.role == "admin":
            await self._ensure_not_last_admin(user)
        await self.session.delete(user)
        await self.session.commit()

    async def count_admins(self) -> int:
        result = await self.session.scalar(
            select(func.count()).select_from(User).where(User.role == "admin", User.is_active.is_(True))
        )
        return int(result or 0)

    async def _ensure_not_last_admin(self, user: User) -> None:
        if user.role != "admin":
            return
        admins = await self.count_admins()
        if admins <= 1:
            raise ValueError("Нельзя изменить или удалить последнего администратора.")
