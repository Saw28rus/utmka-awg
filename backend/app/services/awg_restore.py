"""Восстановление AmneziaWG-входа из снапшота на ЧИСТЫЙ новый VPS (RES2a).

В отличие от `awg_install` (генерит НОВЫЕ ключи сервера и случайную маскировку),
здесь мы разворачиваем `/opt/amnezia/awg/` из зашифрованного снапшота старого
входа: тот же server PrivateKey, те же peers, та же маскировка и тот же
ListenPort. Поэтому клиентские конфиги остаются валидны — handshake идёт со
старым клиентским ключом к тому же серверному ключу, просто на новом IP.

Жёсткие инварианты («калашников», fail-closed):
- порт берём ИЗ снапшота (ListenPort) — клиентские конфиги смотрят на него;
- НЕ запускаем configure_container.sh (он бы сгенерил новые ключи);
- после старта сверяем server public key и число peers со снапшотом — иначе
  это уже «не тот» вход и клиентов он не примет → ошибка.

См. _dev-docs/ENTRY_REPLACEMENT_PLAN.md §5 (Шаг 3–4).
"""

from __future__ import annotations

import base64
import gzip
import io
import shlex
import tarfile
from dataclasses import dataclass, field
from typing import Optional

from app.services.amnezia_ssh import (
    base_vars,
    container_exists,
    docker_available,
    load_script,
    read_container_file,
    replace_vars,
    run_script,
    write_host_file,
)
from app.services.awg_config import parse_interface, parse_peers
from app.services.awg_install import (
    DEFAULT_CIDR,
    DEFAULT_SUBNET_IP,
    VARIANTS,
    _build_image,
    _ensure_docker,
    _prepare_host,
    _run_container,
    _startup_container,
    _upload_dockerfile,
    _verify_running,
)
from app.ssh import exec as ssh_exec

AMNEZIA_AWG_DIR = "/opt/amnezia/awg"


class AwgRestoreError(Exception):
    pass


@dataclass
class SnapshotMeta:
    listen_port: Optional[int]
    subnet_ip: str
    cidr: str
    peers_count: int
    server_public_key: Optional[str]
    has_private_key: bool
    awg_params: dict = field(default_factory=dict)


def parse_snapshot_blob(blob_b64: str) -> SnapshotMeta:
    """Разобрать снапшот (base64 gzip-tar) в памяти панели, без обращения к VPS.

    Достаёт ListenPort/Address/peers/маскировку/server pubkey из awg0.conf и
    wireguard_server_public_key.key. Бросает AwgRestoreError при битом архиве.
    """
    try:
        raw = base64.b64decode(blob_b64)
    except Exception as exc:  # noqa: BLE001
        raise AwgRestoreError("Снапшот повреждён (base64).") from exc

    awg0_text: Optional[str] = None
    server_pub: Optional[str] = None
    try:
        # Снапшот снят как `tar czf - -C /opt/amnezia/awg .` → пути вида ./awg0.conf
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
            for member in tar.getmembers():
                name = member.name.lstrip("./")
                if name == "awg0.conf":
                    f = tar.extractfile(member)
                    if f:
                        awg0_text = f.read().decode("utf-8", errors="replace")
                elif name == "wireguard_server_public_key.key":
                    f = tar.extractfile(member)
                    if f:
                        server_pub = f.read().decode("utf-8", errors="replace").strip()
    except (tarfile.TarError, OSError, gzip.BadGzipFile) as exc:
        raise AwgRestoreError("Снапшот повреждён (tar.gz).") from exc

    if not awg0_text:
        raise AwgRestoreError("В снапшоте нет awg0.conf — нечего восстанавливать.")

    info = parse_interface(awg0_text)
    if not info.private_key:
        raise AwgRestoreError("В снапшоте нет server PrivateKey — вход восстановить нельзя.")
    if not info.listen_port:
        raise AwgRestoreError("В снапшоте нет ListenPort — порт входа неизвестен.")

    subnet_ip, cidr = DEFAULT_SUBNET_IP, DEFAULT_CIDR
    if info.address:
        # Address вида "10.8.1.1/24"
        addr = info.address.split(",")[0].strip()
        if "/" in addr:
            ip_part, cidr_part = addr.split("/", 1)
            subnet_ip = ip_part.strip() or DEFAULT_SUBNET_IP
            cidr = cidr_part.strip() or DEFAULT_CIDR
        elif addr:
            subnet_ip = addr

    peers = parse_peers(awg0_text)
    return SnapshotMeta(
        listen_port=info.listen_port,
        subnet_ip=subnet_ip,
        cidr=cidr,
        peers_count=len(peers),
        server_public_key=server_pub,
        has_private_key=bool(info.private_key),
        awg_params=dict(info.awg_params),
    )


def _restore_tar_into_container(ssh, container: str, blob_b64: str) -> None:
    """Развернуть архив снапшота в /opt/amnezia/awg внутри контейнера."""
    ssh_exec.run(ssh, f"sudo docker exec {shlex.quote(container)} mkdir -p {shlex.quote(AMNEZIA_AWG_DIR)}", timeout=30)
    tmp = f"/tmp/utmka_{container}_entry_restore.b64"
    write_host_file(ssh, tmp, blob_b64, mode="600")
    cmd = (
        f"base64 -d {shlex.quote(tmp)} | sudo docker exec -i {shlex.quote(container)} "
        f"tar xzf - -C {shlex.quote(AMNEZIA_AWG_DIR)}; rc=$?; sudo rm -f {shlex.quote(tmp)}; exit $rc"
    )
    result = ssh_exec.run(ssh, cmd, timeout=120)
    if result.exit_code != 0:
        raise AwgRestoreError(
            f"Не удалось развернуть снапшот в контейнере: {result.stderr.strip()[-300:]}"
        )


def _container_server_pubkey(ssh, container: str, iface: str = "awg0") -> Optional[str]:
    out = ssh_exec.run(
        ssh,
        f"sudo docker exec {shlex.quote(container)} sh -c "
        f"{shlex.quote(f'awg show {iface} public-key 2>/dev/null || wg show {iface} public-key 2>/dev/null || true')}",
        timeout=20,
    ).stdout.strip()
    return out or None


def _container_peers_count(ssh, container: str, iface: str = "awg0") -> int:
    out = ssh_exec.run(
        ssh,
        f"sudo docker exec {shlex.quote(container)} sh -c "
        f"{shlex.quote(f'awg show {iface} peers 2>/dev/null || wg show {iface} peers 2>/dev/null || true')}",
        timeout=20,
    ).stdout.strip()
    return len([ln for ln in out.splitlines() if ln.strip()])


def _iface_up(ssh, container: str, iface: str = "awg0") -> bool:
    out = ssh_exec.run(
        ssh,
        f"sudo docker exec {shlex.quote(container)} sh -c "
        f"{shlex.quote(f'ip -o link show {iface} >/dev/null 2>&1 && echo up || echo down')}",
        timeout=20,
    ).stdout.strip()
    return out.endswith("up")


def restore_awg_entry(ssh, host: str, blob_b64: str, meta: SnapshotMeta) -> dict:
    """Развернуть AWG2-вход из снапшота на новом VPS (ssh уже подключён к нему).

    Возвращает {port, peers_count, server_public_key, container}. Бросает
    AwgRestoreError при любом несоответствии (контейнер уже есть, порт занят,
    pubkey/peers не совпали и т.п.). Вызывающий делает rollback при ошибке.
    """
    cfg = VARIANTS["awg2"]
    container = cfg["container"]
    iface = cfg["iface"]
    port = meta.listen_port
    if not port:
        raise AwgRestoreError("Порт входа неизвестен — восстановление прервано.")

    if container_exists(ssh, container):
        raise AwgRestoreError(f"На новом сервере уже есть контейнер {container} — он не чистый.")

    if not docker_available(ssh):
        _ensure_docker(ssh, host, container, cfg["folder"])

    # vars_map для сборки/запуска/старта. Маскировку НЕ задаём — она придёт со
    # снапшота (configure_container.sh намеренно не запускаем).
    vars_map = base_vars(host, container, cfg["folder"])
    vars_map["$AWG_SERVER_PORT"] = str(port)
    vars_map["$AWG_SUBNET_IP"] = meta.subnet_ip
    vars_map["$WIREGUARD_SUBNET_CIDR"] = meta.cidr

    _prepare_host(ssh, vars_map)
    _upload_dockerfile(ssh, cfg, vars_map)
    _build_image(ssh, vars_map)
    _run_container(ssh, cfg, vars_map)

    # Разворачиваем ключи+peers+маскировку поверх свежего контейнера.
    _restore_tar_into_container(ssh, container, blob_b64)

    # Поднимаем интерфейс по восстановленному awg0.conf.
    _startup_container(ssh, cfg, vars_map)
    _verify_running(ssh, container)

    # --- сверка идентичности (fail-closed) ---
    if not _iface_up(ssh, container, iface):
        raise AwgRestoreError(f"Интерфейс {iface} не поднялся после восстановления.")

    new_pub = _container_server_pubkey(ssh, container, iface)
    if meta.server_public_key and new_pub and new_pub != meta.server_public_key:
        raise AwgRestoreError(
            "Публичный ключ сервера на новом VPS не совпал со снапшотом — "
            "клиенты не подключатся. Восстановление прервано."
        )

    new_peers = _container_peers_count(ssh, container, iface)
    if meta.peers_count and new_peers < meta.peers_count:
        raise AwgRestoreError(
            f"На новом VPS поднялось peers={new_peers}, ожидалось {meta.peers_count} — "
            "часть клиентов потерялась. Восстановление прервано."
        )

    return {
        "port": port,
        "peers_count": new_peers,
        "server_public_key": new_pub or meta.server_public_key,
        "container": container,
    }


def rollback_new_vps(ssh, container: str = "amnezia-awg2") -> None:
    """Снести контейнер на новом VPS (best-effort) при сбое — старый вход цел."""
    ssh_exec.run(ssh, f"sudo docker rm -f {shlex.quote(container)} 2>/dev/null || true", timeout=60)
    ssh_exec.run(ssh, f"sudo docker rmi {shlex.quote(container)} 2>/dev/null || true", timeout=60)
