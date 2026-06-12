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


class ChatUserWithPassword(BaseModel):
    user: ChatUserRead
    password: str  # показывается один раз


class ChatThreadRead(BaseModel):
    id: str
    status: str
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
