from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest


SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

import runtime_profile
from tests.profile_support import write_profile


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
