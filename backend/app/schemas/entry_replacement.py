from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class ReplacePreflightRequest(BaseModel):
    new_host: str = Field(min_length=3, max_length=255)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    ssh_username: str = Field(default="root", min_length=1, max_length=64)
    ssh_password: Optional[str] = Field(default=None, max_length=4096)
    ssh_key: Optional[str] = Field(default=None, max_length=32768)


class ReplacePreflightResult(BaseModel):
    ok: bool
    blockers: list[str] = []
    warnings: list[str] = []
    replacement: Optional[dict[str, Any]] = None


class ReplacementStatus(BaseModel):
    # Свободная форма — отдаём публичную запись процесса как есть.
    model_config = {"extra": "allow"}

    status: Optional[str] = None
    can_activate: bool = False
