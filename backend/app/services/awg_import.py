from __future__ import annotations

import posixpath
import shlex

from app.schemas.servers import ServerCreate
from app.services.awg_config import CLIENTS_TABLE_PATHS, parse_client_names, parse_interface, parse_peers
from app.services.awg_detect import _container_names, _locate_config
from app.services.client_store import client_store
from app.services.server_store import server_store
from app.ssh import exec as ssh_exec


def run_awg_import(server_id: str, server_name: str, payload: ServerCreate) -> tuple[int, int | None]:
    """Возвращает (кол-во импортированных клиентов, vpn_port)."""
    ssh = ssh_exec.connect(
        host=payload.host,
        port=payload.ssh_port,
        username=payload.ssh_username,
        password=payload.ssh_password,
        key=payload.ssh_key,
    )
    try:
        containers = payload.container_names or _container_names(ssh)
        config_path, container, config_text = _locate_config(ssh, containers)
        if not config_text.strip():
            return 0, None

        interface = parse_interface(config_text)
        peers = parse_peers(config_text)
        if not peers:
            return 0, interface.listen_port

        names = _load_client_names(ssh, container, config_path)
        count = client_store.import_peers(
            server_id,
            server_name=server_name,
            peers=peers,
            names=names,
        )
        return count, interface.listen_port
    finally:
        ssh.close()


def _load_client_names(ssh, container, config_path) -> dict[str, str]:
    if container and config_path:
        for path in _candidate_table_paths(config_path):
            text = _read_in_container(ssh, container, path)
            mapping = parse_client_names(text)
            if mapping:
                return mapping

    for path in CLIENTS_TABLE_PATHS:
        text = ssh_exec.run(ssh, f"cat {shlex.quote(path)} 2>/dev/null || true").stdout
        mapping = parse_client_names(text)
        if mapping:
            return mapping

    return {}


def _candidate_table_paths(config_path: str) -> list[str]:
    paths = list(CLIENTS_TABLE_PATHS)
    sibling = posixpath.join(posixpath.dirname(config_path), "clientsTable")
    if sibling not in paths:
        paths.insert(0, sibling)
    return paths


def _read_in_container(ssh, container: str, path: str) -> str:
    cmd = f"docker exec {shlex.quote(container)} sh -c {shlex.quote('cat ' + shlex.quote(path) + ' 2>/dev/null || true')}"
    return ssh_exec.run(ssh, cmd).stdout
