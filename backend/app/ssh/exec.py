import io
from dataclasses import dataclass
from typing import Optional

import paramiko


@dataclass
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str


def load_private_key(raw_key: str):
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
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    raise ValueError(f"SSH-ключ не удалось прочитать: {last_error}")


def connect(
    host: str,
    port: int,
    username: str,
    password: Optional[str] = None,
    key: Optional[str] = None,
    timeout: int = 10,
) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    pkey = load_private_key(key) if key else None
    client.connect(
        hostname=host,
        port=port,
        username=username,
        password=password or None,
        pkey=pkey,
        timeout=timeout,
        banner_timeout=timeout,
        auth_timeout=timeout,
        look_for_keys=False,
        allow_agent=False,
    )
    return client


def run(ssh: paramiko.SSHClient, command: str, timeout: int = 20) -> CommandResult:
    stdin, stdout, stderr = ssh.exec_command(command, timeout=timeout)
    stdin.close()
    exit_code = stdout.channel.recv_exit_status()
    return CommandResult(
        exit_code=exit_code,
        stdout=stdout.read().decode("utf-8", errors="replace"),
        stderr=stderr.read().decode("utf-8", errors="replace"),
    )
