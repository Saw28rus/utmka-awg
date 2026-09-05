"""Страница маскировки: 2.0 и 3.1 — разные профили, 3.1 не крутим."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from app.schemas.awg_masking import (
    MASK_STATUS_INVALID,
    MASK_STATUS_STRONG,
    MASK_VERSION_AWG2,
    MASK_VERSION_AWG31,
    MaskingScore,
    MaskingState,
    MaskingWarning,
)
from app.services.awg_masking import (
    _client_counts,
    _collect_warnings,
    _detect_version,
    _overall_score,
    _score,
    _to_profile,
    _visible_warnings,
    classify_awg_container,
    is_awg31_params,
)


def test_classify_never_treats_31_as_20() -> None:
    assert classify_awg_container("amnezia-awg31") == "awg31"
    assert classify_awg_container("amnezia-awg2") == "awg2"
    assert classify_awg_container("amnezia-awg") == "awg2"
    assert classify_awg_container("amnezia-xray") is None
    assert classify_awg_container("utmka-panel") is None
    assert classify_awg_container("amnezia-awg31-backup") == "awg31"


def test_detect_version_31_by_header_or_h124() -> None:
    assert (
        _detect_version(
            {
                "S3": "12",
                "S4": "12",
                "HeaderProtectionKey": "abc",
                "H1": "1",
                "H2": "2",
                "H3": "3",
                "H4": "4",
            }
        )
        == MASK_VERSION_AWG31
    )
    assert _detect_version({"H1": "1", "H2": "2", "H3": "3", "H4": "4", "S3": "12", "S4": "12"}) == MASK_VERSION_AWG31
    assert _detect_version({"S3": "10", "S4": "8", "H1": "100-200", "H2": "300-400"}) == MASK_VERSION_AWG2
    assert is_awg31_params({"HeaderProtectionKey": "k"})
    assert not is_awg31_params({"S3": "10", "S4": "8", "H1": "100-200"})


def _awg2(**kwargs) -> MaskingState:
    data = dict(
        version=MASK_VERSION_AWG2,
        listen_port=39547,
        jc="5",
        jmin="10",
        jmax="50",
        s1="20",
        s2="30",
        s3="10",
        s4="8",
        h1="100000-200000",
        h2="300000-400000",
        h3="500000-600000",
        h4="700000-800000",
        h_is_ranges=True,
        header_protection=False,
        random_trailers=None,
    )
    data.update(kwargs)
    return MaskingState(**data)


def _awg31(**kwargs) -> MaskingState:
    data = dict(
        version=MASK_VERSION_AWG31,
        listen_port=55423,
        jc="5",
        jmin="10",
        jmax="50",
        s1="12",
        s2="13",
        s3="14",
        s4="15",
        h1="1",
        h2="2",
        h3="3",
        h4="4",
        h_is_ranges=False,
        header_protection=True,
        random_trailers="off",
    )
    data.update(kwargs)
    return MaskingState(**data)


def test_collect_warnings_31_does_not_flag_h_single() -> None:
    warnings = _collect_warnings(_awg31())
    assert "h_single" not in {w.code for w in warnings}
    assert not warnings


def test_collect_warnings_31_trailers_and_hp() -> None:
    codes = {w.code for w in _collect_warnings(_awg31(random_trailers="on"))}
    assert "trailers_on" in codes
    codes = {w.code for w in _collect_warnings(_awg31(header_protection=False))}
    assert "no_header_protection" in codes


def test_score_31_strong_and_invalid() -> None:
    ok = _awg31()
    assert _score(ok, _collect_warnings(ok)).status == MASK_STATUS_STRONG
    broken = _awg31(random_trailers="on")
    assert _score(broken, _collect_warnings(broken)).status == MASK_STATUS_INVALID


def test_profile_31_read_only_20_can_rotate() -> None:
    s31 = _awg31()
    p31 = _to_profile("awg31", s31, _score(s31, []), [], 4)
    assert p31.can_rotate is False
    assert p31.clients_total == 4
    assert p31.header_protection is True

    s2 = _awg2()
    p2 = _to_profile("awg2", s2, _score(s2, []), [], 7)
    assert p2.can_rotate is True
    assert p2.clients_total == 7
    assert p2.header_protection is None


def test_visible_warnings_hide_internals() -> None:
    raw = [
        MaskingWarning(level="warning", code="h_single", message="H"),
        MaskingWarning(level="warning", code="amnezia_legacy_port", message="39547"),
        MaskingWarning(level="info", code="j_amnezia", message="J"),
        MaskingWarning(level="danger", code="trailers_on", message="trailers"),
    ]
    shown = _visible_warnings(raw)
    assert [w.code for w in shown] == ["trailers_on"]


def test_overall_score_worst_wins() -> None:
    strong = MaskingScore(status=MASK_STATUS_STRONG, label="В норме")
    bad = MaskingScore(status=MASK_STATUS_INVALID, label="Ошибка")
    s2 = _awg2()
    s31 = _awg31()
    profiles = [
        _to_profile("awg2", s2, strong, [], 1),
        _to_profile("awg31", s31, bad, [], 1),
    ]
    assert _overall_score(profiles).status == MASK_STATUS_INVALID


def test_client_counts_split_20_and_31() -> None:
    items = [
        SimpleNamespace(protocol="awg2"),
        SimpleNamespace(protocol="awg"),
        SimpleNamespace(protocol="awg31"),
        SimpleNamespace(protocol="awg31"),
        SimpleNamespace(protocol="xray"),
    ]
    with patch("app.services.awg_masking.client_store.list_all", return_value=items):
        counts = _client_counts("srv-1")
    assert counts["awg2"] == 2
    assert counts["awg31"] == 2
