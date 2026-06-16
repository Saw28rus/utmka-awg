from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator


class ReplaceCandidate(BaseModel):
    id: str
    name: str
    host: str
    ssh_port: int
    has_awg2: bool = False


class ReplacePreflightRequest(BaseModel):
    """Либо выбрать сервер из панели (source_server_id), либо ввести SSH вручную."""

    source_server_id: Optional[str] = Field(default=None, min_length=8, max_length=64)
    new_host: Optional[str] = Field(default=None, min_length=3, max_length=255)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    ssh_username: str = Field(default="root", min_length=1, max_length=64)
    ssh_password: Optional[str] = Field(default=None, max_length=4096)
    ssh_key: Optional[str] = Field(default=None, max_length=32768)

    @model_validator(mode="after")
    def _mode(self) -> "ReplacePreflightRequest":
        if self.source_server_id:
            return self
        if not (self.new_host or "").strip():
            raise ValueError("Укажите сервер из панели или введите IP нового VPS.")
        if bool(self.ssh_password) == bool(self.ssh_key):
            raise ValueError("Укажите ровно один способ авторизации: пароль ИЛИ ключ.")
        return self


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
