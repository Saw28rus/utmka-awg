"""Каскад AmneziaWG (Model A). MVP-этап: read-only preflight.

Ничего не меняет на серверах. Только диагностика entry (RU) и exit (NL),
чтобы ответить на ключевой вопрос Model A: где ставить hook (host/netns) и
готовы ли серверы к транзитному туннелю. Apply/rollback — следующий этап.

См. AMNEZIA_CASCADE_PLAN.md §6 (Model A), §7 (routing), §31 (data-plane spec).
"""

from __future__ import annotations

import ipaddress
from datetime import datetime, timezone
from typing import Optional

from app.schemas.cascade import (
    CascadeCheck,
    CascadeLinkStatus,
    CascadeLinkSummary,
    CascadePreflightResult,
)
from app.services.cascade_store import cascade_store
from app.services.server_store import server_store
from app.ssh import exec as ssh_exec

AMNEZIA_AWG_CONTAINERS = ("amnezia-awg2", "amnezia-awg")


class CascadeError(Exception):
    pass


_ENTRY_PROBE = r"""
CTN=""
for c in amnezia-awg2 amnezia-awg; do
  if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$c"; then CTN="$c"; break; fi
done
echo "container=$CTN"
echo "is_root=$([ "$(id -u)" = 0 ] && echo 1 || echo 0)"
echo "has_nft=$(command -v nft >/dev/null 2>&1 && echo 1 || echo 0)"
echo "has_ip=$(command -v ip >/dev/null 2>&1 && echo 1 || echo 0)"
echo "host_masq=$(iptables -t nat -S 2>/dev/null | grep -c MASQUERADE)"
if [ -n "$CTN" ]; then
  PID=$(docker inspect -f '{{.State.Pid}}' "$CTN" 2>/dev/null)
  echo "netns_pid=$PID"
  ADDR=$(docker exec "$CTN" sh -c 'cat /opt/amnezia/awg/wg0.conf /opt/amnezia/awg/awg0.conf 2>/dev/null' 2>/dev/null \
    | awk -F= '/^[[:space:]]*Address/{gsub(/[[:space:]]/,"",$2); print $2; exit}')
  echo "server_addr=$ADDR"
  # AmneziaWG-стек живёт ВНУТРИ контейнера — проверяем там, где будет жить utmka-cas0.
  echo "ctn_has_ip=$(docker exec "$CTN" sh -c 'command -v ip >/dev/null 2>&1 && echo 1 || echo 0' 2>/dev/null)"
  echo "ctn_has_awg=$(docker exec "$CTN" sh -c '(command -v awg >/dev/null 2>&1 || command -v amneziawg-go >/dev/null 2>&1) && echo 1 || echo 0' 2>/dev/null)"
  echo "ctn_awg_kind=$(docker exec "$CTN" sh -c 'if command -v awg >/dev/null 2>&1; then echo tools; elif command -v amneziawg-go >/dev/null 2>&1; then echo go; else echo none; fi' 2>/dev/null)"
  echo "ctn_has_tun=$(docker exec "$CTN" sh -c '[ -c /dev/net/tun ] && echo 1 || echo 0' 2>/dev/null)"
  if [ -n "$PID" ]; then
    echo "netns_masq=$(nsenter -t "$PID" -n iptables -t nat -S 2>/dev/null | grep -c MASQUERADE)"
    echo "netns_ifaces=$(nsenter -t "$PID" -n ip -o -4 addr show 2>/dev/null | awk '{print $2"@"$4}' | tr '\n' ',')"
    echo "netns_has_cas0=$(nsenter -t "$PID" -n ip link show utmka-cas0 >/dev/null 2>&1 && echo 1 || echo 0)"
  fi
fi
"""

# Плейсхолдеры __IFACE__/__TABLE__ подставляются под слот каскада (PA2-2).
# Слот 0 → utmka-cas0 / 7770 = прежнее поведение байт-в-байт.
_LIVE_PROBE_TMPL = r"""
CTN=""
for c in amnezia-awg2 amnezia-awg; do
  if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$c"; then CTN="$c"; break; fi
done
echo "container=$CTN"
if [ -z "$CTN" ]; then echo "active=0"; exit 0; fi
PID=$(docker inspect -f '{{.State.Pid}}' "$CTN" 2>/dev/null)
if [ -z "$PID" ] || [ "$PID" = "0" ]; then echo "active=0"; exit 0; fi
CAS0=$(nsenter -t "$PID" -n ip link show __IFACE__ >/dev/null 2>&1 && echo 1 || echo 0)
RULE=$(nsenter -t "$PID" -n ip rule show 2>/dev/null | grep -c 'lookup __TABLE__' || true)
HS=$(nsenter -t "$PID" -n awg show __IFACE__ latest-handshakes 2>/dev/null | awk '{print $2}' | sort -nr | head -n1)
[ -z "$HS" ] && HS=0
echo "cas0=$CAS0"
echo "rule=$RULE"
echo "handshake=$HS"
if [ "$CAS0" = "1" ] && [ "$RULE" -gt 0 ]; then echo "active=1"; else echo "active=0"; fi
"""


def _live_probe_script(iface: str, table: str) -> str:
    return _LIVE_PROBE_TMPL.replace("__IFACE__", iface).replace("__TABLE__", table)

_EXIT_PROBE = r"""
CTN=""
for c in amnezia-awg2 amnezia-awg; do
  if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$c"; then CTN="$c"; break; fi
done
echo "container=$CTN"
echo "is_root=$([ "$(id -u)" = 0 ] && echo 1 || echo 0)"
echo "public_ip=$(curl -4 -s --max-time 5 ifconfig.me 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}')"
echo "has_nft=$(command -v nft >/dev/null 2>&1 && echo 1 || echo 0)"
echo "ip_forward=$(cat /proc/sys/net/ipv4/ip_forward 2>/dev/null)"
echo "wan_iface=$(ip route show default 2>/dev/null | awk '/default/{print $5; exit}')"
echo "host_tun=$([ -c /dev/net/tun ] && echo 1 || echo 0)"
# AmneziaWG-стек на exit: kernel-модуль / userspace на хосте ЛИБО внутри контейнера.
if command -v awg >/dev/null 2>&1; then
  if lsmod 2>/dev/null | grep -q amneziawg || modinfo amneziawg >/dev/null 2>&1; then
    echo "awg=kernel"
  else
    echo "awg=userspace_tools"
  fi
elif command -v amneziawg-go >/dev/null 2>&1; then
  echo "awg=userspace"
else
  echo "awg=none"
fi
if [ -n "$CTN" ]; then
  PID=$(docker inspect -f '{{.State.Pid}}' "$CTN" 2>/dev/null)
  echo "netns_pid=$PID"
  echo "ctn_has_ip=$(docker exec "$CTN" sh -c 'command -v ip >/dev/null 2>&1 && echo 1 || echo 0' 2>/dev/null)"
  echo "ctn_has_awg=$(docker exec "$CTN" sh -c '(command -v awg >/dev/null 2>&1 || command -v amneziawg-go >/dev/null 2>&1) && echo 1 || echo 0' 2>/dev/null)"
  echo "ctn_awg_kind=$(docker exec "$CTN" sh -c 'if command -v awg >/dev/null 2>&1; then echo tools; elif command -v amneziawg-go >/dev/null 2>&1; then echo go; else echo none; fi' 2>/dev/null)"
  echo "ctn_has_tun=$(docker exec "$CTN" sh -c '[ -c /dev/net/tun ] && echo 1 || echo 0' 2>/dev/null)"
fi
"""


def _parse_kv(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip()
    return out


def _connect(server_id: str):
    target = server_store.ssh_target(server_id)
    if not target:
        raise CascadeError("SSH-доступ к серверу не настроен.")
    return ssh_exec.connect(
        host=target.host,
        port=target.port,
        username=target.username,
        password=target.password,
        key=target.key,
        timeout=15,
    )


def probe_cascade_live(entry_server_id: str) -> dict[str, bool]:
    """Проверяет на entry: поднят ли транзит-интерфейс и есть ли policy-routing каскада.

    Интерфейс/таблица берутся из слота каскада (PA2-2); слот 0 = utmka-cas0/7770.
    """
    from app.services.transit_allocator import resolve_profile

    profile = resolve_profile(cascade_store.get_link(entry_server_id))
    try:
        ssh = _connect(entry_server_id)
    except Exception:  # noqa: BLE001
        return {"active": False, "cas0": False, "rule": False, "handshake": False}
    try:
        res = ssh_exec.run(ssh, _live_probe_script(profile.iface, profile.table), timeout=25)
        vals = _parse_kv(res.stdout)
        cas0 = vals.get("cas0") == "1"
        rule = int(vals.get("rule") or 0) > 0
        hs = (vals.get("handshake") or "0").strip()
        handshake = bool(hs) and hs != "0"
        active = vals.get("active") == "1" or (cas0 and rule)
        return {"active": active, "cas0": cas0, "rule": rule, "handshake": handshake}
    except Exception:  # noqa: BLE001
        return {"active": False, "cas0": False, "rule": False, "handshake": False}
    finally:
        ssh.close()


def reconcile_cascade_state(entry_server_id: str, link: dict) -> tuple[str, bool]:
    """Сверяет сохранённый state с реальным на сервере. Возвращает (state, live_active)."""
    stored = link.get("state") or "none"
    if not link.get("exit_server_id"):
        return stored, False

    live = probe_cascade_live(entry_server_id)
    if live["active"]:
        if stored != "active":
            cascade_store.upsert_link(
                entry_server_id,
                state="active",
                message="Каскад работает на сервере.",
            )
        return "active", True

    if stored == "active":
        cascade_store.upsert_link(
            entry_server_id,
            state="down",
            message="Каскад не обнаружен на сервере — возможно, была перезагрузка.",
        )
        return "down", False

    return stored, False


def reconcile_all_cascades() -> dict:
    """Фоновое самолечение AWG-каскадов: переподнимает те, что должны работать,
    но слетели (типично — после перезагрузки entry-сервера; правила caскада
    не персистентны). Вызывается планировщиком, по образцу Xray-reconcile.

    Лечим только связи с намерением «up» (state active/down) и пройденным
    preflight. Узел недоступен → тихо пропускаем (попробуем в следующий раз).
    """
    import logging

    logger = logging.getLogger("utmka.cascade")
    healed = 0
    checked = 0
    failed = 0

    for link in cascade_store.list_links():
        exit_id = link.get("exit_server_id")
        entry_id = link.get("entry_server_id")
        if not exit_id or not entry_id:
            continue
        state = link.get("state") or "none"
        # «Намерение up»: каскад был включён (active) или помечен down (слетел).
        if state not in ("active", "down"):
            continue
        if not link.get("last_preflight_ok"):
            continue

        checked += 1
        live = probe_cascade_live(entry_id)
        if live["active"]:
            if state != "active":
                cascade_store.upsert_link(entry_id, state="active", message="Каскад работает на сервере.")
            continue

        # Каскад должен работать, но не обнаружен. Узел может быть и недоступен —
        # тогда apply_cascade честно упадёт на SSH, и мы просто попробуем позже.
        try:
            from app.services.cascade_apply import apply_cascade

            apply_cascade(entry_id)
            healed += 1
            logger.info("cascade reconcile: healed entry=%s", entry_id)
            try:
                from app.services.notification_store import notification_store

                entry_rec = server_store.get_record(entry_id) or {}
                notification_store.add(
                    level="warning",
                    code="cascade_self_healed",
                    title="Каскад переподнят автоматически",
                    message=(
                        f"Каскад на входе «{entry_rec.get('name') or entry_id}» слетел "
                        f"(вероятно, перезагрузка сервера) и был автоматически восстановлен."
                    ),
                )
            except Exception:  # noqa: BLE001
                pass
        except Exception as exc:  # noqa: BLE001
            failed += 1
            logger.debug("cascade reconcile: entry=%s не вылечен: %s", entry_id, exc)
            continue

    return {"checked": checked, "healed": healed, "failed": failed}


def _client_subnet_from_addr(addr: Optional[str]) -> Optional[str]:
    """`10.8.1.1/24` -> `10.8.1.0/24`."""
    if not addr:
        return None
    candidate = addr.split(",")[0].strip()
    try:
        return str(ipaddress.ip_network(candidate, strict=False))
    except ValueError:
        return None


def list_cascade_links(*, live_probe: bool = False) -> list[CascadeLinkSummary]:
    """Все настроенные связи entry→exit для списка серверов.

    live_probe=False — только сохранённый state (без SSH на entry).
    live_probe=True — сверка с сервером (кнопка «Обновить»).
    """
    out: list[CascadeLinkSummary] = []
    for link in cascade_store.list_links():
        exit_id = link.get("exit_server_id")
        if not exit_id:
            continue
        state = link.get("state") or "none"
        if state == "none":
            continue
        entry_id = link.get("entry_server_id") or ""
        entry_rec = server_store.get_record(entry_id) if entry_id else None
        exit_rec = server_store.get_record(exit_id)
        if not entry_rec or not exit_rec:
            continue
        if live_probe:
            state, live_active = reconcile_cascade_state(entry_id, link)
        else:
            live_active = state == "active"
        out.append(
            CascadeLinkSummary(
                entry_server_id=entry_id,
                entry_name=entry_rec.get("name") or entry_id,
                entry_host=entry_rec.get("host") or "",
                exit_server_id=exit_id,
                exit_name=exit_rec.get("name") or exit_id,
                exit_host=exit_rec.get("host") or "",
                state=state,
                is_active=live_active,
                live_active=live_active,
                egress_ip=link.get("egress_ip"),
                transit_port=link.get("transit_port"),
            )
        )
    out.sort(key=lambda x: (not x.is_active, x.entry_name))
    return out


def get_cascade_status(entry_server_id: str) -> CascadeLinkStatus:
    record = server_store.get_record(entry_server_id)
    if not record:
        raise CascadeError("Сервер не найден.")
    link = cascade_store.get_link(entry_server_id) or {}
    exit_id = link.get("exit_server_id")
    exit_name = None
    if exit_id:
        exit_rec = server_store.get_record(exit_id)
        exit_name = exit_rec.get("name") if exit_rec else None
    state, live_active = reconcile_cascade_state(entry_server_id, link)
    link = cascade_store.get_link(entry_server_id) or link
    return CascadeLinkStatus(
        entry_server_id=entry_server_id,
        exit_server_id=exit_id,
        exit_name=exit_name,
        state=state,
        nat_model=link.get("nat_model", "model_a"),
        client_subnet=link.get("client_subnet"),
        transit_subnet=link.get("transit_subnet"),
        transit_port=link.get("transit_port"),
        recommended_hook=link.get("recommended_hook"),
        last_preflight_at=link.get("last_preflight_at"),
        last_preflight_ok=link.get("last_preflight_ok", False),
        last_applied_at=link.get("last_applied_at"),
        egress_ip=link.get("egress_ip"),
        message=link.get("message"),
        split_enabled=bool((link.get("split") or {}).get("enabled")),
        split_applied=bool((link.get("split") or {}).get("applied")),
        live_active=live_active,
    )


def run_preflight(entry_server_id: str, exit_server_id: str) -> CascadePreflightResult:
    entry_rec = server_store.get_record(entry_server_id)
    exit_rec = server_store.get_record(exit_server_id)
    if not entry_rec:
        raise CascadeError("Entry-сервер не найден.")
    if not exit_rec:
        raise CascadeError("Exit-сервер не найден.")
    if entry_server_id == exit_server_id:
        raise CascadeError("Entry и Exit не могут быть одним сервером.")

    checks: list[CascadeCheck] = []
    blockers: list[str] = []

    # --- Entry probe ---
    entry_vals: dict[str, str] = {}
    try:
        ssh = _connect(entry_server_id)
        try:
            entry_vals = _parse_kv(ssh_exec.run(ssh, _ENTRY_PROBE, timeout=40).stdout)
        finally:
            ssh.close()
    except CascadeError:
        raise
    except Exception as exc:  # noqa: BLE001
        blockers.append(f"Entry SSH не отвечает: {exc}")

    # --- Exit probe ---
    exit_vals: dict[str, str] = {}
    try:
        ssh = _connect(exit_server_id)
        try:
            exit_vals = _parse_kv(ssh_exec.run(ssh, _EXIT_PROBE, timeout=40).stdout)
        finally:
            ssh.close()
    except CascadeError:
        raise
    except Exception as exc:  # noqa: BLE001
        blockers.append(f"Exit SSH не отвечает: {exc}")

    # --- Entry: root ---
    if entry_vals.get("is_root") == "1":
        checks.append(CascadeCheck(id="entry_root", label="Entry: root-доступ", status="ok", value="Есть"))
    else:
        checks.append(CascadeCheck(
            id="entry_root", label="Entry: root-доступ", status="danger", value="Нет",
            detail="Каскад правит маршрутизацию и фаервол — нужен root/sudo.",
        ))
        blockers.append("Нет root на entry.")

    # --- Entry: Amnezia container ---
    container = entry_vals.get("container") or ""
    netns_pid_raw = entry_vals.get("netns_pid") or ""
    netns_pid = int(netns_pid_raw) if netns_pid_raw.isdigit() else None
    if container:
        checks.append(CascadeCheck(
            id="entry_container", label="Entry: контейнер AmneziaWG", status="ok",
            value=container, detail=f"netns pid={netns_pid}" if netns_pid else None,
        ))
    else:
        checks.append(CascadeCheck(
            id="entry_container", label="Entry: контейнер AmneziaWG", status="danger",
            value="Не найден", detail="Ожидались amnezia-awg2/amnezia-awg.",
        ))
        blockers.append("На entry не найден контейнер AmneziaWG.")

    # --- Entry: client subnet ---
    client_subnet = _client_subnet_from_addr(entry_vals.get("server_addr"))
    if client_subnet:
        checks.append(CascadeCheck(
            id="client_subnet", label="Client subnet", status="ok", value=client_subnet,
        ))
    else:
        checks.append(CascadeCheck(
            id="client_subnet", label="Client subnet", status="warning", value="Не определена",
            detail="Не удалось прочитать Address из конфига AmneziaWG.",
        ))

    # --- Source visibility / hook (ключевой вопрос Model A) ---
    netns_masq = entry_vals.get("netns_masq")
    source_visibility = "unknown"
    recommended_hook = "unknown"
    if container and netns_pid:
        # Стандарт Amnezia: трафик NAT-ится на границе netns контейнера,
        # значит source 10.8.1.x виден ВНУТРИ netns до MASQUERADE -> hook в netns.
        source_visibility = "netns"
        recommended_hook = "netns"
        checks.append(CascadeCheck(
            id="source_visibility", label="Видимость source клиента", status="ok",
            value="В netns контейнера",
            detail=(
                "Model A: SNAT и policy routing ставятся в network namespace контейнера "
                f"(MASQUERADE-правил в netns: {netns_masq or '0'}). "
                "C0 на реальном трафике подтвердит, что from client subnet матчится до NAT."
            ),
        ))
    else:
        source_visibility = "unknown"
        recommended_hook = "blocked"
        checks.append(CascadeCheck(
            id="source_visibility", label="Видимость source клиента", status="danger",
            value="Не подтверждена",
            detail="Без netns контейнера нельзя гарантировать routing до NAT — apply будет заблокирован.",
        ))
        blockers.append("Не удалось определить точку hook (netns) на entry.")

    # --- Entry tooling ---
    _tool_check(checks, blockers, "entry_nft", "Entry: nftables", entry_vals.get("has_nft"))
    _tool_check(checks, blockers, "entry_ip", "Entry: iproute2 (хост)", entry_vals.get("has_ip"))

    # awg-стек и tun проверяем ВНУТРИ контейнера — там поднимается utmka-cas0.
    if container:
        awg_kind = entry_vals.get("ctn_awg_kind") or "none"
        if entry_vals.get("ctn_has_awg") == "1":
            checks.append(CascadeCheck(
                id="entry_awg", label="Entry: AmneziaWG-стек", status="ok",
                value="awg-tools" if awg_kind == "tools" else "amneziawg-go",
                detail=f"Внутри контейнера {container} — здесь поднимется транзит utmka-cas0.",
            ))
        else:
            checks.append(CascadeCheck(
                id="entry_awg", label="Entry: AmneziaWG-стек", status="danger", value="Не найден в контейнере",
                detail=f"В контейнере {container} нет awg/amneziawg-go — транзит поднять негде.",
            ))
            blockers.append("В контейнере entry нет AmneziaWG-стека для транзита.")

        if entry_vals.get("ctn_has_ip") == "1":
            checks.append(CascadeCheck(id="entry_ctn_ip", label="Entry: iproute2 (контейнер)", status="ok", value="Есть"))
        else:
            checks.append(CascadeCheck(
                id="entry_ctn_ip", label="Entry: iproute2 (контейнер)", status="danger", value="Нет",
                detail=f"В {container} нет `ip` — policy routing внутри netns не настроить.",
            ))
            blockers.append("В контейнере entry нет iproute2.")

        if entry_vals.get("ctn_has_tun") == "1":
            checks.append(CascadeCheck(id="entry_tun", label="Entry: /dev/net/tun", status="ok", value="Есть"))
        else:
            checks.append(CascadeCheck(
                id="entry_tun", label="Entry: /dev/net/tun", status="danger", value="Нет",
                detail="Без /dev/net/tun в контейнере userspace-транзит amneziawg-go не создаст интерфейс.",
            ))
            blockers.append("В контейнере entry недоступен /dev/net/tun.")

    # --- Exit: root ---
    if exit_vals.get("is_root") == "1":
        checks.append(CascadeCheck(id="exit_root", label="Exit: root-доступ", status="ok", value="Есть"))
    elif exit_vals:
        checks.append(CascadeCheck(
            id="exit_root", label="Exit: root-доступ", status="danger", value="Нет",
        ))
        blockers.append("Нет root на exit.")

    # --- Exit: public IP ---
    exit_public_ip = exit_vals.get("public_ip") or None
    if exit_public_ip:
        checks.append(CascadeCheck(
            id="exit_public", label="Exit: публичный IP", status="ok", value=exit_public_ip,
            detail="Exit обязан быть reachable снаружи по UDP (endpoint транзита).",
        ))
    elif exit_vals:
        checks.append(CascadeCheck(
            id="exit_public", label="Exit: публичный IP", status="danger", value="Не определён",
        ))
        blockers.append("У exit нет публичного IP — он должен быть endpoint транзита.")

    # --- Exit: AmneziaWG tooling (gate §6.6) — хост ИЛИ контейнер ---
    awg_host = exit_vals.get("awg") or "none"
    exit_container = exit_vals.get("container") or ""
    exit_ctn_awg = exit_vals.get("ctn_has_awg") == "1"
    exit_ctn_kind = exit_vals.get("ctn_awg_kind") or "none"
    if awg_host == "kernel":
        exit_awg_tooling = "kernel"
        checks.append(CascadeCheck(
            id="exit_awg", label="Exit: AmneziaWG-стек", status="ok",
            value="kernel-модуль (хост)",
        ))
    elif awg_host in ("userspace_tools", "userspace"):
        exit_awg_tooling = "userspace"
        checks.append(CascadeCheck(
            id="exit_awg", label="Exit: AmneziaWG-стек", status="ok",
            value="userspace (хост)",
        ))
    elif exit_container and exit_ctn_awg:
        exit_awg_tooling = "container"
        checks.append(CascadeCheck(
            id="exit_awg", label="Exit: AmneziaWG-стек", status="ok",
            value="awg-tools" if exit_ctn_kind == "tools" else "amneziawg-go",
            detail=f"Внутри контейнера {exit_container}. Транзит exit поднимется в его netns.",
        ))
    elif exit_vals:
        # AmneziaWG на exit НЕ обязателен заранее: панель поставит его сама при
        # включении каскада (одна кнопка делает всё). Поэтому это не блокер, а
        # предупреждение-«будет установлено». Жёсткие требования к exit — только
        # root и публичный IP (проверены выше).
        exit_awg_tooling = "auto_install"
        checks.append(CascadeCheck(
            id="exit_awg", label="Exit: AmneziaWG-стек", status="warning", value="Будет установлен",
            detail="На выходном сервере нет AmneziaWG — панель установит его автоматически при включении каскада.",
        ))
    else:
        exit_awg_tooling = "unknown"

    # --- Exit forwarding ---
    if exit_vals.get("ip_forward") == "1":
        checks.append(CascadeCheck(id="exit_fwd", label="Exit: ip_forward", status="ok", value="Включён"))
    elif exit_vals:
        checks.append(CascadeCheck(
            id="exit_fwd", label="Exit: ip_forward", status="warning", value="Выключен",
            detail="Включится при apply (sysctl net.ipv4.ip_forward=1).",
        ))

    # --- Subnet overlap (client vs transit) ---
    from app.services.transit_allocator import allocate_slot, profile_for_slot

    profile = profile_for_slot(allocate_slot(entry_server_id))
    transit_subnet = profile.subnet
    if client_subnet and _subnets_overlap(client_subnet, transit_subnet):
        checks.append(CascadeCheck(
            id="overlap", label="Пересечение подсетей", status="danger",
            value=f"{client_subnet} ↔ {transit_subnet}",
        ))
        blockers.append("Client subnet пересекается с transit subnet — нужно сменить.")
    else:
        checks.append(CascadeCheck(
            id="overlap", label="Пересечение подсетей", status="ok",
            value=f"transit {transit_subnet}",
        ))

    ok = len(blockers) == 0
    message = (
        "Preflight пройден: модель Model A применима, можно переходить к apply (C0/C1)."
        if ok else
        "Preflight выявил блокеры — apply невозможен, пока они не устранены."
    )

    existing = cascade_store.get_link(entry_server_id) or {}
    live = probe_cascade_live(entry_server_id)
    if live["active"]:
        new_state = "active"
        message = "Каскад уже работает на сервере. Проверка пройдена."
    else:
        new_state = "preflight_ok" if ok else "preflight_failed"

    cascade_store.upsert_link(
        entry_server_id,
        exit_server_id=exit_server_id,
        state=new_state,
        nat_model="model_a",
        client_subnet=client_subnet,
        transit_subnet=transit_subnet,
        transit_port=profile.transit_port,
        transit_slot=profile.slot,
        recommended_hook=recommended_hook,
        last_preflight_at=datetime.now(timezone.utc).isoformat(),
        last_preflight_ok=ok,
        message=message,
    )

    return CascadePreflightResult(
        ok=ok,
        entry_server_id=entry_server_id,
        exit_server_id=exit_server_id,
        entry_name=entry_rec.get("name"),
        exit_name=exit_rec.get("name"),
        client_subnet=client_subnet,
        source_visibility=source_visibility,
        recommended_hook=recommended_hook,
        amnezia_container=container or None,
        amnezia_netns_pid=netns_pid,
        exit_public_ip=exit_public_ip,
        exit_awg_tooling=exit_awg_tooling,
        transit_subnet=transit_subnet,
        transit_port=profile.transit_port,
        checks=checks,
        blockers=blockers,
        message=message,
        live_active=live["active"],
    )


def _tool_check(checks: list[CascadeCheck], blockers: list[str], cid: str, label: str, present: Optional[str]) -> None:
    if present == "1":
        checks.append(CascadeCheck(id=cid, label=label, status="ok", value="Есть"))
    else:
        checks.append(CascadeCheck(id=cid, label=label, status="danger", value="Нет"))
        blockers.append(f"{label}: отсутствует.")


def _subnets_overlap(a: str, b: str) -> bool:
    try:
        return ipaddress.ip_network(a, strict=False).overlaps(ipaddress.ip_network(b, strict=False))
    except ValueError:
        return False
