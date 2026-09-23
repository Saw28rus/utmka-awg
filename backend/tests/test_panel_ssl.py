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


def test_panel_host_ssl_script_skips_xray_stream() -> None:
    from app.services.panel_ssl import _build_install_script

    script = _build_install_script(
        domain="2.59.161.189.sslip.io",
        email="",
        backup_dir="/opt/utmka/ssl-backup/t",
        move_xray=False,
        public_ip="2.59.161.189",
        reserve_xray=False,
    )
    assert "libnginx-mod-stream" not in script
    assert "listen 8443" not in script
    assert "amnezia-xray" not in script
    assert "UTMKA_SSL_OK" in script
    assert "/etc/nginx/sites-available/utmka-panel" in script


def test_vpn_node_ssl_script_reserves_xray_stream() -> None:
    from app.services.panel_ssl import _build_install_script

    script = _build_install_script(
        domain="panel.example.com",
        email="",
        backup_dir="/opt/utmka/ssl-backup/t",
        move_xray=False,
        public_ip="1.2.3.4",
        reserve_xray=True,
    )
    assert "libnginx-mod-stream" in script
    assert "listen 8443" in script
    assert "utmka-xray.conf" in script


def test_panel_host_hidden_from_server_list() -> None:
    from app.services.server_store import ServerStore

    assert ServerStore._hidden({"id": "__panel__", "kind": "panel_host"})
    assert ServerStore._hidden({"id": "__panel__"})
    assert ServerStore._hidden({"id": "abc", "kind": "panel_host"})
    assert not ServerStore._hidden({"id": "abc", "host": "1.2.3.4"})


def test_chat_on_panel_host_listens_443() -> None:
    from app.services.chat_domain import _build_install_script

    script = _build_install_script(
        domain="chat.2.59.161.189.sslip.io",
        backup_dir="/opt/utmka/chat-ssl-backup/t",
        passthrough=False,
    )
    assert "utmka-xray.conf" not in script
    assert "8444" not in script
    assert "UTMKA_CHAT_OK" in script
