from __future__ import annotations

import base64
from copy import deepcopy
import json
import sqlite3
from types import SimpleNamespace

import pytest
import yaml

from configure_model_catalog import update_option
from interface.model_catalog import ModelCatalogError, ROUTE_MARKER, public_catalog, resolve_route, validate_catalog
from interface.model_options import model_options_from_catalog


@pytest.fixture
def catalog():
    return {
        "schema_version": 2, "catalog_signing_key": "SIGNING_SECRET_" + "s" * 64,
        "primary_option_id": "deep", "default_option_id": "fast",
        "backends": {"main": {"model": "gpt-current", "base_url": "https://PRIVATE_API.example/v1",
                               "api_key": "PRIVATE_UPSTREAM_KEY", "api_mode": "codex_responses"}},
        "options": {
            "deep": {"display_name": "Deep", "backend": "main", "reasoning_effort": "xhigh", "context_length": 500000},
            "fast": {"display_name": "Fast", "backend": "main", "reasoning_effort": "medium", "context_length": 128000},
        },
    }


def assert_public(value):
    body = json.dumps(value)
    for secret in ("PRIVATE_API", "PRIVATE_UPSTREAM_KEY", "SIGNING_SECRET", "base_url", "api_key", "catalog_signing_key"):
        assert secret not in body


def test_one_catalog_drives_labels_defaults_and_distinct_snapshots(catalog):
    view = public_catalog(catalog)
    assert_public(view)
    options = model_options_from_catalog(view)
    assert options.primary.id == "deep"
    assert options.new_user_default.id == "fast"
    assert options.get("primary") is None
    assert options.get("gpt-6-sol") is None
    deep, fast = options.options
    assert deep.model == fast.model == "gpt-current"
    assert deep.route != fast.route
    assert deep.config_revision != fast.config_revision
    for option in options.options:
        assert_public(option.to_public(is_primary=False))
        encoded = option.route.split(ROUTE_MARKER)[1].split(".")[0]
        assert_public(json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))))


def test_admitted_route_survives_model_settings_and_key_changes_and_restart(catalog):
    old = public_catalog(catalog)["options"][0]
    changed = deepcopy(catalog)
    changed["backends"]["main"].update(model="gpt-next", api_key="ROTATED_SECRET")
    changed["options"]["deep"].update(display_name="Research", reasoning_effort="high", context_length=256000)
    # No in-process cache is needed: reconstruct from the serialized new file.
    reloaded = yaml.safe_load(yaml.safe_dump(changed))
    snapshot, backend = resolve_route(reloaded, old["route"])
    assert snapshot["model"] == "gpt-current"
    assert snapshot["reasoning_effort"] == "xhigh"
    assert snapshot["context_length"] == 500000
    assert backend["api_key"] == "ROTATED_SECRET"
    new = public_catalog(reloaded)["options"][0]
    assert new["model"] == "gpt-next"
    assert new["display_name"] == "Research"
    assert new["config_revision"] != old["config_revision"]


@pytest.mark.parametrize("mutation", ["body", "signature", "prefix", "endpoint", "backend_removed", "option_removed"])
def test_snapshot_tampering_and_retirement_fail_closed(catalog, mutation):
    route = public_catalog(catalog)["options"][0]["route"]
    if mutation == "body":
        prefix, token = route.split(ROUTE_MARKER)
        encoded, signature = token.split(".")
        body = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        body["model"] = "unauthorized-model"
        route = prefix + ROUTE_MARKER + base64.urlsafe_b64encode(json.dumps(body).encode()).decode().rstrip("=") + "." + signature
    elif mutation == "signature":
        route = route[:-1] + ("0" if route[-1] != "0" else "1")
    elif mutation == "prefix":
        route = "unauthorized" + route
    elif mutation == "endpoint":
        catalog["backends"]["main"]["base_url"] = "https://other.example/v1"
    elif mutation == "backend_removed":
        catalog["backends"]["replacement"] = catalog["backends"].pop("main")
        for option in catalog["options"].values():
            option["backend"] = "replacement"
    elif mutation == "option_removed":
        del catalog["options"]["deep"]
        catalog["primary_option_id"] = "fast"
    with pytest.raises(ModelCatalogError):
        resolve_route(catalog, route)


def test_update_one_option_does_not_change_shared_backend_or_admitted_turn(catalog):
    old = public_catalog(catalog)["options"][1]
    updated = update_option(catalog, "fast", model="gpt-new", reasoning_effort="high")
    assert list(updated["options"]) == ["deep", "fast"]
    assert resolve_route(updated, "deep")[0]["model"] == "gpt-current"
    assert resolve_route(updated, "fast")[0]["model"] == "gpt-new"
    assert resolve_route(updated, old["route"])[0]["model"] == "gpt-current"




def test_proxy_sends_frozen_model_and_effort_and_records_no_private_data(catalog, tmp_path, monkeypatch):
    from interface.test_model_proxy import _client, _usage_rows
    client, proxy = _client(tmp_path, monkeypatch)
    route = public_catalog(catalog)["options"][0]["route"]
    catalog["backends"]["main"]["model"] = "gpt-next"
    (tmp_path / "model_proxy.yaml").write_text(yaml.safe_dump(catalog))
    captured = []

    class Response:
        status_code = 200
        headers = {"content-type": "application/json"}
        async def aiter_bytes(self):
            yield b'{"usage":{"input_tokens":7,"output_tokens":3}}'
        async def aclose(self):
            pass

    class Client:
        def __init__(self, **kwargs):
            pass
        def build_request(self, method, url, *, content, headers, params):
            captured.append((url, json.loads(content), headers))
            return SimpleNamespace()
        async def send(self, request, *, stream):
            return Response()
        async def aclose(self):
            pass

    monkeypatch.setattr(proxy.httpx, "AsyncClient", Client)
    headers = {"authorization": "Bearer pmp_alice_0123456789abcdefghijklmnopqrstuvwxyz"}
    for model in (route, "deep"):
        response = client.post("/v1/responses", headers=headers, json={"model": model, "input": "hello", "reasoning": {"effort": "low"}})
        assert response.status_code == 200, response.text
        assert_public(response.json())
    assert [item[1]["model"] for item in captured] == ["gpt-current", "gpt-next"]
    assert all(item[1]["reasoning"]["effort"] == "xhigh" for item in captured)
    assert all(item[2]["authorization"] == "Bearer PRIVATE_UPSTREAM_KEY" for item in captured)
    rows = _usage_rows(tmp_path)
    assert [row["upstream_model"] for row in rows] == ["gpt-current", "gpt-next"]
    assert rows[0]["config_revision"] != rows[1]["config_revision"]
    assert all(row["route_model"] == "deep" for row in rows)
    assert_public(rows)
    assert_public(client.get("/v1/models", headers=headers).json())
    assert client.get("/v1/models/" + route, headers=headers).status_code == 200


def test_session_selection_and_history_are_independent(catalog, tmp_path):
    from interface.auth_db import ensure_auth_db
    from interface.session_model_store import get_session_model_state, record_model_run, set_session_model
    path = tmp_path / "interface.db"
    ensure_auth_db(path)
    old = set_session_model("user", "chat", "deep", path)
    current = get_session_model_state("user", "chat", path)
    assert current["model_id"] == "deep" and current["model_revision"] == old["model_revision"]
    option = public_catalog(catalog)["options"][0]
    snapshot = {**option, "upstream_model": option["model"]}
    record_model_run("user", "chat", "run-1", snapshot, path)
    record_model_run("user", "chat", "run-1", snapshot, path)
    with pytest.raises(ValueError):
        record_model_run("user", "chat", "run-1", {**snapshot, "upstream_model": "different"}, path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT upstream_model FROM session_model_runs").fetchall() == [("gpt-current",)]
        assert ROUTE_MARKER not in "\n".join(db.iterdump())


def test_privileged_catalog_output_never_contains_endpoints_or_credentials(catalog, tmp_path, monkeypatch, capsys):
    from interface import privileged_helper
    config_path = tmp_path / "model_proxy.yaml"
    config_path.write_text(yaml.safe_dump(catalog))
    config_path.chmod(0o600)
    monkeypatch.setenv("POTATO_MODEL_PROXY_CONFIG_PATH", str(config_path))
    monkeypatch.setattr(privileged_helper, "require_root", lambda: None)
    monkeypatch.setattr(privileged_helper, "require_binary", lambda _: None)
    monkeypatch.setattr(privileged_helper.sys, "argv", ["helper", "get-model-catalog"])
    assert privileged_helper.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["catalog"]["options"][0]["model"] == "gpt-current"
    assert_public(result)
    config_path.write_text("api_key: [PRIVATE_UPSTREAM_KEY\n")
    assert privileged_helper.main() == 1
    assert_public(json.loads(capsys.readouterr().out))
    def broken_loader():
        raise RuntimeError("PRIVATE_API PRIVATE_UPSTREAM_KEY SIGNING_SECRET")
    monkeypatch.setattr("interface.model_catalog.load_public_catalog", broken_loader)
    assert privileged_helper.main() == 1
    assert_public(json.loads(capsys.readouterr().out))


def test_catalog_publish_rolls_back_both_files(catalog, tmp_path, monkeypatch):
    import configure_model_catalog as command
    mapping_path, proxy_path = tmp_path / "users_mapping.yaml", tmp_path / "model_proxy.yaml"
    original_mapping = {"hermes": {"model_catalog": True}, "users": []}
    mapping_path.write_text(yaml.safe_dump(original_mapping))
    proxy_path.write_text(yaml.safe_dump(catalog))
    proxy_path.chmod(0o600)
    updated = update_option(catalog, "deep", model="gpt-next")
    real_write = command.write_mapping
    calls = []
    def fail_once(path, value):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("simulated disk failure")
        return real_write(path, value)
    monkeypatch.setattr(command, "write_mapping", fail_once)
    with pytest.raises(OSError):
        command.apply_catalog(mapping_path, proxy_path, original_mapping, updated)
    assert yaml.safe_load(proxy_path.read_text()) == catalog
    assert yaml.safe_load(mapping_path.read_text()) == original_mapping
    backup = next(tmp_path.glob("model-catalog-backup-*"))
    assert backup.stat().st_mode & 0o777 == 0o700
    assert all(item.stat().st_mode & 0o777 == 0o600 for item in backup.iterdir())






def test_catalog_helper_failure_uses_safe_model_configuration_error(monkeypatch):
    from interface.model_options import ModelOptionsError, normalize_model_options
    from interface.privileged_client import PrivilegedClient, PrivilegedClientError
    monkeypatch.setenv("INTERFACE_FORCE_PRIVILEGED_HELPER", "1")

    def unavailable(_self):
        raise PrivilegedClientError("private diagnostic must not escape")

    monkeypatch.setattr(PrivilegedClient, "get_model_catalog", unavailable)
    with pytest.raises(ModelOptionsError, match="^Model catalog is unavailable$"):
        normalize_model_options({"hermes": {"model_catalog": True}})


@pytest.mark.parametrize("route", ["gpt-5.6-sol", "gpt-6-sol", "primary", "gpt-current"])
def test_old_routes_and_bare_upstream_names_are_rejected(catalog, route):
    with pytest.raises(ModelCatalogError, match="not available"):
        resolve_route(catalog, route)
    options = model_options_from_catalog(public_catalog(catalog))
    assert options.get(route) is None


@pytest.mark.parametrize("field", ["legacy_routes", "legacy_ids"])
def test_retired_option_metadata_is_rejected_without_echoing_values(catalog, field):
    catalog["options"]["deep"][field] = ["PRIVATE_OLD_ROUTE"]
    with pytest.raises(ModelCatalogError, match="^Unsupported catalog option fields$"):
        validate_catalog(catalog)


@pytest.mark.parametrize("route", ["gpt-5.6-sol", "gpt-6-sol", "gpt-current"])
def test_proxy_refuses_retired_routes_without_contacting_upstream(catalog, tmp_path, monkeypatch, route):
    from interface.test_model_proxy import _client
    client, proxy = _client(tmp_path, monkeypatch)
    (tmp_path / "model_proxy.yaml").write_text(yaml.safe_dump(catalog))
    def forbid_upstream(**_):
        raise AssertionError("Retired routes must not reach the upstream")
    monkeypatch.setattr(proxy.httpx, "AsyncClient", forbid_upstream)
    headers = {"authorization": "Bearer pmp_alice_0123456789abcdefghijklmnopqrstuvwxyz"}
    assert {item["id"] for item in client.get("/v1/models", headers=headers).json()["data"]} == {"deep", "fast"}
    assert client.get("/v1/models/" + route, headers=headers).status_code == 503
    for endpoint in ("/v1/chat/completions", "/v1/responses"):
        response = client.post(endpoint, headers=headers, json={"model": route, "messages": []})
        assert response.status_code == 503
        assert_public(response.json())


@pytest.mark.parametrize("model,auxiliary,error", [
    ({"default": "fast"}, {"compression": {"context_length": 128000}}, None),
    ({"default": "deep"}, {"compression": {"model": "fast"}}, None),
    ({"default": "gpt-current"}, {}, "stable bootstrap"),
    ({"default": "gpt-5.6-sol"}, {}, "stable bootstrap"),
    ({"default": "deep", "model": "gpt-current"}, {}, "stable bootstrap"),
    ({"default": "fast"}, {"vision": {"model": "gpt-current"}}, "stable option IDs"),
])
def test_user_config_preflight_is_read_only(catalog, tmp_path, monkeypatch, model, auxiliary, error):
    import configure_model_catalog as command
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"model": model, "auxiliary": auxiliary, "display": {"compact": True}}))
    before = path.read_bytes()
    target = SimpleNamespace(hermes_home=tmp_path)
    monkeypatch.setattr(command.MappingStore, "load_targets", lambda _: [target])
    monkeypatch.setattr(command, "read_user_private_text", lambda _, file: file.read_text())
    if error:
        with pytest.raises(ModelCatalogError, match=error):
            command.validate_user_model_routes(catalog, tmp_path / "mapping.yaml")
    else:
        command.validate_user_model_routes(catalog, tmp_path / "mapping.yaml")
    assert path.read_bytes() == before


def test_installation_check_is_read_only_and_rejects_unmigrated_ids(catalog, tmp_path):
    from configure_model_catalog import validate_session_selections
    path = tmp_path / "interface.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE session_model_state (model_id TEXT)")
        conn.execute("INSERT INTO session_model_state VALUES ('primary')")
    before = path.read_bytes()
    with pytest.raises(ModelCatalogError, match="require migration"):
        validate_session_selections(catalog, path)
    assert path.read_bytes() == before
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE session_model_state SET model_id = 'deep'")
    validate_session_selections(catalog, path)


def test_fresh_catalog_initialization_does_not_need_legacy_files(catalog, tmp_path):
    from configure_model_catalog import apply_catalog
    mapping, proxy = tmp_path / "mapping.yaml", tmp_path / "proxy.yaml"
    apply_catalog(mapping, proxy, {}, catalog, initialize=True)
    assert yaml.safe_load(mapping.read_text()) == {"hermes": {"model_catalog": True}, "users": []}
    saved = yaml.safe_load(proxy.read_text())
    assert all("legacy_ids" not in option for option in saved["options"].values())
    assert resolve_route(saved, "deep")[0]["id"] == "deep"
    before = (mapping.read_bytes(), proxy.read_bytes())
    with pytest.raises(ModelCatalogError, match="already exists"):
        apply_catalog(mapping, proxy, {}, catalog, initialize=True)
    assert (mapping.read_bytes(), proxy.read_bytes()) == before


def test_initialization_write_failure_removes_partial_new_configuration(catalog, tmp_path, monkeypatch):
    import configure_model_catalog as command
    mapping, proxy = tmp_path / "mapping.yaml", tmp_path / "proxy.yaml"
    def fail_write(*_):
        raise OSError("disk failure")
    monkeypatch.setattr(command, "write_mapping", fail_write)
    with pytest.raises(OSError):
        command.apply_catalog(mapping, proxy, {}, catalog, initialize=True)
    assert not mapping.exists() and not proxy.exists()


def test_initialize_preview_omits_private_data_and_writes_nothing(catalog, tmp_path, monkeypatch, capsys):
    import contextlib
    import configure_model_catalog as command
    source, mapping, proxy = (tmp_path / name for name in ("input.yaml", "mapping.yaml", "proxy.yaml"))
    catalog.pop("catalog_signing_key")
    source.write_text(yaml.safe_dump(catalog))
    source.chmod(0o600)
    monkeypatch.setattr(command, "require_root", lambda: None)
    monkeypatch.setattr(command, "mapping_lifecycle_lock", lambda **_: contextlib.nullcontext())
    argv = ["catalog", "--mapping", str(mapping), "--proxy-config", str(proxy), "--initialize-from", str(source)]
    monkeypatch.setattr("sys.argv", argv)
    assert command.main() == 0
    preview = capsys.readouterr().out
    assert "PRIVATE_" not in preview and ROUTE_MARKER not in preview
    assert not mapping.exists() and not proxy.exists()
    monkeypatch.setattr("sys.argv", argv + ["--apply"])
    assert command.main() == 0
    saved = yaml.safe_load(proxy.read_text())
    assert len(saved["catalog_signing_key"]) >= 43
    assert saved["schema_version"] == 2


def test_removed_privileged_model_commands_are_rejected():
    from interface.privileged_helper import build_parser
    for command in ("get-active-model", "patch-active-model"):
        with pytest.raises(SystemExit):
            build_parser().parse_args([command, "--username", "alice"])
