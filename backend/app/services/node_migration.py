"""Оркестрация «Полной миграции узла» (NODE_MIGRATION_PLAN.md).

Переносит ВЕСЬ стек панели (VPN + панель + БД + panel_data + секрет Fernet) со
старого сервера на новый. Старый сервер НЕ удаляется — остаётся «отработанным
топливом» (его можно снести вручную или вернуть сменой IP).

Машина состояний:
  draft → preflight → provisioning → waiting_dns → ready → activating → active
                  ↘ preflight_failed                                 ↘ failed

Anti-split-brain: новый узел поднимается в роли `standby` (планировщик выключен,
узлы не трогает). На активации старый узел замораживается (standby + стоп
планировщика), затем новый узел переводится в `active`.

Ключевой инвариант: PANEL_SECRET_KEY на новом узле = секрет этой панели, иначе
зашифрованные Fernet'ом блобы (SSH-креды, ключи клиентов) не расшифруются.
"""

from __future__ import annotations

import ipaddress
from datetime import datetime, timezone
from typing import Optional

import paramiko

from app.core.config import settings
from app.services import node_backup, panel_provision
from app.services import node_migration_store as store_mod
from app.services.node_migration_store import node_migration_store as NM
from app.ssh import exec as ssh_exec

logger_name = "utmka.node_migration"


class NodeMigrationError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Вспомогательные
# --------------------------------------------------------------------------- #


def _connect_target(timeout: int = 20) -> paramiko.SSHClient:
    creds = NM.ssh_creds()
    if not creds:
        raise NodeMigrationError("Нет данных нового сервера для подключения.")
    if not creds.get("password") and not creds.get("key"):
        raise NodeMigrationError("Не заданы SSH-креды нового сервера.")
    return ssh_exec.connect(
        host=creds["host"],
        port=creds["port"],
        username=creds["username"],
        password=creds.get("password"),
        key=creds.get("key"),
        timeout=timeout,
    )


def _is_valid_host(value: str) -> bool:
    value = (value or "").strip()
    if not value:
        return False
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        pass
    return all(c.isalnum() or c in ".-" for c in value) and "." in value


def _panel_domain() -> Optional[str]:
    """Домен текущей панели (для DNS-флипа). Берём из настроек/первого сервера."""
    try:
        from app.services.server_store import server_store

        for rec in server_store.list_records():
            dom = (rec.get("endpoint_host") or "").strip()
            if dom and not _is_pure_ip(dom):
                return dom
    except Exception:  # noqa: BLE001
        pass
    return None


def _is_pure_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value.strip())
        return True
    except ValueError:
        return False


# --------------------------------------------------------------------------- #
# Шаг 1: preflight нового VPS
# --------------------------------------------------------------------------- #


def preflight(
    *,
    source_server_id: Optional[str] = None,
    new_host: Optional[str] = None,
    ssh_port: int = 22,
    ssh_username: str = "root",
    ssh_password: Optional[str] = None,
    ssh_key: Optional[str] = None,
    expected_domain: Optional[str] = None,
) -> dict:
    from app.services.server_store import server_store

    if NM.has_open():
        cur = NM.get()
        if cur and cur.get("status") in {store_mod.STATUS_PROVISIONING, store_mod.STATUS_ACTIVATING}:
            raise NodeMigrationError("Миграция уже выполняется. Дождитесь завершения или отмените.")

    creds_enc: dict = {}
    source_name: Optional[str] = None
    host = (new_host or "").strip()
    port = ssh_port
    username = ssh_username or "root"

    if source_server_id:
        src = server_store.get_record(source_server_id)
        if not src:
            raise NodeMigrationError("Выбранный сервер не найден в панели.")
        host = src.get("host") or ""
        port = src.get("ssh_port") or 22
        username = src.get("ssh_username") or "root"
        source_name = src.get("name")
        creds_enc = {
            "ssh_password_enc": src.get("ssh_password_enc"),
            "ssh_key_enc": src.get("ssh_key_enc"),
        }

    if not _is_valid_host(host):
        raise NodeMigrationError("Укажите корректный IP/домен нового сервера.")

    # Подключение и базовые проверки чистоты/пригодности узла.
    ssh = ssh_exec.connect(
        host=host,
        port=port,
        username=username,
        password=ssh_password if not source_server_id else None,
        key=ssh_key if not source_server_id else None,
        timeout=15,
    ) if not source_server_id else None

    if ssh is None:
        # креды из записи сервера
        target = server_store.ssh_target(source_server_id)
        ssh = ssh_exec.connect(
            host=target.host,
            port=target.port,
            username=target.username,
            password=target.password,
            key=target.key,
            timeout=15,
        )

    public_ip: Optional[str] = None
    try:
        whoami = ssh_exec.run(ssh, "id -u", timeout=15).stdout.strip()
        if whoami != "0":
            raise NodeMigrationError("Нужен root-доступ на новом сервере (id -u != 0).")
        # Свободное место ≥ 3 ГБ (образы + сборка + данные).
        df = ssh_exec.run(ssh, "df -Pk / | awk 'NR==2 {print $4}'", timeout=15).stdout.strip()
        try:
            if df and int(df) < 3 * 1024 * 1024:
                raise NodeMigrationError(f"Мало места на диске нового сервера (~{int(df)//1024} МБ, нужно ≥ 3 ГБ).")
        except ValueError:
            pass
        public_ip = panel_provision.public_ip(ssh)
    finally:
        try:
            ssh.close()
        except Exception:  # noqa: BLE001
            pass

    if not public_ip:
        raise NodeMigrationError("Не удалось определить публичный IP нового сервера.")

    domain = (expected_domain or "").strip() or _panel_domain()

    # Перезаписываем черновик.
    NM.delete()
    NM.create_draft(
        new_host=host,
        new_ssh_port=port,
        new_ssh_username=username,
        new_ssh_password=ssh_password,
        new_ssh_key=ssh_key,
        expected_domain=domain,
        source_node_name=_panel_domain() or "эта панель",
        source_server_id=source_server_id,
        ssh_password_enc=creds_enc.get("ssh_password_enc"),
        ssh_key_enc=creds_enc.get("ssh_key_enc"),
    )
    NM.update(new_public_ip=public_ip)
    NM.set_status(store_mod.STATUS_PREFLIGHT)
    NM.add_step("preflight", "ok", f"root + диск ок, публичный IP {public_ip}")
    return NM.get_public() or {}


# --------------------------------------------------------------------------- #
# Шаг 2: provision (поднять стек + залить данные в standby)
# --------------------------------------------------------------------------- #


def provision() -> dict:
    rec = NM.get()
    if not rec:
        raise NodeMigrationError("Миграция не найдена. Сначала выполните preflight.")
    if rec.get("status") not in {store_mod.STATUS_PREFLIGHT, store_mod.STATUS_PREFLIGHT_FAILED, store_mod.STATUS_PROVISIONING}:
        raise NodeMigrationError("Provision доступен только после успешного preflight.")

    NM.set_status(store_mod.STATUS_PROVISIONING, error=None)
    try:
        ssh = _connect_target(timeout=20)
    except Exception as exc:  # noqa: BLE001
        NM.set_status(store_mod.STATUS_PREFLIGHT_FAILED, error=f"нет связи с новым сервером: {exc}")
        raise NodeMigrationError(f"Нет связи с новым сервером: {exc}") from exc

    try:
        # 1. Свежий стек панели (придёт active со своими секретами — это временно).
        NM.add_step("install", "running", "ставлю Docker и стек панели (может занять несколько минут)")
        panel_provision.install_panel(ssh)
        if not panel_provision.health_ok(ssh):
            raise NodeMigrationError("Свежая панель не поднялась (health не отвечает).")
        NM.add_step("install", "ok", "стек панели поднят")

        # 2. Перевести узел в standby ДО заливки данных (планировщик не стартует).
        panel_provision.set_remote_role(ssh, "standby")
        NM.add_step("standby", "ok", "новый узел в режиме ожидания")

        # 3. Подменить секрет Fernet на наш (иначе блобы не расшифруются).
        panel_provision.set_env_value(ssh, "PANEL_SECRET_KEY", settings.panel_secret_key)
        NM.add_step("secret", "ok", "PANEL_SECRET_KEY синхронизирован")

        # 4. Залить данные (БД + panel_data) со старого узла (источник = локально).
        NM.add_step("data", "running", "переношу базу и данные панели")
        pg_dump = node_backup.local_dump_postgres()
        data_tar = node_backup.local_dump_panel_data()
        node_backup.restore_postgres(ssh, pg_dump, stop_backend=True)
        node_backup.restore_panel_data(ssh, data_tar)
        NM.add_step("data", "ok", f"БД ({len(pg_dump)} Б) и panel_data ({len(data_tar)} Б) перенесены")

        # 5. Перезапустить с новым секретом и ролью standby.
        NM.add_step("recreate", "running", "перезапускаю стек с перенесёнными данными")
        panel_provision.compose_recreate(ssh)
        ok = panel_provision.health_ok(ssh)
        NM.add_step("recreate", "ok" if ok else "warn", "health ок" if ok else "health не подтверждён")

        NM.update(provision_ok=True, health_ok=ok)
        NM.set_status(store_mod.STATUS_WAITING_DNS)
        NM.add_step(
            "waiting_dns",
            "pending",
            f"Смените A-запись домена на {rec.get('new_public_ip')} и нажмите «Проверить DNS».",
        )
    except Exception as exc:  # noqa: BLE001
        NM.add_step("provision", "failed", str(exc))
        NM.set_status(store_mod.STATUS_FAILED, error=str(exc))
        raise NodeMigrationError(str(exc)) from exc
    finally:
        try:
            ssh.close()
        except Exception:  # noqa: BLE001
            pass
    return NM.get_public() or {}


# --------------------------------------------------------------------------- #
# Шаг 3: проверка DNS
# --------------------------------------------------------------------------- #


def check_dns() -> dict:
    from app.services.panel_ssl import _resolve_domain_ips

    rec = NM.get()
    if not rec:
        raise NodeMigrationError("Миграция не найдена.")

    domain = rec.get("expected_domain")
    new_ip = rec.get("new_public_ip")
    resolved: list[str] = []
    dns_ok = False
    if domain and new_ip:
        resolved = _resolve_domain_ips(domain)
        dns_ok = new_ip in resolved

    health_ok = False
    try:
        ssh = _connect_target(timeout=15)
        try:
            health_ok = panel_provision.health_ok(ssh, retries=2, delay=2)
        finally:
            ssh.close()
    except Exception:  # noqa: BLE001
        health_ok = False

    NM.update(
        dns_ok=dns_ok,
        dns_resolved_ips=resolved,
        dns_checked_at=datetime.now(timezone.utc).isoformat(),
        health_ok=health_ok,
    )
    if rec.get("status") in {store_mod.STATUS_WAITING_DNS, store_mod.STATUS_READY}:
        if dns_ok and health_ok:
            NM.set_status(store_mod.STATUS_READY)
        else:
            NM.set_status(store_mod.STATUS_WAITING_DNS)
    return NM.get_public() or {}


# --------------------------------------------------------------------------- #
# Шаг 4: активация (freeze источника → delta-sync → flip нового в active)
# --------------------------------------------------------------------------- #


def activate(*, force: bool = False) -> dict:
    from app.services.panel_role import ROLE_ACTIVE, ROLE_STANDBY, set_role

    rec = NM.get()
    if not rec:
        raise NodeMigrationError("Миграция не найдена.")
    if rec.get("status") not in {store_mod.STATUS_READY, store_mod.STATUS_WAITING_DNS}:
        raise NodeMigrationError("Активация доступна только после готовности нового узла.")
    if not force and not (rec.get("dns_ok") and rec.get("health_ok")):
        raise NodeMigrationError(
            "DNS ещё не указывает на новый сервер или его панель не отвечает. "
            "Дождитесь распространения DNS или активируйте принудительно."
        )

    NM.set_status(store_mod.STATUS_ACTIVATING, error=None)
    source_frozen = False
    try:
        ssh = _connect_target(timeout=20)
    except Exception as exc:  # noqa: BLE001
        NM.set_status(store_mod.STATUS_READY, error=f"нет связи с новым сервером: {exc}")
        raise NodeMigrationError(f"Нет связи с новым сервером: {exc}") from exc

    try:
        # 1. Заморозить ЭТОТ (старый) узел: standby. Фоновые джобы планировщика
        #    сами приостанавливаются по роли (_suspended), стоп самого
        #    планировщика не делаем (мы внутри worker-треда без event loop).
        set_role(ROLE_STANDBY)
        source_frozen = True
        NM.add_step("freeze", "ok", "старая панель переведена в режим ожидания")

        # 2. Отметить миграцию активной ДО финальной синхронизации (попадёт в дамп).
        NM.set_status(store_mod.STATUS_ACTIVE)

        # 3. Финальная дельта-синхронизация (догнать изменения с момента provision).
        NM.add_step("sync", "running", "финальная синхронизация данных")
        pg_dump = node_backup.local_dump_postgres()
        data_tar = node_backup.local_dump_panel_data()
        node_backup.restore_postgres(ssh, pg_dump, stop_backend=True)
        node_backup.restore_panel_data(ssh, data_tar)
        NM.add_step("sync", "ok", "данные синхронизированы")

        # 4. Перевести новый узел в active и перезапустить (его планировщик стартует,
        #    re-apply каскадов произойдёт автоматически по расписанию).
        panel_provision.set_remote_role(ssh, "active")
        panel_provision.compose_recreate(ssh)
        ok = panel_provision.health_ok(ssh)
        NM.add_step("flip", "ok" if ok else "warn", "новый узел активен" if ok else "активирован, health не подтверждён")

        NM.add_step(
            "done",
            "ok",
            "Миграция завершена. Старый сервер оставлен как резерв — снесите его вручную, когда убедитесь, что всё работает.",
        )
    except Exception as exc:  # noqa: BLE001
        # Откат заморозки: возвращаем активность старой панели (джобы возобновятся).
        if source_frozen:
            try:
                set_role(ROLE_ACTIVE)
            except Exception:  # noqa: BLE001
                pass
        NM.add_step("activate", "failed", str(exc))
        NM.set_status(store_mod.STATUS_READY, error=str(exc))
        raise NodeMigrationError(str(exc)) from exc
    finally:
        try:
            ssh.close()
        except Exception:  # noqa: BLE001
            pass
    return NM.get_public() or {}


# --------------------------------------------------------------------------- #
# Отмена / сброс
# --------------------------------------------------------------------------- #


def abort() -> dict:
    rec = NM.get()
    if not rec:
        return {}
    if rec.get("status") == store_mod.STATUS_ACTIVE:
        raise NodeMigrationError("Миграция уже завершена — сбросить нельзя.")
    NM.set_status(store_mod.STATUS_ABORTED)
    result = NM.get_public() or {}
    NM.delete()
    return result
