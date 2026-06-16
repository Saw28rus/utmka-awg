import base64
import posixpath
import shlex
from datetime import datetime, timezone

from typing import Optional

from app.schemas.clients import ClientDetail
from app.services.amnezia_link import build_vpn_link
from app.services.awg_config import (
    CLIENTS_TABLE_PATHS,
    append_client_to_table,
    build_client_config,
    build_peer_block,
    next_client_ip,
    parse_client_names,
    parse_interface,
    parse_peers,
    remove_client_from_table,
    resolve_endpoint_host,
)
from app.services.awg_detect import _container_names, _locate_config
from app.services.client_store import client_store
from app.services.server_store import server_store
from app.ssh import exec as ssh_exec


class ClientCreateError(Exception):
    pass


def create_awg_client(
    server_id: str,
    name: str,
    protocol: str = "awg2",
    *,
    format: str = "both",
    traffic_limit_bytes: Optional[int] = None,
    expires_at: Optional[str] = None,
    keepalive: int = 25,
) -> ClientDetail:
    record = server_store.get_record(server_id)
    target = server_store.ssh_target(server_id)
    if not record or not target:
        raise ClientCreateError("Сервер не найден.")

    try:
        ssh = ssh_exec.connect(
            host=target.host,
            port=target.port,
            username=target.username,
            password=target.password,
            key=target.key,
        )
    except Exception as exc:  # noqa: BLE001
        raise ClientCreateError(f"SSH не отвечает: {exc}") from exc

    try:
        containers = record.get("container_names") or _container_names(ssh)
        config_path, container, config_text = _locate_config(ssh, containers)
        if not container or not config_text.strip():
            raise ClientCreateError(
                "Не найден конфиг AWG2 в контейнере. Создание клиента доступно только для импортированного сервера."
            )

        iface = posixpath.splitext(posixpath.basename(config_path))[0]
        interface = parse_interface(config_text)
        existing_peers = parse_peers(config_text)

        used_ips = [peer.client_ip for peer in existing_peers]
        used_ips.extend(client_store.used_ips_for_server(server_id))
        client_ip = next_client_ip(interface.address, used_ips)

        server_public_key = _server_public_key(ssh, container, iface, interface.private_key)
        if not server_public_key:
            raise ClientCreateError("Не удалось получить публичный ключ сервера.")

        endpoint_port = interface.listen_port or record.get("vpn_port") or 51820
        endpoint_host = resolve_endpoint_host(record, target.host)

        private_key, public_key, preshared_key = _generate_keys(ssh, container)

        _backup_config(ssh, container, config_path)
        peer_block = build_peer_block(public_key, preshared_key, client_ip)
        _append_to_file(ssh, container, config_path, peer_block)
        _update_clients_table(ssh, container, config_path, public_key, name)
        sync_ok = _sync_config(ssh, container, iface, config_path)

        config_text_out = build_client_config(
            client_private_key=private_key,
            client_ip=client_ip,
            dns=interface.dns,
            server_public_key=server_public_key,
            preshared_key=preshared_key,
            endpoint_host=endpoint_host,
            endpoint_port=endpoint_port,
            awg_params=interface.awg_params,
            keepalive=keepalive,
        )

        want_config = format in {"both", "awg", "config"}
        want_vpn = format in {"both", "vpn"}
        vpn_link = (
            build_vpn_link(
                host=endpoint_host,
                port=endpoint_port,
                dns=interface.dns,
                client_ip=client_ip,
                client_private_key=private_key,
                client_public_key=public_key,
                server_public_key=server_public_key,
                preshared_key=preshared_key,
                awg_params=interface.awg_params,
                wg_config_ini=config_text_out,
                description=record["name"],
                keepalive=keepalive,
            )
            if want_vpn
            else None
        )

        detail = client_store.add_client(
            server_id=server_id,
            server_name=record["name"],
            name=name,
            protocol=protocol,
            client_ip=client_ip,
            public_key=public_key,
            private_key=private_key,
            preshared_key=preshared_key,
            config_text=config_text_out if want_config else None,
            vpn_link=vpn_link,
            endpoint=f"{endpoint_host}:{endpoint_port}",
            imported=False,
            traffic_limit_bytes=traffic_limit_bytes,
            expires_at=expires_at,
            keepalive=keepalive,
        )

        new_count = client_store.count_for_server(server_id)
        server_store.update_runtime(server_id, active_peers=new_count, vpn_port=endpoint_port)

        if not sync_ok:
            server_store.update_runtime(
                server_id,
                last_detect_message="Клиент добавлен в конфиг. Не удалось перезагрузить интерфейс — нужен restart контейнера.",
            )

        return detail
    finally:
        ssh.close()


def delete_awg_client(server_id: str, public_key: str) -> bool:
    """Удаляет peer клиента из конфига AWG на сервере и применяет syncconf.

    Идемпотентно: если peer уже отсутствует — успех. Чистит и [Peer]-блок, и
    запись в clientsTable. Перед изменением делает бэкап конфига.
    """
    if not public_key:
        return True

    record = server_store.get_record(server_id)
    target = server_store.ssh_target(server_id)
    if not record or not target:
        raise ClientCreateError("Сервер не найден.")

    try:
        ssh = ssh_exec.connect(
            host=target.host,
            port=target.port,
            username=target.username,
            password=target.password,
            key=target.key,
        )
    except Exception as exc:  # noqa: BLE001
        raise ClientCreateError(f"SSH не отвечает: {exc}") from exc

    try:
        # _parse_blocks/_rebuild живут в awg_enforce, который импортирует этот модуль —
        # импортируем лениво, чтобы избежать кругового импорта.
        from app.services.awg_enforce import _parse_blocks, _rebuild

        containers = record.get("container_names") or _container_names(ssh)
        config_path, container, config_text = _locate_config(ssh, containers)
        if not container or not config_text.strip():
            raise ClientCreateError("Не найден конфиг AWG в контейнере.")

        iface = posixpath.splitext(posixpath.basename(config_path))[0]
        head, blocks_by_pub, order = _parse_blocks(config_text)

        if public_key in blocks_by_pub:
            del blocks_by_pub[public_key]
            order.remove(public_key)
            new_config = _rebuild(head, blocks_by_pub, order)
            _backup_config(ssh, container, config_path)
            _overwrite_file(ssh, container, config_path, new_config)
            _sync_config(ssh, container, iface, config_path)

        _remove_from_clients_table(ssh, container, config_path, public_key)
        return True
    finally:
        ssh.close()


def _run_in_container(ssh, container: str, inner: str, timeout: int = 20):
    cmd = f"docker exec {shlex.quote(container)} sh -c {shlex.quote(inner)}"
    return ssh_exec.run(ssh, cmd, timeout=timeout)


def _server_public_key(ssh, container: str, iface: str, private_key) -> str:
    out = _run_in_container(
        ssh,
        container,
        f"awg show {shlex.quote(iface)} public-key 2>/dev/null || wg show {shlex.quote(iface)} public-key 2>/dev/null || true",
    ).stdout.strip()
    if out:
        return out.splitlines()[0].strip()

    if private_key:
        derived = _run_in_container(
            ssh,
            container,
            f"printf '%s' {shlex.quote(private_key)} | awg pubkey 2>/dev/null || printf '%s' {shlex.quote(private_key)} | wg pubkey 2>/dev/null || true",
        ).stdout.strip()
        if derived:
            return derived.splitlines()[0].strip()
    return ""


def _generate_keys(ssh, container: str) -> tuple[str, str, str]:
    inner = (
        "priv=$(awg genkey 2>/dev/null || wg genkey); "
        'pub=$(printf "%s" "$priv" | (awg pubkey 2>/dev/null || wg pubkey)); '
        "psk=$(awg genpsk 2>/dev/null || wg genpsk); "
        'printf "%s\\n%s\\n%s\\n" "$priv" "$pub" "$psk"'
    )
    out = _run_in_container(ssh, container, inner).stdout.strip().splitlines()
    out = [line.strip() for line in out if line.strip()]
    if len(out) < 2:
        raise ClientCreateError("Не удалось сгенерировать ключи на сервере (нет awg/wg tools).")
    private_key = out[0]
    public_key = out[1]
    preshared_key = out[2] if len(out) > 2 else ""
    return private_key, public_key, preshared_key


def _backup_config(ssh, container: str, config_path: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    backup = f"{config_path}.utmka.bak.{ts}"
    _run_in_container(ssh, container, f"cp {shlex.quote(config_path)} {shlex.quote(backup)} 2>/dev/null || true")


def _append_to_file(ssh, container: str, path: str, content: str) -> None:
    b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
    inner = f"printf '%s' {shlex.quote(b64)} | base64 -d >> {shlex.quote(path)}"
    result = _run_in_container(ssh, container, inner)
    if result.exit_code != 0:
        raise ClientCreateError(f"Не удалось записать peer в конфиг: {result.stderr.strip()}")


def _overwrite_file(ssh, container: str, path: str, content: str) -> None:
    b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
    inner = f"printf '%s' {shlex.quote(b64)} | base64 -d > {shlex.quote(path)}"
    _run_in_container(ssh, container, inner)


def _update_clients_table(ssh, container: str, config_path: str, public_key: str, name: str) -> None:
    table_path = _find_clients_table(ssh, container, config_path)
    existing = _run_in_container(ssh, container, f"cat {shlex.quote(table_path)} 2>/dev/null || true").stdout
    updated = append_client_to_table(existing, public_key, name)
    _overwrite_file(ssh, container, table_path, updated)


def _remove_from_clients_table(ssh, container: str, config_path: str, public_key: str) -> None:
    """Убирает запись клиента из clientsTable. Пишет файл только если он реально изменился."""
    table_path = _find_clients_table(ssh, container, config_path)
    existing = _run_in_container(ssh, container, f"cat {shlex.quote(table_path)} 2>/dev/null || true").stdout
    if not existing.strip():
        return
    updated = remove_client_from_table(existing, public_key)
    if updated.strip() != existing.strip():
        _overwrite_file(ssh, container, table_path, updated)


def _find_clients_table(ssh, container: str, config_path: str) -> str:
    for path in CLIENTS_TABLE_PATHS:
        out = _run_in_container(ssh, container, f"[ -f {shlex.quote(path)} ] && echo found || true").stdout
        if "found" in out:
            return path
    return posixpath.join(posixpath.dirname(config_path), "clientsTable")


def _sync_config(ssh, container: str, iface: str, config_path: str) -> bool:
    """Применяет конфиг к живому интерфейсу через `awg syncconf`.

    Важно: `awg-quick strip <iface>` ищет конфиг в дефолтном пути
    /etc/amnezia/amneziawg/<iface>.conf, тогда как Amnezia хранит его в
    /opt/amnezia/awg/. Поэтому strip делаем по полному пути config_path,
    а stderr подавляем (там предупреждение про world-accessible, ломающее парсинг).
    """
    tmp = f"/tmp/{iface}.utmka.strip"
    cfg = shlex.quote(config_path)
    iq = shlex.quote(iface)
    tq = shlex.quote(tmp)
    inner = (
        f"(awg-quick strip {cfg} 2>/dev/null || wg-quick strip {cfg} 2>/dev/null) > {tq} && "
        f"(awg syncconf {iq} {tq} 2>/dev/null || wg syncconf {iq} {tq} 2>/dev/null); "
        f"code=$?; rm -f {tq}; exit $code"
    )
    return _run_in_container(ssh, container, inner).exit_code == 0


# совместимость для импорта существующих клиентов
def import_client_names(ssh, container: str, config_path: str) -> dict:
    table_path = _find_clients_table(ssh, container, config_path)
    text = _run_in_container(ssh, container, f"cat {shlex.quote(table_path)} 2>/dev/null || true").stdout
    return parse_client_names(text)
