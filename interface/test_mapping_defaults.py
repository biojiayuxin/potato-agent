from __future__ import annotations

import pytest

from interface import mapping


def _valid_mapping_user(tmp_path, username: str, api_port: int) -> dict[str, object]:
    home_dir = tmp_path / username
    return {
        "username": username,
        "email": f"{username}@example.com",
        "linux_user": f"hmx_{username}",
        "home_dir": str(home_dir),
        "hermes_home": str(home_dir / ".hermes"),
        "workdir": str(home_dir),
        "api_port": api_port,
        "api_key": f"sk-{username}",
        "systemd_service": f"hermes-{username}.service",
        "model_proxy_token": f"pmp_{username}_0123456789abcdefghijklmnopqrstuvwxyz",
    }


def test_upsert_user_mapping_entry_uses_configured_linux_home_base(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(mapping, "DEFAULT_LINUX_HOME_BASE", tmp_path / "homes")
    monkeypatch.setattr(mapping, "select_next_port", lambda config: 9000)
    config = {"start_port": 9000}

    entry = mapping.upsert_user_mapping_entry(
        config,
        username="test2",
        email="test2@example.com",
        display_name="Test 2",
        api_key="sk-test",
    )

    expected_home = (tmp_path / "homes" / "hmx_test2").resolve()
    assert entry["linux_user"] == "hmx_test2"
    assert entry["home_dir"] == str(expected_home)
    assert entry["hermes_home"] == str(expected_home / ".hermes")
    assert entry["workdir"] == str(expected_home)
    assert entry["model_proxy_token"].startswith("pmp_")
    assert len(entry["model_proxy_token"]) >= 40

    original_token = entry["model_proxy_token"]
    mapping.upsert_user_mapping_entry(
        config,
        username="test2",
        email="updated@example.com",
        display_name="Updated",
    )
    assert entry["model_proxy_token"] == original_token


def test_ensure_model_proxy_tokens_fills_missing_and_duplicate_values() -> None:
    duplicate = "pmp_shared_0123456789abcdefghijklmnopqrstuvwxyz"
    config = {
        "users": [
            {"username": "alice"},
            {"username": "bob", "model_proxy_token": duplicate},
            {"username": "carol", "model_proxy_token": duplicate},
        ]
    }

    generated = mapping.ensure_model_proxy_tokens(config)
    tokens = [user["model_proxy_token"] for user in config["users"]]

    assert generated == 3
    assert len(tokens) == len(set(tokens))
    assert all(token.startswith("pmp_") and len(token) >= 40 for token in tokens)
    assert duplicate not in tokens


def test_ensure_unique_user_api_keys_rotates_all_shared_and_placeholder_keys() -> None:
    shared = "legacy-shared-user-api-key"
    config = {
        "users": [
            {"username": "alice", "api_key": shared},
            {"username": "bob", "api_key": shared},
            {"username": "carol", "api_key": "${LEGACY_USER_API_KEY}"},
            {"username": "dave", "api_key": "unique-user-api-key"},
        ]
    }

    generated = mapping.ensure_unique_user_api_keys(config)
    keys = [user["api_key"] for user in config["users"]]

    assert generated == 3
    assert len(keys) == len(set(keys))
    assert shared not in keys
    assert "${LEGACY_USER_API_KEY}" not in keys
    assert keys[-1] == "unique-user-api-key"
    assert all(len(key) >= 40 for key in keys[:3])


def test_build_target_defaults_missing_home_fields_to_configured_home_base(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(mapping, "DEFAULT_LINUX_HOME_BASE", tmp_path / "homes")
    mapping_path = tmp_path / "users_mapping.yaml"
    mapping_path.write_text(
        "users:\n"
        "  - username: alice\n"
        "    email: alice@example.com\n"
        "    linux_user: hmx_alice\n"
        "    api_port: 9001\n"
        "    api_key: sk-alice\n"
        "    model_proxy_token: pmp_alice_0123456789abcdefghijklmnopqrstuvwxyz\n",
        encoding="utf-8",
    )

    target = mapping.MappingStore(mapping_path).get_target_by_username("alice")

    assert target is not None
    expected_home = (tmp_path / "homes" / "hmx_alice").resolve()
    assert target.home_dir == expected_home
    assert target.hermes_home == expected_home / ".hermes"
    assert target.workdir == expected_home
    assert target.runtime_profile_path == mapping.Path(
        "/opt/potato-hermes-lite/current/config/runtime-profile.yaml"
    )


def test_build_target_propagates_runtime_profile_path(tmp_path) -> None:
    mapping_path = tmp_path / "users_mapping.yaml"
    mapping_path.write_text(
        "hermes:\n"
        "  runtime_profile_path: /opt/potato/releases/r1/config/runtime-profile.yaml\n"
        "  browser_cdp_url: ws://127.0.0.1:9222/devtools/browser/local\n"
        "users:\n"
        "  - username: alice\n"
        "    email: alice@example.com\n"
        "    linux_user: hmx_alice\n"
        "    api_port: 9001\n"
        "    api_key: sk-alice\n"
        "    model_proxy_token: pmp_alice_0123456789abcdefghijklmnopqrstuvwxyz\n",
        encoding="utf-8",
    )

    target = mapping.MappingStore(mapping_path).get_target_by_username("alice")

    assert target is not None
    assert target.runtime_profile_path == mapping.Path(
        "/opt/potato/releases/r1/config/runtime-profile.yaml"
    )
    assert (
        target.browser_cdp_url
        == "ws://127.0.0.1:9222/devtools/browser/local"
    )


@pytest.mark.parametrize(
    ("field_name", "duplicate_value"),
    [
        ("username", "alice"),
        ("email", "alice@example.com"),
        ("linux_user", "hmx_alice"),
        ("home_dir", "alice"),
        ("hermes_home", "alice/.hermes"),
        ("workdir", "alice"),
        ("systemd_service", "hermes-alice.service"),
        ("api_key", "sk-alice"),
        ("model_proxy_token", "pmp_alice_0123456789abcdefghijklmnopqrstuvwxyz"),
    ],
)
def test_mapping_store_rejects_shared_user_boundaries(
    tmp_path, field_name: str, duplicate_value: str
) -> None:
    alice = _valid_mapping_user(tmp_path, "alice", 9001)
    bob = _valid_mapping_user(tmp_path, "bob", 9002)
    if field_name in {"home_dir", "hermes_home", "workdir"}:
        bob[field_name] = str(tmp_path / duplicate_value)
    else:
        bob[field_name] = duplicate_value
    mapping_path = tmp_path / "users_mapping.yaml"
    mapping.write_mapping(mapping_path, {"users": [alice, bob]})

    with pytest.raises(RuntimeError, match=field_name) as exc_info:
        mapping.MappingStore(mapping_path).load_targets()

    assert str(alice["model_proxy_token"]) not in str(exc_info.value)
    if field_name == "api_key":
        assert str(alice["api_key"]) not in str(exc_info.value)


def test_mapping_store_rejects_shared_api_endpoint_case_insensitively(tmp_path) -> None:
    alice = _valid_mapping_user(tmp_path, "alice", 9001)
    bob = _valid_mapping_user(tmp_path, "bob", 9001)
    alice["api_server_host"] = "LOCALHOST"
    bob["api_server_host"] = "localhost"
    mapping_path = tmp_path / "users_mapping.yaml"
    mapping.write_mapping(mapping_path, {"users": [alice, bob]})

    with pytest.raises(RuntimeError, match="api_endpoint"):
        mapping.MappingStore(mapping_path).load_targets()


@pytest.mark.parametrize(
    "invalid_user",
    [
        "not-an-object",
        {"username": "alice", "api_port": 9001, "api_key": "sk-alice"},
        {
            "username": "alice",
            "api_port": 9001,
            "model_proxy_token": "pmp_alice_0123456789abcdefghijklmnopqrstuvwxyz",
        },
    ],
)
def test_mapping_store_fails_closed_for_invalid_user_entries(
    tmp_path, invalid_user
) -> None:
    mapping_path = tmp_path / "users_mapping.yaml"
    mapping.write_mapping(mapping_path, {"users": [invalid_user]})

    with pytest.raises(RuntimeError):
        mapping.MappingStore(mapping_path).load_targets()


def test_resolve_target_treats_mapping_username_as_authoritative(tmp_path) -> None:
    alice = _valid_mapping_user(tmp_path, "alice", 9001)
    bob = _valid_mapping_user(tmp_path, "bob", 9002)
    mapping_path = tmp_path / "users_mapping.yaml"
    mapping.write_mapping(mapping_path, {"users": [alice, bob]})
    store = mapping.MappingStore(mapping_path)

    target = store.resolve_target(
        mapping_username="bob",
        email="alice@example.com",
        username="alice",
    )

    assert target is not None
    assert target.username == "bob"
    assert (
        store.resolve_target(
            mapping_username="missing",
            email="alice@example.com",
            username="alice",
        )
        is None
    )
