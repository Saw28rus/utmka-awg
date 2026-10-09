"""Папки клиентов и подпись каскада не должны затирать рабочие данные."""

from app.services.awg_config import ParsedPeer
from app.services.cascade_store import CascadeStore
from app.services.client_folders import ClientFolderError, ClientFolderStore
from app.services.client_store import ClientStore
from app.services import persistence


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(persistence, "DATA_DIR", tmp_path)


def test_folder_create_rename_duplicate(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    store = ClientFolderStore()
    folder = store.create("  Друзья  ")
    assert folder["name"] == "Друзья"
    renamed = store.rename(folder["id"], "Родные")
    assert renamed["name"] == "Родные"
    try:
        store.create("родные")
        raise AssertionError("duplicate name must fail")
    except ClientFolderError as exc:
        assert "уже есть" in str(exc)
    assert store.delete(folder["id"]) is True
    assert store.list_all() == []


def test_import_peers_keeps_folder(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    store = ClientStore()
    store._clients["old"] = {
        "id": "old",
        "name": "Ann",
        "server_id": "srv",
        "public_key": "pk1",
        "folder_id": "folder-1",
    }
    store.import_peers(
        "srv",
        server_name="RU",
        peers=[ParsedPeer(public_key="pk1", client_ip="10.8.1.2", index=0)],
        names={"pk1": "Ann"},
    )
    records = list(store._clients.values())
    assert len(records) == 1
    assert records[0]["folder_id"] == "folder-1"
    assert records[0]["name"] == "Ann"


def test_cascade_display_name_survives_state_update(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    store = CascadeStore()
    store.upsert_link("entry", exit_server_id="exit", state="active")
    assert store.set_display_name("entry", "  Семья  ")["display_name"] == "Семья"
    store.upsert_link("entry", state="active", egress_ip="1.2.3.4")
    assert store.get_link("entry")["display_name"] == "Семья"
    assert store.get_link("entry")["exit_server_id"] == "exit"
    cleared = store.set_display_name("entry", "   ")
    assert cleared is not None
    assert "display_name" not in cleared
    assert store.set_display_name("missing", "Имя") is None
