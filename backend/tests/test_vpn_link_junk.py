"""vpn:// должен получать mobile-safe junk, как и .conf."""

from __future__ import annotations

import base64
import json
import zlib

from app.services.amnezia_link import build_vpn_link
from app.services.awg_config import CLIENT_JUNK_MAX_JC, CLIENT_JUNK_MAX_JMAX, build_client_config


def _last_config(link: str) -> dict:
    raw = link.replace("vpn://", "")
    raw += "=" * (-len(raw) % 4)
    blob = base64.urlsafe_b64decode(raw)
    data = json.loads(zlib.decompress(blob[4:]).decode("utf-8"))
    last = data["containers"][0]["awg"]["last_config"]
    return json.loads(last) if isinstance(last, str) else last


def test_vpn_link_clamps_server_junk() -> None:
    awg_params = {
        "Jc": "7",
        "Jmin": "95",
        "Jmax": "702",
        "S1": "60",
        "S2": "58",
        "S3": "54",
        "S4": "25",
        "H1": "100-200",
        "H2": "300-400",
        "H3": "500-600",
        "H4": "700-800",
    }
    conf = build_client_config(
        client_private_key="clientpriv",
        client_ip="10.8.1.2",
        dns="1.1.1.1",
        server_public_key="serverpub",
        preshared_key=None,
        endpoint_host="1.2.3.4",
        endpoint_port=39547,
        awg_params=awg_params,
    )
    link = build_vpn_link(
        host="1.2.3.4",
        port=39547,
        dns="1.1.1.1",
        client_ip="10.8.1.2",
        client_private_key="clientpriv",
        client_public_key="clientpub",
        server_public_key="serverpub",
        preshared_key=None,
        awg_params=awg_params,
        wg_config_ini=conf,
        description="test",
    )
    last = _last_config(link)
    assert int(last["Jc"]) <= CLIENT_JUNK_MAX_JC
    assert int(last["Jmax"]) <= CLIENT_JUNK_MAX_JMAX
    assert "Jc = 4" in conf
    assert "Jmax = 500" in conf
    assert last["S1"] == "60"
    assert last["H1"] == "100-200"
