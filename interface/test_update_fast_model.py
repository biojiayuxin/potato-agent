from __future__ import annotations

from contextlib import nullcontext
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
import yaml

import update_fast_model as migration


def configs():
    deep = {"id": "deep", "name": "Deep", "model": "gpt-5.6-sol"}
    fast = {
        "id": "gpt-5.6-terra", "name": "Fast", "model": "gpt-5.6-terra",
        "context_length": 1050000, "api_mode": "codex_responses",
        "reasoning_effort": "medium",
    }
    compression = {"model": "Deep", "context_length": 1050000, "api_mode": "codex_responses"}
    mapping = {
        "hermes": {
            "model_options": {"primary": "deep", "options": [deep, fast]},
            "config_overrides": {"auxiliary": {"compression": compression}},
        },
        "users": [],
    }
    proxy = {"models": [
        {**item, "base_url": "https://upstream.example/v1", "api_key": "private-test-key"}
        for item in (deep, fast)
    ]}
    return mapping, proxy


def test_update_preserves_every_other_config_field_and_is_idempotent():
    mapping, proxy = configs()
    original_mapping, original_proxy = deepcopy(mapping), deepcopy(proxy)
    updated_mapping, updated_proxy, _ = migration.prepare_update(mapping, proxy)
    for entry in (
        updated_mapping["hermes"]["model_options"]["options"][1],
        updated_proxy["models"][1],
    ):
        assert entry["model"] == "gpt-6-sol"
        assert entry["name"] == "gpt-6-sol"
        assert entry["reasoning_effort"] == "xhigh"
    expected_mapping, expected_proxy = deepcopy(mapping), deepcopy(proxy)
    for item in (
        expected_mapping["hermes"]["model_options"]["options"][1],
        expected_proxy["models"][1],
    ):
        item.update(name="gpt-6-sol", model="gpt-6-sol", reasoning_effort="xhigh")
    assert updated_mapping == expected_mapping
    assert updated_proxy == expected_proxy
    assert mapping == original_mapping
    assert proxy == original_proxy
    again_mapping, again_proxy, _ = migration.prepare_update(updated_mapping, updated_proxy)
    assert again_mapping == updated_mapping
    assert again_proxy == updated_proxy


def test_update_aligns_implicit_route_name_with_upstream_model():
    mapping, proxy = configs()
    del mapping["hermes"]["model_options"]["options"][1]["name"]
    del proxy["models"][1]["name"]
    updated_mapping, updated_proxy, _ = migration.prepare_update(mapping, proxy)
    assert updated_mapping["hermes"]["model_options"]["options"][1]["name"] == "gpt-6-sol"
    assert updated_proxy["models"][1]["name"] == "gpt-6-sol"


def test_update_corrects_already_upgraded_fast_users_with_legacy_route_name():
    mapping, proxy = configs()
    for item in (mapping["hermes"]["model_options"]["options"][1], proxy["models"][1]):
        item.update(id="fast", name="gpt-5.6-terra", model="gpt-6-sol", reasoning_effort="xhigh")
    updated_mapping, updated_proxy, fast = migration.prepare_update(mapping, proxy)
    config = {
        "model": {"default": "gpt-5.6-terra", "base_url": "http://127.0.0.1:8765/v1",
                  "api_mode": "codex_responses", "context_length": 500000},
        "agent": {"reasoning_effort": "xhigh"},
        "auxiliary": {"compression": {"context_length": 500000, "api_mode": "codex_responses"}},
    }
    updated = migration.patch_current_fast_user(config, fast, proxy_base_url="http://127.0.0.1:8765/v1")
    expected = deepcopy(config)
    expected["model"]["default"] = "gpt-6-sol"
    assert updated == expected
    new_fast = migration.normalize_model_options(updated_mapping).new_user_default
    assert new_fast.id == "fast"
    assert new_fast.name == new_fast.model == "gpt-6-sol"
    assert new_fast.matches_model_config(updated["model"], proxy_base_url="http://127.0.0.1:8765/v1")
    assert updated_proxy["models"][1]["name"] == updated["model"]["default"]


def test_update_refuses_route_name_collision():
    mapping, proxy = configs()
    proxy["models"][0]["name"] = "gpt-6-sol"
    with pytest.raises(migration.FastModelUpdateError, match="already in use"):
        migration.prepare_update(mapping, proxy)


def test_update_refuses_to_leave_explicit_compression_on_retired_route():
    mapping, proxy = configs()
    _, _, fast = migration.prepare_update(mapping, proxy)
    with pytest.raises(migration.FastModelUpdateError, match="retiring Fast route"):
        migration.check_retired_compression_route({"auxiliary": {"compression": {"model": "Fast"}}}, fast)
    migration.check_retired_compression_route({"auxiliary": {"compression": {"context_length": 500000}}}, fast)


def test_update_refuses_mismatched_proxy_route():
    mapping, proxy = configs()
    proxy["models"][1]["model"] = "unexpected"
    with pytest.raises(migration.FastModelUpdateError, match="do not agree"):
        migration.prepare_update(mapping, proxy)


@pytest.mark.parametrize("model", [None, "Fast", "gpt-5.6-terra"])
def test_update_detects_compression_that_would_change_with_fast(model):
    mapping, proxy = configs()
    _, _, fast = migration.prepare_update(mapping, proxy)
    with pytest.raises(migration.FastModelUpdateError, match="Compression"):
        migration.check_compression({"auxiliary": {"compression": {"model": model}}}, fast)


def test_current_fast_user_keeps_context_transport_and_compression():
    mapping, proxy = configs()
    _, _, fast = migration.prepare_update(mapping, proxy)
    config = {
        "model": {"default": "Fast", "provider": "custom", "api_mode": "codex_responses", "context_length": 1050000},
        "agent": {"reasoning_effort": "medium", "max_turns": 90},
        "auxiliary": {"compression": {"model": "Deep", "context_length": 400000}},
    }
    # Model config requires the local proxy URL to match.
    config["model"]["base_url"] = "http://127.0.0.1:8765/v1"
    updated = migration.patch_current_fast_user(config, fast, proxy_base_url="http://127.0.0.1:8765/v1")
    expected = deepcopy(config)
    expected["model"]["default"] = "gpt-6-sol"
    expected["agent"]["reasoning_effort"] = "xhigh"
    assert updated == expected
    config["model"]["default"] = "Deep"
    assert migration.patch_current_fast_user(config, fast, proxy_base_url="http://127.0.0.1:8765/v1") is config


@pytest.mark.parametrize("fail_write", [False, True])
@pytest.mark.parametrize("keep_compression_policy", [False, True])
def test_apply_backs_up_retains_fields_and_rolls_back_on_failure(tmp_path, monkeypatch, capsys, fail_write, keep_compression_policy):
    mapping, proxy = configs()
    if keep_compression_policy:
        mapping["hermes"]["config_overrides"]["auxiliary"]["compression"].pop("model")
    mapping_path, proxy_path = tmp_path / "mapping.yaml", tmp_path / "proxy.yaml"
    mapping_path.write_text(yaml.safe_dump(mapping))
    proxy_path.write_text(yaml.safe_dump(proxy))
    target = SimpleNamespace(username="alice", hermes_home=tmp_path / "alice")
    target.hermes_home.mkdir()
    user_path = target.hermes_home / "config.yaml"
    user_config = {
        "model": {"default": "Fast", "base_url": "http://127.0.0.1:8765/v1", "context_length": 900000},
        "agent": {"reasoning_effort": "medium"},
        "auxiliary": {"compression": {"model": "Deep", "context_length": 400000}},
    }
    if keep_compression_policy:
        user_config["auxiliary"]["compression"].pop("model")
    user_path.write_text(yaml.safe_dump(user_config))
    monkeypatch.setattr(migration.os, "geteuid", lambda: 0)
    monkeypatch.setattr(migration, "mapping_lifecycle_lock", lambda **kwargs: nullcontext())
    monkeypatch.setattr(migration, "acquire_user_lock", lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr(migration, "build_targets_from_config", lambda _: [target])
    monkeypatch.setattr(migration, "load_model_proxy_config", lambda path: yaml.safe_load(path.read_text()))
    monkeypatch.setattr(migration, "write_model_proxy_config", lambda path, data: path.write_text(yaml.safe_dump(data)))
    monkeypatch.setattr(migration, "write_mapping", lambda path, data: path.write_text(yaml.safe_dump(data)))
    monkeypatch.setattr(migration, "read_user_private_text", lambda target, path: path.read_text())
    failed = False
    def write_user(target, path, body):
        nonlocal failed
        if fail_write and not failed:
            failed = True
            raise OSError("test write failure")
        path.write_text(body)
    monkeypatch.setattr(migration, "write_user_private_text", write_user)
    argv = ["update_fast_model.py", "--mapping", str(mapping_path), "--proxy-config", str(proxy_path), "--apply"]
    if keep_compression_policy:
        argv.append("--keep-compression-policy")
    monkeypatch.setattr("sys.argv", argv)
    if fail_write:
        with pytest.raises(migration.FastModelUpdateError, match="restored"):
            migration.main()
        assert yaml.safe_load(mapping_path.read_text()) == mapping
        assert yaml.safe_load(proxy_path.read_text()) == proxy
        assert yaml.safe_load(user_path.read_text()) == user_config
    else:
        assert migration.main() == 0
        updated_mapping, updated_proxy, _ = migration.prepare_update(mapping, proxy)
        assert yaml.safe_load(mapping_path.read_text()) == updated_mapping
        assert yaml.safe_load(proxy_path.read_text()) == updated_proxy
        user_config["agent"]["reasoning_effort"] = "xhigh"
        user_config["model"]["default"] = "gpt-6-sol"
        assert yaml.safe_load(user_path.read_text()) == user_config
    backup = next(tmp_path.glob("fast-model-backup-*"))
    assert backup.stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in backup.iterdir())
    assert yaml.safe_load((backup / "model_proxy.yaml").read_text()) == proxy
    assert json.loads((backup / "users.json").read_text())[0]["path"] == str(user_path)
    assert "private-test-key" not in capsys.readouterr().out
