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
from app.services.cascade_protocol import (
    CLIENT_HINT_31,
    containers_for,
    link_protocols,
    merge_link_protocols,
    normalize_cascade_protocols,
    protocol_label,
)
from app.services.cascade_store import cascade_store
from app.services.server_store import server_store
from app.ssh import exec as ssh_exec

AMNEZIA_AWG_CONTAINERS = ("amnezia-awg2", "amnezia-awg")


def _with_containers(script: str, containers: tuple[str, ...]) -> str:
    return script.replace("__CONTAINERS__", " ".join(containers))


def entry_probe_script(protocol: str = "awg2") -> str:
    return _with_containers(_ENTRY_PROBE, containers_for(protocol))


def exit_probe_script(protocol: str = "awg2") -> str:
    return _with_containers(_EXIT_PROBE, containers_for(protocol))


def live_probe_script(iface: str, table: str, protocol: str = "awg2") -> str:
    return _with_containers(
        _live_probe_script(iface, table),
        containers_for(protocol),
    )


class CascadeError(Exception):
    pass


_ENTRY_PROBE = r"""
CTN=""
for c in __CONTAINERS__; do
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
for c in __CONTAINERS__; do
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
for c in __CONTAINERS__; do
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
    Если каскад на 2.0 и 3.1 сразу — активен только когда подняты все ноги.
    """
    from app.services.cascade_protocol import slot_for_protocol
    from app.services.transit_allocator import profile_for_slot, resolve_profile

    link = cascade_store.get_link(entry_server_id) or {}
    legs: list[tuple[str, object]] = []
    for proto in link_protocols(link):
        reserved = slot_for_protocol(link, proto)
        if reserved is None:
            continue
        legs.append((proto, profile_for_slot(reserved)))
    if not legs:
        legs.append(("awg2", resolve_profile(link)))

    try:
        ssh = _connect(entry_server_id)
    except Exception:  # noqa: BLE001
        return {"active": False, "cas0": False, "rule": False, "handshake": False}
    try:
        results = []
        for proto, profile in legs:
            res = ssh_exec.run(
                ssh,
                live_probe_script(profile.iface, profile.table, proto),
                timeout=25,
            )
            vals = _parse_kv(res.stdout)
            cas0 = vals.get("cas0") == "1"
            rule = int(vals.get("rule") or 0) > 0
            hs = (vals.get("handshake") or "0").strip()
            handshake = bool(hs) and hs != "0"
            active = vals.get("active") == "1" or (cas0 and rule)
            results.append(
                {"active": active, "cas0": cas0, "rule": rule, "handshake": handshake}
            )
        if not results:
            return {"active": False, "cas0": False, "rule": False, "handshake": False}
        return {
            "active": all(item["active"] for item in results),
            "cas0": all(item["cas0"] for item in results),
            "rule": all(item["rule"] for item in results),
            "handshake": all(item["handshake"] for item in results),
        }
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
    protocols = link_protocols(link)
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
        protocol=protocols[0] if protocols else "awg2",
        protocols=protocols,
    )


def _ssh_probe_kv(server_id: str, script: str) -> dict[str, str]:
    ssh = _connect(server_id)
    try:
        return _parse_kv(ssh_exec.run(ssh, script, timeout=40).stdout)
    finally:
        ssh.close()


def run_preflight(
    entry_server_id: str,
    exit_server_id: str,
    protocols: Optional[list[str]] = None,
) -> CascadePreflightResult:
    entry_rec = server_store.get_record(entry_server_id)
    exit_rec = server_store.get_record(exit_server_id)
    if not entry_rec:
        raise CascadeError("Entry-сервер не найден.")
    if not exit_rec:
        raise CascadeError("Exit-сервер не найден.")
    if entry_server_id == exit_server_id:
        raise CascadeError("Entry и Exit не могут быть одним сервером.")

    existing = cascade_store.get_link(entry_server_id) or {}
    if protocols is None:
        protocols = existing.get("protocols") or [existing.get("protocol") or "awg2"]
    protos = normalize_cascade_protocols(protocols)
    if not protos:
        raise CascadeError("Выберите AmneziaWG 2.0 и/или 3.1.")
    multi = len(protos) > 1

    def _cid(cid: str, proto: str) -> str:
        return f"{cid}_{proto}" if multi else cid

    def _plabel(base: str, proto: str) -> str:
        if not multi:
            return base
        return f"{base} ({protocol_label(proto)})"

    checks: list[CascadeCheck] = []
    blockers: list[str] = []

    host_entry: dict[str, str] = {}
    host_exit: dict[str, str] = {}
    per_proto: dict[str, dict] = {}

    try:
        host_entry = _ssh_probe_kv(entry_server_id, entry_probe_script(protos[0]))
    except CascadeError:
        raise
    except Exception as exc:  # noqa: BLE001
        blockers.append(f"Entry SSH не отвечает: {exc}")

    try:
        host_exit = _ssh_probe_kv(exit_server_id, exit_probe_script(protos[0]))
    except CascadeError:
        raise
    except Exception as exc:  # noqa: BLE001
        blockers.append(f"Exit SSH не отвечает: {exc}")

    for proto in protos:
        try:
            entry_vals = (
                host_entry
                if proto == protos[0]
                else _ssh_probe_kv(entry_server_id, entry_probe_script(proto))
            )
        except Exception as exc:  # noqa: BLE001
            blockers.append(f"Entry ({protocol_label(proto)}): SSH {exc}")
            entry_vals = {}
        try:
            exit_vals = (
                host_exit
                if proto == protos[0]
                else _ssh_probe_kv(exit_server_id, exit_probe_script(proto))
            )
        except Exception as exc:  # noqa: BLE001
            blockers.append(f"Exit ({protocol_label(proto)}): SSH {exc}")
            exit_vals = {}
        per_proto[proto] = {"entry": entry_vals, "exit": exit_vals}

    entry_vals = per_proto[protos[0]]["entry"]
    exit_vals = per_proto[protos[0]]["exit"]

    # --- Entry: root ---
    if entry_vals.get("is_root") == "1":
        checks.append(CascadeCheck(id="entry_root", label="Entry: root-доступ", status="ok", value="Есть"))
    else:
        checks.append(CascadeCheck(
            id="entry_root", label="Entry: root-доступ", status="danger", value="Нет",
            detail="Каскад правит маршрутизацию и фаервол — нужен root/sudo.",
        ))
        blockers.append("Нет root на entry.")

    # --- Entry: Amnezia container + netns (на каждый выбранный протокол) ---
    source_visibility = "unknown"
    recommended_hook = "unknown"
    primary_container = ""
    primary_subnet = None
    netns_pid = None

    for proto in protos:
        ev = per_proto[proto]["entry"]
        label = protocol_label(proto)
        container = ev.get("container") or ""
        netns_pid_raw = ev.get("netns_pid") or ""
        pid = int(netns_pid_raw) if netns_pid_raw.isdigit() else None
        if proto == protos[0]:
            primary_container = container
            netns_pid = pid
        if container:
            checks.append(CascadeCheck(
                id=_cid("entry_container", proto),
                label=_plabel("Entry: контейнер AmneziaWG", proto),
                status="ok",
                value=container,
                detail=f"netns pid={pid}" if pid else None,
            ))
        else:
            expected = "/".join(containers_for(proto))
            checks.append(CascadeCheck(
                id=_cid("entry_container", proto),
                label=_plabel("Entry: контейнер AmneziaWG", proto),
                status="danger",
                value="Не найден",
                detail=f"Ожидался {expected}. Установите {label} на входном сервере.",
            ))
            blockers.append(f"На входе нет контейнера {label}.")

        client_subnet = _client_subnet_from_addr(ev.get("server_addr"))
        per_proto[proto]["client_subnet"] = client_subnet
        if proto == protos[0]:
            primary_subnet = client_subnet
        if client_subnet:
            checks.append(CascadeCheck(
                id=_cid("client_subnet", proto),
                label=_plabel("Client subnet", proto),
                status="ok",
                value=client_subnet,
            ))
        else:
            checks.append(CascadeCheck(
                id=_cid("client_subnet", proto),
                label=_plabel("Client subnet", proto),
                status="warning",
                value="Не определена",
                detail="Не удалось прочитать Address из конфига AmneziaWG.",
            ))

        if container and pid:
            source_visibility = "netns"
            recommended_hook = "netns"
            checks.append(CascadeCheck(
                id=_cid("source_visibility", proto),
                label=_plabel("Видимость source клиента", proto),
                status="ok",
                value="В netns контейнера",
                detail=(
                    "Model A: SNAT и policy routing ставятся в network namespace контейнера "
                    f"(MASQUERADE-правил в netns: {ev.get('netns_masq') or '0'})."
                ),
            ))
        else:
            if recommended_hook == "unknown":
                recommended_hook = "blocked"
            checks.append(CascadeCheck(
                id=_cid("source_visibility", proto),
                label=_plabel("Видимость source клиента", proto),
                status="danger",
                value="Не подтверждена",
                detail="Без netns контейнера нельзя гарантировать routing до NAT — apply будет заблокирован.",
            ))
            blockers.append(f"Не удалось определить точку hook (netns) на entry для {label}.")

        if container:
            awg_kind = ev.get("ctn_awg_kind") or "none"
            if ev.get("ctn_has_awg") == "1":
                checks.append(CascadeCheck(
                    id=_cid("entry_awg", proto),
                    label=_plabel("Entry: AmneziaWG-стек", proto),
                    status="ok",
                    value="awg-tools" if awg_kind == "tools" else "amneziawg-go",
                    detail=f"Внутри контейнера {container} — здесь поднимется транзит.",
                ))
            else:
                checks.append(CascadeCheck(
                    id=_cid("entry_awg", proto),
                    label=_plabel("Entry: AmneziaWG-стек", proto),
                    status="danger",
                    value="Не найден в контейнере",
                    detail=f"В контейнере {container} нет awg/amneziawg-go — транзит поднять негде.",
                ))
                blockers.append(f"В контейнере {label} на входе нет AmneziaWG-стека для транзита.")

            if ev.get("ctn_has_ip") == "1":
                checks.append(CascadeCheck(
                    id=_cid("entry_ctn_ip", proto),
                    label=_plabel("Entry: iproute2 (контейнер)", proto),
                    status="ok",
                    value="Есть",
                ))
            else:
                checks.append(CascadeCheck(
                    id=_cid("entry_ctn_ip", proto),
                    label=_plabel("Entry: iproute2 (контейнер)", proto),
                    status="danger",
                    value="Нет",
                    detail=f"В {container} нет `ip` — policy routing внутри netns не настроить.",
                ))
                blockers.append(f"В контейнере {label} на входе нет iproute2.")

            if ev.get("ctn_has_tun") == "1":
                checks.append(CascadeCheck(
                    id=_cid("entry_tun", proto),
                    label=_plabel("Entry: /dev/net/tun", proto),
                    status="ok",
                    value="Есть",
                ))
            else:
                checks.append(CascadeCheck(
                    id=_cid("entry_tun", proto),
                    label=_plabel("Entry: /dev/net/tun", proto),
                    status="danger",
                    value="Нет",
                    detail="Без /dev/net/tun в контейнере userspace-транзит не создаст интерфейс.",
                ))
                blockers.append(f"В контейнере {label} на входе недоступен /dev/net/tun.")

    container = primary_container
    client_subnet = primary_subnet

    # --- Entry tooling ---
    _tool_check(checks, blockers, "entry_nft", "Entry: nftables", entry_vals.get("has_nft"))
    _tool_check(checks, blockers, "entry_ip", "Entry: iproute2 (хост)", entry_vals.get("has_ip"))

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

    # --- Exit: AmneziaWG tooling — хост ИЛИ контейнер выбранного протокола ---
    awg_host = exit_vals.get("awg") or "none"
    exit_awg_tooling = "unknown"
    for proto in protos:
        xv = per_proto[proto]["exit"]
        label = protocol_label(proto)
        exit_container = xv.get("container") or ""
        exit_ctn_awg = xv.get("ctn_has_awg") == "1"
        exit_ctn_kind = xv.get("ctn_awg_kind") or "none"
        proto_host = xv.get("awg") or awg_host
        if proto_host == "kernel" and proto == "awg2":
            exit_awg_tooling = "kernel"
            checks.append(CascadeCheck(
                id=_cid("exit_awg", proto),
                label=_plabel("Exit: AmneziaWG-стек", proto),
                status="ok",
                value="kernel-модуль (хост)",
            ))
        elif proto_host in ("userspace_tools", "userspace") and proto == "awg2":
            exit_awg_tooling = "userspace"
            checks.append(CascadeCheck(
                id=_cid("exit_awg", proto),
                label=_plabel("Exit: AmneziaWG-стек", proto),
                status="ok",
                value="userspace (хост)",
            ))
        elif exit_container and exit_ctn_awg:
            exit_awg_tooling = "container"
            checks.append(CascadeCheck(
                id=_cid("exit_awg", proto),
                label=_plabel("Exit: AmneziaWG-стек", proto),
                status="ok",
                value="awg-tools" if exit_ctn_kind == "tools" else "amneziawg-go",
                detail=f"Внутри контейнера {exit_container}. Транзит exit поднимется в его netns.",
            ))
        elif xv:
            exit_awg_tooling = "auto_install"
            extra = " Ключи 3.1 требуют Amnezia VPN 5.0.1.5+." if proto == "awg31" else ""
            checks.append(CascadeCheck(
                id=_cid("exit_awg", proto),
                label=_plabel("Exit: AmneziaWG-стек", proto),
                status="warning",
                value="Будет установлен",
                detail=f"На выходе нет {label} — панель установит его при включении каскада.{extra}",
            ))

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

    extra_used: set[int] = set()
    legs_meta: dict[str, dict] = {}
    primary_profile = None
    for proto in protos:
        slot = allocate_slot(entry_server_id, protocol=proto, extra_used=extra_used)
        extra_used.add(slot)
        profile = profile_for_slot(slot)
        if primary_profile is None:
            primary_profile = profile
        subnet = per_proto[proto].get("client_subnet")
        legs_meta[proto] = {
            "client_subnet": subnet,
            "transit_slot": profile.slot,
            "transit_port": profile.transit_port,
            "transit_subnet": profile.subnet,
            "entry_host_port": profile.entry_host_port,
            "container": per_proto[proto]["entry"].get("container") or None,
        }
        if subnet and _subnets_overlap(subnet, profile.subnet):
            checks.append(CascadeCheck(
                id=_cid("overlap", proto),
                label=_plabel("Пересечение подсетей", proto),
                status="danger",
                value=f"{subnet} ↔ {profile.subnet}",
            ))
            blockers.append(f"{protocol_label(proto)}: client subnet пересекается с transit.")
        else:
            checks.append(CascadeCheck(
                id=_cid("overlap", proto),
                label=_plabel("Пересечение подсетей", proto),
                status="ok",
                value=f"transit {profile.subnet} UDP {profile.transit_port}",
            ))

    profile = primary_profile
    transit_subnet = profile.subnet if profile else None

    ok = len(blockers) == 0
    labels = " + ".join(protocol_label(p) for p in protos)
    message = (
        f"Проверка пройдена для {labels}. Можно включать каскад."
        if ok else
        "Проверка выявила блокеры — каскад включить нельзя, пока они не устранены."
    )
    if ok and "awg31" in protos:
        message = f"{message} {CLIENT_HINT_31}"

    if profile is None:
        raise CascadeError("Не удалось выделить слот транзита.")

    live = probe_cascade_live(entry_server_id)
    if live["active"]:
        new_state = "active"
        message = "Каскад 2.0 работает. 3.1 можно добавить без выключения 2.0."
        merged_protos = merge_link_protocols(existing, protos)
        merged_legs = dict(existing.get("legs") or {})
        merged_legs.update(legs_meta)
        cascade_store.upsert_link(
            entry_server_id,
            exit_server_id=exit_server_id,
            state=new_state,
            nat_model="model_a",
            protocol=existing.get("protocol") or merged_protos[0],
            protocols=merged_protos,
            legs=merged_legs,
            client_subnet=existing.get("client_subnet") or client_subnet,
            transit_subnet=existing.get("transit_subnet") or transit_subnet,
            transit_port=existing.get("transit_port") or profile.transit_port,
            transit_slot=existing.get("transit_slot") if existing.get("transit_slot") is not None else profile.slot,
            recommended_hook=recommended_hook,
            last_preflight_at=datetime.now(timezone.utc).isoformat(),
            last_preflight_ok=ok,
            message=message,
        )
    else:
        new_state = "preflight_ok" if ok else "preflight_failed"
        cascade_store.upsert_link(
            entry_server_id,
            exit_server_id=exit_server_id,
            state=new_state,
            nat_model="model_a",
            protocol=protos[0],
            protocols=protos,
            legs=legs_meta,
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
        protocols=protos,
        client_note=CLIENT_HINT_31 if "awg31" in protos else None,
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
