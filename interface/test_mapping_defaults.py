from __future__ import annotations

from interface import mapping


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
        "    api_key: sk-alice\n",
        encoding="utf-8",
    )

    target = mapping.MappingStore(mapping_path).get_target_by_username("alice")

    assert target is not None
    expected_home = (tmp_path / "homes" / "hmx_alice").resolve()
    assert target.home_dir == expected_home
    assert target.hermes_home == expected_home / ".hermes"
    assert target.workdir == expected_home
