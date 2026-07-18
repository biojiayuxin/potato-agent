from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path

import pytest
import yaml

from _common import PackagingError
from vendor import apply_archive, fetch


def _archive(tmp_path: Path, bundle) -> Path:
    archive = tmp_path / "upstream.tar.gz"
    files = {
        "fixture/uv.lock": b"fixture-lock\n",
        "fixture/pyproject.toml": b'[project]\nname = "hermes-agent"\nversion = "0.16.0"\n',
        "fixture/README.md": b"upstream\n",
    }
    with tarfile.open(archive, "w:gz") as handle:
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            handle.addfile(info, io.BytesIO(content))
    data = bundle.upstream_data.copy()
    data["archive_url"] = archive.as_uri()
    data["archive_sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
    data["uv_lock_sha256"] = hashlib.sha256(files["fixture/uv.lock"]).hexdigest()
    bundle.upstream.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return archive


def test_vendor_fetch_and_empty_patch_apply_are_hash_verified(
    tmp_path: Path, fake_profile_bundle
) -> None:
    archive = _archive(tmp_path, fake_profile_bundle)
    fetched = tmp_path / "fetched.tar.gz"
    result = fetch(fake_profile_bundle.upstream, fetched)
    assert result["sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()

    patches = tmp_path / "patches"
    patches.mkdir()
    (patches / "series").write_text("# empty\n", encoding="utf-8")
    output = tmp_path / "vendor-tree"
    result = apply_archive(
        fake_profile_bundle.upstream,
        fetched,
        patches,
        output,
        check_only=False,
        work_dir=None,
    )
    assert Path(result["output"]) == output
    assert (output / "README.md").read_text(encoding="utf-8") == "upstream\n"


def test_vendor_fails_closed_for_missing_declared_patch(tmp_path: Path, fake_profile_bundle) -> None:
    archive = _archive(tmp_path, fake_profile_bundle)
    data = yaml.safe_load(fake_profile_bundle.upstream.read_text(encoding="utf-8"))
    data["patch_series"] = ["missing.patch"]
    fake_profile_bundle.upstream.write_text(
        yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
    )
    patches = tmp_path / "patches"
    patches.mkdir()
    (patches / "series").write_text("missing.patch\n", encoding="utf-8")
    with pytest.raises(PackagingError, match="declared patch is missing"):
        apply_archive(
            fake_profile_bundle.upstream,
            archive,
            patches,
            None,
            check_only=True,
            work_dir=None,
        )
