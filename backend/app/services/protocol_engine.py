"""Единый контракт протокольного движка (PA1, шаг 3a).

Цель — спрятать ветвление «if protocol == ...» за общий интерфейс. Движок НЕ
содержит новой логики: он делегирует в существующие сервисы (awg_*/xray_*),
сохраняя поведение 1-в-1. Это фундамент для мульти-протокольных каскадов и
управляемых обновлений (см. _dev-docs/MULTI_PROTOCOL_RESILIENCE_PLAN.md §3.1).

Шаг 3a: реализован движок AmneziaWG (awg2/awg_legacy). Xray-движок — шаг 3b,
перевод всех роутов на движок — шаг 3c.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.schemas.clients import ClientDetail


class EngineNotSupported(Exception):
    """Операция не поддерживается данным движком (UI прячет такие кнопки)."""


@dataclass(frozen=True)
class EngineCapabilities:
    """Что движок реально умеет. UI/роуты читают это, не угадывая по protocol."""

    install: bool = False
    create_client: bool = False
    delete_client: bool = False
    enforce: bool = False
    masking: bool = False
    cascade: bool = False
    update: bool = False


@dataclass
class ClientSpec:
    """Запрос на создание клиента (протокол-агностичный)."""

    server_id: str
    name: str
    protocol: str = "awg2"
    format: str = "both"
    traffic_limit_bytes: Optional[int] = None
    expires_at: Optional[str] = None
    keepalive: int = 25


class ProtocolEngine:
    """Базовый контракт. Неподдержанные методы кидают EngineNotSupported."""

    id: str = ""

    def capabilities(self) -> EngineCapabilities:
        raise NotImplementedError

    def install(self, server_id: str, *, port: Optional[int] = None):
        raise EngineNotSupported(f"{self.id}: установка не поддерживается движком.")

    def create_client(self, spec: ClientSpec) -> ClientDetail:
        raise EngineNotSupported(f"{self.id}: создание клиента не поддерживается движком.")

    def delete_client(self, server_id: str, public_key: str) -> bool:
        raise EngineNotSupported(f"{self.id}: удаление клиента не поддерживается движком.")

    def enforce(self, server_id: str) -> int:
        raise EngineNotSupported(f"{self.id}: enforce не поддерживается движком.")


class AwgEngine(ProtocolEngine):
    """AmneziaWG (awg2/awg_legacy). Делегирует в awg_* без смены поведения."""

    SUPPORTED = ("awg2", "awg_legacy")

    def __init__(self, protocol_id: str = "awg2") -> None:
        pid = (protocol_id or "awg2").lower()
        self.id = pid if pid in self.SUPPORTED else "awg2"

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            install=True,
            create_client=True,
            delete_client=True,
            enforce=True,
            masking=(self.id == "awg2"),
            cascade=True,
            update=False,
        )

    def install(self, server_id: str, *, port: Optional[int] = None):
        from app.services.awg_install import DEFAULT_PORT, install_awg

        return install_awg(server_id, variant=self.id, port=port or DEFAULT_PORT)

    def create_client(self, spec: ClientSpec) -> ClientDetail:
        from app.services.awg_client import create_awg_client

        # Пробрасываем исходный protocol как есть — поведение 1-в-1 со старой веткой.
        return create_awg_client(
            spec.server_id,
            spec.name,
            spec.protocol or self.id,
            format=spec.format,
            traffic_limit_bytes=spec.traffic_limit_bytes,
            expires_at=spec.expires_at,
            keepalive=spec.keepalive,
        )

    def delete_client(self, server_id: str, public_key: str) -> bool:
        from app.services.awg_client import delete_awg_client

        return delete_awg_client(server_id, public_key)

    def enforce(self, server_id: str) -> int:
        from app.services.awg_enforce import enforce_server_by_id

        return enforce_server_by_id(server_id)


# Реестр движков. Xray-движок появится на шаге 3b; пока xray обрабатывается
# отдельной веткой в роутах, поэтому get_engine для него не вызывается.
def get_engine(protocol_id: str) -> ProtocolEngine:
    """Движок по id протокола. Любой не-xray = AmneziaWG (как старая ветка)."""
    pid = (protocol_id or "awg2").lower()
    if pid == "xray":
        raise EngineNotSupported("Xray-движок ещё не подключён (шаг 3b).")
    return AwgEngine("awg_legacy" if pid == "awg_legacy" else "awg2")
