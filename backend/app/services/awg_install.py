"""Установка AmneziaWG (awg-go) и AmneziaWG Legacy на сервер по сценарию Amnezia."""

from __future__ import annotations

import secrets as _secrets
import shlex
from dataclasses import dataclass
from typing import Optional

from app.services.amnezia_ssh import (
    base_vars,
    connect_target,
    container_exists,
    docker_available,
    load_script,
    port_busy,
    read_container_file,
    replace_vars,
    run_container_script,
    run_script,
    write_host_file,
)
from app.services.server_store import server_store
from app.ssh import exec as ssh_exec

# variant -> (container, folder, script_dir, conf, iface, store_key)
VARIANTS = {
    "awg2": {
        "container": "amnezia-awg2",
        "folder": "/opt/amnezia/amnezia-awg2",
        "scripts": "awg",
        "conf": "/opt/amnezia/awg/awg0.conf",
        "iface": "awg0",
        "store_key": "awg2",
        "label": "AmneziaWG",
        "with_s34": True,
    },
    "awg_legacy": {
        "container": "amnezia-awg",
        "folder": "/opt/amnezia/amnezia-awg",
        "scripts": "awg_legacy",
        "conf": "/opt/amnezia/awg/wg0.conf",
        "iface": "wg0",
        "store_key": "awg_legacy",
        "label": "AmneziaWG Legacy",
        "with_s34": False,
    },
}

DEFAULT_PORT = 39547
DEFAULT_SUBNET_IP = "10.8.1.1"
DEFAULT_CIDR = "24"


@dataclass
class AwgInstallResult:
    message: str
    container: str
    port: int
    public_key: Optional[str] = None


class AwgInstallError(Exception):
    pass


def install_awg(server_id: str, *, variant: str = "awg2", port: int = DEFAULT_PORT) -> AwgInstallResult:
    cfg = VARIANTS.get(variant)
    if not cfg:
        raise AwgInstallError("Неизвестная версия AmneziaWG.")

    container = cfg["container"]
    record, target, ssh = connect_target(server_id)
    try:
        if container_exists(ssh, container):
            raise AwgInstallError(f"{cfg['label']} уже установлен (контейнер {container}).")

        if not docker_available(ssh):
            _ensure_docker(ssh, target.host, container, cfg["folder"])

        if port_busy(ssh, port, proto="udp"):
            raise AwgInstallError(f"UDP-порт {port} уже занят на сервере.")

        vars_map = _build_vars(target.host, port, cfg)
        _prepare_host(ssh, vars_map)
        _upload_dockerfile(ssh, cfg, vars_map)
        _build_image(ssh, vars_map)
        _run_container(ssh, cfg, vars_map)
        _configure_container(ssh, cfg, vars_map)
        _startup_container(ssh, cfg, vars_map)
        _verify_running(ssh, container)

        public_key = read_container_file(ssh, container, "/opt/amnezia/awg/wireguard_server_public_key.key") or None
        _register(server_id, record, cfg, port)

        return AwgInstallResult(
            message=f"{cfg['label']} установлен. Добавляй клиентов во вкладке «Клиенты».",
            container=container,
            port=port,
            public_key=public_key,
        )
    except AwgInstallError as exc:
        if "уже установлен" not in str(exc).lower():
            _rollback(ssh, container)
        raise
    except Exception as exc:  # noqa: BLE001
        _rollback(ssh, container)
        raise AwgInstallError(str(exc)) from exc
    finally:
        ssh.close()


def _awg_params(with_s34: bool) -> dict[str, str]:
    headers = set()
    while len(headers) < 4:
        headers.add(_secrets.randbelow(2_147_483_640) + 5)
    h1, h2, h3, h4 = list(headers)

    s1 = _secrets.randbelow(136) + 15  # 15..150
    s2 = _secrets.randbelow(136) + 15
    while s1 + 56 == s2:
        s2 = _secrets.randbelow(136) + 15

    params = {
        "$JUNK_PACKET_COUNT": str(_secrets.randbelow(8) + 3),  # 3..10
        "$JUNK_PACKET_MIN_SIZE": "50",
        "$JUNK_PACKET_MAX_SIZE": "1000",
        "$INIT_PACKET_JUNK_SIZE": str(s1),
        "$RESPONSE_PACKET_JUNK_SIZE": str(s2),
        "$COOKIE_REPLY_PACKET_JUNK_SIZE": str(_secrets.randbelow(136) + 15) if with_s34 else "0",
        "$TRANSPORT_PACKET_JUNK_SIZE": str(_secrets.randbelow(136) + 15) if with_s34 else "0",
        "$INIT_PACKET_MAGIC_HEADER": str(h1),
        "$RESPONSE_PACKET_MAGIC_HEADER": str(h2),
        "$UNDERLOAD_PACKET_MAGIC_HEADER": str(h3),
        "$TRANSPORT_PACKET_MAGIC_HEADER": str(h4),
    }
    return params


def _build_vars(host: str, port: int, cfg: dict) -> dict[str, str]:
    vars_map = base_vars(host, cfg["container"], cfg["folder"])
    vars_map["$AWG_SERVER_PORT"] = str(port)
    vars_map["$AWG_SUBNET_IP"] = DEFAULT_SUBNET_IP
    vars_map["$WIREGUARD_SUBNET_CIDR"] = DEFAULT_CIDR
    vars_map.update(_awg_params(cfg["with_s34"]))
    return vars_map


def _ensure_docker(ssh, host: str, container: str, folder: str) -> None:
    script = replace_vars(load_script("shared", "install_docker.sh"), base_vars(host, container, folder))
    result = run_script(ssh, script, timeout=300)
    if result.exit_code != 0 or "command not found" in result.stdout.lower():
        raise AwgInstallError("Docker не установлен и автоматическая установка не удалась.")
    if not docker_available(ssh):
        raise AwgInstallError("Docker недоступен после установки.")


def _prepare_host(ssh, vars_map: dict[str, str]) -> None:
    script = replace_vars(load_script("shared", "prepare_host.sh"), vars_map)
    result = run_script(ssh, script, timeout=60)
    if result.exit_code != 0:
        raise AwgInstallError(f"Подготовка хоста не удалась: {result.stderr.strip() or result.stdout.strip()}")


def _upload_dockerfile(ssh, cfg: dict, vars_map: dict[str, str]) -> None:
    dockerfile = load_script(cfg["scripts"], "Dockerfile")
    write_host_file(ssh, f"{cfg['folder']}/Dockerfile", dockerfile, mode="700")


def _build_image(ssh, vars_map: dict[str, str]) -> None:
    script = replace_vars(load_script("shared", "build_container.sh"), vars_map)
    result = run_script(ssh, script, timeout=600)
    combined = f"{result.stdout}\n{result.stderr}"
    if "pull rate limit" in combined.lower():
        raise AwgInstallError("Docker Hub ограничил скачивание образов. Повтори позже.")
    if result.exit_code != 0:
        raise AwgInstallError(f"Сборка образа не удалась: {combined.strip()[-500:]}")


def _run_container(ssh, cfg: dict, vars_map: dict[str, str]) -> None:
    script = replace_vars(load_script(cfg["scripts"], "run_container.sh"), vars_map)
    result = run_script(ssh, script, timeout=120)
    combined = f"{result.stdout}\n{result.stderr}"
    if "address already in use" in combined or "is already in use by container" in combined:
        raise AwgInstallError("Порт уже занят другим контейнером.")
    if result.exit_code != 0:
        raise AwgInstallError(f"Запуск контейнера не удался: {combined.strip()[-500:]}")


def _configure_container(ssh, cfg: dict, vars_map: dict[str, str]) -> None:
    script = replace_vars(load_script(cfg["scripts"], "configure_container.sh"), vars_map)
    result = run_container_script(ssh, cfg["container"], script, timeout=120)
    if result.exit_code != 0:
        raise AwgInstallError(f"Настройка внутри контейнера не удалась: {result.stderr.strip() or result.stdout.strip()}")


def _startup_container(ssh, cfg: dict, vars_map: dict[str, str]) -> None:
    start_script = replace_vars(load_script(cfg["scripts"], "start.sh"), vars_map)
    container = cfg["container"]
    b64_path = f"/tmp/utmka_{cfg['store_key']}_start.sh"
    write_host_file(ssh, b64_path, start_script, mode="755")
    copy_cmd = (
        f"sudo docker cp {shlex.quote(b64_path)} {shlex.quote(container)}:/opt/amnezia/start.sh "
        f"&& sudo docker exec {shlex.quote(container)} chmod a+x /opt/amnezia/start.sh "
        f"&& sudo docker exec -d {shlex.quote(container)} /opt/amnezia/start.sh "
        f"&& sudo rm -f {shlex.quote(b64_path)}"
    )
    result = ssh_exec.run(ssh, copy_cmd, timeout=60)
    if result.exit_code != 0:
        raise AwgInstallError(f"Не удалось запустить интерфейс в контейнере: {result.stderr.strip()}")


def _verify_running(ssh, container: str) -> None:
    status = ssh_exec.run(
        ssh,
        f"docker inspect -f '{{{{.State.Status}}}}' {shlex.quote(container)} 2>/dev/null || true",
    ).stdout.strip()
    if status != "running":
        raise AwgInstallError(f"Контейнер {container} не перешёл в состояние running.")


def _register(server_id: str, record: dict, cfg: dict, port: int) -> None:
    container = cfg["container"]
    names = list(record.get("container_names") or [])
    if container not in names:
        names.append(container)
    protocols = dict(record.get("installed_protocols") or {})
    protocols[cfg["store_key"]] = {"port": port, "container": container}
    server_store.update_runtime(
        server_id,
        container_names=names,
        installed_protocols=protocols,
        awg2_imported=True,
        vpn_port=port,
    )


def _rollback(ssh, container: str) -> None:
    ssh_exec.run(ssh, f"docker rm -f {shlex.quote(container)} 2>/dev/null || true", timeout=60)
    ssh_exec.run(ssh, f"docker rmi {shlex.quote(container)} 2>/dev/null || true", timeout=60)
