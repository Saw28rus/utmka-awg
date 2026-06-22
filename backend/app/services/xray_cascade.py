"""Xray-каскад (CX2) — цепочка Xray→Xray с серверной маршрутизацией.

Модель (B, «как AWG-каскад, но на Xray»):

    Клиент --[VLESS-Reality]--> Entry (RU) Xray --routing-->
        ├─ РФ/приватка → freedom → выход с Entry (русский IP)
        └─ остальное   → VLESS-Reality outbound → Exit Xray → интернет (выход с Exit)

Ключевое отличие от прежней relay-модели: на entry стоит ПОЛНЫЙ Xray, который
терминирует Reality и сам решает, что отдать напрямую (РФ), а что увести на exit.
Маршрутизация серверная — работает для ЛЮБОГО клиента (vless:// тоже), не зависит
от того, уважает ли клиент embedded-routing. Клиент — обычный Xray-клиент entry.

Калашников: preflight fail-closed (Xray на обоих, exit healthy, entry достаёт
exit), apply ставит outbound+routing на entry аддитивно (rollback = вернуть
freedom-only) и провижинит uplink-UUID на exit; health = TLS-handshake entry→exit
обязан вернуть сертификат сайта маскировки exit.
"""

from __future__ import annotations

import json
import shlex
import uuid as uuid_mod
from datetime import datetime, timezone
from typing import Optional

from app.services.amnezia_ssh import read_container_file
from app.services.server_store import server_store
from app.services.xray_install import CONTAINER_NAME as XRAY_CONTAINER
from app.services.xray_server_config import (
    API_INBOUND_TAG,
    SERVER_CONFIG_PATH,
    ensure_monitoring_config,
    write_server_config,
)
from app.ssh import exec as ssh_exec
from app.services.xray_cascade_store import DEFAULT_RELAY_PORT, xray_cascade_store

# Теги outbound/routing в server.json entry. cascade-exit — это VLESS-Reality
# исходящий на exit; cascade-direct — freedom (РФ-трафик/приватка выходят локально).
EXIT_OUTBOUND_TAG = "cascade-exit"
DIRECT_OUTBOUND_TAG = "cascade-direct"

# Legacy nginx SNI-relay (v1.23) — в chain-модели не используется, но файлы могли
# остаться после обновления и ломать вход (SNI exit → NL вместо локального Xray).
ENTRY_STREAM_MAP_DIR = "/etc/nginx/stream.d/cascade-maps"


def _cleanup_legacy_nginx_relay(ssh) -> None:
    """Удалить все nginx-ветки старого relay-каскада на entry."""
    script = """
set -e
rm -f /etc/nginx/stream.d/cascade-maps/*.map /etc/nginx/stream.d/utmka-cascade-*.conf 2>/dev/null || true
if command -v nginx >/dev/null 2>&1 && [ -d /etc/nginx ]; then
  nginx -t && systemctl reload nginx
fi
echo NGINX_CLEANED
"""
    ssh_exec.run(ssh, script, timeout=60)


def reapply_entry_chain_config(ssh, link: dict) -> None:
    """Переписать outbound+routing на entry из сохранённого link (self-heal / после create_client)."""
    exit_host = link.get("exit_host")
    exit_port = int(link.get("exit_port") or DEFAULT_RELAY_PORT)
    uplink_uuid = link.get("uplink_uuid")
    if not exit_host or not uplink_uuid:
        raise XrayCascadeError("Каскад не сконфигурирован — включите заново.")
    outbound = _build_exit_outbound(_profile_from_link(link), exit_host, exit_port, uplink_uuid)
    _apply_entry_chain(ssh, outbound, bool(link.get("split_ru")))


class XrayCascadeError(Exception):
    pass


def _connect(server_id: str):
    target = server_store.ssh_target(server_id)
    if not target:
        raise XrayCascadeError("SSH-доступ к серверу не настроен.")
    try:
        return ssh_exec.connect(
            host=target.host, port=target.port, username=target.username,
            password=target.password, key=target.key, timeout=15,
        )
    except Exception as exc:  # noqa: BLE001
        raise XrayCascadeError(f"SSH не отвечает: {exc}") from exc


def _xray_running(ssh) -> bool:
    res = ssh_exec.run(
        ssh,
        f"docker exec {shlex.quote(XRAY_CONTAINER)} sh -c 'pgrep -x xray >/dev/null && echo ok || echo no' 2>/dev/null || echo no",
        timeout=20,
    )
    return res.stdout.strip().endswith("ok")


def _tcp_reachable(ssh, host: str, port: int) -> bool:
    res = ssh_exec.run(
        ssh,
        f"timeout 6 bash -c 'cat < /dev/null > /dev/tcp/{shlex.quote(host)}/{int(port)}' 2>/dev/null && echo ok || echo no",
        timeout=12,
    )
    return res.stdout.strip().endswith("ok")


def _tls_ok(ssh, host: str, port: int, sni: str) -> bool:
    """TLS-handshake к exit:port с маскировочным SNI должен вернуть сертификат."""
    sni_q = shlex.quote(sni or "")
    script = (
        f"timeout 10 bash -c \"echo | openssl s_client -connect {shlex.quote(host)}:{int(port)} "
        f"-servername {sni_q} 2>/dev/null\" | grep -qiE 'BEGIN CERTIFICATE|subject=|Verify return' "
        f"&& echo ok || echo no"
    )
    res = ssh_exec.run(ssh, script, timeout=20)
    return res.stdout.strip().endswith("ok")


def _exit_profile(ssh) -> dict:
    """Reality-профиль exit: SNI/порт/ключи/транспорт — для outbound на entry."""
    from app.services.xray_client import (
        PUBLIC_KEY_PATH,
        SHORT_ID_PATH,
        transport_from_inbound,
    )

    raw = read_container_file(ssh, XRAY_CONTAINER, SERVER_CONFIG_PATH)
    if not raw:
        raise XrayCascadeError("На exit не найден server.json Xray.")
    try:
        data = json.loads(raw)
        inbound = (data.get("inbounds") or [])[0]
        reality = (inbound.get("streamSettings") or {}).get("realitySettings") or {}
    except (json.JSONDecodeError, IndexError, KeyError) as exc:
        raise XrayCascadeError("server.json на exit повреждён или не Reality.") from exc
    network, flow, service_name, path = transport_from_inbound(inbound)
    pbk = read_container_file(ssh, XRAY_CONTAINER, PUBLIC_KEY_PATH)
    sid = read_container_file(ssh, XRAY_CONTAINER, SHORT_ID_PATH)
    return {
        "sni": ((reality.get("serverNames") or [""])[0]) or "",
        "exit_port": int(inbound.get("port") or DEFAULT_RELAY_PORT),
        "public_key": pbk or "",
        "short_id": sid or "",
        "flow": flow,
        "network": network,
        "service_name": service_name,
        "path": path,
    }


# ---------------------------------------------------------------------------
# Сборка outbound/routing на entry
# ---------------------------------------------------------------------------


def _build_exit_outbound(profile: dict, exit_host: str, exit_port: int, uplink_uuid: str) -> dict:
    """VLESS-Reality outbound entry→exit (entry выступает клиентом exit)."""
    net = (profile.get("network") or "tcp").lower()
    user: dict = {"id": uplink_uuid, "encryption": "none", "level": 0}
    flow = profile.get("flow")
    if flow and net in ("tcp", "raw"):
        user["flow"] = flow
    stream: dict = {
        "network": net,
        "security": "reality",
        "realitySettings": {
            "fingerprint": "chrome",
            "serverName": profile.get("sni") or "",
            "publicKey": profile.get("public_key") or "",
            "shortId": profile.get("short_id") or "",
            "spiderX": "",
        },
    }
    if net == "grpc":
        stream["grpcSettings"] = {"serviceName": profile.get("service_name") or "", "multiMode": False}
    elif net == "xhttp":
        stream["xhttpSettings"] = {"path": profile.get("path") or "/"}
    return {
        "tag": EXIT_OUTBOUND_TAG,
        "protocol": "vless",
        "settings": {"vnext": [{"address": exit_host, "port": int(exit_port), "users": [user]}]},
        "streamSettings": stream,
    }


def _build_routing_rules(split_ru: bool) -> list[dict]:
    """Серверные правила entry. api → api, дальше — split.

    split_ru=True: РФ-домены/IP и приватка → выход локально (freedom), прочее → exit.
    split_ru=False: весь пользовательский трафик → exit (полный тоннель через NL).
    """
    rules: list[dict] = [
        {"type": "field", "inboundTag": [API_INBOUND_TAG], "outboundTag": API_INBOUND_TAG}
    ]
    if split_ru:
        rules.append({"type": "field", "outboundTag": DIRECT_OUTBOUND_TAG,
                      "domain": ["geosite:category-ru"]})
        rules.append({"type": "field", "outboundTag": DIRECT_OUTBOUND_TAG,
                      "ip": ["geoip:ru", "geoip:private"]})
    rules.append({"type": "field", "outboundTag": EXIT_OUTBOUND_TAG, "network": "tcp,udp"})
    return rules


def _apply_entry_chain(ssh, exit_outbound: dict, split_ru: bool) -> None:
    """Прописать на entry outbound на exit + split-routing и горячо перечитать."""
    from app.services.xray_client import ClientCreateError, _read_server_config, _reload_xray

    config = _read_server_config(ssh)
    ensure_monitoring_config(config)  # сохраняем Stats API / email клиентов entry
    config["outbounds"] = [
        exit_outbound,
        {"tag": DIRECT_OUTBOUND_TAG, "protocol": "freedom"},
    ]
    config.setdefault("routing", {})
    config["routing"]["domainStrategy"] = "IPIfNonMatch"
    config["routing"]["rules"] = _build_routing_rules(split_ru)
    if not write_server_config(ssh, config):
        raise XrayCascadeError("Не удалось записать server.json на entry.")
    try:
        _reload_xray(ssh)
    except ClientCreateError as exc:
        raise XrayCascadeError(f"Xray на entry не принял конфиг каскада: {exc}") from exc


def _remove_entry_chain(ssh) -> None:
    """Вернуть entry к обычному freedom-only (выход локально), снять split-routing."""
    from app.services.xray_client import _read_server_config, _reload_xray

    config = _read_server_config(ssh)
    ensure_monitoring_config(config)
    config["outbounds"] = [{"protocol": "freedom"}]
    config["routing"] = {
        "rules": [{"type": "field", "inboundTag": [API_INBOUND_TAG], "outboundTag": API_INBOUND_TAG}]
    }
    write_server_config(ssh, config)
    try:
        _reload_xray(ssh)
    except Exception:  # noqa: BLE001
        # Rollback должен быть устойчивым: даже если reload зацепило — конфиг уже
        # вернули, контейнер перечитает при следующем рестарте.
        pass


def _chain_alive(ssh) -> bool:
    """На entry активна цепочка: в server.json есть outbound cascade-exit и xray жив."""
    raw = read_container_file(ssh, XRAY_CONTAINER, SERVER_CONFIG_PATH)
    if not raw:
        return False
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return False
    outs = data.get("outbounds") or []
    has_exit = any(isinstance(o, dict) and o.get("tag") == EXIT_OUTBOUND_TAG for o in outs)
    return has_exit and _xray_running(ssh)


def _profile_from_link(link: dict) -> dict:
    return {
        "sni": link.get("sni") or "",
        "exit_port": int(link.get("exit_port") or DEFAULT_RELAY_PORT),
        "public_key": link.get("exit_public_key") or "",
        "short_id": link.get("exit_short_id") or "",
        "flow": link.get("exit_flow") or "",
        "network": link.get("exit_network") or "tcp",
        "service_name": link.get("exit_service_name"),
        "path": link.get("exit_path"),
    }


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


def xray_cascade_preflight(entry_id: str, exit_id: str, relay_port: Optional[int] = None) -> dict:
    """relay_port игнорируется (chain-режим: вход всегда на своём Xray :443)."""
    if entry_id == exit_id:
        raise XrayCascadeError("Entry и exit не могут быть одним сервером.")
    entry_rec = server_store.get_record(entry_id)
    exit_rec = server_store.get_record(exit_id)
    if not entry_rec:
        raise XrayCascadeError("Entry-сервер не найден.")
    if not exit_rec:
        raise XrayCascadeError("Exit-сервер не найден.")

    checks: list[dict] = []
    blockers: list[str] = []

    # Entry обязан иметь свой Xray — он терминирует Reality и маршрутизирует.
    entry_has_xray = server_store.has_xray(entry_rec)
    checks.append({"id": "entry_xray", "label": "Entry: Xray установлен",
                   "status": "ok" if entry_has_xray else "danger",
                   "value": "есть" if entry_has_xray else "нет"})
    if not entry_has_xray:
        blockers.append("На entry (РФ) не установлен Xray — поставьте протокол Xray на этом сервере.")

    if not server_store.has_xray(exit_rec):
        raise XrayCascadeError("На exit-сервере не установлен Xray (VLESS-Reality).")

    exit_ssh = _connect(exit_id)
    try:
        running = _xray_running(exit_ssh)
        checks.append({"id": "exit_xray", "label": "Exit: Xray запущен",
                       "status": "ok" if running else "danger",
                       "value": "running" if running else "не запущен"})
        if not running:
            blockers.append("Xray на exit не запущен.")
        profile = _exit_profile(exit_ssh)
        exit_port = profile["exit_port"]
        sni = profile["sni"]
        ok_keys = bool(profile["public_key"] and profile["short_id"])
        checks.append({"id": "exit_reality", "label": "Exit: Reality",
                       "status": "ok" if (sni and ok_keys) else "danger",
                       "value": f"SNI {sni or '—'}, порт {exit_port}"})
        if not sni:
            blockers.append("У exit не задан Reality-SNI.")
        if not ok_keys:
            blockers.append("На exit не найдены Reality-ключи (pbk/sid) — переустановите Xray на exit.")
    finally:
        exit_ssh.close()

    exit_host = exit_rec.get("host")

    if entry_has_xray:
        entry_ssh = _connect(entry_id)
        try:
            entry_running = _xray_running(entry_ssh)
            checks.append({"id": "entry_running", "label": "Entry: Xray запущен",
                           "status": "ok" if entry_running else "danger",
                           "value": "running" if entry_running else "не запущен"})
            if not entry_running:
                blockers.append("Xray на entry не запущен.")
            reach = _tcp_reachable(entry_ssh, exit_host, exit_port)
            checks.append({"id": "entry_to_exit", "label": "Entry → Exit (TCP)",
                           "status": "ok" if reach else "danger",
                           "value": f"{exit_host}:{exit_port} {'доступен' if reach else 'недоступен'}"})
            if not reach:
                blockers.append(f"Entry не достаёт exit {exit_host}:{exit_port} по TCP.")
        finally:
            entry_ssh.close()

    ok = not blockers
    state = "preflight_ok" if ok else "preflight_failed"
    xray_cascade_store.upsert_link(
        entry_id,
        exit_server_id=exit_id,
        relay_port=443,
        exit_port=exit_port,
        exit_host=exit_host,
        sni=sni,
        mode="chain",
        state=state,
        last_preflight_at=datetime.now(timezone.utc).isoformat(),
        last_preflight_ok=ok,
        message=("Проверка пройдена — вход на своём Xray :443, выход через exit."
                 if ok else "Проверка выявила блокеры."),
    )
    return {
        "ok": ok, "entry_server_id": entry_id, "exit_server_id": exit_id,
        "relay_port": 443, "exit_port": exit_port, "sni": sni, "mode": "chain",
        "checks": checks, "blockers": blockers,
        "message": "Preflight пройден." if ok else "Preflight выявил блокеры.",
    }


# ---------------------------------------------------------------------------
# Apply / Rollback
# ---------------------------------------------------------------------------


def xray_cascade_apply(entry_id: str) -> dict:
    link = xray_cascade_store.get_link(entry_id)
    if not link:
        raise XrayCascadeError("Сначала выполните проверку (preflight).")
    if not link.get("last_preflight_ok"):
        raise XrayCascadeError("Проверка не пройдена — включение заблокировано.")
    exit_id = link.get("exit_server_id")
    exit_host = link.get("exit_host")
    if not exit_id or not exit_host:
        raise XrayCascadeError("Не задан exit-сервер.")
    split_ru = link.get("split_ru")
    if split_ru is None:
        split_ru = True  # по умолчанию РФ выходит локально (как AWG-каскад)

    from app.services.xray_client import (
        ClientCreateError,
        _append_client_to_server,
        _read_server_config,
    )

    steps: list[dict] = []

    # 1) Снимаем актуальный Reality-профиль exit и провижиним uplink-UUID.
    exit_ssh = _connect(exit_id)
    try:
        profile = _exit_profile(exit_ssh)
        exit_port = profile["exit_port"]
        sni = profile["sni"]
        if not sni or not profile["public_key"] or not profile["short_id"]:
            raise XrayCascadeError("Профиль Reality на exit неполный — переустановите Xray на exit.")
        uplink_uuid = link.get("uplink_uuid") or str(uuid_mod.uuid4())
        exit_config = _read_server_config(exit_ssh)
        try:
            _append_client_to_server(exit_ssh, exit_config, uplink_uuid, profile["flow"])
        except ClientCreateError as exc:
            raise XrayCascadeError(f"Не удалось добавить uplink на exit: {exc}") from exc
        steps.append({"name": "Exit: провижининг uplink-UUID", "status": "ok"})
    finally:
        exit_ssh.close()

    # 2) Снимаем legacy nginx-relay (v1.23) и прописываем outbound→exit + split-routing.
    entry_ssh = _connect(entry_id)
    try:
        _cleanup_legacy_nginx_relay(entry_ssh)
        steps.append({"name": "Entry: снят legacy nginx-relay", "status": "ok"})
        outbound = _build_exit_outbound(profile, exit_host, exit_port, uplink_uuid)
        try:
            _apply_entry_chain(entry_ssh, outbound, bool(split_ru))
        except XrayCascadeError:
            _remove_entry_chain(entry_ssh)
            raise
        steps.append({"name": "Entry: outbound→exit + split-routing", "status": "ok"})

        if not _chain_alive(entry_ssh):
            _remove_entry_chain(entry_ssh)
            steps.append({"name": "Health: цепочка на entry", "status": "failed"})
            xray_cascade_store.upsert_link(entry_id, state="rolled_back",
                                           message="Health не пройден — цепочка снята (откат).")
            raise XrayCascadeError("Health-check провален: цепочка на entry не поднялась. Откат выполнен.")

        # TLS-handshake entry→exit с маскировочным SNI — exit реально отвечает Reality.
        tls = _tls_ok(entry_ssh, exit_host, exit_port, sni)
        steps.append({"name": "Health: Reality-handshake entry→exit",
                      "status": "ok" if tls else "warning",
                      "detail": f"TLS к {sni} на {exit_host}:{exit_port}"})
    finally:
        entry_ssh.close()

    xray_cascade_store.upsert_link(
        entry_id, state="active", desired="up", mode="chain",
        exit_port=exit_port, exit_host=exit_host, sni=sni,
        uplink_uuid=uplink_uuid,
        exit_public_key=profile["public_key"], exit_short_id=profile["short_id"],
        exit_flow=profile["flow"], exit_network=profile["network"],
        exit_service_name=profile["service_name"], exit_path=profile["path"],
        split_ru=bool(split_ru),
        last_applied_at=datetime.now(timezone.utc).isoformat(),
        message=("Xray-каскад активен: клиент → entry (РФ напрямую) → exit для прочего."
                 if split_ru else "Xray-каскад активен: весь трафик клиент → entry → exit."),
    )
    return {"ok": True, "state": "active", "entry_server_id": entry_id,
            "exit_server_id": exit_id, "relay_port": 443, "mode": "chain",
            "split_ru": bool(split_ru), "steps": steps, "message": "Xray-каскад включён."}


def xray_cascade_rollback(entry_id: str) -> dict:
    link = xray_cascade_store.get_link(entry_id)
    if not link:
        raise XrayCascadeError("Xray-каскад для этого сервера не найден.")
    exit_id = link.get("exit_server_id")
    uplink_uuid = link.get("uplink_uuid")

    entry_ssh = _connect(entry_id)
    try:
        _remove_entry_chain(entry_ssh)
        _cleanup_legacy_nginx_relay(entry_ssh)
    finally:
        entry_ssh.close()

    # Снимаем uplink-UUID с exit (best-effort — каскад выключается в любом случае).
    if exit_id and uplink_uuid:
        try:
            from app.services.xray_client import delete_xray_client

            delete_xray_client(exit_id, uplink_uuid)
        except Exception:  # noqa: BLE001
            pass

    xray_cascade_store.upsert_link(
        entry_id, state="down", desired="down",
        message="Цепочка снята, entry выходит локально. Каскад выключен.",
    )
    return {"ok": True, "state": "down", "entry_server_id": entry_id,
            "message": "Xray-каскад выключен."}


# ---------------------------------------------------------------------------
# Reconcile (self-heal) / статус
# ---------------------------------------------------------------------------


def _desired_up(link: dict) -> bool:
    desired = link.get("desired")
    if desired:
        return desired == "up"
    return (link.get("state") or "") == "active"


def _routing_needs_heal(ssh) -> bool:
    """True, если cascade-exit пропал из routing (типично после create_client до фикса)."""
    raw = read_container_file(ssh, XRAY_CONTAINER, SERVER_CONFIG_PATH)
    if not raw:
        return True
    try:
        rules = (json.loads(raw).get("routing") or {}).get("rules") or []
    except json.JSONDecodeError:
        return True
    return not any(
        isinstance(r, dict) and r.get("outboundTag") == EXIT_OUTBOUND_TAG for r in rules
    )


def _refresh_exit_keys_if_changed(entry_id: str, link: dict) -> bool:
    """Если Reality-ключи exit изменились (переустановка Xray на exit) — обновить.

    Самая частая причина «каскад активен, но трафик не идёт»: exit переустановили,
    pbk/sid/uuid сменились, а на entry записаны старые. Перечитываем профиль exit,
    и при расхождении заново заводим uplink на exit и переписываем outbound на entry.
    Возвращает True, если что-то обновили.
    """
    exit_id = link.get("exit_server_id")
    if not exit_id:
        return False
    try:
        exit_ssh = _connect(exit_id)
    except XrayCascadeError:
        return False
    try:
        profile = _exit_profile(exit_ssh)
        if not profile.get("public_key") or not profile.get("short_id") or not profile.get("sni"):
            return False
        same_keys = (
            profile["public_key"] == link.get("exit_public_key")
            and profile["short_id"] == link.get("exit_short_id")
            and profile["sni"] == link.get("sni")
            and int(profile["exit_port"]) == int(link.get("exit_port") or 0)
        )
        uplink_uuid = link.get("uplink_uuid")
        from app.services.xray_client import (
            ClientCreateError,
            _append_client_to_server,
            _read_server_config,
        )

        exit_cfg = _read_server_config(exit_ssh)
        clients = exit_cfg["inbounds"][0].get("settings", {}).get("clients", [])
        uplink_present = bool(uplink_uuid) and any(
            isinstance(c, dict) and c.get("id") == uplink_uuid for c in clients
        )
        if same_keys and uplink_present:
            return False  # всё совпадает — обновлять нечего

        if not uplink_uuid:
            uplink_uuid = str(uuid_mod.uuid4())
        if not uplink_present:
            try:
                _append_client_to_server(exit_ssh, exit_cfg, uplink_uuid, profile["flow"])
            except ClientCreateError:
                return False
    finally:
        exit_ssh.close()

    xray_cascade_store.upsert_link(
        entry_id, uplink_uuid=uplink_uuid, sni=profile["sni"],
        exit_port=profile["exit_port"], exit_public_key=profile["public_key"],
        exit_short_id=profile["short_id"], exit_flow=profile["flow"],
        exit_network=profile["network"], exit_service_name=profile["service_name"],
        exit_path=profile["path"],
        message="Ключи exit изменились — каскад переинициализирован автоматически.",
    )
    return True


def reconcile_xray_cascade(entry_id: str, *, heal: bool = True) -> dict:
    """Сверяет цепочку на entry с намерением и (при heal) восстанавливает её.

    Конфиг каскада живёт в server.json контейнера и переживает рестарт. Heal нужен,
    если контейнер пересоздали/конфиг сбросили: переписываем outbound+routing из
    сохранённого профиля exit. Дополнительно ловим смену ключей exit (переустановка)
    и переинициализируем каскад свежими ключами.
    """
    link = xray_cascade_store.get_link(entry_id)
    if not link or not link.get("exit_host"):
        return {"entry": entry_id, "skipped": True}
    if not _desired_up(link):
        return {"entry": entry_id, "skipped": True}

    exit_host = link.get("exit_host")
    exit_port = int(link.get("exit_port") or DEFAULT_RELAY_PORT)
    uplink_uuid = link.get("uplink_uuid")
    split_ru = bool(link.get("split_ru"))

    # Сверяем ключи exit ДО проверки entry: смена ключей на exit (переустановка)
    # ломает цепочку молча, и без обновления никакой heal на entry не поможет.
    keys_refreshed = False
    if heal:
        try:
            keys_refreshed = _refresh_exit_keys_if_changed(entry_id, link)
        except Exception:  # noqa: BLE001
            keys_refreshed = False
        if keys_refreshed:
            link = xray_cascade_store.get_link(entry_id) or link
            uplink_uuid = link.get("uplink_uuid")

    try:
        ssh = _connect(entry_id)
    except XrayCascadeError:
        return {"entry": entry_id, "ok": False, "error": "ssh-unreachable"}
    try:
        _cleanup_legacy_nginx_relay(ssh)
        if _chain_alive(ssh) and not keys_refreshed:
            if _routing_needs_heal(ssh) and uplink_uuid:
                try:
                    reapply_entry_chain_config(ssh, link)
                except XrayCascadeError:
                    pass
            if (link.get("state") or "") != "active":
                xray_cascade_store.upsert_link(entry_id, state="active", message="Каскад активен.")
            return {"entry": entry_id, "ok": True, "healed": False, "state": "active"}
        if keys_refreshed and uplink_uuid:
            # Ключи exit обновились — переписываем outbound на entry свежими ключами.
            try:
                reapply_entry_chain_config(ssh, link)
            except XrayCascadeError:
                pass
            if _chain_alive(ssh):
                xray_cascade_store.upsert_link(
                    entry_id, state="active",
                    last_healed_at=datetime.now(timezone.utc).isoformat(),
                    message="Ключи exit изменились — каскад переинициализирован (self-heal).",
                )
                return {"entry": entry_id, "ok": True, "healed": True, "state": "active"}
        if not heal or not uplink_uuid:
            xray_cascade_store.upsert_link(entry_id, state="down",
                                           message="Цепочка каскада не обнаружена на entry.")
            return {"entry": entry_id, "ok": False, "healed": False, "state": "down"}

        _cleanup_legacy_nginx_relay(ssh)
        outbound = _build_exit_outbound(_profile_from_link(link), exit_host, exit_port, uplink_uuid)
        try:
            _apply_entry_chain(ssh, outbound, split_ru)
        except XrayCascadeError:
            xray_cascade_store.upsert_link(entry_id, state="down",
                                           message="Каскад упал, авто-восстановление не удалось.")
            return {"entry": entry_id, "ok": False, "healed": False, "state": "down"}
        if _chain_alive(ssh):
            xray_cascade_store.upsert_link(
                entry_id, state="active",
                last_healed_at=datetime.now(timezone.utc).isoformat(),
                message="Каскад восстановлен автоматически (self-heal).",
            )
            return {"entry": entry_id, "ok": True, "healed": True, "state": "active"}
        xray_cascade_store.upsert_link(entry_id, state="down",
                                       message="Каскад упал, авто-восстановление не удалось.")
        return {"entry": entry_id, "ok": False, "healed": False, "state": "down"}
    finally:
        ssh.close()


def reconcile_all_xray_cascades() -> dict:
    """Фоновый проход по всем каскадам с намерением up (планировщик)."""
    healed = 0
    checked = 0
    for link in xray_cascade_store.list_links():
        if not _desired_up(link):
            continue
        entry_id = link.get("entry_server_id")
        if not entry_id:
            continue
        checked += 1
        try:
            result = reconcile_xray_cascade(entry_id, heal=True)
            if result.get("healed"):
                healed += 1
        except Exception:  # noqa: BLE001
            continue
    return {"checked": checked, "healed": healed}


def xray_cascade_status(entry_id: str) -> dict:
    link = xray_cascade_store.get_link(entry_id) or {}
    exit_id = link.get("exit_server_id")
    exit_rec = server_store.get_record(exit_id) if exit_id else None
    state = link.get("state") or "none"
    live_active = False
    if _desired_up(link) and link.get("exit_host"):
        result = reconcile_xray_cascade(entry_id, heal=True)
        live_active = bool(result.get("ok") and result.get("state") == "active")
        link = xray_cascade_store.get_link(entry_id) or link
        state = link.get("state") or state
    return {
        "entry_server_id": entry_id,
        "exit_server_id": exit_id,
        "exit_name": exit_rec.get("name") if exit_rec else None,
        "relay_port": link.get("relay_port") or 443,
        "exit_port": link.get("exit_port"),
        "sni": link.get("sni"),
        "mode": link.get("mode") or "chain",
        "split_ru": bool(link.get("split_ru")),
        "state": state,
        "live_active": live_active,
        "last_preflight_ok": link.get("last_preflight_ok", False),
        "last_applied_at": link.get("last_applied_at"),
        "last_healed_at": link.get("last_healed_at"),
        "message": link.get("message"),
    }


# ---------------------------------------------------------------------------
# Split-правило (серверное) и выдача клиентов
# ---------------------------------------------------------------------------


def set_xray_cascade_rules(entry_id: str, enabled: bool) -> dict:
    """Split на стороне entry: РФ напрямую (enabled) или всё через exit (off).

    Серверное правило — переписываем routing на entry. Конфиги клиентов НЕ трогаем
    (они адресованы на entry и работают одинаково при любом split). Срабатывает для
    всех клиентов, включая vless://.
    """
    link = xray_cascade_store.get_link(entry_id)
    if not link:
        raise XrayCascadeError("Xray-каскад для этого узла не настроен.")
    if (link.get("state") or "") != "active":
        raise XrayCascadeError("Каскад не активен.")
    exit_host = link.get("exit_host")
    exit_port = int(link.get("exit_port") or DEFAULT_RELAY_PORT)
    uplink_uuid = link.get("uplink_uuid")
    if not exit_host or not uplink_uuid:
        raise XrayCascadeError("Каскад не сконфигурирован — включите его заново.")

    entry_ssh = _connect(entry_id)
    try:
        link_apply = {**link, "split_ru": bool(enabled)}
        reapply_entry_chain_config(entry_ssh, link_apply)
        if not _chain_alive(entry_ssh):
            raise XrayCascadeError("Не удалось применить split-правило на entry.")
    finally:
        entry_ssh.close()

    xray_cascade_store.upsert_link(entry_id, split_ru=bool(enabled))
    return {
        "ok": True,
        "split_ru": bool(enabled),
        "message": (
            "Split включён: РФ-трафик выходит с entry (РФ), остальное — через exit."
            if enabled else
            "Split выключен: весь трафик клиентов идёт через exit."
        ),
    }


def create_xray_cascade_client(
    entry_id: str,
    name: str,
    *,
    format: str = "both",
    traffic_limit_bytes: Optional[int] = None,
    expires_at: Optional[str] = None,
    fingerprint: Optional[str] = None,
):
    """Выдать клиента в Xray-каскад (chain-модель).

    Клиент — обычный Xray-клиент ENTRY (UUID/ключи на entry, конфиг на entry:443).
    Раздвоение РФ/заграница делает серверный routing на entry — клиенту ничего знать
    не нужно, работает в любом приложении (vless:// тоже).
    """
    link = xray_cascade_store.get_link(entry_id)
    if not link:
        raise XrayCascadeError("Xray-каскад для этого узла не настроен.")
    if (link.get("state") or "") != "active":
        raise XrayCascadeError("Каскад не активен — сначала включите его.")

    from app.services.xray_client import ClientCreateError, create_xray_client

    try:
        return create_xray_client(
            entry_id,
            name,
            format=format,
            traffic_limit_bytes=traffic_limit_bytes,
            expires_at=expires_at,
            channel_entry_id=entry_id,
            fingerprint=fingerprint,
        )
    except ClientCreateError as exc:
        raise XrayCascadeError(str(exc)) from exc


def active_entry_map() -> dict[str, dict]:
    """entry_id → инфо активного Xray-каскада (для выдачи клиентов из общей формы)."""
    out: dict[str, dict] = {}
    for link in xray_cascade_store.list_links():
        if (link.get("state") or "") != "active":
            continue
        entry_id = link.get("entry_server_id")
        if not entry_id:
            continue
        exit_id = link.get("exit_server_id")
        exit_rec = server_store.get_record(exit_id) if exit_id else None
        out[entry_id] = {
            "exit_server_id": exit_id,
            "exit_name": (exit_rec.get("name") if exit_rec else None),
        }
    return out


def list_xray_cascades() -> list[dict]:
    out: list[dict] = []
    for link in xray_cascade_store.list_links():
        if (link.get("state") or "none") == "none":
            continue
        out.append(link)
    return out
