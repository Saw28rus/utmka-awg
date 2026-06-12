from fastapi import APIRouter, Depends

from app.core.deps import require_admin
from app.schemas.auth import CurrentUser
from app.schemas.operations import OperationJob, OperationJobEvent


router = APIRouter()


@router.get("/{job_id}", response_model=OperationJob)
async def get_operation(
    job_id: str,
    _: CurrentUser = Depends(require_admin),
) -> OperationJob:
    return OperationJob(
        id=job_id,
        type="detect",
        status="queued",
        message="Операция еще не запускалась.",
    )


@router.get("/{job_id}/events", response_model=list[OperationJobEvent])
async def get_operation_events(
    job_id: str,
    _: CurrentUser = Depends(require_admin),
) -> list[OperationJobEvent]:
    return [
        OperationJobEvent(
            job_id=job_id,
            level="info",
            code="phase0_placeholder",
            message="Progress log будет подключен в Фазе 1.",
        )
    ]
