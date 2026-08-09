from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from interface.genome_feature_index import (
    FeatureIndexError,
    build_full_index,
    check_index,
    default_index_path,
    index_summary,
    sync_assemblies,
)


DEFAULT_DB_ROOT = Path("/mnt/data/public_data/Genome_browser_DB")


def parse_representative_maps(values: Sequence[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        assembly_id, separator, raw_path = value.partition("=")
        assembly_id = assembly_id.strip()
        raw_path = raw_path.strip()
        if not separator or not assembly_id or not raw_path:
            raise ValueError(
                "--representative-map must use ASSEMBLY_ID=/absolute/path.tsv"
            )
        path = Path(raw_path).resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        if assembly_id in result:
            raise ValueError(f"duplicate representative map for {assembly_id}")
        result[assembly_id] = path
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build or update the centralized Genome Browser feature index."
    )
    parser.add_argument("--db-root", type=Path, default=DEFAULT_DB_ROOT)
    parser.add_argument(
        "--output",
        type=Path,
        help="SQLite output; defaults to GENOME_BROWSER_FEATURE_INDEX_PATH or DB_ROOT/feature_index.sqlite",
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--full", action="store_true", help="Build a complete staging database and publish it atomically.")
    action.add_argument("--sync", action="store_true", help="Transactionally replace one assembly in an existing index.")
    action.add_argument("--check", action="store_true", help="Validate and summarize an existing index.")
    parser.add_argument(
        "--assembly",
        action="append",
        default=[],
        help="Exact assembly ID; repeat for --full. --sync requires exactly one.",
    )
    parser.add_argument(
        "--representative-map",
        action="append",
        default=[],
        metavar="ASSEMBLY_ID=PATH",
        help="Explicit gene-to-representative-transcript TSV for one assembly.",
    )
    parser.add_argument(
        "--deep",
        action="store_true",
        help="With --check, verify annotation SHA-256 instead of relying on path/stat metadata.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    db_root = args.db_root.resolve()
    output = (args.output or default_index_path(db_root)).resolve()
    representative_maps = parse_representative_maps(args.representative_map)
    assembly_ids = set(args.assembly)
    if args.sync and len(args.assembly) != 1:
        parser.error("--sync requires exactly one --assembly")
    try:
        if args.full:
            counts = build_full_index(
                db_root=db_root,
                output=output,
                representative_maps=representative_maps,
                assembly_ids=assembly_ids or None,
            )
            payload = {"action": "full", "path": str(output), **counts}
        elif args.sync:
            results = sync_assemblies(
                db_root=db_root,
                index_path=output,
                assembly_ids=assembly_ids or None,
                representative_maps=representative_maps,
            )
            payload = {
                "action": "sync",
                "path": str(output),
                "updated": results,
                "summary": index_summary(output),
            }
        else:
            payload = {
                "action": "check",
                "path": str(output),
                **check_index(
                    db_root=db_root,
                    index_path=output,
                    representative_maps=representative_maps,
                    deep=args.deep,
                ),
            }
    except (FeatureIndexError, OSError, ValueError) as exc:
        parser.exit(1, f"ERROR: {exc}\n")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not args.check or payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
