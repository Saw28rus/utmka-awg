"""Провижининг панели на удалённом узле по SSH (NODE_MIGRATION_PLAN, фаза 3).

Поднимает на чистом VPS рабочий стек панели (Docker + clone + compose),
управляет ролью узла (active/standby) и .env, делает health-проверку. Сами
данные (БД/panel_data/секрет/сертификаты) переносит panel_backup.py.

Важно: PANEL_SECRET_KEY (Fernet) ОБЯЗАН совпадать со старой панелью, иначе
зашифрованные блобы (SSH-креды серверов, ключи клиентов) не расшифруются.
Пароль Postgres переносить НЕ нужно — он живёт в кластере, а DATABASE_URL новой
установки уже ему соответствует; мы лишь восстанавливаем содержимое БД.
"""

from __future__ import annotations

import logging
import os
import shlex
from typing import Optional

import paramiko

from app.services.panel_role import REMOTE_PANEL_DIR
from app.ssh import exec as ssh_exec

logger = logging.getLogger("utmka.panel_provision")

PANEL_REPO = os.getenv("PANEL_REPO", "Saw28rus/utmka-awg")
PANEL_BRANCH = os.getenv("PANEL_BRANCH", "main")
INSTALL_SCRIPT_URL = (
    f"https://raw.githubusercontent.com/{PANEL_REPO}/{PANEL_BRANCH}/scripts/install-panel.sh"
)
HEALTH_URL = "http://127.0.0.1:8080/api/v1/health"


class PanelProvisionError(Exception):
    pass


def _run(ssh: paramiko.SSHClient, cmd: str, timeout: int, what: str) -> ssh_exec.CommandResult:
    res = ssh_exec.run(ssh, cmd, timeout=timeout)
    if res.exit_code != 0:
        detail = (res.stderr or res.stdout or "").strip()[-800:]
        raise PanelProvisionError(f"{what}: {detail or f'код {res.exit_code}'}")
    return res


def public_ip(ssh: paramiko.SSHClient) -> Optional[str]:
    res = ssh_exec.run(
        ssh,
        "curl -4 -s --max-time 6 ifconfig.me 2>/dev/null "
        "|| curl -4 -s --max-time 6 icanhazip.com 2>/dev/null "
        "|| hostname -I 2>/dev/null | awk '{print $1}'",
        timeout=20,
    )
    ip = (res.stdout or "").strip().split()
    return ip[0] if ip else None


def install_panel(ssh: paramiko.SSHClient, *, admin_password: Optional[str] = None, timeout: int = 1800) -> str:
    """Развернуть свежий стек панели на узле (idempotent: повторный прогон = update).

    Стек поднимется в роли active со свежими секретами — это нормально: дальше
    мы переключим узел в standby и зальём настоящие данные.
    """
    env_prefix = ""
    if admin_password:
        env_prefix = f"ADMIN_PASSWORD={shlex.quote(admin_password)} "
    cmd = (
        "set -o pipefail; "
        f"{env_prefix}curl -fsSL {shlex.quote(INSTALL_SCRIPT_URL)} | {env_prefix}bash"
    )
    res = _run(ssh, cmd, timeout=timeout, what="install-panel.sh")
    return res.stdout


def set_remote_role(ssh: paramiko.SSHClient, role: str) -> None:
    """Записать PANEL_ROLE на узле (читается backend'ом при старте)."""
    normalized = "standby" if str(role).strip().lower() == "standby" else "active"
    path = f"{REMOTE_PANEL_DIR}/PANEL_ROLE"
    _run(
        ssh,
        f"mkdir -p {shlex.quote(REMOTE_PANEL_DIR)} && printf '%s\\n' {shlex.quote(normalized)} > {shlex.quote(path)}",
        timeout=20,
        what="set PANEL_ROLE",
    )


def get_remote_role(ssh: paramiko.SSHClient) -> str:
    res = ssh_exec.run(ssh, f"cat {shlex.quote(REMOTE_PANEL_DIR)}/PANEL_ROLE 2>/dev/null || echo active", timeout=15)
    return "standby" if (res.stdout or "").strip().lower() == "standby" else "active"


def set_env_value(ssh: paramiko.SSHClient, key: str, value: str) -> None:
    """Заменить (или добавить) строку KEY=value в .env узла, не трогая остальное."""
    path = f"{REMOTE_PANEL_DIR}/.env"
    # sed по ключу; если ключа нет — дописываем. Значение пишем через временный файл,
    # чтобы не мучиться с экранированием спецсимволов в sed.
    script = (
        f"f={shlex.quote(path)}; k={shlex.quote(key)}; "
        f"v={shlex.quote(value)}; "
        'if grep -q "^${k}=" "$f"; then '
        'grep -v "^${k}=" "$f" > "$f.tmp" && mv "$f.tmp" "$f"; '
        "fi; "
        'printf "%s=%s\\n" "$k" "$v" >> "$f"; '
        'chmod 600 "$f"'
    )
    _run(ssh, script, timeout=20, what=f"set {key} in .env")


def compose_recreate(ssh: paramiko.SSHClient, *, timeout: int = 600) -> None:
    """Перезапустить стек с подхватом нового .env/роли (force-recreate)."""
    _run(
        ssh,
        f"cd {shlex.quote(REMOTE_PANEL_DIR)} && docker compose up -d --force-recreate",
        timeout=timeout,
        what="docker compose up",
    )


def health_ok(ssh: paramiko.SSHClient, *, retries: int = 30, delay: int = 5) -> bool:
    """Дождаться, пока /api/v1/health на узле ответит 200."""
    cmd = (
        f"for i in $(seq 1 {retries}); do "
        f"code=$(curl -s -o /dev/null -w '%{{http_code}}' --max-time 5 {shlex.quote(HEALTH_URL)} || true); "
        'if [ "$code" = "200" ]; then echo OK; exit 0; fi; '
        f"sleep {delay}; done; echo FAIL; exit 1"
    )
    res = ssh_exec.run(ssh, cmd, timeout=retries * (delay + 6) + 30)
    return "OK" in (res.stdout or "")
