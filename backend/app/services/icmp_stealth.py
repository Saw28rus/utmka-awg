"""Скрытие туннеля от 2ip: не отвечать на ICMP echo (двусторонний пинг).

2ip.ru берёт IP, с которого вы зашли, и пингует его. Домашний NAT обычно молчит,
VPS отвечает — отсюда «Определение туннеля (двусторонний пинг): обнаружен».

Закрываем только echo-request (sysctl + iptables). Destination-unreachable
не трогаем — иначе ломается Path MTU.

В каскаде 2ip пингует **выходной** сервер (тот IP, который видят сайты).
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from app.services.server_store import server_store
from app.ssh import exec as ssh_exec

SYSCTL_FILE = "/etc/sysctl.d/99-utmka-icmp-stealth.conf"
SCRIPT_PATH = "/opt/utmka/icmp-stealth.sh"
UNIT_NAME = "utmka-icmp-stealth.service"
UNIT_PATH = f"/etc/systemd/system/{UNIT_NAME}"
COMMENT = "UTMKA-ICMP-STEALTH"
CONTROL = "icmp_stealth"


class IcmpStealthError(Exception):
    pass


@dataclass
class IcmpStealthState:
    enabled: bool
    persistent: bool
    ping_replies: Optional[bool]
    role: str  # exit | entry | standalone
    message: Optional[str] = None


@dataclass
class IcmpStealthResult:
    ok: bool
    enabled: bool
    ping_replies: Optional[bool]
    message: str


@dataclass
class IcmpStealthServerRow:
    server_id: str
    name: str
    host: str
    enabled: bool
    role: str
    ping_replies: Optional[bool]
    message: Optional[str] = None


def server_cascade_role(server_id: str) -> str:
    """exit — сайты видят этот IP; entry — вход каскада; иначе одиночный узел."""
    try:
        from app.services.cascade_store import cascade_store

        for link in cascade_store.list_links():
            if link.get("exit_server_id") == server_id:
                return "exit"
            if link.get("entry_server_id") == server_id:
                return "entry"
    except Exception:  # noqa: BLE001
        pass
    try:
        from app.services.xray_cascade_store import xray_cascade_store

        for link in xray_cascade_store.list_links():
            if link.get("exit_server_id") == server_id:
                return "exit"
            if link.get("entry_server_id") == server_id:
                return "entry"
    except Exception:  # noqa: BLE001
        pass
    return "standalone"


def get_state(server_id: str) -> IcmpStealthState:
    record = server_store.get_record(server_id)
    target = server_store.ssh_target(server_id)
    if not record or not target:
        raise IcmpStealthError("Сервер не найден.")
    role = server_cascade_role(server_id)
    stored = record.get("icmp_stealth") or {}
    try:
        ssh = _connect(target)
    except Exception as exc:  # noqa: BLE001
        return IcmpStealthState(
            enabled=bool(stored.get("enabled")),
            persistent=bool(stored.get("enabled")),
            ping_replies=None,
            role=role,
            message=f"SSH не отвечает: {exc}",
        )
    try:
        enabled = _sysctl_on(ssh)
        persistent = (
            ssh_exec.run(ssh, f"test -f {shlex.quote(SYSCTL_FILE)}", timeout=10).exit_code == 0
        )
        return IcmpStealthState(
            enabled=enabled,
            persistent=persistent,
            ping_replies=None,
            role=role,
            message=_status_message(enabled, None, role),
        )
    finally:
        ssh.close()


def apply_stealth(server_id: str) -> IcmpStealthResult:
    record = server_store.get_record(server_id)
    target = server_store.ssh_target(server_id)
    if not record or not target:
        raise IcmpStealthError("Сервер не найден.")
    ssh = _connect(target)
    try:
        script = _build_apply_script()
        result = ssh_exec.run(ssh, f"sudo bash -s <<'UTMKA_ICMP_EOF'\n{script}\nUTMKA_ICMP_EOF", timeout=60)
        output = (result.stdout + "\n" + result.stderr).strip()
        if result.exit_code != 0 or "UTMKA_ICMP_STEALTH_OK" not in output:
            raise IcmpStealthError(f"Не удалось закрыть пинг:\n{output[-800:]}")
        if not _sysctl_on(ssh):
            raise IcmpStealthError("sysctl не применился — сервер всё ещё отвечает на ping.")
        ping_replies = _external_ping_replies(record.get("host") or "")
        server_store.update_runtime(
            server_id,
            icmp_stealth={
                "enabled": True,
                "applied_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        if ping_replies is True:
            message = (
                "Ядро больше не отвечает на ping, но снаружи ответ ещё есть — "
                "это отвечает хостер, не VPS. Напишите в поддержку площадки, чтобы "
                "отключили ICMP на IP сервера."
            )
        else:
            message = "Двусторонний пинг закрыт: 2ip больше не должен видеть туннель по ICMP."
        return IcmpStealthResult(ok=True, enabled=True, ping_replies=ping_replies, message=message)
    finally:
        ssh.close()


def disable_stealth(server_id: str) -> IcmpStealthResult:
    record = server_store.get_record(server_id)
    target = server_store.ssh_target(server_id)
    if not record or not target:
        raise IcmpStealthError("Сервер не найден.")
    ssh = _connect(target)
    try:
        result = ssh_exec.run(
            ssh, f"sudo bash -s <<'UTMKA_ICMP_EOF'\n{_build_disable_script()}\nUTMKA_ICMP_EOF", timeout=45
        )
        output = (result.stdout + "\n" + result.stderr).strip()
        if result.exit_code != 0 or "UTMKA_ICMP_STEALTH_OFF" not in output:
            raise IcmpStealthError(f"Не удалось вернуть ping:\n{output[-800:]}")
        server_store.update_runtime(server_id, icmp_stealth={"enabled": False})
        ping_replies = _external_ping_replies(record.get("host") or "")
        return IcmpStealthResult(
            ok=True,
            enabled=False,
            ping_replies=ping_replies,
            message="Ответ на ping снова включён (сервер виден для 2ip как VPS).",
        )
    finally:
        ssh.close()


def list_vpn_states() -> list[IcmpStealthServerRow]:
    rows: list[IcmpStealthServerRow] = []
    for rec in server_store.list_records():
        sid = rec.get("id") or ""
        if not sid:
            continue
        try:
            state = get_state(sid)
            rows.append(
                IcmpStealthServerRow(
                    server_id=sid,
                    name=rec.get("name") or rec.get("host") or sid,
                    host=rec.get("host") or "",
                    enabled=state.enabled,
                    role=state.role,
                    ping_replies=state.ping_replies,
                    message=state.message,
                )
            )
        except IcmpStealthError as exc:
            rows.append(
                IcmpStealthServerRow(
                    server_id=sid,
                    name=rec.get("name") or rec.get("host") or sid,
                    host=rec.get("host") or "",
                    enabled=False,
                    role=server_cascade_role(sid),
                    ping_replies=None,
                    message=str(exc),
                )
            )
    return rows


def apply_all() -> list[IcmpStealthResult]:
    results: list[IcmpStealthResult] = []
    for rec in server_store.list_records():
        sid = rec.get("id") or ""
        name = rec.get("name") or rec.get("host") or sid
        try:
            result = apply_stealth(sid)
            results.append(
                IcmpStealthResult(
                    ok=result.ok,
                    enabled=result.enabled,
                    ping_replies=result.ping_replies,
                    message=f"{name}: {result.message}",
                )
            )
        except IcmpStealthError as exc:
            results.append(
                IcmpStealthResult(ok=False, enabled=False, ping_replies=None, message=f"{name}: {exc}")
            )
    if not results:
        raise IcmpStealthError("Нет VPN-серверов. Сначала добавьте РУ2/NL в список серверов.")
    return results


def disable_all() -> list[IcmpStealthResult]:
    results: list[IcmpStealthResult] = []
    for rec in server_store.list_records():
        sid = rec.get("id") or ""
        name = rec.get("name") or rec.get("host") or sid
        try:
            result = disable_stealth(sid)
            results.append(
                IcmpStealthResult(
                    ok=result.ok,
                    enabled=result.enabled,
                    ping_replies=result.ping_replies,
                    message=f"{name}: {result.message}",
                )
            )
        except IcmpStealthError as exc:
            results.append(
                IcmpStealthResult(ok=False, enabled=True, ping_replies=None, message=f"{name}: {exc}")
            )
    if not results:
        raise IcmpStealthError("Нет VPN-серверов.")
    return results


def _connect(target) -> object:
    if getattr(target, "local", False):
        from app.ssh.host_exec import LocalHostSession

        return LocalHostSession()
    return ssh_exec.connect(
        host=target.host,
        port=target.port,
        username=target.username,
        password=target.password,
        key=target.key,
        timeout=15,
    )


def _sysctl_on(ssh) -> bool:
    out = ssh_exec.run(ssh, "cat /proc/sys/net/ipv4/icmp_echo_ignore_all 2>/dev/null || echo 0", timeout=10)
    return (out.stdout or "").strip() == "1"


def _external_ping_replies(host: str) -> Optional[bool]:
    """Пинг с хоста панели — как 2ip. None, если проверить не удалось."""
    host = (host or "").strip()
    dummy = {"", "local-panel", "127.0.0.1", "localhost"}
    if host in dummy:
        return None
    try:
        from app.ssh.host_exec import run_host

        probe = run_host(
            f"ping -c 1 -W 2 {shlex.quote(host)} >/dev/null 2>&1",
            timeout=8,
        )
    except Exception:  # noqa: BLE001
        return None
    if probe.exit_code == 124:
        return None
    return probe.exit_code == 0


def _status_message(enabled: bool, ping_replies: Optional[bool], role: str) -> str:
    if role == "exit":
        where = "Это выход каскада — 2ip пингует именно этот IP."
    elif role == "entry":
        where = "Это вход каскада. Для 2ip важнее закрыть пинг на выходном сервере."
    else:
        where = "Сайты видят IP этого сервера."
    if enabled and ping_replies is True:
        return f"{where} Ядро молчит, но снаружи ping ещё отвечает (хостер)."
    if enabled:
        return f"{where} Ответ на ping выключен."
    return f"{where} Сейчас сервер отвечает на ping — 2ip пометит туннель."


def _build_apply_script() -> str:
    sysctl_body = (
        "# UTMka: не отвечать на ICMP echo (2ip «двусторонний пинг»).\n"
        "# destination-unreachable не трогаем — нужен Path MTU.\n"
        "net.ipv4.icmp_echo_ignore_all = 1\n"
        "net.ipv6.icmp.echo_ignore_all = 1\n"
    )
    iptables_fn = f"""
_utmka_icmp_ipt() {{
  if command -v iptables >/dev/null 2>&1; then
    iptables -C INPUT -p icmp --icmp-type echo-request -m comment --comment {COMMENT} -j DROP 2>/dev/null \\
      || iptables -I INPUT -p icmp --icmp-type echo-request -m comment --comment {COMMENT} -j DROP
  fi
  if command -v ip6tables >/dev/null 2>&1; then
    ip6tables -C INPUT -p ipv6-icmp --icmpv6-type echo-request -m comment --comment {COMMENT} -j DROP 2>/dev/null \\
      || ip6tables -I INPUT -p ipv6-icmp --icmpv6-type echo-request -m comment --comment {COMMENT} -j DROP 2>/dev/null || true
  fi
}}
"""
    unit = f"""[Unit]
Description=UTMka: hide ICMP echo (2ip two-way ping)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart={SCRIPT_PATH}
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
"""
    inner_script = f"""#!/bin/sh
# UTMka ICMP stealth — файл создан панелью.
set -e
sysctl -w net.ipv4.icmp_echo_ignore_all=1 >/dev/null
sysctl -w net.ipv6.icmp.echo_ignore_all=1 >/dev/null 2>&1 || true
{iptables_fn}
_utmka_icmp_ipt
"""
    return f"""set -euo pipefail
mkdir -p /opt/utmka /etc/sysctl.d /etc/systemd/system
cat > {shlex.quote(SYSCTL_FILE)} <<'UTMKA_SYSCTL_EOF'
{sysctl_body}UTMKA_SYSCTL_EOF
sysctl -p {shlex.quote(SYSCTL_FILE)} >/dev/null 2>&1 || true
sysctl -w net.ipv4.icmp_echo_ignore_all=1 >/dev/null
sysctl -w net.ipv6.icmp.echo_ignore_all=1 >/dev/null 2>&1 || true
cat > {shlex.quote(SCRIPT_PATH)} <<'UTMKA_SH_EOF'
{inner_script}
UTMKA_SH_EOF
chmod 755 {shlex.quote(SCRIPT_PATH)}
cat > {shlex.quote(UNIT_PATH)} <<'UTMKA_UNIT_EOF'
{unit}UTMKA_UNIT_EOF
{iptables_fn}
_utmka_icmp_ipt
systemctl daemon-reload
systemctl enable {UNIT_NAME} >/dev/null 2>&1 || true
echo UTMKA_ICMP_STEALTH_OK
"""


def _build_disable_script() -> str:
    return f"""set -euo pipefail
rm -f {shlex.quote(SYSCTL_FILE)} {shlex.quote(SCRIPT_PATH)} {shlex.quote(UNIT_PATH)}
sysctl -w net.ipv4.icmp_echo_ignore_all=0 >/dev/null 2>&1 || true
sysctl -w net.ipv6.icmp.echo_ignore_all=0 >/dev/null 2>&1 || true
if command -v iptables >/dev/null 2>&1; then
  while iptables -D INPUT -p icmp --icmp-type echo-request -m comment --comment {COMMENT} -j DROP 2>/dev/null; do :; done
fi
if command -v ip6tables >/dev/null 2>&1; then
  while ip6tables -D INPUT -p ipv6-icmp --icmpv6-type echo-request -m comment --comment {COMMENT} -j DROP 2>/dev/null; do :; done
fi
systemctl disable {UNIT_NAME} >/dev/null 2>&1 || true
systemctl daemon-reload >/dev/null 2>&1 || true
echo UTMKA_ICMP_STEALTH_OFF
"""
