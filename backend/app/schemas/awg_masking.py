"""Схемы AWG Masking Center — фазы M1 (инспектор) и M2 (генератор/ротация)."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

# Версии протокола
MASK_VERSION_AWG2 = "awg2"
MASK_VERSION_AWG15 = "awg15"
MASK_VERSION_LEGACY = "legacy"
MASK_VERSION_UNKNOWN = "unknown"

# Статусы маскировки (posture, НЕ гарантия обхода ТСПУ)
MASK_STATUS_STRONG = "strong"
MASK_STATUS_BASIC = "basic"
MASK_STATUS_WEAK = "weak"
MASK_STATUS_LEGACY = "legacy"
MASK_STATUS_INVALID = "invalid"
MASK_STATUS_UNKNOWN = "unknown"


class MaskingWarning(BaseModel):
    level: str  # info | warning | danger
    code: str
    message: str


class MaskingState(BaseModel):
    version: str = MASK_VERSION_UNKNOWN
    container: Optional[str] = None
    interface: Optional[str] = None
    config_path: Optional[str] = None
    listen_port: Optional[int] = None
    endpoint: Optional[str] = None
    mtu: Optional[int] = None
    keepalive: Optional[int] = None
    jc: Optional[str] = None
    jmin: Optional[str] = None
    jmax: Optional[str] = None
    s1: Optional[str] = None
    s2: Optional[str] = None
    s3: Optional[str] = None
    s4: Optional[str] = None
    h1: Optional[str] = None
    h2: Optional[str] = None
    h3: Optional[str] = None
    h4: Optional[str] = None
    h_is_ranges: bool = False
    i_present: list[str] = []


class MaskingScore(BaseModel):
    status: str = MASK_STATUS_UNKNOWN
    label: str = "Неизвестно"


class RealityFallback(BaseModel):
    """M6: Reality (Xray) как запасной канал при полном UDP-бане.

    Не входит в masking score — это отдельный fallback-профиль, не замена AWG.
    """

    installed: bool = False
    running: Optional[bool] = None
    container: Optional[str] = None
    port: Optional[int] = None
    sni: Optional[str] = None
    clients_total: int = 0
    warnings: list[MaskingWarning] = []


class MaskingResponse(BaseModel):
    ok: bool
    server_id: str
    state: Optional[MaskingState] = None
    score: MaskingScore
    warnings: list[MaskingWarning] = []
    read_error: Optional[str] = None
    checked_at: Optional[str] = None
    last_rotation_at: Optional[str] = None
    rotation_age_days: Optional[int] = None
    fallback: Optional[RealityFallback] = None


# --- M2: генератор / ротация --------------------------------------------------


class MaskingPreset(BaseModel):
    id: str
    label: str
    description: str


class MaskingPreviewRequest(BaseModel):
    preset: str


class MaskingPreviewResponse(BaseModel):
    ok: bool
    preset: str
    params: dict[str, str] = {}
    current: dict[str, str] = {}
    errors: list[str] = []
    clients_total: int = 0
    clients_reissuable: int = 0
    clients_skipped: int = 0
    cascade_entry: bool = False
    error: Optional[str] = None


class MaskingApplyRequest(BaseModel):
    preset: str
    params: dict[str, str]


class MaskingStep(BaseModel):
    name: str
    status: str  # ok | failed | skipped | info
    detail: Optional[str] = None


class MaskingApplyResponse(BaseModel):
    ok: bool
    steps: list[MaskingStep] = []
    snapshot_id: Optional[str] = None
    rolled_back: bool = False
    reissued: int = 0
    reissue_skipped: int = 0
    error: Optional[str] = None
    masking: Optional[MaskingResponse] = None


class MaskingSnapshotInfo(BaseModel):
    id: str
    created_at: str
    label: str
    preset: Optional[str] = None


class MaskingRollbackRequest(BaseModel):
    snapshot_id: Optional[str] = None
