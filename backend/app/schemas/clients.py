from typing import Optional

from pydantic import BaseModel, Field


class ClientCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    server_id: str
    protocol: str = "awg2"
    format: str = "both"  # awg2: both|awg|vpn — xray: both|config|vpn
    traffic_limit_bytes: Optional[int] = Field(default=None, ge=0)
    expires_at: Optional[str] = None
    keepalive: int = Field(default=25, ge=0, le=120)
    # Тариф (CH9): основа для самооплаты клиентом в чате
    billing_mode: str = Field(default="free", pattern="^(free|paid)$")
    billing_amount_kopecks: Optional[int] = Field(default=None, ge=100, le=100_000_00)
    billing_period_months: int = Field(default=1, ge=1, le=12)


class KeepaliveUpdate(BaseModel):
    keepalive: int = Field(ge=0, le=120)


class EndpointUpdate(BaseModel):
    endpoint_host: Optional[str] = Field(default=None, max_length=255)


class TransportReissueResult(BaseModel):
    ok: bool
    reissued: int = 0
    skipped: int = 0
    error: Optional[str] = None


class ClientUpdate(BaseModel):
    traffic_limit_bytes: Optional[int] = Field(default=None, ge=0)
    expires_at: Optional[str] = None
    status: Optional[str] = None
    billing_mode: Optional[str] = Field(default=None, pattern="^(free|paid)$")
    billing_amount_kopecks: Optional[int] = Field(default=None, ge=100, le=100_000_00)
    billing_period_months: Optional[int] = Field(default=None, ge=1, le=12)


class ClientTrafficSnapshot(BaseModel):
    id: str
    traffic_used_bytes: int = 0
    traffic_up_bytes: int = 0
    traffic_down_bytes: int = 0
    last_handshake_at: Optional[str] = None
    online: bool = False
    status: str = "active"
    blocked: bool = False


class ClientListItem(BaseModel):
    id: str
    name: str
    server_id: str
    server_name: Optional[str] = None
    channel_id: Optional[str] = None
    protocol: str = "awg2"
    status: str
    client_ip: str
    imported: bool
    public_key: Optional[str] = None
    traffic_used_bytes: int = 0
    traffic_up_bytes: int = 0
    traffic_down_bytes: int = 0
    traffic_limit_bytes: Optional[int] = None
    expires_at: Optional[str] = None
    last_handshake_at: Optional[str] = None
    online: bool = False
    blocked: bool = False
    created_at: Optional[str] = None
    keepalive: int = 25
    billing_mode: str = "free"
    billing_amount_kopecks: Optional[int] = None
    billing_period_months: int = 1


class ClientDetail(ClientListItem):
    config_text: Optional[str] = None
    vpn_link: Optional[str] = None
    qr_awg: Optional[str] = None
    qr_vpn: Optional[str] = None
    endpoint: Optional[str] = None
    has_private_key: bool = False
