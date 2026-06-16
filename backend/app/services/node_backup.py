"""Примитивы переноса узла по SSH (NODE_MIGRATION_PLAN.md, фаза 2).

Перенос Postgres (логический дамп), тома panel_data, файла .env и произвольных
путей (сертификаты) между старым и новым сервером. Бинарные артефакты гоняем
через SFTP (без base64), складывая во временные файлы на узлах.

Каждый примитив идемпотентен и сам определяет имена контейнеров (compose даёт
их по шаблону <project>-<service>-1, но мы ищем по подстроке, чтобы не зависеть
от имени проекта).

NB: это ОТДЕЛЬНЫЙ модуль от `panel_backup.py` (тот делает zip-бэкап настроек в UI).
"""

from __future__ import annotations

import io
import logging
import shlex
import subprocess
import tarfile
from pathlib import Path
from typing import Optional

import paramiko

from app.services.panel_role import REMOTE_PANEL_DIR
from app.ssh import exec as ssh_exec

logger = logging.getLogger("utmka.node_backup")

LOCAL_DATA_DIR = Path("/app/data")

DB_NAME = "utmka_awg"
DB_USER = "utmka"

_TMP_PG = "/tmp/utmka_migrate_pg.dump"
_TMP_DATA = "/tmp/utmka_migrate_paneldata.tgz"
_TMP_GENERIC = "/tmp/utmka_migrate_path.tgz"


class NodeBackupError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Низкоуровневое: контейнеры, SFTP, проверка результата
# --------------------------------------------------------------------------- #


def _check(res: ssh_exec.CommandResult, what: str) -> ssh_exec.CommandResult:
    if res.exit_code != 0:
        detail = (res.stderr or res.stdout or "").strip()
        raise NodeBackupError(f"{what}: {detail or f'код {res.exit_code}'}")
    return res


def detect_container(ssh: paramiko.SSHClient, pattern: str) -> str:
    """Имя запущенного контейнера, чьё имя содержит pattern (postgres/backend/...)."""
    res = ssh_exec.run(
        ssh,
        "docker ps --format '{{.Names}}' | grep -m1 " + shlex.quote(pattern) + " || true",
        timeout=20,
    )
    name = (res.stdout or "").strip().splitlines()
    if not name or not name[0].strip():
        raise NodeBackupError(f"Контейнер «{pattern}» не найден на сервере.")
    return name[0].strip()


def _sftp_read(ssh: paramiko.SSHClient, remote_path: str) -> bytes:
    sftp = ssh.open_sftp()
    try:
        with sftp.open(remote_path, "rb") as fh:
            fh.prefetch()
            return fh.read()
    finally:
        sftp.close()


def _sftp_write(ssh: paramiko.SSHClient, remote_path: str, data: bytes, *, mode: Optional[int] = None) -> None:
    sftp = ssh.open_sftp()
    try:
        with sftp.open(remote_path, "wb") as fh:
            fh.write(data)
        if mode is not None:
            sftp.chmod(remote_path, mode)
    finally:
        sftp.close()


def _rm(ssh: paramiko.SSHClient, path: str) -> None:
    try:
        ssh_exec.run(ssh, f"rm -f {shlex.quote(path)}", timeout=15)
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- #
# Postgres (логический дамп / восстановление)
# --------------------------------------------------------------------------- #


def dump_postgres(ssh: paramiko.SSHClient) -> bytes:
    """Снять логический дамп БД панели (custom format). Read-only для сервиса."""
    pg = detect_container(ssh, "postgres")
    _rm(ssh, _TMP_PG)
    _check(
        ssh_exec.run(
            ssh,
            f"docker exec {shlex.quote(pg)} pg_dump -Fc -U {DB_USER} -d {DB_NAME} "
            f"> {shlex.quote(_TMP_PG)}",
            timeout=300,
        ),
        "pg_dump",
    )
    try:
        data = _sftp_read(ssh, _TMP_PG)
    finally:
        _rm(ssh, _TMP_PG)
    if not data:
        raise NodeBackupError("pg_dump вернул пустой дамп.")
    logger.info("pg_dump: %d байт с контейнера %s", len(data), pg)
    return data


def restore_postgres(ssh: paramiko.SSHClient, dump: bytes, *, stop_backend: bool = True) -> None:
    """Восстановить БД из дампа на целевом сервере (drop+create+pg_restore).

    На время восстановления backend останавливается (release connections),
    затем поднимается обратно.
    """
    if not dump:
        raise NodeBackupError("Пустой дамп — нечего восстанавливать.")
    pg = detect_container(ssh, "postgres")
    backend = None
    if stop_backend:
        try:
            backend = detect_container(ssh, "backend")
        except NodeBackupError:
            backend = None

    _sftp_write(ssh, _TMP_PG, dump)
    try:
        if backend:
            ssh_exec.run(ssh, f"docker stop {shlex.quote(backend)}", timeout=60)

        # Разорвать активные соединения и пересоздать БД.
        ssh_exec.run(
            ssh,
            f"docker exec {shlex.quote(pg)} psql -U {DB_USER} -d postgres -c "
            + shlex.quote(
                f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                f"WHERE datname='{DB_NAME}' AND pid<>pg_backend_pid();"
            ),
            timeout=60,
        )
        _check(
            ssh_exec.run(
                ssh,
                f"docker exec {shlex.quote(pg)} psql -U {DB_USER} -d postgres -c "
                + shlex.quote(f"DROP DATABASE IF EXISTS {DB_NAME};"),
                timeout=60,
            ),
            "drop database",
        )
        _check(
            ssh_exec.run(
                ssh,
                f"docker exec {shlex.quote(pg)} psql -U {DB_USER} -d postgres -c "
                + shlex.quote(f"CREATE DATABASE {DB_NAME} OWNER {DB_USER};"),
                timeout=60,
            ),
            "create database",
        )
        # pg_restore может вернуть !=0 на некритичных warning'ах — проверяем фатальные.
        res = ssh_exec.run(
            ssh,
            f"cat {shlex.quote(_TMP_PG)} | docker exec -i {shlex.quote(pg)} "
            f"pg_restore -U {DB_USER} -d {DB_NAME} --no-owner --no-acl",
            timeout=300,
        )
        if res.exit_code != 0 and "error" in (res.stderr or "").lower():
            # отличаем фатальную ошибку от warning'ов
            fatal = [ln for ln in res.stderr.splitlines() if "error:" in ln.lower()]
            if fatal:
                raise NodeBackupError("pg_restore: " + "; ".join(fatal[:3]))
    finally:
        if backend:
            ssh_exec.run(ssh, f"docker start {shlex.quote(backend)}", timeout=60)
        _rm(ssh, _TMP_PG)
    logger.info("pg_restore: БД %s восстановлена на %s", DB_NAME, pg)


# --------------------------------------------------------------------------- #
# panel_data (том JSON-стораджей: servers, clients, cascade, …)
# --------------------------------------------------------------------------- #


def dump_panel_data(ssh: paramiko.SSHClient) -> bytes:
    """tar тома panel_data (через backend-контейнер: /app/data)."""
    backend = detect_container(ssh, "backend")
    _rm(ssh, _TMP_DATA)
    _check(
        ssh_exec.run(
            ssh,
            f"docker exec {shlex.quote(backend)} sh -c 'cd /app/data && tar czf - .' "
            f"> {shlex.quote(_TMP_DATA)}",
            timeout=120,
        ),
        "tar panel_data",
    )
    try:
        data = _sftp_read(ssh, _TMP_DATA)
    finally:
        _rm(ssh, _TMP_DATA)
    logger.info("panel_data: %d байт", len(data))
    return data


def restore_panel_data(ssh: paramiko.SSHClient, data: bytes) -> None:
    """Распаковать panel_data на целевом backend. PANEL_ROLE в томе не хранится."""
    if not data:
        raise NodeBackupError("Пустой архив panel_data.")
    backend = detect_container(ssh, "backend")
    _sftp_write(ssh, _TMP_DATA, data)
    try:
        _check(
            ssh_exec.run(
                ssh,
                f"cat {shlex.quote(_TMP_DATA)} | docker exec -i {shlex.quote(backend)} "
                f"sh -c 'mkdir -p /app/data && tar xzf - -C /app/data'",
                timeout=120,
            ),
            "untar panel_data",
        )
    finally:
        _rm(ssh, _TMP_DATA)
    logger.info("panel_data восстановлен на %s", backend)


# --------------------------------------------------------------------------- #
# .env (секретный ключ Fernet, пароль БД и пр.)
# --------------------------------------------------------------------------- #


def read_env(ssh: paramiko.SSHClient) -> str:
    """Прочитать .env панели с узла."""
    path = f"{REMOTE_PANEL_DIR}/.env"
    try:
        return _sftp_read(ssh, path).decode("utf-8", errors="replace")
    except FileNotFoundError as exc:  # noqa: BLE001
        raise NodeBackupError(f".env не найден на сервере ({path}).") from exc


def write_env(ssh: paramiko.SSHClient, content: str) -> None:
    """Записать .env панели на узел (0600)."""
    path = f"{REMOTE_PANEL_DIR}/.env"
    ssh_exec.run(ssh, f"mkdir -p {shlex.quote(REMOTE_PANEL_DIR)}", timeout=15)
    _sftp_write(ssh, path, content.encode("utf-8"), mode=0o600)


def env_value(content: str, key: str) -> Optional[str]:
    """Достать значение KEY=value из текста .env."""
    for line in content.splitlines():
        line = line.strip()
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return None


# --------------------------------------------------------------------------- #
# Локальные дампы (источник = ЭТА панель, без SSH; backend выполняется на узле)
# --------------------------------------------------------------------------- #


def _local_docker_bin() -> str:
    from app.services.panel_update import resolve_docker_bin

    docker = resolve_docker_bin()
    if not docker:
        raise NodeBackupError("docker CLI недоступен в backend-контейнере.")
    return docker


def _local_container(pattern: str) -> str:
    docker = _local_docker_bin()
    res = subprocess.run(
        [docker, "ps", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    for name in res.stdout.splitlines():
        if pattern in name:
            return name.strip()
    raise NodeBackupError(f"Локальный контейнер «{pattern}» не найден.")


def local_dump_postgres() -> bytes:
    """pg_dump БД этой панели через локальный docker (источник миграции)."""
    docker = _local_docker_bin()
    pg = _local_container("postgres")
    res = subprocess.run(
        [docker, "exec", pg, "pg_dump", "-Fc", "-U", DB_USER, "-d", DB_NAME],
        capture_output=True,
        timeout=300,
    )
    if res.returncode != 0:
        raise NodeBackupError("local pg_dump: " + res.stderr.decode("utf-8", "replace")[-500:])
    if not res.stdout:
        raise NodeBackupError("local pg_dump вернул пустой дамп.")
    logger.info("local pg_dump: %d байт", len(res.stdout))
    return res.stdout


def local_dump_panel_data() -> bytes:
    """tar.gz каталога /app/data этой панели (источник миграции).

    PANEL_ROLE НЕ попадает (он на хосте, вне тома) — целевой узел сам задаёт роль.
    """
    if not LOCAL_DATA_DIR.exists():
        raise NodeBackupError(f"Каталог данных {LOCAL_DATA_DIR} не найден.")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for item in sorted(LOCAL_DATA_DIR.iterdir()):
            # пропускаем временные/лог-файлы апдейтера
            if item.name in {"update.lock", "update.log", "update_state.json"}:
                continue
            tar.add(str(item), arcname=item.name)
    data = buf.getvalue()
    logger.info("local panel_data: %d байт", len(data))
    return data


# --------------------------------------------------------------------------- #
# Произвольный путь (сертификаты Let's Encrypt, nginx-конфиги)
# --------------------------------------------------------------------------- #


def copy_path(source_ssh: paramiko.SSHClient, target_ssh: paramiko.SSHClient, path: str) -> bool:
    """Перенести каталог/файл `path` (абсолютный) со старого узла на новый as-is.

    Возвращает False, если на источнике пути нет (не критично — пропускаем).
    """
    path = path.rstrip("/")
    check = ssh_exec.run(source_ssh, f"test -e {shlex.quote(path)} && echo Y || echo N", timeout=15)
    if "Y" not in check.stdout:
        return False

    # tar относительно / — чтобы распаковать на целевом в то же место.
    rel = path.lstrip("/")
    _rm(source_ssh, _TMP_GENERIC)
    _check(
        ssh_exec.run(
            source_ssh,
            f"tar czf {shlex.quote(_TMP_GENERIC)} -C / {shlex.quote(rel)}",
            timeout=120,
        ),
        f"tar {path}",
    )
    try:
        blob = _sftp_read(source_ssh, _TMP_GENERIC)
    finally:
        _rm(source_ssh, _TMP_GENERIC)

    _sftp_write(target_ssh, _TMP_GENERIC, blob)
    try:
        _check(
            ssh_exec.run(target_ssh, f"tar xzf {shlex.quote(_TMP_GENERIC)} -C /", timeout=120),
            f"untar {path}",
        )
    finally:
        _rm(target_ssh, _TMP_GENERIC)
    logger.info("copied %s (%d байт)", path, len(blob))
    return True
