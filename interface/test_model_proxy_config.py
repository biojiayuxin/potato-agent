from __future__ import annotations

import os
import stat
from types import SimpleNamespace

import pytest

from interface import model_proxy_config


def test_proxy_config_owner_is_dedicated_group_when_root(monkeypatch) -> None:
    monkeypatch.setattr(model_proxy_config.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        model_proxy_config.grp,
        "getgrnam",
        lambda name: SimpleNamespace(gr_gid=4321),
    )

    assert model_proxy_config._proxy_config_owner_and_mode() == (0, 4321, 0o640)


def test_proxy_config_owner_is_private_caller_when_non_root(monkeypatch) -> None:
    monkeypatch.setattr(model_proxy_config.os, "geteuid", lambda: 1001)
    monkeypatch.setattr(model_proxy_config.os, "getegid", lambda: 1002)

    assert model_proxy_config._proxy_config_owner_and_mode() == (1001, 1002, 0o600)


def test_proxy_config_write_requires_service_group_when_root(monkeypatch) -> None:
    monkeypatch.setattr(model_proxy_config.os, "geteuid", lambda: 0)

    def missing_group(_name: str):
        raise KeyError

    monkeypatch.setattr(model_proxy_config.grp, "getgrnam", missing_group)

    with pytest.raises(
        model_proxy_config.ModelProxyConfigError,
        match="Required model proxy group does not exist",
    ):
        model_proxy_config._proxy_config_owner_and_mode()


def test_load_proxy_config_rejects_symlink(tmp_path) -> None:
    target = tmp_path / "target.yaml"
    target.write_text("models: []\n", encoding="utf-8")
    target.chmod(0o600)
    link = tmp_path / "model_proxy.yaml"
    link.symlink_to(target)

    with pytest.raises(
        model_proxy_config.ModelProxyConfigError,
        match="Unable to load model proxy configuration",
    ):
        model_proxy_config.load_model_proxy_config(link)


@pytest.mark.parametrize("mode", [0o644, 0o660, 0o604, 0o777])
def test_load_proxy_config_rejects_excess_permissions(tmp_path, mode) -> None:
    path = tmp_path / "model_proxy.yaml"
    path.write_text("models: []\n", encoding="utf-8")
    path.chmod(mode)

    with pytest.raises(
        model_proxy_config.ModelProxyConfigError,
        match="permissions must be",
    ):
        model_proxy_config.load_model_proxy_config(path)


def test_load_proxy_config_accepts_private_regular_file(tmp_path) -> None:
    path = tmp_path / "model_proxy.yaml"
    path.write_text("models: []\n", encoding="utf-8")
    path.chmod(0o600)

    assert model_proxy_config.load_model_proxy_config(path) == {"models": []}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_group_readable_proxy_config_requires_dedicated_group(monkeypatch) -> None:
    monkeypatch.setattr(model_proxy_config.os, "geteuid", lambda: 2001)
    monkeypatch.setattr(model_proxy_config.os, "getegid", lambda: 2001)
    monkeypatch.setattr(model_proxy_config.os, "getgroups", lambda: [2001, 3000])
    monkeypatch.setattr(
        model_proxy_config.grp,
        "getgrnam",
        lambda name: SimpleNamespace(gr_gid=3000),
    )
    file_stat = SimpleNamespace(
        st_mode=stat.S_IFREG | 0o640,
        st_uid=0,
        st_gid=2001,
    )

    with pytest.raises(
        model_proxy_config.ModelProxyConfigError,
        match="dedicated service group",
    ):
        model_proxy_config._validate_proxy_config_access(file_stat)


def test_load_proxy_config_rejects_non_caller_owner(monkeypatch, tmp_path) -> None:
    path = tmp_path / "model_proxy.yaml"
    path.write_text("models: []\n", encoding="utf-8")
    path.chmod(0o600)
    file_uid = path.stat().st_uid
    monkeypatch.setattr(model_proxy_config.os, "geteuid", lambda: file_uid + 1000)

    with pytest.raises(
        model_proxy_config.ModelProxyConfigError,
        match="owned by root or the current service user",
    ):
        model_proxy_config.load_model_proxy_config(path)


def test_load_proxy_config_rejects_oversize_file(monkeypatch, tmp_path) -> None:
    path = tmp_path / "model_proxy.yaml"
    path.write_text("models: []\n", encoding="utf-8")
    path.chmod(0o600)
    original_fstat = os.fstat

    def oversized(descriptor):
        current = original_fstat(descriptor)
        values = list(current)
        values[6] = model_proxy_config.MAX_MODEL_PROXY_CONFIG_BYTES + 1
        return os.stat_result(values)

    monkeypatch.setattr(model_proxy_config.os, "fstat", oversized)
    with pytest.raises(
        model_proxy_config.ModelProxyConfigError,
        match="exceeds the size limit",
    ):
        model_proxy_config.load_model_proxy_config(path)
