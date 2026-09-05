"""DPI-тренд: ночной простой ≠ деградация, резкий дневной обрыв — да."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.dpi_stats import detect_dpi_level, quiet_period


ACTIVE = 6
DAY_ONLINE = 4
START = datetime(2026, 9, 3, 0, 0, tzinfo=timezone.utc)


def _pt(ts: datetime, online: int, active: int = ACTIVE) -> dict:
    rate = round(online / active, 4) if active else None
    return {"ts": ts.isoformat(), "online": online, "active": active, "rate": rate}


def _walk(hours: int, online_at) -> list[dict]:
    """Срезы каждые 5 минут. online_at(hour) → число онлайн."""
    out: list[dict] = []
    for i in range(hours * 12):
        ts = START + timedelta(minutes=5 * i)
        out.append(_pt(ts, online_at(ts.hour)))
    return out


def _day_night(hour: int) -> int:
    return DAY_ONLINE if 8 <= hour < 18 else 0


def test_night_idle_is_ok_even_if_daytime_peak_was_high() -> None:
    series = _walk(48, _day_night)
    last = datetime.fromisoformat(series[-1]["ts"])
    assert last.hour not in range(8, 18)
    assert series[-1]["online"] == 0
    assert detect_dpi_level(series) == "ok"
    assert detect_dpi_level(series, prev_level="degraded") == "ok"
    assert quiet_period(series) is True


def test_daytime_cliff_is_degraded() -> None:
    series = _walk(36, _day_night)
    last = datetime.fromisoformat(series[-1]["ts"])
    assert 8 <= last.hour < 18
    assert series[-1]["online"] == DAY_ONLINE
    # Последние 15 мин — все пропали, как при блоке.
    dropped = list(series)
    for i in range(1, 4):
        dropped[-i] = {**dropped[-i], "online": 0, "rate": 0.0}
    assert detect_dpi_level(dropped) == "degraded"


def test_few_clients_never_degraded() -> None:
    series = []
    for i in range(40):
        ts = START + timedelta(minutes=5 * i)
        online = 2 if i < 30 else 0
        series.append(_pt(ts, online, active=2))
    assert detect_dpi_level(series) == "ok"


def test_empty_or_short_series_ok() -> None:
    assert detect_dpi_level([]) == "ok"
    assert detect_dpi_level([_pt(START, 0)]) == "ok"


def test_hysteresis_does_not_flap_on_small_swings() -> None:
    series = _walk(36, _day_night)
    # 4 → 3: не половина, не деградация.
    mild = list(series)
    for i in range(1, 4):
        mild[-i] = {**mild[-i], "online": 3, "rate": round(3 / ACTIVE, 4)}
    assert detect_dpi_level(mild) == "ok"

    dropped = list(series)
    for i in range(1, 4):
        dropped[-i] = {**dropped[-i], "online": 0, "rate": 0.0}
    assert detect_dpi_level(dropped, prev_level="ok") == "degraded"
    # Ещё нули — остаёмся в деградации, пока люди не вернулись.
    assert detect_dpi_level(dropped, prev_level="degraded") == "degraded"
    # Вернулись к дневной занятости.
    assert detect_dpi_level(series, prev_level="degraded") == "ok"


def test_old_all_time_max_would_false_alarm_at_night() -> None:
    """Раньше норма = max за историю: ночь 0% при дневном пике 4/6 → ложная авария."""
    series = _walk(48, _day_night)
    rates = [s["rate"] for s in series if s.get("rate") is not None]
    assert max(rates) >= 0.6
    assert series[-1]["rate"] == 0.0
    # Старое правило: recent <= max * 0.5
    recent = sum(s["rate"] for s in series[-3:]) / 3
    assert recent <= max(rates) * 0.5
    assert detect_dpi_level(series) == "ok"
