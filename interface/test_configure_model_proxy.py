from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import yaml

import configure_model_proxy


REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_configure(mapping_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    return subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "configure_model_proxy.py"),
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


def _secret_file(mapping_path: Path, name: str, value: str) -> Path:
    path = mapping_path.parent / name
    path.write_text(value + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def _base_args(mapping_path: Path) -> list[str]:
    key_path = _secret_file(mapping_path, "primary.key", "sk-primary")
    return [
        "--base-url",
        "https://primary.example/v1",
        "--model",
        "gpt-5.4",
        "--api-key-file",
        str(key_path),
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
        model_proxy_token="pmp_alice_0123456789abcdefghijklmnopqrstuvwxyz",
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


def test_configure_model_proxy_writes_fallback_to_proxy_only() -> None:
    mapping_path = Path(tempfile.mkdtemp(prefix="potato-configure-model-test-")) / "users_mapping.yaml"
    fallback_key = _secret_file(mapping_path, "fallback.key", "sk-fallback")

    result = _run_configure(
        mapping_path,
        *_base_args(mapping_path),
        "--fallback-base-url",
        "https://fallback.example/v1",
        "--fallback-model",
        "gpt-5.4-mini",
        "--fallback-api-key-file",
        str(fallback_key),
    )

    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(mapping_path.read_text(encoding="utf-8"))
    proxy = yaml.safe_load(mapping_path.with_name("model_proxy.yaml").read_text(encoding="utf-8"))
    hermes = data["hermes"]
    assert "fallback_model" not in hermes
    assert "fallback_providers" not in hermes
    assert proxy["models"][-1]["model"] == "gpt-5.4-mini"
    assert proxy["models"][-1]["api_key"] == "sk-fallback"
    assert "sk-fallback" not in mapping_path.read_text(encoding="utf-8")
    assert stat.S_IMODE(mapping_path.with_name("model_proxy.yaml").stat().st_mode) == 0o600


def test_configure_model_proxy_writes_context_length() -> None:
    mapping_path = Path(tempfile.mkdtemp(prefix="potato-configure-model-test-")) / "users_mapping.yaml"

    result = _run_configure(
        mapping_path,
        *_base_args(mapping_path),
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
        "context_length": 1050000,
        "api_mode": "codex_responses",
        "reasoning_effort": "xhigh",
    }
    proxy = yaml.safe_load(mapping_path.with_name("model_proxy.yaml").read_text(encoding="utf-8"))
    assert proxy["models"][0]["base_url"] == "https://primary.example/v1"
    assert proxy["models"][0]["api_key"] == "sk-primary"


def test_configure_model_proxy_rejects_api_key_in_process_arguments() -> None:
    mapping_path = Path(tempfile.mkdtemp(prefix="potato-configure-model-test-")) / "users_mapping.yaml"

    result = _run_configure(
        mapping_path,
        "--base-url",
        "https://primary.example/v1",
        "--model",
        "gpt-5.4",
        "--api-key",
        "sk-must-not-be-in-argv",
    )

    assert result.returncode == 2
    assert "unrecognized arguments" in result.stderr
    assert not mapping_path.exists()


def test_configure_model_proxy_rejects_world_readable_key_file() -> None:
    mapping_path = Path(tempfile.mkdtemp(prefix="potato-configure-model-test-")) / "users_mapping.yaml"
    key_path = _secret_file(mapping_path, "insecure.key", "sk-private")
    key_path.chmod(0o644)

    result = _run_configure(
        mapping_path,
        "--base-url",
        "https://primary.example/v1",
        "--model",
        "gpt-5.4",
        "--api-key-file",
        str(key_path),
    )

    assert result.returncode == 1
    assert "must not be accessible by group or other users" in result.stderr
    assert not mapping_path.exists()


def test_configure_model_proxy_defaults_api_mode_and_reasoning_effort() -> None:
    mapping_path = Path(tempfile.mkdtemp(prefix="potato-configure-model-test-")) / "users_mapping.yaml"

    result = _run_configure(
        mapping_path,
        *_base_args(mapping_path),
    )

    assert result.returncode == 0, result.stderr
    hermes = yaml.safe_load(mapping_path.read_text(encoding="utf-8"))["hermes"]
    assert hermes["model"]["api_mode"] == "codex_responses"
    assert hermes["config_overrides"]["agent"]["reasoning_effort"] == "xhigh"
    assert hermes["model_options"]["options"][0]["api_mode"] == "codex_responses"
    assert hermes["model_options"]["options"][0]["reasoning_effort"] == "xhigh"


def test_configure_model_proxy_allows_explicit_api_mode_and_reasoning_effort() -> None:
    mapping_path = Path(tempfile.mkdtemp(prefix="potato-configure-model-test-")) / "users_mapping.yaml"

    result = _run_configure(
        mapping_path,
        *_base_args(mapping_path),
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


def test_configure_model_proxy_writes_optional_model_options() -> None:
    mapping_path = Path(tempfile.mkdtemp(prefix="potato-configure-model-test-")) / "users_mapping.yaml"
    fast_key = _secret_file(mapping_path, "fast.key", "sk-fast")
    deep_key = _secret_file(mapping_path, "deep.key", "sk-deep")

    result = _run_configure(
        mapping_path,
        *_base_args(mapping_path),
        "--option",
        f"id=fast,name=Fast,model=gpt-5.4-mini,base_url=https://fast.example/v1,api_key_file={fast_key},context_length=500000",
        "--option",
        f"id=deep,model=gpt-5.5,base_url=https://deep.example/v1,api_key_file={deep_key},api_mode=chat_completions",
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
                "api_mode": "codex_responses",
                "reasoning_effort": "xhigh",
            },
            {
                "id": "fast",
                "name": "Fast",
                "provider": "custom",
                "model": "gpt-5.4-mini",
                "context_length": 500000,
                "api_mode": "codex_responses",
                "reasoning_effort": "xhigh",
            },
            {
                "id": "deep",
                "name": "gpt-5.5",
                "provider": "custom",
                "model": "gpt-5.5",
                "api_mode": "chat_completions",
                "reasoning_effort": "xhigh",
            },
        ],
    }
    proxy = yaml.safe_load(mapping_path.with_name("model_proxy.yaml").read_text(encoding="utf-8"))
    assert [item["api_key"] for item in proxy["models"]] == [
        "sk-primary",
        "sk-fast",
        "sk-deep",
    ]
    assert "sk-fast" not in mapping_path.read_text(encoding="utf-8")


def test_configure_model_proxy_preserves_duplicate_upstream_model_names_by_option_name() -> None:
    mapping_path = Path(tempfile.mkdtemp(prefix="potato-configure-model-test-")) / "users_mapping.yaml"
    alt_key = _secret_file(mapping_path, "alt.key", "sk-alt")

    result = _run_configure(
        mapping_path,
        *_base_args(mapping_path),
        "--option",
        f"id=alt,name=Alt,model=gpt-5.4,base_url=https://alt.example/v1,api_key_file={alt_key}",
    )

    assert result.returncode == 0, result.stderr
    proxy = yaml.safe_load(mapping_path.with_name("model_proxy.yaml").read_text(encoding="utf-8"))
    assert [(item["name"], item["model"], item["base_url"]) for item in proxy["models"]] == [
        ("gpt-5.4", "gpt-5.4", "https://primary.example/v1"),
        ("Alt", "gpt-5.4", "https://alt.example/v1"),
    ]


def test_configure_model_proxy_rejects_too_many_optional_models() -> None:
    mapping_path = Path(tempfile.mkdtemp(prefix="potato-configure-model-test-")) / "users_mapping.yaml"

    result = _run_configure(
        mapping_path,
        *_base_args(mapping_path),
        "--option",
        "id=one,model=one,base_url=https://one.example/v1,api_key=sk-one",
        "--option",
        "id=two,model=two,base_url=https://two.example/v1,api_key=sk-two",
        "--option",
        "id=three,model=three,base_url=https://three.example/v1,api_key=sk-three",
        "--option",
        "id=four,model=four,base_url=https://four.example/v1,api_key=sk-four",
    )

    assert result.returncode == 1
    assert "--option may be provided at most three times" in result.stderr


def test_configure_model_proxy_rejects_duplicate_option_id() -> None:
    mapping_path = Path(tempfile.mkdtemp(prefix="potato-configure-model-test-")) / "users_mapping.yaml"
    alt_key = _secret_file(mapping_path, "alt.key", "sk-alt")

    result = _run_configure(
        mapping_path,
        *_base_args(mapping_path),
        "--option",
        f"id=primary,model=alt,base_url=https://alt.example/v1,api_key_file={alt_key}",
    )

    assert result.returncode == 1
    assert "Duplicate model option id" in result.stderr


def test_configure_model_proxy_rejects_invalid_context_length() -> None:
    mapping_path = Path(tempfile.mkdtemp(prefix="potato-configure-model-test-")) / "users_mapping.yaml"

    result = _run_configure(
        mapping_path,
        *_base_args(mapping_path),
        "--context-length",
        "1050K",
    )

    assert result.returncode == 1
    assert "--context-length must be a plain positive integer" in result.stderr


def test_configure_model_proxy_migrates_legacy_fallback_model_list() -> None:
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

    result = _run_configure(mapping_path, *_base_args(mapping_path))

    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(mapping_path.read_text(encoding="utf-8"))
    hermes = data["hermes"]
    assert "fallback_model" not in hermes
    assert "fallback_providers" not in hermes
    proxy = yaml.safe_load(mapping_path.with_name("model_proxy.yaml").read_text(encoding="utf-8"))
    assert proxy["models"][-1]["model"] == "old-fallback"
    assert proxy["models"][-1]["api_key"] == "sk-old-fallback"
    assert "sk-old-fallback" not in mapping_path.read_text(encoding="utf-8")


def test_configure_model_proxy_clear_fallback_removes_fallback_config() -> None:
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

    result = _run_configure(mapping_path, *_base_args(mapping_path), "--clear-fallback")

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
        configure_model_proxy.pwd,
        "getpwnam",
        lambda username: SimpleNamespace(pw_uid=123, pw_gid=456),
    )

    configure_model_proxy.apply_user_runtime_model_patch(
        _runtime_config(),
        target,
        context_length=1050000,
        fallback_action=configure_model_proxy.FALLBACK_ACTION_PRESERVE,
    )

    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert data["model"] == {
        "default": "gpt-5.4",
        "provider": "custom",
        "base_url": "http://127.0.0.1:8765/v1",
        "api_key": "pmp_alice_0123456789abcdefghijklmnopqrstuvwxyz",
        "api_mode": "codex_responses",
        "context_length": 1050000,
    }
    assert data["agent"]["reasoning_effort"] == "xhigh"
    assert "web" in data["agent"]["disabled_toolsets"]
    assert data["auxiliary"]["compression"]["context_length"] == 1050000
    assert data["auxiliary"]["summarizer"]["model"] == "custom-summary"
    assert data["auxiliary"]["summarizer"]["provider"] == "custom"
    assert data["auxiliary"]["summarizer"]["base_url"] == ""
    assert data["auxiliary"]["summarizer"]["api_key"] == ""
    assert data["auxiliary"]["summarizer"]["fallback_chain"] == []
    assert data["memory"] == {"enabled": True, "provider": ""}
    assert data["tools"] == {"filesystem": True}
    assert data["terminal"] == {"backend": "local", "timeout": 300}
    assert "fallback_providers" not in data


def test_apply_user_runtime_patch_removes_openai_api_key_from_env(
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
        configure_model_proxy.pwd,
        "getpwnam",
        lambda username: SimpleNamespace(pw_uid=123, pw_gid=456),
    )

    configure_model_proxy.apply_user_runtime_model_patch(
        _runtime_config(),
        target,
        context_length=None,
        fallback_action=configure_model_proxy.FALLBACK_ACTION_PRESERVE,
    )

    assert env_path.read_text(encoding="utf-8") == (
        "# keep this comment\nFOO=bar\n\nBAZ=qux\n"
    )


def test_apply_user_runtime_patch_does_not_append_openai_api_key_to_env(
    monkeypatch, tmp_path
) -> None:
    target = _target(tmp_path)
    target.hermes_home.mkdir(parents=True)
    env_path = target.hermes_home / ".env"
    env_path.write_text("# keep\nFOO=bar", encoding="utf-8")

    monkeypatch.setattr(
        configure_model_proxy.pwd,
        "getpwnam",
        lambda username: SimpleNamespace(pw_uid=123, pw_gid=456),
    )

    configure_model_proxy.apply_user_runtime_model_patch(
        _runtime_config(),
        target,
        context_length=None,
        fallback_action=configure_model_proxy.FALLBACK_ACTION_PRESERVE,
    )

    assert env_path.read_text(encoding="utf-8") == "# keep\nFOO=bar"


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
        configure_model_proxy.pwd,
        "getpwnam",
        lambda username: SimpleNamespace(pw_uid=123, pw_gid=456),
    )

    configure_model_proxy.apply_user_runtime_model_patch(
        _runtime_config(),
        target,
        context_length=None,
        fallback_action=configure_model_proxy.FALLBACK_ACTION_PRESERVE,
    )

    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert "fallback_providers" not in data


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
        configure_model_proxy.pwd,
        "getpwnam",
        lambda username: SimpleNamespace(pw_uid=123, pw_gid=456),
    )

    configure_model_proxy.apply_user_runtime_model_patch(
        _runtime_config(),
        target,
        context_length=None,
        fallback_action=configure_model_proxy.FALLBACK_ACTION_CLEAR,
    )

    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert "fallback_providers" not in data
    assert "fallback_model" not in data
    assert data["memory"] == {"enabled": True, "provider": ""}


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
    model_proxy_token: pmp_alice_0123456789abcdefghijklmnopqrstuvwxyz
    systemd_service: hermes-alice.service
""".lstrip(),
        encoding="utf-8",
    )
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(
        configure_model_proxy, "confirm_apply_to_users", lambda *args, **kwargs: True
    )
    monkeypatch.setattr(configure_model_proxy, "require_root", lambda: None)
    monkeypatch.setattr(configure_model_proxy, "require_binary", lambda name: None)
    monkeypatch.setattr(
        configure_model_proxy,
        "apply_user_runtime_model_patch",
        lambda config, target, **kwargs: calls.append(
            ("install", target.systemd_service)
        ),
    )
    monkeypatch.setattr(configure_model_proxy, "is_service_active", lambda name: True)
    monkeypatch.setattr(
        configure_model_proxy,
        "restart_service",
        lambda name: calls.append(("restart", name)),
    )
    monkeypatch.setattr(
        configure_model_proxy,
        "wait_for_service_active",
        lambda name: calls.append(("wait-service", name)),
    )

    configure_model_proxy.apply_model_config_to_users(
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


def test_first_user_credential_migration_validates_in_memory_before_write(
    monkeypatch, tmp_path
) -> None:
    mapping_path = tmp_path / "users_mapping.yaml"
    mapping_path.write_text(
        f"""
users:
  - username: alice
    email: alice@example.com
    display_name: Alice
    linux_user: hmx_alice
    home_dir: {tmp_path / 'home'}
    hermes_home: {tmp_path / 'home' / '.hermes'}
    workdir: {tmp_path / 'home' / 'work'}
    api_port: 8643
    api_key: ${{LEGACY_SHARED_USER_API_KEY}}
    systemd_service: hermes-alice.service
""".lstrip(),
        encoding="utf-8",
    )
    original_mapping = mapping_path.read_bytes()
    proxy_path = mapping_path.with_name("model_proxy.yaml")
    primary_key = _secret_file(mapping_path, "primary.key", "sk-primary")
    confirmed_targets = []
    apply_calls = []

    def confirm(mapping, targets, **kwargs):
        assert mapping == mapping_path
        assert mapping_path.read_bytes() == original_mapping
        assert not proxy_path.exists()
        confirmed_targets.extend(targets)
        return True

    monkeypatch.setattr(configure_model_proxy, "require_root", lambda: None)
    monkeypatch.setattr(configure_model_proxy, "require_binary", lambda name: None)
    monkeypatch.setattr(configure_model_proxy, "confirm_apply_to_users", confirm)
    monkeypatch.setattr(
        configure_model_proxy,
        "apply_model_config_to_users",
        lambda *args, **kwargs: apply_calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        configure_model_proxy.sys,
        "argv",
        [
            "configure_model_proxy.py",
            "--mapping",
            str(mapping_path),
            "--base-url",
            "https://primary.example/v1",
            "--model",
            "gpt-5.4",
            "--api-key-file",
            str(primary_key),
            "--apply-to-users",
        ],
    )

    assert configure_model_proxy.main() == 0

    assert len(confirmed_targets) == 1
    target = confirmed_targets[0]
    assert target.model_proxy_token.startswith("pmp_")
    assert target.api_key != "${LEGACY_SHARED_USER_API_KEY}"
    persisted = yaml.safe_load(mapping_path.read_text(encoding="utf-8"))
    assert persisted["users"][0]["model_proxy_token"] == target.model_proxy_token
    assert persisted["users"][0]["api_key"] == target.api_key
    assert proxy_path.exists()
    assert len(apply_calls) == 1
    assert apply_calls[0][1]["confirmation_complete"] is True
