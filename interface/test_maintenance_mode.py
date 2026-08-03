from __future__ import annotations

import http.client
import importlib.machinery
import importlib.util
from pathlib import Path
import socket
import sqlite3
import sys
import threading
from types import ModuleType

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = REPO_ROOT / "packaging" / "libexec" / "potato-maintenance-server"
CONTROL_PATH = REPO_ROOT / "packaging" / "libexec" / "potato-maintenancectl"
SIGNUP_DRAIN_PATH = REPO_ROOT / "hermes-lite" / "scripts" / "signup_drain_gate.py"


def _load_script(name: str, path: Path) -> ModuleType:
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def maintenance_server() -> ModuleType:
    return _load_script("test_potato_maintenance_server", SERVER_PATH)


@pytest.fixture()
def running_server(maintenance_server: ModuleType):
    assets = maintenance_server.Assets(
        html=b"<!doctype html><title>Maintenance</title><p>test page</p>",
        logo=b"\x89PNG\r\n\x1a\n-test-logo",
    )
    server = maintenance_server.build_server(
        maintenance_server.ServerConfig(host="127.0.0.1", port=0),
        assets,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address, assets
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _request(address, method: str, path: str, body: bytes | None = None):
    connection = http.client.HTTPConnection(*address, timeout=2)
    try:
        connection.request(method, path, body=body)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def _assert_security_headers(headers: dict[str, str]) -> None:
    normalized = {name.lower(): value for name, value in headers.items()}
    assert normalized["cache-control"] == "no-store"
    assert normalized["retry-after"]
    assert "default-src 'none'" in normalized["content-security-policy"]
    assert normalized["x-content-type-options"] == "nosniff"
    assert normalized["x-potato-maintenance"] == "1"


@pytest.mark.parametrize("path", ["/", "/sessions/abc?draft=private", "/health"])
def test_page_routes_return_html_503_with_security_headers(
    running_server,
    path: str,
) -> None:
    address, assets = running_server
    status, headers, body = _request(address, "GET", path)

    assert status == 503
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert body == assets.html
    _assert_security_headers(headers)


def test_api_and_non_readonly_requests_return_json_503(running_server) -> None:
    address, _assets = running_server
    for method, path in (
        ("GET", "/api/session?token=secret"),
        ("POST", "/anything/private?token=secret"),
        ("BREW", "/unknown-method"),
    ):
        status, headers, body = _request(address, method, path, b"private-body")
        assert status == 503
        assert headers["Content-Type"] == "application/json; charset=utf-8"
        assert b"private-body" not in body
        assert b"secret" not in body
        _assert_security_headers(headers)


def test_malformed_request_still_gets_a_hardened_503(running_server) -> None:
    address, _assets = running_server
    with socket.create_connection(address, timeout=2) as connection:
        connection.sendall(b"MALFORMED\r\n\r\n")
        response = bytearray()
        while chunk := connection.recv(4096):
            response.extend(chunk)

    assert response.startswith(b"HTTP/1.0 503 Service Unavailable\r\n")
    assert b"Cache-Control: no-store\r\n" in response
    assert b"X-Potato-Maintenance: 1\r\n" in response
    assert bytes(response).endswith(
        b'{"detail":"Potato Agent is temporarily unavailable"}'
    )


def test_head_matches_get_headers_without_a_body(running_server) -> None:
    address, assets = running_server
    status, headers, body = _request(address, "HEAD", "/somewhere")

    assert status == 503
    assert int(headers["Content-Length"]) == len(assets.html)
    assert body == b""
    _assert_security_headers(headers)


def test_health_and_logo_are_the_only_successful_routes(running_server) -> None:
    address, assets = running_server
    status, headers, body = _request(address, "GET", "/_potato/maintenance-health")
    assert status == 200
    assert body == b'{"status":"ok"}'
    _assert_security_headers(headers)

    status, headers, body = _request(address, "GET", "/_potato/maintenance-logo.png")
    assert status == 200
    assert headers["Content-Type"] == "image/png"
    assert body == assets.logo
    _assert_security_headers(headers)


def test_path_traversal_never_reads_files(running_server, tmp_path: Path) -> None:
    address, assets = running_server
    secret = tmp_path / "secret.txt"
    secret.write_text("not-for-http", encoding="utf-8")

    status, _headers, body = _request(
        address,
        "GET",
        "/_potato/%2e%2e/%2e%2e/secret.txt",
    )

    assert status == 503
    assert body == assets.html
    assert secret.read_bytes() not in body


def test_config_rejects_unspecified_listener(
    maintenance_server: ModuleType,
) -> None:
    with pytest.raises(ValueError, match="specific IPv4"):
        maintenance_server.parse_config(
            b"[server]\nbind_host = 0.0.0.0\nbind_port = 3000\n"
        )


class FakeSystemd:
    def __init__(self, control: ModuleType) -> None:
        self.control = control
        self.states = {
            control.INTERFACE_UNIT: "active",
            control.MAINTENANCE_UNIT: "inactive",
        }
        self.fail_start: set[str] = set()
        self.unhealthy: set[str] = set()
        self.actions: list[tuple[str, str]] = []

    def systemctl(self, *arguments: str) -> bool:
        if arguments[:2] == ("is-active", "--quiet"):
            return self.states[arguments[2]] == "active"
        action, unit = arguments
        self.actions.append((action, unit))
        if action == "stop":
            self.states[unit] = "inactive"
            return True
        if action == "start":
            if unit in self.fail_start:
                return False
            self.states[unit] = "active"
            return True
        raise AssertionError(arguments)

    def probe(self, _host: str, _port: int, _path: str, *, maintenance: bool) -> bool:
        unit = self.control.MAINTENANCE_UNIT if maintenance else self.control.INTERFACE_UNIT
        return self.states[unit] == "active" and unit not in self.unhealthy

    def port_is_free(self, _host: str, _port: int) -> bool:
        return all(state != "active" for state in self.states.values())


@pytest.fixture()
def maintenance_control(monkeypatch: pytest.MonkeyPatch) -> tuple[ModuleType, FakeSystemd]:
    control = _load_script("test_potato_maintenance_control", CONTROL_PATH)
    fake = FakeSystemd(control)
    monkeypatch.setattr(control, "_systemctl", fake.systemctl)
    monkeypatch.setattr(control, "_probe", fake.probe)
    monkeypatch.setattr(control, "_port_is_free", fake.port_is_free)
    monkeypatch.setattr(control, "WAIT_ATTEMPTS", 1)
    return control, fake


def test_enter_failure_restores_interface(maintenance_control) -> None:
    control, fake = maintenance_control
    fake.fail_start.add(control.MAINTENANCE_UNIT)

    assert control.enter("127.0.0.1", 3000) is False
    assert fake.states[control.INTERFACE_UNIT] == "active"
    assert fake.states[control.MAINTENANCE_UNIT] == "inactive"
    assert ("start", control.INTERFACE_UNIT) in fake.actions


def test_leave_failure_restores_maintenance(maintenance_control) -> None:
    control, fake = maintenance_control
    fake.states[control.INTERFACE_UNIT] = "inactive"
    fake.states[control.MAINTENANCE_UNIT] = "active"
    fake.unhealthy.add(control.INTERFACE_UNIT)

    assert control.leave("127.0.0.1", 3000) is False
    assert fake.states[control.INTERFACE_UNIT] == "inactive"
    assert fake.states[control.MAINTENANCE_UNIT] == "active"
    assert ("start", control.MAINTENANCE_UNIT) in fake.actions


def test_repeated_switch_commands_are_idempotent(maintenance_control) -> None:
    control, fake = maintenance_control

    assert control.leave("127.0.0.1", 3000) is True
    assert fake.actions == []

    fake.states[control.INTERFACE_UNIT] = "inactive"
    fake.states[control.MAINTENANCE_UNIT] = "active"
    assert control.enter("127.0.0.1", 3000) is True
    assert fake.actions == []


def test_successful_enter_and_leave_keep_one_listener(maintenance_control) -> None:
    control, fake = maintenance_control

    assert control.enter("127.0.0.1", 3000) is True
    assert fake.states == {
        control.INTERFACE_UNIT: "inactive",
        control.MAINTENANCE_UNIT: "active",
    }

    assert control.leave("127.0.0.1", 3000) is True
    assert fake.states == {
        control.INTERFACE_UNIT: "active",
        control.MAINTENANCE_UNIT: "inactive",
    }


def test_status_reports_listener_and_unit_states(
    maintenance_control,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    control, fake = maintenance_control
    monkeypatch.setattr(control, "_unit_state", lambda unit: fake.states[unit])

    assert control.status("127.0.0.1", 3000) is True
    assert capsys.readouterr().out.splitlines() == [
        "address=127.0.0.1:3000",
        f"listener={control.INTERFACE_UNIT}",
        f"{control.INTERFACE_UNIT}=active",
        f"{control.MAINTENANCE_UNIT}=inactive",
    ]


def test_packaged_units_conflict_and_maintenance_is_self_contained() -> None:
    interface_unit = (
        REPO_ROOT / "packaging" / "systemd" / "potato-interface.service"
    ).read_text(encoding="utf-8")
    maintenance_unit = (
        REPO_ROOT / "packaging" / "systemd" / "potato-maintenance.service"
    ).read_text(encoding="utf-8")

    assert "Conflicts=potato-maintenance.service" in interface_unit
    assert "Conflicts=potato-interface.service" in maintenance_unit
    assert "DynamicUser=true" in maintenance_unit
    assert "ProtectSystem=strict" in maintenance_unit
    assert "ProtectHome=true" in maintenance_unit
    assert "NoNewPrivileges=true" in maintenance_unit
    assert "ReadWritePaths=" not in maintenance_unit
    assert "/srv/" not in maintenance_unit
    assert "/opt/interface-env" not in maintenance_unit
    assert "/usr/local/libexec/potato-maintenance-server" in maintenance_unit


def test_installer_reuses_logo_and_does_not_start_the_service() -> None:
    installer = (REPO_ROOT / "packaging" / "install_maintenance_mode.sh").read_text(
        encoding="utf-8"
    )

    assert "interface/static/LOGO.png" in installer
    assert "/usr/local/share/potato-agent/maintenance/logo.png" in installer
    assert "/usr/local/sbin/potato-maintenancectl" in installer
    assert "systemctl daemon-reload" in installer
    assert "maintenance source must be root-owned and immutable" in installer
    assert "systemctl start" not in installer
    assert "systemctl enable" not in installer


@pytest.fixture()
def signup_drain() -> ModuleType:
    return _load_script("test_signup_drain_gate", SIGNUP_DRAIN_PATH)


def _signup_db(tmp_path: Path) -> Path:
    path = tmp_path / "interface.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            create table signup_jobs (job_id text primary key, status text not null);
            create table session_live_state (session_id text, status text);
            create table runtime_leases (lease_id text, expires_at integer);
            create table turn_submission_receipts (receipt_id text, status text);
            """
        )
    return path


def test_signup_gate_ignores_active_agent_and_background_runtime(
    signup_drain: ModuleType,
    tmp_path: Path,
) -> None:
    path = _signup_db(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "insert into session_live_state values (?, ?)", ("session-1", "running")
        )
        connection.execute(
            "insert into runtime_leases values (?, ?)", ("lease-1", 9999999999)
        )
        connection.execute(
            "insert into turn_submission_receipts values (?, ?)",
            ("receipt-1", "pending"),
        )

    signup_drain.wait_for_signup_jobs(path, 0)
    signup_drain.assert_no_provisioning_jobs(path)


@pytest.mark.parametrize("status", ["pending", "provisioning"])
def test_signup_gate_blocks_active_signup_work(
    signup_drain: ModuleType,
    tmp_path: Path,
    status: str,
) -> None:
    path = _signup_db(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.execute("insert into signup_jobs values (?, ?)", ("job-1", status))

    with pytest.raises(signup_drain.SignupDrainError, match="did not drain"):
        signup_drain.wait_for_signup_jobs(path, 0)


def test_post_stop_signup_gate_allows_pending_but_rejects_provisioning(
    signup_drain: ModuleType,
    tmp_path: Path,
) -> None:
    path = _signup_db(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.execute("insert into signup_jobs values ('pending-job', 'pending')")
    signup_drain.assert_no_provisioning_jobs(path)

    with sqlite3.connect(path) as connection:
        connection.execute(
            "insert into signup_jobs values ('provisioning-job', 'provisioning')"
        )
    with pytest.raises(signup_drain.SignupDrainError, match="still active"):
        signup_drain.assert_no_provisioning_jobs(path)
