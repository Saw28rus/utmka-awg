"""Утилиты SSH для установки протоколов Amnezia на хосте."""

from __future__ import annotations

import base64
import shlex
from pathlib import Path
from typing import Optional

from app.ssh import exec as ssh_exec

DATA_ROOT = Path(__file__).resolve().parent.parent / "data" / "amnezia_scripts"


def load_script(*parts: str) -> str:
    path = DATA_ROOT.joinpath(*parts)
    if not path.is_file():
        raise FileNotFoundError(f"Скрипт не найден: {path}")
    return path.read_text(encoding="utf-8")


def replace_vars(script: str, variables: dict[str, str]) -> str:
    result = script
    for key, value in variables.items():
        result = result.replace(key, value)
    return result


def write_host_file(ssh, path: str, content: str, *, mode: str = "644") -> None:
    b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
    quoted = shlex.quote(path)
    cmd = (
        f"printf '%s' {shlex.quote(b64)} | base64 -d | sudo tee {quoted} >/dev/null "
        f"&& sudo chmod {mode} {quoted}"
    )
    result = ssh_exec.run(ssh, cmd, timeout=60)
    if result.exit_code != 0:
        raise RuntimeError(result.stderr.strip() or f"Не удалось записать файл {path}")


def run_script(ssh, script: str, *, timeout: int = 120) -> ssh_exec.CommandResult:
    b64 = base64.b64encode(script.encode("utf-8")).decode("ascii")
    cmd = f"printf '%s' {shlex.quote(b64)} | base64 -d | sudo bash -s"
    return ssh_exec.run(ssh, cmd, timeout=timeout)


def run_container_script(ssh, container: str, script: str, *, timeout: int = 120) -> ssh_exec.CommandResult:
    b64 = base64.b64encode(script.encode("utf-8")).decode("ascii")
    inner = f"printf '%s' {shlex.quote(b64)} | base64 -d | bash -s"
    cmd = f"sudo docker exec -i {shlex.quote(container)} bash -c {shlex.quote(inner)}"
    return ssh_exec.run(ssh, cmd, timeout=timeout)


def read_container_file(ssh, container: str, path: str) -> str:
    cmd = f"sudo docker exec {shlex.quote(container)} cat {shlex.quote(path)} 2>/dev/null || true"
    return ssh_exec.run(ssh, cmd, timeout=20).stdout.strip()


def docker_available(ssh) -> bool:
    return ssh_exec.run(ssh, "command -v docker >/dev/null 2>&1 && echo yes || echo no").stdout.strip() == "yes"


def container_exists(ssh, name: str) -> bool:
    out = ssh_exec.run(ssh, "docker ps -a --format '{{.Names}}' 2>/dev/null || true").stdout
    return name in {line.strip() for line in out.splitlines() if line.strip()}


def port_busy(ssh, port: int, *, proto: str = "tcp") -> bool:
    script = (
        f"ss -ln{proto[0]} 2>/dev/null | awk '{{print $4}}' | grep -E ':{port}$' >/dev/null && echo busy || "
        f"(command -v lsof >/dev/null 2>&1 && lsof -i {proto}:{port} -s{proto[0:2].upper()}:LISTEN 2>/dev/null | grep -q . && echo busy || echo free)"
    )
    return "busy" in ssh_exec.run(ssh, script, timeout=20).stdout


def detect_xray_arch(ssh) -> str:
    arch = ssh_exec.run(ssh, "uname -m 2>/dev/null || echo x86_64").stdout.strip()
    mapping = {
        "x86_64": "64",
        "amd64": "64",
        "aarch64": "arm64-v8a",
        "arm64": "arm64-v8a",
        "armv7l": "arm32-v7a",
    }
    return mapping.get(arch, "64")


def connect_target(server_id: str):
    from app.services.server_store import server_store

    record = server_store.get_record(server_id)
    target = server_store.ssh_target(server_id)
    if not record or not target:
        raise ValueError("Сервер не найден.")
    ssh = ssh_exec.connect(
        host=target.host,
        port=target.port,
        username=target.username,
        password=target.password,
        key=target.key,
        timeout=15,
    )
    return record, target, ssh


def base_vars(host: str, container_name: str, folder: str) -> dict[str, str]:
    return {
        "$REMOTE_HOST": host,
        "$CONTAINER_NAME": container_name,
        "$DOCKERFILE_FOLDER": folder,
        "$SERVER_IP_ADDRESS": host,
    }
