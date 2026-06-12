"""AWG Masking Center — фаза M1: инспектор (read-only).

Читает конфиг AmneziaWG с управляемого сервера, парсит параметры обфускации
(`J/S/H/I`), определяет версию и считает «masking posture» (strong/basic/weak/
legacy/invalid). Ничего на сервере не меняет.

ВАЖНО: статус — это качество конфигурации, а НЕ гарантия обхода ТСПУ.
"""

from __future__ import annotations

import json
import re
import shlex
from datetime import datetime, timezone
from typing import Optional

from app.schemas.awg_masking import (
    MASK_STATUS_BASIC,
    MASK_STATUS_INVALID,
    MASK_STATUS_LEGACY,
    MASK_STATUS_STRONG,
    MASK_STATUS_UNKNOWN,
    MASK_STATUS_WEAK,
    MASK_VERSION_AWG2,
    MASK_VERSION_AWG15,
    MASK_VERSION_LEGACY,
    MASK_VERSION_UNKNOWN,
    MaskingResponse,
    MaskingScore,
    MaskingState,
    MaskingWarning,
    RealityFallback,
)
from app.services.awg_config import parse_interface
from app.services.client_store import client_store
from app.services.server_store import server_store
from app.ssh import exec as ssh_exec

# Кандидаты пути конфига внутри контейнера Amnezia.
CONFIG_PATHS = (
    "/opt/amnezia/awg/awg0.conf",
    "/opt/amnezia/awg/wg0.conf",
    "/etc/amnezia/amneziawg/awg0.conf",
    "/etc/amnezia/amneziawg/wg0.conf",
    "/etc/wireguard/awg0.conf",
    "/etc/wireguard/wg0.conf",
)

# Исторический статический fallback каскада (удалён из production в M4).
# Если параметры сервера совпадают с ним — это узнаваемый «учебниковый» отпечаток.
STATIC_FALLBACK_H = {
    "H1": "1871418625-2043862971",
    "H2": "2115335747-2145071315",
    "H3": "2145892709-2146591227",
    "H4": "2147152343-2147329661",
}

DEFAULT_WG_PORT = 51820
ROTATION_REMINDER_DAYS = 60

# M6: Reality (Xray) — запасной канал по TCP/443 на случай полного UDP-бана.
XRAY_CONTAINER = "amnezia-xray"
XRAY_SERVER_CONFIG = "/opt/amnezia/xray/server.json"

_LABELS = {
    MASK_STATUS_STRONG: "Сильная маскировка AWG 2.0",
    MASK_STATUS_BASIC: "AWG 2.0 базовая",
    MASK_STATUS_WEAK: "Слабая маскировка",
    MASK_STATUS_LEGACY: "Legacy",
    MASK_STATUS_INVALID: "Конфиг невалиден",
    MASK_STATUS_UNKNOWN: "Неизвестно",
}


def read_masking(server_id: str) -> MaskingResponse:
    """Главная точка входа M1. Блокирующая (SSH) — вызывать через asyncio.to_thread."""
    now = datetime.now(timezone.utc).isoformat()
    record = server_store.get_record(server_id)
    target = server_store.ssh_target(server_id)
    if not record or not target:
        return MaskingResponse(
            ok=False,
            server_id=server_id,
            score=MaskingScore(status=MASK_STATUS_UNKNOWN, label=_LABELS[MASK_STATUS_UNKNOWN]),
            read_error="Сервер не найден.",
            checked_at=now,
        )

    try:
        ssh = ssh_exec.connect(
            host=target.host,
            port=target.port,
            username=target.username,
            password=target.password,
            key=target.key,
            timeout=15,
        )
    except Exception as exc:  # noqa: BLE001
        return MaskingResponse(
            ok=False,
            server_id=server_id,
            score=MaskingScore(status=MASK_STATUS_UNKNOWN, label=_LABELS[MASK_STATUS_UNKNOWN]),
            read_error=f"SSH не отвечает: {exc}",
            checked_at=now,
        )

    try:
        fallback = _read_reality_fallback(ssh, record, server_id, target.host)

        container = _find_awg_container(ssh)
        config_text = ""
        config_path: Optional[str] = None
        if container:
            config_path, config_text = _read_container_config(ssh, container)
        if not config_text.strip():
            host_path, host_text = _read_host_config(ssh)
            if host_text.strip():
                config_path, config_text = host_path, host_text

        if not config_text.strip():
            return MaskingResponse(
                ok=False,
                server_id=server_id,
                score=MaskingScore(status=MASK_STATUS_UNKNOWN, label=_LABELS[MASK_STATUS_UNKNOWN]),
                read_error="Конфиг AmneziaWG не найден на сервере.",
                checked_at=now,
                fallback=fallback,
            )

        state = _build_state(config_text, container=container, config_path=config_path, host=target.host)
        warnings = _collect_warnings(state)
        score = _score(state, warnings)

        last_rotation_at = record.get("last_masking_rotation_at")
        age_days = _rotation_age_days(last_rotation_at)
        if (
            score.status in (MASK_STATUS_STRONG, MASK_STATUS_BASIC)
            and age_days is not None
            and age_days >= ROTATION_REMINDER_DAYS
        ):
            warnings.append(
                MaskingWarning(
                    level="info",
                    code="rotation_stale",
                    message=f"Параметры маскировки не менялись {age_days} дн. "
                    "Периодическая ротация снижает накопление отпечатка у DPI.",
                )
            )

        return MaskingResponse(
            ok=True,
            server_id=server_id,
            state=state,
            score=score,
            warnings=warnings,
            checked_at=now,
            last_rotation_at=last_rotation_at,
            rotation_age_days=age_days,
            fallback=fallback,
        )
    finally:
        ssh.close()


# --- чтение конфигурации -----------------------------------------------------


def _find_awg_container(ssh) -> Optional[str]:
    out = ssh_exec.run(
        ssh,
        "docker ps -a --format '{{.Names}}' 2>/dev/null || true",
        timeout=20,
    ).stdout
    names = [line.strip() for line in out.splitlines() if line.strip()]
    service_markers = ("panel", "web", "db", "dind", "postgres", "redis")
    candidates = [n for n in names if not any(m in n.lower() for m in service_markers)]
    # AWG 2.0 приоритетнее legacy.
    for name in candidates:
        if "awg2" in name.lower() or "amnezia-awg2" in name.lower():
            return name
    for name in candidates:
        low = name.lower()
        if "awg" in low or "amnezia" in low:
            return name
    return None


def _read_container_config(ssh, container: str) -> tuple[Optional[str], str]:
    inner = (
        "for p in " + " ".join(shlex.quote(p) for p in CONFIG_PATHS) + "; do "
        "if [ -f \"$p\" ]; then echo \"#PATH:$p\"; cat \"$p\"; break; fi; done"
    )
    cmd = f"sudo docker exec {shlex.quote(container)} sh -c {shlex.quote(inner)} 2>/dev/null || true"
    out = ssh_exec.run(ssh, cmd, timeout=20).stdout
    return _split_path_marker(out)


def _read_host_config(ssh) -> tuple[Optional[str], str]:
    inner = (
        "for p in " + " ".join(shlex.quote(p) for p in CONFIG_PATHS) + "; do "
        "if [ -f \"$p\" ]; then echo \"#PATH:$p\"; cat \"$p\"; break; fi; done"
    )
    cmd = f"sudo sh -c {shlex.quote(inner)} 2>/dev/null || true"
    out = ssh_exec.run(ssh, cmd, timeout=20).stdout
    return _split_path_marker(out)


def _split_path_marker(out: str) -> tuple[Optional[str], str]:
    lines = out.splitlines()
    if lines and lines[0].startswith("#PATH:"):
        return lines[0][len("#PATH:"):].strip(), "\n".join(lines[1:])
    return None, out


# --- M6: Reality fallback ------------------------------------------------------


def _read_reality_fallback(ssh, record: dict, server_id: str, host: str) -> RealityFallback:
    """Состояние Reality (Xray) как запасного канала. Не влияет на masking score."""
    status = ssh_exec.run(
        ssh,
        f"sudo docker inspect -f '{{{{.State.Status}}}}' {shlex.quote(XRAY_CONTAINER)} 2>/dev/null || true",
        timeout=20,
    ).stdout.strip()

    if not status:
        return RealityFallback(installed=False)

    running = status == "running"
    sni = _read_reality_sni(ssh)
    port = _reality_port(record)
    clients_total = sum(
        1
        for c in client_store.list_all(server_id)
        if (c.protocol or "").lower() == "xray"
    )

    warnings: list[MaskingWarning] = []
    if not running:
        warnings.append(
            MaskingWarning(
                level="warning",
                code="reality_stopped",
                message=f"Контейнер Reality установлен, но не запущен (статус: {status}). "
                "Запасной канал сейчас не работает.",
            )
        )

    own_hosts = {host.lower()}
    endpoint_host = (record.get("endpoint_host") or "").strip().lower()
    if endpoint_host:
        own_hosts.add(endpoint_host)
    if sni and sni.lower() in own_hosts:
        warnings.append(
            MaskingWarning(
                level="warning",
                code="reality_sni_self",
                message=f"SNI Reality ({sni}) указывает на адрес самого сервера. "
                "Reality должен имитировать внешний популярный TLS-сайт, а не ваш домен.",
            )
        )
    elif not sni:
        warnings.append(
            MaskingWarning(
                level="info",
                code="reality_sni_unknown",
                message="Не удалось прочитать SNI из конфига Reality на сервере.",
            )
        )

    if running and clients_total == 0:
        warnings.append(
            MaskingWarning(
                level="info",
                code="reality_no_clients",
                message="Reality работает, но ни одному клиенту не выдан запасной профиль. "
                "Выдайте его заранее, пока UDP (AmneziaWG) не заблокирован.",
            )
        )

    return RealityFallback(
        installed=True,
        running=running,
        container=XRAY_CONTAINER,
        port=port,
        sni=sni,
        clients_total=clients_total,
        warnings=warnings,
    )


def _read_reality_sni(ssh) -> Optional[str]:
    out = ssh_exec.run(
        ssh,
        f"sudo docker exec {shlex.quote(XRAY_CONTAINER)} cat {shlex.quote(XRAY_SERVER_CONFIG)} 2>/dev/null || true",
        timeout=20,
    ).stdout
    if not out.strip():
        return None
    try:
        config = json.loads(out)
    except ValueError:
        return None
    for inbound in config.get("inbounds") or []:
        if not isinstance(inbound, dict) or inbound.get("protocol") != "vless":
            continue
        reality = ((inbound.get("streamSettings") or {}).get("realitySettings")) or {}
        names = reality.get("serverNames") or []
        if names and isinstance(names[0], str) and names[0].strip():
            return names[0].strip()
        dest = reality.get("dest")
        if isinstance(dest, str) and dest.strip():
            return dest.split(":", 1)[0].strip() or None
    return None


def _reality_port(record: dict) -> Optional[int]:
    protocols = record.get("installed_protocols") or {}
    xray = protocols.get("xray") or {}
    port = xray.get("port")
    try:
        return int(port) if port is not None else None
    except (ValueError, TypeError):
        return None


# --- разбор и оценка ---------------------------------------------------------


def _build_state(
    config_text: str,
    *,
    container: Optional[str],
    config_path: Optional[str],
    host: str,
) -> MaskingState:
    info = parse_interface(config_text)
    params = info.awg_params

    h_values = [params.get(k) for k in ("H1", "H2", "H3", "H4")]
    h_present = [v for v in h_values if v]
    h_is_ranges = bool(h_present) and all(_is_range(v) for v in h_present)

    i_present = [k for k in ("I1", "I2", "I3", "I4", "I5") if params.get(k)]

    mtu = _read_mtu(config_text)
    listen_port = info.listen_port
    endpoint = f"{host}:{listen_port}" if listen_port else None

    return MaskingState(
        version=_detect_version(params),
        container=container,
        interface=_interface_from_path(config_path),
        config_path=config_path,
        listen_port=listen_port,
        endpoint=endpoint,
        mtu=mtu,
        keepalive=None,  # keepalive — настройка клиентского шаблона, не серверного конфига
        jc=params.get("Jc"),
        jmin=params.get("Jmin"),
        jmax=params.get("Jmax"),
        s1=params.get("S1"),
        s2=params.get("S2"),
        s3=params.get("S3"),
        s4=params.get("S4"),
        h1=params.get("H1"),
        h2=params.get("H2"),
        h3=params.get("H3"),
        h4=params.get("H4"),
        h_is_ranges=h_is_ranges,
        i_present=i_present,
    )


def _detect_version(params: dict[str, str]) -> str:
    has_s3 = "S3" in params
    has_s4 = "S4" in params
    has_i = any(k in params for k in ("I1", "I2", "I3", "I4", "I5"))
    has_any = any(k in params for k in ("Jc", "Jmin", "Jmax", "S1", "S2", "H1", "H2", "H3", "H4"))
    if has_s3 and has_s4:
        return MASK_VERSION_AWG2
    if has_i:
        return MASK_VERSION_AWG15
    if has_any:
        return MASK_VERSION_LEGACY
    return MASK_VERSION_UNKNOWN


def _collect_warnings(state: MaskingState) -> list[MaskingWarning]:
    warnings: list[MaskingWarning] = []

    overlap = _h_ranges_overlap(state)
    if overlap:
        warnings.append(
            MaskingWarning(
                level="danger",
                code="h_overlap",
                message="Диапазоны H пересекаются — конфиг невалиден, применять нельзя.",
            )
        )

    if state.version in (MASK_VERSION_LEGACY, MASK_VERSION_UNKNOWN, MASK_VERSION_AWG15):
        warnings.append(
            MaskingWarning(
                level="warning",
                code="no_s34",
                message="Нет S3/S4 — это не сильная AWG 2.0. Рекомендуется установка/миграция на AWG 2.0.",
            )
        )
    else:
        if _to_int(state.s3) == 0 or _to_int(state.s4) == 0:
            warnings.append(
                MaskingWarning(
                    level="warning",
                    code="s34_zero",
                    message="S3/S4 равны 0 — версия AWG 2.0, но маскировка базовая.",
                )
            )
        if not state.h_is_ranges and any([state.h1, state.h2, state.h3, state.h4]):
            warnings.append(
                MaskingWarning(
                    level="warning",
                    code="h_single",
                    message="H заданы одиночными значениями, а не диапазонами — работает, но без динамики.",
                )
            )

    jmin = _to_int(state.jmin)
    jmax = _to_int(state.jmax)
    if jmin is not None and jmax is not None and jmin > jmax:
        warnings.append(
            MaskingWarning(level="danger", code="j_order", message="Jmin больше Jmax — некорректные параметры J.")
        )
    if (jmin is not None and jmin < 64) or (jmax is not None and jmax > 1024):
        warnings.append(
            MaskingWarning(
                level="warning",
                code="j_range",
                message="Jmin/Jmax вне официального диапазона 64–1024 (extended, требует lab-подтверждения).",
            )
        )

    s123 = [_to_int(state.s1), _to_int(state.s2), _to_int(state.s3)]
    s4 = _to_int(state.s4)
    s_extended = any(v is not None and v > 64 for v in s123) or (s4 is not None and s4 > 32)
    if s_extended:
        warnings.append(
            MaskingWarning(
                level="warning",
                code="s_range",
                message="S1–S3 > 64 или S4 > 32 — extended-значения вне официального диапазона "
                "(работают на этом awg-go, но требуют lab-подтверждения; новый генератор их не выпускает).",
            )
        )

    if state.listen_port == DEFAULT_WG_PORT:
        warnings.append(
            MaskingWarning(level="warning", code="default_port", message="Используется дефолтный WireGuard-порт 51820.")
        )

    if _matches_static_fallback(state):
        warnings.append(
            MaskingWarning(
                level="danger",
                code="static_fallback",
                message="Параметры совпадают со статическим fallback-профилем — это узнаваемый отпечаток.",
            )
        )

    if state.i_present:
        warnings.append(
            MaskingWarning(
                level="info",
                code="cps_present",
                message=f"Заданы CPS-параметры ({', '.join(state.i_present)}) — продвинутый слой маскировки.",
            )
        )

    return warnings


def _score(state: MaskingState, warnings: list[MaskingWarning]) -> MaskingScore:
    codes = {w.code for w in warnings}

    if "h_overlap" in codes or "j_order" in codes:
        return MaskingScore(status=MASK_STATUS_INVALID, label=_LABELS[MASK_STATUS_INVALID])

    if state.version == MASK_VERSION_UNKNOWN:
        return MaskingScore(status=MASK_STATUS_UNKNOWN, label=_LABELS[MASK_STATUS_UNKNOWN])

    if state.version == MASK_VERSION_LEGACY:
        return MaskingScore(status=MASK_STATUS_LEGACY, label=_LABELS[MASK_STATUS_LEGACY])

    if state.version == MASK_VERSION_AWG15:
        return MaskingScore(status=MASK_STATUS_WEAK, label=_LABELS[MASK_STATUS_WEAK])

    # AWG 2.0
    s3 = _to_int(state.s3) or 0
    s4 = _to_int(state.s4) or 0
    strong = (
        s3 > 0
        and s4 > 0
        and state.h_is_ranges
        and all([state.h1, state.h2, state.h3, state.h4])
        and state.listen_port != DEFAULT_WG_PORT
        and "static_fallback" not in codes
    )
    if strong:
        return MaskingScore(status=MASK_STATUS_STRONG, label=_LABELS[MASK_STATUS_STRONG])
    return MaskingScore(status=MASK_STATUS_BASIC, label=_LABELS[MASK_STATUS_BASIC])


# --- helpers -----------------------------------------------------------------


def _is_range(value: Optional[str]) -> bool:
    if not value:
        return False
    return bool(re.fullmatch(r"\d+\s*-\s*\d+", value.strip()))


def _range_bounds(value: Optional[str]) -> Optional[tuple[int, int]]:
    if not value:
        return None
    text = value.strip()
    if _is_range(text):
        lo, hi = (int(p) for p in re.split(r"\s*-\s*", text))
        return (lo, hi) if lo <= hi else (hi, lo)
    num = _to_int(text)
    if num is None:
        return None
    return (num, num)


def _h_ranges_overlap(state: MaskingState) -> bool:
    bounds = [
        _range_bounds(state.h1),
        _range_bounds(state.h2),
        _range_bounds(state.h3),
        _range_bounds(state.h4),
    ]
    present = [b for b in bounds if b is not None]
    present.sort()
    for i in range(1, len(present)):
        if present[i][0] <= present[i - 1][1]:
            return True
    return False


def _matches_static_fallback(state: MaskingState) -> bool:
    return (
        state.h1 == STATIC_FALLBACK_H["H1"]
        and state.h2 == STATIC_FALLBACK_H["H2"]
        and state.h3 == STATIC_FALLBACK_H["H3"]
        and state.h4 == STATIC_FALLBACK_H["H4"]
    )


def _read_mtu(config_text: str) -> Optional[int]:
    match = re.search(r"(?im)^[ \t]*MTU[ \t]*=[ \t]*(\d+)", config_text)
    return int(match.group(1)) if match else None


def _interface_from_path(config_path: Optional[str]) -> Optional[str]:
    if not config_path:
        return None
    name = config_path.rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[0] or None


def _to_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return None


def _rotation_age_days(last_rotation_at: Optional[str]) -> Optional[int]:
    if not last_rotation_at:
        return None
    try:
        parsed = datetime.fromisoformat(last_rotation_at)
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - parsed
    return max(0, delta.days)
