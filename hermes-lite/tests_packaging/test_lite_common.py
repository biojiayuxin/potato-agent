from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from _lite_common import (
    LiteReleaseError,
    compare_inventory,
    copy_inventory_files,
    source_inventory,
    wheel_inventory,
)


def test_source_inventory_is_content_and_mode_exact(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    regular = source / "regular.txt"
    executable = source / "run.sh"
    regular.write_text("one\n", encoding="utf-8")
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    regular.chmod(0o664)
    executable.chmod(0o775)

    expected = source_inventory(source)
    assert [item["mode"] for item in expected["files"]] == ["0644", "0755"]

    regular.write_text("two\n", encoding="utf-8")
    with pytest.raises(LiteReleaseError, match="changed=.*regular.txt"):
        compare_inventory(expected, source_inventory(source), label="source")


def test_source_inventory_excludes_release_infrastructure(tmp_path: Path) -> None:
    (tmp_path / "runtime.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "verify.py").write_text("ignored\n", encoding="utf-8")
    (tmp_path / "tests_packaging").mkdir()
    (tmp_path / "tests_packaging" / "test_x.py").write_text(
        "ignored\n", encoding="utf-8"
    )
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "runtime.pyc").write_bytes(b"ignored")

    inventory = source_inventory(tmp_path)
    assert [item["path"] for item in inventory["files"]] == ["runtime.py"]


def test_copy_inventory_never_copies_unlisted_files(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "kept.py").write_text("kept = True\n", encoding="utf-8")
    inventory = source_inventory(source)
    (source / "scripts").mkdir()
    (source / "scripts" / "ignored.py").write_text("ignored\n", encoding="utf-8")

    destination = tmp_path / "copy"
    copy_inventory_files(source, destination, inventory)

    assert (destination / "kept.py").is_file()
    assert not (destination / "scripts").exists()


def test_wheel_inventory_rejects_path_traversal(tmp_path: Path) -> None:
    wheel = tmp_path / "unsafe.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("../escape.py", "bad\n")

    with pytest.raises(LiteReleaseError, match="unsafe path"):
        wheel_inventory(wheel)
