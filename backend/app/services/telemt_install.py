"""Установка Telemt (Telegram MTProto Proxy) на сервер по сценарию Amnezia."""

from __future__ import annotations

import shlex
import time
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

CONTAINER_NAME = "amnezia-telemt"
DOCKER_FOLDER = "/opt/amnezia/amnezia-telemt"
SCRIPTS = "telemt"
DEFAULT_PORT = 443
DEFAULT_TLS_DOMAIN = "www.cloudflare.com"
USER_NAME = "amnezia"


@dataclass
class TelemtInstallResult:
    message: str
    container: str
    port: int
    tls_domain: str
    secret: Optional[str] = None
    tg_link: Optional[str] = None


class TelemtInstallError(Exception):
    pass


def install_telemt(
    server_id: str, *, port: int = DEFAULT_PORT, tls_domain: Optional[str] = None
) -> TelemtInstallResult:
    domain = (tls_domain or DEFAULT_TLS_DOMAIN).strip() or DEFAULT_TLS_DOMAIN

    record, target, ssh = connect_target(server_id)
    try:
        if container_exists(ssh, CONTAINER_NAME):
            raise TelemtInstallError(f"Telemt уже установлен (контейнер {CONTAINER_NAME}).")

        if not docker_available(ssh):
            _ensure_docker(ssh, target.host)

        if port_busy(ssh, port, proto="tcp"):
            raise TelemtInstallError(f"TCP-порт {port} уже занят на сервере.")

        vars_map = _build_vars(target.host, port, domain)
        _prepare_host(ssh, vars_map)
        write_host_file(ssh, f"{DOCKER_FOLDER}/Dockerfile", load_script(SCRIPTS, "Dockerfile"), mode="700")
        _build_image(ssh, vars_map)
        _run_container(ssh, vars_map)
        _configure_container(ssh, vars_map)
        _startup_container(ssh, vars_map)
        _verify_running(ssh)

        secret = read_container_file(ssh, CONTAINER_NAME, "/data/secret") or None
        _register(server_id, record, port)

        tg_link = _build_tg_link(target.host, port, secret, domain) if secret else None
        return TelemtInstallResult(
            message="Telemt (Telegram Proxy) установлен. Открой ссылку в Telegram, чтобы подключить прокси.",
            container=CONTAINER_NAME,
            port=port,
            tls_domain=domain,
            secret=secret,
            tg_link=tg_link,
        )
    except TelemtInstallError as exc:
        if "уже установлен" not in str(exc).lower():
            _rollback(ssh)
        raise
    except Exception as exc:  # noqa: BLE001
        _rollback(ssh)
        raise TelemtInstallError(str(exc)) from exc
    finally:
        ssh.close()


def _build_vars(host: str, port: int, domain: str) -> dict[str, str]:
    vars_map = base_vars(host, CONTAINER_NAME, DOCKER_FOLDER)
    vars_map.update(
        {
            "$TELEMT_PORT": str(port),
            "$TELEMT_USE_MIDDLE_PROXY": "false",
            "$TELEMT_TAG": "",
            "$TELEMT_TOML_SECURE": "true",
            "$TELEMT_TOML_TLS": "true",
            "$TELEMT_PUBLIC_HOST": host,
            "$TELEMT_TLS_DOMAIN": domain,
            "$TELEMT_MASK": "true",
            "$TELEMT_TLS_EMULATION": "true",
            "$TELEMT_USER_NAME": USER_NAME,
            "$TELEMT_REGENERATE_SECRET": "1",
            "$TELEMT_SECRET": "",
        }
    )
    return vars_map


def _ensure_docker(ssh, host: str) -> None:
    script = replace_vars(load_script("shared", "install_docker.sh"), base_vars(host, CONTAINER_NAME, DOCKER_FOLDER))
    result = run_script(ssh, script, timeout=300)
    if result.exit_code != 0 or "command not found" in result.stdout.lower():
        raise TelemtInstallError("Docker не установлен и автоматическая установка не удалась.")
    if not docker_available(ssh):
        raise TelemtInstallError("Docker недоступен после установки.")


def _prepare_host(ssh, vars_map: dict[str, str]) -> None:
    script = replace_vars(load_script("shared", "prepare_host.sh"), vars_map)
    result = run_script(ssh, script, timeout=60)
    if result.exit_code != 0:
        raise TelemtInstallError(f"Подготовка хоста не удалась: {result.stderr.strip() or result.stdout.strip()}")


def _build_image(ssh, vars_map: dict[str, str]) -> None:
    script = replace_vars(load_script("shared", "build_container.sh"), vars_map)
    result = run_script(ssh, script, timeout=600)
    combined = f"{result.stdout}\n{result.stderr}"
    if "pull rate limit" in combined.lower():
        raise TelemtInstallError("Docker Hub ограничил скачивание образов. Повтори позже.")
    if result.exit_code != 0:
        raise TelemtInstallError(f"Сборка образа Telemt не удалась: {combined.strip()[-500:]}")


def _run_container(ssh, vars_map: dict[str, str]) -> None:
    script = replace_vars(load_script(SCRIPTS, "run_container.sh"), vars_map)
    result = run_script(ssh, script, timeout=120)
    combined = f"{result.stdout}\n{result.stderr}"
    if "address already in use" in combined or "is already in use by container" in combined:
        raise TelemtInstallError("Порт уже занят другим контейнером.")
    if result.exit_code != 0:
        raise TelemtInstallError(f"Запуск контейнера не удался: {combined.strip()[-500:]}")


def _configure_container(ssh, vars_map: dict[str, str]) -> None:
    script = replace_vars(load_script(SCRIPTS, "configure_container.sh"), vars_map)
    result = run_container_script(ssh, CONTAINER_NAME, script, timeout=120)
    if result.exit_code != 0:
        raise TelemtInstallError(f"Настройка Telemt внутри контейнера не удалась: {result.stderr.strip() or result.stdout.strip()}")


def _startup_container(ssh, vars_map: dict[str, str]) -> None:
    start_script = replace_vars(load_script(SCRIPTS, "start.sh"), vars_map)
    b64_path = "/tmp/utmka_telemt_start.sh"
    write_host_file(ssh, b64_path, start_script, mode="755")
    copy_cmd = (
        f"sudo docker cp {shlex.quote(b64_path)} {shlex.quote(CONTAINER_NAME)}:/opt/amnezia/start.sh "
        f"&& sudo docker exec {shlex.quote(CONTAINER_NAME)} chmod a+x /opt/amnezia/start.sh "
        f"&& sudo rm -f {shlex.quote(b64_path)} "
        f"&& sudo docker restart {shlex.quote(CONTAINER_NAME)}"
    )
    result = ssh_exec.run(ssh, copy_cmd, timeout=90)
    if result.exit_code != 0:
        raise TelemtInstallError(f"Не удалось запустить Telemt в контейнере: {result.stderr.strip()}")


def _verify_running(ssh) -> None:
    time.sleep(3)
    status = ssh_exec.run(
        ssh,
        f"docker inspect -f '{{{{.State.Status}}}}' {shlex.quote(CONTAINER_NAME)} 2>/dev/null || true",
    ).stdout.strip()
    restarting = ssh_exec.run(
        ssh,
        f"docker inspect -f '{{{{.State.Restarting}}}}' {shlex.quote(CONTAINER_NAME)} 2>/dev/null || true",
    ).stdout.strip()
    if status != "running" or restarting == "true":
        logs = ssh_exec.run(ssh, f"docker logs --tail 30 {shlex.quote(CONTAINER_NAME)} 2>&1", timeout=20).stdout
        raise TelemtInstallError(f"Telemt не запустился. Логи: {logs.strip()[-400:]}")


def _build_tg_link(host: str, port: int, secret: str, domain: str) -> str:
    # FakeTLS secret: ee + 32-hex secret + hex(domain)
    faketls = f"ee{secret}{domain.encode('utf-8').hex()}"
    return f"tg://proxy?server={host}&port={port}&secret={faketls}"


def _register(server_id: str, record: dict, port: int) -> None:
    names = list(record.get("container_names") or [])
    if CONTAINER_NAME not in names:
        names.append(CONTAINER_NAME)
    protocols = dict(record.get("installed_protocols") or {})
    protocols["telemt"] = {"port": port, "container": CONTAINER_NAME}
    server_store.update_runtime(server_id, container_names=names, installed_protocols=protocols)


def _rollback(ssh) -> None:
    ssh_exec.run(ssh, f"docker rm -f {shlex.quote(CONTAINER_NAME)} 2>/dev/null || true", timeout=60)
    ssh_exec.run(ssh, f"docker rmi {shlex.quote(CONTAINER_NAME)} 2>/dev/null || true", timeout=60)
