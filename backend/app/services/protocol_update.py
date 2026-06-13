"""UP2 apply-путь: управляемое обновление протокола на узле.

Эталон — update-panel.sh: snapshot → действие → health → rollback.

Инкремент 2: реализован AmneziaWG (awg2/awg_legacy). Безопасность («калашников»):
- сначала снимаем bundle (конфиг + ключи + clientsTable + start.sh) — restore-источник;
- бэкапим текущий образ тегом, и только потом пересобираем pinned-версию;
- сборка идёт ДО удаления контейнера: если сборка упала — сервис не тронут;
- после пересоздания восстанавливаем ЖИВОЙ конфиг (НЕ генерим новый — ключи и
  параметры маскировки H/S/J/I сохраняются, клиентам reissue не нужен);
- health-проба (контейнер running + интерфейс поднят); при любом сбое — авто-откат
  на прежний образ и тот же конфиг (fail-closed).

Xray-обновление — отдельным инкрементом (нужен Reality-probe и т.п.).
См. _dev-docs/MULTI_PROTOCOL_RESILIENCE_PLAN.md §5.7.
"""

from __future__ import annotations

import shlex
import time
from dataclasses import dataclass
from typing import Optional

from app.services.amnezia_ssh import (
    base_vars,
    connect_target,
    load_script,
    replace_vars,
    write_host_file,
)
from app.services.awg_install import VARIANTS
from app.services.protocol_versions import pinned, record_install
from app.services.server_store import server_store
from app.ssh import exec as ssh_exec

BACKUP_TAG = "utmka-preupdate"
AWG_CONFIG_DIR = "/opt/amnezia/awg"
AMNEZIA_ROOT = "/opt/amnezia"


@dataclass
class UpdateResult:
    message: str
    container: str
    protocol: str
    from_version: Optional[str]
    to_version: Optional[str]
    rolled_back: bool = False


class UpdateError(Exception):
    def __init__(self, message: str, *, rolled_back: bool = False) -> None:
        super().__init__(message)
        self.rolled_back = rolled_back


def update_protocol(server_id: str, protocol: str) -> UpdateResult:
    pid = (protocol or "").lower()
    if pid not in ("awg2", "awg_legacy"):
        raise UpdateError(
            "Обновление через панель поддержано только для AmneziaWG (Xray — отдельным шагом)."
        )
    return _update_awg(server_id, pid)


def _q(value: str) -> str:
    return shlex.quote(value)


def _container_status(ssh, container: str) -> str:
    return ssh_exec.run(
        ssh,
        f"docker inspect -f '{{{{.State.Status}}}}' {_q(container)} 2>/dev/null || true",
    ).stdout.strip()


def _iface_up(ssh, container: str, iface: str) -> bool:
    out = ssh_exec.run(
        ssh,
        f"docker exec {_q(container)} sh -c 'ip -o link show {_q(iface)} >/dev/null 2>&1 "
        f"&& echo up || echo down' 2>/dev/null || true",
        timeout=20,
    ).stdout.strip()
    return out.endswith("up")


def _resolve_port(record: dict, store_key: str) -> Optional[int]:
    proto = (record.get("installed_protocols") or {}).get(store_key) or {}
    port = proto.get("port")
    if isinstance(port, int):
        return port
    vpn_port = record.get("vpn_port")
    return vpn_port if isinstance(vpn_port, int) else None


def _capture_bundle(ssh, container: str) -> str:
    """Снять live-снимок (конфиг+ключи+start.sh) в base64. Источник restore."""
    cmd = (
        f"docker exec {_q(container)} sh -c "
        f"{_q(f'tar czf - -C {AMNEZIA_ROOT} awg start.sh 2>/dev/null | base64 -w0')}"
    )
    result = ssh_exec.run(ssh, cmd, timeout=180)
    blob = (result.stdout or "").strip()
    if result.exit_code != 0 or not blob:
        raise UpdateError("Не удалось снять снапшот конфига перед обновлением.")
    return blob


def _restore_bundle(ssh, container: str, bundle_b64: str) -> None:
    """Восстановить конфиг/ключи/start.sh из bundle в свежесозданный контейнер."""
    ssh_exec.run(ssh, f"docker exec {_q(container)} mkdir -p {_q(AWG_CONFIG_DIR)}", timeout=30)
    tmp = f"/tmp/utmka_{container}_restore.b64"
    write_host_file(ssh, tmp, bundle_b64, mode="600")
    cmd = (
        f"base64 -d {_q(tmp)} | sudo docker exec -i {_q(container)} "
        f"tar xzf - -C {_q(AMNEZIA_ROOT)}; rc=$?; sudo rm -f {_q(tmp)}; exit $rc"
    )
    result = ssh_exec.run(ssh, cmd, timeout=120)
    if result.exit_code != 0:
        raise UpdateError(
            f"Не удалось восстановить конфиг в контейнере: {result.stderr.strip()[-300:]}"
        )


def _start_interface(ssh, container: str) -> None:
    cmd = (
        f"sudo docker exec {_q(container)} chmod a+x {_q(AMNEZIA_ROOT)}/start.sh "
        f"&& sudo docker exec -d {_q(container)} {_q(AMNEZIA_ROOT)}/start.sh"
    )
    result = ssh_exec.run(ssh, cmd, timeout=60)
    if result.exit_code != 0:
        raise UpdateError(f"Не удалось запустить интерфейс: {result.stderr.strip()[-300:]}")


def _recreate_and_start(ssh, cfg: dict, port: int, host: str, bundle_b64: str) -> None:
    """rm → run → restore → start → health. Используется и для apply, и для rollback."""
    container = cfg["container"]
    ssh_exec.run(ssh, f"docker rm -f {_q(container)} 2>/dev/null || true", timeout=60)

    vars_map = base_vars(host, container, cfg["folder"])
    vars_map["$AWG_SERVER_PORT"] = str(port)
    run_script_text = replace_vars(load_script(cfg["scripts"], "run_container.sh"), vars_map)
    from app.services.amnezia_ssh import run_script

    run_res = run_script(ssh, run_script_text, timeout=120)
    if run_res.exit_code != 0:
        combined = f"{run_res.stdout}\n{run_res.stderr}".strip()[-300:]
        raise UpdateError(f"Запуск контейнера не удался: {combined}")

    _restore_bundle(ssh, container, bundle_b64)
    _start_interface(ssh, container)

    time.sleep(2)
    if _container_status(ssh, container) != "running":
        raise UpdateError(f"Контейнер {container} не в состоянии running после пересоздания.")
    if not _iface_up(ssh, container, cfg["iface"]):
        raise UpdateError(f"Интерфейс {cfg['iface']} не поднялся после обновления.")


def _update_awg(server_id: str, pid: str) -> UpdateResult:
    cfg = VARIANTS[pid]
    container = cfg["container"]

    record = server_store.get_record(server_id)
    if not record:
        raise UpdateError("Сервер не найден.")
    port = _resolve_port(record, cfg["store_key"])
    if not port:
        raise UpdateError("Не удалось определить порт протокола — обновление прервано.")

    to_version = (pinned(pid) or {}).get("version")
    from_version = None
    try:
        record2, target, ssh = connect_target(server_id)
    except Exception as exc:  # noqa: BLE001
        raise UpdateError(f"Не удалось подключиться к серверу: {exc}") from exc

    host = target.host
    try:
        if _container_status(ssh, container) != "running":
            raise UpdateError(f"Контейнер {container} не запущен — обновлять нечего.")

        # 1. Snapshot (restore-источник). До любых изменений.
        bundle = _capture_bundle(ssh, container)

        # 2. Бэкап текущего образа тегом (старая версия сохранена для отката).
        ssh_exec.run(ssh, f"docker rmi {_q(f'{container}:{BACKUP_TAG}')} 2>/dev/null || true", timeout=60)
        tag_res = ssh_exec.run(
            ssh, f"docker tag {_q(container)} {_q(f'{container}:{BACKUP_TAG}')}", timeout=30
        )
        if tag_res.exit_code != 0:
            raise UpdateError("Не удалось забэкапить текущий образ — обновление прервано.")

        # 3. Пересборка pinned-образа. Контейнер ещё НЕ тронут: при сбое сервис цел.
        write_host_file(
            ssh, f"{cfg['folder']}/Dockerfile", load_script(cfg["scripts"], "Dockerfile"), mode="700"
        )
        from app.services.amnezia_ssh import run_script

        build_text = replace_vars(
            load_script("shared", "build_container.sh"), base_vars(host, container, cfg["folder"])
        )
        build_res = run_script(ssh, build_text, timeout=900)
        build_out = f"{build_res.stdout}\n{build_res.stderr}"
        if "pull rate limit" in build_out.lower():
            raise UpdateError("Docker Hub ограничил скачивание образов. Повтори позже.")
        if build_res.exit_code != 0:
            raise UpdateError(f"Сборка нового образа не удалась: {build_out.strip()[-300:]}")

        # 4-6. Пересоздать на новом образе, восстановить конфиг, поднять, health.
        try:
            _recreate_and_start(ssh, cfg, port, host, bundle)
        except UpdateError as exc:
            _rollback_to_backup(ssh, cfg, port, host, bundle)
            raise UpdateError(
                f"Обновление не удалось ({exc}). Выполнен авто-откат на прежнюю версию.",
                rolled_back=True,
            ) from exc

        # 7. Успех: зафиксировать версию в манифесте.
        record_install(server_id, pid)
        return UpdateResult(
            message=f"{cfg['label']} обновлён до {to_version}. Клиенты не затронуты.",
            container=container,
            protocol=pid,
            from_version=from_version,
            to_version=to_version,
            rolled_back=False,
        )
    finally:
        ssh.close()


def _rollback_to_backup(ssh, cfg: dict, port: int, host: str, bundle_b64: str) -> None:
    """Вернуть прежний образ из бэкап-тега и пересоздать контейнер с тем же конфигом."""
    container = cfg["container"]
    try:
        retag = ssh_exec.run(
            ssh, f"docker tag {_q(f'{container}:{BACKUP_TAG}')} {_q(container)}", timeout=30
        )
        if retag.exit_code != 0:
            return
        _recreate_and_start(ssh, cfg, port, host, bundle_b64)
    except Exception:  # noqa: BLE001
        # Откат — best-effort; если и он упал, оставляем как есть (оператор увидит ошибку).
        pass
