"""AWG Masking Center — фаза M2: генератор пресетов и безопасная ротация.

Принципы (UNIFIED_IMPLEMENTATION_PLAN §5.5/§5.7):
- per-server unique, crypto-random параметры; никаких глобальных дефолтов;
- production safe policy: Jc 0-10, Jmin/Jmax 1-1024, S1/S2 0-150, S3 0-64,
  S4 0-32, H1-H4 — непересекающиеся диапазоны до INT32_MAX (как AmneziaVPN);
- apply: read -> snapshot (шифрованный) -> validate -> dry-run -> write ->
  restart -> health check -> reissue клиентов; при сбое — автооткат;
- fail-closed: без валидного snapshot изменения не применяются.
"""

from __future__ import annotations

import base64
import re
import secrets
import shlex
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from app.core.crypto import decrypt, encrypt
from app.schemas.awg_masking import (
    MaskingApplyResponse,
    MaskingPreset,
    MaskingPreviewResponse,
    MaskingStep,
)
from app.services.amnezia_link import build_vpn_link
from app.services.awg_config import (
    build_client_config,
    parse_interface,
    parse_peers,
    resolve_endpoint_host,
)
from app.services.awg_masking import (
    STATIC_FALLBACK_H,
    _find_awg_container,
    _read_container_config,
    is_awg31_params,
    read_masking,
)
from app.services.cascade_store import cascade_store
from app.services.client_store import client_store
from app.services.persistence import read_json, write_json
from app.services.server_store import server_store
from app.ssh import exec as ssh_exec

SNAPSHOTS_FILE = "masking_snapshots.json"
MAX_SNAPSHOTS_PER_SERVER = 5

# Ключи, которые ротация меняет. I1-I5 — только если include_cps=True.
ROTATED_KEYS = ("Jc", "Jmin", "Jmax", "S1", "S2", "S3", "S4", "H1", "H2", "H3", "H4")
CPS_KEYS = ("I1", "I2", "I3", "I4", "I5")

# H: избегаем зарезервированных значений WG (1-4). Верх — INT32_MAX, как в
# приложении AmneziaVPN: часть Android-клиентов не принимает uint32 > 2^31-1.
H_MIN = 5
H_MAX = 2_147_483_647
H_RANGE_WIDTH_MIN = 65_536
H_RANGE_WIDTH_MAX = 1_048_575

# Как в amnezia-client installController.cpp (AWG 2.0).
AWG_MSG_INIT = 148
AWG_MSG_RESPONSE = 92
AWG_MSG_COOKIE = 64

PRESETS: dict[str, dict] = {
    "amnezia": {
        "label": "Как в Amnezia",
        "description": "Те же параметры, что ставит приложение AmneziaVPN. Лучше проходит мобильные сети.",
    },
    "mask": {
        "label": "Маскировка",
        "description": "Максимальная обфускация для жёстких сетей (РФ): высокий Jc, крупные S, широкие H-диапазоны.",
        "jc": (7, 10),
        "s123": (48, 64),
        "s4": (24, 32),
        "jmin": (64, 128),
        "jmax": (512, 1024),
    },
    "balance": {
        "label": "Баланс",
        "description": "Компромисс маскировки и скорости.",
        "jc": (3, 6),
        "s123": (24, 48),
        "s4": (12, 24),
        "jmin": (64, 96),
        "jmax": (256, 512),
    },
    "speed": {
        "label": "Скорость",
        "description": "Минимальный overhead, H-диапазоны сохраняются. Для сетей без активного DPI.",
        "jc": (1, 3),
        "s123": (8, 24),
        "s4": (4, 12),
        "jmin": (64, 80),
        "jmax": (128, 256),
    },
}


class MaskingApplyError(Exception):
    pass


def list_presets() -> list[MaskingPreset]:
    return [
        MaskingPreset(id=pid, label=p["label"], description=p["description"])
        for pid, p in PRESETS.items()
    ]


# --- генерация ----------------------------------------------------------------


def _rand_between(lo: int, hi: int) -> int:
    return lo + secrets.randbelow(hi - lo + 1)


def _generate_h_ranges() -> list[str]:
    """4 непересекающихся случайных диапазона, не совпадающих со старым fallback."""
    for _ in range(50):
        starts = sorted(
            secrets.randbelow(H_MAX - H_MIN - H_RANGE_WIDTH_MAX) + H_MIN for _ in range(4)
        )
        ranges: list[tuple[int, int]] = []
        ok = True
        for idx, start in enumerate(starts):
            width = _rand_between(H_RANGE_WIDTH_MIN, H_RANGE_WIDTH_MAX)
            end = start + width
            if ranges and start <= ranges[-1][1]:
                ok = False
                break
            if idx < 3 and end >= starts[idx + 1]:
                end = starts[idx + 1] - 1
                if end - start < H_RANGE_WIDTH_MIN:
                    ok = False
                    break
            if end > H_MAX:
                end = H_MAX
            ranges.append((start, end))
        if not ok or len(ranges) != 4:
            continue
        rendered = [f"{lo}-{hi}" for lo, hi in ranges]
        if set(rendered) & set(STATIC_FALLBACK_H.values()):
            continue
        return rendered
    raise MaskingApplyError("Не удалось сгенерировать валидные H-диапазоны.")


def generate_amnezia_app_params() -> dict[str, str]:
    """Ровно то, что генерит приложение AmneziaVPN при установке AWG 2.0.

    Источник: amnezia-client installController.cpp (DockerContainer::Awg2):
    Jc 4–6, Jmin=10, Jmax=50, S1/S2 15–149, S3 1–63, S4 1–19,
    H1–H4 — последовательные непересекающиеся диапазоны до INT32_MAX, без I1–I5.
    """
    jc = 4 + secrets.randbelow(3)

    def _rand(lo: int, hi_exclusive: int) -> int:
        return lo + secrets.randbelow(hi_exclusive - lo)

    s1 = _rand(15, 150)
    used = {s1}
    s2 = _rand(15, 150)
    while s2 in used or s1 + AWG_MSG_INIT == s2 + AWG_MSG_RESPONSE:
        s2 = _rand(15, 150)
    used.add(s2)
    s3 = _rand(1, 64)
    while (
        s3 in used
        or s1 + AWG_MSG_INIT == s3 + AWG_MSG_COOKIE
        or s2 + AWG_MSG_RESPONSE == s3 + AWG_MSG_COOKIE
    ):
        s3 = _rand(1, 64)
    used.add(s3)
    s4 = _rand(1, 20)
    while s4 in used:
        s4 = _rand(1, 20)

    headers: list[str] = []
    for _ in range(50):
        min_v = H_MIN
        headers = []
        ok = True
        for _h in range(4):
            if min_v >= H_MAX - 2:
                ok = False
                break
            first = _rand(min_v, H_MAX)
            second = _rand(first, H_MAX) if first < H_MAX - 1 else first + 1
            if second <= first:
                second = min(first + 1, H_MAX)
            headers.append(f"{first}-{second}")
            min_v = second + 1
        if ok and len(headers) == 4:
            break
    else:
        raise MaskingApplyError("Не удалось сгенерировать H-диапазоны Amnezia.")

    return {
        "Jc": str(jc),
        "Jmin": "10",
        "Jmax": "50",
        "S1": str(s1),
        "S2": str(s2),
        "S3": str(s3),
        "S4": str(s4),
        "H1": headers[0],
        "H2": headers[1],
        "H3": headers[2],
        "H4": headers[3],
    }


def generate_amnezia_31_params() -> dict[str, str]:
    """Параметры AWG 3.1 как у self-hosted AmneziaVPN 5.0.1.5.

    Header Protection: H1–H4 = 1–4 (кастомные заголовки выключены), S1–S4 ≥ 12
    (nonce ChaCha). Junk как в приложении. HeaderProtectionKey генерится
    внутри контейнера (`awg genkey`), не здесь. RandomTrailers выключен:
    иначе клиент без флага не соберёт handshake.
    """
    jc = 4 + secrets.randbelow(3)

    def _rand(lo: int, hi_exclusive: int) -> int:
        return lo + secrets.randbelow(hi_exclusive - lo)

    s1 = _rand(15, 150)
    used = {s1}
    s2 = _rand(15, 150)
    while s2 in used or s1 + AWG_MSG_INIT == s2 + AWG_MSG_RESPONSE:
        s2 = _rand(15, 150)
    used.add(s2)
    s3 = _rand(12, 64)
    while (
        s3 in used
        or s1 + AWG_MSG_INIT == s3 + AWG_MSG_COOKIE
        or s2 + AWG_MSG_RESPONSE == s3 + AWG_MSG_COOKIE
    ):
        s3 = _rand(12, 64)
    used.add(s3)
    s4 = _rand(12, 33)
    while s4 in used:
        s4 = _rand(12, 33)

    return {
        "Jc": str(jc),
        "Jmin": "10",
        "Jmax": "50",
        "S1": str(s1),
        "S2": str(s2),
        "S3": str(s3),
        "S4": str(s4),
        "H1": "1",
        "H2": "2",
        "H3": "3",
        "H4": "4",
        "ContentPaddingAddition": "10-100",
        # RandomTrailers двусторонний: телефон часто не включает флаг,
        # а сервер с on рвёт handshake и на Wi‑Fi, и на LTE.
        "RandomTrailers": "off",
        "DisableCookies": "on",
    }


def generate_cps_params() -> dict[str, str]:
    """Опциональный CPS-слой (I1–I5): уникальный hex-префикс перед handshake.

    Формат AmneziaWG: теги <b HEX> (байты) и <r lo-hi> (случайная длина).
    Не входит в базовый strong-score; включается явно. При сбое apply — откат.
    """

    def blob(n: int) -> str:
        return secrets.token_hex(n)

    return {
        "I1": f"<b 0x{blob(8)}><r 32-96><c>",
        "I2": f"<b 0x{blob(8)}>",
        "I3": f"<b 0x{blob(4)}>",
        "I4": f"<b 0x{blob(4)}>",
        "I5": f"<b 0x{blob(8)}>",
    }


def generate_params(preset_id: str, *, include_cps: bool = False) -> dict[str, str]:
    if preset_id == "amnezia":
        out = generate_amnezia_app_params()
        if include_cps:
            out.update(generate_cps_params())
        return out

    preset = PRESETS.get(preset_id)
    if not preset or "jc" not in preset:
        raise MaskingApplyError("Неизвестный пресет маскировки.")

    jmin = _rand_between(*preset["jmin"])
    jmax = _rand_between(*preset["jmax"])
    if jmin >= jmax:
        jmax = min(1024, jmin + 64)

    s1 = _rand_between(*preset["s123"])
    s2 = _rand_between(*preset["s123"])
    # ограничение awg: init(148+S1) не должен равняться response(92+S2)
    while s1 + 56 == s2:
        s2 = _rand_between(*preset["s123"])
    s3 = _rand_between(*preset["s123"])
    s4 = _rand_between(*preset["s4"])
    if s4 == 0:
        s4 = 1
    if s3 == 0:
        s3 = 1

    h1, h2, h3, h4 = _generate_h_ranges()
    out = {
        "Jc": str(_rand_between(*preset["jc"])),
        "Jmin": str(jmin),
        "Jmax": str(jmax),
        "S1": str(s1),
        "S2": str(s2),
        "S3": str(s3),
        "S4": str(s4),
        "H1": h1,
        "H2": h2,
        "H3": h3,
        "H4": h4,
    }
    if include_cps:
        out.update(generate_cps_params())
    return out


def rotation_include_cps(server_id: str) -> bool:
    """CPS на авто-ротации только если I1–I5 уже стоят на сервере."""
    try:
        resp = read_masking(server_id)
        return bool(resp.state and resp.state.i_present)
    except Exception:  # noqa: BLE001
        return False


def validate_params(params: dict[str, str]) -> list[str]:
    """Safe policy: вне диапазонов — блок (генератор extended не выпускает)."""
    errors: list[str] = []
    for key in ROTATED_KEYS:
        if not str(params.get(key) or "").strip():
            errors.append(f"Не задан параметр {key}.")
    if errors:
        return errors

    def _int(key: str) -> Optional[int]:
        try:
            return int(str(params[key]).strip())
        except (ValueError, TypeError):
            errors.append(f"{key} — не число.")
            return None

    jc, jmin, jmax = _int("Jc"), _int("Jmin"), _int("Jmax")
    s_vals = {k: _int(k) for k in ("S1", "S2", "S3", "S4")}
    if errors:
        return errors

    if jc is not None and not 0 <= jc <= 10:
        errors.append("Jc вне диапазона 0–10.")
    if jmin is not None and jmax is not None:
        if not 1 <= jmin <= 1024 or not 1 <= jmax <= 1024:
            errors.append("Jmin/Jmax вне диапазона 1–1024.")
        if jmin > jmax:
            errors.append("Jmin больше Jmax.")
    for key in ("S1", "S2"):
        val = s_vals[key]
        if val is not None and not 0 <= val <= 150:
            errors.append(f"{key} вне диапазона 0–150.")
    if s_vals["S3"] is not None and not 0 <= s_vals["S3"] <= 64:
        errors.append("S3 вне диапазона 0–64.")
    if s_vals["S4"] is not None and not 0 <= s_vals["S4"] <= 32:
        errors.append("S4 вне диапазона 0–32.")
    if (
        s_vals["S1"] is not None
        and s_vals["S2"] is not None
        and s_vals["S1"] + 56 == s_vals["S2"]
    ):
        errors.append("S1+56 == S2 — конфликт размеров init/response пакетов.")

    bounds: list[tuple[int, int]] = []
    for key in ("H1", "H2", "H3", "H4"):
        raw = str(params[key]).strip()
        match = re.fullmatch(r"(\d+)\s*-\s*(\d+)", raw)
        if not match:
            errors.append(f"{key} должен быть диапазоном вида lo-hi.")
            continue
        lo, hi = int(match.group(1)), int(match.group(2))
        if lo >= hi:
            errors.append(f"{key}: начало диапазона не меньше конца.")
            continue
        if lo < 5 or hi > H_MAX:
            errors.append(f"{key} вне допустимых значений (5..{H_MAX}).")
            continue
        bounds.append((lo, hi))
    bounds.sort()
    for i in range(1, len(bounds)):
        if bounds[i][0] <= bounds[i - 1][1]:
            errors.append("H-диапазоны пересекаются.")
            break
    if all(str(params.get(k) or "").strip() == v for k, v in STATIC_FALLBACK_H.items()):
        errors.append("H совпадают с известным статическим fallback-профилем.")
    for key in CPS_KEYS:
        raw = str(params.get(key) or "").strip()
        if not raw:
            continue
        if "<b" not in raw and "<r" not in raw:
            errors.append(f"{key} — не похоже на CPS-тег AmneziaWG.")
    return errors


# --- snapshots ----------------------------------------------------------------


def _load_snapshots() -> dict[str, list[dict]]:
    return read_json(SNAPSHOTS_FILE, {})


def _save_snapshots(data: dict[str, list[dict]]) -> None:
    write_json(SNAPSHOTS_FILE, data)


def list_snapshots(server_id: str) -> list[dict]:
    snaps = _load_snapshots().get(server_id, [])
    return [
        {
            "id": s["id"],
            "created_at": s["created_at"],
            "label": s.get("label") or "snapshot",
            "preset": s.get("preset"),
        }
        for s in reversed(snaps)
    ]


def _store_snapshot(
    server_id: str,
    *,
    conf_text: str,
    container: str,
    config_path: str,
    preset: Optional[str],
) -> str:
    enc = encrypt(conf_text)
    if not enc:
        raise MaskingApplyError(
            "Snapshot не зашифровался (panel_secret_key недоступен) — применение заблокировано."
        )
    data = _load_snapshots()
    snaps = data.get(server_id, [])
    snap_id = str(uuid4())
    snaps.append(
        {
            "id": snap_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "label": f"До ротации ({PRESETS.get(preset or '', {}).get('label', preset or 'manual')})",
            "preset": preset,
            "container": container,
            "config_path": config_path,
            "conf_enc": enc,
        }
    )
    data[server_id] = snaps[-MAX_SNAPSHOTS_PER_SERVER:]
    _save_snapshots(data)
    return snap_id


def _get_snapshot(server_id: str, snapshot_id: Optional[str]) -> Optional[dict]:
    snaps = _load_snapshots().get(server_id, [])
    if not snaps:
        return None
    if snapshot_id is None:
        return snaps[-1]
    for snap in snaps:
        if snap["id"] == snapshot_id:
            return snap
    return None


# --- работа с конфигом на сервере ----------------------------------------------


def _connect(server_id: str):
    target = server_store.ssh_target(server_id)
    if not target:
        raise MaskingApplyError("Сервер не найден.")
    try:
        return ssh_exec.connect(
            host=target.host,
            port=target.port,
            username=target.username,
            password=target.password,
            key=target.key,
            timeout=15,
        ), target
    except Exception as exc:  # noqa: BLE001
        raise MaskingApplyError(f"SSH не отвечает: {exc}") from exc


def _run_in_container(ssh, container: str, inner: str, timeout: int = 30):
    cmd = f"sudo docker exec {shlex.quote(container)} sh -c {shlex.quote(inner)}"
    return ssh_exec.run(ssh, cmd, timeout=timeout)


def _overwrite_container_file(ssh, container: str, path: str, content: str) -> None:
    b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
    inner = f"printf '%s' {shlex.quote(b64)} | base64 -d > {shlex.quote(path)}"
    result = _run_in_container(ssh, container, inner)
    if result.exit_code != 0:
        raise MaskingApplyError(f"Не удалось записать конфиг: {result.stderr.strip()}")


def _restart_interface(ssh, container: str, config_path: str, iface: str) -> tuple[bool, str]:
    cfg = shlex.quote(config_path)
    inner = (
        f"(awg-quick down {cfg} 2>/dev/null || wg-quick down {cfg} 2>/dev/null || true); "
        f"awg-quick up {cfg} 2>&1 || wg-quick up {cfg} 2>&1"
    )
    res = _run_in_container(ssh, container, inner, timeout=60)
    check = _run_in_container(
        ssh, container, f"awg show {shlex.quote(iface)} 2>/dev/null || wg show {shlex.quote(iface)} 2>/dev/null"
    )
    if check.exit_code == 0 and check.stdout.strip():
        return True, ""
    detail = (res.stdout or res.stderr or "").strip()[-400:]
    return False, detail or "интерфейс не поднялся"


def _apply_keys(params: dict[str, str]) -> tuple[str, ...]:
    extra = tuple(k for k in CPS_KEYS if k in params and str(params.get(k) or "").strip())
    return ROTATED_KEYS + extra


def _render_new_config(config_text: str, params: dict[str, str]) -> str:
    """Заменяем только параметры маскировки, не трогая ключи/peers/прочее."""
    new_text = config_text
    missing: list[str] = []
    for key in _apply_keys(params):
        pattern = re.compile(rf"(?im)^([ \t]*){re.escape(key)}[ \t]*=.*$")
        if pattern.search(new_text):
            new_text = pattern.sub(rf"\g<1>{key} = {params[key]}", new_text, count=1)
        else:
            missing.append(key)
    if missing:
        lines = [f"{key} = {params[key]}" for key in missing]
        anchor = re.compile(r"(?im)^([ \t]*ListenPort[ \t]*=.*)$")
        if anchor.search(new_text):
            new_text = anchor.sub("\\1\n" + "\n".join(lines), new_text, count=1)
        else:
            new_text = new_text.replace("[Interface]", "[Interface]\n" + "\n".join(lines), 1)
    return new_text


def _iface_from_path(config_path: str) -> str:
    name = config_path.rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[0]


def _server_public_key(ssh, container: str, iface: str, private_key: Optional[str]) -> str:
    out = _run_in_container(
        ssh,
        container,
        f"awg show {shlex.quote(iface)} public-key 2>/dev/null || wg show {shlex.quote(iface)} public-key 2>/dev/null || true",
    ).stdout.strip()
    if out:
        return out.splitlines()[0].strip()
    if private_key:
        out = _run_in_container(
            ssh,
            container,
            f"printf '%s' {shlex.quote(private_key)} | awg pubkey 2>/dev/null || "
            f"printf '%s' {shlex.quote(private_key)} | wg pubkey 2>/dev/null || true",
        ).stdout.strip()
        if out:
            return out.splitlines()[0].strip()
    return ""


# --- preview -------------------------------------------------------------------


def _client_reissue_stats(server_id: str) -> tuple[int, int, int]:
    total = reissuable = skipped = 0
    for item in client_store.list_all(server_id):
        if item.protocol not in ("awg2", "awg", "awg_legacy"):
            continue
        total += 1
        detail = client_store.get_detail(item.id)
        if detail and detail.has_private_key:
            reissuable += 1
        else:
            skipped += 1
    return total, reissuable, skipped


def preview_rotation(server_id: str, preset_id: str, include_cps: bool = False) -> MaskingPreviewResponse:
    """Блокирующая (SSH) — вызывать через asyncio.to_thread."""
    if preset_id not in PRESETS:
        return MaskingPreviewResponse(ok=False, preset=preset_id, error="Неизвестный пресет.")
    try:
        ssh, _target = _connect(server_id)
    except MaskingApplyError as exc:
        return MaskingPreviewResponse(ok=False, preset=preset_id, error=str(exc))

    try:
        container = _find_awg_container(ssh)
        if not container:
            return MaskingPreviewResponse(
                ok=False, preset=preset_id, error="Контейнер AmneziaWG 2.0 не найден."
            )
        config_path, config_text = _read_container_config(ssh, container)
        if not config_path or not config_text.strip():
            return MaskingPreviewResponse(
                ok=False, preset=preset_id, error="Конфиг AmneziaWG не найден в контейнере."
            )
        info = parse_interface(config_text)
        if is_awg31_params(info.awg_params):
            return MaskingPreviewResponse(
                ok=False,
                preset=preset_id,
                error="Это AmneziaWG 3.1. Маскировку 2.0 сюда применять нельзя.",
            )
        if not (info.awg_params.get("S3") and info.awg_params.get("S4")):
            return MaskingPreviewResponse(
                ok=False,
                preset=preset_id,
                error="Сервер не AWG 2.0 — ротация недоступна. Сначала установите AmneziaWG 2.0.",
            )

        params = generate_params(preset_id, include_cps=include_cps)
        errors = validate_params(params)
        current = {k: v for k, v in info.awg_params.items() if k in ROTATED_KEYS or k in CPS_KEYS}
        total, reissuable, skipped = _client_reissue_stats(server_id)
        return MaskingPreviewResponse(
            ok=not errors,
            preset=preset_id,
            params=params,
            current=current,
            errors=errors,
            clients_total=total,
            clients_reissuable=reissuable,
            clients_skipped=skipped,
            cascade_entry=cascade_store.get_link(server_id) is not None,
        )
    finally:
        ssh.close()


# --- apply / rollback ----------------------------------------------------------


def apply_rotation(
    server_id: str,
    preset_id: str,
    params: dict[str, str],
    *,
    notify_chat: bool = True,
) -> MaskingApplyResponse:
    """Блокирующая (SSH) — вызывать через asyncio.to_thread."""
    steps: list[MaskingStep] = []

    errors = validate_params(params)
    if errors:
        return MaskingApplyResponse(
            ok=False,
            steps=[MaskingStep(name="Валидация параметров", status="failed", detail="; ".join(errors))],
            error="Параметры не прошли валидацию: " + "; ".join(errors),
        )
    steps.append(MaskingStep(name="Валидация параметров", status="ok"))

    try:
        ssh, target = _connect(server_id)
    except MaskingApplyError as exc:
        return MaskingApplyResponse(ok=False, steps=steps, error=str(exc))

    try:
        container = _find_awg_container(ssh)
        if not container:
            return MaskingApplyResponse(ok=False, steps=steps, error="Контейнер AmneziaWG 2.0 не найден.")
        config_path, old_text = _read_container_config(ssh, container)
        if not config_path or not old_text.strip():
            return MaskingApplyResponse(ok=False, steps=steps, error="Конфиг AmneziaWG не найден.")
        iface = _iface_from_path(config_path)

        old_info = parse_interface(old_text)
        if not old_info.private_key:
            return MaskingApplyResponse(
                ok=False, steps=steps, error="В конфиге нет PrivateKey — структура неожиданная, не рискуем."
            )
        if not (old_info.awg_params.get("S3") and old_info.awg_params.get("S4")):
            return MaskingApplyResponse(
                ok=False, steps=steps, error="Сервер не AWG 2.0 (нет S3/S4) — ротация недоступна."
            )
        if is_awg31_params(old_info.awg_params):
            return MaskingApplyResponse(
                ok=False,
                steps=steps,
                error="Это AmneziaWG 3.1. Маскировку 2.0 сюда применять нельзя — сломается Header Protection.",
            )
        steps.append(MaskingStep(name="Чтение текущего конфига", status="ok", detail=config_path))

        # snapshot ДО изменений (fail-closed при недоступном шифровании)
        snapshot_id = _store_snapshot(
            server_id, conf_text=old_text, container=container, config_path=config_path, preset=preset_id
        )
        steps.append(MaskingStep(name="Snapshot конфига (зашифрован)", status="ok"))

        # dry-run: рендерим и перепроверяем структуру
        new_text = _render_new_config(old_text, params)
        new_info = parse_interface(new_text)
        old_peers = len(parse_peers(old_text))
        new_peers = len(parse_peers(new_text))
        dry_problems: list[str] = []
        if new_info.private_key != old_info.private_key:
            dry_problems.append("PrivateKey изменился")
        if new_info.listen_port != old_info.listen_port:
            dry_problems.append("ListenPort изменился")
        if new_peers != old_peers:
            dry_problems.append(f"число peer'ов изменилось ({old_peers} -> {new_peers})")
        for key in _apply_keys(params):
            if (new_info.awg_params.get(key) or "") != params[key]:
                dry_problems.append(f"{key} не применился в рендере")
        if dry_problems:
            steps.append(MaskingStep(name="Dry-run рендера", status="failed", detail="; ".join(dry_problems)))
            return MaskingApplyResponse(
                ok=False, steps=steps, snapshot_id=snapshot_id,
                error="Dry-run не прошёл, на сервер ничего не записано: " + "; ".join(dry_problems),
            )
        steps.append(MaskingStep(name="Dry-run рендера", status="ok"))

        # запись + рестарт
        _overwrite_container_file(ssh, container, config_path, new_text)
        steps.append(MaskingStep(name="Запись нового конфига", status="ok"))

        up_ok, up_detail = _restart_interface(ssh, container, config_path, iface)
        if not up_ok:
            steps.append(MaskingStep(name="Перезапуск интерфейса", status="failed", detail=up_detail))
            rolled = _restore_config(ssh, container, config_path, iface, old_text)
            steps.append(
                MaskingStep(
                    name="Автооткат на snapshot",
                    status="ok" if rolled else "failed",
                    detail=None if rolled else "интерфейс не поднялся и после отката — проверьте сервер вручную",
                )
            )
            return MaskingApplyResponse(
                ok=False, steps=steps, snapshot_id=snapshot_id, rolled_back=rolled,
                error=f"Интерфейс не поднялся с новыми параметрами: {up_detail}",
            )
        steps.append(MaskingStep(name="Перезапуск интерфейса", status="ok"))

        # health: порт слушается
        port = old_info.listen_port
        if port:
            listen = _run_in_container(
                ssh, container, f"ss -lun 2>/dev/null | grep -c ':{port} ' || true"
            ).stdout.strip()
            steps.append(
                MaskingStep(
                    name="Проверка UDP-порта",
                    status="ok" if listen and listen != "0" else "info",
                    detail=None if listen and listen != "0" else "не удалось подтвердить листенер (ss недоступен?)",
                )
            )

        # reissue клиентов
        reissued, skipped, reissued_ids = _reissue_clients(
            ssh,
            container=container,
            iface=iface,
            server_id=server_id,
            host=target.host,
            dns=old_info.dns,
            listen_port=old_info.listen_port or 51820,
            server_private_key=old_info.private_key,
            awg_params={**old_info.awg_params, **params},
        )
        steps.append(
            MaskingStep(
                name="Перевыпуск клиентских конфигов",
                status="ok",
                detail=f"обновлено {reissued}, пропущено {skipped} (импортированные без ключей)",
            )
        )

        chat_delivered = 0
        if notify_chat and reissued_ids:
            from app.services.chat_key_delivery import deliver_keys_for_clients

            chat_delivered = deliver_keys_for_clients(
                reissued_ids, reason="masking_rotation"
            )
            steps.append(
                MaskingStep(
                    name="Ключи в чат",
                    status="ok" if chat_delivered else "info",
                    detail=(
                        f"отправлено в {chat_delivered} диалог(а)"
                        if chat_delivered
                        else "привязанных чат-аккаунтов нет — раздайте конфиги вручную"
                    ),
                )
            )

        if cascade_store.get_link(server_id):
            steps.append(
                MaskingStep(
                    name="Каскад",
                    status="info",
                    detail="Транзит каскада продолжает работать на прежних параметрах. "
                    "Рекомендуется переприменить каскад, чтобы транзит совпадал с новой маскировкой.",
                )
            )

        server_store.update_runtime(
            server_id, last_masking_rotation_at=datetime.now(timezone.utc).isoformat()
        )
        fresh = read_masking(server_id)
        return MaskingApplyResponse(
            ok=True,
            steps=steps,
            snapshot_id=snapshot_id,
            reissued=reissued,
            reissue_skipped=skipped,
            chat_delivered=chat_delivered,
            masking=fresh,
        )
    except MaskingApplyError as exc:
        return MaskingApplyResponse(ok=False, steps=steps, error=str(exc))
    finally:
        ssh.close()


def rollback_rotation(server_id: str, snapshot_id: Optional[str] = None) -> MaskingApplyResponse:
    """Восстановление конфига из snapshot. Блокирующая (SSH)."""
    steps: list[MaskingStep] = []
    snap = _get_snapshot(server_id, snapshot_id)
    if not snap:
        return MaskingApplyResponse(ok=False, error="Snapshot не найден.")
    conf_text = decrypt(snap.get("conf_enc"))
    if not conf_text:
        return MaskingApplyResponse(
            ok=False, error="Snapshot не расшифровался (panel_secret_key изменился?) — откат невозможен."
        )
    steps.append(MaskingStep(name="Чтение snapshot", status="ok", detail=snap.get("created_at")))

    try:
        ssh, target = _connect(server_id)
    except MaskingApplyError as exc:
        return MaskingApplyResponse(ok=False, steps=steps, error=str(exc))

    try:
        container = snap.get("container") or _find_awg_container(ssh)
        config_path = snap.get("config_path")
        if not container or not config_path:
            return MaskingApplyResponse(ok=False, steps=steps, error="Не найден контейнер/путь конфига.")
        iface = _iface_from_path(config_path)

        rolled = _restore_config(ssh, container, config_path, iface, conf_text)
        steps.append(
            MaskingStep(
                name="Восстановление конфига", status="ok" if rolled else "failed",
                detail=None if rolled else "интерфейс не поднялся после восстановления",
            )
        )
        if not rolled:
            return MaskingApplyResponse(
                ok=False, steps=steps, error="Конфиг записан, но интерфейс не поднялся — проверьте сервер."
            )

        info = parse_interface(conf_text)
        reissued, skipped, reissued_ids = _reissue_clients(
            ssh,
            container=container,
            iface=iface,
            server_id=server_id,
            host=target.host,
            dns=info.dns,
            listen_port=info.listen_port or 51820,
            server_private_key=info.private_key,
            awg_params=dict(info.awg_params),
        )
        steps.append(
            MaskingStep(
                name="Перевыпуск клиентских конфигов",
                status="ok",
                detail=f"обновлено {reissued}, пропущено {skipped}",
            )
        )
        chat_delivered = 0
        if reissued_ids:
            from app.services.chat_key_delivery import deliver_keys_for_clients

            chat_delivered = deliver_keys_for_clients(
                reissued_ids, reason="masking_rotation"
            )
        fresh = read_masking(server_id)
        return MaskingApplyResponse(
            ok=True, steps=steps, snapshot_id=snap["id"], rolled_back=True,
            reissued=reissued, reissue_skipped=skipped, chat_delivered=chat_delivered, masking=fresh,
        )
    except MaskingApplyError as exc:
        return MaskingApplyResponse(ok=False, steps=steps, error=str(exc))
    finally:
        ssh.close()


def _restore_config(ssh, container: str, config_path: str, iface: str, old_text: str) -> bool:
    try:
        _overwrite_container_file(ssh, container, config_path, old_text)
    except MaskingApplyError:
        return False
    ok, _detail = _restart_interface(ssh, container, config_path, iface)
    return ok


# --- reissue -------------------------------------------------------------------


def _reissue_clients(
    ssh,
    *,
    container: str,
    iface: str,
    server_id: str,
    host: str,
    dns: Optional[str],
    listen_port: int,
    server_private_key: Optional[str],
    awg_params: dict[str, str],
) -> tuple[int, int, list[str]]:
    """Пере-рендер конфигов/ссылок клиентов с новыми параметрами (ключи не меняются)."""
    record = server_store.get_record(server_id) or {}
    server_name = record.get("name") or "Server"
    # Endpoint в конфиге должен уважать заданный домен (панели/явный), а не голый
    # IP — иначе ротация маскировки затирала бы домен обратно на IP.
    endpoint_host = resolve_endpoint_host(record, host)
    server_public_key = _server_public_key(ssh, container, iface, server_private_key)

    reissued = skipped = 0
    reissued_ids: list[str] = []
    for item in client_store.list_all(server_id):
        if item.protocol not in ("awg2", "awg", "awg_legacy"):
            continue
        secrets_bundle = client_store.get_secrets(item.id)
        if not secrets_bundle or not secrets_bundle.get("private_key") or not server_public_key:
            skipped += 1
            continue
        private_key = secrets_bundle["private_key"]
        preshared_key = secrets_bundle.get("preshared_key")
        public_key = item.public_key or ""

        config_text = build_client_config(
            client_private_key=private_key,
            client_ip=item.client_ip,
            dns=dns,
            server_public_key=server_public_key,
            preshared_key=preshared_key,
            endpoint_host=endpoint_host,
            endpoint_port=listen_port,
            awg_params=awg_params,
        )
        vpn_link = build_vpn_link(
            host=endpoint_host,
            port=listen_port,
            dns=dns,
            client_ip=item.client_ip,
            client_private_key=private_key,
            client_public_key=public_key,
            server_public_key=server_public_key,
            preshared_key=preshared_key,
            awg_params=awg_params,
            wg_config_ini=config_text,
            description=server_name,
        )
        client_store.update_issued_config(
            item.id,
            config_text=config_text,
            vpn_link=vpn_link,
            endpoint=f"{endpoint_host}:{listen_port}",
        )
        reissued += 1
        reissued_ids.append(item.id)
    return reissued, skipped, reissued_ids
