from typing import Optional

from pydantic import BaseModel, Field


# --- client ---------------------------------------------------------------


class ChatLoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=1, max_length=128)


class ChatProfile(BaseModel):
    username: str
    display_name: Optional[str] = None


class ChatTokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    profile: ChatProfile


class ChatRefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=10, max_length=256)


class ChatAttachmentInfo(BaseModel):
    id: str
    kind: str
    filename: str
    expires_at: str
    expired: bool = False


class ChatMessageRead(BaseModel):
    id: int
    sender: str  # admin | client | system
    body: str
    created_at: str
    attachment: Optional[ChatAttachmentInfo] = None


class ChatAttachmentView(BaseModel):
    filename: str
    expires_at: str
    has_conf: bool
    config_text: Optional[str] = None
    vpn_link: Optional[str] = None
    qr_data_url: Optional[str] = None  # legacy: QR первого доступного формата
    qr_awg_data_url: Optional[str] = None  # QR конфига для AmneziaWG/WireGuard
    qr_vpn_data_url: Optional[str] = None  # QR ссылки vpn:// для AmneziaVPN


class ChatMessagesPage(BaseModel):
    messages: list[ChatMessageRead]
    thread_status: str


class ChatSendRequest(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


class ChatClientConfig(BaseModel):
    push_enabled: bool = False
    vapid_public_key: Optional[str] = None


class ChatVpnInfo(BaseModel):
    linked: bool = False
    name: Optional[str] = None
    status: Optional[str] = None
    expires_at: Optional[str] = None
    days_left: Optional[int] = None
    traffic_used_bytes: int = 0
    traffic_limit_bytes: Optional[int] = None
    billing_mode: str = "free"
    billing_amount_kopecks: Optional[int] = None
    billing_period_months: int = 1
    yookassa_available: bool = False
    can_self_pay: bool = False
    self_pay_remaining: int = 0


class ChatSelfPaymentResponse(BaseModel):
    pay_url: str
    invoice_id: str
    expires_at: Optional[str] = None
    amount_kopecks: Optional[int] = None
    reused: bool = False


class ChatPushSubscribeRequest(BaseModel):
    endpoint: str = Field(min_length=10, max_length=2000)
    p256dh: str = Field(min_length=10, max_length=255)
    auth: str = Field(min_length=1, max_length=255)


class ChatPushUnsubscribeRequest(BaseModel):
    endpoint: str = Field(min_length=10, max_length=2000)


# --- admin ----------------------------------------------------------------


class ChatStatusRead(BaseModel):
    enabled: bool
    domain: Optional[str] = None
    public_url: Optional[str] = None
    moderator_access: bool = True
    users: int = 0
    threads: int = 0


class ChatUserRead(BaseModel):
    id: str
    username: str
    display_name: Optional[str] = None
    client_id: Optional[str] = None
    client_name: Optional[str] = None
    is_active: bool
    last_login_at: Optional[str] = None
    created_at: str


class ChatUserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    display_name: Optional[str] = Field(default=None, max_length=120)


class ChatResetPasswordRequest(BaseModel):
    # Пусто → сгенерируется случайный пароль; иначе — заданный админом.
    password: Optional[str] = Field(default=None, max_length=128)


class ChatUserWithPassword(BaseModel):
    user: ChatUserRead
    password: str  # показывается один раз


class ChatFolderRead(BaseModel):
    id: str
    name: str
    color: Optional[str] = None
    sort_order: int = 0
    count: int = 0


class ChatFolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    color: Optional[str] = Field(default=None, max_length=7)


class ChatFolderUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=64)
    color: Optional[str] = Field(default=None, max_length=7)
    sort_order: Optional[int] = None


class ChatMoveThreadRequest(BaseModel):
    folder_id: Optional[str] = None


class ChatThreadRead(BaseModel):
    id: str
    status: str
    folder_id: Optional[str] = None
    last_message_at: Optional[str] = None
    username: str
    display_name: Optional[str] = None
    user_is_active: bool = True
    client_id: Optional[str] = None
    client_name: Optional[str] = None
    client_missing: bool = False
    chat_user_id: Optional[str] = None
    last_preview: Optional[str] = None
    last_sender: Optional[str] = None


class ChatThreadStatusRequest(BaseModel):
    status: str = Field(pattern="^(open|resolved)$")


class ChatLinkRequest(BaseModel):
    client_id: Optional[str] = Field(default=None, max_length=64)


class ChatInvoiceItem(BaseModel):
    id: str
    description: Optional[str] = None
    amount_rub: str
    status: str
    pay_url: Optional[str] = None
    created_at: str
    expires_at: Optional[str] = None


class ChatInsertInvoiceRequest(BaseModel):
    invoice_id: str
