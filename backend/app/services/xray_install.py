"""Установка Xray (VLESS-Reality) на сервер по сценарию Amnezia."""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Optional

from app.services.amnezia_ssh import (
    base_vars,
    connect_target,
    container_exists,
    detect_xray_arch,
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

CONTAINER_NAME = "amnezia-xray"
DOCKER_FOLDER = "/opt/amnezia/amnezia-xray"
DEFAULT_SITE = "www.googletagmanager.com"
DEFAULT_PORT = 443
DEFAULT_TRANSPORT = "tcp"
SUPPORTED_TRANSPORTS = ("tcp", "grpc", "xhttp")


@dataclass
class XrayInstallResult:
    message: str
    container: str
    port: int
    site_name: str
    client_uuid: Optional[str] = None
    public_key: Optional[str] = None
    short_id: Optional[str] = None
    transport: str = DEFAULT_TRANSPORT


class XrayInstallError(Exception):
    pass


def install_xray(
    server_id: str,
    *,
    port: int = DEFAULT_PORT,
    site_name: Optional[str] = None,
    transport: str = DEFAULT_TRANSPORT,
) -> XrayInstallResult:
    site = (site_name or DEFAULT_SITE).strip()
    if not site:
        raise XrayInstallError("Укажи домен маскировки Reality (SNI).")
    transport = (transport or DEFAULT_TRANSPORT).lower()
    if transport not in SUPPORTED_TRANSPORTS:
        raise XrayInstallError(
            f"Неподдерживаемый транспорт: {transport}. Доступно: {', '.join(SUPPORTED_TRANSPORTS)}."
        )

    record, target, ssh = connect_target(server_id)
    try:
        if container_exists(ssh, CONTAINER_NAME):
            raise XrayInstallError("Xray уже установлен на сервере (контейнер amnezia-xray).")

        if not docker_available(ssh):
            _ensure_docker(ssh, target.host)

        if port_busy(ssh, port, proto="tcp"):
            raise XrayInstallError(f"TCP-порт {port} уже занят на сервере.")

        vars_map = _build_vars(target.host, port, site, transport)
        _prepare_host(ssh, vars_map)
        _upload_dockerfile(ssh, vars_map)
        _build_image(ssh, vars_map)
        _run_container(ssh, vars_map)
        _configure_container(ssh, vars_map)
        _startup_container(ssh, vars_map)
        _verify_running(ssh)

        creds = _read_credentials(ssh)
        _register_container(server_id, record, port, transport)

        return XrayInstallResult(
            message="Xray (VLESS-Reality) установлен. Подключайся через клиент AmneziaVPN.",
            container=CONTAINER_NAME,
            port=port,
            site_name=site,
            client_uuid=creds.get("uuid"),
            public_key=creds.get("public_key"),
            short_id=creds.get("short_id"),
            transport=transport,
        )
    except XrayInstallError as exc:
        if "уже установлен" not in str(exc).lower():
            _rollback(ssh)
        raise
    except Exception as exc:
        _rollback(ssh)
        raise XrayInstallError(str(exc)) from exc
    finally:
        ssh.close()


def _build_vars(host: str, port: int, site: str, transport: str = DEFAULT_TRANSPORT) -> dict[str, str]:
    import secrets

    vars_map = base_vars(host, CONTAINER_NAME, DOCKER_FOLDER)
    vars_map["$XRAY_SERVER_PORT"] = str(port)
    vars_map["$XRAY_SITE_NAME"] = site
    vars_map["$XRAY_NETWORK"] = transport

    # flow (xtls-rprx-vision) валиден только для tcp/raw.
    if transport in ("tcp", "raw"):
        vars_map["$XRAY_FLOW_FIELD"] = ',\n                        "flow": "xtls-rprx-vision"'
    else:
        vars_map["$XRAY_FLOW_FIELD"] = ""

    if transport == "grpc":
        service_name = secrets.token_hex(6)
        vars_map["$XRAY_TRANSPORT_BLOCK"] = (
            '\n                "grpcSettings": {\n'
            f'                    "serviceName": "{service_name}",\n'
            '                    "multiMode": false\n'
            '                },'
        )
    elif transport == "xhttp":
        path = "/" + secrets.token_hex(4)
        vars_map["$XRAY_TRANSPORT_BLOCK"] = (
            '\n                "xhttpSettings": {\n'
            f'                    "path": "{path}"\n'
            '                },'
        )
    else:
        vars_map["$XRAY_TRANSPORT_BLOCK"] = ""
    return vars_map


def _ensure_docker(ssh, host: str) -> None:
    script = replace_vars(load_script("shared", "install_docker.sh"), base_vars(host, CONTAINER_NAME, DOCKER_FOLDER))
    result = run_script(ssh, script, timeout=300)
    if result.exit_code != 0 or "command not found" in result.stdout.lower():
        raise XrayInstallError("Docker не установлен и автоматическая установка не удалась.")
    if not docker_available(ssh):
        raise XrayInstallError("Docker недоступен после установки.")


def _prepare_host(ssh, vars_map: dict[str, str]) -> None:
    script = replace_vars(load_script("shared", "prepare_host.sh"), vars_map)
    result = run_script(ssh, script, timeout=60)
    if result.exit_code != 0:
        raise XrayInstallError(f"Подготовка хоста не удалась: {result.stderr.strip() or result.stdout.strip()}")


def _upload_dockerfile(ssh, vars_map: dict[str, str]) -> None:
    dockerfile = load_script("xray", "Dockerfile")
    arch = detect_xray_arch(ssh)
    dockerfile = dockerfile.replace("Xray-linux-64.zip", f"Xray-linux-{arch}.zip")
    docker_path = f"{DOCKER_FOLDER}/Dockerfile"
    write_host_file(ssh, docker_path, dockerfile, mode="700")


def _build_image(ssh, vars_map: dict[str, str]) -> None:
    script = replace_vars(load_script("shared", "build_container.sh"), vars_map)
    result = run_script(ssh, script, timeout=600)
    combined = f"{result.stdout}\n{result.stderr}"
    if "pull rate limit" in combined.lower():
        raise XrayInstallError("Docker Hub ограничил скачивание образов. Повтори позже.")
    if result.exit_code != 0:
        raise XrayInstallError(f"Сборка образа Xray не удалась: {combined.strip()[-500:]}")


def _run_container(ssh, vars_map: dict[str, str]) -> None:
    script = replace_vars(load_script("xray", "run_container.sh"), vars_map)
    result = run_script(ssh, script, timeout=120)
    combined = f"{result.stdout}\n{result.stderr}"
    if "address already in use" in combined or "is already in use by container" in combined:
        raise XrayInstallError("Порт уже занят другим контейнером.")
    if result.exit_code != 0:
        raise XrayInstallError(f"Запуск контейнера не удался: {combined.strip()[-500:]}")


def _configure_container(ssh, vars_map: dict[str, str]) -> None:
    script = replace_vars(load_script("xray", "configure_container.sh"), vars_map)
    result = run_container_script(ssh, CONTAINER_NAME, script, timeout=120)
    if result.exit_code != 0:
        raise XrayInstallError(f"Настройка Xray внутри контейнера не удалась: {result.stderr.strip() or result.stdout.strip()}")


def _startup_container(ssh, vars_map: dict[str, str]) -> None:
    start_script = replace_vars(load_script("xray", "start.sh"), vars_map)
    b64_path = "/tmp/utmka_xray_start.sh"
    write_host_file(ssh, b64_path, start_script, mode="755")
    copy_cmd = (
        f"sudo docker cp {shlex.quote(b64_path)} {shlex.quote(CONTAINER_NAME)}:/opt/amnezia/start.sh "
        f"&& sudo docker exec {shlex.quote(CONTAINER_NAME)} chmod a+x /opt/amnezia/start.sh "
        f"&& sudo docker exec -d {shlex.quote(CONTAINER_NAME)} /opt/amnezia/start.sh "
        f"&& sudo rm -f {shlex.quote(b64_path)}"
    )
    result = ssh_exec.run(ssh, copy_cmd, timeout=60)
    if result.exit_code != 0:
        raise XrayInstallError(f"Не удалось запустить Xray в контейнере: {result.stderr.strip()}")


def _verify_running(ssh) -> None:
    status = ssh_exec.run(
        ssh,
        f"docker inspect -f '{{{{.State.Status}}}}' {shlex.quote(CONTAINER_NAME)} 2>/dev/null || true",
    ).stdout.strip()
    if status != "running":
        raise XrayInstallError("Контейнер amnezia-xray не перешёл в состояние running.")

    proc = ssh_exec.run(
        ssh,
        f"docker exec {shlex.quote(CONTAINER_NAME)} sh -c 'pgrep -x xray >/dev/null && echo ok || echo fail' 2>/dev/null || true",
        timeout=20,
    ).stdout.strip()
    if proc != "ok":
        logs = ssh_exec.run(ssh, f"docker logs --tail 30 {shlex.quote(CONTAINER_NAME)} 2>&1", timeout=20).stdout
        raise XrayInstallError(f"Процесс xray не запустился. Логи: {logs.strip()[-400:]}")


def _read_credentials(ssh) -> dict[str, str]:
    uuid_val = read_container_file(ssh, CONTAINER_NAME, "/opt/amnezia/xray/xray_uuid.key")
    public_key = read_container_file(ssh, CONTAINER_NAME, "/opt/amnezia/xray/xray_public.key")
    short_id = read_container_file(ssh, CONTAINER_NAME, "/opt/amnezia/xray/xray_short_id.key")
    return {"uuid": uuid_val or None, "public_key": public_key or None, "short_id": short_id or None}


def _register_container(server_id: str, record: dict, port: int, transport: str = DEFAULT_TRANSPORT) -> None:
    names = list(record.get("container_names") or [])
    if CONTAINER_NAME not in names:
        names.append(CONTAINER_NAME)
    protocols = dict(record.get("installed_protocols") or {})
    protocols["xray"] = {"port": port, "container": CONTAINER_NAME, "transport": transport}
    server_store.update_runtime(server_id, container_names=names, installed_protocols=protocols)


def _rollback(ssh) -> None:
    ssh_exec.run(ssh, f"docker rm -f {shlex.quote(CONTAINER_NAME)} 2>/dev/null || true", timeout=60)
    ssh_exec.run(ssh, f"docker rmi {shlex.quote(CONTAINER_NAME)} 2>/dev/null || true", timeout=60)
