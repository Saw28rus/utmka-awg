"""Дефолты AWG 2.0: H-диапазоны, CPS, каскадный persist, шаблоны конфига."""

from __future__ import annotations

import re

from app.services.awg_install import DEFAULT_PORT, _awg_params
from app.services.awg_masking_apply import (
    CPS_KEYS,
    H_MAX,
    ROTATED_KEYS,
    _render_new_config,
    generate_cps_params,
    generate_params,
    validate_params,
)
from app.services.cascade_persist import (
    CONTAINER_UP,
    EXIT_LOCK_COMMENT,
    _exit_lock_sh,
    _exit_restore_sh,
    _oneshot_unit,
)


H_RANGE = re.compile(r"^\d+-\d+$")


def _assert_h_ranges(params: dict[str, str]) -> None:
    bounds: list[tuple[int, int]] = []
    for key in ("H1", "H2", "H3", "H4"):
        raw = params[key]
        assert H_RANGE.fullmatch(raw), f"{key}={raw} не диапазон"
        lo, hi = (int(x) for x in raw.split("-"))
        assert lo < hi
        bounds.append((lo, hi))
    bounds.sort()
    for i in range(1, len(bounds)):
        assert bounds[i][0] > bounds[i - 1][1]


def test_generate_params_mask_uses_h_ranges() -> None:
    params = generate_params("mask")
    assert set(params) == set(ROTATED_KEYS)
    _assert_h_ranges(params)
    assert 7 <= int(params["Jc"]) <= 10
    assert 64 <= int(params["Jmin"]) <= int(params["Jmax"]) <= 1024
    assert int(params["S3"]) > 0
    assert int(params["S4"]) > 0
    assert not validate_params(params)
    for key in ("H1", "H2", "H3", "H4"):
        hi = int(params[key].split("-")[1])
        assert hi <= H_MAX


def test_generate_params_amnezia_matches_app() -> None:
    for _ in range(20):
        params = generate_params("amnezia")
        assert set(params) == set(ROTATED_KEYS)
        assert params["Jmin"] == "10"
        assert params["Jmax"] == "50"
        assert 4 <= int(params["Jc"]) <= 6
        assert 15 <= int(params["S1"]) <= 149
        assert 15 <= int(params["S2"]) <= 149
        assert 1 <= int(params["S3"]) <= 63
        assert 1 <= int(params["S4"]) <= 19
        _assert_h_ranges(params)
        assert not validate_params(params)


def test_generate_params_without_cps_by_default() -> None:
    params = generate_params("balance")
    for key in CPS_KEYS:
        assert key not in params


def test_generate_cps_params_shape() -> None:
    cps = generate_cps_params()
    assert set(cps) == set(CPS_KEYS)
    for key, value in cps.items():
        assert "<b 0x" in value
    params = generate_params("balance", include_cps=True)
    assert not validate_params(params)
    for key in CPS_KEYS:
        assert params[key].startswith("<b")


def test_validate_params_rejects_single_h() -> None:
    params = generate_params("speed")
    params["H1"] = "123456"
    errors = validate_params(params)
    assert any("диапазон" in e for e in errors)


def test_validate_params_rejects_bad_cps() -> None:
    params = generate_params("balance", include_cps=True)
    params["I1"] = "not-a-tag"
    errors = validate_params(params)
    assert any("I1" in e for e in errors)


def test_render_new_config_inserts_i_keys() -> None:
    src = """[Interface]
PrivateKey = abc
ListenPort = 51820
Jc = 3
Jmin = 64
Jmax = 256
S1 = 20
S2 = 30
S3 = 10
S4 = 8
H1 = 100000-200000
H2 = 300000-400000
H3 = 500000-600000
H4 = 700000-800000
"""
    params = generate_params("mask", include_cps=True)
    out = _render_new_config(src, params)
    for key in ROTATED_KEYS:
        assert f"{key} = {params[key]}" in out
    for key in CPS_KEYS:
        assert f"{key} = {params[key]}" in out
    assert out.index("ListenPort") < out.index("I1 =")


def test_awg_install_params_match_amnezia_app() -> None:
    assert DEFAULT_PORT == 55424
    mapped = _awg_params(True)
    assert mapped["$JUNK_PACKET_MIN_SIZE"] == "10"
    assert mapped["$JUNK_PACKET_MAX_SIZE"] == "50"
    assert 4 <= int(mapped["$JUNK_PACKET_COUNT"]) <= 6
    assert "-" in mapped["$INIT_PACKET_MAGIC_HEADER"]
    assert int(mapped["$COOKIE_REPLY_PACKET_JUNK_SIZE"]) > 0
    for key in (
        "$INIT_PACKET_MAGIC_HEADER",
        "$RESPONSE_PACKET_MAGIC_HEADER",
        "$UNDERLOAD_PACKET_MAGIC_HEADER",
        "$TRANSPORT_PACKET_MAGIC_HEADER",
    ):
        hi = int(mapped[key].split("-")[1])
        assert hi <= H_MAX
    legacy = _awg_params(False)
    assert "-" not in legacy["$INIT_PACKET_MAGIC_HEADER"]
    assert legacy["$COOKIE_REPLY_PACKET_JUNK_SIZE"] == "0"


def test_exit_lock_script_drops_foreign_udp() -> None:
    script = _exit_lock_sh(44332, "203.0.113.10", add=True)
    assert "203.0.113.10" in script
    assert "--dport 44332" in script
    assert EXIT_LOCK_COMMENT in script
    assert "-j DROP" in script
    skip = _exit_lock_sh(44332, "", add=True)
    assert "LOCK_SKIP_NO_IP" in skip


def test_cascade_restore_units_wait_for_docker() -> None:
    restore = _exit_restore_sh("amnezia-awg", 44332, "172.17.0.2", "203.0.113.10")
    assert CONTAINER_UP in restore
    assert "systemctl restart" in restore
    unit = _oneshot_unit("/opt/utmka/cascade/restore.sh")
    assert "After=docker.service" in unit
    assert "ExecStart=/opt/utmka/cascade/restore.sh" in unit
