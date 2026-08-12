from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from interface.build_pan_genome_db import (
    BuildConfig,
    build_database,
    classify_orthogroup,
)


def write_orthogroups_fixture(path: Path) -> Path:
    path.write_text(
        "Orthogroup\tGenomeA\tGenomeB\tGenomeC\n"
        "OG0001\tA_gene1, A_gene2\t\tC_gene1\n"
        "OG0002\tA_gene3\tB_gene1, B_gene2\t\n"
        "OG0003\t\tB_gene3\tC_gene2\n",
        encoding="utf-8",
    )
    return path


def fixture_config(source: Path, output: Path) -> BuildConfig:
    return BuildConfig(
        source_tsv=source,
        output_db=output,
        dataset_version="fixture-v1",
        expected_genome_count=3,
        expected_orthogroup_count=3,
        expected_gene_membership_count=8,
        expected_core_orthogroup_count=0,
        expected_soft_core_orthogroup_count=0,
        expected_dispensable_orthogroup_count=3,
        expected_private_orthogroup_count=0,
    )


def test_build_database_imports_normalized_orthogroups(tmp_path) -> None:
    source = write_orthogroups_fixture(tmp_path / "Orthogroups.tsv")
    output = tmp_path / "output" / "pan_genome.sqlite"

    result = build_database(fixture_config(source, output))

    assert result["counts"] == {
        "genomes": 3,
        "orthogroups": 3,
        "gene_memberships": 8,
        "occupied_cells": 6,
        "orthogroup_categories": {
            "core": 0,
            "soft-core": 0,
            "dispensable": 3,
            "private": 0,
        },
    }
    assert result["source"]["name"] == "Orthogroups.tsv"
    assert len(result["source"]["sha256"]) == 64
    assert result["database_bytes"] == output.stat().st_size

    with sqlite3.connect(output) as conn:
        conn.row_factory = sqlite3.Row
        genomes = conn.execute(
            """
            SELECT name, display_order, gene_count, orthogroup_count
            FROM genomes ORDER BY display_order
            """
        ).fetchall()
        assert [tuple(row) for row in genomes] == [
            ("GenomeA", 0, 3, 2),
            ("GenomeB", 1, 3, 2),
            ("GenomeC", 2, 2, 2),
        ]
        group = conn.execute(
            """
            SELECT gene_count, genome_count, category
            FROM orthogroups WHERE name = 'OG0001'
            """
        ).fetchone()
        assert tuple(group) == (3, 2, "dispensable")
        memberships = conn.execute(
            """
            SELECT g.name, m.gene_id, o.name
            FROM orthogroup_members AS m
            JOIN genomes AS g USING(genome_pk)
            JOIN orthogroups AS o USING(orthogroup_pk)
            ORDER BY g.display_order, m.gene_id
            """
        ).fetchall()
        assert len(memberships) == 8
        assert tuple(memberships[0]) == ("GenomeA", "A_gene1", "OG0001")
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"


@pytest.mark.parametrize(
    ("genome_count", "expected"),
    [
        (1, "private"),
        (2, "dispensable"),
        (121, "dispensable"),
        (122, "soft-core"),
        (134, "soft-core"),
        (135, "core"),
    ],
)
def test_classify_orthogroup_boundaries(genome_count: int, expected: str) -> None:
    assert classify_orthogroup(genome_count=genome_count, total_genomes=135) == expected


def test_classify_orthogroup_rejects_empty_group() -> None:
    with pytest.raises(ValueError, match="outside 1-135"):
        classify_orthogroup(genome_count=0, total_genomes=135)


def test_build_database_rejects_duplicate_genome_gene_membership(tmp_path) -> None:
    source = tmp_path / "Orthogroups.tsv"
    source.write_text(
        "Orthogroup\tGenomeA\nOG0001\tA_gene1\nOG0002\ta_GENE1\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate orthogroup or genome/gene"):
        build_database(
            BuildConfig(
                source_tsv=source,
                output_db=tmp_path / "pan_genome.sqlite",
                dataset_version="fixture-v1",
                expected_genome_count=1,
                expected_orthogroup_count=2,
                expected_gene_membership_count=2,
            )
        )


def test_build_database_validates_shape_and_preserves_existing_output(tmp_path) -> None:
    source = tmp_path / "Orthogroups.tsv"
    source.write_text(
        "Orthogroup\tGenomeA\tGenomeB\nOG0001\tA_gene1\n",
        encoding="utf-8",
    )
    output = tmp_path / "pan_genome.sqlite"
    output.write_bytes(b"existing database placeholder")

    with pytest.raises(ValueError, match="has 2 columns; expected 3"):
        build_database(
            BuildConfig(
                source_tsv=source,
                output_db=output,
                dataset_version="fixture-v1",
                expected_genome_count=2,
                expected_orthogroup_count=None,
                expected_gene_membership_count=None,
            )
        )

    assert output.read_bytes() == b"existing database placeholder"


def test_build_database_validates_expected_counts(tmp_path) -> None:
    source = write_orthogroups_fixture(tmp_path / "Orthogroups.tsv")

    with pytest.raises(ValueError, match="expected 4 orthogroups, found 3"):
        build_database(
            BuildConfig(
                source_tsv=source,
                output_db=tmp_path / "pan_genome.sqlite",
                dataset_version="fixture-v1",
                expected_genome_count=3,
                expected_orthogroup_count=4,
                expected_gene_membership_count=8,
            )
        )


def test_build_database_validates_expected_category_counts(tmp_path) -> None:
    source = write_orthogroups_fixture(tmp_path / "Orthogroups.tsv")
    config = fixture_config(source, tmp_path / "pan_genome.sqlite")

    with pytest.raises(ValueError, match="expected 2 dispensable orthogroups, found 3"):
        build_database(
            replace(
                config,
                expected_dispensable_orthogroup_count=2,
            )
        )
