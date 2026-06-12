"""JWT и rate-limit для клиентского чата (CH2).

Чат-токены подписываются ОТДЕЛЬНЫМ секретом (derived от panel_secret_key) и
несут aud=chat_client — panel-JWT и chat-JWT взаимно недействительны.
"""

import hashlib
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import jwt

from app.core.config import settings

CHAT_AUD = "chat_client"
CHAT_ACCESS_MINUTES = 30
CHAT_REFRESH_DAYS = 14

# Lockout логина: 5 неудач подряд -> 15 минут блокировки (in-memory, per-process)
LOGIN_MAX_FAILS = 5
LOGIN_LOCKOUT_SECONDS = 15 * 60
_login_fails: dict[str, tuple[int, float]] = {}


def _chat_secret() -> str:
    return hashlib.sha256(f"{settings.panel_secret_key}::chat_client".encode()).hexdigest()


def create_chat_access_token(chat_user_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": chat_user_id,
        "aud": CHAT_AUD,
        "scope": "chat",
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=CHAT_ACCESS_MINUTES)).timestamp()),
    }
    return jwt.encode(payload, _chat_secret(), algorithm=settings.jwt_algorithm)


def decode_chat_token(token: str) -> dict[str, Any]:
    return jwt.decode(
        token, _chat_secret(), algorithms=[settings.jwt_algorithm], audience=CHAT_AUD
    )


def new_refresh_token() -> tuple[str, str, datetime]:
    """Возвращает (token, sha256-hash, expires_at). В БД храним только hash."""
    token = secrets.token_urlsafe(48)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    expires = datetime.now(timezone.utc) + timedelta(days=CHAT_REFRESH_DAYS)
    return token, token_hash, expires


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def login_locked(key: str) -> Optional[int]:
    """Секунды до разблокировки или None."""
    entry = _login_fails.get(key)
    if not entry:
        return None
    fails, last = entry
    if fails < LOGIN_MAX_FAILS:
        return None
    remaining = int(LOGIN_LOCKOUT_SECONDS - (time.time() - last))
    if remaining <= 0:
        _login_fails.pop(key, None)
        return None
    return remaining


def register_login_fail(key: str) -> None:
    fails, _ = _login_fails.get(key, (0, 0.0))
    _login_fails[key] = (fails + 1, time.time())
    if len(_login_fails) > 10_000:
        cutoff = time.time() - LOGIN_LOCKOUT_SECONDS
        for k in [k for k, (_, ts) in _login_fails.items() if ts < cutoff]:
            _login_fails.pop(k, None)


def clear_login_fails(key: str) -> None:
    _login_fails.pop(key, None)
