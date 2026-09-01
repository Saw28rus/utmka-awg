"""Каскад AmneziaWG 2.0 / 3.1: выбор протокола, каналы, conf транзита."""

from __future__ import annotations

from app.services.awg_config import AWG_PARAM_KEYS
from app.services.cascade_apply import _render_conf
from app.services.cascade_protocol import (
    CLIENT_HINT_31,
    cascade_channel_id,
    containers_for,
    link_protocols,
    normalize_cascade_protocols,
    persist_suffix,
    protocol_on_cascade,
)
from app.services.protocol_engine import get_engine
from app.services.transit_allocator import profile_for_slot


def test_normalize_cascade_protocols_order() -> None:
    assert normalize_cascade_protocols(["awg31", "awg2", "xray", "awg"]) == ["awg2", "awg31"]
    assert normalize_cascade_protocols(["awg31"]) == ["awg31"]
    assert normalize_cascade_protocols([]) == []


def test_link_protocols_legacy_is_awg2() -> None:
    assert link_protocols({}) == ["awg2"]
    assert link_protocols({"protocol": "awg31"}) == ["awg31"]
    assert link_protocols({"protocols": ["awg31", "awg2"]}) == ["awg2", "awg31"]


def test_protocol_on_cascade() -> None:
    both = {"protocols": ["awg2", "awg31"]}
    assert protocol_on_cascade("awg2", both)
    assert protocol_on_cascade("awg31", both)
    only2 = {"exit_server_id": "x"}
    assert protocol_on_cascade("awg2", only2)
    assert not protocol_on_cascade("awg31", only2)


def test_channel_ids_split_by_protocol() -> None:
    assert cascade_channel_id("srv1", "awg2") == "cascade:srv1"
    assert cascade_channel_id("srv1", "awg31") == "cascade:srv1:awg31"


def test_containers_and_persist_suffix() -> None:
    assert "amnezia-awg31" in containers_for("awg31")
    assert persist_suffix("awg31") == "-awg31"
    assert persist_suffix("awg2") == ""


def test_slot1_does_not_reuse_slot0_entry_port() -> None:
    p0 = profile_for_slot(0)
    p1 = profile_for_slot(1)
    assert p0.transit_port == 51821
    assert p0.entry_host_port == 51822
    assert p1.transit_port == 51822
    assert p1.entry_host_port == 51823
    assert p1.transit_port != p0.entry_host_port or p1.entry_host_port != p0.transit_port


def test_render_conf_keeps_header_protection() -> None:
    text = _render_conf(
        private_key="priv",
        address="10.250.0.1/30",
        listen_port=51821,
        peer_pub="pub",
        psk="psk",
        allowed_ips="10.250.0.2/32",
        params={
            "Jc": "5",
            "Jmin": "10",
            "Jmax": "50",
            "S1": "12",
            "S2": "13",
            "S3": "14",
            "S4": "15",
            "H1": "1",
            "H2": "2",
            "H3": "3",
            "H4": "4",
            "HeaderProtectionKey": "hpkey",
            "RandomTrailers": "on",
            "DisableCookies": "on",
            "ContentPaddingAddition": "10-100",
        },
    )
    assert "HeaderProtectionKey = hpkey" in text
    assert "RandomTrailers = on" in text
    assert "ContentPaddingAddition = 10-100" in text
    for key in ("Jc", "S3", "H1"):
        assert key in AWG_PARAM_KEYS


def test_engine_awg31_has_cascade() -> None:
    caps = get_engine("awg31").capabilities()
    assert caps.cascade
    assert not caps.masking
    assert get_engine("awg2").capabilities().cascade


def test_client_hint_mentions_amnezia_5() -> None:
    assert "5.0.1.5" in CLIENT_HINT_31
