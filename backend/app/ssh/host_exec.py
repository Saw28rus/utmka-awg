"""Команды на хосте панели из backend-контейнера (без SSH).

Backend уже имеет docker.sock. Привилегированный sibling + nsenter в PID 1
даёт root на Ubuntu-хосте — nginx/certbot/iptables ставятся там же, где крутится
`install-panel.sh`, а не на VPN-узлах.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from app.ssh.exec import CommandResult


class LocalHostSession:
    """Маркер вместо paramiko.SSHClient: ssh_exec.run уходит в run_host()."""

    _utmka_local_host = True

    def close(self) -> None:
        return None


class HostExecError(RuntimeError):
    pass


def _docker_bin() -> str:
    return os.getenv("DOCKER", os.getenv("DOCKER_BIN", "docker"))


def _nsenter_host_path() -> str:
    # docker run -v интерпретирует путь на ХОСТЕ, не в контейнере.
    return os.getenv("UTMKA_HOST_NSENTER", "/usr/bin/nsenter")


def _self_image() -> str:
    env = (os.getenv("UTMKA_HOST_EXEC_IMAGE") or "").strip()
    if env:
        return env
    docker = _docker_bin()
    names = [os.getenv("HOSTNAME", ""), "backend"]
    for name in names:
        if not name:
            continue
        try:
            proc = subprocess.run(
                [docker, "inspect", "-f", "{{.Config.Image}}", name],
                capture_output=True,
                text=True,
                timeout=8,
            )
        except Exception:  # noqa: BLE001
            continue
        img = (proc.stdout or "").strip()
        if proc.returncode == 0 and img:
            return img
    return (os.getenv("UTMKA_HOST_EXEC_FALLBACK_IMAGE") or "python:3.12-slim").strip()


def run_host(command: str, timeout: int = 30) -> CommandResult:
    """Выполнить shell-команду в namespaces хоста (как root)."""
    docker = _docker_bin()
    nsenter = _nsenter_host_path()
    env_image = (os.getenv("UTMKA_HOST_EXEC_IMAGE") or "").strip()
    image = _self_image()
    fallback = os.getenv("UTMKA_HOST_EXEC_FALLBACK_IMAGE") or "python:3.12-slim"
    pull_never = not env_image and image != fallback
    cmd = [
        docker,
        "run",
        "--rm",
        "--privileged",
        "--pid=host",
        "--network=host",
        "--ipc=host",
    ]
    if pull_never:
        cmd.extend(["--pull", "never"])
    cmd.extend(
        [
            "-v",
            f"{nsenter}:/utmka-nsenter:ro",
            image,
            "/utmka-nsenter",
            "-t",
            "1",
            "-m",
            "-u",
            "-i",
            "-n",
            "--",
            "bash",
            "-lc",
            command,
        ]
    )
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=max(timeout, 5),
        )
    except FileNotFoundError as exc:
        raise HostExecError("Docker CLI недоступен в контейнере панели.") from exc
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        err = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
        return CommandResult(exit_code=124, stdout=out, stderr=err or f"таймаут {timeout}с")
    return CommandResult(
        exit_code=proc.returncode,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
    )


def host_exec_available() -> bool:
    if not shutil.which(_docker_bin()) and not Path(_docker_bin()).is_file():
        return False
    sock = Path("/var/run/docker.sock")
    return sock.exists()
