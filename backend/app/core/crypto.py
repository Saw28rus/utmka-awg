import base64
import hashlib
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


def _build_fernet() -> Fernet:
    digest = hashlib.sha256(settings.panel_secret_key.encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


_fernet = _build_fernet()


def encrypt(value: Optional[str]) -> Optional[str]:
    if value is None or value == "":
        return None
    return _fernet.encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt(token: Optional[str]) -> Optional[str]:
    if not token:
        return None
    try:
        return _fernet.decrypt(token.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None
