"""Health-движок (OBS1): детект состояния узлов + авто-restart с ограничителями.

Калашников:
- проверка по расписанию (scheduler), одна SSH-сессия на узел;
- авто-restart только «упавших» контейнеров (exited/created), НЕ трогаем
  crash-loop (restarting) — чтобы не усугублять;
- жёсткий лимит попыток в окне (anti-flapping), иначе помечаем degraded;
- состояние и алерты persist в health_store (panel_data);
- секреты не логируются.
"""

from __future__ import annotations

import logging
import shlex
from datetime import datetime, timezone

from app.services.health_store import health_store
from app.services.notification_store import notification_store
from app.services.server_store import server_store
from app.ssh import exec as ssh_exec

logger = logging.getLogger("utmka.health")

MAX_RESTARTS_PER_WINDOW = 3
RESTART_WINDOW_SECONDS = 1800  # 30 минут
RESTARTABLE_STATES = {"exited", "created", "dead"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso() -> str:
    return _now().isoformat()


def _expected_containers(record: dict) -> list[str]:
    names = list(record.get("container_names") or [])
    # дополняем по installed_protocols (на случай рассинхрона списков)
    for proto in (record.get("installed_protocols") or {}).values():
        c = proto.get("container") if isinstance(proto, dict) else None
        if c and c not in names:
            names.append(c)
    return names


def _container_status(ssh, container: str) -> str:
    res = ssh_exec.run(
        ssh,
        f"docker inspect -f '{{{{.State.Status}}}}' {shlex.quote(container)} 2>/dev/null || echo missing",
        timeout=20,
    )
    return (res.stdout.strip() or "unknown")


def _restart_budget(prev: dict, container: str) -> bool:
    """True, если ещё можно перезапускать контейнер в текущем окне."""
    history = (prev.get("restarts") or {}).get(container) or {}
    window_start = history.get("window_start")
    count = int(history.get("count") or 0)
    if not window_start:
        return True
    try:
        started = datetime.fromisoformat(window_start)
    except ValueError:
        return True
    if (_now() - started).total_seconds() > RESTART_WINDOW_SECONDS:
        return True  # окно истекло — бюджет сброшен
    return count < MAX_RESTARTS_PER_WINDOW


def _bump_restart(restarts: dict, container: str) -> None:
    history = restarts.get(container) or {}
    window_start = history.get("window_start")
    reset = True
    if window_start:
        try:
            started = datetime.fromisoformat(window_start)
            reset = (_now() - started).total_seconds() > RESTART_WINDOW_SECONDS
        except ValueError:
            reset = True
    if reset:
        restarts[container] = {"window_start": _iso(), "count": 1, "last_at": _iso()}
    else:
        history["count"] = int(history.get("count") or 0) + 1
        history["last_at"] = _iso()
        restarts[container] = history


def _notify_transition(record: dict, prev_state: str, new_state: str, restarted: list[str], skipped_limit: list[str]) -> None:
    """Уведомления только при смене состояния и на событиях (без спама)."""
    name = record.get("name") or record.get("host") or record["id"]
    sid = record["id"]
    if restarted:
        notification_store.add(
            level="info", code="auto_restart",
            title=f"Авто-перезапуск на «{name}»",
            message="Восстановлены контейнеры: " + ", ".join(restarted) + ".",
            server_id=sid,
        )
    if skipped_limit:
        notification_store.add(
            level="danger", code="restart_limit",
            title=f"Лимит перезапусков на «{name}»",
            message="Не удаётся стабилизировать: " + ", ".join(skipped_limit) + ". Нужна ручная проверка.",
            server_id=sid,
        )
    if prev_state == new_state:
        return
    if new_state == "down":
        notification_store.add(
            level="danger", code="node_down",
            title=f"Узел «{name}» недоступен",
            message="SSH не отвечает — проверьте сервер.", server_id=sid,
        )
    elif new_state == "degraded" and prev_state in ("ok", "unknown", ""):
        notification_store.add(
            level="warning", code="node_degraded",
            title=f"Проблемы на «{name}»",
            message="Один или несколько контейнеров не работают.", server_id=sid,
        )
    elif new_state == "ok" and prev_state in ("degraded", "down"):
        notification_store.add(
            level="info", code="node_recovered",
            title=f"Узел «{name}» восстановлен",
            message="Все контейнеры снова работают.", server_id=sid,
        )


def check_server(server_id: str, *, auto_restart: bool = True) -> dict:
    record = server_store.get_record(server_id)
    if not record:
        health_store.forget(server_id)
        return {"server_id": server_id, "state": "unknown", "message": "Сервер не найден."}

    prev = health_store.get(server_id) or {}
    prev_state = prev.get("state") or "unknown"
    restarts = dict(prev.get("restarts") or {})
    target = server_store.ssh_target(server_id)
    if not target:
        return health_store.upsert(
            server_id, state="unknown", online=False, checked_at=_iso(),
            message="SSH-доступ не настроен.",
        )

    try:
        ssh = ssh_exec.connect(
            host=target.host, port=target.port, username=target.username,
            password=target.password, key=target.key, timeout=15,
        )
    except Exception:  # noqa: BLE001
        consecutive = int(prev.get("consecutive_failures") or 0) + 1
        result = health_store.upsert(
            server_id, state="down", online=False, checked_at=_iso(),
            consecutive_failures=consecutive,
            message="SSH не отвечает (узел недоступен).",
            alerts=[{"level": "danger", "code": "node_down",
                     "message": "Узел недоступен по SSH."}],
        )
        _notify_transition(record, prev_state, "down", [], [])
        return result

    try:
        containers = _expected_containers(record)
        statuses: dict[str, str] = {c: _container_status(ssh, c) for c in containers}
        restarted: list[str] = []
        skipped_limit: list[str] = []

        problems = [c for c, s in statuses.items() if s != "running" and s != "missing"]
        if auto_restart:
            for c in problems:
                if statuses[c] not in RESTARTABLE_STATES:
                    continue  # restarting/paused — не вмешиваемся
                if not _restart_budget({"restarts": restarts}, c):
                    skipped_limit.append(c)
                    continue
                ssh_exec.run(ssh, f"docker start {shlex.quote(c)} 2>/dev/null || true", timeout=60)
                _bump_restart(restarts, c)
                statuses[c] = _container_status(ssh, c)
                if statuses[c] == "running":
                    restarted.append(c)
                logger.info("health: restart %s on %s -> %s", c, server_id, statuses[c])

        still_bad = [c for c, s in statuses.items() if s not in ("running", "missing")]
        alerts: list[dict] = []
        for c in still_bad:
            if c in skipped_limit:
                alerts.append({"level": "danger", "code": "restart_limit",
                               "message": f"Контейнер {c}: достигнут лимит авто-перезапусков."})
            else:
                alerts.append({"level": "warning", "code": "container_down",
                               "message": f"Контейнер {c}: статус {statuses[c]}."})
        if restarted:
            alerts.append({"level": "info", "code": "auto_restart",
                           "message": "Авто-перезапуск: " + ", ".join(restarted) + "."})

        state = "ok" if not still_bad else "degraded"
        result = health_store.upsert(
            server_id,
            state=state,
            online=True,
            checked_at=_iso(),
            consecutive_failures=0,
            containers=statuses,
            restarts=restarts,
            restarted=restarted,
            alerts=alerts,
            message="Все контейнеры работают." if state == "ok" else "Есть проблемы с контейнерами.",
        )
        server_store.update_runtime(server_id, status="online")
        _notify_transition(record, prev_state, state, restarted, skipped_limit)
        return result
    finally:
        ssh.close()


def run_health_check_all(*, auto_restart: bool = True) -> dict:
    checked = 0
    degraded = 0
    down = 0
    restarted = 0
    for record in server_store.list_records():
        sid = record.get("id")
        if not sid:
            continue
        checked += 1
        try:
            res = check_server(sid, auto_restart=auto_restart)
        except Exception:  # noqa: BLE001
            logger.exception("health: check failed for %s", sid)
            continue
        if res.get("state") == "degraded":
            degraded += 1
        elif res.get("state") == "down":
            down += 1
        restarted += len(res.get("restarted") or [])
    return {"checked": checked, "degraded": degraded, "down": down, "restarted": restarted}


def get_health_overview() -> list[dict]:
    out: list[dict] = []
    for record in server_store.list_records():
        sid = record["id"]
        h = health_store.get(sid) or {}
        out.append(
            {
                "server_id": sid,
                "server_name": record.get("name"),
                "host": record.get("host"),
                "state": h.get("state") or "unknown",
                "online": h.get("online", False),
                "checked_at": h.get("checked_at"),
                "containers": h.get("containers") or {},
                "alerts": h.get("alerts") or [],
                "restarted": h.get("restarted") or [],
            }
        )
    return out
