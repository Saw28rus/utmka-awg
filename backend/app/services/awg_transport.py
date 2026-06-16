"""AWG Masking Center — фаза M3 (безопасная часть): keepalive и endpoint-домен.

Обе операции — только перевыпуск клиентских конфигов/ссылок (config + vpn-link + QR
в панели). Сервер НЕ перезапускается, ключи клиентов не меняются. Поэтому риск
минимальный: старые подключения продолжают работать, новый конфиг просто
актуальнее (другой keepalive или хостнейм в Endpoint).

Смену UDP-порта (пересоздание контейнера) сюда НЕ включаем — это отдельная
рискованная операция, требующая snapshot/recreate/handshake/rollback.
"""

from __future__ import annotations

from typing import Optional

from app.schemas.clients import TransportReissueResult
from app.services.amnezia_link import build_vpn_link
from app.services.awg_config import build_client_config, parse_interface, resolve_endpoint_host
from app.services.awg_masking import _find_awg_container, _read_container_config
from app.services.awg_masking_apply import _connect, _server_public_key
from app.services.client_store import client_store
from app.services.server_store import server_store


class TransportError(Exception):
    pass


def _endpoint_host_for(record: dict, ssh_host: str) -> str:
    return resolve_endpoint_host(record, ssh_host)


def _reissue(server_id: str, *, only_client_id: Optional[str] = None) -> TransportReissueResult:
    """Перевыпуск конфигов клиентов сервера (всех или одного). Блокирующая (SSH)."""
    record = server_store.get_record(server_id)
    if not record:
        return TransportReissueResult(ok=False, error="Сервер не найден.")
    try:
        ssh, target = _connect(server_id)
    except Exception as exc:  # noqa: BLE001
        return TransportReissueResult(ok=False, error=str(exc))

    try:
        container = _find_awg_container(ssh)
        if not container:
            return TransportReissueResult(ok=False, error="Контейнер AmneziaWG не найден.")
        config_path, config_text = _read_container_config(ssh, container)
        if not config_path or not config_text.strip():
            return TransportReissueResult(ok=False, error="Конфиг AmneziaWG не найден.")
        iface = config_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        info = parse_interface(config_text)
        listen_port = info.listen_port or record.get("vpn_port") or 51820
        endpoint_host = _endpoint_host_for(record, target.host)
        server_public_key = _server_public_key(ssh, container, iface, info.private_key)
        if not server_public_key:
            return TransportReissueResult(ok=False, error="Не удалось получить публичный ключ сервера.")
        server_name = record.get("name") or "Server"

        reissued = skipped = 0
        for item in client_store.list_all(server_id):
            if item.protocol not in ("awg2", "awg", "awg_legacy"):
                continue
            if only_client_id and item.id != only_client_id:
                continue
            bundle = client_store.get_secrets(item.id)
            if not bundle or not bundle.get("private_key"):
                skipped += 1
                continue
            keepalive = client_store.get_keepalive(item.id)
            config_text_out = build_client_config(
                client_private_key=bundle["private_key"],
                client_ip=item.client_ip,
                dns=info.dns,
                server_public_key=server_public_key,
                preshared_key=bundle.get("preshared_key"),
                endpoint_host=endpoint_host,
                endpoint_port=listen_port,
                awg_params=dict(info.awg_params),
                keepalive=keepalive,
            )
            vpn_link = build_vpn_link(
                host=endpoint_host,
                port=listen_port,
                dns=info.dns,
                client_ip=item.client_ip,
                client_private_key=bundle["private_key"],
                client_public_key=item.public_key or "",
                server_public_key=server_public_key,
                preshared_key=bundle.get("preshared_key"),
                awg_params=dict(info.awg_params),
                wg_config_ini=config_text_out,
                description=server_name,
                keepalive=keepalive,
            )
            client_store.update_issued_config(
                item.id,
                config_text=config_text_out,
                vpn_link=vpn_link,
                endpoint=f"{endpoint_host}:{listen_port}",
            )
            reissued += 1
        return TransportReissueResult(ok=True, reissued=reissued, skipped=skipped)
    finally:
        ssh.close()


def apply_keepalive(client_id: str, keepalive: int) -> TransportReissueResult:
    """Меняет PersistentKeepalive у одного клиента и перевыпускает его конфиг."""
    detail = client_store.get_detail(client_id)
    if not detail:
        return TransportReissueResult(ok=False, error="Клиент не найден.")
    client_store.set_keepalive(client_id, keepalive)
    bundle = client_store.get_secrets(client_id)
    if not bundle or not bundle.get("private_key"):
        # Импортированный клиент без ключей: значение сохранили, но конфиг не перевыпустить.
        return TransportReissueResult(ok=True, reissued=0, skipped=1)
    return _reissue(detail.server_id, only_client_id=client_id)


def apply_endpoint(server_id: str, endpoint_host: Optional[str]) -> TransportReissueResult:
    """Задаёт/снимает endpoint-домен сервера и перевыпускает конфиги всех клиентов."""
    record = server_store.get_record(server_id)
    if not record:
        return TransportReissueResult(ok=False, error="Сервер не найден.")
    host = (endpoint_host or "").strip()
    server_store.update_runtime(server_id, endpoint_host=host or None)
    return _reissue(server_id)
