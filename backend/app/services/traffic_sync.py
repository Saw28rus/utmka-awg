"""Лёгкая синхронизация трафика клиентов — AWG dump и Xray Stats API."""

from concurrent.futures import ThreadPoolExecutor

from app.schemas.clients import ClientTrafficSnapshot
from app.services.awg_enforce import enforce_server
from app.services.client_store import client_store
from app.services.metrics import _resolve_awg
from app.services.server_store import server_store
from app.services.xray_stats import fetch_xray_stats
from app.ssh import exec as ssh_exec


def sync_online_traffic() -> list[ClientTrafficSnapshot]:
    """Обновляет трафик/онлайн-статус клиентов по всем серверам с клиентами.

    ВАЖНО: серверы нельзя фильтровать по «есть online-клиенты» — после рестарта
    интерфейса (ротация маскировки, перезапуск контейнера) все клиенты разом
    становятся offline, и такой сервер никогда больше не синкался бы (deadlock).
    """
    clients = client_store.list_all()
    server_ids = {c.server_id for c in clients}
    if not server_ids:
        return []

    if len(server_ids) == 1:
        _sync_server(next(iter(server_ids)))
    else:
        with ThreadPoolExecutor(max_workers=min(8, len(server_ids))) as pool:
            list(pool.map(_sync_server, server_ids))

    updated = {c.id: c for c in client_store.list_all()}
    snapshots: list[ClientTrafficSnapshot] = []
    for client in clients:
        fresh = updated.get(client.id)
        if not fresh:
            continue
        snapshots.append(
            ClientTrafficSnapshot(
                id=fresh.id,
                traffic_used_bytes=fresh.traffic_used_bytes,
                traffic_up_bytes=fresh.traffic_up_bytes,
                traffic_down_bytes=fresh.traffic_down_bytes,
                last_handshake_at=fresh.last_handshake_at,
                online=fresh.online,
                status=fresh.status,
                blocked=fresh.blocked,
            )
        )
    return snapshots


def _sync_server(server_id: str) -> None:
    record = server_store.get_record(server_id)
    target = server_store.ssh_target(server_id)
    if not record or not target:
        return
    try:
        ssh = ssh_exec.connect(
            host=target.host,
            port=target.port,
            username=target.username,
            password=target.password,
            key=target.key,
        )
    except Exception:  # noqa: BLE001
        return
    try:
        _container, stats = _resolve_awg(ssh, record)
        if stats:
            client_store.update_traffic(server_id, stats)
        if server_store.has_xray(record):
            xray_stats = fetch_xray_stats(ssh, server_id)
            if xray_stats:
                client_store.update_traffic(server_id, xray_stats)
        try:
            enforce_server(ssh, record, server_id)
        except Exception:  # noqa: BLE001
            pass
    finally:
        ssh.close()
