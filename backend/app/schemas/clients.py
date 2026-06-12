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


class ClientDetail(ClientListItem):
    config_text: Optional[str] = None
    vpn_link: Optional[str] = None
    qr_awg: Optional[str] = None
    qr_vpn: Optional[str] = None
    endpoint: Optional[str] = None
    has_private_key: bool = False
