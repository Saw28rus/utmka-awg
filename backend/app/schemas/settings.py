from typing import Any, Optional

from pydantic import BaseModel, Field


class PanelSettingsRead(BaseModel):
    app_name: str
    default_dns: str
    default_subnet: str
    default_udp_port_min: int
    default_udp_port_max: int
    access_token_minutes: int
    refresh_token_days: int
    maintenance_mode: bool
    has_github_token: bool
    panel_version: str
    update_capable: bool


class PanelSettingsUpdate(BaseModel):
    app_name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    default_dns: Optional[str] = None
    default_subnet: Optional[str] = None
    default_udp_port_min: Optional[int] = Field(default=None, ge=1, le=65535)
    default_udp_port_max: Optional[int] = Field(default=None, ge=1, le=65535)
    access_token_minutes: Optional[int] = Field(default=None, ge=5, le=1440)
    refresh_token_days: Optional[int] = Field(default=None, ge=1, le=90)
    github_token: Optional[str] = None


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str = Field(min_length=12, max_length=128)


class ThemeUpdateRequest(BaseModel):
    theme: str = Field(pattern="^(dark|light)$")


class UpdateCheckResponse(BaseModel):
    current: str
    latest: Optional[str] = None
    available: Optional[bool] = None
    message: str
    capable: bool
    changelog_url: Optional[str] = None


class PanelJobRead(BaseModel):
    id: str
    type: str
    status: str
    progress: int
    message: Optional[str] = None
    log: Optional[str] = None
    rollback_ref: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class YooKassaStatusRead(BaseModel):
    connected: bool
    shop_id: Optional[str] = None
    secret_key_masked: Optional[str] = None


class YooKassaConnectRequest(BaseModel):
    shop_id: str = Field(min_length=1, max_length=64)
    secret_key: str = Field(min_length=1, max_length=256)


class AuditEventRead(BaseModel):
    id: str
    user_email: Optional[str] = None
    action: str
    target_type: Optional[str] = None
    target_id: Optional[str] = None
    detail: Optional[dict[str, Any]] = None
    ip: Optional[str] = None
    created_at: str
