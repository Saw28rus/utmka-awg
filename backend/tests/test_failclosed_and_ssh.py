"""Fail-closed каскада, честный split-health, SSH-отпечаток, интерфейсы WAN/AMN."""

from __future__ import annotations

from unittest.mock import MagicMock

import paramiko
import pytest

from app.services.cascade_apply import (
    _entry_host_udp_nat,
    _entry_up_script,
    _failclosed_up_script,
)
from app.services.cascade_split import (
    evaluate_split_health,
    merge_split_health,
    split_health,
)
from app.ssh.exec import (
    HostKeyMismatchError,
    _FingerprintPolicy,
    fingerprints_equal,
    key_fingerprint,
)
from app.services.transit_allocator import profile_for_slot


def _up_script() -> str:
    return _entry_up_script("10.8.1.0/24", profile_for_slot(0))


def test_entry_up_puts_failclosed_before_tunnel_bounce() -> None:
    text = _up_script()
    drop_at = text.index("utmka-failclosed")
    down_at = text.index("awg-quick down")
    assert drop_at < down_at


def test_entry_up_fills_routes_before_adding_rule() -> None:
    text = _up_script()
    assert "ip route flush table" not in text
    assert "ip rule del" not in text
    default_at = text.index("ip route replace default")
    blackhole_at = text.index("ip route replace blackhole")
    rule_at = text.rindex("ip rule add")
    assert default_at < rule_at
    assert blackhole_at < rule_at


def test_entry_up_keeps_blackhole_and_transit_accept() -> None:
    text = _up_script()
    assert "utmka-fc-transit" in text
    assert "utmka-fc-split" in text
    assert "blackhole default metric 100" in text


def test_failclosed_snippet_is_idempotent() -> None:
    text = _failclosed_up_script("10.8.1.0/24", "utmka-cas0", "7770")
    assert "utmka-failclosed" in text
    assert "|| iptables" in text


def test_entry_host_nat_detects_wan_and_amn() -> None:
    text = _entry_host_udp_nat("172.29.172.10", "203.0.113.10", 51822, "198.51.100.7", 51821)
    assert "ip route get" in text
    assert '-o "$WAN"' in text
    assert '-i "$WAN"' in text
    assert '-o "$AMN"' in text
    assert '-i "$AMN"' in text
    assert "-o eth0" not in text
    assert "-i eth0" not in text
    assert "-o amn0" not in text


def test_split_health_script_checks_mangle_with_source() -> None:
    captured: dict[str, str] = {}

    def fake_run(ssh, script, timeout=30):  # noqa: ANN001
        captured["script"] = script

        class _Res:
            stdout = "ru=1 ru2=1 foreign=0 rule=1 mangle=1"

        return _Res()

    import app.services.cascade_split as mod

    original = mod.run_script
    mod.run_script = fake_run  # type: ignore[assignment]
    try:
        health = split_health(object(), 42, "10.8.1.0/24")
    finally:
        mod.run_script = original
    script = captured["script"]
    assert "mangle=" in script
    assert "-s 10.8.1.0/24" in script
    assert health["ok"] is True
    assert health["mangle_present"] is True


def test_evaluate_health_false_when_structural_fails() -> None:
    health = evaluate_split_health(
        ru_in_set=True,
        foreign_excluded=True,
        rule_present=True,
        mangle_present=False,
    )
    assert health["ok"] is False


def test_evaluate_health_false_on_egress_mismatch() -> None:
    health = evaluate_split_health(
        ru_in_set=True,
        foreign_excluded=True,
        rule_present=True,
        mangle_present=True,
        ru_egress="198.51.100.7",
        foreign_egress="198.51.100.7",
        entry_ip="203.0.113.10",
        exit_ip="198.51.100.7",
    )
    assert health["egress_mismatch"] is True
    assert health["ok"] is False
    assert health["egress_confirmed"] is False


def test_evaluate_health_confirms_live_egress() -> None:
    health = evaluate_split_health(
        ru_in_set=True,
        foreign_excluded=True,
        rule_present=True,
        mangle_present=True,
        ru_egress="203.0.113.10",
        foreign_egress="198.51.100.7",
        entry_ip="203.0.113.10",
        exit_ip="198.51.100.7",
    )
    assert health["ok"] is True
    assert health["egress_confirmed"] is True


def test_merge_health_requires_every_protocol() -> None:
    ok = evaluate_split_health(
        ru_in_set=True, foreign_excluded=True, rule_present=True, mangle_present=True
    )
    bad = evaluate_split_health(
        ru_in_set=False, foreign_excluded=True, rule_present=True, mangle_present=True
    )
    merged = merge_split_health([ok, bad])
    assert merged["ok"] is False
    assert merged["ru_in_set"] is False


def test_fingerprint_policy_allows_unknown_then_pins() -> None:
    key = paramiko.RSAKey.generate(1024)
    policy = _FingerprintPolicy(None)
    client = MagicMock()
    policy.missing_host_key(client, "203.0.113.10", key)
    assert policy.seen == key_fingerprint(key)
    client.get_host_keys().add.assert_called_once()


def test_fingerprint_policy_rejects_changed_key() -> None:
    key = paramiko.RSAKey.generate(1024)
    policy = _FingerprintPolicy("SHA256:not-this-key")
    client = MagicMock()
    with pytest.raises(HostKeyMismatchError) as exc:
        policy.missing_host_key(client, "203.0.113.10", key)
    assert exc.value.host == "203.0.113.10"
    assert "изменился" in str(exc.value)


def test_fingerprints_equal_trims() -> None:
    assert fingerprints_equal(" SHA256:abc ", "SHA256:abc")
    assert not fingerprints_equal("SHA256:abc", "SHA256:xyz")
    assert not fingerprints_equal(None, "SHA256:abc")
