"""AmneziaWG 3.1: install defaults, client conf, vpn:// protocolVersion."""

from __future__ import annotations

from app.services.amnezia_link import build_vpn_link, detect_protocol_version
from app.services.awg_config import build_client_config, parse_interface
from app.services.awg_install import DEFAULT_PORT, VARIANTS, _awg_params
from app.services.awg_masking_apply import generate_amnezia_31_params
from app.services.protocol_engine import get_engine
from app.services.protocol_versions import PINNED


def test_awg31_variant_and_pin() -> None:
    assert "awg31" in VARIANTS
    assert VARIANTS["awg31"]["container"] == "amnezia-awg31"
    assert VARIANTS["awg31"]["scripts"] == "awg31"
    assert VARIANTS["awg31"]["subnet_ip"] == "10.8.3.1"
    assert PINNED["awg31"]["image"] == "amneziavpn/amneziawg-go:3.1.20260828"
    assert DEFAULT_PORT == 55424
    engine = get_engine("awg31")
    caps = engine.capabilities()
    assert engine.id == "awg31"
    assert caps.install
    assert caps.create_client
    assert caps.masking is False
    assert caps.cascade


def test_generate_amnezia_31_params() -> None:
    for _ in range(20):
        params = generate_amnezia_31_params()
        assert params["Jmin"] == "10"
        assert params["Jmax"] == "50"
        assert 4 <= int(params["Jc"]) <= 6
        assert int(params["S1"]) >= 12
        assert int(params["S2"]) >= 12
        assert int(params["S3"]) >= 12
        assert int(params["S4"]) >= 12
        sizes = {params["S1"], params["S2"], params["S3"], params["S4"]}
        assert len(sizes) == 4
        assert params["H1"] == "1"
        assert params["H2"] == "2"
        assert params["H3"] == "3"
        assert params["H4"] == "4"
        assert params["RandomTrailers"] == "on"
        assert params["DisableCookies"] == "on"
        assert params["ContentPaddingAddition"] == "10-100"


def test_awg_install_params_awg31() -> None:
    mapped = _awg_params("awg31")
    assert mapped["$JUNK_PACKET_MIN_SIZE"] == "10"
    assert mapped["$JUNK_PACKET_MAX_SIZE"] == "50"
    assert mapped["$INIT_PACKET_MAGIC_HEADER"] == "1"
    assert mapped["$TRANSPORT_PACKET_MAGIC_HEADER"] == "4"
    assert int(mapped["$INIT_PACKET_JUNK_SIZE"]) >= 12
    assert mapped["$RANDOM_TRAILERS"] == "on"
    assert mapped["$CONTENT_PADDING_ADDITION"] == "10-100"


def test_detect_protocol_version_31() -> None:
    assert detect_protocol_version({"S3": "10", "S4": "8"}) == "2"
    assert (
        detect_protocol_version(
            {
                "S3": "12",
                "S4": "12",
                "HeaderProtectionKey": "abc",
                "RandomTrailers": "on",
            }
        )
        == "3.1"
    )


def test_vpn_link_and_conf_keep_awg31_keys() -> None:
    awg_params = {
        "Jc": "5",
        "Jmin": "10",
        "Jmax": "50",
        "S1": "40",
        "S2": "41",
        "S3": "12",
        "S4": "13",
        "H1": "1",
        "H2": "2",
        "H3": "3",
        "H4": "4",
        "HeaderProtectionKey": "headerprotkeybase64value",
        "ContentPaddingAddition": "10-100",
        "RandomTrailers": "on",
        "DisableCookies": "on",
    }
    conf = build_client_config(
        client_private_key="clientpriv",
        client_ip="10.8.3.2",
        dns="1.1.1.1",
        server_public_key="serverpub",
        preshared_key=None,
        endpoint_host="1.2.3.4",
        endpoint_port=55424,
        awg_params=awg_params,
    )
    assert "HeaderProtectionKey = headerprotkeybase64value" in conf
    assert "RandomTrailers = on" in conf
    assert "DisableCookies = on" in conf
    assert "ContentPaddingAddition = 10-100" in conf
    parsed = parse_interface(conf)
    assert parsed.awg_params["HeaderProtectionKey"] == "headerprotkeybase64value"

    import base64
    import json
    import zlib

    link = build_vpn_link(
        host="1.2.3.4",
        port=55424,
        dns="1.1.1.1",
        client_ip="10.8.3.2",
        client_private_key="clientpriv",
        client_public_key="clientpub",
        server_public_key="serverpub",
        preshared_key=None,
        awg_params=awg_params,
        wg_config_ini=conf,
        description="test",
    )
    raw = link.replace("vpn://", "")
    raw += "=" * (-len(raw) % 4)
    blob = base64.urlsafe_b64decode(raw)
    data = json.loads(zlib.decompress(blob[4:]).decode("utf-8"))
    container = data["containers"][0]
    assert container["awg"]["protocol_version"] == "3.1"
    assert container["awg"]["protocolVersion"] == "3.1"
    last = json.loads(container["awg"]["last_config"])
    assert last["HeaderProtectionKey"] == "headerprotkeybase64value"
    assert last["protocol_version"] == "3.1"
    assert last["RandomTrailers"] == "on"
    assert last["Jc"] == "5"


def test_client_protocols_see_awg31_from_container_name() -> None:
    from app.services.server_store import ServerStore

    store = ServerStore.__new__(ServerStore)
    rec = {
        "awg2_imported": True,
        "container_names": ["amnezia-awg2", "amnezia-awg31"],
        "installed_protocols": {},
    }
    assert store._client_protocols(rec) == ["awg31", "awg2"]
    assert "AmneziaWG 3.1" in store._protocols(rec)


def test_client_protocols_see_awg31_from_installed_map() -> None:
    from app.services.server_store import ServerStore

    store = ServerStore.__new__(ServerStore)
    rec = {
        "awg2_imported": True,
        "container_names": ["amnezia-awg2"],
        "installed_protocols": {"awg31": {"port": 55425, "container": "amnezia-awg31"}},
    }
    assert "awg31" in store._client_protocols(rec)
    assert "awg2" in store._client_protocols(rec)

