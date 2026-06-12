from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_admin
from app.db.session import get_db
from app.schemas.settings import AuditEventRead
from app.services.audit_service import AuditService


router = APIRouter()


@router.get("", response_model=dict)
async def list_audit(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _admin=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    svc = AuditService(db)
    events, total = await svc.list_events(limit=limit, offset=offset)
    return {
        "total": total,
        "items": [
            AuditEventRead(
                id=str(e.id),
                user_email=e.user_email,
                action=e.action,
                target_type=e.target_type,
                target_id=e.target_id,
                detail=e.detail,
                ip=e.ip,
                created_at=e.created_at.isoformat(),
            )
            for e in events
        ],
    }
