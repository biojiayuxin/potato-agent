from __future__ import annotations

import csv
import gzip
import os
from pathlib import Path
from typing import Any, Iterable

import yaml


REPO_EXPORT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = REPO_EXPORT_ROOT / "config.yaml"


def expand_path(value: str | Path) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(str(value)))).resolve()


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    config_path = expand_path(path or DEFAULT_CONFIG_PATH)
    with config_path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    if os.getenv("WGCNA_EXPORT_DIR"):
        config["output_dir"] = os.getenv("WGCNA_EXPORT_DIR")
    config["base_dir"] = expand_path(config["base_dir"])
    config["output_dir"] = expand_path(config.get("output_dir") or "~/tmp/wgcna_coexpression_export")
    config["networks"] = [str(item) for item in config.get("networks", [])]
    return config


def ensure_export_dirs(config: dict[str, Any]) -> None:
    for name in ("tables", "logs"):
        (Path(config["output_dir"]) / name).mkdir(parents=True, exist_ok=True)


def table_path(config: dict[str, Any], name: str) -> Path:
    return Path(config["output_dir"]) / "tables" / name


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def write_tsv(path: Path, fieldnames: list[str], rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    opener = gzip.open if path.suffix == ".gz" else open
    mode = "wt" if path.suffix == ".gz" else "w"
    with opener(path, mode, encoding="utf-8", newline="") as handle:  # type: ignore[arg-type]
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
            delimiter="\t",
            lineterminator="\n",
            extrasaction="ignore",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({
                key: "" if value is None else value
                for key, value in row.items()
            })


def truth(value: bool) -> str:
    return "true" if value else "false"


def clean_float(value: str | None) -> str:
    text = str(value or "").strip()
    if text.upper() == "NA":
        return ""
    return text
