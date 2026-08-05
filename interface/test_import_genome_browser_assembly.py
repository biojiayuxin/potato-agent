from __future__ import annotations

import json
from pathlib import Path

import pytest

from interface.import_genome_browser_assembly import (
    build_assembly,
    update_manifest,
    update_assemblies_tsv,
    validate_and_sort_gff3,
)


def test_validate_and_sort_gff3_adds_header_and_infers_gene_count(tmp_path: Path) -> None:
    source = tmp_path / "source.gff3"
    source.write_text(
        "chr2\tsource\tmRNA\t20\t30\t.\t+\t.\tID=tx2;Parent=gene2\n"
        "chr1\tsource\tmRNA\t10\t15\t.\t+\t.\tID=tx1;Parent=gene1\n"
        "chr1\tsource\texon\t10\t15\t.\t+\t.\tParent=tx1\n",
        encoding="utf-8",
    )
    output = tmp_path / "sorted.gff3"

    counts = validate_and_sort_gff3(
        source,
        output,
        {"chr1": 100, "chr2": 100},
        tmp_path,
    )

    assert counts == {"featureCount": 5, "geneCount": 2, "transcriptCount": 2}
    assert output.read_text(encoding="utf-8").splitlines() == [
        "##gff-version 3",
        "chr1\tsource\texon\t10\t15\t.\t+\t.\tParent=tx1",
        "chr1\tsource\tgene\t10\t15\t.\t+\t.\tID=gene1;Name=gene1",
        "chr1\tsource\tmRNA\t10\t15\t.\t+\t.\tID=tx1;Parent=gene1",
        "chr2\tsource\tgene\t20\t30\t.\t+\t.\tID=gene2;Name=gene2",
        "chr2\tsource\tmRNA\t20\t30\t.\t+\t.\tID=tx2;Parent=gene2",
    ]


def test_validate_and_sort_gff3_rejects_missing_non_gene_parent(tmp_path: Path) -> None:
    source = tmp_path / "source.gff3"
    source.write_text(
        "chr1\tsource\texon\t10\t15\t.\t+\t.\tParent=missing_transcript\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="cannot synthesize missing parent missing_transcript"):
        validate_and_sort_gff3(source, tmp_path / "sorted.gff3", {"chr1": 100}, tmp_path)


def test_manifest_and_tsv_are_updated_for_new_monoploid_assembly(tmp_path: Path) -> None:
    assembly = build_assembly(
        sample="DMv6.1",
        display_name="DM v6.1",
        fasta=tmp_path / "DM.fa",
        gff3=tmp_path / "DM.gff3",
        species="Solanum tuberosum group Phureja DM1-3 516 R44",
        doi="",
        sequence_lengths={"chr01": 10, "chr02": 20},
        annotation_counts={"featureCount": 6, "geneCount": 2, "transcriptCount": 3},
    )
    manifest = {
        "assemblies": [],
        "counts": {},
    }

    updated = update_manifest(manifest, assembly)
    tsv_header = "\t".join(
        (
            "id",
            "category",
            "ploidy",
            "sample",
            "display_name",
            "haplotypes",
            "total_bp",
            "ref_name_count",
            "gene_count",
            "transcript_count",
            "reference",
            "annotation",
            "species",
            "doi",
        )
    )
    existing_tsv = (
        tsv_header
        + "\nphased_diploid/Test\tphased_diploid\tphased_diploid\tTest\tTest (H1 + H2)"
        "\tH1,H2\t100\t2\t4\t5\tref.bgz\tannotation.bgz\tSolanum tuberosum\t\n"
    )
    tsv = update_assemblies_tsv(existing_tsv, assembly)

    assert updated["counts"] == {
        "assemblies": 1,
        "monoploidAssemblies": 1,
        "phasedDiploidAssemblies": 0,
        "phasedTetraploidAssemblies": 0,
        "sourceFastaFiles": 1,
    }
    assert json.loads(json.dumps(updated))["assemblies"][0]["totalBp"] == 30
    assert "monoploid/DMv6.1\tmonoploid\tmonoploid\tDMv6.1\tDM v6.1" in tsv
    assert "Test (H1 + H2)\tH1,H2" in tsv
