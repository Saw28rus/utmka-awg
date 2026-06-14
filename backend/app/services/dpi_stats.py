"""OBS3 — сбор DPI-трендов и детекция возможной деградации.

Сигнал: handshake success rate = доля активных клиентов со свежим handshake.
Резкое падение rate при достаточном числе клиентов → «возможна деградация/блок»
(вход для RES1/RES2). Не «детектор ТСПУ», а тренды. Read-only по данным панели,
никаких изменений на серверах.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.services.client_store import client_store
from app.services.dpi_store import dpi_store
from app.services.notification_store import notification_store
from app.services.server_store import server_store

logger = logging.getLogger(__name__)

# Порог детекции: чтобы не ловить шум на 1-2 клиентах.
MIN_ACTIVE = 4
# База (норма) должна быть достаточно высокой, чтобы падение было значимым.
BASELINE_MIN = 0.6
# Текущее значение ниже половины базы → деградация.
DROP_RATIO = 0.5
# Сколько последних точек усредняем для «текущего» rate.
RECENT_WINDOW = 3


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _server_sample(server_id: str) -> dict:
    clients = client_store.list_all(server_id=server_id)
    active = [c for c in clients if c.status == "active"]
    online = sum(1 for c in active if c.online)
    active_n = len(active)
    rate = round(online / active_n, 4) if active_n else None
    return {"ts": _now_iso(), "online": online, "active": active_n, "rate": rate}


def _recent_rate(series: list[dict]) -> float | None:
    rates = [s["rate"] for s in series[-RECENT_WINDOW:] if s.get("rate") is not None]
    if not rates:
        return None
    return sum(rates) / len(rates)


def _baseline_rate(series: list[dict]) -> float | None:
    """Норма = лучший устойчивый rate за историю (исключая последнее окно)."""
    older = [s["rate"] for s in series[:-RECENT_WINDOW] if s.get("rate") is not None]
    if not older:
        return None
    return max(older)


def _evaluate(server_id: str, sample: dict, series: list[dict]) -> str:
    """Возвращает уровень: 'ok' | 'degraded'. Шлёт уведомление при смене."""
    level = "ok"
    if (sample.get("active") or 0) >= MIN_ACTIVE:
        baseline = _baseline_rate(series)
        recent = _recent_rate(series)
        if baseline is not None and recent is not None:
            if baseline >= BASELINE_MIN and recent <= baseline * DROP_RATIO:
                level = "degraded"

    prev = (dpi_store.get_state(server_id) or {}).get("level") or "ok"
    if level != prev:
        dpi_store.set_state(server_id, level, _now_iso())
        record = server_store.get_record(server_id)
        name = (record or {}).get("name") or server_id
        if level == "degraded":
            notification_store.add(
                level="warning",
                code="dpi_degraded",
                title=f"Возможна деградация на «{name}»",
                message=(
                    "Резко упала доля клиентов на связи (handshake success). "
                    "Возможна блокировка/ухудшение — проверьте маскировку."
                ),
                server_id=server_id,
            )
        else:
            notification_store.add(
                level="info",
                code="dpi_recovered",
                title=f"Связь восстановилась на «{name}»",
                message="Доля клиентов на связи вернулась к норме.",
                server_id=server_id,
            )
    return level


def sample_all() -> dict:
    checked = 0
    degraded = 0
    for record in server_store.list_records():
        sid = record.get("id")
        if not sid:
            continue
        try:
            sample = _server_sample(sid)
            history = dpi_store.series(sid)
            dpi_store.append(sid, sample)
            level = _evaluate(sid, sample, history + [sample])
            checked += 1
            if level == "degraded":
                degraded += 1
        except Exception:  # noqa: BLE001
            logger.exception("dpi: sample failed for %s", sid)
    return {"checked": checked, "degraded": degraded}


def get_server_dpi(server_id: str) -> dict:
    series = dpi_store.series(server_id)
    state = dpi_store.get_state(server_id) or {"level": "ok"}
    return {
        "server_id": server_id,
        "level": state.get("level") or "ok",
        "since": state.get("since"),
        "recent_rate": _recent_rate(series),
        "baseline_rate": _baseline_rate(series),
        "samples": series,
    }


def get_dpi_overview() -> list[dict]:
    out: list[dict] = []
    for record in server_store.list_records():
        sid = record["id"]
        series = dpi_store.series(sid)
        state = dpi_store.get_state(sid) or {"level": "ok"}
        last = series[-1] if series else None
        out.append(
            {
                "server_id": sid,
                "server_name": record.get("name"),
                "level": state.get("level") or "ok",
                "recent_rate": _recent_rate(series),
                "baseline_rate": _baseline_rate(series),
                "online": (last or {}).get("online"),
                "active": (last or {}).get("active"),
                "points": len(series),
            }
        )
    return out
