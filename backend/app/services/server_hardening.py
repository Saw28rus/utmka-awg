"""Управление параметрами безопасности сервера из аудита (Фазы 1–2).

Управляемые контролы:
- ``fail2ban``  — установка/включение/выключение (низкий риск);
- ``updates``   — unattended-upgrades без авто-ребута (низкий риск);
- ``ufw``       — включение/выключение файрвола (средний риск, защита от локаута).

Защита от локаута (для UFW):
1. **dead-man switch** — перед включением UFW на сервере взводится отложенный
   ``systemd-run --on-active``-таймер, который сам выключит UFW через N секунд,
   если панель не отменит его после успешной проверки. Если панель/сеть упала —
   сервер восстановит доступ сам.
2. **canary-reconnect** — после применения панель открывает НОВОЕ ssh-соединение
   и проверяет связь. Не поднялось — немедленный откат через старое соединение
   (а dead-man остаётся как финальная страховка).

UFW открывает все уже слушающие порты (``ss``) + SSH/80/443/8080, поэтому ни
один работающий сервис не закрывается. Docker-порты UFW не фильтрует — VPN в
контейнерах продолжает работать в любом случае.
"""

from __future__ import annotations

import shlex
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from app.services.server_store import server_store
from app.ssh import exec as ssh_exec

# Порты, которые UFW обязан держать открытыми независимо от наличия слушателя.
MANDATORY_TCP_PORTS = (80, 443, 8080)
DEADMAN_UNIT = "utmka-ufw-revert"
DEADMAN_SCRIPT = "/opt/utmka/ufw-revert.sh"
DEADMAN_TIMEOUT = 120  # секунд до авто-отката UFW
UTMKA_DIR = "/opt/utmka"

CONTROLS = {"fail2ban", "updates", "ufw"}


class HardeningError(Exception):
    pass


@dataclass
class HardeningResult:
    ok: bool
    control: str
    enabled: bool
    message: str


@dataclass
class UfwPreview:
    tcp_ports: list[int]
    udp_ports: list[int]
    ssh_port: int


# ---------------------------------------------------------------------------
# инфраструктура
# ---------------------------------------------------------------------------


def _target(server_id: str):
    target = server_store.ssh_target(server_id)
    if not target:
        raise HardeningError("Сервер не найден.")
    return target


def _connect(target, *, timeout: int = 15):
    return ssh_exec.connect(
        host=target.host,
        port=target.port,
        username=target.username,
        password=target.password,
        key=target.key,
        timeout=timeout,
    )


def _sudo(ssh, script: str, *, timeout: int = 120) -> ssh_exec.CommandResult:
    return ssh_exec.run(ssh, f"sudo sh -c {shlex.quote(script)}", timeout=timeout)


def _verify_reconnect(server_id: str, *, attempts: int = 3, delay: float = 2.0) -> bool:
    """Canary: получится ли заново подключиться по SSH (доступ не потерян)."""
    target = _target(server_id)
    for i in range(attempts):
        try:
            ssh = _connect(target, timeout=12)
            try:
                res = ssh_exec.run(ssh, "echo UTMKA_ALIVE", timeout=10)
                if "UTMKA_ALIVE" in res.stdout:
                    return True
            finally:
                ssh.close()
        except Exception:  # noqa: BLE001
            pass
        if i < attempts - 1:
            time.sleep(delay)
    return False


def _panel_and_server_ips(ssh, server_id: str) -> list[str]:
    """IP, которые нельзя банить: egress панели ($SSH_CONNECTION) + публичный IP сервера."""
    ips: list[str] = []
    res = ssh_exec.run(ssh, 'echo "$SSH_CONNECTION"; hostname -I 2>/dev/null', timeout=15)
    lines = [l.strip() for l in res.stdout.splitlines() if l.strip()]
    if lines:
        conn = lines[0].split()
        if conn:
            ips.append(conn[0])  # IP, с которого пришла панель
    if len(lines) > 1:
        ips.extend(lines[1].split())  # адреса самого сервера
    seen: set[str] = set()
    unique: list[str] = []
    for ip in ips:
        if ip and ip not in seen and ":" not in ip:  # только IPv4, без дублей
            seen.add(ip)
            unique.append(ip)
    return unique


def _record_state(server_id: str, control: str, enabled: bool) -> None:
    record = server_store.get_record(server_id) or {}
    hardening = dict(record.get("hardening") or {})
    hardening[control] = {
        "enabled": enabled,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    server_store.update_runtime(server_id, hardening=hardening)


# ---------------------------------------------------------------------------
# Fail2ban
# ---------------------------------------------------------------------------


def apply_fail2ban(server_id: str, *, caller_ip: Optional[str] = None) -> HardeningResult:
    target = _target(server_id)
    ssh = _connect(target)
    try:
        ignore = _panel_and_server_ips(ssh, server_id)
        if caller_ip and ":" not in caller_ip and caller_ip not in ignore:
            ignore.append(caller_ip)
        ignore_str = " ".join(ignore)
        ssh_port = int((server_store.get_record(server_id) or {}).get("ssh_port") or 22)
        script = f"""set -e
export DEBIAN_FRONTEND=noninteractive
if ! command -v fail2ban-client >/dev/null 2>&1; then
  apt-get update -qq
  apt-get install -y -qq fail2ban
fi
mkdir -p /etc/fail2ban
cat > /etc/fail2ban/jail.local <<'UTMKA_EOF'
[DEFAULT]
ignoreip = 127.0.0.1/8 ::1 {ignore_str}
bantime = 1h
findtime = 10m
maxretry = 5

[sshd]
enabled = true
port = {ssh_port}
backend = systemd
UTMKA_EOF
systemctl enable fail2ban >/dev/null 2>&1 || true
systemctl restart fail2ban
sleep 1
systemctl is-active fail2ban
echo UTMKA_F2B_APPLIED
"""
        res = _sudo(ssh, script, timeout=180)
        out = (res.stdout + "\n" + res.stderr).strip()
        if "UTMKA_F2B_APPLIED" not in out:
            raise HardeningError(f"Не удалось включить Fail2ban:\n{out[-700:]}")
        _record_state(server_id, "fail2ban", True)
        return HardeningResult(
            ok=True, control="fail2ban", enabled=True,
            message="Fail2ban включён. Ваш IP и IP панели добавлены в исключения — себя не забаните.",
        )
    finally:
        ssh.close()


def disable_fail2ban(server_id: str) -> HardeningResult:
    target = _target(server_id)
    ssh = _connect(target)
    try:
        _sudo(ssh, "systemctl disable --now fail2ban >/dev/null 2>&1 || true; echo UTMKA_F2B_DISABLED", timeout=60)
        _record_state(server_id, "fail2ban", False)
        return HardeningResult(
            ok=True, control="fail2ban", enabled=False,
            message="Fail2ban выключен. Защита SSH от перебора паролей больше не активна.",
        )
    finally:
        ssh.close()


# ---------------------------------------------------------------------------
# Unattended-upgrades (автообновления безопасности, без авто-ребута)
# ---------------------------------------------------------------------------


def apply_updates(server_id: str) -> HardeningResult:
    target = _target(server_id)
    ssh = _connect(target)
    try:
        script = """set -e
export DEBIAN_FRONTEND=noninteractive
if ! dpkg -l unattended-upgrades 2>/dev/null | grep -q '^ii'; then
  apt-get update -qq
  apt-get install -y -qq unattended-upgrades
fi
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'UTMKA_EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
UTMKA_EOF
cat > /etc/apt/apt.conf.d/52utmka-unattended <<'UTMKA_EOF'
// Управляется панелью UTMka. Авто-ребут выключен, чтобы не оборвать VPN.
Unattended-Upgrade::Automatic-Reboot "false";
UTMKA_EOF
systemctl enable unattended-upgrades >/dev/null 2>&1 || true
systemctl restart unattended-upgrades >/dev/null 2>&1 || true
echo UTMKA_UPDATES_APPLIED
"""
        res = _sudo(ssh, script, timeout=180)
        out = (res.stdout + "\n" + res.stderr).strip()
        if "UTMKA_UPDATES_APPLIED" not in out:
            raise HardeningError(f"Не удалось настроить автообновления:\n{out[-700:]}")
        _record_state(server_id, "updates", True)
        return HardeningResult(
            ok=True, control="updates", enabled=True,
            message="Автообновления безопасности включены. Авто-перезагрузка выключена — VPN не оборвётся.",
        )
    finally:
        ssh.close()


def disable_updates(server_id: str) -> HardeningResult:
    target = _target(server_id)
    ssh = _connect(target)
    try:
        script = """cat > /etc/apt/apt.conf.d/20auto-upgrades <<'UTMKA_EOF'
APT::Periodic::Update-Package-Lists "0";
APT::Periodic::Unattended-Upgrade "0";
UTMKA_EOF
systemctl disable --now unattended-upgrades >/dev/null 2>&1 || true
echo UTMKA_UPDATES_DISABLED
"""
        _sudo(ssh, script, timeout=60)
        _record_state(server_id, "updates", False)
        return HardeningResult(
            ok=True, control="updates", enabled=False,
            message="Автообновления безопасности выключены.",
        )
    finally:
        ssh.close()


# ---------------------------------------------------------------------------
# UFW
# ---------------------------------------------------------------------------


def _listening_ports(ssh) -> tuple[list[int], list[int]]:
    """Возвращает (tcp_ports, udp_ports), которые реально слушаются (не loopback)."""
    tcp = _parse_ss(ssh_exec.run(ssh, "ss -H -tln 2>/dev/null", timeout=20).stdout)
    udp = _parse_ss(ssh_exec.run(ssh, "ss -H -uln 2>/dev/null", timeout=20).stdout)
    return tcp, udp


def _parse_ss(text: str) -> list[int]:
    ports: set[int] = set()
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 2:
            continue
        local = fields[-2]  # Local Address:Port (Peer — последнее поле)
        # loopback не требует разрешения в файрволе
        if local.startswith("127.") or local.startswith("[::1]"):
            continue
        if ":" not in local:
            continue
        port_str = local.rsplit(":", 1)[1]
        try:
            port = int(port_str)
        except ValueError:
            continue
        if 1 <= port <= 65535:
            ports.add(port)
    return sorted(ports)


def ufw_preview(server_id: str) -> UfwPreview:
    target = _target(server_id)
    ssh = _connect(target)
    try:
        tcp, udp = _listening_ports(ssh)
        ssh_port = int((server_store.get_record(server_id) or {}).get("ssh_port") or 22)
        tcp_set = set(tcp) | set(MANDATORY_TCP_PORTS) | {ssh_port}
        return UfwPreview(tcp_ports=sorted(tcp_set), udp_ports=sorted(set(udp)), ssh_port=ssh_port)
    finally:
        ssh.close()


def apply_ufw(server_id: str) -> HardeningResult:
    target = _target(server_id)
    ssh = _connect(target)
    try:
        tcp, udp = _listening_ports(ssh)
        ssh_port = int(target.port or 22)
        tcp_ports = sorted(set(tcp) | set(MANDATORY_TCP_PORTS) | {ssh_port})
        udp_ports = sorted(set(udp))

        allow_lines = "\n".join(
            [f"ufw allow {p}/tcp >/dev/null" for p in tcp_ports]
            + [f"ufw allow {p}/udp >/dev/null" for p in udp_ports]
        )
        token = uuid.uuid4().hex[:8]
        script = f"""set -e
export DEBIAN_FRONTEND=noninteractive
if ! command -v ufw >/dev/null 2>&1; then
  apt-get update -qq
  apt-get install -y -qq ufw
fi
mkdir -p {UTMKA_DIR}
# --- dead-man switch: авто-выключение UFW через {DEADMAN_TIMEOUT}s, если панель не отменит ---
cat > {DEADMAN_SCRIPT} <<'UTMKA_EOF'
#!/bin/sh
# UTMka dead-man: вернуть доступ, если панель не подтвердила успех.
ufw --force disable
UTMKA_EOF
chmod +x {DEADMAN_SCRIPT}
systemctl stop {DEADMAN_UNIT}.timer >/dev/null 2>&1 || true
systemctl reset-failed {DEADMAN_UNIT}.timer >/dev/null 2>&1 || true
systemd-run --on-active={DEADMAN_TIMEOUT} --unit={DEADMAN_UNIT} --description="UTMka UFW dead-man revert ({token})" /bin/sh {DEADMAN_SCRIPT} >/dev/null
# --- открыть все нужные порты ДО включения ---
ufw allow {ssh_port}/tcp >/dev/null
{allow_lines}
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw --force enable >/dev/null
echo UTMKA_UFW_APPLIED
"""
        res = _sudo(ssh, script, timeout=180)
        out = (res.stdout + "\n" + res.stderr).strip()
        if "UTMKA_UFW_APPLIED" not in out:
            # применение не удалось — снять dead-man и UFW на всякий случай
            _ufw_revert(ssh)
            raise HardeningError(f"Не удалось включить UFW:\n{out[-700:]}")

        # canary: доступ по SSH должен остаться. Открытое соединение пока живо.
        if not _verify_reconnect(server_id):
            _ufw_revert(ssh)
            raise HardeningError(
                "После включения UFW сервер перестал отвечать по SSH — "
                "файрвол откатил, всё как было."
            )

        # успех — снимаем dead-man
        _disarm_deadman(ssh)
        _record_state(server_id, "ufw", True)
        opened = ", ".join(str(p) for p in tcp_ports)
        return HardeningResult(
            ok=True, control="ufw", enabled=True,
            message=f"UFW включён. Открыты порты: {opened} (TCP) + слушающие UDP. Доступ проверен.",
        )
    finally:
        ssh.close()


def disable_ufw(server_id: str) -> HardeningResult:
    target = _target(server_id)
    ssh = _connect(target)
    try:
        _ufw_revert(ssh)
        _record_state(server_id, "ufw", False)
        return HardeningResult(
            ok=True, control="ufw", enabled=False,
            message="UFW выключен. Фильтрация портов на уровне ОС снята.",
        )
    finally:
        ssh.close()


def _ufw_revert(ssh) -> None:
    _sudo(
        ssh,
        f"ufw --force disable >/dev/null 2>&1 || true; "
        f"systemctl stop {DEADMAN_UNIT}.timer >/dev/null 2>&1 || true; "
        f"systemctl reset-failed {DEADMAN_UNIT}.timer >/dev/null 2>&1 || true; "
        f"echo UTMKA_UFW_DISABLED",
        timeout=60,
    )


def _disarm_deadman(ssh) -> None:
    _sudo(
        ssh,
        f"systemctl stop {DEADMAN_UNIT}.timer >/dev/null 2>&1 || true; "
        f"systemctl reset-failed {DEADMAN_UNIT}.timer >/dev/null 2>&1 || true; "
        f"echo UTMKA_DEADMAN_OFF",
        timeout=30,
    )


# ---------------------------------------------------------------------------
# Диспетчер
# ---------------------------------------------------------------------------


def run_action(server_id: str, control: str, action: str, *, caller_ip: Optional[str] = None) -> HardeningResult:
    if control not in CONTROLS:
        raise HardeningError("Этим параметром безопасности пока нельзя управлять из панели.")
    if action not in ("enable", "disable"):
        raise HardeningError("Недопустимое действие.")
    enable = action == "enable"
    if control == "fail2ban":
        return apply_fail2ban(server_id, caller_ip=caller_ip) if enable else disable_fail2ban(server_id)
    if control == "updates":
        return apply_updates(server_id) if enable else disable_updates(server_id)
    if control == "ufw":
        return apply_ufw(server_id) if enable else disable_ufw(server_id)
    raise HardeningError("Неизвестный контрол.")
