import uuid
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditEvent


class AuditService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def log(
        self,
        action: str,
        *,
        user_id: Optional[uuid.UUID] = None,
        user_email: Optional[str] = None,
        target_type: Optional[str] = None,
        target_id: Optional[str] = None,
        detail: Optional[dict[str, Any]] = None,
        ip: Optional[str] = None,
    ) -> AuditEvent:
        event = AuditEvent(
            user_id=user_id,
            user_email=user_email,
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail=detail,
            ip=ip,
        )
        self.session.add(event)
        await self.session.commit()
        await self.session.refresh(event)
        return event

    async def list_events(self, *, limit: int = 50, offset: int = 0) -> tuple[list[AuditEvent], int]:
        total = await self.session.scalar(select(func.count()).select_from(AuditEvent)) or 0
        result = await self.session.execute(
            select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(limit).offset(offset)
        )
        return list(result.scalars().all()), int(total)
