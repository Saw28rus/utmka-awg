"""Схемы каскада AmneziaWG (Model A). MVP: read-only preflight + статус link."""

from typing import Optional

from pydantic import BaseModel, Field


class CascadeCheck(BaseModel):
    id: str
    label: str
    status: str = "unknown"  # ok | warning | danger | unknown
    value: str = ""
    detail: Optional[str] = None


class CascadePreflightRequest(BaseModel):
    exit_server_id: str = Field(min_length=1)


class CascadePreflightResult(BaseModel):
    ok: bool
    entry_server_id: str
    exit_server_id: str
    entry_name: Optional[str] = None
    exit_name: Optional[str] = None
    # Ключевой вопрос Model A: виден ли source клиента до NAT и где ставить hook
    client_subnet: Optional[str] = None
    source_visibility: str = "unknown"  # host | netns | nated | unknown
    recommended_hook: str = "unknown"   # host | netns | blocked
    amnezia_container: Optional[str] = None
    amnezia_netns_pid: Optional[int] = None
    exit_public_ip: Optional[str] = None
    exit_awg_tooling: str = "unknown"   # kernel | userspace | none | unknown
    transit_subnet: Optional[str] = None
    transit_port: Optional[int] = None
    checks: list[CascadeCheck] = []
    blockers: list[str] = []
    message: str = ""
    live_active: bool = False


class CascadeStep(BaseModel):
    name: str
    status: str  # ok | failed | skipped
    detail: Optional[str] = None


class CascadeApplyResult(BaseModel):
    ok: bool
    state: str  # active | rolled_back | rollback_failed | aborted
    entry_server_id: str
    exit_server_id: str
    egress_ip: Optional[str] = None
    expected_exit_ip: Optional[str] = None
    transit_subnet: Optional[str] = None
    transit_port: Optional[int] = None
    steps: list[CascadeStep] = []
    message: str = ""


class CascadeLinkSummary(BaseModel):
    """Краткая связь entry↔exit для списка серверов."""

    entry_server_id: str
    entry_name: str
    entry_host: str
    exit_server_id: str
    exit_name: str
    exit_host: str
    state: str = "none"
    is_active: bool = False
    live_active: bool = False
    egress_ip: Optional[str] = None
    transit_port: Optional[int] = None


class CascadeLinkStatus(BaseModel):
    entry_server_id: str
    exit_server_id: Optional[str] = None
    exit_name: Optional[str] = None
    state: str = "none"  # none | preflight_ok | preflight_failed | draft | active | down | rolled_back
    nat_model: str = "model_a"
    client_subnet: Optional[str] = None
    transit_subnet: Optional[str] = None
    transit_port: Optional[int] = None
    recommended_hook: Optional[str] = None
    last_preflight_at: Optional[str] = None
    last_preflight_ok: bool = False
    last_applied_at: Optional[str] = None
    egress_ip: Optional[str] = None
    message: Optional[str] = None
    split_enabled: bool = False
    split_applied: bool = False
    live_active: bool = False


# ---------------------------------------------------------------------------
# Split-routing (РФ напрямую, остальное через exit)
# ---------------------------------------------------------------------------


class SplitSourceInfo(BaseModel):
    id: str
    label: str
    description: str
    default_enabled: bool = True
    kind: str = "cidr"


class CascadeRulesStatus(BaseModel):
    entry_server_id: str
    cascade_active: bool = False
    enabled: bool = False
    applied: bool = False
    source_ids: list[str] = []
    custom_cidrs: list[str] = []
    direct_cidr_count: int = 0
    list_updated_at: Optional[str] = None
    sources: list[SplitSourceInfo] = []
    entry_name: Optional[str] = None
    exit_name: Optional[str] = None
    entry_public_ip: Optional[str] = None
    exit_public_ip: Optional[str] = None
    health: Optional[dict] = None
    last_error: Optional[str] = None
    message: Optional[str] = None


class CascadeRulesUpdate(BaseModel):
    enabled: bool
    source_ids: Optional[list[str]] = None
    custom_cidrs: Optional[list[str]] = None


class CascadeRulesApplyResult(BaseModel):
    ok: bool
    enabled: bool
    applied: bool
    direct_cidr_count: int = 0
    steps: list[CascadeStep] = []
    health: Optional[dict] = None
    invalid_cidrs: list[str] = []
    message: str = ""
