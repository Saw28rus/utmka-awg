"""Роль панели: active | standby (anti-split-brain для миграции узла).

В любой момент только ОДНА панель должна быть `active` и управлять узлами
(планировщик, ротации, reconcile, мутирующие операции). Новая панель при
полной миграции узла (NODE_MIGRATION_PLAN.md) поднимается в `standby` и не
трогает узлы, пока на активации роли не поменяются местами.

Роль хранится в файле НА ХОСТЕ панели (вне тома panel_data!), иначе при
копировании данных роль «приехала» бы на новый сервер. Внутри backend-контейнера
каталог установки смонтирован как PANEL_INSTALL_DIR (по умолчанию /host/utmka-awg).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger("utmka.panel_role")

ROLE_ACTIVE = "active"
ROLE_STANDBY = "standby"

# Внутри backend-контейнера host-каталог панели смонтирован сюда.
_INSTALL_DIR = Path(os.getenv("PANEL_INSTALL_DIR", "/host/utmka-awg"))
_ROLE_FILE = _INSTALL_DIR / "PANEL_ROLE"

# Путь к панели на УДАЛЁННОМ узле (для детекта co-located панели по SSH).
REMOTE_PANEL_DIR = os.getenv("PANEL_HOST_DIR", "/opt/utmka-awg")


def get_role() -> str:
    """Текущая роль этой панели. По умолчанию (нет файла) — active (обратная совместимость)."""
    try:
        value = _ROLE_FILE.read_text(encoding="utf-8").strip().lower()
    except (FileNotFoundError, OSError):
        return ROLE_ACTIVE
    return ROLE_STANDBY if value == ROLE_STANDBY else ROLE_ACTIVE


def is_active() -> bool:
    return get_role() == ROLE_ACTIVE


def is_standby() -> bool:
    return get_role() == ROLE_STANDBY


def set_role(role: str) -> str:
    """Записать роль на хост. Возвращает фактически установленное значение."""
    normalized = ROLE_STANDBY if str(role).strip().lower() == ROLE_STANDBY else ROLE_ACTIVE
    try:
        _INSTALL_DIR.mkdir(parents=True, exist_ok=True)
        _ROLE_FILE.write_text(normalized + "\n", encoding="utf-8")
        logger.info("panel role set to %s", normalized)
    except OSError as exc:  # noqa: BLE001
        logger.warning("не удалось записать роль панели (%s): %s", _ROLE_FILE, exc)
    return normalized


class PanelStandbyError(Exception):
    """Операция запрещена: панель в режиме ожидания (идёт миграция)."""


def ensure_active() -> None:
    """Гард для мутирующих серверных операций. Бросает, если панель в standby."""
    if is_standby():
        raise PanelStandbyError(
            "Эта панель в режиме ожидания (идёт миграция узла). "
            "Управление серверами доступно только на активной панели."
        )


# --------------------------------------------------------------------------- #
# Детект co-located панели на удалённом узле (по SSH)
# --------------------------------------------------------------------------- #

_PANEL_PROBE = (
    f"if [ -f {REMOTE_PANEL_DIR}/docker-compose.yml ]; then echo COMPOSE=1; else echo COMPOSE=0; fi; "
    "C=$(docker ps --format '{{.Names}}' 2>/dev/null | grep -cE 'utmka|(^|[-_])(frontend|backend|postgres)$' || true); "
    "echo CONTAINERS=$C"
)


def is_panel_node(server_id: str) -> bool:
    """True, если на узле физически установлена панель UTMka (co-located).

    Детект по SSH: наличие /opt/utmka-awg/docker-compose.yml или запущенных
    контейнеров панели. Узел недоступен → считаем, что панели нет (не блокируем).
    """
    from app.services.server_store import server_store
    from app.ssh import exec as ssh_exec

    target = server_store.ssh_target(server_id)
    if not target:
        return False
    try:
        ssh = ssh_exec.connect(
            host=target.host,
            port=target.port,
            username=target.username,
            password=target.password,
            key=target.key,
            timeout=15,
        )
    except Exception:  # noqa: BLE001
        return False
    try:
        out = ssh_exec.run(ssh, _PANEL_PROBE, timeout=20).stdout
    except Exception:  # noqa: BLE001
        return False
    finally:
        try:
            ssh.close()
        except Exception:  # noqa: BLE001
            pass

    has_compose = "COMPOSE=1" in out
    containers = 0
    for line in out.splitlines():
        if line.startswith("CONTAINERS="):
            try:
                containers = int(line.split("=", 1)[1].strip())
            except ValueError:
                containers = 0
    return has_compose or containers > 0
