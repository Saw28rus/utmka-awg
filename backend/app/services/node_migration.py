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
# Полная миграция: детект co-located VPN-входа (панель + AWG/Xray на одном узле)
# --------------------------------------------------------------------------- #


def _detect_colocated_entry(exclude_ids: set[str]) -> Optional[dict]:
    """Найти серверную запись узла, на котором ФИЗИЧЕСКИ стоит ЭТА панель и есть VPN.

    Это и есть «вход каскада, совмещённый с панелью» — его VPN (AWG/Xray), ключи и
    роль входа надо перенести на новый сервер вместе с панелью, иначе после флипа
    DNS вход останется на старом (заблокированном) сервере.

    Детект (без локального IP, чтобы работать и за NAT/в контейнере):
      1) запись с активным panel_ssl (там живёт домен панели) и установленным VPN;
      2) иначе — первая VPN-запись, на которой по SSH видно контейнеры панели.
    Возвращает серверную запись или None (тогда миграция = только панель, как раньше).
    """
    from app.services.panel_role import is_panel_node
    from app.services.server_store import server_store

    vpn_records: list[dict] = []
    for rec in server_store.list_records():
        sid = rec.get("id")
        if not sid or sid in exclude_ids:
            continue
        has_vpn = bool(
            rec.get("awg2_imported")
            or (rec.get("installed_protocols") or {}).get("awg2")
            or server_store.has_xray(rec)
        )
        if not has_vpn:
            continue
        vpn_records.append(rec)
        ssl = rec.get("panel_ssl") or {}
        if ssl.get("status") == "active":
            return rec

    for rec in vpn_records:
        try:
            if is_panel_node(rec["id"]):
                return rec
        except Exception:  # noqa: BLE001
            continue
    return None


def _entry_protocols(rec: dict) -> dict:
    """Какие VPN-протоколы переносить с co-located входа + их параметры."""
    from app.services.server_store import server_store

    protocols: dict = {}
    installed = rec.get("installed_protocols") or {}
    if rec.get("awg2_imported") or installed.get("awg2"):
        awg = installed.get("awg2") or {}
        protocols["awg2"] = {"port": awg.get("port") or rec.get("vpn_port")}
    if server_store.has_xray(rec):
        xr = installed.get("xray") or {}
        protocols["xray"] = {
            "port": xr.get("port") or 443,
            "reserved_443": bool(xr.get("reserved_443")),
            "transport": xr.get("transport") or "tcp",
        }
    return protocols


def _entry_cascade_info(entry_id: str) -> dict:
    """Роль входа в каскадах (AWG и/или Xray), чтобы переподнять их на новом IP."""
    info: dict = {"awg_exit_id": None, "xray": False}
    try:
        from app.services.cascade_store import cascade_store

        link = cascade_store.get_link(entry_id)
        if link and link.get("exit_server_id"):
            info["awg_exit_id"] = link.get("exit_server_id")
    except Exception:  # noqa: BLE001
        pass
    try:
        from app.services.xray_cascade import _desired_up
        from app.services.xray_cascade_store import xray_cascade_store

        xlink = xray_cascade_store.get_link(entry_id)
        if xlink and xlink.get("exit_host") and _desired_up(xlink):
            info["xray"] = True
    except Exception:  # noqa: BLE001
        pass
    return info


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
        # RAM (+swap) ≥ ~1 ГБ: на меньшем установка стека часто зависает по OOM, и
        # узел становится недоступен по SSH прямо во время провижининга.
        mem_kb = ssh_exec.run(ssh, "awk '/MemTotal/{print $2}' /proc/meminfo", timeout=15).stdout.strip()
        swap_kb = ssh_exec.run(ssh, "awk '/SwapTotal/{print $2}' /proc/meminfo", timeout=15).stdout.strip()
        try:
            total_mb = (int(mem_kb or 0) + int(swap_kb or 0)) // 1024
        except ValueError:
            total_mb = 0
        if total_mb and total_mb < 900:
            raise NodeMigrationError(
                f"Мало оперативной памяти на новом сервере (~{total_mb} МБ с учётом swap). "
                "Нужно ≥ 1 ГБ (или добавьте swap) — иначе установка зависнет по OOM."
            )
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

    # --- Полная миграция: переносим ещё и VPN co-located входа -------------- #
    _preflight_colocated_vpn(exclude_target_id=source_server_id)

    return NM.get_public() or {}


def _preflight_colocated_vpn(*, exclude_target_id: Optional[str]) -> None:
    """Определить co-located VPN-вход и проверить, что его можно перенести 1:1.

    Записывает в запись миграции: full_migration, entry_server_id, entry_protocols,
    cascade-роль. При нехватке слепка ключей (и недоступном старом входе) — warn в
    шаги, но не блокируем (оператор увидит и решит). На активации проверим строго.
    """
    from app.services.protocol_backup import latest_snapshot

    exclude = {exclude_target_id} if exclude_target_id else set()
    entry = _detect_colocated_entry(exclude)
    if not entry:
        NM.update(full_migration=False)
        NM.add_step(
            "vpn_detect",
            "ok",
            "VPN-входа на узле панели не найдено — переносим только панель.",
        )
        return

    entry_id = entry["id"]
    protocols = _entry_protocols(entry)
    cascade = _entry_cascade_info(entry_id)
    NM.update(
        full_migration=True,
        entry_server_id=entry_id,
        entry_server_name=entry.get("name"),
        entry_protocols=protocols,
        cascade_awg_exit_id=cascade.get("awg_exit_id"),
        cascade_xray=cascade.get("xray"),
    )

    proto_list = ", ".join(p.upper() for p in protocols) or "—"
    NM.add_step(
        "vpn_detect",
        "ok",
        f"Найден совмещённый вход «{entry.get('name')}»: {proto_list}. Перенесём VPN, ключи и роль входа.",
    )

    # Доступность слепков ключей (основа переноса без перевыпуска конфигов).
    missing: list[str] = []
    for proto in protocols:
        snap_proto = "awg2" if proto == "awg2" else proto
        if not latest_snapshot(entry_id, snap_proto):
            missing.append(proto.upper())
    if missing:
        NM.add_step(
            "vpn_snapshot",
            "warn",
            "Нет готового слепка ключей для "
            + ", ".join(missing)
            + " — снимем вживую на этапе развёртывания (старый вход должен быть доступен по SSH).",
        )
    else:
        NM.add_step("vpn_snapshot", "ok", "Слепки ключей входа на месте.")


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

        # 6. Полная миграция: развернуть VPN (AWG/Xray) из слепков ключей на новом
        #    VPS — те же серверные ключи/peers/Reality → клиентам конфиги не менять.
        if rec.get("full_migration") and rec.get("entry_server_id"):
            _provision_vpn(ssh, NM.get() or rec)

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


def _grab_blob(entry_id: str, proto: str) -> Optional[str]:
    """Слепок ключей протокола: live (старый вход жив) или последний сохранённый."""
    from app.services.protocol_backup import (
        get_snapshot_blob,
        latest_snapshot,
        snapshot_protocol,
    )

    snap_id: Optional[str] = None
    try:
        meta_snap = snapshot_protocol(entry_id, proto)
        snap_id = meta_snap.get("id")
    except Exception:  # noqa: BLE001
        last = latest_snapshot(entry_id, proto)
        snap_id = last.get("id") if last else None
    return get_snapshot_blob(entry_id, proto, snap_id)


def _provision_vpn(ssh: paramiko.SSHClient, rec: dict) -> None:
    """Развернуть VPN co-located входа на новом VPS (ssh подключён к новому серверу).

    Снимаем слепок ключей со СТАРОГО входа (он доступен по SSH) и разворачиваем 1:1
    на новом: те же server keys / peers / Reality public key / short_id / uuid.
    Старый вход не трогаем — при сбое чистим только новый VPS (fail-closed).
    """
    entry_id = rec.get("entry_server_id")
    protocols = rec.get("entry_protocols") or {}
    public_ip = rec.get("new_public_ip") or rec.get("new_host")
    restored: dict = {}

    # --- AWG2 --------------------------------------------------------------- #
    if "awg2" in protocols:
        from app.services.awg_restore import (
            AwgRestoreError,
            parse_snapshot_blob,
            restore_awg_entry,
            rollback_new_vps,
        )

        NM.add_step("vpn_awg", "running", "переношу AmneziaWG (ключи + peers)")
        blob = _grab_blob(entry_id, "awg2")
        if not blob:
            NM.add_step("vpn_awg", "failed", "нет слепка ключей AWG")
            raise NodeMigrationError("Нет слепка ключей AmneziaWG-входа — перенос невозможен.")
        try:
            meta = parse_snapshot_blob(blob)
            result = restore_awg_entry(ssh, public_ip, blob, meta)
        except AwgRestoreError as exc:
            try:
                rollback_new_vps(ssh)
            except Exception:  # noqa: BLE001
                pass
            NM.add_step("vpn_awg", "failed", str(exc))
            raise NodeMigrationError(f"AmneziaWG не развернулся на новом сервере: {exc}") from exc
        restored["awg2"] = {"port": result["port"]}
        NM.add_step(
            "vpn_awg",
            "ok",
            f"AmneziaWG поднят на {public_ip}:{result['port']}, peers {result['peers_count']}",
        )

    # --- Xray (VLESS-Reality) ---------------------------------------------- #
    if "xray" in protocols:
        from app.services.xray_restore import (
            XrayRestoreError,
            parse_xray_snapshot_blob,
            restore_xray_entry,
            rollback_xray_vps,
        )

        xr = protocols["xray"]
        NM.add_step("vpn_xray", "running", "переношу Xray (Reality-ключи + конфиг каскада)")
        blob = _grab_blob(entry_id, "xray")
        if not blob:
            NM.add_step("vpn_xray", "failed", "нет слепка ключей Xray")
            raise NodeMigrationError("Нет слепка ключей Xray-входа — перенос невозможен.")
        try:
            meta = parse_xray_snapshot_blob(blob)
            result = restore_xray_entry(
                ssh,
                public_ip,
                blob,
                meta,
                reserved=bool(xr.get("reserved_443")),
                connect_port=int(xr.get("port") or 443),
                transport=str(xr.get("transport") or "tcp"),
            )
        except XrayRestoreError as exc:
            try:
                rollback_xray_vps(ssh)
            except Exception:  # noqa: BLE001
                pass
            # Если AWG в этом же шаге уже подняли — тоже снести, чтобы новый VPS
            # остался чистым (иначе повторный provision упрётся в «сервер не чистый»).
            if "awg2" in restored:
                try:
                    from app.services.awg_restore import rollback_new_vps

                    rollback_new_vps(ssh)
                except Exception:  # noqa: BLE001
                    pass
            NM.add_step("vpn_xray", "failed", str(exc))
            raise NodeMigrationError(f"Xray не развернулся на новом сервере: {exc}") from exc
        restored["xray"] = {
            "port": result["port"],
            "reserved_443": bool(result.get("reserved")),
            "transport": str(xr.get("transport") or "tcp"),
        }
        NM.add_step(
            "vpn_xray",
            "ok",
            f"Xray поднят (Reality public key совпал со слепком), порт {result['port']}.",
        )

    NM.update(restored_vpn=restored)


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
# Полная миграция: своп роли VPN-входа + re-apply каскада + SSL (на active панели)
# --------------------------------------------------------------------------- #

AWG2_CONTAINER = "amnezia-awg2"
XRAY_CONTAINER = "amnezia-xray"


def _snapshot_record(rec: dict) -> dict:
    """Снимок полей серверной записи для отката swap (как в entry_replacement)."""
    return {
        "host": rec.get("host"),
        "ssh_port": rec.get("ssh_port"),
        "ssh_username": rec.get("ssh_username"),
        "ssh_password_enc": rec.get("ssh_password_enc"),
        "ssh_key_enc": rec.get("ssh_key_enc"),
        "container_names": rec.get("container_names"),
        "installed_protocols": rec.get("installed_protocols"),
        "awg2_imported": rec.get("awg2_imported"),
        "vpn_port": rec.get("vpn_port"),
        "status": rec.get("status"),
        "endpoint_host": rec.get("endpoint_host"),
        "panel_ssl": rec.get("panel_ssl"),
        "chat_domain": rec.get("chat_domain"),
        "former_entry": rec.get("former_entry"),
    }


def _restore_record(server_id: str, snap: dict) -> None:
    from app.services.server_store import server_store

    server_store.update_runtime(server_id, **snap)


def _full_migration_swap(rec: dict) -> None:
    """Перенести роль VPN-входа на новый сервер (panel ещё active).

    1) SWAP: тот же server_id входа теперь смотрит на новый VPS (host/ssh/протоколы);
    2) старый сервер делаем одиночным «бывший вход» (переиспользуем запись запасного);
    3) re-apply каскада (AWG + Xray) на новом входе;
    4) best-effort переподнятие SSL/чат-домена на новом сервере.

    Меняет ЛОКАЛЬНЫЕ стораджи (server_store, cascade) — они уедут на новый узел при
    финальной синхронизации в activate(). Fail-closed: при провале каскада откатываем
    записи на старый сервер и поднимаем ошибку (миграция → READY, ничего не потеряно).
    """
    from app.services.server_store import server_store

    raw = NM.get() or rec
    entry_id = raw.get("entry_server_id")
    target_id = raw.get("source_server_id")  # запись запасного сервера (новый VPS), если из панели
    restored = raw.get("restored_vpn") or {}

    old = server_store.get_record(entry_id)
    if not old:
        raise NodeMigrationError("Запись VPN-входа исчезла из списка серверов.")

    entry_backup = _snapshot_record(old)
    target_backup = _snapshot_record(server_store.get_record(target_id)) if target_id else None
    NM.update(entry_swap_backup=entry_backup, target_swap_backup=target_backup)

    # --- 1. Собрать протоколы входа на новом VPS ---------------------------- #
    protocols = dict(old.get("installed_protocols") or {})
    names = list(old.get("container_names") or [])
    awg_port = None
    if "awg2" in restored:
        awg_port = restored["awg2"].get("port") or (protocols.get("awg2") or {}).get("port")
        protocols["awg2"] = {"port": awg_port, "container": AWG2_CONTAINER}
        if AWG2_CONTAINER not in names:
            names.append(AWG2_CONTAINER)
    if "xray" in restored:
        xr = restored["xray"]
        entry = {"port": xr.get("port") or 443, "container": XRAY_CONTAINER, "transport": xr.get("transport") or "tcp"}
        if xr.get("reserved_443"):
            entry["reserved_443"] = True
            from app.services.xray_install import XRAY_LOCAL_PORT

            entry["local_port"] = XRAY_LOCAL_PORT
        protocols["xray"] = entry
        if XRAY_CONTAINER not in names:
            names.append(XRAY_CONTAINER)

    # --- 2. SWAP записи входа на новый VPS ---------------------------------- #
    server_store.update_runtime(
        entry_id,
        host=raw["new_host"],
        ssh_port=raw.get("new_ssh_port", 22),
        ssh_username=raw.get("new_ssh_username", "root"),
        ssh_password_enc=raw.get("new_ssh_password_enc"),
        ssh_key_enc=raw.get("new_ssh_key_enc"),
        container_names=names,
        installed_protocols=protocols,
        awg2_imported="awg2" in protocols,
        vpn_port=awg_port or old.get("vpn_port"),
        status="online",
    )
    NM.add_step("vpn_swap", "ok", f"роль входа перенесена на {raw['new_host']}")

    # --- 3. Старый сервер → одиночный «бывший вход» ------------------------- #
    _demote_old_to_solo(entry_id, entry_backup, target_id)

    # --- 4. re-apply каскадов на новом входе (host-правила прибиты к IP) ----- #
    awg_exit = raw.get("cascade_awg_exit_id")
    if awg_exit:
        NM.add_step("cascade_awg", "running", "переподнимаю AmneziaWG-каскад на новом входе")
        try:
            from app.services.cascade import run_preflight
            from app.services.cascade_apply import apply_cascade

            pf = run_preflight(entry_id, awg_exit)
            if not getattr(pf, "ok", True):
                raise NodeMigrationError("Каскадный preflight (AWG) не пройден на новом входе.")
            apply_cascade(entry_id)
            NM.add_step("cascade_awg", "ok", "AmneziaWG-каскад поднят на новом входе")
        except Exception as exc:  # noqa: BLE001
            _rollback_full_swap(entry_id, entry_backup, target_id, target_backup)
            NM.add_step("cascade_awg", "failed", str(exc))
            raise NodeMigrationError(
                f"AmneziaWG-каскад не поднялся на новом входе: {exc}. Записи возвращены на старый сервер."
            ) from exc

    if raw.get("cascade_xray"):
        NM.add_step("cascade_xray", "running", "переподнимаю Xray-каскад на новом входе")
        try:
            from app.services.xray_cascade import reconcile_xray_cascade

            r = reconcile_xray_cascade(entry_id, heal=True)
            if r.get("ok"):
                NM.add_step("cascade_xray", "ok", "Xray-каскад активен на новом входе")
            else:
                NM.add_step(
                    "cascade_xray",
                    "warn",
                    "Xray-каскад пока не подтверждён — планировщик нового узла до-восстановит его автоматически.",
                )
        except Exception as exc:  # noqa: BLE001
            NM.add_step("cascade_xray", "warn", f"Xray-каскад: {exc} (досстановится автоматически)")

    # --- 5. SSL / чат-домен на новом сервере (best-effort) ------------------ #
    _reapply_host_ssl(entry_id, entry_backup)


def _demote_old_to_solo(entry_id: str, backup: dict, target_id: Optional[str]) -> None:
    """Старый (заменённый) сервер остаётся в панели одиночным с бейджем «бывший вход».

    Если новый VPS был запасной записью панели (target_id) — переиспользуем её под
    старый сервер. Иначе (ручной ввод) создаём новую одиночную запись.
    """
    from app.services.server_store import server_store

    if not backup.get("host"):
        return
    now = datetime.now(timezone.utc).isoformat()
    protocols = backup.get("installed_protocols") or {}
    runtime = {
        "host": backup.get("host"),
        "ssh_port": backup.get("ssh_port") or 22,
        "ssh_username": backup.get("ssh_username") or "root",
        "ssh_password_enc": backup.get("ssh_password_enc"),
        "ssh_key_enc": backup.get("ssh_key_enc"),
        "container_names": backup.get("container_names") or [],
        "installed_protocols": protocols,
        "awg2_imported": bool(protocols.get("awg2")),
        "vpn_port": backup.get("vpn_port"),
        "status": "offline",
        "endpoint_host": None,
        "former_entry": True,
        "former_entry_at": now,
    }
    if target_id and server_store.get_record(target_id):
        server_store.update_runtime(target_id, **runtime)
        try:
            from app.services.health_store import health_store
            from app.services.metrics_cache import metrics_cache
            from app.services.protocol_backup import forget_node

            metrics_cache.invalidate(target_id)
            forget_node(target_id)
            health_store.forget(target_id)
        except Exception:  # noqa: BLE001
            pass
        NM.add_step("old_solo", "ok", "старый сервер оставлен одиночным («бывший вход»)")
        return

    try:
        from app.core.crypto import decrypt
        from app.schemas.servers import ServerCreate

        created = server_store.create(
            ServerCreate(
                name=(server_store.get_record(entry_id) or {}).get("name") or "Бывший вход",
                host=backup["host"],
                ssh_port=backup.get("ssh_port") or 22,
                ssh_username=backup.get("ssh_username") or "root",
                ssh_password=decrypt(backup.get("ssh_password_enc")),
                ssh_key=decrypt(backup.get("ssh_key_enc")),
                awg2_detected=bool(protocols.get("awg2")),
                container_names=backup.get("container_names") or [],
            ),
            message="Бывший входной сервер (заменён миграцией)",
        )
        server_store.update_runtime(
            created.id,
            installed_protocols=protocols,
            awg2_imported=bool(protocols.get("awg2")),
            vpn_port=backup.get("vpn_port"),
            status="offline",
            former_entry=True,
            former_entry_at=now,
        )
        NM.add_step("old_solo", "ok", "старый сервер добавлен одиночным («бывший вход»)")
    except Exception as exc:  # noqa: BLE001
        NM.add_step("old_solo", "warn", f"не удалось сохранить старый сервер записью: {exc}")


def _rollback_full_swap(
    entry_id: str, entry_backup: dict, target_id: Optional[str], target_backup: Optional[dict]
) -> None:
    """Вернуть записи входа/запасного на старый сервер (откат swap при сбое каскада)."""
    try:
        from app.services.cascade_apply import rollback_cascade

        rollback_cascade(entry_id)
    except Exception:  # noqa: BLE001
        pass
    _restore_record(entry_id, entry_backup)
    if target_id and target_backup:
        _restore_record(target_id, target_backup)


def _reapply_host_ssl(entry_id: str, backup: dict) -> None:
    """Переподнять SSL/чат-домен панели на новом сервере его же доменом (best-effort).

    DNS уже указывает на новый IP (условие активации) → Let's Encrypt пройдёт.
    Если не получилось — не валим миграцию: панель доступна по http://<new_ip>:8080,
    оператор переподнимет SSL кнопкой «Panel SSL». Manual SSL (вне панели) не трогаем.
    """
    panel_ssl_state = backup.get("panel_ssl") or {}
    domain = (panel_ssl_state.get("domain") or "").strip()
    if panel_ssl_state.get("status") == "active" and domain:
        NM.add_step("ssl_panel", "running", f"переподнимаю HTTPS панели на {domain}")
        try:
            from app.services.panel_ssl import install_panel_ssl

            install_panel_ssl(entry_id, domain)
            NM.add_step("ssl_panel", "ok", f"HTTPS панели поднят на новом сервере ({domain})")
        except Exception as exc:  # noqa: BLE001
            NM.add_step(
                "ssl_panel",
                "warn",
                f"HTTPS не переподнялся автоматически ({exc}). После миграции нажмите «Panel SSL».",
            )

    chat_state = backup.get("chat_domain") or {}
    chat_dom = (chat_state.get("domain") or "").strip()
    if chat_state.get("status") == "active" and chat_dom:
        NM.add_step("ssl_chat", "running", f"переподнимаю чат-домен {chat_dom}")
        try:
            from app.services.chat_domain import install_chat_domain

            install_chat_domain(entry_id, chat_dom)
            NM.add_step("ssl_chat", "ok", f"чат-домен поднят на новом сервере ({chat_dom})")
        except Exception as exc:  # noqa: BLE001
            NM.add_step("ssl_chat", "warn", f"чат-домен не переподнялся автоматически ({exc}).")


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
    swap_done = False
    try:
        ssh = _connect_target(timeout=20)
    except Exception as exc:  # noqa: BLE001
        NM.set_status(store_mod.STATUS_READY, error=f"нет связи с новым сервером: {exc}")
        raise NodeMigrationError(f"Нет связи с новым сервером: {exc}") from exc

    try:
        # 0. Полная миграция: пока панель ЕЩЁ active — переносим роль VPN-входа на
        #    новый сервер (своп записи + re-apply каскада + SSL). Мутирующие
        #    серверные операции требуют active-роли, поэтому делаем ДО freeze.
        if rec.get("full_migration") and rec.get("entry_server_id"):
            _full_migration_swap(rec)
            swap_done = True

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

        done_msg = (
            "Миграция завершена. Панель и VPN-вход работают на новом сервере, "
            "клиентам конфиги менять не нужно. Старый сервер оставлен резервом — "
            "снесите его вручную, когда убедитесь, что всё работает."
            if rec.get("full_migration")
            else "Миграция завершена. Старый сервер оставлен как резерв — снесите его вручную, когда убедитесь, что всё работает."
        )
        NM.add_step("done", "ok", done_msg)
    except Exception as exc:  # noqa: BLE001
        # Откат свопа роли входа: если запись входа уже перевели на новый VPS (а сбой
        # случился на freeze/sync/flip) — вернуть записи на старый сервер, иначе вход
        # «повиснет» на новом узле, пока панель ещё на старом. Делаем ДО разморозки,
        # пока узел временно в standby (но _rollback использует только server_store).
        if swap_done:
            try:
                raw = NM.get() or {}
                _rollback_full_swap(
                    rec.get("entry_server_id"),
                    raw.get("entry_swap_backup") or {},
                    rec.get("source_server_id"),
                    raw.get("target_swap_backup"),
                )
                NM.add_step(
                    "vpn_swap",
                    "failed",
                    "активация прервалась — роль входа возвращена на старый сервер",
                )
            except Exception:  # noqa: BLE001
                pass
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


def recover_orphaned_on_startup() -> None:
    """Подхватить «осиротевшую» миграцию после рестарта панели.

    Тяжёлые шаги (provision/activate) выполняются в фоновом потоке. Если панель
    перезапустилась во время такого шага, поток убит, а запись осталась висеть в
    статусе provisioning/activating «без движений». Здесь мы переводим её в
    конечное состояние с понятной причиной, чтобы оператор мог отменить/повторить.
    """
    try:
        rec = NM.get()
    except Exception:  # noqa: BLE001
        return
    if not rec:
        return
    status = rec.get("status")
    if status == store_mod.STATUS_PROVISIONING:
        NM.add_step(
            "provision",
            "failed",
            "Прервано перезапуском панели — провижининг не завершён. Отмените и запустите заново.",
        )
        NM.set_status(
            store_mod.STATUS_FAILED,
            error="Провижининг прерван перезапуском панели. Запустите шаг заново.",
        )
    elif status == store_mod.STATUS_ACTIVATING:
        # Активация могла успеть заморозить ЭТОТ узел в standby — вернём active,
        # чтобы планировщик и управление узлами не остались выключенными.
        try:
            from app.services.panel_role import ROLE_ACTIVE, set_role

            set_role(ROLE_ACTIVE)
        except Exception:  # noqa: BLE001
            pass
        NM.add_step(
            "activate",
            "failed",
            "Прервано перезапуском панели во время активации. Узел возвращён в active.",
        )
        NM.set_status(
            store_mod.STATUS_READY,
            error="Активация прервана перезапуском панели; узел возвращён в active. "
            "Проверьте состояние и активируйте заново.",
        )


def abort() -> dict:
    rec = NM.get()
    if not rec:
        return {}
    if rec.get("status") == store_mod.STATUS_ACTIVE:
        raise NodeMigrationError("Миграция уже завершена — сбросить нельзя.")

    # Полная миграция: best-effort снести VPN-контейнеры, развёрнутые на новом VPS,
    # чтобы повторная попытка на тот же сервер не упёрлась в «сервер не чистый».
    if rec.get("full_migration") and rec.get("status") in {
        store_mod.STATUS_PROVISIONING,
        store_mod.STATUS_WAITING_DNS,
        store_mod.STATUS_READY,
        store_mod.STATUS_FAILED,
    }:
        try:
            from app.services.awg_restore import rollback_new_vps
            from app.services.xray_restore import rollback_xray_vps

            ssh = _connect_target(timeout=15)
            try:
                rollback_new_vps(ssh)
                rollback_xray_vps(ssh)
            finally:
                ssh.close()
        except Exception:  # noqa: BLE001
            pass

    NM.set_status(store_mod.STATUS_ABORTED)
    result = NM.get_public() or {}
    NM.delete()
    return result
