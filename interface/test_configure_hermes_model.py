from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

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
    assert data["hermes"]["model"]["context_length"] == 1050000


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
        "install_user_runtime_files",
        lambda config, target: calls.append(("install", target.systemd_service)),
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
