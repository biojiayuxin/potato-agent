import importlib
import os
import sys
from pathlib import Path

import runtime_profile
from hermes_cli import env_loader
from hermes_cli.env_loader import load_hermes_dotenv


def test_user_env_overrides_stale_shell_values(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    env_file = home / ".env"
    env_file.write_text("OPENAI_BASE_URL=https://new.example/v1\n", encoding="utf-8")

    monkeypatch.setenv("OPENAI_BASE_URL", "https://old.example/v1")

    loaded = load_hermes_dotenv(hermes_home=home)

    assert loaded == [env_file]
    assert os.getenv("OPENAI_BASE_URL") == "https://new.example/v1"


def test_user_env_cannot_override_process_fixed_runtime_policy(
    tmp_path, monkeypatch
):
    home = tmp_path / "hermes"
    home.mkdir()
    env_file = home / ".env"
    env_file.write_text(
        "HERMES_RUNTIME_PROFILE_PATH=/tmp/user-profile.yaml\n"
        "HERMES_DISABLE_MCP=0\n"
        "TERMINAL_ENV=docker\n"
        "BROWSER_CDP_URL=ws://example.com/devtools/browser/remote\n"
        "OPENAI_BASE_URL=https://new.example/v1\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "HERMES_RUNTIME_PROFILE_PATH", "/opt/potato/runtime-profile.yaml"
    )
    monkeypatch.setenv("HERMES_DISABLE_MCP", "1")
    monkeypatch.setenv("TERMINAL_ENV", "local")
    monkeypatch.setenv("BROWSER_CDP_URL", "")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://old.example/v1")

    load_hermes_dotenv(hermes_home=home)

    assert os.environ["HERMES_RUNTIME_PROFILE_PATH"] == "/opt/potato/runtime-profile.yaml"
    assert os.environ["HERMES_DISABLE_MCP"] == "1"
    assert os.environ["TERMINAL_ENV"] == "local"
    assert os.environ["BROWSER_CDP_URL"] == ""
    assert os.environ["OPENAI_BASE_URL"] == "https://new.example/v1"


def test_secret_sources_see_process_fixed_runtime_policy(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    (home / ".env").write_text(
        "HERMES_RUNTIME_PROFILE_PATH=\n"
        "HERMES_DISABLE_LAZY_INSTALLS=0\n",
        encoding="utf-8",
    )
    profile_path = (
        Path(__file__).resolve().parents[3]
        / "packaging"
        / "hermes"
        / "runtime-profile.yaml"
    )
    monkeypatch.setenv("HERMES_RUNTIME_PROFILE_PATH", str(profile_path))
    monkeypatch.setenv("HERMES_DISABLE_LAZY_INSTALLS", "1")
    monkeypatch.setattr(runtime_profile, "_profile", runtime_profile._UNSET)

    observed_profiles = []

    def inspect_runtime_policy(_home_path):
        assert os.environ["HERMES_RUNTIME_PROFILE_PATH"] == str(profile_path)
        assert os.environ["HERMES_DISABLE_LAZY_INSTALLS"] == "1"
        assert runtime_profile.automatic_installs_disabled()
        observed_profiles.append(runtime_profile.get_runtime_profile())

    monkeypatch.setattr(
        env_loader,
        "_apply_external_secret_sources",
        inspect_runtime_policy,
    )

    load_hermes_dotenv(hermes_home=home)

    assert len(observed_profiles) == 1
    assert observed_profiles[0] is runtime_profile.get_runtime_profile()
    assert observed_profiles[0] is not None
    assert observed_profiles[0].path == profile_path.resolve()
    assert os.environ["HERMES_RUNTIME_PROFILE_PATH"] == str(profile_path)
    assert os.environ["HERMES_DISABLE_LAZY_INSTALLS"] == "1"


def test_user_env_can_set_runtime_policy_when_parent_did_not(
    tmp_path, monkeypatch
):
    home = tmp_path / "hermes"
    home.mkdir()
    env_file = home / ".env"
    env_file.write_text(
        "HERMES_RUNTIME_PROFILE_PATH=/opt/user-selected-profile.yaml\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("HERMES_RUNTIME_PROFILE_PATH", raising=False)

    load_hermes_dotenv(hermes_home=home)

    assert (
        os.environ["HERMES_RUNTIME_PROFILE_PATH"]
        == "/opt/user-selected-profile.yaml"
    )


def test_project_env_overrides_stale_shell_values_when_user_env_missing(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    project_env = tmp_path / ".env"
    project_env.write_text("OPENAI_BASE_URL=https://project.example/v1\n", encoding="utf-8")

    monkeypatch.setenv("OPENAI_BASE_URL", "https://old.example/v1")

    loaded = load_hermes_dotenv(hermes_home=home, project_env=project_env)

    assert loaded == [project_env]
    assert os.getenv("OPENAI_BASE_URL") == "https://project.example/v1"


def test_project_env_is_sanitized_before_loading(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    project_env = tmp_path / ".env"
    project_env.write_text(
        "TELEGRAM_BOT_TOKEN=0123456789:test"
        "ANTHROPIC_API_KEY=sk-ant-test123\n",
        encoding="utf-8",
    )

    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    loaded = load_hermes_dotenv(hermes_home=home, project_env=project_env)

    assert loaded == [project_env]
    assert os.getenv("TELEGRAM_BOT_TOKEN") == "0123456789:test"
    assert os.getenv("ANTHROPIC_API_KEY") == "sk-ant-test123"


def test_user_env_takes_precedence_over_project_env(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    user_env = home / ".env"
    project_env = tmp_path / ".env"
    user_env.write_text("OPENAI_BASE_URL=https://user.example/v1\n", encoding="utf-8")
    project_env.write_text("OPENAI_BASE_URL=https://project.example/v1\nOPENAI_API_KEY=project-key\n", encoding="utf-8")

    monkeypatch.setenv("OPENAI_BASE_URL", "https://old.example/v1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    loaded = load_hermes_dotenv(hermes_home=home, project_env=project_env)

    assert loaded == [user_env, project_env]
    assert os.getenv("OPENAI_BASE_URL") == "https://user.example/v1"
    assert os.getenv("OPENAI_API_KEY") == "project-key"


def test_null_bytes_in_user_env_are_stripped(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    env_file = home / ".env"
    # Null bytes can be introduced when copy-pasting API keys.
    env_file.write_text("GLM_API_KEY=abc\x00\x00\nOPENAI_API_KEY=sk-123\n", encoding="utf-8")

    monkeypatch.delenv("GLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    loaded = load_hermes_dotenv(hermes_home=home)

    assert loaded == [env_file]
    assert os.getenv("GLM_API_KEY") == "abc"
    assert os.getenv("OPENAI_API_KEY") == "sk-123"


def test_main_import_applies_user_env_over_shell_values(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    (home / ".env").write_text(
        "OPENAI_BASE_URL=https://new.example/v1\nHERMES_INFERENCE_PROVIDER=custom\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("OPENAI_BASE_URL", "https://old.example/v1")
    monkeypatch.setenv("HERMES_INFERENCE_PROVIDER", "openrouter")

    sys.modules.pop("hermes_cli.main", None)
    importlib.import_module("hermes_cli.main")

    assert os.getenv("OPENAI_BASE_URL") == "https://new.example/v1"
    assert os.getenv("HERMES_INFERENCE_PROVIDER") == "custom"
