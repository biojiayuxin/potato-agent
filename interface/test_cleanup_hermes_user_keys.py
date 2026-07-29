from __future__ import annotations

from types import SimpleNamespace

import pytest

from cleanup_hermes_user_keys import (
    CleanupHermesUserKeysError,
    _load_yaml_mapping,
    _read_user_text,
)


def _target(hermes_home):
    return SimpleNamespace(hermes_home=hermes_home)


@pytest.mark.parametrize("name,reader", [("config.yaml", _load_yaml_mapping), (".env", _read_user_text)])
def test_cleanup_refuses_user_symlinks_outside_hermes_home(
    tmp_path, name, reader
) -> None:
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir(mode=0o700)
    secret = tmp_path / "root-only-secret"
    sentinel = "upstream-secret-must-not-be-read"
    secret.write_text(sentinel, encoding="utf-8")
    path = hermes_home / name
    path.symlink_to(secret)

    with pytest.raises(CleanupHermesUserKeysError) as exc_info:
        reader(_target(hermes_home), path)

    assert sentinel not in str(exc_info.value)
