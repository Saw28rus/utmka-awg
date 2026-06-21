"""UP2 apply-путь: управляемое обновление протокола на узле.

Эталон — update-panel.sh: snapshot → действие → health → rollback.

Поддержаны AmneziaWG (awg2/awg_legacy) и Xray (VLESS-Reality). Безопасность
(«калашников»):
- сначала снимаем bundle (конфиг + ключи + start.sh) — restore-источник;
- бэкапим текущий образ тегом, и только потом пересобираем pinned-версию;
- сборка идёт ДО удаления контейнера: если сборка упала — сервис не тронут;
- после пересоздания восстанавливаем ЖИВОЙ конфиг (НЕ генерим новый — ключи и
  параметры маскировки сохраняются, клиентам reissue не нужен);
- health-проба (контейнер running + протокол-специфичная проверка); при любом
  сбое — авто-откат на прежний образ и тот же конфиг (fail-closed).

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
    detect_xray_arch,
    load_script,
    replace_vars,
    run_script,
    write_host_file,
)
from app.services.awg_install import VARIANTS
from app.services.protocol_versions import pinned, record_install
from app.services.server_store import server_store
from app.services.xray_install import XRAY_LOCAL_PORT, _panel_reserves_443
from app.ssh import exec as ssh_exec

BACKUP_TAG = "utmka-preupdate"
AMNEZIA_ROOT = "/opt/amnezia"

SUPPORTED = ("awg2", "awg_legacy", "xray")


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


def _spec(pid: str) -> Optional[dict]:
    """Протокол-специфичные параметры обновления (контейнер, скрипты, health)."""
    if pid in ("awg2", "awg_legacy"):
        v = VARIANTS[pid]
        return {
            "protocol": pid,
            "container": v["container"],
            "folder": v["folder"],
            "scripts": v["scripts"],
            "config_subdir": "awg",
            "port_var": "$AWG_SERVER_PORT",
            "store_key": v["store_key"],
            "label": v["label"],
            "health": "iface",
            "iface": v["iface"],
            "arch": False,
        }
    if pid == "xray":
        return {
            "protocol": "xray",
            "container": "amnezia-xray",
            "folder": "/opt/amnezia/amnezia-xray",
            "scripts": "xray",
            "config_subdir": "xray",
            "port_var": "$XRAY_SERVER_PORT",
            "store_key": "xray",
            "label": "Xray (VLESS-Reality)",
            "health": "process",
            "iface": None,
            "arch": True,
        }
    return None


def update_protocol(server_id: str, protocol: str) -> UpdateResult:
    pid = (protocol or "").lower()
    spec = _spec(pid)
    if not spec:
        raise UpdateError(f"Обновление протокола «{protocol}» не поддержано.")
    return _run_update(server_id, spec)


def _q(value: str) -> str:
    return shlex.quote(value)


def _container_status(ssh, container: str) -> str:
    return ssh_exec.run(
        ssh,
        f"docker inspect -f '{{{{.State.Status}}}}' {_q(container)} 2>/dev/null || true",
    ).stdout.strip()


def _health_ok(ssh, spec: dict) -> bool:
    container = spec["container"]
    if _container_status(ssh, container) != "running":
        return False
    if spec["health"] == "iface":
        out = ssh_exec.run(
            ssh,
            f"docker exec {_q(container)} sh -c 'ip -o link show {_q(spec['iface'])} "
            f">/dev/null 2>&1 && echo up || echo down' 2>/dev/null || true",
            timeout=20,
        ).stdout.strip()
        return out.endswith("up")
    # health == "process": процесс xray поднялся.
    out = ssh_exec.run(
        ssh,
        f"docker exec {_q(container)} sh -c 'pgrep -x xray >/dev/null && echo ok || echo fail' "
        f"2>/dev/null || true",
        timeout=20,
    ).stdout.strip()
    return out.endswith("ok")


def _resolve_port(record: dict, store_key: str) -> Optional[int]:
    proto = (record.get("installed_protocols") or {}).get(store_key) or {}
    port = proto.get("port")
    if isinstance(port, int):
        return port
    vpn_port = record.get("vpn_port")
    return vpn_port if isinstance(vpn_port, int) else None


def _capture_bundle(ssh, spec: dict) -> str:
    """Снять live-снимок (конфиг+ключи+start.sh) в base64. Источник restore."""
    container = spec["container"]
    subdir = spec["config_subdir"]
    cmd = (
        f"docker exec {_q(container)} sh -c "
        f"{_q(f'tar czf - -C {AMNEZIA_ROOT} {subdir} start.sh 2>/dev/null | base64 -w0')}"
    )
    result = ssh_exec.run(ssh, cmd, timeout=180)
    blob = (result.stdout or "").strip()
    if result.exit_code != 0 or not blob:
        raise UpdateError("Не удалось снять снапшот конфига перед обновлением.")
    return blob


def _restore_bundle(ssh, spec: dict, bundle_b64: str) -> None:
    """Восстановить конфиг/ключи/start.sh из bundle в свежесозданный контейнер."""
    container = spec["container"]
    config_dir = f"{AMNEZIA_ROOT}/{spec['config_subdir']}"
    ssh_exec.run(ssh, f"docker exec {_q(container)} mkdir -p {_q(config_dir)}", timeout=30)
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


def _start_process(ssh, container: str) -> None:
    cmd = (
        f"sudo docker exec {_q(container)} chmod a+x {_q(AMNEZIA_ROOT)}/start.sh "
        f"&& sudo docker exec -d {_q(container)} {_q(AMNEZIA_ROOT)}/start.sh"
    )
    result = ssh_exec.run(ssh, cmd, timeout=60)
    if result.exit_code != 0:
        raise UpdateError(f"Не удалось запустить протокол: {result.stderr.strip()[-300:]}")


def _run_container(
    ssh, spec: dict, port: int, host: str, extra_vars: Optional[dict[str, str]] = None
) -> None:
    vars_map = base_vars(host, spec["container"], spec["folder"])
    vars_map[spec["port_var"]] = str(port)
    if extra_vars:
        vars_map.update(extra_vars)
    run_text = replace_vars(load_script(spec["scripts"], "run_container.sh"), vars_map)
    run_res = run_script(ssh, run_text, timeout=120)
    if run_res.exit_code != 0:
        combined = f"{run_res.stdout}\n{run_res.stderr}".strip()[-300:]
        raise UpdateError(f"Запуск контейнера не удался: {combined}")


def _recreate_and_start(
    ssh, spec: dict, port: int, host: str, bundle_b64: str,
    extra_vars: Optional[dict[str, str]] = None,
) -> None:
    """rm → run → restore → start → health. Используется и для apply, и для rollback."""
    container = spec["container"]
    ssh_exec.run(ssh, f"docker rm -f {_q(container)} 2>/dev/null || true", timeout=60)
    _run_container(ssh, spec, port, host, extra_vars)
    _restore_bundle(ssh, spec, bundle_b64)
    _start_process(ssh, container)

    time.sleep(3)
    if not _health_ok(ssh, spec):
        raise UpdateError(f"Health-проверка не пройдена после пересоздания ({container}).")


def _upload_dockerfile(ssh, spec: dict) -> None:
    dockerfile = load_script(spec["scripts"], "Dockerfile")
    if spec["arch"]:
        arch = detect_xray_arch(ssh)
        dockerfile = dockerfile.replace("Xray-linux-64.zip", f"Xray-linux-{arch}.zip")
    write_host_file(ssh, f"{spec['folder']}/Dockerfile", dockerfile, mode="700")


def _build_image(ssh, spec: dict, host: str) -> None:
    build_text = replace_vars(
        load_script("shared", "build_container.sh"),
        base_vars(host, spec["container"], spec["folder"]),
    )
    build_res = run_script(ssh, build_text, timeout=900)
    build_out = f"{build_res.stdout}\n{build_res.stderr}"
    if "pull rate limit" in build_out.lower():
        raise UpdateError("Docker Hub ограничил скачивание образов. Повтори позже.")
    if build_res.exit_code != 0:
        raise UpdateError(f"Сборка нового образа не удалась: {build_out.strip()[-300:]}")


def _run_update(server_id: str, spec: dict) -> UpdateResult:
    pid = spec["protocol"]
    container = spec["container"]

    record = server_store.get_record(server_id)
    if not record:
        raise UpdateError("Сервер не найден.")
    port = _resolve_port(record, spec["store_key"])
    if not port:
        raise UpdateError("Не удалось определить порт протокола — обновление прервано.")

    # Xray в режиме резерва :443 публикуется на 127.0.0.1:1443 (наружу :443 держит nginx).
    # При обновлении сохраняем то же отображение, иначе контейнер встанет на 0.0.0.0:443
    # и конфликтнёт с nginx. Для прочих протоколов/режимов — публикация = тот же порт.
    extra_vars: dict[str, str] = {}
    if pid == "xray":
        if _panel_reserves_443(record):
            extra_vars["$XRAY_PUBLISH"] = f"127.0.0.1:{XRAY_LOCAL_PORT}"
        else:
            extra_vars["$XRAY_PUBLISH"] = str(port)

    to_version = (pinned(pid) or {}).get("version")
    try:
        _record2, target, ssh = connect_target(server_id)
    except Exception as exc:  # noqa: BLE001
        raise UpdateError(f"Не удалось подключиться к серверу: {exc}") from exc

    host = target.host
    try:
        if _container_status(ssh, container) != "running":
            raise UpdateError(f"Контейнер {container} не запущен — обновлять нечего.")

        # 1. Snapshot (restore-источник). До любых изменений.
        bundle = _capture_bundle(ssh, spec)

        # 2. Бэкап текущего образа тегом (старая версия сохранена для отката).
        backup_ref = f"{container}:{BACKUP_TAG}"
        ssh_exec.run(ssh, f"docker rmi {_q(backup_ref)} 2>/dev/null || true", timeout=60)
        tag_res = ssh_exec.run(ssh, f"docker tag {_q(container)} {_q(backup_ref)}", timeout=30)
        if tag_res.exit_code != 0:
            raise UpdateError("Не удалось забэкапить текущий образ — обновление прервано.")

        # 3. Пересборка pinned-образа. Контейнер ещё НЕ тронут: при сбое сервис цел.
        _upload_dockerfile(ssh, spec)
        _build_image(ssh, spec, host)

        # 4-6. Пересоздать на новом образе, восстановить конфиг, поднять, health.
        try:
            _recreate_and_start(ssh, spec, port, host, bundle, extra_vars)
        except UpdateError as exc:
            _rollback_to_backup(ssh, spec, port, host, bundle, extra_vars)
            raise UpdateError(
                f"Обновление не удалось ({exc}). Выполнен авто-откат на прежнюю версию.",
                rolled_back=True,
            ) from exc

        # 7. Успех: зафиксировать версию в манифесте.
        record_install(server_id, pid)
        return UpdateResult(
            message=f"{spec['label']} обновлён до {to_version}. Клиенты не затронуты.",
            container=container,
            protocol=pid,
            from_version=None,
            to_version=to_version,
            rolled_back=False,
        )
    finally:
        ssh.close()


def _rollback_to_backup(
    ssh, spec: dict, port: int, host: str, bundle_b64: str,
    extra_vars: Optional[dict[str, str]] = None,
) -> None:
    """Вернуть прежний образ из бэкап-тега и пересоздать контейнер с тем же конфигом."""
    container = spec["container"]
    backup_ref = f"{container}:{BACKUP_TAG}"
    try:
        retag = ssh_exec.run(ssh, f"docker tag {_q(backup_ref)} {_q(container)}", timeout=30)
        if retag.exit_code != 0:
            return
        _recreate_and_start(ssh, spec, port, host, bundle_b64, extra_vars)
    except Exception:  # noqa: BLE001
        # Откат — best-effort; если и он упал, оставляем как есть (оператор увидит ошибку).
        pass
