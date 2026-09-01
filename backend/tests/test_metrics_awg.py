"""Метрики AWG: dump 2.0 и 3.1 склеиваются, клиенты 3.1 не теряются."""

from __future__ import annotations

from app.services.awg_config import merge_peer_stats, parse_dump
from app.services.metrics import awg_metric_containers


DUMP_AWG2 = """\
awg0 private= pubkeyServer20= 39547 off
awg0 client20AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA= (none) 188.162.1.1:12345 10.8.1.2/32 1710000000 111 222 25
utmka-cas0 transit20AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA= (none) 5.253.59.249:51821 10.7.0.2/32 1710000001 10 20 25
"""

DUMP_AWG31 = """\
awg0 private= pubkeyServer31= 55423 5 10 50 12 13 14 15 1 2 3 4 (none) (none) (none) (none) (none) off
awg0 client31AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA= (none) 188.162.1.1:54321 10.8.3.2/32 1710000500 333 444 25
utmka-cas1 transit31AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA= (none) 5.253.59.249:51822 10.7.1.2/32 1710000501 30 40 25
"""


def test_parse_dump_reads_awg31_peer_despite_long_interface_line() -> None:
    stats = parse_dump(DUMP_AWG31)
    assert "client31AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" in stats
    peer = stats["client31AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="]
    assert peer.rx_bytes == 333
    assert peer.tx_bytes == 444
    assert peer.handshake_unix == 1710000500


def test_merge_peer_stats_keeps_both_protocol_clients() -> None:
    merged = merge_peer_stats(parse_dump(DUMP_AWG2), parse_dump(DUMP_AWG31))
    assert "client20AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" in merged
    assert "client31AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" in merged
    assert merged["client31AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="].rx_bytes == 333
    assert merged["client20AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="].tx_bytes == 222


def test_merge_peer_stats_prefers_newer_handshake() -> None:
    older = parse_dump(
        "peerAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA= (none) 1.1.1.1:1 10.8.3.2/32 100 1 1 25\n"
    )
    newer = parse_dump(
        "peerAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA= (none) 1.1.1.1:1 10.8.3.2/32 200 9 9 25\n"
    )
    merged = merge_peer_stats(older, newer)
    assert merged["peerAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="].rx_bytes == 9
    assert merged["peerAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="].handshake_unix == 200


def test_awg_metric_containers_includes_both_and_skips_panel() -> None:
    names = awg_metric_containers(
        {
            "container_names": [
                "utmka-awg-backend-1",
                "amnezia-awg2",
                "amnezia-awg31",
            ],
            "installed_protocols": {
                "awg2": {"container": "amnezia-awg2", "port": 39547},
                "awg31": {"container": "amnezia-awg31", "port": 55423},
            },
        }
    )
    assert names == ["amnezia-awg2", "amnezia-awg31"]


def test_awg_metric_containers_from_protocols_if_names_stale() -> None:
    names = awg_metric_containers(
        {
            "container_names": ["amnezia-awg2"],
            "installed_protocols": {
                "awg2": {"container": "amnezia-awg2"},
                "awg31": {"container": "amnezia-awg31"},
            },
        }
    )
    assert "amnezia-awg2" in names
    assert "amnezia-awg31" in names
