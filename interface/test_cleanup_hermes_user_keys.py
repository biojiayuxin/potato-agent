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


@pytest.mark.parametrize("bootstrap,expected", [("fast", "fast"), ("deep", "deep"), ("gpt-shared", "fast")])
def test_repair_preserves_stable_id_without_guessing_from_upstream(tmp_path, monkeypatch, bootstrap, expected):
    import cleanup_hermes_user_keys as command
    from interface.model_catalog import public_catalog
    from interface.model_options import model_options_from_catalog
    from interface.test_model_support import make_catalog

    catalog = make_catalog()
    backend_id = catalog["options"]["deep"]["backend"]
    catalog["options"]["fast"]["backend"] = backend_id
    catalog["backends"][backend_id]["model"] = "gpt-shared"
    options = model_options_from_catalog(public_catalog(catalog))
    target = SimpleNamespace(username="test", linux_user="test", hermes_home=tmp_path)
    monkeypatch.setattr(command, "build_parser", lambda: SimpleNamespace(parse_args=lambda: SimpleNamespace(mapping=tmp_path / "mapping.yaml", dry_run=False)))
    monkeypatch.setattr(command, "load_mapping", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(command, "normalize_model_options", lambda _: options)
    monkeypatch.setattr(command.MappingStore, "load_targets", lambda _: [target])
    monkeypatch.setattr(command, "_load_yaml_mapping", lambda *_: {"model": {"default": bootstrap, "api_mode": "outdated"}})
    monkeypatch.setattr(command, "_read_user_text", lambda *_: "")
    monkeypatch.setattr(command.pwd, "getpwnam", lambda _: SimpleNamespace())
    repaired = []
    monkeypatch.setattr(command, "repair_user_model_config", lambda target, option, **_: repaired.append(option.id))
    assert command.main() == 0
    assert repaired == [expected]
