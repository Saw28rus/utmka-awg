"""Сбор расширенной информации о сервере: система, контейнеры, протоколы, безопасность."""

import shlex
from typing import Optional

from app.schemas.servers import (
    ContainerInfo,
    ProtocolInfo,
    SecurityCheck,
    ServerOverview,
    SystemInfo,
)
from app.services.client_store import client_store
from app.services.panel_ssl import panel_https_security_check
from app.services.server_store import server_store
from app.ssh import exec as ssh_exec

# Каталог протоколов Amnezia. matches — подстроки в имени контейнера.
PROTOCOL_CATALOG: list[dict] = [
    {
        "id": "awg2",
        "name": "AmneziaWG",
        "description": "Новая версия протокола на основе awg-go. Расширенная обфускация с параметрами S3, S4.",
        "matches": ["awg2", "amnezia-awg2"],
        "managed": True,
        "can_install": True,
    },
    {
        "id": "awg_legacy",
        "name": "AmneziaWG Legacy",
        "description": "Оригинальная версия AWG на базе ядра WireGuard. Совместима со старыми клиентами.",
        "matches": ["amnezia-awg"],
        "exclude": ["awg2"],
        "can_install": True,
    },
    {
        "id": "xray",
        "name": "Xray (VLESS-Reality)",
        "description": "Маскировка под обычный веб-трафик (XTLS-Reality). Устойчив к глубокому анализу пакетов.",
        "matches": ["xray"],
        "can_install": True,
    },
    {
        "id": "telemt",
        "name": "Telemt (Telegram Proxy)",
        "description": "Прокси для Telegram на базе MTProxy с продвинутой обфускацией и эмуляцией TLS.",
        "matches": ["telemt", "mtproxy", "mtprotoproxy"],
        "can_install": True,
    },
    {
        "id": "wireguard",
        "name": "WireGuard",
        "description": "Стандартный и самый быстрый VPN-протокол. Встроен в современные ОС, но блокируется DPI.",
        "matches": ["wireguard", "amnezia-wg"],
        "exclude": ["awg"],
        "can_install": True,
    },
]

SYSTEM_SCRIPT = r"""
. /etc/os-release 2>/dev/null && echo "os=$PRETTY_NAME"
echo "kernel=$(uname -r 2>/dev/null)"
echo "arch=$(uname -m 2>/dev/null)"
echo "cpu_model=$(grep -m1 'model name' /proc/cpuinfo 2>/dev/null | cut -d: -f2- | sed 's/^ *//')"
echo "cores=$(nproc 2>/dev/null)"
echo "docker_version=$(docker --version 2>/dev/null | sed 's/Docker version //;s/,.*//')"
echo "public_ip=$(hostname -I 2>/dev/null | awk '{print $1}')"
"""

SECURITY_SCRIPT = r"""
echo "ufw=$(ufw status 2>/dev/null | head -1 | awk '{print $2}')"
echo "fail2ban=$(systemctl is-active fail2ban 2>/dev/null)"
conf=$(sshd -T 2>/dev/null)
if [ -n "$conf" ]; then
  echo "permit_root=$(echo "$conf" | awk '/^permitrootlogin /{print $2}')"
  echo "password_auth=$(echo "$conf" | awk '/^passwordauthentication /{print $2}')"
else
  echo "permit_root=$(grep -iE '^\s*PermitRootLogin' /etc/ssh/sshd_config 2>/dev/null | tail -1 | awk '{print $2}')"
  echo "password_auth=$(grep -iE '^\s*PasswordAuthentication' /etc/ssh/sshd_config 2>/dev/null | tail -1 | awk '{print $2}')"
fi
echo "unattended=$(dpkg -l unattended-upgrades 2>/dev/null | awk '/^ii/{print "installed"}')"
"""

ALLOWED_CONTAINER_ACTIONS = {"start", "stop", "restart"}
ALLOWED_PROTOCOL_ACTIONS = {"start", "stop", "restart", "remove"}


def get_server_overview(server_id: str) -> ServerOverview:
    record = server_store.get_record(server_id)
    target = server_store.ssh_target(server_id)
    if not record or not target:
        return ServerOverview(server_id=server_id, message="Сервер не найден.")

    try:
        ssh = _connect(target)
    except Exception as exc:  # noqa: BLE001
        return ServerOverview(server_id=server_id, online=False, message=f"SSH не отвечает: {exc}")

    try:
        system = _collect_system(ssh, record)
        containers = _collect_containers(ssh)
        protocols = _build_protocols(server_id, record, containers)
        security = _collect_security(ssh, record, server_id)
        _sync_xray_metadata(server_id, record, containers)
        return ServerOverview(
            server_id=server_id,
            online=True,
            system=system,
            containers=containers,
            protocols=protocols,
            security=security,
        )
    finally:
        ssh.close()


def run_container_action(server_id: str, container: str, action: str) -> str:
    if action not in ALLOWED_CONTAINER_ACTIONS:
        raise ValueError("Недопустимое действие.")
    target = server_store.ssh_target(server_id)
    if not target:
        raise ValueError("Сервер не найден.")
    ssh = _connect(target)
    try:
        _ensure_container_exists(ssh, container)
        result = ssh_exec.run(ssh, f"docker {action} {shlex.quote(container)}", timeout=60)
        if result.exit_code != 0:
            raise RuntimeError(result.stderr.strip() or f"docker {action} завершился с ошибкой.")
        return f"Контейнер {container}: {action} выполнен."
    finally:
        ssh.close()


def get_container_logs(server_id: str, container: str, tail: int = 200) -> str:
    target = server_store.ssh_target(server_id)
    if not target:
        raise ValueError("Сервер не найден.")
    tail = max(10, min(tail, 1000))
    ssh = _connect(target)
    try:
        _ensure_container_exists(ssh, container)
        result = ssh_exec.run(
            ssh,
            f"docker logs --tail {tail} {shlex.quote(container)} 2>&1",
            timeout=30,
        )
        return result.stdout
    finally:
        ssh.close()


def run_protocol_action(server_id: str, protocol_id: str, action: str) -> str:
    if action not in ALLOWED_PROTOCOL_ACTIONS:
        raise ValueError("Недопустимое действие.")
    record = server_store.get_record(server_id)
    target = server_store.ssh_target(server_id)
    if not record or not target:
        raise ValueError("Сервер не найден.")

    entry = next((p for p in PROTOCOL_CATALOG if p["id"] == protocol_id), None)
    if not entry:
        raise ValueError("Неизвестный протокол.")

    ssh = _connect(target)
    try:
        containers = _collect_containers(ssh)
        container = _match_container(entry, [c.name for c in containers])
        if not container:
            raise ValueError("Контейнер протокола не найден на сервере.")

        if action == "remove":
            result = ssh_exec.run(ssh, f"docker rm -f {shlex.quote(container)}", timeout=60)
            if result.exit_code != 0:
                raise RuntimeError(result.stderr.strip() or "Не удалось удалить контейнер.")
            # Чистим стор, иначе карточки сервера продолжат показывать протокол
            # установленным (badge берётся из installed_protocols/container_names).
            _forget_protocol_in_store(server_id, protocol_id, container)
            return f"Протокол {entry['name']} удалён (контейнер {container})."

        result = ssh_exec.run(ssh, f"docker {action} {shlex.quote(container)}", timeout=60)
        if result.exit_code != 0:
            raise RuntimeError(result.stderr.strip() or f"docker {action} завершился с ошибкой.")
        return f"Протокол {entry['name']}: {action} выполнен."
    finally:
        ssh.close()


def _connect(target):
    return ssh_exec.connect(
        host=target.host,
        port=target.port,
        username=target.username,
        password=target.password,
        key=target.key,
    )


def _ensure_container_exists(ssh, container: str) -> None:
    out = ssh_exec.run(
        ssh, "docker ps -a --format '{{.Names}}' 2>/dev/null || true"
    ).stdout.splitlines()
    if container not in [name.strip() for name in out if name.strip()]:
        raise ValueError(f"Контейнер {container} не найден на сервере.")


def _collect_system(ssh, record: dict) -> SystemInfo:
    values = _parse_kv(ssh_exec.run(ssh, SYSTEM_SCRIPT, timeout=20).stdout)
    return SystemInfo(
        os=values.get("os") or None,
        kernel=values.get("kernel") or None,
        arch=values.get("arch") or None,
        cpu_model=values.get("cpu_model") or None,
        cores=_to_int(values.get("cores")),
        docker_version=values.get("docker_version") or None,
        public_ip=record.get("host") or values.get("public_ip") or None,
    )


def _collect_containers(ssh) -> list[ContainerInfo]:
    out = ssh_exec.run(
        ssh,
        "docker ps -a --format '{{.Names}}\t{{.Image}}\t{{.State}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null || true",
        timeout=20,
    ).stdout
    containers: list[ContainerInfo] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 4 or not parts[0].strip():
            continue
        containers.append(
            ContainerInfo(
                name=parts[0].strip(),
                image=parts[1].strip(),
                state=parts[2].strip(),
                status=parts[3].strip(),
                ports=parts[4].strip() if len(parts) > 4 else "",
            )
        )

    stats_out = ssh_exec.run(
        ssh,
        "docker stats --no-stream --format '{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}' 2>/dev/null || true",
        timeout=30,
    ).stdout
    stats: dict[str, tuple[Optional[float], str]] = {}
    for line in stats_out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            cpu = _to_float(parts[1].strip().rstrip("%"))
            stats[parts[0].strip()] = (cpu, parts[2].strip())
    for item in containers:
        if item.name in stats:
            item.cpu_percent, item.mem_usage = stats[item.name]
    return containers


def _build_protocols(server_id: str, record: dict, containers: list[ContainerInfo]) -> list[ProtocolInfo]:
    by_name = {c.name: c for c in containers}
    names = list(by_name.keys())
    result: list[ProtocolInfo] = []
    for entry in PROTOCOL_CATALOG:
        container_name = _match_container(entry, names)
        container = by_name.get(container_name) if container_name else None
        clients = 0
        if entry["id"] == "awg2" and container_name:
            clients = client_store.count_for_server(server_id)
        result.append(
            ProtocolInfo(
                id=entry["id"],
                name=entry["name"],
                description=entry["description"],
                installed=container is not None,
                running=bool(container and container.state == "running"),
                container=container_name,
                ports=container.ports if container else "",
                managed=bool(entry.get("managed")),
                can_install=bool(entry.get("can_install")) and container is None,
                clients_count=clients,
            )
        )
    return result


def _forget_protocol_in_store(server_id: str, protocol_id: str, container: str) -> None:
    """Убирает протокол из стора после удаления контейнера на VPS.

    Без этого `_has_xray` (карточки серверов) продолжает считать протокол
    установленным по осиротевшим installed_protocols/container_names.
    """
    record = server_store.get_record(server_id) or {}
    names = [n for n in (record.get("container_names") or []) if n != container]
    protocols = dict(record.get("installed_protocols") or {})
    protocols.pop(protocol_id, None)
    server_store.update_runtime(
        server_id, container_names=names, installed_protocols=protocols
    )


def _sync_xray_metadata(server_id: str, record: dict, containers: list[ContainerInfo]) -> None:
    """Сверяем amnezia-xray со стором в обе стороны.

    Есть контейнер → дописываем Xray в стор. Контейнера нет, а в сторе он числится
    → чистим расхождение (иначе карточки сервера врут после удаления Xray).

    Защита от ложного срабатывания: пустой список контейнеров означает, что docker
    не ответил (а не «Xray точно нет») — в этом случае ничего не трогаем.
    """
    if not containers:
        return
    xray = next((c for c in containers if c.name == "amnezia-xray"), None)
    names = list(record.get("container_names") or [])
    protocols = dict(record.get("installed_protocols") or {})

    if xray:
        changed = False
        if "amnezia-xray" not in names:
            names.append("amnezia-xray")
            changed = True
        if "xray" not in protocols:
            port = 443
            if xray.ports:
                import re

                match = re.search(r":(\d+)->", xray.ports)
                if match:
                    port = int(match.group(1))
            protocols["xray"] = {"port": port, "container": "amnezia-xray"}
            changed = True
        if changed:
            server_store.update_runtime(
                server_id, container_names=names, installed_protocols=protocols
            )
        return

    # Контейнера нет — снимаем осиротевшие метки Xray, если они остались в сторе.
    if ("amnezia-xray" in names) or ("xray" in protocols):
        names = [n for n in names if n != "amnezia-xray"]
        protocols.pop("xray", None)
        server_store.update_runtime(
            server_id, container_names=names, installed_protocols=protocols
        )


def _match_container(entry: dict, names: list[str]) -> Optional[str]:
    """Находит контейнер протокола по подстрокам, учитывая исключения.

    Сервисные контейнеры панели (web, db, dind) не считаем протоколами.
    """
    service_markers = ("panel", "web", "db", "dind", "postgres", "redis")
    excludes = entry.get("exclude", [])
    for name in names:
        low = name.lower()
        if any(marker in low for marker in service_markers):
            continue
        if any(ex in low for ex in excludes):
            continue
        if any(match in low for match in entry["matches"]):
            return name
    return None


def _collect_security(ssh, record: dict, server_id: str) -> list[SecurityCheck]:
    values = _parse_kv(ssh_exec.run(ssh, SECURITY_SCRIPT, timeout=20).stdout)
    checks: list[SecurityCheck] = []

    ufw = (values.get("ufw") or "").lower()
    if ufw == "active":
        checks.append(
            SecurityCheck(
                id="ufw", label="Файрвол UFW", status="ok", value="Активен",
                actionable=True, control="ufw", enabled=True,
            )
        )
    elif ufw == "inactive":
        checks.append(
            SecurityCheck(
                id="ufw",
                label="Файрвол UFW",
                status="warning",
                value="Выключен",
                recommendation="Включи UFW — панель сама откроет SSH, VPN и нужные порты, чтобы не потерять доступ.",
                actionable=True, control="ufw", enabled=False,
            )
        )
    else:
        checks.append(
            SecurityCheck(
                id="ufw",
                label="Файрвол UFW",
                status="unknown",
                value="Не установлен",
                recommendation="Включи UFW — панель установит и настроит его автоматически.",
                actionable=True, control="ufw", enabled=False,
            )
        )

    f2b = (values.get("fail2ban") or "").lower()
    if f2b == "active":
        checks.append(
            SecurityCheck(
                id="fail2ban", label="Fail2ban", status="ok", value="Активен",
                actionable=True, control="fail2ban", enabled=True,
            )
        )
    else:
        checks.append(
            SecurityCheck(
                id="fail2ban",
                label="Fail2ban",
                status="warning",
                value="Не активен",
                recommendation="Включи Fail2ban — защита SSH от перебора паролей (твой IP в исключениях).",
                actionable=True, control="fail2ban", enabled=False,
            )
        )

    permit_root = (values.get("permit_root") or "").lower()
    if permit_root in ("no", "prohibit-password", "without-password"):
        checks.append(
            SecurityCheck(
                id="root_login",
                label="Вход root по паролю",
                status="ok",
                value="Запрещён" if permit_root == "no" else "Только по ключу",
            )
        )
    elif permit_root == "yes":
        checks.append(
            SecurityCheck(
                id="root_login",
                label="Вход root по паролю",
                status="danger",
                value="Разрешён",
                recommendation="Разреши root только по ключу: PermitRootLogin prohibit-password.",
            )
        )
    else:
        checks.append(SecurityCheck(id="root_login", label="Вход root по паролю", status="unknown", value="—"))

    password_auth = (values.get("password_auth") or "").lower()
    if password_auth == "no":
        checks.append(SecurityCheck(id="password_auth", label="Парольный вход SSH", status="ok", value="Выключен"))
    elif password_auth == "yes":
        checks.append(
            SecurityCheck(
                id="password_auth",
                label="Парольный вход SSH",
                status="warning",
                value="Включён",
                recommendation="Перейди на SSH-ключи и выключи PasswordAuthentication.",
            )
        )
    else:
        checks.append(SecurityCheck(id="password_auth", label="Парольный вход SSH", status="unknown", value="—"))

    ssh_port = record.get("ssh_port") or 22
    if int(ssh_port) == 22:
        checks.append(
            SecurityCheck(
                id="ssh_port",
                label="Порт SSH",
                status="warning",
                value="22 (стандартный)",
                recommendation="Смени порт SSH со стандартного 22 — меньше шума от сканеров.",
            )
        )
    else:
        checks.append(SecurityCheck(id="ssh_port", label="Порт SSH", status="ok", value=str(ssh_port)))

    if (values.get("unattended") or "") == "installed":
        checks.append(
            SecurityCheck(
                id="updates", label="Автообновления безопасности", status="ok", value="Включены",
                actionable=True, control="updates", enabled=True,
            )
        )
    else:
        checks.append(
            SecurityCheck(
                id="updates",
                label="Автообновления безопасности",
                status="warning",
                value="Не настроены",
                recommendation="Включи автообновления — патчи безопасности без перезагрузки сервера.",
                actionable=True, control="updates", enabled=False,
            )
        )

    panel = panel_https_security_check(server_id)
    checks.append(
        SecurityCheck(
            id="panel_https",
            label="HTTPS панели",
            status=panel.get("status", "unknown"),
            value=panel.get("value", "—"),
            recommendation=panel.get("recommendation"),
        )
    )

    return checks


def _parse_kv(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip()
    return result


def _to_int(value: Optional[str]) -> Optional[int]:
    try:
        return int(value) if value not in (None, "") else None
    except ValueError:
        return None


def _to_float(value: Optional[str]) -> Optional[float]:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None
