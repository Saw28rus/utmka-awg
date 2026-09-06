import base64
import hashlib
import io
import time
from dataclasses import dataclass
from typing import Optional

import paramiko


class SSHTimeoutError(TimeoutError):
    """SSH-команда не завершилась за отведённое время (узел завис / связь оборвалась)."""


class HostKeyMismatchError(Exception):
    """Ключ SSH-сервера не совпал с сохранённым отпечатком."""

    def __init__(self, host: str, expected: str, seen: str):
        self.host = host
        self.expected = expected
        self.seen = seen
        super().__init__(
            f"SSH-ключ сервера {host} изменился. Было {expected}, стало {seen}. "
            "Это может быть переустановка ОС — или чужой узел. "
            "Подтвердите новый ключ на вкладке «Безопасность»."
        )


@dataclass
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str


def key_fingerprint(key: paramiko.PKey) -> str:
    digest = hashlib.sha256(key.asbytes()).digest()
    b64 = base64.b64encode(digest).decode("ascii").rstrip("=")
    return f"SHA256:{b64}"


def fingerprints_equal(left: Optional[str], right: Optional[str]) -> bool:
    if not left or not right:
        return False
    return left.strip() == right.strip()


def seen_fingerprint(client: paramiko.SSHClient) -> Optional[str]:
    stored = getattr(client, "_utmka_hostkey_fp", None)
    if stored:
        return stored
    transport = client.get_transport()
    if transport is None:
        return None
    key = transport.get_remote_server_key()
    return key_fingerprint(key) if key else None


class _FingerprintPolicy(paramiko.MissingHostKeyPolicy):
    def __init__(self, expected: Optional[str] = None) -> None:
        self.expected = expected
        self.seen: Optional[str] = None

    def missing_host_key(self, client, hostname, key) -> None:  # noqa: ANN001
        self.seen = key_fingerprint(key)
        if self.expected and not fingerprints_equal(self.expected, self.seen):
            raise HostKeyMismatchError(hostname, self.expected, self.seen)
        client.get_host_keys().add(hostname, key.get_name(), key)


def _lookup_stored_fp(host: str, port: int) -> Optional[str]:
    try:
        from app.services.server_store import server_store

        return server_store.hostkey_fp_for(host, port)
    except Exception:  # noqa: BLE001
        return None


def _remember_stored_fp(host: str, port: int, fingerprint: str) -> None:
    try:
        from app.services.server_store import server_store

        server_store.remember_hostkey_fp(host, port, fingerprint)
    except Exception:  # noqa: BLE001
        pass


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
    expected_fingerprint: Optional[str] = None,
) -> paramiko.SSHClient:
    expected = expected_fingerprint if expected_fingerprint else _lookup_stored_fp(host, port)
    policy = _FingerprintPolicy(expected)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(policy)
    pkey = load_private_key(key) if key else None
    try:
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
    except HostKeyMismatchError:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass
        raise
    seen = policy.seen or seen_fingerprint(client)
    if seen:
        client._utmka_hostkey_fp = seen  # noqa: SLF001
        _remember_stored_fp(host, port, seen)
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
