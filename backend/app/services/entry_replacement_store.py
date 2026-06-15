"""Хранилище процесса «Замена Входа» (RES2a — Entry disaster recovery).

Одна активная замена на заменяемый вход (`old_entry_id`). Креды нового VPS
хранятся только в зашифрованном виде (Fernet, как в server_store). Машина
состояний и инварианты описаны в _dev-docs/ENTRY_REPLACEMENT_PLAN.md §4.

Файл — read-only журнал процесса; реальные изменения на серверах делает
entry_replacement.py. Persistent (atomic write_json), переживает рестарт.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from app.core.crypto import decrypt, encrypt
from app.services.persistence import read_json, write_json

REPLACEMENTS_FILE = "entry_replacements.json"

# Статусы процесса (см. план §4).
STATUS_DRAFT = "draft"
STATUS_PREFLIGHT = "preflight"
STATUS_PREFLIGHT_FAILED = "preflight_failed"
STATUS_PROVISIONING = "provisioning"
STATUS_WAITING_DNS = "waiting_dns"
STATUS_READY = "ready"
STATUS_ACTIVATING = "activating"
STATUS_ACTIVE = "active"
STATUS_FAILED = "failed"
STATUS_ABORTED = "aborted"

# Статусы, при которых процесс ещё «живой» (один на вход).
OPEN_STATUSES = {
    STATUS_DRAFT,
    STATUS_PREFLIGHT,
    STATUS_PREFLIGHT_FAILED,
    STATUS_PROVISIONING,
    STATUS_WAITING_DNS,
    STATUS_READY,
    STATUS_ACTIVATING,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class EntryReplacementStore:
    def __init__(self) -> None:
        self._items: dict[str, dict] = read_json(REPLACEMENTS_FILE, {})

    def _persist(self) -> None:
        write_json(REPLACEMENTS_FILE, self._items)

    # --- чтение ---------------------------------------------------------------

    def get(self, old_entry_id: str) -> Optional[dict]:
        return self._items.get(old_entry_id)

    def get_public(self, old_entry_id: str) -> Optional[dict]:
        rec = self._items.get(old_entry_id)
        return self._public(rec) if rec else None

    def all_public(self) -> list[dict]:
        return [self._public(r) for r in self._items.values()]

    def has_open(self, old_entry_id: str) -> bool:
        rec = self._items.get(old_entry_id)
        return bool(rec and rec.get("status") in OPEN_STATUSES)

    def ssh_creds(self, old_entry_id: str) -> Optional[dict]:
        """Расшифрованные SSH-креды нового VPS для подключения."""
        rec = self._items.get(old_entry_id)
        if not rec:
            return None
        return {
            "host": rec["new_host"],
            "port": rec.get("new_ssh_port", 22),
            "username": rec.get("new_ssh_username", "root"),
            "password": decrypt(rec.get("new_ssh_password_enc")),
            "key": decrypt(rec.get("new_ssh_key_enc")),
        }

    # --- запись ---------------------------------------------------------------

    def create_draft(
        self,
        old_entry_id: str,
        *,
        new_host: str,
        new_ssh_port: int,
        new_ssh_username: str,
        new_ssh_password: Optional[str],
        new_ssh_key: Optional[str],
        expected_domain: Optional[str],
        port: Optional[int],
        cascade_exit_id: Optional[str],
    ) -> dict:
        rec = {
            "id": str(uuid4()),
            "old_entry_id": old_entry_id,
            "status": STATUS_DRAFT,
            "new_host": new_host,
            "new_ssh_port": new_ssh_port,
            "new_ssh_username": new_ssh_username,
            "new_ssh_password_enc": encrypt(new_ssh_password),
            "new_ssh_key_enc": encrypt(new_ssh_key),
            "new_public_ip": None,
            "snapshot_id": None,
            "snapshot_source": None,
            "expected_domain": (expected_domain or "").strip() or None,
            "port": port,
            "port_changed": False,
            "cascade_exit_id": cascade_exit_id,
            "dns_ok": False,
            "dns_resolved_ips": [],
            "dns_checked_at": None,
            "health_ok": False,
            "health_detail": None,
            "steps": [],
            "error": None,
            "created_at": _now(),
            "updated_at": _now(),
            "activated_at": None,
            # снимок старых SSH-кредов для отката swap при сбое активации
            "old_host_backup": None,
        }
        self._items[old_entry_id] = rec
        self._persist()
        return self._public(rec)

    def update(self, old_entry_id: str, **fields) -> Optional[dict]:
        rec = self._items.get(old_entry_id)
        if not rec:
            return None
        rec.update(fields)
        rec["updated_at"] = _now()
        self._persist()
        return self._public(rec)

    def add_step(self, old_entry_id: str, name: str, status: str, detail: Optional[str] = None) -> None:
        rec = self._items.get(old_entry_id)
        if not rec:
            return
        steps = rec.setdefault("steps", [])
        # Идемпотентность: один и тот же шаг обновляем, а не плодим.
        for s in steps:
            if s.get("name") == name:
                s["status"] = status
                s["detail"] = detail
                s["at"] = _now()
                break
        else:
            steps.append({"name": name, "status": status, "detail": detail, "at": _now()})
        rec["updated_at"] = _now()
        self._persist()

    def set_status(self, old_entry_id: str, status: str, *, error: Optional[str] = None) -> None:
        rec = self._items.get(old_entry_id)
        if not rec:
            return
        rec["status"] = status
        if error is not None:
            rec["error"] = error
        if status == STATUS_ACTIVE:
            rec["activated_at"] = _now()
        rec["updated_at"] = _now()
        self._persist()

    def delete(self, old_entry_id: str) -> bool:
        if old_entry_id in self._items:
            del self._items[old_entry_id]
            self._persist()
            return True
        return False

    def forget_server(self, server_id: str) -> int:
        """Убрать замены, где сервер — заменяемый вход или новый exit-партнёр."""
        removed = 0
        for key in list(self._items.keys()):
            rec = self._items[key]
            if rec.get("old_entry_id") == server_id or rec.get("cascade_exit_id") == server_id:
                del self._items[key]
                removed += 1
        if removed:
            self._persist()
        return removed

    # --- утилиты --------------------------------------------------------------

    def _public(self, rec: dict) -> dict:
        """Без шифрованных кредов — наружу отдаём только безопасные поля."""
        hidden = {"new_ssh_password_enc", "new_ssh_key_enc", "old_host_backup"}
        return {k: v for k, v in rec.items() if k not in hidden}


entry_replacement_store = EntryReplacementStore()
