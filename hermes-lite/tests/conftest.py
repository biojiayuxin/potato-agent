from __future__ import annotations

import copy
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml


SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

import runtime_profile


BASE_PROFILE = {
    "schema_version": 1,
    "name": "potato",
    "revision": 1,
    "toolsets": {
        "enabled": list(runtime_profile.POTATO_ENABLED_TOOLSETS),
        "disabled": [
            "web",
            "search",
            "x_search",
            "clarify",
            "cronjob",
            "kanban",
            "moa",
            "tts",
            "image_gen",
            "video",
            "video_gen",
            "computer_use",
            "messaging",
            "homeassistant",
            "discord",
            "discord_admin",
            "yuanbao",
            "feishu_doc",
            "feishu_drive",
            "spotify",
        ],
    },
    "expected_tools": list(runtime_profile.POTATO_EXPECTED_TOOLS),
    "plugins": {
        "allow_user": False,
        "allow_project": False,
        "allow_entrypoint": False,
        "allowed_general_keys": [],
        "forbidden_kinds": ["platform"],
    },
    "providers": {
        "model": ["custom"],
        "api_modes": ["codex_responses", "chat_completions"],
        "browser": "local",
        "memory": "builtin",
        "context_engine": "compressor",
        "web": [],
    },
    "mcp": {"enabled": False},
    "runtime": {
        "allow_lazy_installs": False,
        "lsp_enabled": True,
        "lsp_install_strategy": "manual",
        "terminal_backend": "local",
        "skills_dependency_strategy": "user_managed",
    },
}


def write_profile(path: Path, *, api_modes: list[str] | None = None) -> Path:
    data = copy.deepcopy(BASE_PROFILE)
    if api_modes is not None:
        data["providers"]["api_modes"] = api_modes
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


@dataclass(frozen=True)
class RuntimePaths:
    source: Path
    home: Path
    hermes_home: Path
    profile: Path
    state_home: Path


@pytest.fixture
def runtime_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RuntimePaths:
    home = tmp_path / "home"
    hermes_home = home / ".hermes"
    state_home = tmp_path / "state"
    home.mkdir()
    hermes_home.mkdir()
    state_home.mkdir()
    profile = write_profile(tmp_path / "runtime-profile.yaml")

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("HERMES_RUNTIME_PROFILE_PATH", str(profile))
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    monkeypatch.setenv("HERMES_GATEWAY_LOCK_DIR", str(state_home / "gateway-locks"))
    runtime_profile._profile = runtime_profile._UNSET
    yield RuntimePaths(SOURCE_ROOT, home, hermes_home, profile, state_home)
    runtime_profile._profile = runtime_profile._UNSET

