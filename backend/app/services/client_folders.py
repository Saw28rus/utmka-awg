"""Папки клиентов в панели. Это только группировка в списке, на VPN не влияет."""

from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from app.services.persistence import read_json, write_json

FOLDERS_FILE = "client_folders.json"
MAX_FOLDERS = 40
MAX_NAME = 40


class ClientFolderError(Exception):
    pass


class ClientFolderStore:
    def __init__(self) -> None:
        data = read_json(FOLDERS_FILE, {})
        raw = data.get("folders") if isinstance(data, dict) else {}
        self._folders: dict[str, dict] = raw if isinstance(raw, dict) else {}

    def _persist(self) -> None:
        write_json(FOLDERS_FILE, {"folders": self._folders})

    def list_all(self) -> list[dict]:
        return sorted(
            self._folders.values(),
            key=lambda folder: (int(folder.get("sort_order") or 0), str(folder.get("name") or "").lower()),
        )

    def get(self, folder_id: str) -> Optional[dict]:
        return self._folders.get(folder_id)

    def create(self, name: str) -> dict:
        clean = _clean_name(name)
        self._reject_duplicate(clean)
        if len(self._folders) >= MAX_FOLDERS:
            raise ClientFolderError(f"Не больше {MAX_FOLDERS} папок.")
        orders = [int(folder.get("sort_order") or 0) for folder in self._folders.values()]
        folder_id = str(uuid4())
        record = {
            "id": folder_id,
            "name": clean,
            "sort_order": (max(orders) + 1) if orders else 0,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._folders[folder_id] = record
        self._persist()
        return record

    def rename(self, folder_id: str, name: str) -> dict:
        record = self._folders.get(folder_id)
        if not record:
            raise ClientFolderError("Папка не найдена.")
        clean = _clean_name(name)
        self._reject_duplicate(clean, ignore_id=folder_id)
        record["name"] = clean
        self._persist()
        return record

    def delete(self, folder_id: str) -> bool:
        if folder_id not in self._folders:
            return False
        del self._folders[folder_id]
        self._persist()
        return True

    def _reject_duplicate(self, name: str, ignore_id: Optional[str] = None) -> None:
        needle = name.casefold()
        for folder in self._folders.values():
            if ignore_id and folder.get("id") == ignore_id:
                continue
            if str(folder.get("name") or "").casefold() == needle:
                raise ClientFolderError("Папка с таким именем уже есть.")


def _clean_name(name: str) -> str:
    clean = " ".join((name or "").split())
    if not clean:
        raise ClientFolderError("Введите название папки.")
    if len(clean) > MAX_NAME:
        raise ClientFolderError(f"Название не длиннее {MAX_NAME} символов.")
    return clean


client_folder_store = ClientFolderStore()
