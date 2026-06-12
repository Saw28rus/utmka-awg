import io
import re
import shlex
from dataclasses import dataclass
from typing import Optional

import paramiko

from app.schemas.servers import DetectCheck, DetectPreviewRequest, DetectResult


CONFIG_PATHS = (
    "/opt/amnezia/awg/wg0.conf",
    "/opt/amnezia/awg/awg0.conf",
    "/etc/amnezia/amneziawg/awg0.conf",
    "/etc/amnezia/amneziawg/wg0.conf",
    "/etc/wireguard/wg0.conf",
    "/etc/wireguard/awg0.conf",
)


@dataclass
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str


def run_awg_detect(payload: DetectPreviewRequest) -> DetectResult:
    checks: list[DetectCheck] = []

    try:
        ssh = _connect(payload)
    except Exception as exc:
        return DetectResult(
            confidence="error",
            branch="needs_review",
            checks=[
                DetectCheck(
                    key="ssh",
                    label="SSH",
                    status="error",
                    message=f"Не удалось подключиться: {exc}",
                )
            ],
            message="SSH не отвечает или доступ не подходит. Проверь IP, порт, пользователя и пароль.",
        )

    try:
        checks.append(
            DetectCheck(
                key="ssh",
                label="SSH",
                status="ok",
                message="Сервер отвечает, авторизация прошла.",
            )
        )

        os_result = _run(ssh, "uname -srm; (lsb_release -ds 2>/dev/null || . /etc/os-release 2>/dev/null && echo \"$PRETTY_NAME\" || true)")
        os_release = " ".join(line.strip() for line in os_result.stdout.splitlines() if line.strip())
        checks.append(
            DetectCheck(
                key="os",
                label="ОС",
                status="ok" if os_release else "warning",
                message=os_release or "Не удалось определить ОС.",
            )
        )

        docker_result = _run(ssh, "command -v docker >/dev/null 2>&1 && echo yes || echo no")
        docker_available = "yes" in docker_result.stdout
        checks.append(
            DetectCheck(
                key="docker",
                label="Docker",
                status="ok" if docker_available else "warning",
                message="Docker найден." if docker_available else "Docker не найден. Для установки с нуля панель сможет поставить его позже.",
            )
        )

        container_names = _container_names(ssh) if docker_available else []
        checks.append(
            DetectCheck(
                key="containers",
                label="Контейнеры",
                status="ok" if container_names else "warning",
                message=", ".join(container_names) if container_names else "Контейнеры Amnezia/AWG не найдены.",
            )
        )

        config_path, config_source, config_text = _locate_config(ssh, container_names)
        peers_count = len(re.findall(r"(?im)^\s*\[Peer\]\s*$", config_text))
        if config_source and config_source != "host":
            live_peers = _count_live_peers(ssh, config_source)
            peers_count = max(peers_count, live_peers)
        has_interface = bool(re.search(r"(?im)^\s*\[Interface\]\s*$", config_text))
        has_awg_params = bool(
            re.search(r"(?im)^\s*(Jc|Jmin|Jmax|S[1-4]|H[1-4]|I[1-5])\s*=", config_text)
        )
        looks_like_amnezia = any("amnezia" in name.lower() or "awg" in name.lower() for name in container_names)
        awg2_detected = bool(config_path and has_interface and (has_awg_params or looks_like_amnezia))

        if config_source and config_source != "host":
            config_message = f"{config_path} (в контейнере {config_source})"
        elif config_path:
            config_message = config_path
        else:
            config_message = "Файл awg0.conf не найден ни на хосте, ни в контейнерах Amnezia."

        checks.append(
            DetectCheck(
                key="config",
                label="Конфиг AWG",
                status="ok" if config_path else "warning",
                message=config_message,
            )
        )
        checks.append(
            DetectCheck(
                key="peers",
                label="Клиенты",
                status="ok" if peers_count else ("warning" if config_path else "neutral"),
                message=f"Найдено peers: {peers_count}" if config_path else "Клиентов пока нет или AWG2 не установлен.",
            )
        )

        if awg2_detected:
            return DetectResult(
                confidence="high" if has_awg_params else "medium",
                branch="import",
                checks=checks,
                message="AWG2/Amnezia найдены. Можно подключить существующую конфигурацию без смены ключей.",
                awg2_detected=True,
                config_path=config_path,
                peers_count=peers_count,
                container_names=container_names,
                docker_available=docker_available,
                os_release=os_release or None,
            )

        if config_path and has_interface:
            return DetectResult(
                confidence="medium",
                branch="needs_review",
                checks=checks,
                message="Найден похожий WireGuard/AWG конфиг, но AWG2-параметры не подтверждены. Нужна ручная проверка.",
                awg2_detected=False,
                config_path=config_path,
                peers_count=peers_count,
                container_names=container_names,
                docker_available=docker_available,
                os_release=os_release or None,
            )

        return DetectResult(
            confidence="high",
            branch="install",
            checks=checks,
            message="AWG2/Amnezia не найдены. Сервер можно готовить к установке с нуля.",
            awg2_detected=False,
            config_path=None,
            peers_count=0,
            container_names=container_names,
            docker_available=docker_available,
            os_release=os_release or None,
        )
    finally:
        ssh.close()


def _connect(payload: DetectPreviewRequest) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    pkey = _load_private_key(payload.ssh_key) if payload.ssh_key else None
    client.connect(
        hostname=payload.host,
        port=payload.ssh_port,
        username=payload.ssh_username,
        password=payload.ssh_password or None,
        pkey=pkey,
        timeout=10,
        banner_timeout=10,
        auth_timeout=10,
        look_for_keys=False,
        allow_agent=False,
    )
    return client


def _load_private_key(raw_key: str):
    key_stream = io.StringIO(raw_key)
    loaders = (
        paramiko.Ed25519Key.from_private_key,
        paramiko.RSAKey.from_private_key,
        paramiko.ECDSAKey.from_private_key,
    )
    last_error: Optional[Exception] = None
    for loader in loaders:
        key_stream.seek(0)
        try:
            return loader(key_stream)
        except Exception as exc:
            last_error = exc
    raise ValueError(f"SSH-ключ не удалось прочитать: {last_error}")


def _run(ssh: paramiko.SSHClient, command: str, timeout: int = 12) -> CommandResult:
    stdin, stdout, stderr = ssh.exec_command(command, timeout=timeout)
    stdin.close()
    exit_code = stdout.channel.recv_exit_status()
    return CommandResult(
        exit_code=exit_code,
        stdout=stdout.read().decode("utf-8", errors="replace"),
        stderr=stderr.read().decode("utf-8", errors="replace"),
    )


def _container_names(ssh: paramiko.SSHClient) -> list[str]:
    result = _run(
        ssh,
        "docker ps -a --format '{{.Names}}' 2>/dev/null | grep -Ei 'amnezia|awg|wireguard' || true",
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _locate_config(
    ssh: paramiko.SSHClient, container_names: list[str]
) -> tuple[Optional[str], Optional[str], str]:
    """Ищем awg0.conf сначала на хосте, затем внутри контейнеров Amnezia/AWG."""
    host_path = _find_config_path(ssh)
    if host_path:
        text = _read_config(ssh, host_path)
        if text.strip():
            return host_path, "host", text

    for container in container_names:
        container_path = _find_config_in_container(ssh, container)
        if container_path:
            text = _read_config_in_container(ssh, container, container_path)
            if text.strip():
                return container_path, container, text

    return None, None, ""


def _find_config_path(ssh: paramiko.SSHClient) -> Optional[str]:
    checks = " ".join(shlex.quote(path) for path in CONFIG_PATHS)
    result = _run(ssh, f"for p in {checks}; do [ -f \"$p\" ] && echo \"$p\"; done")
    paths = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return paths[0] if paths else None


def _read_config(ssh: paramiko.SSHClient, path: Optional[str]) -> str:
    if not path:
        return ""
    result = _run(ssh, f"cat {shlex.quote(path)} 2>/dev/null || true")
    return result.stdout


def _find_config_in_container(ssh: paramiko.SSHClient, container: str) -> Optional[str]:
    inner = "for p in " + " ".join(shlex.quote(path) for path in CONFIG_PATHS) + "; do [ -f \"$p\" ] && echo \"$p\"; done"
    cmd = f"docker exec {shlex.quote(container)} sh -c {shlex.quote(inner)} 2>/dev/null || true"
    result = _run(ssh, cmd)
    paths = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return paths[0] if paths else None


def _read_config_in_container(ssh: paramiko.SSHClient, container: str, path: Optional[str]) -> str:
    if not path:
        return ""
    cmd = f"docker exec {shlex.quote(container)} cat {shlex.quote(path)} 2>/dev/null || true"
    result = _run(ssh, cmd)
    return result.stdout


def _count_live_peers(ssh: paramiko.SSHClient, container: str) -> int:
    """Считаем подключенных peers по живому интерфейсу (awg/wg show), если он поднят."""
    inner = "awg show all peers 2>/dev/null || wg show all peers 2>/dev/null || true"
    cmd = f"docker exec {shlex.quote(container)} sh -c {shlex.quote(inner)} 2>/dev/null || true"
    result = _run(ssh, cmd)
    return len([line for line in result.stdout.splitlines() if line.strip()])
