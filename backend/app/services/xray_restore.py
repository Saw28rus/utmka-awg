"""Восстановление Xray-входа (VLESS-Reality) из снапшота на ЧИСТЫЙ новый VPS.

Зеркало `awg_restore`, но для Xray. В отличие от `install_xray` (генерит НОВЫЕ
Reality-ключи и UUID), здесь мы разворачиваем `/opt/amnezia/xray/` из
зашифрованного снапшота старого входа: тот же приватный/публичный Reality-ключ,
тот же short_id, тот же UUID, тот же server.json (включая каскадный outbound на
exit и split-routing). Поэтому клиентские vless://-ссылки остаются валидны —
клиент по-прежнему ходит на ДОМЕН (DNS перепривязываем на новый IP), а Reality
public key / short_id / uuid совпадают со снапшотом.

Жёсткие инварианты (fail-closed):
- НЕ запускаем configure_container.sh (он бы сгенерил новые ключи);
- режим публикации (:443 напрямую или 127.0.0.1:1443 за nginx-passthrough)
  берём из записи СТАРОГО входа — чтобы host-nginx (его конфиг переносим
  отдельно через copy_path) фронтил Xray точно так же, как на старом сервере;
- после старта сверяем xray_public.key (и uuid) со снапшотом — иначе это «не
  тот» вход, клиенты Reality не подключатся → ошибка и откат.
"""

from __future__ import annotations

import base64
import gzip
import io
import shlex
import tarfile
from dataclasses import dataclass
from typing import Optional

from app.services.amnezia_ssh import (
    container_exists,
    docker_available,
    read_container_file,
    write_host_file,
)
from app.services.xray_install import (
    CONTAINER_NAME,
    DEFAULT_PORT,
    XRAY_LOCAL_PORT,
    XrayInstallError,
    _build_image,
    _build_vars,
    _ensure_docker,
    _prepare_host,
    _run_container,
    _startup_container,
    _upload_dockerfile,
    _verify_running,
)
from app.ssh import exec as ssh_exec

XRAY_DIR = "/opt/amnezia/xray"


class XrayRestoreError(Exception):
    pass


@dataclass
class XraySnapshotMeta:
    uuid: Optional[str]
    public_key: Optional[str]
    short_id: Optional[str]
    has_private_key: bool


def parse_xray_snapshot_blob(blob_b64: str) -> XraySnapshotMeta:
    """Разобрать снапшот Xray (base64 gzip-tar) в памяти панели, без SSH.

    Достаёт uuid / public key / short_id / наличие приватного ключа из
    xray_*.key. Бросает XrayRestoreError при битом архиве или отсутствии
    приватного ключа (без него вход не восстановить идентично).
    """
    try:
        raw = base64.b64decode(blob_b64)
    except Exception as exc:  # noqa: BLE001
        raise XrayRestoreError("Снапшот Xray повреждён (base64).") from exc

    files: dict[str, str] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
            for member in tar.getmembers():
                name = member.name.lstrip("./")
                if name in {
                    "xray_uuid.key",
                    "xray_public.key",
                    "xray_private.key",
                    "xray_short_id.key",
                }:
                    f = tar.extractfile(member)
                    if f:
                        files[name] = f.read().decode("utf-8", errors="replace").strip()
    except (tarfile.TarError, OSError, gzip.BadGzipFile) as exc:
        raise XrayRestoreError("Снапшот Xray повреждён (tar.gz).") from exc

    if not files.get("xray_private.key"):
        raise XrayRestoreError(
            "В снапшоте Xray нет приватного Reality-ключа — вход восстановить идентично нельзя."
        )

    return XraySnapshotMeta(
        uuid=files.get("xray_uuid.key") or None,
        public_key=files.get("xray_public.key") or None,
        short_id=files.get("xray_short_id.key") or None,
        has_private_key=True,
    )


def _restore_tar_into_container(ssh, blob_b64: str) -> None:
    """Развернуть архив снапшота в /opt/amnezia/xray внутри контейнера."""
    ssh_exec.run(
        ssh,
        f"sudo docker exec {shlex.quote(CONTAINER_NAME)} mkdir -p {shlex.quote(XRAY_DIR)}",
        timeout=30,
    )
    tmp = "/tmp/utmka_xray_entry_restore.b64"
    write_host_file(ssh, tmp, blob_b64, mode="600")
    cmd = (
        f"base64 -d {shlex.quote(tmp)} | sudo docker exec -i {shlex.quote(CONTAINER_NAME)} "
        f"tar xzf - -C {shlex.quote(XRAY_DIR)}; rc=$?; sudo rm -f {shlex.quote(tmp)}; exit $rc"
    )
    result = ssh_exec.run(ssh, cmd, timeout=120)
    if result.exit_code != 0:
        raise XrayRestoreError(
            f"Не удалось развернуть снапшот Xray в контейнере: {result.stderr.strip()[-300:]}"
        )


def restore_xray_entry(
    ssh,
    host: str,
    blob_b64: str,
    meta: XraySnapshotMeta,
    *,
    reserved: bool = False,
    connect_port: int = DEFAULT_PORT,
    transport: str = "tcp",
) -> dict:
    """Развернуть Xray-вход из снапшота на новом VPS (ssh уже подключён к нему).

    reserved=True → панель на новом узле фронтит :443 nginx-passthrough'ом, контейнер
    публикуем на 127.0.0.1:{XRAY_LOCAL_PORT} (host-nginx конфиг переносится отдельно).
    reserved=False → публикуем на 0.0.0.0:{connect_port}.

    Возвращает {port, public_key, uuid, container, reserved}. Бросает XrayRestoreError
    при несоответствии (контейнер уже есть, pubkey не совпал и т.п.) — вызывающий
    делает rollback.
    """
    if container_exists(ssh, CONTAINER_NAME):
        raise XrayRestoreError(
            f"На новом сервере уже есть контейнер {CONTAINER_NAME} — он не чистый."
        )

    if not docker_available(ssh):
        _ensure_docker(ssh, host)

    # internal_port — порт ВНУТРИ контейнера (всегда :443, на него смотрят
    # клиентские ссылки и каскад). publish — как пробрасываем наружу.
    vars_map = _build_vars(host, DEFAULT_PORT, "www.googletagmanager.com", transport)
    if reserved:
        vars_map["$XRAY_PUBLISH"] = f"127.0.0.1:{XRAY_LOCAL_PORT}"
    else:
        vars_map["$XRAY_PUBLISH"] = str(connect_port)
        vars_map["$XRAY_SERVER_PORT"] = str(connect_port)

    try:
        _prepare_host(ssh, vars_map)
        _upload_dockerfile(ssh, vars_map)
        _build_image(ssh, vars_map)
        _run_container(ssh, vars_map)
    except XrayInstallError as exc:
        raise XrayRestoreError(str(exc)) from exc

    # Разворачиваем ключи + server.json поверх свежего контейнера (НЕ configure!).
    _restore_tar_into_container(ssh, blob_b64)

    # Поднимаем xray по восстановленному server.json.
    try:
        _startup_container(ssh, vars_map)
        _verify_running(ssh)
    except XrayInstallError as exc:
        raise XrayRestoreError(str(exc)) from exc

    # --- сверка идентичности (fail-closed) ---
    new_pub = (read_container_file(ssh, CONTAINER_NAME, "/opt/amnezia/xray/xray_public.key") or "").strip()
    if meta.public_key and new_pub and new_pub != meta.public_key:
        raise XrayRestoreError(
            "Публичный Reality-ключ Xray на новом VPS не совпал со снапшотом — "
            "клиенты не подключатся. Восстановление прервано."
        )
    new_uuid = (read_container_file(ssh, CONTAINER_NAME, "/opt/amnezia/xray/xray_uuid.key") or "").strip()

    return {
        "port": connect_port,
        "public_key": new_pub or meta.public_key,
        "uuid": new_uuid or meta.uuid,
        "container": CONTAINER_NAME,
        "reserved": reserved,
    }


def rollback_xray_vps(ssh, container: str = CONTAINER_NAME) -> None:
    """Снести Xray-контейнер на новом VPS (best-effort) при сбое — старый вход цел."""
    ssh_exec.run(ssh, f"sudo docker rm -f {shlex.quote(container)} 2>/dev/null || true", timeout=60)
    ssh_exec.run(ssh, f"sudo docker rmi {shlex.quote(container)} 2>/dev/null || true", timeout=60)
