from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator


DEFAULT_SOURCE_TSV = Path(
    "/mnt/data/potato_agent/work/pan-genome-260709/02_diamond_reuse/"
    "Results_pg_repre_fixed_a20/Orthogroups/Orthogroups.tsv"
)
DEFAULT_OUTPUT_DB = Path("/srv/pan_genome/current/pan_genome.sqlite")
DEFAULT_DATASET_VERSION = "pan-genome-260709-a20"
SCHEMA_VERSION = 2
EXPECTED_GENOME_COUNT = 135
EXPECTED_ORTHOGROUP_COUNT = 203_875
EXPECTED_GENE_MEMBERSHIP_COUNT = 4_570_577
EXPECTED_CORE_ORTHOGROUP_COUNT = 2_107
EXPECTED_SOFT_CORE_ORTHOGROUP_COUNT = 8_510
EXPECTED_DISPENSABLE_ORTHOGROUP_COUNT = 189_934
EXPECTED_PRIVATE_ORTHOGROUP_COUNT = 3_324
SOFT_CORE_MIN_GENOME_COUNT = 122
ORTHOGROUP_CATEGORIES = (
    "core",
    "soft-core",
    "dispensable",
    "private",
)
MAX_FIELD_SIZE = 64 * 1024 * 1024
MAX_IDENTIFIER_LENGTH = 1024


@dataclass(frozen=True)
class BuildConfig:
    source_tsv: Path = DEFAULT_SOURCE_TSV
    output_db: Path = DEFAULT_OUTPUT_DB
    dataset_version: str = DEFAULT_DATASET_VERSION
    expected_genome_count: int | None = EXPECTED_GENOME_COUNT
    expected_orthogroup_count: int | None = EXPECTED_ORTHOGROUP_COUNT
    expected_gene_membership_count: int | None = EXPECTED_GENE_MEMBERSHIP_COUNT
    expected_core_orthogroup_count: int | None = EXPECTED_CORE_ORTHOGROUP_COUNT
    expected_soft_core_orthogroup_count: int | None = (
        EXPECTED_SOFT_CORE_ORTHOGROUP_COUNT
    )
    expected_dispensable_orthogroup_count: int | None = (
        EXPECTED_DISPENSABLE_ORTHOGROUP_COUNT
    )
    expected_private_orthogroup_count: int | None = EXPECTED_PRIVATE_ORTHOGROUP_COUNT


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_identifier(value: str, label: str, line_number: int) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{label} is empty on line {line_number}")
    if len(value) > MAX_IDENTIFIER_LENGTH:
        raise ValueError(
            f"{label} exceeds {MAX_IDENTIFIER_LENGTH} characters on line {line_number}"
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{label} contains a control character on line {line_number}")
    return value


def _parse_members(cell: str, *, line_number: int, genome: str) -> list[str]:
    if not cell.strip():
        return []
    raw_members = cell.split(",")
    members = [member.strip() for member in raw_members]
    if any(not member for member in members):
        raise ValueError(
            f"empty gene identifier in genome {genome!r} on line {line_number}"
        )
    return [
        _validate_identifier(member, "gene identifier", line_number)
        for member in members
    ]


def open_output_database(output_db: Path) -> tuple[sqlite3.Connection, Path]:
    output_db = output_db.resolve()
    output_db.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{output_db.name}.", suffix=".tmp", dir=output_db.parent
    )
    os.close(fd)
    temp_path = Path(temp_name)
    conn = sqlite3.connect(temp_path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn, temp_path


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA foreign_keys = ON;
        PRAGMA journal_mode = OFF;
        PRAGMA synchronous = OFF;
        PRAGMA temp_store = MEMORY;

        CREATE TABLE pan_genome_metadata(
          singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
          schema_version INTEGER NOT NULL,
          dataset_version TEXT NOT NULL UNIQUE,
          built_at TEXT NOT NULL,
          source_name TEXT NOT NULL,
          source_sha256 TEXT NOT NULL CHECK(length(source_sha256) = 64),
          source_bytes INTEGER NOT NULL CHECK(source_bytes >= 0),
          counts_json TEXT NOT NULL CHECK(json_valid(counts_json))
        ) STRICT;

        CREATE TABLE genomes(
          genome_pk INTEGER PRIMARY KEY,
          name TEXT NOT NULL COLLATE NOCASE UNIQUE,
          display_order INTEGER NOT NULL UNIQUE CHECK(display_order >= 0),
          gene_count INTEGER NOT NULL DEFAULT 0 CHECK(gene_count >= 0),
          orthogroup_count INTEGER NOT NULL DEFAULT 0 CHECK(orthogroup_count >= 0)
        ) STRICT;

        CREATE TABLE orthogroups(
          orthogroup_pk INTEGER PRIMARY KEY,
          name TEXT NOT NULL COLLATE NOCASE UNIQUE,
          display_order INTEGER NOT NULL UNIQUE CHECK(display_order >= 0),
          gene_count INTEGER NOT NULL CHECK(gene_count >= 0),
          genome_count INTEGER NOT NULL CHECK(genome_count > 0),
          category TEXT NOT NULL CHECK(
            category IN ('core', 'soft-core', 'dispensable', 'private')
          )
        ) STRICT;

        CREATE TABLE orthogroup_members(
          genome_pk INTEGER NOT NULL REFERENCES genomes(genome_pk),
          gene_id TEXT NOT NULL COLLATE NOCASE,
          orthogroup_pk INTEGER NOT NULL REFERENCES orthogroups(orthogroup_pk),
          PRIMARY KEY(genome_pk, gene_id)
        ) STRICT, WITHOUT ROWID;
        """
    )


def _read_header(reader: csv.reader) -> list[str]:
    try:
        header = next(reader)
    except StopIteration as exc:
        raise ValueError("Orthogroups TSV is empty") from exc
    if len(header) < 2 or header[0].strip() != "Orthogroup":
        raise ValueError("first TSV column must be named 'Orthogroup'")
    genomes = [_validate_identifier(value, "genome name", 1) for value in header[1:]]
    normalized = [name.casefold() for name in genomes]
    if len(set(normalized)) != len(normalized):
        raise ValueError("Orthogroups TSV contains duplicate genome names")
    return genomes


def _iter_rows(
    reader: csv.reader, *, expected_columns: int
) -> Iterator[tuple[int, list[str]]]:
    for line_number, row in enumerate(reader, start=2):
        if len(row) != expected_columns:
            raise ValueError(
                f"line {line_number} has {len(row)} columns; expected {expected_columns}"
            )
        yield line_number, row


def _validate_expected(label: str, actual: int, expected: int | None) -> None:
    if expected is not None and actual != expected:
        raise ValueError(f"expected {expected} {label}, found {actual}")


def classify_orthogroup(*, genome_count: int, total_genomes: int) -> str:
    if genome_count < 1 or genome_count > total_genomes:
        raise ValueError(
            f"orthogroup genome count {genome_count} is outside 1-{total_genomes}"
        )
    if genome_count == total_genomes:
        return "core"
    if genome_count >= SOFT_CORE_MIN_GENOME_COUNT:
        return "soft-core"
    if genome_count >= 2:
        return "dispensable"
    return "private"


def ingest_tsv(conn: sqlite3.Connection, config: BuildConfig) -> dict[str, Any]:
    source_tsv = config.source_tsv.resolve()
    csv.field_size_limit(MAX_FIELD_SIZE)
    with source_tsv.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle, delimiter="\t")
        genomes = _read_header(reader)
        _validate_expected("genomes", len(genomes), config.expected_genome_count)

        conn.executemany(
            "INSERT INTO genomes(genome_pk, name, display_order) VALUES (?, ?, ?)",
            (
                (display_order + 1, name, display_order)
                for display_order, name in enumerate(genomes)
            ),
        )

        genome_gene_counts = [0] * len(genomes)
        genome_orthogroup_counts = [0] * len(genomes)
        orthogroup_count = 0
        gene_membership_count = 0
        occupied_cell_count = 0
        category_counts = {category: 0 for category in ORTHOGROUP_CATEGORIES}

        for line_number, row in _iter_rows(reader, expected_columns=len(genomes) + 1):
            orthogroup = _validate_identifier(
                row[0], "orthogroup identifier", line_number
            )
            orthogroup_pk = orthogroup_count + 1
            group_members: list[tuple[int, str, int]] = []
            group_genome_count = 0

            for genome_index, cell in enumerate(row[1:]):
                members = _parse_members(
                    cell, line_number=line_number, genome=genomes[genome_index]
                )
                if not members:
                    continue
                genome_pk = genome_index + 1
                group_genome_count += 1
                occupied_cell_count += 1
                genome_gene_counts[genome_index] += len(members)
                genome_orthogroup_counts[genome_index] += 1
                group_members.extend(
                    (genome_pk, gene_id, orthogroup_pk) for gene_id in members
                )

            try:
                category = classify_orthogroup(
                    genome_count=group_genome_count,
                    total_genomes=len(genomes),
                )
            except ValueError as exc:
                raise ValueError(
                    f"orthogroup {orthogroup!r} has no members on line {line_number}"
                ) from exc

            try:
                conn.execute(
                    """
                    INSERT INTO orthogroups(
                      orthogroup_pk, name, display_order, gene_count,
                      genome_count, category
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        orthogroup_pk,
                        orthogroup,
                        orthogroup_count,
                        len(group_members),
                        group_genome_count,
                        category,
                    ),
                )
                conn.executemany(
                    """
                    INSERT INTO orthogroup_members(genome_pk, gene_id, orthogroup_pk)
                    VALUES (?, ?, ?)
                    """,
                    group_members,
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(
                    f"duplicate orthogroup or genome/gene membership on line {line_number}"
                ) from exc

            orthogroup_count += 1
            gene_membership_count += len(group_members)
            category_counts[category] += 1

    _validate_expected(
        "orthogroups", orthogroup_count, config.expected_orthogroup_count
    )
    _validate_expected(
        "gene memberships",
        gene_membership_count,
        config.expected_gene_membership_count,
    )
    for category, expected in (
        ("core", config.expected_core_orthogroup_count),
        ("soft-core", config.expected_soft_core_orthogroup_count),
        ("dispensable", config.expected_dispensable_orthogroup_count),
        ("private", config.expected_private_orthogroup_count),
    ):
        _validate_expected(
            f"{category} orthogroups",
            category_counts[category],
            expected,
        )
    conn.executemany(
        """
        UPDATE genomes
        SET gene_count = ?, orthogroup_count = ?
        WHERE genome_pk = ?
        """,
        (
            (gene_count, genome_orthogroup_counts[index], index + 1)
            for index, gene_count in enumerate(genome_gene_counts)
        ),
    )
    return {
        "genomes": len(genomes),
        "orthogroups": orthogroup_count,
        "gene_memberships": gene_membership_count,
        "occupied_cells": occupied_cell_count,
        "orthogroup_categories": category_counts,
    }


def finalize_database(
    conn: sqlite3.Connection,
    *,
    config: BuildConfig,
    counts: dict[str, Any],
    source: dict[str, Any],
) -> dict[str, Any]:
    conn.executescript(
        """
        CREATE INDEX orthogroup_members_by_group
          ON orthogroup_members(orthogroup_pk, genome_pk, gene_id);
        CREATE INDEX orthogroup_members_by_gene
          ON orthogroup_members(gene_id, genome_pk, orthogroup_pk);
        CREATE INDEX orthogroups_by_category
          ON orthogroups(category, display_order);
        ANALYZE;
        """
    )
    built_at = (
        datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )
    conn.execute(
        """
        INSERT INTO pan_genome_metadata(
          singleton, schema_version, dataset_version, built_at,
          source_name, source_sha256, source_bytes, counts_json
        ) VALUES (1, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            SCHEMA_VERSION,
            config.dataset_version,
            built_at,
            source["name"],
            source["sha256"],
            source["bytes"],
            _compact_json(counts),
        ),
    )
    conn.commit()

    foreign_key_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_key_errors:
        raise ValueError(f"foreign key check failed: {foreign_key_errors[:3]}")
    integrity_check = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
    if integrity_check != "ok":
        raise ValueError(f"SQLite integrity check failed: {integrity_check}")
    quick_check = str(conn.execute("PRAGMA quick_check").fetchone()[0])
    if quick_check != "ok":
        raise ValueError(f"SQLite quick check failed: {quick_check}")
    return {"built_at": built_at, "source": source}


def build_database(config: BuildConfig) -> dict[str, Any]:
    source_tsv = config.source_tsv.resolve()
    if not source_tsv.is_file():
        raise FileNotFoundError(f"Orthogroups TSV not found: {source_tsv}")
    if not config.dataset_version.strip():
        raise ValueError("dataset version must not be empty")

    source_stat = source_tsv.stat()
    source = {
        "name": source_tsv.name,
        "sha256": _sha256_file(source_tsv),
        "bytes": source_stat.st_size,
    }

    output_db = config.output_db.resolve()
    conn, temp_path = open_output_database(output_db)
    try:
        create_schema(conn)
        counts = ingest_tsv(conn, config)
        metadata = finalize_database(
            conn,
            config=config,
            counts=counts,
            source=source,
        )
        final_source_stat = source_tsv.stat()
        if (
            final_source_stat.st_size != source_stat.st_size
            or final_source_stat.st_mtime_ns != source_stat.st_mtime_ns
        ):
            raise ValueError("Orthogroups TSV changed while the database was built")
        conn.close()
        temp_path.replace(output_db)
    except Exception:
        conn.close()
        temp_path.unlink(missing_ok=True)
        raise

    return {
        "output_db": str(output_db),
        "database_bytes": output_db.stat().st_size,
        "schema_version": SCHEMA_VERSION,
        "dataset_version": config.dataset_version,
        "built_at": metadata["built_at"],
        "counts": counts,
        "source": metadata["source"],
    }


def _optional_expected_count(value: str) -> int | None:
    count = int(value)
    if count < 0:
        raise argparse.ArgumentTypeError("expected count must be zero or greater")
    return None if count == 0 else count


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the Potato pan-genome Orthogroups SQLite query database."
    )
    parser.add_argument("--source-tsv", type=Path, default=DEFAULT_SOURCE_TSV)
    parser.add_argument(
        "--output-db",
        type=Path,
        default=Path(os.getenv("PAN_GENOME_DB_PATH") or DEFAULT_OUTPUT_DB),
    )
    parser.add_argument("--dataset-version", default=DEFAULT_DATASET_VERSION)
    parser.add_argument(
        "--expected-genomes",
        type=_optional_expected_count,
        default=EXPECTED_GENOME_COUNT,
        help="Expected genome count; use 0 to disable this check.",
    )
    parser.add_argument(
        "--expected-orthogroups",
        type=_optional_expected_count,
        default=EXPECTED_ORTHOGROUP_COUNT,
        help="Expected orthogroup count; use 0 to disable this check.",
    )
    parser.add_argument(
        "--expected-gene-memberships",
        type=_optional_expected_count,
        default=EXPECTED_GENE_MEMBERSHIP_COUNT,
        help="Expected membership count; use 0 to disable this check.",
    )
    parser.add_argument(
        "--expected-core-orthogroups",
        type=_optional_expected_count,
        default=EXPECTED_CORE_ORTHOGROUP_COUNT,
        help="Expected core orthogroup count; use 0 to disable this check.",
    )
    parser.add_argument(
        "--expected-soft-core-orthogroups",
        type=_optional_expected_count,
        default=EXPECTED_SOFT_CORE_ORTHOGROUP_COUNT,
        help="Expected soft-core orthogroup count; use 0 to disable this check.",
    )
    parser.add_argument(
        "--expected-dispensable-orthogroups",
        type=_optional_expected_count,
        default=EXPECTED_DISPENSABLE_ORTHOGROUP_COUNT,
        help="Expected dispensable orthogroup count; use 0 to disable this check.",
    )
    parser.add_argument(
        "--expected-private-orthogroups",
        type=_optional_expected_count,
        default=EXPECTED_PRIVATE_ORTHOGROUP_COUNT,
        help="Expected private orthogroup count; use 0 to disable this check.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = build_database(
        BuildConfig(
            source_tsv=args.source_tsv,
            output_db=args.output_db,
            dataset_version=args.dataset_version,
            expected_genome_count=args.expected_genomes,
            expected_orthogroup_count=args.expected_orthogroups,
            expected_gene_membership_count=args.expected_gene_memberships,
            expected_core_orthogroup_count=args.expected_core_orthogroups,
            expected_soft_core_orthogroup_count=args.expected_soft_core_orthogroups,
            expected_dispensable_orthogroup_count=(
                args.expected_dispensable_orthogroups
            ),
            expected_private_orthogroup_count=args.expected_private_orthogroups,
        )
    )
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
