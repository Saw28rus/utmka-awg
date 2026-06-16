from typing import Optional

from pydantic import BaseModel, Field


class ServerCreate(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    host: str = Field(min_length=3, max_length=255)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    ssh_username: str = Field(default="root", min_length=1, max_length=80)
    ssh_password: Optional[str] = None
    ssh_key: Optional[str] = None
    notes: Optional[str] = None
    detect_branch: Optional[str] = None
    awg2_detected: bool = False
    config_path: Optional[str] = None
    active_peers: int = 0
    container_names: list[str] = []


class ServerRead(BaseModel):
    id: str
    name: str
    host: str
    ssh_port: int
    ssh_username: str
    status: str
    awg2_imported: bool
    notes: Optional[str] = None
    detect_branch: str = "needs_review"
    awg2_detected: bool = False
    config_path: Optional[str] = None
    active_peers: int = 0
    protocols: list[str] = []
    client_protocols: list[str] = []
    vpn_port: Optional[int] = None
    endpoint_host: Optional[str] = None
    last_detect_message: Optional[str] = None
    created_at: Optional[str] = None
    former_entry: bool = False


class ServerMetrics(BaseModel):
    server_id: str
    status: str
    online: bool
    cpu_percent: Optional[float] = None
    mem_used_bytes: Optional[int] = None
    mem_total_bytes: Optional[int] = None
    disk_used_bytes: Optional[int] = None
    disk_total_bytes: Optional[int] = None
    uptime_seconds: Optional[int] = None
    awg2_container: Optional[str] = None
    awg2_running: bool = False
    active_peers: int = 0
    total_traffic_bytes: int = 0
    protocols: list[str] = []
    message: Optional[str] = None


class ServerListItem(ServerRead):
    cpu_percent: Optional[float] = None
    mem_used_mb: Optional[int] = None
    mem_total_mb: Optional[int] = None


class ServerMinimal(BaseModel):
    id: str
    name: str
    host: str
    status: str
    protocols: list[str] = []
    awg2_imported: bool = False
    client_protocols: list[str] = []


class DetectCheck(BaseModel):
    key: str
    label: str
    status: str
    message: Optional[str] = None


class DetectResult(BaseModel):
    server_id: Optional[str] = None
    confidence: str
    branch: str
    checks: list[DetectCheck]
    message: str
    awg2_detected: bool = False
    config_path: Optional[str] = None
    peers_count: int = 0
    container_names: list[str] = []
    docker_available: bool = False
    os_release: Optional[str] = None


class DetectPreviewRequest(ServerCreate):
    pass


class SystemInfo(BaseModel):
    os: Optional[str] = None
    kernel: Optional[str] = None
    arch: Optional[str] = None
    cpu_model: Optional[str] = None
    cores: Optional[int] = None
    docker_version: Optional[str] = None
    public_ip: Optional[str] = None


class ContainerInfo(BaseModel):
    name: str
    image: str = ""
    state: str = ""
    status: str = ""
    ports: str = ""
    cpu_percent: Optional[float] = None
    mem_usage: Optional[str] = None


class ProtocolInfo(BaseModel):
    id: str
    name: str
    description: str
    installed: bool = False
    running: bool = False
    container: Optional[str] = None
    ports: str = ""
    managed: bool = False
    can_install: bool = False
    clients_count: int = 0


class SecurityCheck(BaseModel):
    id: str
    label: str
    status: str = "unknown"  # ok | warning | danger | unknown
    value: str = ""
    recommendation: Optional[str] = None
    actionable: bool = False  # можно ли включать/выключать из панели
    control: Optional[str] = None  # имя контрола для /security/action (ufw|fail2ban|updates)
    enabled: Optional[bool] = None  # текущее состояние (для тумблера)


class ServerOverview(BaseModel):
    server_id: str
    online: bool = False
    message: Optional[str] = None
    system: SystemInfo = SystemInfo()
    containers: list[ContainerInfo] = []
    protocols: list[ProtocolInfo] = []
    security: list[SecurityCheck] = []


class ContainerActionRequest(BaseModel):
    action: str  # start | stop | restart


class ProtocolActionRequest(BaseModel):
    action: str  # start | stop | restart | remove


class ProtocolInstallRequest(BaseModel):
    port: int = Field(default=443, ge=1, le=65535)
    site_name: Optional[str] = Field(default=None, max_length=255)
    transport: Optional[str] = Field(default=None, max_length=16)


class ProtocolInstallResult(BaseModel):
    status: str = "ok"
    message: str
    container: Optional[str] = None
    port: Optional[int] = None
    site_name: Optional[str] = None
    client_uuid: Optional[str] = None
    public_key: Optional[str] = None
    short_id: Optional[str] = None
    secret: Optional[str] = None
    tg_link: Optional[str] = None
    transport: Optional[str] = None


class PanelSslStatus(BaseModel):
    domain: Optional[str] = None
    url: Optional[str] = None
    status: str = "not_configured"
    panel_detected: bool = False
    xray_on_443: bool = False
    nginx_installed: bool = False
    cert_present: bool = False
    cert_expires_at: Optional[str] = None
    public_ip: Optional[str] = None
    fallback_url: Optional[str] = None
    message: Optional[str] = None


class PanelSslVerifyRequest(BaseModel):
    domain: str = Field(min_length=4, max_length=253)


class PanelSslVerifyResult(BaseModel):
    ok: bool
    domain: str
    resolved_ips: list[str] = []
    server_public_ip: Optional[str] = None
    panel_detected: bool = False
    xray_on_443: bool = False
    port_80_available: bool = False
    message: str


class PanelSslInstallRequest(BaseModel):
    domain: str = Field(min_length=4, max_length=253)
    email: Optional[str] = Field(default=None, max_length=255)


class PanelSslAutoInstallRequest(BaseModel):
    email: Optional[str] = Field(default=None, max_length=255)


class PanelSslInstallResult(BaseModel):
    ok: bool
    domain: str
    url: str
    fallback_url: str
    xray_passthrough: bool = False
    message: str


class PanelHardenStatus(BaseModel):
    enabled: bool = False
    allowed_ips: list[str] = []
    persistent: bool = False
    https_active: bool = False
    https_url: Optional[str] = None
    your_ip: Optional[str] = None
    message: Optional[str] = None


class PanelHardenApplyRequest(BaseModel):
    allowed_ips: list[str] = []
    force: bool = False


class PanelHardenResult(BaseModel):
    ok: bool
    enabled: bool
    allowed_ips: list[str] = []
    message: str


class SecurityActionRequest(BaseModel):
    control: str  # ufw | fail2ban | updates
    action: str  # enable | disable


class SecurityActionResult(BaseModel):
    ok: bool
    control: str
    enabled: bool
    message: str


class UfwPreviewResult(BaseModel):
    tcp_ports: list[int] = []
    udp_ports: list[int] = []
    ssh_port: int = 22


class ChatDomainStatus(BaseModel):
    domain: Optional[str] = None
    enabled: bool = False
    ssl_status: str = "not_configured"
    public_url: Optional[str] = None
    cert_expires_at: Optional[str] = None
    panel_https_active: bool = False
    harden_active: bool = False
    server_public_ip: Optional[str] = None
    dns_record_hint: Optional[str] = None
    message: Optional[str] = None


class ChatDomainVerifyRequest(BaseModel):
    domain: str = Field(min_length=4, max_length=253)


class ChatDomainVerifyResult(BaseModel):
    ok: bool
    domain: str
    resolved_ips: list[str] = []
    server_public_ip: Optional[str] = None
    message: str


class ChatIsolationCheck(BaseModel):
    label: str
    expected: str
    actual: str
    ok: bool


class ChatDomainInstallResult(BaseModel):
    ok: bool
    domain: str
    public_url: str
    isolation: list[ChatIsolationCheck] = []
    message: str
