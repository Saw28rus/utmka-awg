from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from app.core.crypto import decrypt, encrypt
from app.schemas.servers import ServerCreate, ServerListItem, ServerRead
from app.services.persistence import read_json, write_json

SERVERS_FILE = "servers.json"


class SshTarget:
    def __init__(self, host: str, port: int, username: str, password: Optional[str], key: Optional[str]):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.key = key


class ServerStore:
    def __init__(self) -> None:
        self._servers: dict[str, dict] = read_json(SERVERS_FILE, {})

    def _persist(self) -> None:
        write_json(SERVERS_FILE, self._servers)

    def create(self, payload: ServerCreate, *, message: Optional[str] = None) -> ServerRead:
        server_id = str(uuid4())
        branch = payload.detect_branch or ("import" if payload.awg2_detected else "install")
        record = {
            "id": server_id,
            "name": payload.name,
            "host": payload.host,
            "ssh_port": payload.ssh_port,
            "ssh_username": payload.ssh_username,
            "ssh_password_enc": encrypt(payload.ssh_password),
            "ssh_key_enc": encrypt(payload.ssh_key),
            "status": "online" if branch in {"import", "install"} else "unknown",
            "awg2_imported": branch == "import",
            "notes": payload.notes,
            "detect_branch": branch,
            "awg2_detected": payload.awg2_detected,
            "config_path": payload.config_path,
            "container_names": payload.container_names,
            "active_peers": payload.active_peers,
            "vpn_port": None,
            "last_detect_message": message,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._servers[server_id] = record
        self._persist()
        return self._to_read(record)

    def list(self) -> list[ServerListItem]:
        return [self._to_list_item(record) for record in self._servers.values()]

    def get(self, server_id: str) -> Optional[ServerRead]:
        record = self._servers.get(server_id)
        return self._to_read(record) if record else None

    def get_record(self, server_id: str) -> Optional[dict]:
        return self._servers.get(server_id)

    def ssh_target(self, server_id: str) -> Optional[SshTarget]:
        record = self._servers.get(server_id)
        if not record:
            return None
        return SshTarget(
            host=record["host"],
            port=record["ssh_port"],
            username=record["ssh_username"],
            password=decrypt(record.get("ssh_password_enc")),
            key=decrypt(record.get("ssh_key_enc")),
        )

    def update_after_import(
        self,
        server_id: str,
        active_peers: int,
        message: str,
        *,
        vpn_port: Optional[int] = None,
    ) -> Optional[ServerRead]:
        record = self._servers.get(server_id)
        if not record:
            return None
        record["active_peers"] = active_peers
        record["awg2_imported"] = True
        record["last_detect_message"] = message
        if vpn_port is not None:
            record["vpn_port"] = vpn_port
        self._persist()
        return self._to_read(record)

    def update_runtime(self, server_id: str, **fields) -> None:
        record = self._servers.get(server_id)
        if not record:
            return
        record.update(fields)
        self._persist()

    def delete(self, server_id: str) -> bool:
        if server_id in self._servers:
            del self._servers[server_id]
            self._persist()
            return True
        return False

    def list_records(self) -> list[dict]:
        return list(self._servers.values())

    def client_protocols(self, record: dict) -> list[str]:
        return self._client_protocols(record)

    def _protocols(self, record: dict) -> list[str]:
        result: list[str] = []
        if record.get("awg2_detected") or record.get("awg2_imported"):
            result.append("AmneziaWG 2.0")
        if self._has_xray(record):
            result.append("Xray (VLESS-Reality)")
        return result

    def _client_protocols(self, record: dict) -> list[str]:
        protos: list[str] = []
        if record.get("awg2_imported"):
            protos.append("awg2")
        if self._has_xray(record):
            protos.append("xray")
        return protos

    def has_xray(self, record: dict) -> bool:
        return self._has_xray(record)

    def _has_xray(self, record: dict) -> bool:
        if (record.get("installed_protocols") or {}).get("xray"):
            return True
        return any(name == "amnezia-xray" for name in (record.get("container_names") or []))

    def _panel_domain(self, record: dict) -> Optional[str]:
        panel_ssl = record.get("panel_ssl") or {}
        if panel_ssl.get("status") == "active":
            domain = (panel_ssl.get("domain") or "").strip()
            return domain or None
        return None

    def _to_read(self, record: dict) -> ServerRead:
        return ServerRead(
            id=record["id"],
            name=record["name"],
            host=record["host"],
            ssh_port=record["ssh_port"],
            ssh_username=record["ssh_username"],
            status=record.get("status", "unknown"),
            awg2_imported=record.get("awg2_imported", False),
            notes=record.get("notes"),
            detect_branch=record.get("detect_branch", "needs_review"),
            awg2_detected=record.get("awg2_detected", False),
            config_path=record.get("config_path"),
            active_peers=record.get("active_peers", 0),
            protocols=self._protocols(record),
            client_protocols=self._client_protocols(record),
            vpn_port=record.get("vpn_port"),
            endpoint_host=record.get("endpoint_host"),
            panel_domain=self._panel_domain(record),
            last_detect_message=record.get("last_detect_message"),
            created_at=record.get("created_at"),
            former_entry=record.get("former_entry", False),
        )

    def _to_list_item(self, record: dict) -> ServerListItem:
        base = self._to_read(record)
        return ServerListItem(**base.model_dump())


server_store = ServerStore()
