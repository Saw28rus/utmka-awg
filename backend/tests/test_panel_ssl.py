"""Let's Encrypt: понятная ошибка при 500/JWS и повтор certbot."""

from app.services.panel_ssl import certbot_issue_script, transient_acme_hint


def test_transient_acme_hint_jws() -> None:
    hint = transient_acme_hint(
        "nginx: configuration file /etc/nginx/nginx.conf test is successful\n"
        "An unexpected error occurred:\nUnable to validate JWS\n"
    )
    assert hint is not None
    assert "Let's Encrypt" in hint
    assert "sslip.io" in hint


def test_transient_acme_hint_busy() -> None:
    hint = transient_acme_hint('{"type": "urn:ietf:params:acme:error:rateLimited", "detail": "Service busy; retry later."}')
    assert hint is not None
    assert "минуты" in hint or "Let's Encrypt" in hint


def test_transient_acme_hint_unrelated() -> None:
    assert transient_acme_hint("DNS does not resolve") is None


def test_certbot_issue_script_retries_webroot_not_standalone() -> None:
    script = certbot_issue_script("155.212.246.237.sslip.io", "--register-unsafely-without-email")
    assert "--webroot" in script
    assert "--standalone" not in script
    assert "попытка" in script
    assert "UTMKA_CERTBOT_FAIL" in script
    assert "155.212.246.237.sslip.io" in script
