from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import decrypt, encrypt
from app.models.panel_settings import PanelSetting
from app.services.yookassa import mask_secret_key

DEFAULT_SETTINGS: dict[str, str] = {
    "app_name": settings.app_name,
    "default_dns": settings.default_dns,
    "default_subnet": settings.default_subnet,
    "default_udp_port_min": str(settings.default_udp_port_min),
    "default_udp_port_max": str(settings.default_udp_port_max),
    "access_token_minutes": str(settings.access_token_minutes),
    "refresh_token_days": str(settings.refresh_token_days),
    "maintenance_mode": "false",
    "github_token_encrypted": "",
    "yookassa_shop_id": "",
    "yookassa_secret_key_encrypted": "",
    # Chat Mini-App (CH1+): включается только после привязки домена и проверок изоляции
    "chat_domain": "",
    "chat_enabled": "false",
    "chat_ssl_status": "not_configured",
    "chat_public_url": "",
    "chat_moderator_access": "true",
    "chat_retention_days": "90",
    # Web Push (CH5): VAPID-ключи генерируются один раз при первом включении.
    "chat_vapid_public": "",
    "chat_vapid_private_enc": "",
}

SENSITIVE_KEYS = {"github_token_encrypted", "yookassa_secret_key_encrypted"}


class PanelSettingsService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_all(self, include_secrets: bool = False) -> dict[str, Any]:
        rows = (await self.session.execute(select(PanelSetting))).scalars().all()
        data: dict[str, Any] = dict(DEFAULT_SETTINGS)
        for row in rows:
            if row.value is not None:
                data[row.key] = row.value
        result = {
            "app_name": data.get("app_name", settings.app_name),
            "default_dns": data.get("default_dns", settings.default_dns),
            "default_subnet": data.get("default_subnet", settings.default_subnet),
            "default_udp_port_min": int(data.get("default_udp_port_min", settings.default_udp_port_min)),
            "default_udp_port_max": int(data.get("default_udp_port_max", settings.default_udp_port_max)),
            "access_token_minutes": int(data.get("access_token_minutes", settings.access_token_minutes)),
            "refresh_token_days": int(data.get("refresh_token_days", settings.refresh_token_days)),
            "maintenance_mode": data.get("maintenance_mode", "false") == "true",
            "has_github_token": bool(data.get("github_token_encrypted")),
        }
        if include_secrets:
            token = decrypt(data.get("github_token_encrypted") or None)
            result["github_token"] = token or ""
        return result

    async def get(self, key: str) -> Optional[str]:
        row = await self.session.get(PanelSetting, key)
        if row and row.value is not None:
            return row.value
        return DEFAULT_SETTINGS.get(key)

    async def set(self, key: str, value: Optional[str]) -> None:
        row = await self.session.get(PanelSetting, key)
        if row is None:
            row = PanelSetting(key=key, value=value)
            self.session.add(row)
        else:
            row.value = value
        await self.session.commit()

    async def set_many(self, values: dict[str, Any]) -> dict[str, Any]:
        for key, value in values.items():
            if key == "github_token":
                enc = encrypt(str(value)) if value else ""
                await self._set_local("github_token_encrypted", enc)
                continue
            if key in DEFAULT_SETTINGS or key == "maintenance_mode":
                await self._set_local(key, str(value).lower() if isinstance(value, bool) else str(value))
        await self.session.commit()
        return await self.get_all()

    async def _set_local(self, key: str, value: Optional[str]) -> None:
        row = await self.session.get(PanelSetting, key)
        if row is None:
            self.session.add(PanelSetting(key=key, value=value))
        else:
            row.value = value

    async def get_github_token(self) -> Optional[str]:
        enc = await self.get("github_token_encrypted")
        return decrypt(enc or None)

    async def is_maintenance(self) -> bool:
        val = await self.get("maintenance_mode")
        return val == "true"

    async def get_yookassa_status(self) -> dict[str, Any]:
        shop_id = (await self.get("yookassa_shop_id") or "").strip()
        secret = await self.get_yookassa_secret_key()
        connected = bool(shop_id and secret)
        return {
            "connected": connected,
            "shop_id": shop_id if connected else None,
            "secret_key_masked": mask_secret_key(secret) if secret else None,
        }

    async def get_yookassa_secret_key(self) -> Optional[str]:
        enc = await self.get("yookassa_secret_key_encrypted")
        return decrypt(enc or None)

    async def connect_yookassa(self, shop_id: str, secret_key: str) -> dict[str, Any]:
        shop_id = shop_id.strip()
        secret_key = secret_key.strip()
        await self._set_local("yookassa_shop_id", shop_id)
        await self._set_local("yookassa_secret_key_encrypted", encrypt(secret_key) or "")
        await self.session.commit()
        return await self.get_yookassa_status()

    async def disconnect_yookassa(self) -> dict[str, Any]:
        await self._set_local("yookassa_shop_id", "")
        await self._set_local("yookassa_secret_key_encrypted", "")
        await self.session.commit()
        return await self.get_yookassa_status()
