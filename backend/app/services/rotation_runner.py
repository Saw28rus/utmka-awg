"""RES1 — раннер безопасной авто-ротации маскировки AWG2.

Поверх существующего `awg_masking_apply` (snapshot → apply → health → reissue →
авто-откат). Решает «пора ли ротировать» по расписанию или сигналу OBS3 и
вызывает безопасное применение. Уведомляет оператора о результате.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from app.services.awg_masking_apply import PRESETS, apply_rotation, generate_params
from app.services.dpi_store import dpi_store
from app.services.notification_store import notification_store
from app.services.rotation_policy_store import rotation_policy_store
from app.services.server_store import server_store

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _in_window(now_hour: int, start: int, end: int) -> bool:
    if start == end:
        return True  # окно не задано — всегда можно
    if start < end:
        return start <= now_hour < end
    # окно через полночь (например 22→4)
    return now_hour >= start or now_hour < end


def _schedule_due(policy: dict, now: datetime) -> bool:
    last = policy.get("last_rotated_at")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    interval = max(1, int(policy.get("interval_days") or 14))
    return (now - last_dt).total_seconds() >= interval * 86400


def _dpi_degraded(server_id: str) -> bool:
    return (dpi_store.get_state(server_id) or {}).get("level") == "degraded"


def _reason_for(server_id: str, policy: dict, now: datetime) -> Optional[str]:
    """Возвращает причину ротации ('dpi'|'schedule') или None."""
    if not policy.get("enabled"):
        return None
    # Аварийная ротация по DPI игнорирует окно обслуживания.
    if policy.get("trigger_on_dpi") and _dpi_degraded(server_id):
        return "dpi"
    if _schedule_due(policy, now):
        if _in_window(now.hour, int(policy.get("window_start") or 0), int(policy.get("window_end") or 0)):
            return "schedule"
    return None


def rotate_server(server_id: str, *, reason: str = "manual") -> dict:
    """Одна ротация сервера через безопасный apply. Блокирующая (SSH)."""
    policy = rotation_policy_store.get(server_id)
    preset = policy.get("preset") or "balance"
    if preset not in PRESETS:
        preset = "balance"
    record = server_store.get_record(server_id)
    name = (record or {}).get("name") or server_id

    params = generate_params(preset)
    result = apply_rotation(server_id, preset, params)
    now_iso = _now().isoformat()

    if result.ok:
        rotation_policy_store.mark_rotated(server_id, when=now_iso, status="ok")
        reason_text = {"dpi": "по сигналу DPI", "schedule": "по расписанию", "manual": "вручную"}.get(reason, reason)
        notification_store.add(
            level="warning",
            code="masking_rotated",
            title=f"Маскировка ротирована на «{name}»",
            message=(
                f"Параметры маскировки обновлены ({reason_text}, пресет «{preset}»). "
                "Клиентам нужно переимпортировать конфиги — старые QR больше не подойдут."
            ),
            server_id=server_id,
        )
    else:
        status = "rolled_back" if "откат" in (result.error or "").lower() else "failed"
        rotation_policy_store.mark_rotated(server_id, when=now_iso, status=status, error=result.error)
        notification_store.add(
            level="danger",
            code="masking_rotate_failed",
            title=f"Ротация маскировки не удалась на «{name}»",
            message=(result.error or "Применение не прошло, выполнен откат.") + " Конфиги клиентов не изменены.",
            server_id=server_id,
        )
    return {"server_id": server_id, "ok": result.ok, "reason": reason, "error": result.error}


def run_due_rotations() -> dict:
    """Проверяет все политики и ротирует те, что «пора». Для планировщика."""
    now = _now()
    checked = 0
    rotated = 0
    failed = 0
    for server_id, policy in rotation_policy_store.all().items():
        reason = _reason_for(server_id, policy, now)
        if not reason:
            continue
        if not server_store.get_record(server_id):
            continue
        checked += 1
        try:
            res = rotate_server(server_id, reason=reason)
            if res["ok"]:
                rotated += 1
            else:
                failed += 1
        except Exception:  # noqa: BLE001
            failed += 1
            logger.exception("rotation: failed for %s", server_id)
    return {"checked": checked, "rotated": rotated, "failed": failed}
