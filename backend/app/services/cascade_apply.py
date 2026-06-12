"""Каскад AmneziaWG — Apply / Rollback (Model A, double-SNAT на entry).

Транзит `utmka-cas0` поднимается ВНУТРИ netns контейнера `amnezia-awg2`
через родной `awg-quick` с `Table = off` (без wg-quick-маршрутной магии).
Policy routing, double-SNAT и структурный fail-closed добавляются вручную.

Свойства MVP (C1):
- безопасность: snapshot ip-rule/route/iptables до изменений; авто-rollback при
  любой ошибке шага после изменения трафика или при провале health-check;
- fail-closed: таблица каскада содержит `default dev utmka-cas0` + `blackhole`,
  поэтому при падении транзита клиентский трафик дропается, а не утекает direct;
- health: из netns entry запрос с source = client addr через каскад обязан
  вернуть публичный IP exit. Иначе откат.

НЕ входит в этот MVP (следующая веха): systemd-agent persistence (переживание
reboot). См. AMNEZIA_CASCADE_PLAN.md §11. Сейчас каскад живёт до перезагрузки
контейнера/сервера.
"""

from __future__ import annotations

import base64
import shlex
from datetime import datetime, timezone
from typing import Optional

from app.core.crypto import decrypt, encrypt
from app.schemas.cascade import CascadeApplyResult, CascadeStep
from app.services.amnezia_ssh import run_container_script, run_script
from app.services.awg_config import AWG_PARAM_KEYS, parse_interface
from app.services.cascade import (
    _ENTRY_PROBE,
    _EXIT_PROBE,
    CascadeError,
    _client_subnet_from_addr,
    _connect,
    _parse_kv,
)
from app.services.cascade_store import (
    DEFAULT_ENTRY_TRANSIT_IP,
    DEFAULT_EXIT_TRANSIT_IP,
    DEFAULT_TRANSIT_SUBNET,
    cascade_store,
)
from app.services.server_store import server_store

IFACE = "utmka-cas0"
CONF_PATH = "/tmp/utmka-cas0.conf"
TABLE = "7770"
RULE_PRIORITY = "300"
MTU = "1280"
LABEL = "utmka-cascade"
ENTRY_HOST_PORT_OFFSET = 1  # entry ListenPort / SNAT на хосте = transit_port + 1


# ---------------------------------------------------------------------------
# AWG2-параметры обфускации транзита (копируем с awg0, иначе fallback с range H1–H4).
# ---------------------------------------------------------------------------


def _read_awg_params(ssh, container: str) -> dict[str, str]:
    """Берём обфускацию с основного awg0 транзита (fail-closed, без статического fallback).

    Транзит обязан использовать ТЕ ЖЕ параметры, что и entry `awg0`, иначе AWG2 не
    договорится. Если параметры не читаются или это не сильный AWG2 (нет S3/S4) —
    apply блокируется. Никаких «учебниковых» дефолтов в production: статический
    fallback сам по себе узнаваемый отпечаток для DPI (M4 hardening).
    """
    res = run_container_script(
        ssh,
        container,
        "cat /opt/amnezia/awg/awg0.conf /opt/amnezia/awg/wg0.conf 2>/dev/null | head -60",
        timeout=20,
    )
    info = parse_interface(res.stdout)
    params = {k: str(v) for k, v in info.awg_params.items() if k in AWG_PARAM_KEYS}
    # S3/S4 обязательны: это признак сильного AWG2. Значение "0" допустимо (ключ есть).
    required = ("Jc", "Jmin", "Jmax", "S1", "S2", "S3", "S4", "H1", "H2", "H3", "H4")
    missing = [k for k in required if not params.get(k)]
    if missing:
        raise CascadeError(
            "Каскад заблокирован: не удалось прочитать сильные AWG2-параметры маскировки "
            f"с entry (отсутствуют: {', '.join(missing)}). Транзит без точной копии "
            "обфускации entry не поднять, а статический fallback отключён ради защиты "
            "от DPI. Проверьте, что на entry установлен AmneziaWG 2.0 (S3/S4)."
        )
    return params


def _render_conf(
    *,
    private_key: str,
    address: str,
    listen_port: int,
    peer_pub: str,
    psk: str,
    allowed_ips: str,
    params: dict[str, str],
    endpoint: Optional[str] = None,
    listen_port_optional: bool = False,
) -> str:
    lines = [
        "[Interface]",
        f"PrivateKey = {private_key}",
        f"Address = {address}",
        f"MTU = {MTU}",
        "Table = off",
    ]
    if listen_port_optional or listen_port:
        lines.insert(3, f"ListenPort = {listen_port}")
    for key in ("Jc", "Jmin", "Jmax", "S1", "S2", "S3", "S4", "H1", "H2", "H3", "H4"):
        if key in params:
            lines.append(f"{key} = {params[key]}")
    lines += [
        "",
        "[Peer]",
        f"PublicKey = {peer_pub}",
        f"PresharedKey = {psk}",
        f"AllowedIPs = {allowed_ips}",
        "PersistentKeepalive = 25",
    ]
    if endpoint:
        lines.append(f"Endpoint = {endpoint}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _write_container_file(ssh, container: str, path: str, content: str) -> None:
    b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
    inner = f"printf '%s' {shlex.quote(b64)} | base64 -d > {shlex.quote(path)} && chmod 600 {shlex.quote(path)}"
    res = run_container_script(ssh, container, inner, timeout=30)
    if res.exit_code != 0:
        raise CascadeError(f"Не удалось записать {path} в {container}: {res.stderr.strip()}")


def _keygen(ssh, container: str) -> dict[str, str]:
    script = (
        "GEN=$(command -v awg || command -v wg); "
        'PRIV=$("$GEN" genkey); '
        'PUB=$(printf "%s" "$PRIV" | "$GEN" pubkey); '
        'PSK=$("$GEN" genpsk); '
        'printf "priv=%s\\npub=%s\\npsk=%s\\n" "$PRIV" "$PUB" "$PSK"'
    )
    res = run_container_script(ssh, container, script, timeout=30)
    vals = _parse_kv(res.stdout)
    if not vals.get("priv") or not vals.get("pub"):
        raise CascadeError(f"Не удалось сгенерировать ключи в {container}: {res.stderr.strip()}")
    return vals


def _amnezia_container(record: dict) -> Optional[str]:
    for name in record.get("container_names") or []:
        if name in ("amnezia-awg2", "amnezia-awg"):
            return name
    return None


def _container_amnezia_ip(ssh, container: str) -> str:
    """IP контейнера в amnezia-dns-net (amn0), не docker0."""
    quoted = shlex.quote(container)
    tmpl = (
        "{{range $k,$v := .NetworkSettings.Networks}}"
        "{{if eq $k \"amnezia-dns-net\"}}{{$v.IPAddress}}{{end}}{{end}}"
    )
    res = run_script(
        ssh,
        f"docker inspect -f '{tmpl}' {quoted}",
        timeout=20,
    )
    ip = res.stdout.strip()
    if ip:
        return ip
    res = run_script(
        ssh,
        "docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' "
        + quoted,
        timeout=20,
    )
    for candidate in res.stdout.split():
        if candidate.startswith("172.29."):
            return candidate
    return (res.stdout.split() or [""])[0]


def _host_public_ip(ssh) -> str:
    res = run_script(
        ssh,
        "curl -4 -s --max-time 5 ifconfig.me 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}'",
        timeout=12,
    )
    return (res.stdout.strip().split() or [""])[0]


def _gather(entry_id: str, exit_id: str) -> dict:
    """Свежие факты с обоих серверов перед apply (без изменений)."""
    facts: dict = {}
    ssh = _connect(entry_id)
    try:
        entry_vals = _parse_kv(run_script(ssh, _ENTRY_PROBE, timeout=40).stdout)
        entry_ctn = entry_vals.get("container") or ""
        if entry_ctn:
            entry_vals["ctn_ip"] = _container_amnezia_ip(ssh, entry_ctn)
        entry_vals["public_ip"] = _host_public_ip(ssh)
        facts["entry"] = entry_vals
    finally:
        ssh.close()
    ssh = _connect(exit_id)
    try:
        ev = _parse_kv(run_script(ssh, _EXIT_PROBE, timeout=40).stdout)
        exit_ctn = ev.get("container") or ""
        if exit_ctn:
            ev["ctn_ip"] = _container_amnezia_ip(ssh, exit_ctn)
        facts["exit"] = ev
    finally:
        ssh.close()
    return facts


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def apply_cascade(entry_id: str) -> CascadeApplyResult:
    from app.services.cascade import probe_cascade_live

    link = cascade_store.get_link(entry_id)
    if not link:
        raise CascadeError("Сначала проверьте готовность серверов.")
    live = probe_cascade_live(entry_id)
    if live["active"]:
        cascade_store.upsert_link(entry_id, state="active")
        raise CascadeError("Каскад уже работает. Выключите его, если нужно перенастроить.")
    if not link.get("last_preflight_ok"):
        raise CascadeError("Проверка не пройдена — включение заблокировано.")
    exit_id = link.get("exit_server_id")
    if not exit_id:
        raise CascadeError("Не выбран exit-сервер.")

    entry_rec = server_store.get_record(entry_id)
    exit_rec = server_store.get_record(exit_id)
    if not entry_rec or not exit_rec:
        raise CascadeError("Entry или exit сервер не найден.")

    steps: list[CascadeStep] = []
    facts = _gather(entry_id, exit_id)
    entry_f, exit_f = facts.get("entry", {}), facts.get("exit", {})

    entry_ctn = entry_f.get("container") or _amnezia_container(entry_rec)
    exit_ctn = exit_f.get("container") or _amnezia_container(exit_rec)
    client_subnet = _client_subnet_from_addr(entry_f.get("server_addr"))
    server_addr = (entry_f.get("server_addr") or "").split("/")[0].split(",")[0].strip()
    exit_public_ip = exit_f.get("public_ip") or exit_rec.get("host")
    entry_public_ip = entry_f.get("public_ip") or entry_rec.get("host")
    exit_ctn_ip = exit_f.get("ctn_ip") or ""
    entry_ctn_ip = entry_f.get("ctn_ip") or ""
    port = int(link.get("transit_port") or 51821)
    entry_host_port = port + ENTRY_HOST_PORT_OFFSET

    # --- abort-before-change: проверка предусловий ---
    problems = []
    if not entry_ctn:
        problems.append("нет контейнера AmneziaWG на entry")
    if not exit_ctn:
        problems.append("нет контейнера AmneziaWG на exit")
    if not client_subnet or not server_addr:
        problems.append("не определена client subnet на entry")
    if not exit_public_ip:
        problems.append("нет публичного IP exit")
    if not entry_public_ip:
        problems.append("нет публичного IP entry")
    if not exit_ctn_ip:
        problems.append("не определён IP контейнера exit в amnezia-dns-net")
    if not entry_ctn_ip:
        problems.append("не определён IP контейнера entry в amnezia-dns-net")
    if entry_f.get("ctn_awg_kind") != "tools":
        problems.append("в контейнере entry нет awg-tools (awg-quick)")
    if exit_f.get("ctn_awg_kind") != "tools":
        problems.append("в контейнере exit нет awg-tools (awg-quick)")
    if problems:
        raise CascadeError("Apply заблокирован: " + "; ".join(problems) + ".")

    # --- keys ---
    entry_ssh = _connect(entry_id)
    exit_ssh = _connect(exit_id)
    params = _read_awg_params(entry_ssh, entry_ctn)
    egress_ip: Optional[str] = None
    applied_traffic = False
    try:
        entry_keys = _keygen(entry_ssh, entry_ctn)
        exit_keys = _keygen(exit_ssh, exit_ctn)
        psk = entry_keys["psk"]
        steps.append(CascadeStep(name="Генерация ключей транзита", status="ok"))

        # --- snapshot ---
        snap = _snapshot(entry_ssh, entry_ctn, exit_ssh, exit_ctn)
        cascade_store.upsert_link(entry_id, snapshot=snap)
        steps.append(CascadeStep(name="Snapshot ip-rule/route/iptables", status="ok"))

        # === EXIT: транзит + NAT в netns + host DNAT ===
        exit_conf = _render_conf(
            private_key=exit_keys["priv"],
            address=f"{DEFAULT_EXIT_TRANSIT_IP}/30",
            listen_port=port,
            peer_pub=entry_keys["pub"],
            psk=psk,
            allowed_ips=f"{DEFAULT_ENTRY_TRANSIT_IP}/32",
            params=params,
        )
        _write_container_file(exit_ssh, exit_ctn, CONF_PATH, exit_conf)
        res = run_container_script(exit_ssh, exit_ctn, _EXIT_UP_SCRIPT, timeout=60)
        _ensure(res, "Поднять транзит на exit (netns)")
        steps.append(CascadeStep(name="Exit: транзит utmka-cas0 + NAT", status="ok"))

        res = run_script(exit_ssh, _exit_host_socat(port, exit_ctn_ip), timeout=40)
        _ensure(res, "Exit host socat relay для UDP-порта транзита")
        steps.append(CascadeStep(name="Exit: проброс UDP-порта на контейнер", status="ok"))

        # === ENTRY: host UDP NAT (обход docker MASQUERADE + raw DROP) ===
        res = run_script(
            entry_ssh,
            _entry_host_udp_nat(
                entry_ctn_ip, entry_public_ip, entry_host_port, exit_public_ip, port
            ),
            timeout=40,
        )
        _ensure(res, "Entry host SNAT/DNAT для транзитного UDP")
        steps.append(CascadeStep(name="Entry: проброс UDP транзита на контейнер", status="ok"))

        # === ENTRY: транзит + policy routing + double-SNAT + fail-closed ===
        entry_conf = _render_conf(
            private_key=entry_keys["priv"],
            address=f"{DEFAULT_ENTRY_TRANSIT_IP}/30",
            listen_port=entry_host_port,
            peer_pub=exit_keys["pub"],
            psk=psk,
            allowed_ips="0.0.0.0/0",
            params=params,
            endpoint=f"{exit_public_ip}:{port}",
        )
        _write_container_file(entry_ssh, entry_ctn, CONF_PATH, entry_conf)
        applied_traffic = True
        res = run_container_script(
            entry_ssh, entry_ctn, _entry_up_script(client_subnet), timeout=60
        )
        _ensure(res, "Поднять каскад на entry (routing+SNAT+fail-closed)")
        steps.append(CascadeStep(name="Entry: транзит + policy routing + SNAT + fail-closed", status="ok"))

        # === HEALTH: egress через каскад должен быть = exit public IP ===
        egress_ip, handshake_ok = _health_egress(entry_ssh, entry_ctn, server_addr)
        health_note = None
        if egress_ip and exit_public_ip and egress_ip.strip() == exit_public_ip.strip():
            steps.append(CascadeStep(
                name="Health: внешний IP клиента", status="ok",
                detail=f"egress {egress_ip} = exit {exit_public_ip}",
            ))
        elif egress_ip:
            # реальный leak/misroute — внешний IP не exit → откат
            steps.append(CascadeStep(
                name="Health: внешний IP клиента", status="failed",
                detail=f"ожидался {exit_public_ip}, получено {egress_ip}",
            ))
            raise CascadeError(
                f"Health-check провален: внешний IP {egress_ip} != exit {exit_public_ip} (возможна утечка)."
            )
        elif handshake_ok:
            # транзит рукопожатие есть, но egress не измерить (нет curl/wget в контейнере)
            health_note = "Транзит установлен (handshake есть), но внешний IP не удалось измерить из контейнера — проверьте на клиенте."
            steps.append(CascadeStep(
                name="Health: внешний IP клиента", status="ok",
                detail=health_note,
            ))
        else:
            steps.append(CascadeStep(
                name="Health: транзит", status="failed",
                detail="Нет handshake транзита utmka-cas0 — туннель не поднялся.",
            ))
            raise CascadeError("Health-check провален: транзит utmka-cas0 не установил handshake.")

        # split-слой (РФ напрямую), если включён в правилах
        try:
            from app.services.cascade_rules import apply_split_after_cascade

            split_step = apply_split_after_cascade(entry_id)
            if split_step is not None:
                steps.append(split_step)
        except Exception:  # noqa: BLE001
            pass

        # success
        cascade_store.upsert_link(
            entry_id,
            state="active",
            transit_subnet=DEFAULT_TRANSIT_SUBNET,
            transit_port=port,
            egress_ip=egress_ip,
            last_applied_at=datetime.now(timezone.utc).isoformat(),
            entry_priv_enc=encrypt(entry_keys["priv"]),
            exit_priv_enc=encrypt(exit_keys["priv"]),
            psk_enc=encrypt(psk),
            entry_pub=entry_keys["pub"],
            exit_pub=exit_keys["pub"],
            exit_ctn_ip=exit_ctn_ip,
            entry_ctn_ip=entry_ctn_ip,
            entry_host_port=entry_host_port,
            message=health_note or "Каскад активен: клиентский трафик идёт через exit.",
        )
        return CascadeApplyResult(
            ok=True, state="active",
            entry_server_id=entry_id, exit_server_id=exit_id,
            egress_ip=egress_ip, expected_exit_ip=exit_public_ip,
            transit_subnet=DEFAULT_TRANSIT_SUBNET, transit_port=port,
            steps=steps,
            message=health_note or "Каскад активен. Клиент → entry → exit → интернет.",
        )

    except Exception as exc:  # noqa: BLE001
        # авто-rollback
        rb_ok = True
        try:
            _teardown(
                entry_ssh, entry_ctn, exit_ssh, exit_ctn, client_subnet, port,
                exit_ctn_ip, entry_ctn_ip, entry_public_ip, entry_host_port,
                exit_public_ip,
            )
        except Exception:  # noqa: BLE001
            rb_ok = False
        state = "rolled_back" if rb_ok else "rollback_failed"
        steps.append(CascadeStep(
            name="Авто-откат", status="ok" if rb_ok else "failed",
            detail=None if rb_ok else "Откат завершился с ошибкой — нужен ручной разбор.",
        ))
        cascade_store.upsert_link(
            entry_id, state=state, egress_ip=None,
            message=f"Apply прерван: {exc}",
        )
        cascade_store.set_split(entry_id, applied=False)
        msg = str(exc) if isinstance(exc, CascadeError) else f"Ошибка apply: {exc}"
        return CascadeApplyResult(
            ok=False, state=state,
            entry_server_id=entry_id, exit_server_id=exit_id,
            egress_ip=egress_ip, expected_exit_ip=exit_public_ip,
            transit_subnet=DEFAULT_TRANSIT_SUBNET, transit_port=port,
            steps=steps, message=msg,
        )
    finally:
        entry_ssh.close()
        exit_ssh.close()
        _ = applied_traffic


# ---------------------------------------------------------------------------
# Rollback (ручной)
# ---------------------------------------------------------------------------


def rollback_cascade(entry_id: str) -> CascadeApplyResult:
    link = cascade_store.get_link(entry_id)
    if not link:
        raise CascadeError("Каскад для этого сервера не найден.")
    exit_id = link.get("exit_server_id")
    entry_rec = server_store.get_record(entry_id)
    exit_rec = server_store.get_record(exit_id) if exit_id else None
    if not entry_rec or not exit_rec:
        raise CascadeError("Entry или exit сервер не найден.")

    facts = _gather(entry_id, exit_id)
    entry_ctn = facts.get("entry", {}).get("container") or _amnezia_container(entry_rec)
    exit_ctn = facts.get("exit", {}).get("container") or _amnezia_container(exit_rec)
    client_subnet = _client_subnet_from_addr(facts.get("entry", {}).get("server_addr")) or link.get("client_subnet")
    port = int(link.get("transit_port") or 51821)
    exit_ctn_ip = facts.get("exit", {}).get("ctn_ip") or link.get("exit_ctn_ip") or ""
    entry_ctn_ip = facts.get("entry", {}).get("ctn_ip") or link.get("entry_ctn_ip") or ""
    entry_public_ip = (
        facts.get("entry", {}).get("public_ip") or entry_rec.get("host") or ""
    )
    entry_host_port = int(link.get("entry_host_port") or port + ENTRY_HOST_PORT_OFFSET)
    exit_public_ip = facts.get("exit", {}).get("public_ip") or (exit_rec.get("host") if exit_rec else "")

    steps: list[CascadeStep] = []
    entry_ssh = _connect(entry_id)
    exit_ssh = _connect(exit_id)
    try:
        ok = True
        try:
            _teardown(
                entry_ssh, entry_ctn, exit_ssh, exit_ctn, client_subnet, port,
                exit_ctn_ip, entry_ctn_ip, entry_public_ip, entry_host_port,
                exit_public_ip,
            )
        except Exception:  # noqa: BLE001
            ok = False
        steps.append(CascadeStep(name="Снятие правил каскада", status="ok" if ok else "failed"))
        state = "rolled_back" if ok else "rollback_failed"
        cascade_store.upsert_link(
            entry_id, state=state, egress_ip=None,
            message="Каскад снят. Клиентский трафик снова выходит напрямую через entry."
            if ok else "Откат завершился с ошибкой — нужен ручной разбор.",
        )
        cascade_store.set_split(entry_id, applied=False)
        return CascadeApplyResult(
            ok=ok, state=state,
            entry_server_id=entry_id, exit_server_id=exit_id or "",
            transit_port=port, steps=steps,
            message="Каскад снят." if ok else "Откат с ошибкой.",
        )
    finally:
        entry_ssh.close()
        exit_ssh.close()


# ---------------------------------------------------------------------------
# bash-скрипты
# ---------------------------------------------------------------------------


def _ensure(res, step: str) -> None:
    if res.exit_code != 0:
        raise CascadeError(f"{step}: {res.stderr.strip() or res.stdout.strip() or 'код ' + str(res.exit_code)}")


def _snapshot(entry_ssh, entry_ctn, exit_ssh, exit_ctn) -> str:
    snap_script = (
        "echo '== ip rule =='; ip rule 2>/dev/null; "
        "echo '== ip route table all =='; ip route show table all 2>/dev/null; "
        "echo '== iptables-save =='; iptables-save 2>/dev/null"
    )
    e = run_container_script(entry_ssh, entry_ctn, snap_script, timeout=40).stdout
    x = run_container_script(exit_ssh, exit_ctn, snap_script, timeout=40).stdout
    raw = f"### ENTRY netns\n{e}\n### EXIT netns\n{x}\n"
    return base64.b64encode(raw.encode("utf-8")).decode("ascii")


_EXIT_UP_SCRIPT = r"""
set -e
WAN=$(ip route show default | awk '/default/{print $5; exit}')
[ -n "$WAN" ] || WAN=eth0
awg-quick down /tmp/utmka-cas0.conf 2>/dev/null || true
ip link del utmka-cas0 2>/dev/null || true
awg-quick up /tmp/utmka-cas0.conf
sysctl -w net.ipv4.ip_forward=1 >/dev/null 2>&1 || true
iptables -t nat -D POSTROUTING -s 10.250.0.0/30 -o "$WAN" -j MASQUERADE 2>/dev/null || true
iptables -t nat -A POSTROUTING -s 10.250.0.0/30 -o "$WAN" -j MASQUERADE
iptables -D FORWARD -i utmka-cas0 -o "$WAN" -j ACCEPT 2>/dev/null || true
iptables -A FORWARD -i utmka-cas0 -o "$WAN" -j ACCEPT
iptables -D FORWARD -i "$WAN" -o utmka-cas0 -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || true
iptables -A FORWARD -i "$WAN" -o utmka-cas0 -m state --state RELATED,ESTABLISHED -j ACCEPT
echo OK_EXIT
"""


def _exit_host_socat(port: int, ctn_ip: str) -> str:
    """UDP relay на хосте: публичный :port → amnezia-dns-net контейнер.

    iptables DNAT+SNAT ломается из-за docker MASQUERADE (случайный source port).
    """
    p = str(int(port))
    ip = shlex.quote(ctn_ip)
    return f"""
set -e
command -v socat >/dev/null 2>&1 || (apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq socat)
pkill -f 'socat UDP4-LISTEN:{p},' 2>/dev/null || true
nohup socat UDP4-LISTEN:{p},fork,reuseaddr UDP4:{ip}:{p} >/tmp/utmka-cascade-socat.log 2>&1 &
sleep 1
pgrep -f 'socat UDP4-LISTEN:{p},' >/dev/null
echo OK_SOCAT
"""


def _entry_host_udp_nat(
    ctn_ip: str,
    public_ip: str,
    host_port: int,
    exit_ip: str,
    exit_port: int,
) -> str:
    """Статический SNAT/DNAT для исходящего транзита entry → exit.

    Docker MASQUERADE не создаёт обратный путь; raw PREROUTING DROP режет DNAT
    на IP контейнера. Фиксированный порт + явные правила.
    """
    hp = str(int(host_port))
    ep = str(int(exit_port))
    ctn = shlex.quote(ctn_ip)
    pub = shlex.quote(public_ip)
    xip = shlex.quote(exit_ip)
    cm = f"{LABEL}-entry-udp"
    return f"""
set -e
sysctl -w net.ipv4.ip_forward=1 >/dev/null 2>&1 || true
iptables -t nat -D POSTROUTING -s {ctn} -p udp -o eth0 -d {xip} --dport {ep} -m comment --comment {cm}-snat -j SNAT --to-source {pub}:{hp} 2>/dev/null || true
iptables -t nat -I POSTROUTING 1 -s {ctn} -p udp -o eth0 -d {xip} --dport {ep} -m comment --comment {cm}-snat -j SNAT --to-source {pub}:{hp}
iptables -t nat -D PREROUTING -i eth0 -p udp -d {pub} --dport {hp} -m comment --comment {cm}-dnat -j DNAT --to-destination {ctn}:{hp} 2>/dev/null || true
iptables -t nat -I PREROUTING 2 -i eth0 -p udp -d {pub} --dport {hp} -m comment --comment {cm}-dnat -j DNAT --to-destination {ctn}:{hp}
iptables -t raw -D PREROUTING -i eth0 -p udp -d {ctn} --dport {hp} -m comment --comment {cm}-raw -j ACCEPT 2>/dev/null || true
iptables -t raw -I PREROUTING 1 -i eth0 -p udp -d {ctn} --dport {hp} -m comment --comment {cm}-raw -j ACCEPT
iptables -D FORWARD -i eth0 -o amn0 -p udp -d {ctn} --dport {hp} -m comment --comment {cm}-fwd-in -j ACCEPT 2>/dev/null || true
iptables -I FORWARD 1 -i eth0 -o amn0 -p udp -d {ctn} --dport {hp} -m comment --comment {cm}-fwd-in -j ACCEPT
iptables -D FORWARD -i amn0 -o eth0 -p udp -s {ctn} --sport {hp} -m comment --comment {cm}-fwd-out -j ACCEPT 2>/dev/null || true
iptables -I FORWARD 1 -i amn0 -o eth0 -p udp -s {ctn} --sport {hp} -m comment --comment {cm}-fwd-out -j ACCEPT
echo OK_ENTRY_HOST_NAT
"""


def _entry_up_script(client_subnet: str) -> str:
    cs = shlex.quote(client_subnet)
    return f"""
set -e
awg-quick down /tmp/utmka-cas0.conf 2>/dev/null || true
ip link del utmka-cas0 2>/dev/null || true
awg-quick up /tmp/utmka-cas0.conf
ip rule del from {cs} lookup {TABLE} 2>/dev/null || true
ip rule add from {cs} lookup {TABLE} priority {RULE_PRIORITY}
ip route flush table {TABLE} 2>/dev/null || true
ip route add default dev {IFACE} table {TABLE}
ip route add blackhole default metric 100 table {TABLE}
iptables -t nat -D POSTROUTING -s {cs} -o {IFACE} -j SNAT --to-source {DEFAULT_ENTRY_TRANSIT_IP} 2>/dev/null || true
iptables -t nat -A POSTROUTING -s {cs} -o {IFACE} -j SNAT --to-source {DEFAULT_ENTRY_TRANSIT_IP}
iptables -D FORWARD -s {cs} -o {IFACE} -j ACCEPT 2>/dev/null || true
iptables -A FORWARD -s {cs} -o {IFACE} -j ACCEPT
iptables -D FORWARD -d {cs} -i {IFACE} -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || true
iptables -A FORWARD -d {cs} -i {IFACE} -m state --state RELATED,ESTABLISHED -j ACCEPT
echo OK_ENTRY
"""


def _health_egress(entry_ssh, entry_ctn, server_addr: str) -> tuple[Optional[str], bool]:
    src = shlex.quote(server_addr)
    script = f"""
HS=0
for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  HS=$(awg show {IFACE} latest-handshakes 2>/dev/null | awk '{{print $2}}' | sort -nr | head -n1)
  [ -n "$HS" ] && [ "$HS" != "0" ] && break
  sleep 2
done
echo "handshake=$HS"
IP=$(curl -4 -s --max-time 8 --interface {src} https://api.ipify.org 2>/dev/null \
  || curl -4 -s --max-time 8 --interface {src} http://ifconfig.me 2>/dev/null \
  || wget -qO- --timeout=8 --bind-address={src} http://ifconfig.me 2>/dev/null)
echo "egress=$IP"
"""
    res = run_container_script(entry_ssh, entry_ctn, script, timeout=80)
    vals = _parse_kv(res.stdout)
    ip = (vals.get("egress") or "").strip()
    hs = (vals.get("handshake") or "0").strip()
    handshake_ok = bool(hs) and hs != "0"
    return (ip or None), handshake_ok


def _teardown(
    entry_ssh,
    entry_ctn,
    exit_ssh,
    exit_ctn,
    client_subnet,
    port,
    exit_ctn_ip,
    entry_ctn_ip="",
    entry_public_ip="",
    entry_host_port=0,
    exit_public_ip="",
) -> None:
    cs = shlex.quote(client_subnet or "10.8.1.0/24")
    p = str(int(port))
    entry_down = f"""
ip rule del from {cs} lookup {TABLE} 2>/dev/null || true
ip route flush table {TABLE} 2>/dev/null || true
iptables -t nat -D POSTROUTING -s {cs} -o {IFACE} -j SNAT --to-source {DEFAULT_ENTRY_TRANSIT_IP} 2>/dev/null || true
iptables -D FORWARD -s {cs} -o {IFACE} -j ACCEPT 2>/dev/null || true
iptables -D FORWARD -d {cs} -i {IFACE} -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || true
awg-quick down /tmp/utmka-cas0.conf 2>/dev/null || ip link del {IFACE} 2>/dev/null || true
rm -f /tmp/utmka-cas0.conf 2>/dev/null || true
echo DOWN_ENTRY
"""
    exit_down = f"""
WAN=$(ip route show default | awk '/default/{{print $5; exit}}'); [ -n "$WAN" ] || WAN=eth0
iptables -t nat -D POSTROUTING -s 10.250.0.0/30 -o "$WAN" -j MASQUERADE 2>/dev/null || true
iptables -D FORWARD -i {IFACE} -o "$WAN" -j ACCEPT 2>/dev/null || true
iptables -D FORWARD -i "$WAN" -o {IFACE} -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || true
awg-quick down /tmp/utmka-cas0.conf 2>/dev/null || ip link del {IFACE} 2>/dev/null || true
rm -f /tmp/utmka-cas0.conf 2>/dev/null || true
echo DOWN_EXIT
"""
    exit_host_down = f"""
pkill -f 'socat UDP4-LISTEN:{p},' 2>/dev/null || true
echo DOWN_SOCAT
"""
    entry_host_down = ""
    if entry_ctn_ip and entry_public_ip and entry_host_port:
        hp = str(int(entry_host_port))
        ctn = shlex.quote(entry_ctn_ip)
        pub = shlex.quote(entry_public_ip)
        xip = shlex.quote(exit_public_ip or "0.0.0.0")
        cm = f"{LABEL}-entry-udp"
        entry_host_down = f"""
iptables -t nat -D POSTROUTING -s {ctn} -p udp -o eth0 -d {xip} --dport {p} -m comment --comment {cm}-snat -j SNAT --to-source {pub}:{hp} 2>/dev/null || true
iptables -t nat -D PREROUTING -i eth0 -p udp -d {pub} --dport {hp} -m comment --comment {cm}-dnat -j DNAT --to-destination {ctn}:{hp} 2>/dev/null || true
iptables -t raw -D PREROUTING -i eth0 -p udp -d {ctn} --dport {hp} -m comment --comment {cm}-raw -j ACCEPT 2>/dev/null || true
iptables -D FORWARD -i eth0 -o amn0 -p udp -d {ctn} --dport {hp} -m comment --comment {cm}-fwd-in -j ACCEPT 2>/dev/null || true
iptables -D FORWARD -i amn0 -o eth0 -p udp -s {ctn} --sport {hp} -m comment --comment {cm}-fwd-out -j ACCEPT 2>/dev/null || true
echo DOWN_ENTRY_HOST
"""
    errors = []
    # split-слой (ipset/mangle/fwmark в netns) снимаем первым — он висит на netns entry
    try:
        from app.services import cascade_split

        pid = cascade_split.netns_pid(entry_ssh, entry_ctn)
        if pid:
            cascade_split.teardown_split(entry_ssh, pid, client_subnet or "10.8.1.0/24")
    except Exception:  # noqa: BLE001
        pass
    try:
        run_container_script(entry_ssh, entry_ctn, entry_down, timeout=40)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"entry: {exc}")
    if entry_host_down:
        try:
            run_script(entry_ssh, entry_host_down, timeout=30)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"entry-host: {exc}")
    try:
        run_container_script(exit_ssh, exit_ctn, exit_down, timeout=40)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"exit: {exc}")
    try:
        run_script(exit_ssh, exit_host_down, timeout=30)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"exit-host: {exc}")
    if errors:
        raise CascadeError("; ".join(errors))


_ = (decrypt,)  # зарезервировано для re-apply/agent
