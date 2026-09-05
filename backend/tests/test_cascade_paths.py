"""Путь каскада в UI: вход → выход только если протокол реально едет через каскад."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.cascade_paths import (
    apply_awg_cascade_to_servers,
    build_awg_cascade_index,
    resolve_client_cascade_exit,
)


NAMES = {"ru": "КлиентRU", "nl": "КлиентNL"}


def test_awg_path_when_protocol_on_active_cascade() -> None:
    link = {
        "state": "active",
        "exit_server_id": "nl",
        "protocols": ["awg2", "awg31"],
    }
    assert resolve_client_cascade_exit(
        server_id="ru",
        protocol="awg31",
        awg_link=link,
        xray_link=None,
        names=NAMES,
    ) == ("КлиентNL", True)
    assert resolve_client_cascade_exit(
        server_id="ru",
        protocol="awg2",
        awg_link=link,
        xray_link=None,
        names=NAMES,
    ) == ("КлиентNL", True)


def test_awg_path_hidden_when_protocol_not_on_cascade() -> None:
    link = {"state": "active", "exit_server_id": "nl"}
    assert resolve_client_cascade_exit(
        server_id="ru",
        protocol="awg31",
        awg_link=link,
        xray_link=None,
        names=NAMES,
    ) == (None, False)
    assert resolve_client_cascade_exit(
        server_id="ru",
        protocol="awg2",
        awg_link=link,
        xray_link=None,
        names=NAMES,
    ) == ("КлиентNL", True)


def test_awg_path_hidden_when_cascade_down() -> None:
    link = {
        "state": "rolled_back",
        "exit_server_id": "nl",
        "protocols": ["awg2", "awg31"],
    }
    assert resolve_client_cascade_exit(
        server_id="ru",
        protocol="awg2",
        awg_link=link,
        xray_link=None,
        names=NAMES,
    ) == (None, False)


def test_xray_path_when_active() -> None:
    xlink = {"state": "active", "exit_server_id": "nl"}
    assert resolve_client_cascade_exit(
        server_id="ru",
        protocol="xray",
        awg_link=None,
        xray_link=xlink,
        names=NAMES,
    ) == ("КлиентNL", True)
    down = {"state": "down", "exit_server_id": "nl"}
    assert resolve_client_cascade_exit(
        server_id="ru",
        protocol="xray",
        awg_link=None,
        xray_link=down,
        names=NAMES,
    ) == (None, False)


def test_build_awg_cascade_index_roles() -> None:
    links = [
        {
            "state": "active",
            "entry_server_id": "ru",
            "exit_server_id": "nl",
        },
        {
            "state": "rolled_back",
            "entry_server_id": "other",
            "exit_server_id": "nl",
        },
    ]
    index = build_awg_cascade_index(links, NAMES)
    assert index["ru"]["role"] == "entry"
    assert index["ru"]["exit_name"] == "КлиентNL"
    assert index["nl"]["role"] == "exit"
    assert index["nl"]["peer_name"] == "КлиентRU"
    assert "other" not in index


def test_apply_awg_cascade_to_servers(monkeypatch) -> None:
    ru = SimpleNamespace(
        id="ru",
        awg_cascade_active=False,
        awg_cascade_role=None,
        awg_cascade_exit_name=None,
        awg_cascade_peer_name=None,
    )
    nl = SimpleNamespace(
        id="nl",
        awg_cascade_active=False,
        awg_cascade_role=None,
        awg_cascade_exit_name=None,
        awg_cascade_peer_name=None,
    )
    solo = SimpleNamespace(
        id="solo",
        awg_cascade_active=True,
        awg_cascade_role="entry",
        awg_cascade_exit_name="x",
        awg_cascade_peer_name="y",
    )

    def _links():
        return [
            {
                "state": "active",
                "entry_server_id": "ru",
                "exit_server_id": "nl",
            }
        ]

    monkeypatch.setattr(
        "app.services.cascade_paths.cascade_store.list_links",
        _links,
    )
    monkeypatch.setattr(
        "app.services.cascade_paths._server_names",
        lambda: NAMES,
    )
    apply_awg_cascade_to_servers([ru, nl, solo])
    assert ru.awg_cascade_active is True
    assert ru.awg_cascade_role == "entry"
    assert ru.awg_cascade_exit_name == "КлиентNL"
    assert nl.awg_cascade_role == "exit"
    assert nl.awg_cascade_exit_name is None
    assert solo.awg_cascade_active is False
    assert solo.awg_cascade_role is None
