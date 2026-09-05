"""OBS3 — сбор DPI-трендов и детекция возможной деградации.

Сигнал: сколько клиентов сейчас с живым handshake. Резкий обрыв среди тех,
кто в этот час суток обычно на связи → «возможна деградация/блок».
Ночной простой (все спят, VPN не включают) — это не авария.

Не «детектор ТСПУ», а тренды. Read-only по данным панели,
никаких изменений на серверах.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from app.services.client_store import client_store
from app.services.dpi_store import dpi_store
from app.services.notification_store import notification_store
from app.services.server_store import server_store

logger = logging.getLogger(__name__)

# Не судим сервер с парой клиентов — слишком шумно.
MIN_ACTIVE = 4
# Чтобы говорить «обрыв», в окне сравнения кто-то реально был онлайн.
MIN_ONLINE_SIGNAL = 2
# Текущее ниже половины недавней занятости → деградация.
DROP_RATIO = 0.5
# Выход из деградации — с гистерезисом, чтобы 2↔3 клиента не щёлкали уведомлениями.
RECOVER_RATIO = 0.75
# Срез раз в 5 минут: 3 точки ≈ 15 мин.
RECENT_WINDOW = 3
# Запасной «обрыв»: занятость за ~2 часа до текущего окна.
BASELINE_WINDOW = 24
MIN_PRIOR_POINTS = 6
# Для нормы «этот час суток» нужно хотя бы полчаса истории этого часа.
HOUR_MIN_SAMPLES = 6


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _server_sample(server_id: str) -> dict:
    clients = client_store.list_all(server_id=server_id)
    active = [c for c in clients if c.status == "active"]
    online = sum(1 for c in active if c.online)
    active_n = len(active)
    rate = round(online / active_n, 4) if active_n else None
    return {"ts": _now_iso(), "online": online, "active": active_n, "rate": rate}


def _median(values: list[float]) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _online(sample: dict) -> int:
    return int(sample.get("online") or 0)


def _parse_ts(ts: object) -> Optional[datetime]:
    if not ts or not isinstance(ts, str):
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _sample_hour(sample: dict) -> Optional[int]:
    dt = _parse_ts(sample.get("ts"))
    return dt.hour if dt else None


def _recent_samples(series: list[dict]) -> list[dict]:
    return series[-RECENT_WINDOW:] if series else []


def _prior_samples(series: list[dict]) -> list[dict]:
    end = len(series) - RECENT_WINDOW
    if end <= 0:
        return []
    start = max(0, end - BASELINE_WINDOW)
    return series[start:end]


def _mean_online(samples: list[dict]) -> Optional[float]:
    if not samples:
        return None
    return sum(_online(s) for s in samples) / len(samples)


def _max_online(samples: list[dict]) -> int:
    if not samples:
        return 0
    return max(_online(s) for s in samples)


def _recent_rate(series: list[dict]) -> Optional[float]:
    rates = [s["rate"] for s in series[-RECENT_WINDOW:] if s.get("rate") is not None]
    if not rates:
        return None
    return sum(rates) / len(rates)


def _hour_typical_online(series: list[dict], hour: int) -> Optional[float]:
    older = series[:-RECENT_WINDOW] if len(series) > RECENT_WINDOW else []
    vals = [_online(s) for s in older if _sample_hour(s) == hour]
    if len(vals) < HOUR_MIN_SAMPLES:
        return None
    return _median(vals)


def _hour_typical_rate(series: list[dict], hour: int) -> Optional[float]:
    older = series[:-RECENT_WINDOW] if len(series) > RECENT_WINDOW else []
    rates = [
        s["rate"]
        for s in older
        if s.get("rate") is not None and _sample_hour(s) == hour
    ]
    if len(rates) < HOUR_MIN_SAMPLES:
        return None
    return _median(rates)


def _baseline_rate(series: list[dict]) -> Optional[float]:
    """Норма для карточки: типичный rate в этот час суток, не пик за всё время."""
    if not series:
        return None
    hour = _sample_hour(series[-1])
    if hour is not None:
        typical = _hour_typical_rate(series, hour)
        if typical is not None:
            return typical
    prior = _prior_samples(series)
    rates = [s["rate"] for s in prior if s.get("rate") is not None]
    if len(rates) < 3:
        return None
    return _median(rates)


def detect_dpi_level(series: list[dict], prev_level: str = "ok") -> str:
    """ok | degraded. Ноль онлайн ночью не авария; обрыв дневной занятости — да."""
    if not series:
        return "ok"
    last = series[-1]
    if int(last.get("active") or 0) < MIN_ACTIVE:
        return "ok"

    recent = _recent_samples(series)
    recent_online = _mean_online(recent)
    if recent_online is None:
        return "ok"

    hour = _sample_hour(last)
    hour_typical = _hour_typical_online(series, hour) if hour is not None else None

    # Этот час суток обычно тихий (спят) и сейчас тоже тихо — не деградация.
    if hour_typical is not None and hour_typical < MIN_ONLINE_SIGNAL:
        if recent_online <= max(hour_typical, 0.5):
            return "ok"

    prior = _prior_samples(series)
    prior_max = _max_online(prior)
    cliff = (
        len(prior) >= MIN_PRIOR_POINTS
        and prior_max >= MIN_ONLINE_SIGNAL
        and recent_online < prior_max * DROP_RATIO
    )
    hour_drop = (
        hour_typical is not None
        and hour_typical >= MIN_ONLINE_SIGNAL
        and recent_online < hour_typical * DROP_RATIO
    )
    is_bad = cliff or hour_drop
    recover_ref = max(prior_max, hour_typical or 0.0)

    if prev_level == "degraded":
        if recent_online >= recover_ref * RECOVER_RATIO and recent_online >= MIN_ONLINE_SIGNAL:
            return "ok"
        if hour_typical is not None and hour_typical < MIN_ONLINE_SIGNAL:
            return "ok"
        if is_bad:
            return "degraded"
        if recent_online < MIN_ONLINE_SIGNAL:
            return "degraded"
        return "ok"

    return "degraded" if is_bad else "ok"


def quiet_period(series: list[dict]) -> bool:
    """Сейчас типично тихо (ночь/никто не сидит в VPN) — не пишем «связь восстановилась»."""
    if not series:
        return True
    hour = _sample_hour(series[-1])
    typical = _hour_typical_online(series, hour) if hour is not None else None
    recent = _mean_online(_recent_samples(series)) or 0.0
    return typical is not None and typical < MIN_ONLINE_SIGNAL and recent < MIN_ONLINE_SIGNAL


def _evaluate(server_id: str, series: list[dict]) -> str:
    """Возвращает уровень: 'ok' | 'degraded'. Шлёт уведомление при смене."""
    prev = (dpi_store.get_state(server_id) or {}).get("level") or "ok"
    level = detect_dpi_level(series, prev)
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
        elif not quiet_period(series):
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
            level = _evaluate(sid, history + [sample])
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
