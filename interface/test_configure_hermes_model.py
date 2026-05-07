from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import yaml

import configure_hermes_model


REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_configure(mapping_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    return subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "configure_hermes_model.py"),
            "--mapping",
            str(mapping_path),
            *args,
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _base_args() -> list[str]:
    return [
        "--base-url",
        "https://primary.example/v1",
        "--model",
        "gpt-5.4",
        "--api-key",
        "sk-primary",
    ]


def _target(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        username="alice",
        email="alice@example.com",
        display_name="Alice",
        linux_user="hmx_alice",
        home_dir=tmp_path / "home",
        hermes_home=tmp_path / "home" / ".hermes",
        workdir=tmp_path / "home" / "work",
        api_server_host="127.0.0.1",
        api_port=8643,
        api_key="sk-user",
        api_server_model_name="Hermes",
        systemd_service="hermes-alice.service",
        extra_env={"OPENAI_API_KEY": "sk-primary"},
        config_overrides={},
    )


def _runtime_config() -> dict:
    return {
        "hermes": {
            "model": {
                "default": "gpt-5.4",
                "provider": "custom",
                "base_url": "https://primary.example/v1",
                "api_key": "sk-primary",
                "context_length": 1050000,
                "api_mode": "codex_responses",
            },
            "config_overrides": {
                "agent": {
                    "reasoning_effort": "xhigh",
                },
            },
            "fallback_providers": [
                {
                    "provider": "custom",
                    "model": "gpt-5.4-mini",
                    "base_url": "https://fallback.example/v1",
                    "api_key": "sk-fallback",
                }
            ],
        }
    }


def test_configure_hermes_model_writes_standard_fallback_providers() -> None:
    mapping_path = Path(tempfile.mkdtemp(prefix="potato-configure-model-test-")) / "users_mapping.yaml"

    result = _run_configure(
        mapping_path,
        *_base_args(),
        "--fallback-base-url",
        "https://fallback.example/v1",
        "--fallback-model",
        "gpt-5.4-mini",
        "--fallback-api-key",
        "sk-fallback",
    )

    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(mapping_path.read_text(encoding="utf-8"))
    hermes = data["hermes"]
    assert "fallback_model" not in hermes
    assert hermes["fallback_providers"] == [
        {
            "provider": "custom",
            "model": "gpt-5.4-mini",
            "base_url": "https://fallback.example/v1",
            "api_key": "sk-fallback",
        }
    ]


def test_configure_hermes_model_writes_context_length() -> None:
    mapping_path = Path(tempfile.mkdtemp(prefix="potato-configure-model-test-")) / "users_mapping.yaml"

    result = _run_configure(
        mapping_path,
        *_base_args(),
        "--context-length",
        "1,050,000",
    )

    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(mapping_path.read_text(encoding="utf-8"))
    hermes = data["hermes"]
    assert hermes["model"]["context_length"] == 1050000
    assert (
        hermes["config_overrides"]["auxiliary"]["compression"]["context_length"]
        == 1050000
    )
    assert hermes["model_options"]["primary"] == "primary"
    assert hermes["model_options"]["options"][0] == {
        "id": "primary",
        "name": "gpt-5.4",
        "provider": "custom",
        "model": "gpt-5.4",
        "base_url": "https://primary.example/v1",
        "api_key": "sk-primary",
        "context_length": 1050000,
        "api_mode": "codex_responses",
        "reasoning_effort": "xhigh",
    }


def test_configure_hermes_model_defaults_api_mode_and_reasoning_effort() -> None:
    mapping_path = Path(tempfile.mkdtemp(prefix="potato-configure-model-test-")) / "users_mapping.yaml"

    result = _run_configure(
        mapping_path,
        *_base_args(),
    )

    assert result.returncode == 0, result.stderr
    hermes = yaml.safe_load(mapping_path.read_text(encoding="utf-8"))["hermes"]
    assert hermes["model"]["api_mode"] == "codex_responses"
    assert hermes["config_overrides"]["agent"]["reasoning_effort"] == "xhigh"
    assert hermes["model_options"]["options"][0]["api_mode"] == "codex_responses"
    assert hermes["model_options"]["options"][0]["reasoning_effort"] == "xhigh"


def test_configure_hermes_model_allows_explicit_api_mode_and_reasoning_effort() -> None:
    mapping_path = Path(tempfile.mkdtemp(prefix="potato-configure-model-test-")) / "users_mapping.yaml"

    result = _run_configure(
        mapping_path,
        *_base_args(),
        "--api-mode",
        "chat_completions",
        "--reasoning-effort",
        "high",
    )

    assert result.returncode == 0, result.stderr
    hermes = yaml.safe_load(mapping_path.read_text(encoding="utf-8"))["hermes"]
    assert hermes["model"]["api_mode"] == "chat_completions"
    assert hermes["config_overrides"]["agent"]["reasoning_effort"] == "high"
    assert hermes["model_options"]["options"][0]["api_mode"] == "chat_completions"
    assert hermes["model_options"]["options"][0]["reasoning_effort"] == "high"


def test_configure_hermes_model_writes_optional_model_options() -> None:
    mapping_path = Path(tempfile.mkdtemp(prefix="potato-configure-model-test-")) / "users_mapping.yaml"

    result = _run_configure(
        mapping_path,
        *_base_args(),
        "--option",
        "id=fast,name=Fast,model=gpt-5.4-mini,base_url=https://fast.example/v1,api_key=sk-fast,context_length=500000",
        "--option",
        "id=deep,model=gpt-5.5,base_url=https://deep.example/v1,api_key=sk-deep,api_mode=chat_completions",
    )

    assert result.returncode == 0, result.stderr
    hermes = yaml.safe_load(mapping_path.read_text(encoding="utf-8"))["hermes"]
    assert hermes["model_options"] == {
        "primary": "primary",
        "options": [
            {
                "id": "primary",
                "name": "gpt-5.4",
                "provider": "custom",
                "model": "gpt-5.4",
                "base_url": "https://primary.example/v1",
                "api_key": "sk-primary",
                "api_mode": "codex_responses",
                "reasoning_effort": "xhigh",
            },
            {
                "id": "fast",
                "name": "Fast",
                "provider": "custom",
                "model": "gpt-5.4-mini",
                "base_url": "https://fast.example/v1",
                "api_key": "sk-fast",
                "context_length": 500000,
                "api_mode": "codex_responses",
                "reasoning_effort": "xhigh",
            },
            {
                "id": "deep",
                "name": "gpt-5.5",
                "provider": "custom",
                "model": "gpt-5.5",
                "base_url": "https://deep.example/v1",
                "api_key": "sk-deep",
                "api_mode": "chat_completions",
                "reasoning_effort": "xhigh",
            },
        ],
    }


def test_configure_hermes_model_rejects_too_many_optional_models() -> None:
    mapping_path = Path(tempfile.mkdtemp(prefix="potato-configure-model-test-")) / "users_mapping.yaml"

    result = _run_configure(
        mapping_path,
        *_base_args(),
        "--option",
        "id=one,model=one,base_url=https://one.example/v1,api_key=sk-one",
        "--option",
        "id=two,model=two,base_url=https://two.example/v1,api_key=sk-two",
        "--option",
        "id=three,model=three,base_url=https://three.example/v1,api_key=sk-three",
    )

    assert result.returncode == 1
    assert "--option may be provided at most twice" in result.stderr


def test_configure_hermes_model_rejects_duplicate_option_id() -> None:
    mapping_path = Path(tempfile.mkdtemp(prefix="potato-configure-model-test-")) / "users_mapping.yaml"

    result = _run_configure(
        mapping_path,
        *_base_args(),
        "--option",
        "id=primary,model=alt,base_url=https://alt.example/v1,api_key=sk-alt",
    )

    assert result.returncode == 1
    assert "Duplicate model option id" in result.stderr


def test_configure_hermes_model_rejects_invalid_context_length() -> None:
    mapping_path = Path(tempfile.mkdtemp(prefix="potato-configure-model-test-")) / "users_mapping.yaml"

    result = _run_configure(
        mapping_path,
        *_base_args(),
        "--context-length",
        "1050K",
    )

    assert result.returncode == 1
    assert "--context-length must be a plain positive integer" in result.stderr


def test_configure_hermes_model_migrates_legacy_fallback_model_list() -> None:
    mapping_path = Path(tempfile.mkdtemp(prefix="potato-configure-model-test-")) / "users_mapping.yaml"
    mapping_path.write_text(
        """
hermes:
  model:
    default: old-model
    provider: custom
    base_url: https://old-primary.example/v1
    api_key: sk-old-primary
  extra_env:
    OPENAI_API_KEY: sk-old-primary
  fallback_model:
    - provider: custom
      model: old-fallback
      base_url: https://old-fallback.example/v1
      api_key: sk-old-fallback
users: []
""".lstrip(),
        encoding="utf-8",
    )

    result = _run_configure(mapping_path, *_base_args())

    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(mapping_path.read_text(encoding="utf-8"))
    hermes = data["hermes"]
    assert "fallback_model" not in hermes
    assert hermes["fallback_providers"] == [
        {
            "provider": "custom",
            "model": "old-fallback",
            "base_url": "https://old-fallback.example/v1",
            "api_key": "sk-old-fallback",
        }
    ]


def test_configure_hermes_model_clear_fallback_removes_fallback_config() -> None:
    mapping_path = Path(tempfile.mkdtemp(prefix="potato-configure-model-test-")) / "users_mapping.yaml"
    mapping_path.write_text(
        """
hermes:
  model:
    default: old-model
    provider: custom
    base_url: https://old-primary.example/v1
    api_key: sk-old-primary
  extra_env:
    OPENAI_API_KEY: sk-old-primary
  fallback_providers:
    - provider: custom
      model: old-fallback
      base_url: https://old-fallback.example/v1
      api_key: sk-old-fallback
users: []
""".lstrip(),
        encoding="utf-8",
    )

    result = _run_configure(mapping_path, *_base_args(), "--clear-fallback")

    assert result.returncode == 0, result.stderr
    hermes = yaml.safe_load(mapping_path.read_text(encoding="utf-8"))["hermes"]
    assert "fallback_providers" not in hermes
    assert "fallback_model" not in hermes


def test_apply_user_runtime_patch_preserves_unmanaged_config(
    monkeypatch, tmp_path
) -> None:
    target = _target(tmp_path)
    target.hermes_home.mkdir(parents=True)
    config_path = target.hermes_home / "config.yaml"
    config_path.write_text(
        """
model:
  default: old-model
  provider: custom
  base_url: https://old-primary.example/v1
  api_key: sk-old-primary
  context_length: 800000
  api_mode: chat_completions
memory:
  enabled: true
tools:
  filesystem: true
terminal:
  backend: local
  timeout: 300
auxiliary:
  summarizer:
    model: custom-summary
  compression:
    context_length: 800000
fallback_providers:
  - provider: custom
    model: keep-fallback
    base_url: https://keep-fallback.example/v1
    api_key: sk-keep-fallback
""".lstrip(),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        configure_hermes_model.pwd,
        "getpwnam",
        lambda username: SimpleNamespace(pw_uid=123, pw_gid=456),
    )
    monkeypatch.setattr(
        configure_hermes_model,
        "_set_owner_and_mode",
        lambda path, uid, gid, mode: None,
    )

    configure_hermes_model.apply_user_runtime_model_patch(
        _runtime_config(),
        target,
        context_length=1050000,
        fallback_action=configure_hermes_model.FALLBACK_ACTION_PRESERVE,
    )

    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert data["model"] == {
        "default": "gpt-5.4",
        "provider": "custom",
        "base_url": "https://primary.example/v1",
        "api_key": "sk-primary",
        "api_mode": "codex_responses",
        "context_length": 1050000,
    }
    assert data["agent"] == {"reasoning_effort": "xhigh"}
    assert data["auxiliary"]["compression"]["context_length"] == 1050000
    assert data["auxiliary"]["summarizer"] == {"model": "custom-summary"}
    assert data["memory"] == {"enabled": True}
    assert data["tools"] == {"filesystem": True}
    assert data["terminal"] == {"backend": "local", "timeout": 300}
    assert data["fallback_providers"][0]["model"] == "keep-fallback"


def test_apply_user_runtime_patch_updates_only_openai_api_key_in_env(
    monkeypatch, tmp_path
) -> None:
    target = _target(tmp_path)
    target.hermes_home.mkdir(parents=True)
    env_path = target.hermes_home / ".env"
    env_path.write_text(
        "# keep this comment\nFOO=bar\nOPENAI_API_KEY=sk-old\n\nBAZ=qux\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        configure_hermes_model.pwd,
        "getpwnam",
        lambda username: SimpleNamespace(pw_uid=123, pw_gid=456),
    )
    monkeypatch.setattr(
        configure_hermes_model,
        "_set_owner_and_mode",
        lambda path, uid, gid, mode: None,
    )

    configure_hermes_model.apply_user_runtime_model_patch(
        _runtime_config(),
        target,
        context_length=None,
        fallback_action=configure_hermes_model.FALLBACK_ACTION_PRESERVE,
    )

    assert env_path.read_text(encoding="utf-8") == (
        "# keep this comment\nFOO=bar\nOPENAI_API_KEY=sk-primary\n\nBAZ=qux\n"
    )


def test_apply_user_runtime_patch_appends_openai_api_key_to_env(
    monkeypatch, tmp_path
) -> None:
    target = _target(tmp_path)
    target.hermes_home.mkdir(parents=True)
    env_path = target.hermes_home / ".env"
    env_path.write_text("# keep\nFOO=bar", encoding="utf-8")

    monkeypatch.setattr(
        configure_hermes_model.pwd,
        "getpwnam",
        lambda username: SimpleNamespace(pw_uid=123, pw_gid=456),
    )
    monkeypatch.setattr(
        configure_hermes_model,
        "_set_owner_and_mode",
        lambda path, uid, gid, mode: None,
    )

    configure_hermes_model.apply_user_runtime_model_patch(
        _runtime_config(),
        target,
        context_length=None,
        fallback_action=configure_hermes_model.FALLBACK_ACTION_PRESERVE,
    )

    assert env_path.read_text(encoding="utf-8") == (
        "# keep\nFOO=bar\nOPENAI_API_KEY=sk-primary\n"
    )


def test_apply_user_runtime_patch_preserves_fallback_when_unrequested(
    monkeypatch, tmp_path
) -> None:
    target = _target(tmp_path)
    target.hermes_home.mkdir(parents=True)
    config_path = target.hermes_home / "config.yaml"
    config_path.write_text(
        """
model:
  default: old-model
  provider: custom
  base_url: https://old.example/v1
  api_key: sk-old
fallback_providers:
  - provider: custom
    model: keep-fallback
    base_url: https://keep.example/v1
    api_key: sk-keep
""".lstrip(),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        configure_hermes_model.pwd,
        "getpwnam",
        lambda username: SimpleNamespace(pw_uid=123, pw_gid=456),
    )
    monkeypatch.setattr(
        configure_hermes_model,
        "_set_owner_and_mode",
        lambda path, uid, gid, mode: None,
    )

    configure_hermes_model.apply_user_runtime_model_patch(
        _runtime_config(),
        target,
        context_length=None,
        fallback_action=configure_hermes_model.FALLBACK_ACTION_PRESERVE,
    )

    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert data["fallback_providers"] == [
        {
            "provider": "custom",
            "model": "keep-fallback",
            "base_url": "https://keep.example/v1",
            "api_key": "sk-keep",
        }
    ]


def test_apply_user_runtime_patch_clear_fallback_only_removes_fallback(
    monkeypatch, tmp_path
) -> None:
    target = _target(tmp_path)
    target.hermes_home.mkdir(parents=True)
    config_path = target.hermes_home / "config.yaml"
    config_path.write_text(
        """
model:
  default: old-model
  provider: custom
  base_url: https://old.example/v1
  api_key: sk-old
memory:
  enabled: true
fallback_model:
  provider: custom
  model: legacy-fallback
fallback_providers:
  - provider: custom
    model: keep-fallback
    base_url: https://keep.example/v1
    api_key: sk-keep
""".lstrip(),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        configure_hermes_model.pwd,
        "getpwnam",
        lambda username: SimpleNamespace(pw_uid=123, pw_gid=456),
    )
    monkeypatch.setattr(
        configure_hermes_model,
        "_set_owner_and_mode",
        lambda path, uid, gid, mode: None,
    )

    configure_hermes_model.apply_user_runtime_model_patch(
        _runtime_config(),
        target,
        context_length=None,
        fallback_action=configure_hermes_model.FALLBACK_ACTION_CLEAR,
    )

    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert "fallback_providers" not in data
    assert "fallback_model" not in data
    assert data["memory"] == {"enabled": True}


def test_apply_model_config_to_users_does_not_wait_for_legacy_models_endpoint(
    monkeypatch, tmp_path
) -> None:
    mapping_path = tmp_path / "users_mapping.yaml"
    mapping_path.write_text(
        """
start_port: 8643
hermes:
  executable: /usr/local/bin/hermes
  api_server_host: 127.0.0.1
  api_server_model_name: Hermes
  model:
    default: gpt-5.5
    provider: custom
    base_url: https://primary.example/v1
    api_key: sk-primary
  extra_env:
    OPENAI_API_KEY: sk-primary
users:
  - username: alice
    email: alice@example.com
    display_name: Alice
    linux_user: hmx_alice
    home_dir: /home/hmx_alice
    hermes_home: /home/hmx_alice/.hermes
    workdir: /home/hmx_alice/work
    api_port: 8643
    api_key: sk-user
    systemd_service: hermes-alice.service
""".lstrip(),
        encoding="utf-8",
    )
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(
        configure_hermes_model, "confirm_apply_to_users", lambda *args, **kwargs: True
    )
    monkeypatch.setattr(configure_hermes_model, "require_root", lambda: None)
    monkeypatch.setattr(configure_hermes_model, "require_binary", lambda name: None)
    monkeypatch.setattr(
        configure_hermes_model,
        "apply_user_runtime_model_patch",
        lambda config, target, **kwargs: calls.append(
            ("install", target.systemd_service)
        ),
    )
    monkeypatch.setattr(configure_hermes_model, "is_service_active", lambda name: True)
    monkeypatch.setattr(
        configure_hermes_model,
        "restart_service",
        lambda name: calls.append(("restart", name)),
    )
    monkeypatch.setattr(
        configure_hermes_model,
        "wait_for_service_active",
        lambda name: calls.append(("wait-service", name)),
    )

    configure_hermes_model.apply_model_config_to_users(
        mapping_path,
        old_base_url="https://old.example/v1",
        new_base_url="https://primary.example/v1",
        old_model_name="old-model",
        new_model_name="gpt-5.5",
        old_context_length=None,
        new_context_length=1000000,
        old_api_key="sk-old",
        new_api_key="sk-primary",
        old_fallback_provider=None,
        new_fallback_provider=None,
    )

    assert calls == [
        ("install", "hermes-alice.service"),
        ("restart", "hermes-alice.service"),
        ("wait-service", "hermes-alice.service"),
    ]
