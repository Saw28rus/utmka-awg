from typing import Optional

from pydantic import BaseModel


class OperationJob(BaseModel):
    id: str
    type: str
    status: str
    message: Optional[str] = None


class OperationJobEvent(BaseModel):
    job_id: str
    level: str
    code: str
    message: str
