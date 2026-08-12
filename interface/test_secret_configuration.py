from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from fastapi import Response

from interface import mailer, secret_config


SECRET_ENVIRONMENT_NAMES = (
    "CREDENTIALS_DIRECTORY",
    "INTERFACE_ALLOW_INSECURE_HTTP",
    "INTERFACE_BIND_HOST",
    "INTERFACE_ENVIRONMENT",
    "INTERFACE_RESEND_API_KEY",
    "INTERFACE_RESEND_API_KEY_FILE",
    "INTERFACE_SESSION_COOKIE_SECURE",
    "INTERFACE_SESSION_SECRET",
    "INTERFACE_SESSION_SECRET_FILE",
)


def _clear_secret_environment(monkeypatch) -> None:
    for name in SECRET_ENVIRONMENT_NAMES:
        monkeypatch.delenv(name, raising=False)


def _write_private_secret(path: Path, value: str) -> None:
    path.write_text(f"{value}\n", encoding="utf-8")
    path.chmod(0o600)


def test_session_secret_loads_from_explicit_private_file(
    tmp_path, monkeypatch
) -> None:
    _clear_secret_environment(monkeypatch)
    secret_path = tmp_path / "session-secret"
    _write_private_secret(secret_path, "s" * 43)
    monkeypatch.setenv("INTERFACE_ENVIRONMENT", "production")
    monkeypatch.setenv("INTERFACE_SESSION_SECRET_FILE", str(secret_path))

    assert secret_config.load_session_secret() == "s" * 43


def test_session_secret_loads_from_systemd_credential(tmp_path, monkeypatch) -> None:
    _clear_secret_environment(monkeypatch)
    secret_path = tmp_path / "interface-session-secret"
    _write_private_secret(secret_path, "c" * 43)
    monkeypatch.setenv("INTERFACE_ENVIRONMENT", "production")
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))

    assert secret_config.load_session_secret() == "c" * 43


def test_session_secret_accepts_systemd_idmapped_modes(
    tmp_path, monkeypatch
) -> None:
    _clear_secret_environment(monkeypatch)
    credential_dir = tmp_path / "credentials"
    credential_dir.mkdir()
    secret_path = credential_dir / "interface-session-secret"
    secret_path.write_text("c" * 43, encoding="utf-8")
    secret_path.chmod(0o440)
    credential_dir.chmod(0o550)
    monkeypatch.setenv("INTERFACE_ENVIRONMENT", "production")
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(credential_dir))

    assert secret_config.load_session_secret() == "c" * 43


def test_systemd_credential_rejects_other_readable_file(
    tmp_path, monkeypatch
) -> None:
    _clear_secret_environment(monkeypatch)
    credential_dir = tmp_path / "credentials"
    credential_dir.mkdir()
    secret_path = credential_dir / "interface-session-secret"
    secret_path.write_text("c" * 43, encoding="utf-8")
    secret_path.chmod(0o444)
    credential_dir.chmod(0o550)
    monkeypatch.setenv("INTERFACE_ENVIRONMENT", "production")
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(credential_dir))

    with pytest.raises(secret_config.SecretConfigurationError, match="group or other"):
        secret_config.load_session_secret()


def test_systemd_credential_rejects_other_accessible_directory(
    tmp_path, monkeypatch
) -> None:
    _clear_secret_environment(monkeypatch)
    credential_dir = tmp_path / "credentials"
    credential_dir.mkdir()
    secret_path = credential_dir / "interface-session-secret"
    secret_path.write_text("c" * 43, encoding="utf-8")
    secret_path.chmod(0o440)
    credential_dir.chmod(0o555)
    monkeypatch.setenv("INTERFACE_ENVIRONMENT", "production")
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(credential_dir))

    with pytest.raises(
        secret_config.SecretConfigurationError,
        match="CREDENTIALS_DIRECTORY.*other users",
    ):
        secret_config.load_session_secret()


def test_production_session_secret_is_required(monkeypatch) -> None:
    _clear_secret_environment(monkeypatch)
    monkeypatch.setenv("INTERFACE_ENVIRONMENT", "production")

    with pytest.raises(secret_config.SecretConfigurationError, match="required"):
        secret_config.load_session_secret()


def test_production_session_secret_rejects_short_file_value(
    tmp_path, monkeypatch
) -> None:
    _clear_secret_environment(monkeypatch)
    secret_path = tmp_path / "session-secret"
    _write_private_secret(secret_path, "too-short")
    monkeypatch.setenv("INTERFACE_ENVIRONMENT", "production")
    monkeypatch.setenv("INTERFACE_SESSION_SECRET_FILE", str(secret_path))

    with pytest.raises(secret_config.SecretConfigurationError, match="32 bytes"):
        secret_config.load_session_secret()


def test_production_rejects_secret_in_process_environment(monkeypatch) -> None:
    _clear_secret_environment(monkeypatch)
    monkeypatch.setenv("INTERFACE_ENVIRONMENT", "production")
    monkeypatch.setenv("INTERFACE_SESSION_SECRET", "s" * 43)

    with pytest.raises(
        secret_config.SecretConfigurationError, match="must not be stored directly"
    ):
        secret_config.load_session_secret()


def test_secret_loader_rejects_conflicting_sources(tmp_path, monkeypatch) -> None:
    _clear_secret_environment(monkeypatch)
    secret_path = tmp_path / "session-secret"
    _write_private_secret(secret_path, "f" * 43)
    monkeypatch.setenv("INTERFACE_SESSION_SECRET", "e" * 43)
    monkeypatch.setenv("INTERFACE_SESSION_SECRET_FILE", str(secret_path))

    with pytest.raises(secret_config.SecretConfigurationError, match="only one source"):
        secret_config.load_session_secret()


def test_secret_loader_rejects_file_readable_by_other_users(
    tmp_path, monkeypatch
) -> None:
    _clear_secret_environment(monkeypatch)
    secret_path = tmp_path / "session-secret"
    secret_path.write_text("secret\n", encoding="utf-8")
    secret_path.chmod(0o644)
    monkeypatch.setenv("INTERFACE_SESSION_SECRET_FILE", str(secret_path))

    with pytest.raises(secret_config.SecretConfigurationError, match="group or other"):
        secret_config.load_session_secret()


def test_secret_loader_rejects_file_owned_by_another_user(
    tmp_path, monkeypatch
) -> None:
    _clear_secret_environment(monkeypatch)
    secret_path = tmp_path / "session-secret"
    _write_private_secret(secret_path, "s" * 43)
    file_uid = secret_path.stat().st_uid
    monkeypatch.setattr(secret_config.os, "geteuid", lambda: file_uid + 1000)
    monkeypatch.setenv("INTERFACE_SESSION_SECRET_FILE", str(secret_path))

    with pytest.raises(
        secret_config.SecretConfigurationError,
        match="owned by root or the service user",
    ):
        secret_config.load_session_secret()


def test_mailer_loads_existing_resend_key_from_file(tmp_path, monkeypatch) -> None:
    _clear_secret_environment(monkeypatch)
    key_path = tmp_path / "resend-api-key"
    _write_private_secret(key_path, "re_existing_key")
    monkeypatch.setenv("INTERFACE_RESEND_API_KEY_FILE", str(key_path))
    monkeypatch.setenv("INTERFACE_MAIL_FROM", "Potato Agent <noreply@example.com>")

    settings = mailer.get_resend_settings()

    assert settings.api_key == "re_existing_key"
    assert settings.mail_from == "Potato Agent <noreply@example.com>"


def test_resend_error_body_is_not_propagated(monkeypatch) -> None:
    sentinel_key = "re_must_not_escape_from_upstream"
    sentinel_body = f"echoed Authorization: Bearer {sentinel_key}"

    class FakeAsyncClient:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            import httpx

            return httpx.Response(401, text=sentinel_body)

    monkeypatch.setattr(mailer.httpx, "AsyncClient", FakeAsyncClient)
    settings = mailer.ResendSettings(
        api_key=sentinel_key,
        mail_from="Potato Agent <noreply@example.com>",
    )

    with pytest.raises(mailer.MailerDeliveryError) as exc_info:
        asyncio.run(
            mailer.send_resend_email(
                email="alice@example.com",
                subject="Test",
                text="body",
                html="<p>body</p>",
                settings=settings,
            )
        )

    assert str(exc_info.value) == "Resend rejected email"
    assert sentinel_key not in str(exc_info.value)
    assert sentinel_body not in str(exc_info.value)


def test_secure_cookie_configuration_applies_to_set_and_clear(monkeypatch) -> None:
    import interface.app as app_mod

    monkeypatch.setattr(app_mod, "SESSION_COOKIE_SECURE", True)
    response = Response()
    app_mod._set_session_cookie(response, "token")
    app_mod._clear_session_cookie(response)

    cookie_headers = response.headers.getlist("set-cookie")
    assert len(cookie_headers) == 2
    assert all("Secure" in header for header in cookie_headers)
    assert all("HttpOnly" in header for header in cookie_headers)
    assert all("SameSite=lax" in header for header in cookie_headers)
    assert all("Path=/" in header for header in cookie_headers)


def test_insecure_http_cookie_keeps_non_transport_protections(monkeypatch) -> None:
    import interface.app as app_mod

    monkeypatch.setattr(app_mod, "SESSION_COOKIE_SECURE", False)
    response = Response()
    app_mod._set_session_cookie(response, "token")
    app_mod._clear_session_cookie(response)

    cookie_headers = response.headers.getlist("set-cookie")
    assert len(cookie_headers) == 2
    assert all("Secure" not in header for header in cookie_headers)
    assert all("HttpOnly" in header for header in cookie_headers)
    assert all("SameSite=lax" in header for header in cookie_headers)
    assert all("Path=/" in header for header in cookie_headers)


def test_production_secure_cookie_default_is_fail_safe(monkeypatch) -> None:
    _clear_secret_environment(monkeypatch)
    monkeypatch.setenv("INTERFACE_ENVIRONMENT", "production")
    monkeypatch.delenv("INTERFACE_SESSION_COOKIE_SECURE", raising=False)

    assert secret_config.load_session_cookie_secure()


def test_production_rejects_disabling_secure_cookie_without_opt_in(monkeypatch) -> None:
    _clear_secret_environment(monkeypatch)
    monkeypatch.setenv("INTERFACE_ENVIRONMENT", "production")
    monkeypatch.setenv("INTERFACE_SESSION_COOKIE_SECURE", "false")

    with pytest.raises(
        secret_config.SecretConfigurationError,
        match="INTERFACE_ALLOW_INSECURE_HTTP=true",
    ):
        secret_config.load_session_cookie_secure()


def test_production_allows_explicit_insecure_http_profile(monkeypatch) -> None:
    _clear_secret_environment(monkeypatch)
    monkeypatch.setenv("INTERFACE_ENVIRONMENT", "production")
    monkeypatch.setenv("INTERFACE_ALLOW_INSECURE_HTTP", "true")
    monkeypatch.setenv("INTERFACE_SESSION_COOKIE_SECURE", "false")

    assert not secret_config.load_session_cookie_secure()


def test_production_rejects_inconsistent_insecure_http_profile(monkeypatch) -> None:
    _clear_secret_environment(monkeypatch)
    monkeypatch.setenv("INTERFACE_ENVIRONMENT", "production")
    monkeypatch.setenv("INTERFACE_ALLOW_INSECURE_HTTP", "true")
    monkeypatch.setenv("INTERFACE_SESSION_COOKIE_SECURE", "true")

    with pytest.raises(
        secret_config.SecretConfigurationError,
        match="INTERFACE_SESSION_COOKIE_SECURE=false",
    ):
        secret_config.load_session_cookie_secure()


def test_production_rejects_invalid_insecure_http_boolean(monkeypatch) -> None:
    _clear_secret_environment(monkeypatch)
    monkeypatch.setenv("INTERFACE_ENVIRONMENT", "production")
    monkeypatch.setenv("INTERFACE_ALLOW_INSECURE_HTTP", "sometimes")

    with pytest.raises(
        secret_config.SecretConfigurationError,
        match="INTERFACE_ALLOW_INSECURE_HTTP must be a boolean",
    ):
        secret_config.load_session_cookie_secure()


def test_development_can_disable_secure_cookie(monkeypatch) -> None:
    _clear_secret_environment(monkeypatch)
    monkeypatch.setenv("INTERFACE_ENVIRONMENT", "development")
    monkeypatch.setenv("INTERFACE_SESSION_COOKIE_SECURE", "false")

    assert not secret_config.load_session_cookie_secure()


def test_interface_serve_defaults_to_production(monkeypatch) -> None:
    import interface.serve as serve

    called: dict[str, object] = {}
    _clear_secret_environment(monkeypatch)

    def fake_run(*args, **kwargs) -> None:
        called.update(kwargs)
        called["environment"] = os.environ.get("INTERFACE_ENVIRONMENT")

    monkeypatch.setattr(serve.uvicorn, "run", fake_run)

    serve.main(["--host", "127.0.0.1", "--port", "3001"])

    assert called["environment"] == "production"
    assert "INTERFACE_ENVIRONMENT" not in os.environ
    assert called["host"] == "127.0.0.1"
    assert called["port"] == 3001


def test_interface_serve_defaults_to_loopback(monkeypatch) -> None:
    import interface.serve as serve

    monkeypatch.delenv("INTERFACE_BIND_HOST", raising=False)
    args = serve.build_parser().parse_args([])

    assert args.host == "127.0.0.1"


def test_interface_serve_uses_site_bind_host(monkeypatch) -> None:
    import interface.serve as serve

    monkeypatch.setenv("INTERFACE_BIND_HOST", "192.0.2.10")
    args = serve.build_parser().parse_args([])

    assert args.host == "192.0.2.10"


def test_interface_serve_accepts_multiple_explicit_insecure_http_hosts(
    monkeypatch,
) -> None:
    import interface.serve as serve

    _clear_secret_environment(monkeypatch)
    monkeypatch.setenv("INTERFACE_ENVIRONMENT", "production")
    monkeypatch.setenv("INTERFACE_ALLOW_INSECURE_HTTP", "true")
    monkeypatch.setenv("INTERFACE_SESSION_COOKIE_SECURE", "false")

    assert serve._validate_listener("192.0.2.10,198.51.100.20") == (
        "192.0.2.10",
        "198.51.100.20",
    )


@pytest.mark.parametrize(
    "hosts",
    (
        "192.0.2.10,",
        "192.0.2.10,192.0.2.10",
        "192.0.2.10,127.0.0.1",
        "192.0.2.10,example.com",
    ),
)
def test_interface_serve_rejects_unsafe_multiple_insecure_http_hosts(
    monkeypatch, hosts: str
) -> None:
    import interface.serve as serve

    _clear_secret_environment(monkeypatch)
    monkeypatch.setenv("INTERFACE_ENVIRONMENT", "production")
    monkeypatch.setenv("INTERFACE_ALLOW_INSECURE_HTTP", "true")
    monkeypatch.setenv("INTERFACE_SESSION_COOKIE_SECURE", "false")

    with pytest.raises(secret_config.SecretConfigurationError):
        serve._validate_listener(hosts)


def test_interface_serve_binds_each_configured_host(monkeypatch) -> None:
    import interface.serve as serve

    bound_hosts: list[str] = []
    closed_hosts: list[str] = []
    observed_sockets: list[object] = []

    class FakeSocket:
        def __init__(self, host: str):
            self.host = host

        def close(self) -> None:
            closed_hosts.append(self.host)

    class FakeConfig:
        def __init__(self, *args, host: str, **kwargs):
            self.host = host

        def bind_socket(self):
            bound_hosts.append(self.host)
            return FakeSocket(self.host)

    class FakeServer:
        started = True

        def __init__(self, *, config):
            self.config = config

        def run(self, *, sockets):
            observed_sockets.extend(sockets)

    monkeypatch.setattr(serve.uvicorn, "Config", FakeConfig)
    monkeypatch.setattr(serve.uvicorn, "Server", FakeServer)

    serve._run_server(("192.0.2.10", "198.51.100.20"), 3000)

    assert bound_hosts == ["192.0.2.10", "198.51.100.20"]
    assert [value.host for value in observed_sockets] == bound_hosts
    assert closed_hosts == bound_hosts


def test_interface_serve_rejects_public_production_listener_without_opt_in(
    monkeypatch,
) -> None:
    import interface.serve as serve

    _clear_secret_environment(monkeypatch)
    monkeypatch.setenv("INTERFACE_ENVIRONMENT", "production")

    with pytest.raises(
        secret_config.SecretConfigurationError,
        match="INTERFACE_ALLOW_INSECURE_HTTP=true",
    ):
        serve.main(["--host", "0.0.0.0"])


def test_interface_serve_rejects_ipv6_wildcard_without_opt_in(monkeypatch) -> None:
    import interface.serve as serve

    _clear_secret_environment(monkeypatch)
    monkeypatch.setenv("INTERFACE_ENVIRONMENT", "production")

    with pytest.raises(
        secret_config.SecretConfigurationError,
        match="INTERFACE_ALLOW_INSECURE_HTTP=true",
    ):
        serve.main(["--host", "::"])


def test_interface_serve_accepts_explicit_insecure_http_profile(monkeypatch) -> None:
    import interface.serve as serve

    _clear_secret_environment(monkeypatch)
    monkeypatch.setenv("INTERFACE_ENVIRONMENT", "production")
    monkeypatch.setenv("INTERFACE_ALLOW_INSECURE_HTTP", "true")
    monkeypatch.setenv("INTERFACE_BIND_HOST", "192.0.2.10")
    monkeypatch.setenv("INTERFACE_SESSION_COOKIE_SECURE", "false")
    called: dict[str, object] = {}

    monkeypatch.setattr(
        serve.uvicorn,
        "run",
        lambda *args, **kwargs: called.update(kwargs),
    )

    serve.main(["--port", "3002"])

    assert called["host"] == "192.0.2.10"
    assert called["port"] == 3002


def test_interface_serve_rejects_loopback_in_insecure_http_profile(
    monkeypatch,
) -> None:
    import interface.serve as serve

    _clear_secret_environment(monkeypatch)
    monkeypatch.setenv("INTERFACE_ENVIRONMENT", "production")
    monkeypatch.setenv("INTERFACE_ALLOW_INSECURE_HTTP", "true")
    monkeypatch.setenv("INTERFACE_SESSION_COOKIE_SECURE", "false")

    with pytest.raises(
        secret_config.SecretConfigurationError,
        match="specific non-loopback IPv4 listener",
    ):
        serve.main(["--host", "127.0.0.1"])


def test_interface_serve_rejects_wildcard_in_insecure_http_profile(
    monkeypatch,
) -> None:
    import interface.serve as serve

    _clear_secret_environment(monkeypatch)
    monkeypatch.setenv("INTERFACE_ENVIRONMENT", "production")
    monkeypatch.setenv("INTERFACE_ALLOW_INSECURE_HTTP", "true")
    monkeypatch.setenv("INTERFACE_SESSION_COOKIE_SECURE", "false")

    with pytest.raises(
        secret_config.SecretConfigurationError,
        match="specific non-loopback IPv4 listener",
    ):
        serve.main(["--host", "0.0.0.0"])


def test_interface_serve_keeps_development_listener_behavior(monkeypatch) -> None:
    import interface.serve as serve

    _clear_secret_environment(monkeypatch)
    monkeypatch.setenv("INTERFACE_ENVIRONMENT", "development")
    called: dict[str, object] = {}
    monkeypatch.setattr(
        serve.uvicorn,
        "run",
        lambda *args, **kwargs: called.update(kwargs),
    )

    serve.main(["--host", "0.0.0.0"])

    assert called["host"] == "0.0.0.0"


def test_packaged_service_uses_credentials_and_production_guards() -> None:
    service_path = Path(__file__).parents[1] / "packaging/systemd/potato-interface.service"
    service = service_path.read_text(encoding="utf-8")

    assert "Environment=INTERFACE_ENVIRONMENT=production" in service
    assert "Environment=INTERFACE_ALLOW_INSECURE_HTTP=false" in service
    assert "Environment=INTERFACE_BIND_HOST=127.0.0.1" in service
    assert "Environment=INTERFACE_SESSION_COOKIE_SECURE=true" in service
    assert (
        "Environment=INTERFACE_PRIVILEGED_HELPER="
        "/usr/local/libexec/potato-agent-privileged-helper"
    ) in service
    assert "LoadCredential=interface-session-secret:" in service
    assert "LoadCredential=resend-api-key:" in service
    assert "Environment=INTERFACE_SESSION_SECRET=" not in service
    assert "Environment=INTERFACE_RESEND_API_KEY=" not in service
    assert "-m interface.serve --port 3000" in service
    assert "--host" not in service
    assert "PrivateTmp=yes" in service
    assert "ProtectProc=invisible" in service
    assert "ProcSubset=pid" in service
    assert "LimitCORE=0" in service
    assert "UMask=0077" in service
    assert "Wants=potato-model-proxy.service" in service
