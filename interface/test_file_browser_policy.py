from __future__ import annotations

from pathlib import Path

import pytest

from interface.file_browser_policy import (
    DEFAULT_PUBLIC_DATA_PATH,
    HOME_AND_PUBLIC_DATA_MODE,
    HOME_ONLY_MODE,
    USER_READABLE_MODE,
    FileBrowserAccessError,
    authorize_file_browser_path,
    normalize_file_browser_mode,
)


def _browser_roots(tmp_path: Path) -> tuple[Path, Path, Path]:
    home = tmp_path / "home"
    public_data = tmp_path / "public_data"
    outside = tmp_path / "outside"
    home.mkdir()
    public_data.mkdir()
    outside.mkdir()
    return home, public_data, outside


def test_default_public_data_path_matches_deployment_mount() -> None:
    assert DEFAULT_PUBLIC_DATA_PATH == Path("/mnt/data/public_data")


def test_home_and_public_data_allows_home_file(tmp_path: Path) -> None:
    home, public_data, _ = _browser_roots(tmp_path)
    home_file = home / "notes.txt"
    home_file.write_text("notes\n", encoding="utf-8")

    resolved = authorize_file_browser_path(
        home_file,
        home=home,
        mode=HOME_AND_PUBLIC_DATA_MODE,
        public_data_root=public_data,
    )

    assert resolved == home_file.resolve()


@pytest.mark.parametrize("through_home_link", [False, True])
def test_home_and_public_data_allows_public_data_file(
    tmp_path: Path,
    through_home_link: bool,
) -> None:
    home, public_data, _ = _browser_roots(tmp_path)
    public_file = public_data / "dataset.tsv"
    public_file.write_text("gene\tvalue\n", encoding="utf-8")
    requested_path = public_file
    if through_home_link:
        public_link = home / "public_data"
        public_link.symlink_to(public_data, target_is_directory=True)
        requested_path = public_link / public_file.name

    resolved = authorize_file_browser_path(
        requested_path,
        home=home,
        mode=HOME_AND_PUBLIC_DATA_MODE,
        public_data_root=public_data,
    )

    assert resolved == public_file.resolve()


@pytest.mark.parametrize("through_home_link", [False, True])
def test_home_and_public_data_rejects_arbitrary_outside_file(
    tmp_path: Path,
    through_home_link: bool,
) -> None:
    home, public_data, outside = _browser_roots(tmp_path)
    outside_file = outside / "secret.txt"
    outside_file.write_text("secret\n", encoding="utf-8")
    requested_path = outside_file
    if through_home_link:
        outside_link = home / "outside"
        outside_link.symlink_to(outside, target_is_directory=True)
        requested_path = outside_link / outside_file.name

    with pytest.raises(FileBrowserAccessError):
        authorize_file_browser_path(
            requested_path,
            home=home,
            mode=HOME_AND_PUBLIC_DATA_MODE,
            public_data_root=public_data,
        )


def test_home_and_public_data_rejects_symlink_escape_from_public_data(
    tmp_path: Path,
) -> None:
    home, public_data, outside = _browser_roots(tmp_path)
    outside_file = outside / "secret.txt"
    outside_file.write_text("secret\n", encoding="utf-8")
    escape_link = public_data / "escape"
    escape_link.symlink_to(outside, target_is_directory=True)

    with pytest.raises(FileBrowserAccessError):
        authorize_file_browser_path(
            escape_link / outside_file.name,
            home=home,
            mode=HOME_AND_PUBLIC_DATA_MODE,
            public_data_root=public_data,
        )


def test_unknown_mode_fails_closed_as_home_only(tmp_path: Path) -> None:
    home, public_data, _ = _browser_roots(tmp_path)
    public_file = public_data / "dataset.tsv"
    public_file.write_text("gene\tvalue\n", encoding="utf-8")

    assert normalize_file_browser_mode("future_mode") == HOME_ONLY_MODE
    with pytest.raises(FileBrowserAccessError):
        authorize_file_browser_path(
            public_file,
            home=home,
            mode="future_mode",
            public_data_root=public_data,
        )


def test_user_readable_keeps_allowing_paths_outside_managed_roots(
    tmp_path: Path,
) -> None:
    home, public_data, outside = _browser_roots(tmp_path)
    outside_file = outside / "shared.txt"
    outside_file.write_text("shared\n", encoding="utf-8")

    resolved = authorize_file_browser_path(
        outside_file,
        home=home,
        mode=USER_READABLE_MODE,
        public_data_root=public_data,
    )

    assert resolved == outside_file.resolve()
