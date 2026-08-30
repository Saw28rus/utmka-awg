"""Персистентность AWG-каскада на самих серверах (reboot / рестарт контейнера).

Правила каскада живут в netns контейнера и в хостовом iptables/socat — после
ребута они пропадают. Панельный reconcile поднимает каскад раз в ~90 с, но
только если панель доступна. Здесь — systemd + hook в start.sh контейнера:
каскад встаёт сам, даже когда панель выключена.
"""

from __future__ import annotations

import logging
import shlex

from app.services.amnezia_ssh import run_container_script, run_script, write_host_file
from app.services.cascade_split import HOST_IPSET_FILE, MARK, SET_NAME, SPLIT_RULE_PRIORITY, SPLIT_TABLE
from app.services.transit_allocator import TransitProfile

logger = logging.getLogger("utmka.cascade.persist")

HOST_DIR = "/opt/utmka/cascade"
ENTRY_UNIT = "utmka-awg-cascade.service"
EXIT_UNIT = "utmka-awg-cascade-exit.service"
SOCAT_UNIT = "utmka-cascade-socat.service"
HOOK_MARK = "utmka-cascade-up"
EXIT_LOCK_COMMENT = "utmka-exit-lock"
CONTAINER_UP = "/opt/amnezia/awg/utmka-cascade-up.sh"


def persist_entry(
    ssh,
    *,
    container: str,
    client_subnet: str,
    profile: TransitProfile,
    entry_ctn_ip: str,
    entry_public_ip: str,
    exit_public_ip: str,
    entry_up_script: str,
    entry_host_nat_script: str,
    split_enabled: bool,
) -> None:
    """Записать restore-скрипты и включить systemd на entry."""
    run_script(ssh, f"mkdir -p {HOST_DIR}", timeout=15)
    _write_container_script(ssh, container, CONTAINER_UP, entry_up_script)
    _ensure_start_hook(ssh, container)
    write_host_file(ssh, f"{HOST_DIR}/entry-host-nat.sh", _bash(entry_host_nat_script), mode="755")
    write_host_file(
        ssh,
        f"{HOST_DIR}/restore.sh",
        _entry_restore_sh(container, split_enabled=split_enabled, client_subnet=client_subnet),
        mode="755",
    )
    if split_enabled:
        write_host_file(
            ssh,
            f"{HOST_DIR}/split-up.sh",
            _split_restore_sh(container, client_subnet),
            mode="755",
        )
    write_host_file(ssh, f"/etc/systemd/system/{ENTRY_UNIT}", _oneshot_unit(f"{HOST_DIR}/restore.sh"), mode="644")
    _enable_unit(ssh, ENTRY_UNIT)
    logger.info("cascade persist: entry systemd enabled")


def persist_exit(
    ssh,
    *,
    container: str,
    profile: TransitProfile,
    exit_ctn_ip: str,
    entry_public_ip: str,
    exit_up_script: str,
) -> None:
    """Restore на exit: транзит в контейнере, socat, вход только с IP entry."""
    run_script(ssh, f"mkdir -p {HOST_DIR}", timeout=15)
    _write_container_script(ssh, container, CONTAINER_UP, exit_up_script)
    _ensure_start_hook(ssh, container)
    port = profile.transit_port
    write_host_file(
        ssh,
        f"{HOST_DIR}/socat.sh",
        _socat_sh(port, exit_ctn_ip),
        mode="755",
    )
    write_host_file(
        ssh,
        f"{HOST_DIR}/exit-lock.sh",
        _exit_lock_sh(port, entry_public_ip, add=True),
        mode="755",
    )
    write_host_file(
        ssh,
        f"{HOST_DIR}/restore.sh",
        _exit_restore_sh(container, port, exit_ctn_ip, entry_public_ip),
        mode="755",
    )
    write_host_file(ssh, f"/etc/systemd/system/{EXIT_UNIT}", _oneshot_unit(f"{HOST_DIR}/restore.sh"), mode="644")
    write_host_file(
        ssh,
        f"/etc/systemd/system/{SOCAT_UNIT}",
        _socat_unit(port, exit_ctn_ip),
        mode="644",
    )
    _enable_unit(ssh, EXIT_UNIT)
    _enable_unit(ssh, SOCAT_UNIT)
    # сразу применить lock (не ждать ребута)
    run_script(ssh, _exit_lock_sh(port, entry_public_ip, add=True), timeout=20)
    logger.info("cascade persist: exit systemd + lock enabled")


def remove_entry(ssh, container: str) -> None:
    _disable_unit(ssh, ENTRY_UNIT)
    _remove_start_hook(ssh, container)
    run_container_script(ssh, container, f"rm -f {CONTAINER_UP}", timeout=20)
    run_script(
        ssh,
        f"rm -f {HOST_DIR}/restore.sh {HOST_DIR}/entry-host-nat.sh {HOST_DIR}/split-up.sh "
        f"/etc/systemd/system/{ENTRY_UNIT}; systemctl daemon-reload >/dev/null 2>&1 || true",
        timeout=20,
    )


def remove_exit(ssh, container: str, transit_port: int, entry_public_ip: str) -> None:
    _disable_unit(ssh, SOCAT_UNIT)
    _disable_unit(ssh, EXIT_UNIT)
    run_script(ssh, _exit_lock_sh(transit_port, entry_public_ip, add=False), timeout=20)
    _remove_start_hook(ssh, container)
    run_container_script(ssh, container, f"rm -f {CONTAINER_UP}", timeout=20)
    run_script(
        ssh,
        f"rm -f {HOST_DIR}/restore.sh {HOST_DIR}/socat.sh {HOST_DIR}/exit-lock.sh "
        f"/etc/systemd/system/{EXIT_UNIT} /etc/systemd/system/{SOCAT_UNIT}; "
        f"systemctl daemon-reload >/dev/null 2>&1 || true",
        timeout=20,
    )


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------


def _bash(body: str) -> str:
    text = body.strip()
    if not text.startswith("#!"):
        text = "#!/bin/bash\n" + text
    if not text.endswith("\n"):
        text += "\n"
    return text


def _write_container_script(ssh, container: str, path: str, body: str) -> None:
    content = _bash(body)
    import base64

    b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
    inner = (
        f"printf '%s' {shlex.quote(b64)} | base64 -d > {shlex.quote(path)} "
        f"&& chmod 755 {shlex.quote(path)}"
    )
    res = run_container_script(ssh, container, inner, timeout=30)
    if res.exit_code != 0:
        logger.warning("не удалось записать %s в %s: %s", path, container, res.stderr.strip())


def _ensure_start_hook(ssh, container: str) -> None:
    """Вставить вызов cascade-up в start.sh контейнера (перед tail -f)."""
    simple = f"""
set +e
START=/opt/amnezia/start.sh
[ -f "$START" ] || exit 0
if grep -q '{HOOK_MARK} BEGIN' "$START" 2>/dev/null; then echo HOOK_OK; exit 0; fi
HOOK_FILE=/tmp/utmka-cascade-hook.sh
cat > "$HOOK_FILE" << 'HOOK'
# {HOOK_MARK} BEGIN
if [ -x {CONTAINER_UP} ]; then
  {CONTAINER_UP} || true
fi
# {HOOK_MARK} END
HOOK
if grep -q 'tail -f /dev/null' "$START"; then
  awk -v hfile="$HOOK_FILE" '
    /tail -f \\/dev\\/null/ {{ while ((getline line < hfile) > 0) print line; close(hfile) }}
    {{ print }}
  ' "$START" > "$START.new" && mv "$START.new" "$START"
else
  cat "$HOOK_FILE" >> "$START"
fi
chmod +x "$START" 2>/dev/null || true
rm -f "$HOOK_FILE"
echo HOOK_OK
"""
    res = run_container_script(ssh, container, simple, timeout=30)
    if "HOOK_OK" not in (res.stdout or ""):
        logger.warning("start.sh hook не встал: %s", (res.stderr or res.stdout or "").strip())


def _remove_start_hook(ssh, container: str) -> None:
    script = f"""
set +e
START=/opt/amnezia/start.sh
[ -f "$START" ] || exit 0
awk '
  /{HOOK_MARK} BEGIN/ {{ skip=1; next }}
  /{HOOK_MARK} END/ {{ skip=0; next }}
  skip {{ next }}
  {{ print }}
' "$START" > "$START.clean" 2>/dev/null && mv "$START.clean" "$START" || true
echo HOOK_REMOVED
"""
    run_container_script(ssh, container, script, timeout=20)


def _entry_restore_sh(container: str, *, split_enabled: bool, client_subnet: str) -> str:
    split_line = f"[ -x {HOST_DIR}/split-up.sh ] && {HOST_DIR}/split-up.sh || true" if split_enabled else "true"
    ctn = shlex.quote(container)
    return f"""#!/bin/bash
set -e
CTN={ctn}
for i in $(seq 1 60); do
  docker inspect -f '{{{{.State.Running}}}}' "$CTN" 2>/dev/null | grep -qx true && break
  sleep 2
done
docker inspect -f '{{{{.State.Running}}}}' "$CTN" 2>/dev/null | grep -qx true || exit 1
sleep 3
docker exec "$CTN" sh {CONTAINER_UP} || true
[ -x {HOST_DIR}/entry-host-nat.sh ] && {HOST_DIR}/entry-host-nat.sh || true
{split_line}
echo RESTORE_ENTRY_OK
"""


def _exit_restore_sh(container: str, port: int, ctn_ip: str, entry_ip: str) -> str:
    ctn = shlex.quote(container)
    return f"""#!/bin/bash
set -e
CTN={ctn}
for i in $(seq 1 60); do
  docker inspect -f '{{{{.State.Running}}}}' "$CTN" 2>/dev/null | grep -qx true && break
  sleep 2
done
docker inspect -f '{{{{.State.Running}}}}' "$CTN" 2>/dev/null | grep -qx true || exit 1
sleep 3
docker exec "$CTN" sh {CONTAINER_UP} || true
[ -x {HOST_DIR}/exit-lock.sh ] && {HOST_DIR}/exit-lock.sh || true
systemctl restart {SOCAT_UNIT} >/dev/null 2>&1 || {HOST_DIR}/socat.sh || true
echo RESTORE_EXIT_OK
"""


def _split_restore_sh(container: str, client_subnet: str) -> str:
    ctn = shlex.quote(container)
    cs = shlex.quote(client_subnet)
    ipset_file = shlex.quote(HOST_IPSET_FILE)
    return f"""#!/bin/bash
set -e
CTN={ctn}
PID=$(docker inspect -f '{{{{.State.Pid}}}}' "$CTN" 2>/dev/null || true)
[ -n "$PID" ] && [ "$PID" != "0" ] || exit 0
[ -f {ipset_file} ] || exit 0
NSE="nsenter -t $PID -n"
$NSE ipset restore -! < {ipset_file} || true
$NSE iptables -t mangle -C PREROUTING -s {cs} -m set --match-set {SET_NAME} dst -j MARK --set-mark {MARK} 2>/dev/null \\
  || $NSE iptables -t mangle -A PREROUTING -s {cs} -m set --match-set {SET_NAME} dst -j MARK --set-mark {MARK}
$NSE ip rule del fwmark {MARK} lookup {SPLIT_TABLE} priority {SPLIT_RULE_PRIORITY} 2>/dev/null || true
$NSE ip rule add fwmark {MARK} lookup {SPLIT_TABLE} priority {SPLIT_RULE_PRIORITY}
$NSE ip route flush cache 2>/dev/null || true
echo SPLIT_RESTORE_OK
"""


def _socat_sh(port: int, ctn_ip: str) -> str:
    p = str(int(port))
    ip = shlex.quote(ctn_ip)
    return f"""#!/bin/bash
set -e
command -v socat >/dev/null 2>&1 || (apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq socat)
pkill -f 'socat UDP4-LISTEN:{p},' 2>/dev/null || true
exec socat UDP4-LISTEN:{p},fork,reuseaddr UDP4:{ip}:{p}
"""


def _socat_unit(port: int, ctn_ip: str) -> str:
    p = str(int(port))
    ip = ctn_ip
    return f"""[Unit]
Description=UTMka cascade UDP socat (exit)
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=simple
Restart=always
RestartSec=2
ExecStartPre=/bin/sh -c "command -v socat >/dev/null || (apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq socat)"
ExecStart=/usr/bin/socat UDP4-LISTEN:{p},fork,reuseaddr UDP4:{ip}:{p}

[Install]
WantedBy=multi-user.target
"""


def _exit_lock_sh(port: int, entry_ip: str, *, add: bool) -> str:
    """Транзитный UDP на exit принимает пакеты только с публичного IP входа."""
    ip = (entry_ip or "").strip()
    if not ip:
        return "#!/bin/bash\necho LOCK_SKIP_NO_IP\n"
    p = str(int(port))
    src = shlex.quote(ip)
    cm = EXIT_LOCK_COMMENT
    drop = (
        f"-p udp --dport {p} ! -s {src} -m comment --comment {cm} -j DROP"
    )
    if add:
        return f"""#!/bin/bash
set +e
iptables -C INPUT {drop} 2>/dev/null || iptables -I INPUT 1 {drop}
iptables -C DOCKER-USER {drop} 2>/dev/null || iptables -I DOCKER-USER 1 {drop} 2>/dev/null || true
echo LOCK_OK
"""
    return f"""#!/bin/bash
set +e
iptables -D INPUT {drop} 2>/dev/null || true
iptables -D DOCKER-USER {drop} 2>/dev/null || true
echo LOCK_DOWN
"""


def _oneshot_unit(exec_start: str) -> str:
    return f"""[Unit]
Description=UTMka AWG cascade restore
After=docker.service network-online.target
Requires=docker.service
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
TimeoutStartSec=180
ExecStart={exec_start}

[Install]
WantedBy=multi-user.target
"""


def _enable_unit(ssh, unit: str) -> None:
    run_script(
        ssh,
        f"systemctl daemon-reload >/dev/null 2>&1; systemctl enable --now {shlex.quote(unit)} >/dev/null 2>&1 || "
        f"systemctl enable {shlex.quote(unit)} >/dev/null 2>&1 || true",
        timeout=30,
    )


def _disable_unit(ssh, unit: str) -> None:
    run_script(
        ssh,
        f"systemctl disable --now {shlex.quote(unit)} >/dev/null 2>&1 || true; "
        f"rm -f /etc/systemd/system/{shlex.quote(unit)}; systemctl daemon-reload >/dev/null 2>&1 || true",
        timeout=20,
    )
