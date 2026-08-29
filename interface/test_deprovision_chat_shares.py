from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import deprovision_interface_user as command


def _configure_deprovision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    invalidate,
) -> tuple[list[str], Path, Path]:
    auth_db = tmp_path / "interface.db"
    share_db = tmp_path / "chat-shares.db"
    mapping_path = tmp_path / "users_mapping.yaml"
    calls: list[str] = []

    monkeypatch.setattr(
        command.sys,
        "argv",
        [
            "deprovision_interface_user.py",
            "alice",
            "--mapping",
            str(mapping_path),
            "--auth-db",
            str(auth_db),
            "--share-db",
            str(share_db),
        ],
    )
    monkeypatch.setattr(command, "require_root", lambda: calls.append("root"))
    monkeypatch.setattr(
        command, "require_binary", lambda name: calls.append(f"binary:{name}")
    )
    monkeypatch.setattr(command, "load_mapping", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        command,
        "MappingStore",
        lambda _path: SimpleNamespace(
            get_target_by_username=lambda _username: SimpleNamespace(
                systemd_service="hermes-alice.service",
                linux_user="hmx_alice",
            )
        ),
    )
    monkeypatch.setattr(
        command,
        "list_users",
        lambda *, db_path: [
            SimpleNamespace(
                id="user-id-1",
                username="alice",
                mapping_username="alice",
            )
        ],
    )
    monkeypatch.setattr(
        command,
        "is_temporary_user",
        lambda user_id, *, db_path: False,
    )
    monkeypatch.setattr(command, "invalidate_user_chat_share_data", invalidate)
    monkeypatch.setattr(
        command,
        "stop_and_remove_service",
        lambda service: calls.append(f"stop:{service}"),
    )
    monkeypatch.setattr(
        command,
        "remove_linux_user",
        lambda linux_user, *, delete_home: calls.append(f"linux:{linux_user}"),
    )
    monkeypatch.setattr(
        command,
        "delete_user_by_mapping_username",
        lambda username, *, db_path: calls.append(f"auth:{username}"),
    )
    monkeypatch.setattr(
        command,
        "remove_user_mapping_entry",
        lambda config, username: calls.append(f"mapping:{username}"),
    )
    monkeypatch.setattr(
        command,
        "write_mapping",
        lambda path, config: calls.append("write-mapping"),
    )
    return calls, auth_db, share_db


def test_deprovision_cleans_share_identity_before_destructive_steps(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    observed: list[tuple[str, str, Path]] = []

    def invalidate(user_id: str, *, recipient_user_id: str, db_path: Path):
        observed.append((user_id, recipient_user_id, db_path))

    calls, _, share_db = _configure_deprovision(
        monkeypatch,
        tmp_path,
        invalidate=invalidate,
    )

    assert command.main() == 0
    assert observed == [("user-id-1", "formal:user-id-1", share_db)]
    assert calls.index("stop:hermes-alice.service") < calls.index("linux:hmx_alice")
    assert calls.index("linux:hmx_alice") < calls.index("auth:alice")


def test_deprovision_fails_closed_when_share_cleanup_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def invalidate(*args, **kwargs):
        raise OSError("share DB unavailable")

    calls, _, _ = _configure_deprovision(
        monkeypatch,
        tmp_path,
        invalidate=invalidate,
    )

    with pytest.raises(OSError, match="share DB unavailable"):
        command.main()

    assert not any(
        call.startswith(("stop:", "linux:", "auth:", "mapping:"))
        or call == "write-mapping"
        for call in calls
    )


def test_deprovision_resolves_share_owner_only_by_mapping_username(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    observed: list[str] = []
    _configure_deprovision(
        monkeypatch,
        tmp_path,
        invalidate=lambda user_id, **kwargs: observed.append(user_id),
    )
    monkeypatch.setattr(
        command,
        "list_users",
        lambda *, db_path: [
            SimpleNamespace(
                id="cross-namespace-user",
                username="alice",
                mapping_username="bob",
            ),
            SimpleNamespace(
                id="mapping-owner",
                username="account-name",
                mapping_username="alice",
            ),
        ],
    )

    assert command.main() == 0
    assert observed == ["mapping-owner"]
