"""Установка классического WireGuard на сервер по сценарию Amnezia."""

from __future__ import annotations

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

CONTAINER_NAME = "amnezia-wireguard"
DOCKER_FOLDER = "/opt/amnezia/amnezia-wireguard"
SCRIPTS = "wireguard"
DEFAULT_PORT = 51820
DEFAULT_SUBNET_IP = "10.8.2.1"
DEFAULT_CIDR = "24"


@dataclass
class WireguardInstallResult:
    message: str
    container: str
    port: int
    public_key: Optional[str] = None


class WireguardInstallError(Exception):
    pass


def install_wireguard(server_id: str, *, port: int = DEFAULT_PORT) -> WireguardInstallResult:
    record, target, ssh = connect_target(server_id)
    try:
        if container_exists(ssh, CONTAINER_NAME):
            raise WireguardInstallError(f"WireGuard уже установлен (контейнер {CONTAINER_NAME}).")

        if not docker_available(ssh):
            _ensure_docker(ssh, target.host)

        if port_busy(ssh, port, proto="udp"):
            raise WireguardInstallError(f"UDP-порт {port} уже занят на сервере.")

        vars_map = _build_vars(target.host, port)
        _prepare_host(ssh, vars_map)
        write_host_file(ssh, f"{DOCKER_FOLDER}/Dockerfile", load_script(SCRIPTS, "Dockerfile"), mode="700")
        _build_image(ssh, vars_map)
        _run_container(ssh, vars_map)
        _configure_container(ssh, vars_map)
        _startup_container(ssh, vars_map)
        _verify_running(ssh)

        public_key = read_container_file(
            ssh, CONTAINER_NAME, "/opt/amnezia/wireguard/wireguard_server_public_key.key"
        ) or None
        _register(server_id, record, port)

        return WireguardInstallResult(
            message="WireGuard установлен. Сервер поднят, добавь клиента вручную по публичному ключу сервера.",
            container=CONTAINER_NAME,
            port=port,
            public_key=public_key,
        )
    except WireguardInstallError as exc:
        if "уже установлен" not in str(exc).lower():
            _rollback(ssh)
        raise
    except Exception as exc:  # noqa: BLE001
        _rollback(ssh)
        raise WireguardInstallError(str(exc)) from exc
    finally:
        ssh.close()


def _build_vars(host: str, port: int) -> dict[str, str]:
    vars_map = base_vars(host, CONTAINER_NAME, DOCKER_FOLDER)
    vars_map["$WIREGUARD_SERVER_PORT"] = str(port)
    vars_map["$WIREGUARD_SUBNET_IP"] = DEFAULT_SUBNET_IP
    vars_map["$WIREGUARD_SUBNET_CIDR"] = DEFAULT_CIDR
    return vars_map


def _ensure_docker(ssh, host: str) -> None:
    script = replace_vars(load_script("shared", "install_docker.sh"), base_vars(host, CONTAINER_NAME, DOCKER_FOLDER))
    result = run_script(ssh, script, timeout=300)
    if result.exit_code != 0 or "command not found" in result.stdout.lower():
        raise WireguardInstallError("Docker не установлен и автоматическая установка не удалась.")
    if not docker_available(ssh):
        raise WireguardInstallError("Docker недоступен после установки.")


def _prepare_host(ssh, vars_map: dict[str, str]) -> None:
    script = replace_vars(load_script("shared", "prepare_host.sh"), vars_map)
    result = run_script(ssh, script, timeout=60)
    if result.exit_code != 0:
        raise WireguardInstallError(f"Подготовка хоста не удалась: {result.stderr.strip() or result.stdout.strip()}")


def _build_image(ssh, vars_map: dict[str, str]) -> None:
    script = replace_vars(load_script("shared", "build_container.sh"), vars_map)
    result = run_script(ssh, script, timeout=600)
    combined = f"{result.stdout}\n{result.stderr}"
    if "pull rate limit" in combined.lower():
        raise WireguardInstallError("Docker Hub ограничил скачивание образов. Повтори позже.")
    if result.exit_code != 0:
        raise WireguardInstallError(f"Сборка образа не удалась: {combined.strip()[-500:]}")


def _run_container(ssh, vars_map: dict[str, str]) -> None:
    script = replace_vars(load_script(SCRIPTS, "run_container.sh"), vars_map)
    result = run_script(ssh, script, timeout=120)
    combined = f"{result.stdout}\n{result.stderr}"
    if "address already in use" in combined or "is already in use by container" in combined:
        raise WireguardInstallError("Порт уже занят другим контейнером.")
    if result.exit_code != 0:
        raise WireguardInstallError(f"Запуск контейнера не удался: {combined.strip()[-500:]}")


def _configure_container(ssh, vars_map: dict[str, str]) -> None:
    script = replace_vars(load_script(SCRIPTS, "configure_container.sh"), vars_map)
    result = run_container_script(ssh, CONTAINER_NAME, script, timeout=120)
    if result.exit_code != 0:
        raise WireguardInstallError(f"Настройка внутри контейнера не удалась: {result.stderr.strip() or result.stdout.strip()}")


def _startup_container(ssh, vars_map: dict[str, str]) -> None:
    start_script = replace_vars(load_script(SCRIPTS, "start.sh"), vars_map)
    b64_path = "/tmp/utmka_wireguard_start.sh"
    write_host_file(ssh, b64_path, start_script, mode="755")
    copy_cmd = (
        f"sudo docker cp {shlex.quote(b64_path)} {shlex.quote(CONTAINER_NAME)}:/opt/amnezia/start.sh "
        f"&& sudo docker exec {shlex.quote(CONTAINER_NAME)} chmod a+x /opt/amnezia/start.sh "
        f"&& sudo docker exec -d {shlex.quote(CONTAINER_NAME)} /opt/amnezia/start.sh "
        f"&& sudo rm -f {shlex.quote(b64_path)}"
    )
    result = ssh_exec.run(ssh, copy_cmd, timeout=60)
    if result.exit_code != 0:
        raise WireguardInstallError(f"Не удалось запустить интерфейс в контейнере: {result.stderr.strip()}")


def _verify_running(ssh) -> None:
    status = ssh_exec.run(
        ssh,
        f"docker inspect -f '{{{{.State.Status}}}}' {shlex.quote(CONTAINER_NAME)} 2>/dev/null || true",
    ).stdout.strip()
    if status != "running":
        raise WireguardInstallError(f"Контейнер {CONTAINER_NAME} не перешёл в состояние running.")


def _register(server_id: str, record: dict, port: int) -> None:
    names = list(record.get("container_names") or [])
    if CONTAINER_NAME not in names:
        names.append(CONTAINER_NAME)
    protocols = dict(record.get("installed_protocols") or {})
    protocols["wireguard"] = {"port": port, "container": CONTAINER_NAME}
    server_store.update_runtime(server_id, container_names=names, installed_protocols=protocols)


def _rollback(ssh) -> None:
    ssh_exec.run(ssh, f"docker rm -f {shlex.quote(CONTAINER_NAME)} 2>/dev/null || true", timeout=60)
    ssh_exec.run(ssh, f"docker rmi {shlex.quote(CONTAINER_NAME)} 2>/dev/null || true", timeout=60)
