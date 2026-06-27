import io
import time
from dataclasses import dataclass
from typing import Optional

import paramiko


class SSHTimeoutError(TimeoutError):
    """SSH-команда не завершилась за отведённое время (узел завис / связь оборвалась)."""


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
    """Выполнить команду по SSH с ЖЁСТКИМ ограничением по времени.

    Важно: `paramiko` `recv_exit_status()` блокируется БЕЗ учёта таймаута, поэтому
    зависший/недоступный узел раньше вешал поток навсегда (см. инцидент с миграцией).
    Здесь мы:
      * включаем keepalive (детект «полуживого» TCP);
      * вычитываем stdout/stderr на лету (иначе буфер канала переполнится и команда
        с большим выводом «зависнет» в ожидании чтения);
      * выходим по дедлайну (wall-clock), закрывая канал и бросая SSHTimeoutError.
    """
    transport = ssh.get_transport()
    if transport is None:
        raise paramiko.SSHException("SSH-транспорт закрыт.")
    try:
        transport.set_keepalive(15)
    except Exception:  # noqa: BLE001
        pass

    try:
        chan = transport.open_session(timeout=min(timeout, 30))
    except TypeError:
        chan = transport.open_session()
    chan.settimeout(0.0)  # неблокирующее чтение; читаем только когда *_ready()
    chan.exec_command(command)

    out = bytearray()
    err = bytearray()
    deadline = time.monotonic() + max(timeout, 1)

    def _drain() -> None:
        while chan.recv_ready():
            data = chan.recv(65536)
            if not data:
                break
            out.extend(data)
        while chan.recv_stderr_ready():
            data = chan.recv_stderr(65536)
            if not data:
                break
            err.extend(data)

    while True:
        _drain()
        if chan.exit_status_ready():
            _drain()
            break
        if time.monotonic() > deadline:
            try:
                chan.close()
            except Exception:  # noqa: BLE001
                pass
            raise SSHTimeoutError(
                f"Команда не завершилась за {timeout}с — узел не отвечает (завис или нет связи)."
            )
        time.sleep(0.2)

    exit_code = chan.recv_exit_status()
    try:
        chan.close()
    except Exception:  # noqa: BLE001
        pass
    return CommandResult(
        exit_code=exit_code,
        stdout=out.decode("utf-8", errors="replace"),
        stderr=err.decode("utf-8", errors="replace"),
    )
