from __future__ import annotations

from interface import app as interface_app_mod
from interface import file_browser_policy
from interface.test_file_upload import _build_client_and_user


def _allow_direct_file_access(monkeypatch) -> None:
    monkeypatch.setattr(
        interface_app_mod,
        "_assert_user_can_read_file",
        lambda path, *, linux_user: None,
    )
    monkeypatch.setattr(
        interface_app_mod,
        "_assert_user_can_open_directory",
        lambda path, *, linux_user: None,
    )


def test_home_and_public_data_allows_public_preview_and_download(monkeypatch) -> None:
    client, home_dir = _build_client_and_user(monkeypatch)
    _allow_direct_file_access(monkeypatch)
    public_data = home_dir.parent / "shared-public-data"
    public_data.mkdir()
    (public_data / "README.txt").write_text("shared data\n", encoding="utf-8")
    (home_dir / "public_data").symlink_to(public_data, target_is_directory=True)
    monkeypatch.setattr(interface_app_mod, "FILE_BROWSER_MODE", "home_and_public_data")
    monkeypatch.setattr(file_browser_policy, "DEFAULT_PUBLIC_DATA_PATH", public_data)

    try:
        preview = client.get(
            "/api/files/preview/text",
            params={"path": "public_data/README.txt"},
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["content"] == "shared data\n"

        download = client.get(
            "/api/files/download",
            params={"path": "public_data/README.txt"},
        )
        assert download.status_code == 200, download.text
        assert download.content == b"shared data\n"
    finally:
        client.close()


def test_home_and_public_data_rejects_other_external_symlink(monkeypatch) -> None:
    client, home_dir = _build_client_and_user(monkeypatch)
    _allow_direct_file_access(monkeypatch)
    public_data = home_dir.parent / "shared-public-data"
    public_data.mkdir()
    outside = home_dir.parent / "other-readable-data"
    outside.mkdir()
    (outside / "secret.txt").write_text("not shared\n", encoding="utf-8")
    (home_dir / "outside").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(interface_app_mod, "FILE_BROWSER_MODE", "home_and_public_data")
    monkeypatch.setattr(file_browser_policy, "DEFAULT_PUBLIC_DATA_PATH", public_data)

    try:
        response = client.get(
            "/api/files/preview/meta",
            params={"path": "outside/secret.txt"},
        )
        assert response.status_code == 403, response.text
    finally:
        client.close()


def test_restricted_file_tree_filters_symlinks_outside_allowed_roots(
    monkeypatch,
) -> None:
    client, home_dir = _build_client_and_user(monkeypatch)
    _allow_direct_file_access(monkeypatch)
    public_data = home_dir.parent / "shared-public-data"
    public_data.mkdir()
    outside = home_dir.parent / "other-readable-data"
    outside.mkdir()
    (home_dir / "public_data").symlink_to(public_data, target_is_directory=True)
    (home_dir / "outside").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(interface_app_mod, "FILE_BROWSER_MODE", "home_and_public_data")
    monkeypatch.setattr(file_browser_policy, "DEFAULT_PUBLIC_DATA_PATH", public_data)
    monkeypatch.setattr(
        interface_app_mod,
        "_list_directory_as_user",
        lambda path, *, relative_path, linux_user: [
            {"name": "work", "path": "work", "type": "directory"},
            {"name": "public_data", "path": "public_data", "type": "directory"},
            {"name": "outside", "path": "outside", "type": "directory"},
        ],
    )

    try:
        response = client.get("/api/files/tree")
        assert response.status_code == 200, response.text
        assert [entry["name"] for entry in response.json()["entries"]] == [
            "work",
            "public_data",
        ]
    finally:
        client.close()
