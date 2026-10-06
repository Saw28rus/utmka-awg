"""ICMP stealth: close echo-request only, keep PMTU ICMP."""

from app.services.icmp_stealth import _build_apply_script, _build_disable_script, server_cascade_role


def test_apply_script_ignores_echo_not_all_icmp() -> None:
    script = _build_apply_script()
    assert "net.ipv4.icmp_echo_ignore_all = 1" in script
    assert "UTMKA_ICMP_STEALTH_OK" in script
    assert "--icmp-type echo-request" in script
    assert "utmka-icmp-stealth.service" in script
    assert "-p icmp -j DROP" not in script


def test_disable_script_restores_echo() -> None:
    script = _build_disable_script()
    assert "icmp_echo_ignore_all=0" in script
    assert "UTMKA_ICMP_STEALTH_OFF" in script
    assert "99-utmka-icmp-stealth.conf" in script


def test_cascade_role_unknown_is_standalone() -> None:
    assert server_cascade_role("no-such-server") == "standalone"
