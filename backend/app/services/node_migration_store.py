"""Хранилище процесса «Полная миграция узла» (NODE_MIGRATION_PLAN.md).

Глобальный singleton: одновременно может идти только ОДНА миграция панели
(переезжает весь стек целиком). Креды нового VPS — только в зашифрованном виде
(Fernet, как в server_store). Persistent (atomic write_json), переживает рестарт
и сам копируется на новый узел вместе с panel_data.

Файл — журнал процесса; изменения на серверах делает node_migration.py.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from app.core.crypto import decrypt, encrypt
from app.services.persistence import read_json, write_json

MIGRATIONS_FILE = "node_migrations.json"
SINGLETON_KEY = "current"

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

OPEN_STATUSES = {
    STATUS_DRAFT,
    STATUS_PREFLIGHT,
    STATUS_PREFLIGHT_FAILED,
    STATUS_PROVISIONING,
    STATUS_WAITING_DNS,
    STATUS_READY,
    STATUS_ACTIVATING,
}

_HIDDEN = {"new_ssh_password_enc", "new_ssh_key_enc"}

# Если шаг provision/activate не двигался дольше этого времени — помечаем «застрял»
# (узел завис / поток убит рестартом). Это advisory-флаг для UI, а не авто-отмена.
STALL_SECONDS = 25 * 60


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class NodeMigrationStore:
    def __init__(self) -> None:
        self._items: dict[str, dict] = read_json(MIGRATIONS_FILE, {})

    def _persist(self) -> None:
        write_json(MIGRATIONS_FILE, self._items)

    # --- чтение ---------------------------------------------------------------

    def get(self) -> Optional[dict]:
        return self._items.get(SINGLETON_KEY)

    def get_public(self) -> Optional[dict]:
        rec = self._items.get(SINGLETON_KEY)
        return self._public(rec) if rec else None

    def has_open(self) -> bool:
        rec = self._items.get(SINGLETON_KEY)
        return bool(rec and rec.get("status") in OPEN_STATUSES)

    def ssh_creds(self) -> Optional[dict]:
        rec = self._items.get(SINGLETON_KEY)
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
        *,
        new_host: str,
        new_ssh_port: int,
        new_ssh_username: str,
        new_ssh_password: Optional[str],
        new_ssh_key: Optional[str],
        expected_domain: Optional[str],
        source_node_name: Optional[str] = None,
        source_server_id: Optional[str] = None,
        ssh_password_enc: Optional[str] = None,
        ssh_key_enc: Optional[str] = None,
    ) -> dict:
        rec = {
            "id": str(uuid4()),
            "status": STATUS_DRAFT,
            "new_host": new_host,
            "new_ssh_port": new_ssh_port,
            "new_ssh_username": new_ssh_username,
            "new_ssh_password_enc": ssh_password_enc if ssh_password_enc is not None else encrypt(new_ssh_password),
            "new_ssh_key_enc": ssh_key_enc if ssh_key_enc is not None else encrypt(new_ssh_key),
            "new_public_ip": None,
            "expected_domain": (expected_domain or "").strip() or None,
            "source_node_name": source_node_name,
            "source_server_id": source_server_id,
            "dns_ok": False,
            "dns_resolved_ips": [],
            "dns_checked_at": None,
            "provision_ok": False,
            "health_ok": False,
            "steps": [],
            "error": None,
            "created_at": _now(),
            "updated_at": _now(),
            "activated_at": None,
        }
        self._items[SINGLETON_KEY] = rec
        self._persist()
        return self._public(rec)

    def update(self, **fields) -> Optional[dict]:
        rec = self._items.get(SINGLETON_KEY)
        if not rec:
            return None
        rec.update(fields)
        rec["updated_at"] = _now()
        self._persist()
        return self._public(rec)

    def add_step(self, name: str, status: str, detail: Optional[str] = None) -> None:
        rec = self._items.get(SINGLETON_KEY)
        if not rec:
            return
        steps = rec.setdefault("steps", [])
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

    def set_status(self, status: str, *, error: Optional[str] = None) -> None:
        rec = self._items.get(SINGLETON_KEY)
        if not rec:
            return
        rec["status"] = status
        if error is not None:
            rec["error"] = error
        if status == STATUS_ACTIVE:
            rec["activated_at"] = _now()
        rec["updated_at"] = _now()
        self._persist()

    def delete(self) -> bool:
        if SINGLETON_KEY in self._items:
            del self._items[SINGLETON_KEY]
            self._persist()
            return True
        return False

    # --- утилиты --------------------------------------------------------------

    def _public(self, rec: dict) -> dict:
        pub = {k: v for k, v in rec.items() if k not in _HIDDEN}
        pub["stalled"] = self._is_stalled(rec)
        return pub

    @staticmethod
    def _is_stalled(rec: dict) -> bool:
        if rec.get("status") not in {STATUS_PROVISIONING, STATUS_ACTIVATING}:
            return False
        updated = rec.get("updated_at")
        if not updated:
            return False
        try:
            ts = datetime.fromisoformat(updated)
        except ValueError:
            return False
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds() > STALL_SECONDS


node_migration_store = NodeMigrationStore()
