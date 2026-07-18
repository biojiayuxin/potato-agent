from pathlib import Path

import pytest

from _common import PackagingError, ensure_non_production_path, tree_inventory


def test_tree_inventory_changes_with_content_path_and_mode(tmp_path: Path) -> None:
    root = tmp_path / "tree"
    root.mkdir()
    item = root / "item.txt"
    item.write_text("one", encoding="utf-8")
    first = tree_inventory(root)

    item.write_text("two", encoding="utf-8")
    second = tree_inventory(root)
    assert first["tree_sha256"] != second["tree_sha256"]

    item.rename(root / "renamed.txt")
    third = tree_inventory(root)
    assert second["tree_sha256"] != third["tree_sha256"]


@pytest.mark.parametrize("path", [Path("/opt/release"), Path("/srv/potato/release")])
def test_production_output_paths_are_rejected(path: Path) -> None:
    with pytest.raises(PackagingError, match="production root"):
        ensure_non_production_path(path, label="test output")
