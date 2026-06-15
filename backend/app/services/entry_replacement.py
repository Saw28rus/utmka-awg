"""Оркестрация «Замены Входа» (RES2a) — disaster recovery входа без перевыпуска
клиентских конфигов.

Поток (см. _dev-docs/ENTRY_REPLACEMENT_PLAN.md):
  preflight  → проверка чистоты нового VPS (root/clean/port/public-ip)
  provision  → снять слепок старого входа + развернуть 1-в-1 на новом VPS
  waiting_dns→ оператор меняет A-запись домена у регистратора
  check_dns  → панель проверяет, что домен резолвится на новый IP
  activate   → swap записи сервера на новый VPS + re-apply каскада (fail-closed)

Главный инвариант: server keys / peers / маскировка / ListenPort переносятся
из снапшота → старые vpn:// и .conf продолжают работать. Меняются только IP
сервера и A-запись домена.
"""

from __future__ import annotations

import ipaddress
import shlex
from typing import Optional

from app.services import entry_replacement_store as store_mod
from app.services.entry_replacement_store import entry_replacement_store as ER
from app.ssh import exec as ssh_exec

AWG2_CONTAINER = "amnezia-awg2"


class EntryReplacementError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Вспомогательные
# --------------------------------------------------------------------------- #


def _connect_new(old_entry_id: str):
    creds = ER.ssh_creds(old_entry_id)
    if not creds:
        raise EntryReplacementError("Нет данных нового сервера для подключения.")
    if not creds.get("password") and not creds.get("key"):
        raise EntryReplacementError("Не заданы SSH-креды нового сервера.")
    return ssh_exec.connect(
        host=creds["host"],
        port=creds["port"],
        username=creds["username"],
        password=creds.get("password"),
        key=creds.get("key"),
        timeout=15,
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
    # hostname: буквы/цифры/точки/дефисы
    return all(c.isalnum() or c in ".-" for c in value) and "." in value


def _entry_cascade_exit(old_entry_id: str) -> Optional[str]:
    from app.services.cascade_store import cascade_store

    link = cascade_store.get_link(old_entry_id)
    if link and link.get("exit_server_id"):
        return link.get("exit_server_id")
    return None


def _container_running(ssh, container: str) -> bool:
    out = ssh_exec.run(
        ssh,
        f"sudo docker inspect -f '{{{{.State.Status}}}}' {shlex.quote(container)} 2>/dev/null || true",
        timeout=20,
    ).stdout.strip()
    return out == "running"


# --------------------------------------------------------------------------- #
# Шаг 1–2: preflight нового VPS
# --------------------------------------------------------------------------- #


def preflight(
    old_entry_id: str,
    *,
    new_host: str,
    ssh_port: int,
    ssh_username: str,
    ssh_password: Optional[str],
    ssh_key: Optional[str],
) -> dict:
    from app.services.amnezia_ssh import container_exists, docker_available, port_busy
    from app.services.panel_ssl import _server_public_ip
    from app.services.protocol_backup import latest_snapshot
    from app.services.server_store import server_store

    old = server_store.get_record(old_entry_id)
    if not old:
        raise EntryReplacementError("Заменяемый вход не найден.")

    # MVP: только чистый AWG2-вход. Xray на том же узле — фаза 2.
    if server_store.has_xray(old):
        raise EntryReplacementError(
            "На этом входе установлен Xray. Замена входа в MVP поддерживает только "
            "чистый AWG2-вход (миграция Xray/панели — отдельный сценарий)."
        )
    if not (old.get("awg2_imported") or (old.get("installed_protocols") or {}).get("awg2")):
        raise EntryReplacementError("На этом сервере нет AWG2 — заменять нечего.")

    if ER.has_open(old_entry_id):
        cur = ER.get(old_entry_id)
        # повторный preflight перетирает draft, но не активную провизию
        if cur and cur.get("status") in {store_mod.STATUS_PROVISIONING, store_mod.STATUS_ACTIVATING}:
            raise EntryReplacementError("Замена уже выполняется. Дождитесь завершения или отмените.")

    new_host = (new_host or "").strip()
    if not _is_valid_host(new_host):
        raise EntryReplacementError("Некорректный IP/hostname нового сервера.")
    if new_host == old.get("host"):
        raise EntryReplacementError("Это тот же сервер, что и старый вход.")
    if bool(ssh_password) == bool(ssh_key):
        raise EntryReplacementError("Укажите ровно один способ авторизации: пароль ИЛИ ключ.")

    # Не пересекаемся с другими узлами панели.
    for rec in server_store.list_records():
        if rec.get("id") == old_entry_id:
            continue
        if rec.get("host") == new_host:
            raise EntryReplacementError("Этот IP уже используется другим узлом панели.")

    exit_id = _entry_cascade_exit(old_entry_id)
    if exit_id:
        exit_rec = server_store.get_record(exit_id)
        if exit_rec and exit_rec.get("host") == new_host:
            raise EntryReplacementError("Вход и выход не могут быть одним сервером.")

    expected_domain = (old.get("endpoint_host") or "").strip() or None
    old_port = (old.get("installed_protocols") or {}).get("awg2", {}).get("port") or old.get("vpn_port")

    ER.create_draft(
        old_entry_id,
        new_host=new_host,
        new_ssh_port=ssh_port,
        new_ssh_username=ssh_username,
        new_ssh_password=ssh_password,
        new_ssh_key=ssh_key,
        expected_domain=expected_domain,
        port=old_port,
        cascade_exit_id=exit_id,
    )
    ER.set_status(old_entry_id, store_mod.STATUS_PREFLIGHT)

    blockers: list[str] = []
    warnings: list[str] = []

    # Слепок с ключами — основа всей фичи.
    snap = latest_snapshot(old_entry_id, "awg2")
    old_reachable = False
    tssh = None
    try:
        from app.services.amnezia_ssh import connect_target

        _r, _t, tssh = connect_target(old_entry_id)
        old_reachable = True
    except Exception:  # noqa: BLE001
        old_reachable = False
    finally:
        if tssh:
            tssh.close()

    if not snap and not old_reachable:
        blockers.append(
            "Нет слепка ключей старого входа, и старый сервер недоступен по SSH. "
            "Восстановить конфиги клиентов без перевыпуска невозможно."
        )

    if not expected_domain:
        warnings.append(
            "У входа не указан домен (endpoint). При смене IP клиентам в любом случае "
            "понадобятся новые конфиги — фича рассчитана на работу с доменом."
        )

    public_ip: Optional[str] = None
    try:
        ssh = _connect_new(old_entry_id)
    except Exception as exc:  # noqa: BLE001
        ER.add_step(old_entry_id, "ssh", "fail", str(exc))
        ER.set_status(old_entry_id, store_mod.STATUS_PREFLIGHT_FAILED, error=str(exc))
        return _result(old_entry_id, blockers=[f"SSH недоступен: {exc}"], warnings=warnings)

    try:
        ER.add_step(old_entry_id, "ssh", "ok", "SSH-подключение установлено")

        whoami = ssh_exec.run(ssh, "id -u 2>/dev/null || echo 1", timeout=15).stdout.strip()
        if whoami != "0":
            blockers.append("Нужен root-доступ на новом сервере (для docker/iptables).")
        ER.add_step(old_entry_id, "root", "ok" if whoami == "0" else "fail")

        public_ip = _server_public_ip(ssh)
        if not public_ip:
            blockers.append("Не удалось определить публичный IPv4 нового сервера.")
        else:
            try:
                if ipaddress.ip_address(public_ip).is_private:
                    blockers.append(f"Публичный IP сервера приватный ({public_ip}) — за NAT работать не будет.")
            except ValueError:
                pass
        ER.add_step(old_entry_id, "public_ip", "ok" if public_ip else "fail", public_ip)

        # Чистота: чужой AWG/WG ломать нельзя.
        dirty = []
        if container_exists(ssh, AWG2_CONTAINER) or container_exists(ssh, "amnezia-awg"):
            dirty.append("уже есть контейнер AmneziaWG")
        conf_exists = ssh_exec.run(
            ssh, "test -f /opt/amnezia/awg/awg0.conf && echo yes || echo no", timeout=15
        ).stdout.strip()
        if conf_exists == "yes":
            dirty.append("найден /opt/amnezia/awg/awg0.conf")
        if dirty:
            blockers.append("Сервер не чистый: " + ", ".join(dirty) + ". Возьмите чистый VPS.")
        ER.add_step(old_entry_id, "clean", "ok" if not dirty else "fail", "; ".join(dirty) or None)

        # Порт ListenPort должен быть свободен.
        cur = ER.get(old_entry_id) or {}
        port = cur.get("port")
        if port:
            busy = port_busy(ssh, int(port), proto="udp")
            if busy:
                blockers.append(f"UDP-порт {port} занят на новом сервере.")
            ER.add_step(old_entry_id, "port", "fail" if busy else "ok", str(port))

        if not docker_available(ssh):
            warnings.append("Docker не установлен — панель поставит его на этапе провижна.")
        ER.add_step(old_entry_id, "docker", "ok")
    finally:
        ssh.close()

    ER.update(old_entry_id, new_public_ip=public_ip)
    if blockers:
        ER.set_status(old_entry_id, store_mod.STATUS_PREFLIGHT_FAILED, error="; ".join(blockers))
    # если блокеров нет — остаёмся в preflight (готовы к provision)
    return _result(old_entry_id, blockers=blockers, warnings=warnings)


def _result(old_entry_id: str, *, blockers: list[str], warnings: list[str]) -> dict:
    rec = ER.get_public(old_entry_id) or {}
    return {
        "ok": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "replacement": rec,
    }


# --------------------------------------------------------------------------- #
# Шаг 3–4: provision (снять слепок + развернуть на новом VPS)
# --------------------------------------------------------------------------- #


def provision(old_entry_id: str) -> dict:
    from app.services.awg_restore import (
        AwgRestoreError,
        parse_snapshot_blob,
        restore_awg_entry,
        rollback_new_vps,
    )
    from app.services.panel_ssl import _server_public_ip
    from app.services.protocol_backup import (
        get_snapshot_blob,
        latest_snapshot,
        snapshot_protocol,
    )

    rec = ER.get(old_entry_id)
    if not rec:
        raise EntryReplacementError("Замена не найдена. Сначала выполните preflight.")
    if rec.get("status") not in {store_mod.STATUS_PREFLIGHT, store_mod.STATUS_FAILED}:
        raise EntryReplacementError(
            f"Провижн недоступен в статусе «{rec.get('status')}». Нужен успешный preflight."
        )

    ER.set_status(old_entry_id, store_mod.STATUS_PROVISIONING, error=None)
    ER.add_step(old_entry_id, "snapshot", "running")

    # 3a. Слепок: live (старый жив) или последний сохранённый.
    snapshot_source = "live"
    snapshot_id: Optional[str] = None
    try:
        meta_snap = snapshot_protocol(old_entry_id, "awg2")
        snapshot_id = meta_snap.get("id")
    except Exception:  # noqa: BLE001
        snapshot_source = "stored"
        last = latest_snapshot(old_entry_id, "awg2")
        snapshot_id = last.get("id") if last else None

    blob = get_snapshot_blob(old_entry_id, "awg2", snapshot_id)
    if not blob:
        ER.add_step(old_entry_id, "snapshot", "fail", "нет слепка с ключами")
        ER.set_status(old_entry_id, store_mod.STATUS_FAILED, error="Нет слепка ключей входа.")
        raise EntryReplacementError(
            "Нет слепка ключей старого входа — восстановить без перевыпуска нельзя."
        )

    try:
        meta = parse_snapshot_blob(blob)
    except AwgRestoreError as exc:
        ER.add_step(old_entry_id, "snapshot", "fail", str(exc))
        ER.set_status(old_entry_id, store_mod.STATUS_FAILED, error=str(exc))
        raise EntryReplacementError(str(exc))

    ER.update(old_entry_id, snapshot_id=snapshot_id, snapshot_source=snapshot_source, port=meta.listen_port)
    ER.add_step(
        old_entry_id,
        "snapshot",
        "ok",
        f"{snapshot_source}: порт {meta.listen_port}, peers {meta.peers_count}",
    )

    # 4. Развернуть на новом VPS.
    ER.add_step(old_entry_id, "restore", "running")
    try:
        ssh = _connect_new(old_entry_id)
    except Exception as exc:  # noqa: BLE001
        ER.add_step(old_entry_id, "restore", "fail", str(exc))
        ER.set_status(old_entry_id, store_mod.STATUS_FAILED, error=str(exc))
        raise EntryReplacementError(f"SSH к новому серверу недоступен: {exc}")

    try:
        host = ER.get(old_entry_id)["new_host"]
        public_ip = _server_public_ip(ssh) or host
        try:
            result = restore_awg_entry(ssh, public_ip, blob, meta)
        except AwgRestoreError as exc:
            ER.add_step(old_entry_id, "restore", "fail", str(exc))
            rollback_new_vps(ssh)
            ER.add_step(old_entry_id, "rollback", "ok", "контейнер на новом VPS удалён")
            ER.set_status(old_entry_id, store_mod.STATUS_FAILED, error=str(exc))
            raise EntryReplacementError(f"Восстановление не удалось: {exc}. Новый VPS очищен, старый вход цел.")

        ER.update(
            old_entry_id,
            new_public_ip=public_ip,
            port=result["port"],
        )
        ER.add_step(
            old_entry_id,
            "restore",
            "ok",
            f"AWG2 поднят на {public_ip}:{result['port']}, peers {result['peers_count']}",
        )
    finally:
        ssh.close()

    ER.set_status(old_entry_id, store_mod.STATUS_WAITING_DNS)
    # сразу прогоним DNS-проверку — вдруг домен уже указывает на новый IP
    try:
        check_dns(old_entry_id)
    except Exception:  # noqa: BLE001
        pass
    return ER.get_public(old_entry_id) or {}


# --------------------------------------------------------------------------- #
# Шаг 5: DNS-проверка
# --------------------------------------------------------------------------- #


def check_dns(old_entry_id: str) -> dict:
    from datetime import datetime, timezone

    from app.services.panel_ssl import _resolve_domain_ips

    rec = ER.get(old_entry_id)
    if not rec:
        raise EntryReplacementError("Замена не найдена.")

    domain = rec.get("expected_domain")
    new_ip = rec.get("new_public_ip")
    resolved: list[str] = []
    dns_ok = False

    if domain and new_ip:
        resolved = _resolve_domain_ips(domain)
        dns_ok = new_ip in resolved

    # health нового входа: контейнер AWG2 запущен.
    health_ok = False
    health_detail = None
    try:
        ssh = _connect_new(old_entry_id)
        try:
            health_ok = _container_running(ssh, AWG2_CONTAINER)
            health_detail = "контейнер AWG2 running" if health_ok else "контейнер AWG2 не запущен"
        finally:
            ssh.close()
    except Exception as exc:  # noqa: BLE001
        health_detail = f"нет связи с новым VPS: {exc}"

    ER.update(
        old_entry_id,
        dns_ok=dns_ok,
        dns_resolved_ips=resolved,
        dns_checked_at=datetime.now(timezone.utc).isoformat(),
        health_ok=health_ok,
        health_detail=health_detail,
    )

    # Переход waiting_dns → ready / обратно.
    cur = ER.get(old_entry_id)
    if cur and cur.get("status") in {store_mod.STATUS_WAITING_DNS, store_mod.STATUS_READY}:
        if dns_ok and health_ok:
            ER.set_status(old_entry_id, store_mod.STATUS_READY)
        else:
            ER.set_status(old_entry_id, store_mod.STATUS_WAITING_DNS)
    return ER.get_public(old_entry_id) or {}


# --------------------------------------------------------------------------- #
# Шаг 7: активация (swap записи сервера + re-apply каскада)
# --------------------------------------------------------------------------- #


def activate(old_entry_id: str) -> dict:
    from app.services.cascade_apply import apply_cascade, rollback_cascade
    from app.services.server_store import server_store

    rec = ER.get(old_entry_id)
    if not rec:
        raise EntryReplacementError("Замена не найдена.")
    if rec.get("status") not in {store_mod.STATUS_READY, store_mod.STATUS_WAITING_DNS}:
        raise EntryReplacementError(f"Активация недоступна в статусе «{rec.get('status')}».")

    # Финальная повторная проверка DNS + health перед swap.
    check_dns(old_entry_id)
    rec = ER.get(old_entry_id)
    if not rec.get("dns_ok"):
        raise EntryReplacementError(
            "Домен ещё не указывает на новый IP. Смените A-запись у регистратора и подождите TTL."
        )
    if not rec.get("health_ok"):
        raise EntryReplacementError("Новый вход не отвечает (контейнер AWG2 не запущен).")

    old = server_store.get_record(old_entry_id)
    if not old:
        raise EntryReplacementError("Заменяемый вход исчез из списка серверов.")

    ER.set_status(old_entry_id, store_mod.STATUS_ACTIVATING, error=None)

    # Бэкап старых полей записи для отката swap.
    backup = {
        "host": old.get("host"),
        "ssh_port": old.get("ssh_port"),
        "ssh_username": old.get("ssh_username"),
        "ssh_password_enc": old.get("ssh_password_enc"),
        "ssh_key_enc": old.get("ssh_key_enc"),
        "container_names": old.get("container_names"),
        "installed_protocols": old.get("installed_protocols"),
        "vpn_port": old.get("vpn_port"),
        "status": old.get("status"),
    }
    ER.update(old_entry_id, old_host_backup=backup)

    raw = ER.get(old_entry_id)
    port = raw.get("port") or backup.get("vpn_port")
    protocols = dict(old.get("installed_protocols") or {})
    protocols["awg2"] = {"port": port, "container": AWG2_CONTAINER}
    names = list(old.get("container_names") or [])
    if AWG2_CONTAINER not in names:
        names.append(AWG2_CONTAINER)

    # SWAP: тот же server_id теперь смотрит на новый VPS.
    server_store.update_runtime(
        old_entry_id,
        host=raw["new_host"],
        ssh_port=raw.get("new_ssh_port", 22),
        ssh_username=raw.get("new_ssh_username", "root"),
        ssh_password_enc=raw.get("new_ssh_password_enc"),
        ssh_key_enc=raw.get("new_ssh_key_enc"),
        container_names=names,
        installed_protocols=protocols,
        awg2_imported=True,
        vpn_port=port,
        status="online",
    )
    ER.add_step(old_entry_id, "swap", "ok", f"вход теперь на {raw['new_host']}")

    # Каскад: re-apply на новом public IP (host-правила прибиты к IP входа).
    exit_id = raw.get("cascade_exit_id")
    if exit_id:
        ER.add_step(old_entry_id, "cascade", "running")
        try:
            from app.services.cascade import run_preflight

            pf = run_preflight(old_entry_id, exit_id)
            if not getattr(pf, "ok", True):
                raise EntryReplacementError("Каскадный preflight не пройден на новом входе.")
            apply_cascade(old_entry_id)
            ER.add_step(old_entry_id, "cascade", "ok", "каскад поднят на новом входе")
        except Exception as exc:  # noqa: BLE001
            ER.add_step(old_entry_id, "cascade", "fail", str(exc))
            # Откат: чистим каскад на новом VPS, возвращаем запись на старый сервер.
            try:
                rollback_cascade(old_entry_id)
            except Exception:  # noqa: BLE001
                pass
            _restore_server_record(old_entry_id, backup)
            ER.set_status(old_entry_id, store_mod.STATUS_FAILED, error=f"Каскад не поднялся: {exc}")
            raise EntryReplacementError(
                f"Каскад не поднялся на новом входе: {exc}. Запись сервера возвращена на старый VPS."
            )

    ER.set_status(old_entry_id, store_mod.STATUS_ACTIVE)

    # Уведомление + аудит (best-effort).
    try:
        from app.services.notification_store import notification_store

        notification_store.add(
            level="success",
            code="entry_replaced",
            title="Вход заменён",
            message=f"Вход «{old.get('name')}» теперь на {raw['new_host']}. Клиентам конфиги менять не нужно.",
        )
    except Exception:  # noqa: BLE001
        pass

    return ER.get_public(old_entry_id) or {}


def _restore_server_record(old_entry_id: str, backup: dict) -> None:
    from app.services.server_store import server_store

    server_store.update_runtime(
        old_entry_id,
        host=backup.get("host"),
        ssh_port=backup.get("ssh_port"),
        ssh_username=backup.get("ssh_username"),
        ssh_password_enc=backup.get("ssh_password_enc"),
        ssh_key_enc=backup.get("ssh_key_enc"),
        container_names=backup.get("container_names"),
        installed_protocols=backup.get("installed_protocols"),
        vpn_port=backup.get("vpn_port"),
        status=backup.get("status") or "unknown",
    )


# --------------------------------------------------------------------------- #
# Отмена
# --------------------------------------------------------------------------- #


def abort(old_entry_id: str) -> dict:
    from app.services.awg_restore import rollback_new_vps

    rec = ER.get(old_entry_id)
    if not rec:
        raise EntryReplacementError("Замена не найдена.")
    if rec.get("status") == store_mod.STATUS_ACTIVE:
        raise EntryReplacementError("Замена уже активирована — отмена недоступна.")

    # Если успели что-то развернуть на новом VPS — снести.
    if rec.get("status") in {store_mod.STATUS_PROVISIONING, store_mod.STATUS_WAITING_DNS, store_mod.STATUS_READY}:
        try:
            ssh = _connect_new(old_entry_id)
            try:
                rollback_new_vps(ssh)
                ER.add_step(old_entry_id, "rollback", "ok", "новый VPS очищен")
            finally:
                ssh.close()
        except Exception:  # noqa: BLE001
            pass

    ER.set_status(old_entry_id, store_mod.STATUS_ABORTED)
    ER.delete(old_entry_id)
    return {"ok": True}


def status(old_entry_id: str) -> Optional[dict]:
    rec = ER.get_public(old_entry_id)
    if not rec:
        return None
    rec["can_activate"] = bool(
        rec.get("status") in {store_mod.STATUS_READY, store_mod.STATUS_WAITING_DNS}
        and rec.get("dns_ok")
        and rec.get("health_ok")
    )
    return rec
