"""HARDEN: ограничение порта :8080 панели файрволом (аварийный вход только с IP админа).

Основной вход в панель — HTTPS (panel_ssl). Порт :8080 не закрывается полностью,
а ограничивается: доступ только с доверенных IP. Реализация:

- выделенная iptables-цепочка ``UTMKA-PANEL-8080`` (RETURN для разрешённых IP, затем DROP);
- jump из ``DOCKER-USER`` (порт публикуется docker'ом — внешний трафик идёт через FORWARD)
  и из ``INPUT`` (defense in depth);
- скрипт ``/opt/utmka/harden-8080.sh`` + systemd-юнит — правила переживают перезагрузку;
- локальный nginx-прокси (HTTPS → 127.0.0.1:8080) не затрагивается: host-локальный
  трафик не проходит через FORWARD/DOCKER-USER.

Fail-safe: применение требует работающего HTTPS; IP текущей сессии админа должен быть
в списке (иначе нужен явный force); после применения — health-check и автооткат при провале.
"""

from __future__ import annotations

import ipaddress
import shlex
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from app.services.server_store import server_store
from app.ssh import exec as ssh_exec

CHAIN = "UTMKA-PANEL-8080"
HARDEN_SCRIPT = "/opt/utmka/harden-8080.sh"
HARDEN_UNIT_NAME = "utmka-harden-8080.service"
HARDEN_UNIT = f"/etc/systemd/system/{HARDEN_UNIT_NAME}"
PANEL_PORT = 8080


class PanelHardenError(Exception):
    pass


@dataclass
class PanelHardenState:
    enabled: bool
    allowed_ips: list[str]
    persistent: bool
    https_active: bool
    https_url: Optional[str]
    message: Optional[str] = None


@dataclass
class PanelHardenApplyResult:
    ok: bool
    enabled: bool
    allowed_ips: list[str]
    message: str


def get_harden_state(server_id: str) -> PanelHardenState:
    record = server_store.get_record(server_id)
    target = server_store.ssh_target(server_id)
    if not record or not target:
        raise PanelHardenError("Сервер не найден.")

    ssl_info = record.get("panel_ssl") or {}
    https_active = ssl_info.get("status") == "active"
    https_url = ssl_info.get("url")

    try:
        ssh = _connect(target)
    except Exception as exc:  # noqa: BLE001
        stored = record.get("panel_harden") or {}
        return PanelHardenState(
            enabled=bool(stored.get("enabled")),
            allowed_ips=list(stored.get("allowed_ips") or []),
            persistent=bool(stored.get("enabled")),
            https_active=https_active,
            https_url=https_url,
            message=f"SSH не отвечает: {exc}",
        )

    try:
        enabled = _chain_active(ssh)
        allowed = _read_allowed_ips(ssh) if enabled else []
        persistent = _unit_enabled(ssh)
        return PanelHardenState(
            enabled=enabled,
            allowed_ips=allowed,
            persistent=persistent,
            https_active=https_active,
            https_url=https_url,
        )
    finally:
        ssh.close()


def apply_harden(
    server_id: str,
    allowed_ips: list[str],
    *,
    caller_ip: Optional[str],
    force: bool = False,
) -> PanelHardenApplyResult:
    record = server_store.get_record(server_id)
    target = server_store.ssh_target(server_id)
    if not record or not target:
        raise PanelHardenError("Сервер не найден.")

    ssl_info = record.get("panel_ssl") or {}
    if ssl_info.get("status") != "active":
        raise PanelHardenError(
            "Сначала включите HTTPS для панели (домен + сертификат). "
            "Без рабочего HTTPS ограничение :8080 оставит вас без основного входа."
        )

    networks = _normalize_ips(allowed_ips)
    if not networks and not force:
        raise PanelHardenError(
            "Список разрешённых IP пуст — :8080 будет закрыт полностью "
            "(аварийный вход только через SSH-туннель). Подтвердите действие повторно."
        )

    caller = _public_caller_ip(caller_ip)
    if caller and networks and not _ip_covered(caller, networks) and not force:
        raise PanelHardenError(
            f"Ваш текущий IP ({caller}) не входит в список разрешённых. "
            "Добавьте его или подтвердите действие повторно, если это намеренно."
        )

    ssh = _connect(target)
    try:
        script = _build_apply_script(networks)
        ssh_exec.run(ssh, "sudo mkdir -p /opt/utmka", timeout=15)
        _write_root_file(ssh, HARDEN_SCRIPT, script, mode="755")
        _write_root_file(ssh, HARDEN_UNIT, _build_unit(), mode="644")

        result = ssh_exec.run(
            ssh,
            f"sudo systemctl daemon-reload && sudo systemctl enable {HARDEN_UNIT_NAME} >/dev/null 2>&1; "
            f"sudo {HARDEN_SCRIPT}",
            timeout=60,
        )
        output = (result.stdout + "\n" + result.stderr).strip()
        if result.exit_code != 0 or "UTMKA_HARDEN_APPLIED" not in output:
            _run_disable(ssh)
            raise PanelHardenError(f"Не удалось применить правила файрвола:\n{output[-800:]}")

        # Health-check: локальный вход (nginx-прокси) и HTTPS должны остаться живыми.
        ok_local = (
            ssh_exec.run(
                ssh,
                f"curl -sf --max-time 8 http://127.0.0.1:{PANEL_PORT}/api/v1/health >/dev/null",
                timeout=20,
            ).exit_code
            == 0
        )
        domain = ssl_info.get("domain")
        ok_https = True
        if domain:
            ok_https = (
                ssh_exec.run(
                    ssh,
                    f"curl -sf --max-time 10 https://{shlex.quote(domain)}/api/v1/health >/dev/null",
                    timeout=25,
                ).exit_code
                == 0
            )
        if not ok_local or not ok_https:
            _run_disable(ssh)
            raise PanelHardenError(
                "После применения правил панель перестала отвечать "
                f"({'локально' if not ok_local else 'по HTTPS'}) — правила откатил, всё как было."
            )

        allowed_str = [str(n) for n in networks]
        server_store.update_runtime(
            server_id,
            panel_harden={
                "enabled": True,
                "allowed_ips": allowed_str,
                "applied_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        if allowed_str:
            message = f":8080 ограничен. Доступ остался только с: {', '.join(allowed_str)}."
        else:
            message = (
                ":8080 закрыт полностью. Аварийный вход: ssh -L 8080:127.0.0.1:8080 user@сервер, "
                "затем http://127.0.0.1:8080 в браузере."
            )
        return PanelHardenApplyResult(ok=True, enabled=True, allowed_ips=allowed_str, message=message)
    finally:
        ssh.close()


def disable_harden(server_id: str) -> PanelHardenApplyResult:
    record = server_store.get_record(server_id)
    target = server_store.ssh_target(server_id)
    if not record or not target:
        raise PanelHardenError("Сервер не найден.")

    ssh = _connect(target)
    try:
        _run_disable(ssh)
        if _chain_active(ssh):
            raise PanelHardenError("Не удалось снять ограничение — цепочка файрвола осталась.")
        server_store.update_runtime(server_id, panel_harden={"enabled": False, "allowed_ips": []})
        return PanelHardenApplyResult(
            ok=True,
            enabled=False,
            allowed_ips=[],
            message="Ограничение снято: :8080 снова доступен всем. Рекомендуется только на время отладки.",
        )
    finally:
        ssh.close()


# --- внутреннее ---------------------------------------------------------------


def _connect(target) -> object:
    return ssh_exec.connect(
        host=target.host,
        port=target.port,
        username=target.username,
        password=target.password,
        key=target.key,
        timeout=15,
    )


def _normalize_ips(raw: list[str]) -> list[ipaddress.IPv4Network]:
    networks: list[ipaddress.IPv4Network] = []
    for item in raw:
        text = (item or "").strip()
        if not text:
            continue
        try:
            net = ipaddress.ip_network(text, strict=False)
        except ValueError as exc:
            raise PanelHardenError(f"Некорректный IP или подсеть: {text}") from exc
        if net.version != 4:
            raise PanelHardenError(
                f"{text}: поддерживаются только IPv4-адреса (IPv6-доступ к :8080 закрывается целиком)."
            )
        if net.is_loopback or net.is_multicast:
            raise PanelHardenError(f"{text}: loopback/multicast адрес здесь не имеет смысла.")
        networks.append(net)
    # без дублей, в стабильном порядке
    seen: set[str] = set()
    unique: list[ipaddress.IPv4Network] = []
    for net in networks:
        if str(net) not in seen:
            seen.add(str(net))
            unique.append(net)
    return unique


def _public_caller_ip(caller_ip: Optional[str]) -> Optional[str]:
    if not caller_ip:
        return None
    try:
        addr = ipaddress.ip_address(caller_ip)
    except ValueError:
        return None
    if addr.version != 4 or addr.is_private or addr.is_loopback:
        return None
    return str(addr)


def _ip_covered(ip: str, networks: list[ipaddress.IPv4Network]) -> bool:
    addr = ipaddress.ip_address(ip)
    return any(addr in net for net in networks)


def _build_apply_script(networks: list[ipaddress.IPv4Network]) -> str:
    allow_lines = "\n".join(
        f"iptables -A {CHAIN} -s {net} -j RETURN" for net in networks
    )
    return f"""#!/bin/sh
# UTMka HARDEN: доступ к :{PANEL_PORT} только с разрешённых IP. Файл создан панелью.
set -e

iptables -N {CHAIN} 2>/dev/null || iptables -F {CHAIN}
# Локальный трафик (nginx HTTPS-прокси -> 127.0.0.1:{PANEL_PORT} через docker-proxy) — всегда разрешён.
iptables -A {CHAIN} -i lo -j RETURN
iptables -A {CHAIN} -s 127.0.0.0/8 -j RETURN
{allow_lines}
iptables -A {CHAIN} -j DROP

iptables -N DOCKER-USER 2>/dev/null || true
iptables -C DOCKER-USER -p tcp -m conntrack --ctorigdstport {PANEL_PORT} --ctdir ORIGINAL -j {CHAIN} 2>/dev/null || \\
  iptables -I DOCKER-USER -p tcp -m conntrack --ctorigdstport {PANEL_PORT} --ctdir ORIGINAL -j {CHAIN}
iptables -C INPUT -p tcp --dport {PANEL_PORT} -j {CHAIN} 2>/dev/null || \\
  iptables -I INPUT -p tcp --dport {PANEL_PORT} -j {CHAIN}

if command -v ip6tables >/dev/null 2>&1; then
  ip6tables -N {CHAIN} 2>/dev/null || ip6tables -F {CHAIN} || true
  ip6tables -A {CHAIN} -i lo -j RETURN 2>/dev/null || true
  ip6tables -A {CHAIN} -j DROP 2>/dev/null || true
  ip6tables -C INPUT -p tcp --dport {PANEL_PORT} -j {CHAIN} 2>/dev/null || \\
    ip6tables -I INPUT -p tcp --dport {PANEL_PORT} -j {CHAIN} 2>/dev/null || true
  ip6tables -C DOCKER-USER -p tcp -m conntrack --ctorigdstport {PANEL_PORT} --ctdir ORIGINAL -j {CHAIN} 2>/dev/null || \\
    ip6tables -I DOCKER-USER -p tcp -m conntrack --ctorigdstport {PANEL_PORT} --ctdir ORIGINAL -j {CHAIN} 2>/dev/null || true
fi

echo UTMKA_HARDEN_APPLIED
"""


def _build_unit() -> str:
    return f"""[Unit]
Description=UTMka panel: restrict :{PANEL_PORT} to admin IPs
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=oneshot
ExecStart={HARDEN_SCRIPT}
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
"""


def _run_disable(ssh) -> None:
    script = f"""
for t in iptables ip6tables; do
  command -v $t >/dev/null 2>&1 || continue
  $t -D DOCKER-USER -p tcp -m conntrack --ctorigdstport {PANEL_PORT} --ctdir ORIGINAL -j {CHAIN} 2>/dev/null || true
  $t -D INPUT -p tcp --dport {PANEL_PORT} -j {CHAIN} 2>/dev/null || true
  $t -F {CHAIN} 2>/dev/null || true
  $t -X {CHAIN} 2>/dev/null || true
done
systemctl disable {HARDEN_UNIT_NAME} >/dev/null 2>&1 || true
rm -f {shlex.quote(HARDEN_UNIT)} {shlex.quote(HARDEN_SCRIPT)}
systemctl daemon-reload >/dev/null 2>&1 || true
echo UTMKA_HARDEN_DISABLED
"""
    ssh_exec.run(ssh, f"sudo sh -c {shlex.quote(script)}", timeout=60)


def _chain_active(ssh) -> bool:
    code = ssh_exec.run(
        ssh,
        f"sudo iptables -S INPUT 2>/dev/null | grep -q {shlex.quote(CHAIN)}",
        timeout=15,
    ).exit_code
    return code == 0


def _unit_enabled(ssh) -> bool:
    out = ssh_exec.run(
        ssh,
        f"sudo systemctl is-enabled {HARDEN_UNIT_NAME} 2>/dev/null || true",
        timeout=15,
    ).stdout.strip()
    return out == "enabled"


def _read_allowed_ips(ssh) -> list[str]:
    out = ssh_exec.run(
        ssh,
        f"sudo iptables -S {shlex.quote(CHAIN)} 2>/dev/null || true",
        timeout=15,
    ).stdout
    ips: list[str] = []
    for line in out.splitlines():
        parts = line.split()
        if "-s" in parts and "RETURN" in parts:
            idx = parts.index("-s")
            if idx + 1 < len(parts):
                value = parts[idx + 1]
                # iptables печатает /32 для одиночных адресов — убираем для читаемости
                ips.append(value[:-3] if value.endswith("/32") else value)
    return ips


def _write_root_file(ssh, path: str, content: str, *, mode: str) -> None:
    tmp = f"/tmp/utmka_harden_{abs(hash(path)) % 100000}"
    heredoc = f"cat > {shlex.quote(tmp)} <<'UTMKA_HARDEN_EOF'\n{content}\nUTMKA_HARDEN_EOF"
    result = ssh_exec.run(ssh, heredoc, timeout=20)
    if result.exit_code != 0:
        raise PanelHardenError(f"Не удалось записать файл {path}.")
    result = ssh_exec.run(
        ssh,
        f"sudo mv {shlex.quote(tmp)} {shlex.quote(path)} && sudo chmod {mode} {shlex.quote(path)}",
        timeout=20,
    )
    if result.exit_code != 0:
        raise PanelHardenError(f"Не удалось установить файл {path}.")
