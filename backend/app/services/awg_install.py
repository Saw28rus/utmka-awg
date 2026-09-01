"""Установка AmneziaWG (awg-go) и AmneziaWG Legacy на сервер по сценарию Amnezia."""

from __future__ import annotations

import secrets as _secrets
import shlex
from dataclasses import dataclass
from typing import Optional

from app.services.amnezia_ssh import (
    base_vars,
    connect_target,
    container_exists,
    docker_available,
    load_script,
    port_busy,
    read_container_file,
    replace_vars,
    run_container_script,
    run_script,
    write_host_file,
)
from app.services.server_store import server_store
from app.ssh import exec as ssh_exec

# variant -> (container, folder, script_dir, conf, iface, store_key)
VARIANTS = {
    "awg2": {
        "container": "amnezia-awg2",
        "folder": "/opt/amnezia/amnezia-awg2",
        "scripts": "awg",
        "conf": "/opt/amnezia/awg/awg0.conf",
        "iface": "awg0",
        "store_key": "awg2",
        "label": "AmneziaWG 2.0",
        "profile": "awg2",
        "subnet_ip": "10.8.1.1",
        "with_s34": True,
    },
    "awg31": {
        "container": "amnezia-awg31",
        "folder": "/opt/amnezia/amnezia-awg31",
        "scripts": "awg31",
        "conf": "/opt/amnezia/awg/awg0.conf",
        "iface": "awg0",
        "store_key": "awg31",
        "label": "AmneziaWG 3.1",
        "profile": "awg31",
        "subnet_ip": "10.8.3.1",
        "with_s34": True,
    },
    "awg_legacy": {
        "container": "amnezia-awg",
        "folder": "/opt/amnezia/amnezia-awg",
        "scripts": "awg_legacy",
        "conf": "/opt/amnezia/awg/wg0.conf",
        "iface": "wg0",
        "store_key": "awg_legacy",
        "label": "AmneziaWG Legacy",
        "profile": "legacy",
        "subnet_ip": "10.8.1.1",
        "with_s34": False,
    },
}

DEFAULT_PORT = 55424
DEFAULT_SUBNET_IP = "10.8.1.1"
DEFAULT_CIDR = "24"


@dataclass
class AwgInstallResult:
    message: str
    container: str
    port: int
    public_key: Optional[str] = None


class AwgInstallError(Exception):
    pass


def _first_free_udp(ssh, candidates: tuple[int, ...], *, skip: int) -> Optional[int]:
    for candidate in candidates:
        if candidate == skip:
            continue
        if not port_busy(ssh, candidate, proto="udp"):
            return candidate
    return None


def _ensure_host_udp_allow(ssh, port: int) -> None:
    """Если UFW активен — открыть UDP нового протокола (2.0 уже мог быть в правилах)."""
    run_script(
        ssh,
        (
            "if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -qi '^Status: active'; then "
            f"ufw allow {int(port)}/udp comment 'utmka-awg' >/dev/null 2>&1 || true; fi"
        ),
        timeout=20,
    )


def install_awg(server_id: str, *, variant: str = "awg2", port: int = DEFAULT_PORT) -> AwgInstallResult:
    cfg = VARIANTS.get(variant)
    if not cfg:
        raise AwgInstallError("Неизвестная версия AmneziaWG.")

    container = cfg["container"]
    record, target, ssh = connect_target(server_id)
    try:
        if container_exists(ssh, container):
            # Контейнер есть, а карточка/выдача клиентов смотрит в store —
            # дописываем протокол, иначе 3.1 виден на сервере, но не в «Создать клиента».
            _register(server_id, record, cfg, port)
            raise AwgInstallError(f"{cfg['label']} уже установлен (контейнер {container}).")

        if not docker_available(ssh):
            _ensure_docker(ssh, target.host, container, cfg["folder"])

        if port_busy(ssh, port, proto="udp"):
            if variant == "awg31":
                fallback = _first_free_udp(ssh, (443, 55425, 55426, 55427, 51820), skip=port)
                if fallback is None:
                    raise AwgInstallError(
                        f"UDP-порт {port} занят (обычно это AmneziaWG 2.0). "
                        "Укажи свободный порт, например 443."
                    )
                port = fallback
            else:
                raise AwgInstallError(f"UDP-порт {port} уже занят на сервере.")

        vars_map = _build_vars(target.host, port, cfg)
        _prepare_host(ssh, vars_map)
        _upload_dockerfile(ssh, cfg, vars_map)
        _build_image(ssh, vars_map)
        _run_container(ssh, cfg, vars_map)
        _configure_container(ssh, cfg, vars_map)
        _startup_container(ssh, cfg, vars_map)
        _verify_running(ssh, container)
        if variant == "awg31":
            _verify_awg_iface(ssh, container, port)
        _ensure_host_udp_allow(ssh, port)

        public_key = read_container_file(ssh, container, "/opt/amnezia/awg/wireguard_server_public_key.key") or None
        _register(server_id, record, cfg, port)

        extra = ""
        if variant == "awg31":
            extra = (
                f" UDP {port} должен быть открыт в файрволе хостинга. "
                "Ключ клиенту — только vpn:// в Amnezia VPN 5.0.1.5+, не приложение AmneziaWG."
            )
            extra += _maybe_attach_awg31_cascade(server_id)

        return AwgInstallResult(
            message=f"{cfg['label']} установлен на UDP {port}.{extra} Добавляй клиентов во вкладке «Клиенты».",
            container=container,
            port=port,
            public_key=public_key,
        )
    except AwgInstallError as exc:
        if "уже установлен" not in str(exc).lower():
            _rollback(ssh, container)
        raise
    except Exception as exc:  # noqa: BLE001
        _rollback(ssh, container)
        raise AwgInstallError(str(exc)) from exc
    finally:
        ssh.close()


def _awg_params(with_s34: bool | str = True) -> dict[str, str]:
    """Параметры обфускации для свежей установки.

    AWG 3.1 — Header Protection, S≥12, H1–H4 = 1–4, padding/trailers/cookies.
    AWG 2.0 — как приложение AmneziaVPN: маленький junk, случайные S3/S4 и H-диапазоны.
    Legacy — одиночные H без S3/S4.
    """
    if with_s34 is True:
        profile = "awg2"
    elif with_s34 is False:
        profile = "legacy"
    else:
        profile = str(with_s34)

    if profile == "awg31":
        from app.services.awg_masking_apply import generate_amnezia_31_params

        gen = generate_amnezia_31_params()
        return {
            "$JUNK_PACKET_COUNT": gen["Jc"],
            "$JUNK_PACKET_MIN_SIZE": gen["Jmin"],
            "$JUNK_PACKET_MAX_SIZE": gen["Jmax"],
            "$INIT_PACKET_JUNK_SIZE": gen["S1"],
            "$RESPONSE_PACKET_JUNK_SIZE": gen["S2"],
            "$COOKIE_REPLY_PACKET_JUNK_SIZE": gen["S3"],
            "$TRANSPORT_PACKET_JUNK_SIZE": gen["S4"],
            "$INIT_PACKET_MAGIC_HEADER": gen["H1"],
            "$RESPONSE_PACKET_MAGIC_HEADER": gen["H2"],
            "$UNDERLOAD_PACKET_MAGIC_HEADER": gen["H3"],
            "$TRANSPORT_PACKET_MAGIC_HEADER": gen["H4"],
            "$CONTENT_PADDING_ADDITION": gen["ContentPaddingAddition"],
            "$RANDOM_TRAILERS": gen["RandomTrailers"],
            "$DISABLE_COOKIES": gen["DisableCookies"],
        }

    if profile == "awg2":
        from app.services.awg_masking_apply import generate_amnezia_app_params

        gen = generate_amnezia_app_params()
        return {
            "$JUNK_PACKET_COUNT": gen["Jc"],
            "$JUNK_PACKET_MIN_SIZE": gen["Jmin"],
            "$JUNK_PACKET_MAX_SIZE": gen["Jmax"],
            "$INIT_PACKET_JUNK_SIZE": gen["S1"],
            "$RESPONSE_PACKET_JUNK_SIZE": gen["S2"],
            "$COOKIE_REPLY_PACKET_JUNK_SIZE": gen["S3"],
            "$TRANSPORT_PACKET_JUNK_SIZE": gen["S4"],
            "$INIT_PACKET_MAGIC_HEADER": gen["H1"],
            "$RESPONSE_PACKET_MAGIC_HEADER": gen["H2"],
            "$UNDERLOAD_PACKET_MAGIC_HEADER": gen["H3"],
            "$TRANSPORT_PACKET_MAGIC_HEADER": gen["H4"],
        }

    headers = set()
    while len(headers) < 4:
        headers.add(_secrets.randbelow(2_147_483_640) + 5)
    h1, h2, h3, h4 = list(headers)
    s1 = _secrets.randbelow(136) + 15
    s2 = _secrets.randbelow(136) + 15
    while s1 + 56 == s2:
        s2 = _secrets.randbelow(136) + 15
    return {
        "$JUNK_PACKET_COUNT": str(_secrets.randbelow(2) + 3),
        "$JUNK_PACKET_MIN_SIZE": "64",
        "$JUNK_PACKET_MAX_SIZE": "500",
        "$INIT_PACKET_JUNK_SIZE": str(s1),
        "$RESPONSE_PACKET_JUNK_SIZE": str(s2),
        "$COOKIE_REPLY_PACKET_JUNK_SIZE": "0",
        "$TRANSPORT_PACKET_JUNK_SIZE": "0",
        "$INIT_PACKET_MAGIC_HEADER": str(h1),
        "$RESPONSE_PACKET_MAGIC_HEADER": str(h2),
        "$UNDERLOAD_PACKET_MAGIC_HEADER": str(h3),
        "$TRANSPORT_PACKET_MAGIC_HEADER": str(h4),
    }


def _build_vars(host: str, port: int, cfg: dict) -> dict[str, str]:
    vars_map = base_vars(host, cfg["container"], cfg["folder"])
    vars_map["$AWG_SERVER_PORT"] = str(port)
    vars_map["$AWG_SUBNET_IP"] = cfg.get("subnet_ip") or DEFAULT_SUBNET_IP
    vars_map["$WIREGUARD_SUBNET_CIDR"] = DEFAULT_CIDR
    vars_map.update(_awg_params(cfg.get("profile") or ("awg2" if cfg.get("with_s34") else "legacy")))
    return vars_map


def _ensure_docker(ssh, host: str, container: str, folder: str) -> None:
    script = replace_vars(load_script("shared", "install_docker.sh"), base_vars(host, container, folder))
    result = run_script(ssh, script, timeout=300)
    if result.exit_code != 0 or "command not found" in result.stdout.lower():
        raise AwgInstallError("Docker не установлен и автоматическая установка не удалась.")
    if not docker_available(ssh):
        raise AwgInstallError("Docker недоступен после установки.")


def _prepare_host(ssh, vars_map: dict[str, str]) -> None:
    script = replace_vars(load_script("shared", "prepare_host.sh"), vars_map)
    result = run_script(ssh, script, timeout=60)
    if result.exit_code != 0:
        raise AwgInstallError(f"Подготовка хоста не удалась: {result.stderr.strip() or result.stdout.strip()}")


def _upload_dockerfile(ssh, cfg: dict, vars_map: dict[str, str]) -> None:
    dockerfile = load_script(cfg["scripts"], "Dockerfile")
    write_host_file(ssh, f"{cfg['folder']}/Dockerfile", dockerfile, mode="700")


def _build_image(ssh, vars_map: dict[str, str]) -> None:
    script = replace_vars(load_script("shared", "build_container.sh"), vars_map)
    result = run_script(ssh, script, timeout=600)
    combined = f"{result.stdout}\n{result.stderr}"
    if "pull rate limit" in combined.lower():
        raise AwgInstallError("Docker Hub ограничил скачивание образов. Повтори позже.")
    if result.exit_code != 0:
        raise AwgInstallError(f"Сборка образа не удалась: {combined.strip()[-500:]}")


def _run_container(ssh, cfg: dict, vars_map: dict[str, str]) -> None:
    script = replace_vars(load_script(cfg["scripts"], "run_container.sh"), vars_map)
    result = run_script(ssh, script, timeout=120)
    combined = f"{result.stdout}\n{result.stderr}"
    if "address already in use" in combined or "is already in use by container" in combined:
        raise AwgInstallError("Порт уже занят другим контейнером.")
    if result.exit_code != 0:
        raise AwgInstallError(f"Запуск контейнера не удался: {combined.strip()[-500:]}")


def _configure_container(ssh, cfg: dict, vars_map: dict[str, str]) -> None:
    script = replace_vars(load_script(cfg["scripts"], "configure_container.sh"), vars_map)
    result = run_container_script(ssh, cfg["container"], script, timeout=120)
    if result.exit_code != 0:
        raise AwgInstallError(f"Настройка внутри контейнера не удалась: {result.stderr.strip() or result.stdout.strip()}")


def _startup_container(ssh, cfg: dict, vars_map: dict[str, str]) -> None:
    start_script = replace_vars(load_script(cfg["scripts"], "start.sh"), vars_map)
    container = cfg["container"]
    b64_path = f"/tmp/utmka_{cfg['store_key']}_start.sh"
    write_host_file(ssh, b64_path, start_script, mode="755")
    copy_cmd = (
        f"sudo docker cp {shlex.quote(b64_path)} {shlex.quote(container)}:/opt/amnezia/start.sh "
        f"&& sudo docker exec {shlex.quote(container)} chmod a+x /opt/amnezia/start.sh "
        f"&& sudo docker exec -d {shlex.quote(container)} /opt/amnezia/start.sh "
        f"&& sudo rm -f {shlex.quote(b64_path)}"
    )
    result = ssh_exec.run(ssh, copy_cmd, timeout=60)
    if result.exit_code != 0:
        raise AwgInstallError(f"Не удалось запустить интерфейс в контейнере: {result.stderr.strip()}")


def _verify_running(ssh, container: str) -> None:
    status = ssh_exec.run(
        ssh,
        f"docker inspect -f '{{{{.State.Status}}}}' {shlex.quote(container)} 2>/dev/null || true",
    ).stdout.strip()
    if status != "running":
        raise AwgInstallError(f"Контейнер {container} не перешёл в состояние running.")


def _verify_awg_iface(ssh, container: str, port: int) -> None:
    """awg-quick в start.sh раньше глотался в подshell — контейнер жив, awg0 нет."""
    quoted = shlex.quote(container)
    listen = ""
    for _ in range(8):
        listen = ssh_exec.run(
            ssh,
            f"docker exec {quoted} sh -c 'awg show awg0 listen-port 2>/dev/null || true'",
            timeout=20,
        ).stdout.strip()
        if listen.isdigit():
            break
        ssh_exec.run(ssh, "sleep 1", timeout=5)
    if not listen.isdigit():
        raise AwgInstallError(
            "Контейнер 3.1 запущен, но интерфейс awg0 не поднялся. "
            "Проверь логи: docker logs amnezia-awg31"
        )
    if int(listen) != int(port):
        raise AwgInstallError(
            f"awg0 слушает UDP {listen}, а ставили {port}. Переустанови протокол."
        )


def _register(server_id: str, record: dict, cfg: dict, port: int) -> None:
    container = cfg["container"]
    names = list(record.get("container_names") or [])
    if container not in names:
        names.append(container)
    protocols = dict(record.get("installed_protocols") or {})
    protocols[cfg["store_key"]] = {"port": port, "container": container}
    runtime: dict = {
        "container_names": names,
        "installed_protocols": protocols,
    }
    if cfg["store_key"] in ("awg2", "awg_legacy"):
        runtime["awg2_imported"] = True
        runtime["vpn_port"] = port
    elif not record.get("vpn_port"):
        runtime["vpn_port"] = port
    server_store.update_runtime(server_id, **runtime)


def _maybe_attach_awg31_cascade(server_id: str) -> str:
    """Если вход уже в каскаде 2.0 — сразу поднять ногу 3.1, не ломая 2.0."""
    try:
        from app.services.cascade_apply import apply_cascade
        from app.services.cascade_store import cascade_store
    except Exception:  # noqa: BLE001
        return ""
    link = cascade_store.get_link(server_id)
    if not link or (link.get("state") or "") != "active":
        return ""
    try:
        result = apply_cascade(server_id, ["awg31"])
        if result.ok:
            return " Каскад 3.1 включён рядом с 2.0: интернет ключей 3.1 идёт через выход."
    except Exception as exc:  # noqa: BLE001
        return (
            f" Каскад 2.0 не тронут. 3.1 не удалось добавить автоматически ({exc}). "
            "Открой вкладку «Каскад» и добавь 3.1 вручную."
        )
    return ""


def _rollback(ssh, container: str) -> None:
    ssh_exec.run(ssh, f"docker rm -f {shlex.quote(container)} 2>/dev/null || true", timeout=60)
    ssh_exec.run(ssh, f"docker rmi {shlex.quote(container)} 2>/dev/null || true", timeout=60)
