from __future__ import annotations

import gzip
import json
import sqlite3
from pathlib import Path

import pytest

import interface.genome_feature_index as feature_index_module
from interface.build_genome_feature_index import main as build_index_main
from interface.genome_feature_index import (
    AmbiguousFeatureError,
    FeatureIndexError,
    build_full_index,
    check_index,
    index_summary,
    load_fai,
    load_representative_map,
    resolve_feature,
    sync_assemblies,
)


def _write_assembly(
    root: Path,
    *,
    assembly_id: str,
    annotation_lines: list[str],
    seqids: dict[str, int] | None = None,
) -> dict[str, object]:
    seqids = seqids or {"chr1": 2000}
    directory = root / assembly_id
    reference_dir = directory / "reference"
    annotation_dir = directory / "annotation"
    reference_dir.mkdir(parents=True)
    annotation_dir.mkdir(parents=True)
    sample = assembly_id.split("/", 1)[1]
    reference = reference_dir / f"{sample}.fa.bgz"
    reference.write_bytes(b"reference-placeholder")
    fai = Path(str(reference) + ".fai")
    fai.write_text(
        "".join(f"{name}\t{length}\t0\t80\t81\n" for name, length in seqids.items()),
        encoding="utf-8",
    )
    annotation = annotation_dir / f"{sample}.gff3.bgz"
    with gzip.open(annotation, "wt", encoding="utf-8") as handle:
        handle.write("##gff-version 3\n")
        handle.write("\n".join(annotation_lines) + "\n")
    return {
        "id": assembly_id,
        "sample": sample,
        "reference": str(reference.relative_to(root)),
        "fai": str(fai.relative_to(root)),
        "annotation": str(annotation.relative_to(root)),
    }


def _write_manifest(root: Path, assemblies: list[dict[str, object]]) -> None:
    (root / "assemblies.json").write_text(
        json.dumps({"assemblies": assemblies}), encoding="utf-8"
    )


def _basic_annotation(prefix: str = "") -> list[str]:
    gene = f"{prefix}gene1"
    tx1 = f"{prefix}tx1"
    tx2 = f"{prefix}tx2"
    # Child rows deliberately precede their parent rows, matching coordinate-sorted GFFs.
    return [
        f"chr1\tsrc\texon\t100\t150\t.\t+\t.\tParent={tx1}",
        f"chr1\tsrc\tCDS\t110\t140\t.\t+\t0\tParent={tx1}",
        f"chr1\tsrc\tgene\t100\t500\t.\t+\t.\tID={gene};Name={prefix}GeneAlias",
        f"chr1\tsrc\tmRNA\t100\t300\t.\t+\t.\tID={tx1};Parent={gene}",
        f"chr1\tsrc\texon\t200\t300\t.\t+\t.\tParent={tx1}",
        f"chr1\tsrc\tCDS\t200\t260\t.\t+\t2\tParent={tx1}",
        f"chr1\tsrc\tmRNA\t100\t500\t.\t+\t.\tID={tx2};Parent={gene};Name={prefix}TxAlias",
        f"chr1\tsrc\texon\t100\t500\t.\t+\t.\tParent={tx2}",
        f"chr1\tsrc\tCDS\t120\t450\t.\t+\t0\tParent={tx2}",
    ]


def test_full_index_resolves_ids_aliases_and_longest_cds(tmp_path: Path) -> None:
    assembly = _write_assembly(
        tmp_path,
        assembly_id="monoploid/Test",
        annotation_lines=_basic_annotation(),
    )
    _write_manifest(tmp_path, [assembly])
    output = tmp_path / "feature_index.sqlite"

    counts = build_full_index(db_root=tmp_path, output=output)
    resolved = resolve_feature(
        output, assembly_id="monoploid/Test", query_id="GeneAlias"
    )

    assert counts == {"assemblies": 1, "genes": 1, "transcripts": 2}
    assert resolved["query"] == {
        "id": "GeneAlias",
        "matchedBy": "alias",
        "resolvedType": "gene",
        "resolvedId": "gene1",
    }
    assert resolved["gene"]["representativeTranscriptId"] == "tx2"
    assert [item["id"] for item in resolved["transcripts"]] == ["tx2", "tx1"]
    representative = resolved["transcripts"][0]
    assert representative["representativeSource"] == "longest_cds"
    assert representative["codingStart"] == 120
    assert representative["cdsFivePrimePosition"] == 120
    assert representative["cds"] == [
        {
            "order": 1,
            "refName": "chr1",
            "start": 120,
            "end": 450,
            "strand": "+",
            "phase": 0,
        }
    ]


def test_explicit_representative_map_and_negative_strand_order(tmp_path: Path) -> None:
    lines = [
        "chr1\tsrc\tgene\t100\t900\t.\t-\t.\tID=geneN",
        "chr1\tsrc\tmRNA\t100\t900\t.\t-\t.\tID=txA;Parent=geneN",
        "chr1\tsrc\tmRNA\t100\t900\t.\t-\t.\tID=txB;Parent=geneN",
        "chr1\tsrc\texon\t100\t200\t.\t-\t.\tParent=txA,txB",
        "chr1\tsrc\texon\t700\t900\t.\t-\t.\tParent=txA,txB",
        "chr1\tsrc\tCDS\t120\t180\t.\t-\t1\tParent=txA",
        "chr1\tsrc\tCDS\t720\t880\t.\t-\t0\tParent=txA",
        "chr1\tsrc\tCDS\t150\t180\t.\t-\t1\tParent=txB",
        "chr1\tsrc\tCDS\t750\t850\t.\t-\t0\tParent=txB",
    ]
    assembly = _write_assembly(
        tmp_path, assembly_id="monoploid/Negative", annotation_lines=lines
    )
    _write_manifest(tmp_path, [assembly])
    mapping = tmp_path / "representatives.tsv"
    mapping.write_text(
        "GeneID\tRepre TransID\tAlt TransID\n"
        "geneN\ttxB\ttxA\n",
        encoding="utf-8",
    )
    output = tmp_path / "feature_index.sqlite"

    build_full_index(
        db_root=tmp_path,
        output=output,
        representative_maps={"monoploid/Negative": mapping},
    )
    resolved = resolve_feature(
        output, assembly_id="monoploid/Negative", query_id="geneN"
    )

    assert resolved["gene"]["representativeTranscriptId"] == "txB"
    tx_b = resolved["transcripts"][0]
    assert tx_b["representativeSource"] == "mapping"
    assert tx_b["codingStart"] == 850
    assert tx_b["cdsFivePrimePosition"] == 850
    assert [(item["start"], item["end"]) for item in tx_b["exons"]] == [
        (700, 900),
        (100, 200),
    ]
    assert [(item["start"], item["end"]) for item in tx_b["cds"]] == [
        (750, 850),
        (150, 180),
    ]


def test_manifest_representative_map_is_relative_to_database_root(tmp_path: Path) -> None:
    assembly = _write_assembly(
        tmp_path,
        assembly_id="monoploid/DMv8.2",
        annotation_lines=_basic_annotation(),
    )
    mapping = tmp_path / "metadata" / "representative-maps" / "DMv8.2.tsv"
    mapping.parent.mkdir(parents=True)
    mapping.write_text("gene1\ttx1\n", encoding="utf-8")
    assembly["representativeMap"] = str(mapping.relative_to(tmp_path))
    _write_manifest(tmp_path, [assembly])
    output = tmp_path / "feature_index.sqlite"

    build_full_index(db_root=tmp_path, output=output)

    resolved = resolve_feature(
        output, assembly_id="monoploid/DMv8.2", query_id="gene1"
    )
    assert resolved["gene"]["representativeTranscriptId"] == "tx1"
    assert resolved["transcripts"][0]["representativeSource"] == "mapping"


def test_unambiguous_gff_representative_marker_precedes_longest_cds(
    tmp_path: Path,
) -> None:
    lines = [
        line.replace("ID=tx1;Parent=gene1", "ID=tx1;Parent=gene1;representative=true")
        for line in _basic_annotation()
    ]
    assembly = _write_assembly(
        tmp_path,
        assembly_id="monoploid/Test",
        annotation_lines=lines,
    )
    _write_manifest(tmp_path, [assembly])
    output = tmp_path / "feature_index.sqlite"

    build_full_index(db_root=tmp_path, output=output)

    resolved = resolve_feature(
        output, assembly_id="monoploid/Test", query_id="gene1"
    )
    assert resolved["gene"]["representativeTranscriptId"] == "tx1"
    assert resolved["transcripts"][0]["representativeSource"] == "gff_attribute"


def test_known_assembly_without_representative_map_uses_longest_cds(
    tmp_path: Path,
) -> None:
    assembly = _write_assembly(
        tmp_path,
        assembly_id="monoploid/DMv8.2",
        annotation_lines=_basic_annotation(),
    )
    _write_manifest(tmp_path, [assembly])
    output = tmp_path / "feature_index.sqlite"

    build_full_index(db_root=tmp_path, output=output)

    resolved = resolve_feature(
        output, assembly_id="monoploid/DMv8.2", query_id="gene1"
    )
    assert resolved["gene"]["representativeTranscriptId"] == "tx2"
    assert resolved["transcripts"][0]["representativeSource"] == "longest_cds"


def test_sync_after_manifest_map_removal_reselects_longest_cds(
    tmp_path: Path,
) -> None:
    assembly = _write_assembly(
        tmp_path,
        assembly_id="monoploid/DMv8.2",
        annotation_lines=_basic_annotation(),
    )
    mapping = tmp_path / "metadata" / "representative-maps" / "DMv8.2.tsv"
    mapping.parent.mkdir(parents=True)
    mapping.write_text("gene1\ttx1\n", encoding="utf-8")
    assembly["representativeMap"] = str(mapping.relative_to(tmp_path))
    _write_manifest(tmp_path, [assembly])
    output = tmp_path / "feature_index.sqlite"
    build_full_index(db_root=tmp_path, output=output)

    assembly.pop("representativeMap")
    _write_manifest(tmp_path, [assembly])

    stale = check_index(db_root=tmp_path, index_path=output)
    assert stale["staleAssemblies"] == ["monoploid/DMv8.2"]

    sync_assemblies(
        db_root=tmp_path,
        index_path=output,
        assembly_ids={"monoploid/DMv8.2"},
    )
    resolved = resolve_feature(
        output, assembly_id="monoploid/DMv8.2", query_id="gene1"
    )
    assert resolved["gene"]["representativeTranscriptId"] == "tx2"
    assert resolved["transcripts"][0]["representativeSource"] == "longest_cds"


def test_manifest_representative_map_cannot_escape_database_root(tmp_path: Path) -> None:
    assembly = _write_assembly(
        tmp_path,
        assembly_id="monoploid/DMv8.2",
        annotation_lines=_basic_annotation(),
    )
    outside = tmp_path.parent / "outside-representatives.tsv"
    outside.write_text("gene1\ttx1\n", encoding="utf-8")
    assembly["representativeMap"] = "../outside-representatives.tsv"
    _write_manifest(tmp_path, [assembly])

    with pytest.raises(FeatureIndexError, match="path escapes database root"):
        build_full_index(db_root=tmp_path, output=tmp_path / "feature_index.sqlite")


def test_opposite_strand_alternative_transcript_is_preserved(tmp_path: Path) -> None:
    lines = [
        "chr1\tsrc\tgene\t100\t900\t.\t-\t.\tID=geneN",
        "chr1\tsrc\tmRNA\t100\t900\t.\t-\t.\tID=txRepresentative;Parent=geneN",
        "chr1\tsrc\tCDS\t700\t850\t.\t-\t0\tParent=txRepresentative",
        "chr1\tsrc\tmRNA\t200\t500\t.\t+\t.\tID=txOpposite;Parent=geneN",
        "chr1\tsrc\tCDS\t250\t450\t.\t+\t0\tParent=txOpposite",
    ]
    assembly = _write_assembly(
        tmp_path, assembly_id="monoploid/Opposite", annotation_lines=lines
    )
    _write_manifest(tmp_path, [assembly])
    mapping = tmp_path / "representatives.tsv"
    mapping.write_text("geneN\ttxRepresentative\n", encoding="utf-8")
    output = tmp_path / "feature_index.sqlite"

    build_full_index(
        db_root=tmp_path,
        output=output,
        representative_maps={"monoploid/Opposite": mapping},
    )
    resolved = resolve_feature(
        output, assembly_id="monoploid/Opposite", query_id="txOpposite"
    )

    assert resolved["gene"]["strand"] == "-"
    assert resolved["gene"]["representativeTranscriptId"] == "txRepresentative"
    assert resolved["transcripts"][0]["id"] == "txOpposite"
    assert resolved["transcripts"][0]["strand"] == "+"


def test_same_ids_are_scoped_by_assembly(tmp_path: Path) -> None:
    left = _write_assembly(
        tmp_path,
        assembly_id="monoploid/Left",
        annotation_lines=_basic_annotation(),
    )
    right = _write_assembly(
        tmp_path,
        assembly_id="monoploid/Right",
        annotation_lines=_basic_annotation(),
    )
    _write_manifest(tmp_path, [left, right])
    output = tmp_path / "feature_index.sqlite"

    build_full_index(db_root=tmp_path, output=output)

    assert resolve_feature(output, assembly_id="monoploid/Left", query_id="gene1")[
        "assembly"
    ] == "monoploid/Left"
    assert resolve_feature(output, assembly_id="monoploid/Right", query_id="gene1")[
        "assembly"
    ] == "monoploid/Right"


def test_tetraploid_duplicate_ids_are_qualified_by_seqid(tmp_path: Path) -> None:
    assembly = _write_assembly(
        tmp_path,
        assembly_id="phased_tetraploid/Qualified",
        seqids={"chr1_hap1": 1000, "chr1_hap2": 1000},
        annotation_lines=[
            "chr1_hap1\tsrc\tgene\t100\t400\t.\t+\t.\tID=gene1",
            "chr1_hap1\tsrc\ttranscript\t100\t400\t.\t+\t.\tID=tx1;Parent=gene1",
            "chr1_hap1\tsrc\texon\t100\t400\t.\t+\t.\tParent=tx1",
            "chr1_hap1\tsrc\tCDS\t120\t360\t.\t+\t0\tParent=tx1",
            "chr1_hap2\tsrc\tgene\t500\t900\t.\t-\t.\tID=gene1",
            "chr1_hap2\tsrc\ttranscript\t500\t900\t.\t-\t.\tID=tx1;Parent=gene1",
            "chr1_hap2\tsrc\texon\t500\t900\t.\t-\t.\tParent=tx1",
            "chr1_hap2\tsrc\tCDS\t540\t880\t.\t-\t0\tParent=tx1",
        ],
    )
    assembly["geneCount"] = 2
    assembly["transcriptCount"] = 2
    _write_manifest(tmp_path, [assembly])
    output = tmp_path / "feature_index.sqlite"

    build_full_index(db_root=tmp_path, output=output)

    with pytest.raises(AmbiguousFeatureError) as gene_error:
        resolve_feature(
            output,
            assembly_id="phased_tetraploid/Qualified",
            query_id="gene1",
        )
    assert gene_error.value.candidates == (
        ("gene", "gene1@chr1_hap1"),
        ("gene", "gene1@chr1_hap2"),
    )
    with pytest.raises(AmbiguousFeatureError) as transcript_error:
        resolve_feature(
            output,
            assembly_id="phased_tetraploid/Qualified",
            query_id="tx1",
        )
    assert transcript_error.value.candidates == (
        ("transcript", "tx1@chr1_hap1"),
        ("transcript", "tx1@chr1_hap2"),
    )

    hap2 = resolve_feature(
        output,
        assembly_id="phased_tetraploid/Qualified",
        query_id="gene1@chr1_hap2",
    )
    assert hap2["gene"]["refName"] == "chr1_hap2"
    assert hap2["gene"]["representativeTranscriptId"] == "tx1@chr1_hap2"
    assert hap2["transcripts"][0]["cdsFivePrimePosition"] == 880
    assert hap2["transcripts"][0]["cds"][0]["start"] == 540


def test_ambiguous_alias_is_reported(tmp_path: Path) -> None:
    lines = _basic_annotation() + [
        "chr1\tsrc\tgene\t1000\t1200\t.\t+\t.\tID=gene2;Name=GeneAlias",
        "chr1\tsrc\tmRNA\t1000\t1200\t.\t+\t.\tID=tx3;Parent=gene2",
        "chr1\tsrc\texon\t1000\t1200\t.\t+\t.\tParent=tx3",
    ]
    assembly = _write_assembly(
        tmp_path, assembly_id="monoploid/Test", annotation_lines=lines
    )
    _write_manifest(tmp_path, [assembly])
    output = tmp_path / "feature_index.sqlite"
    build_full_index(db_root=tmp_path, output=output)

    with pytest.raises(AmbiguousFeatureError):
        resolve_feature(output, assembly_id="monoploid/Test", query_id="GeneAlias")


def test_failed_full_build_preserves_existing_output(tmp_path: Path) -> None:
    output = tmp_path / "feature_index.sqlite"
    output.write_bytes(b"existing-index")
    assembly = _write_assembly(
        tmp_path,
        assembly_id="monoploid/Broken",
        annotation_lines=[
            "chr1\tsrc\tgene\t1\t100\t.\t+\t.\tID=gene1",
            "chr1\tsrc\tmRNA\t1\t100\t.\t+\t.\tID=tx1;Parent=missing",
        ],
    )
    _write_manifest(tmp_path, [assembly])

    with pytest.raises(FeatureIndexError):
        build_full_index(db_root=tmp_path, output=output)

    assert output.read_bytes() == b"existing-index"


def test_cli_reports_builder_errors_without_masking_them(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc_info:
        build_index_main(
            [
                "--db-root",
                str(tmp_path),
                "--output",
                str(tmp_path / "missing.sqlite"),
                "--check",
            ]
        )

    assert exc_info.value.code == 1


def test_sync_replaces_only_selected_assembly(tmp_path: Path) -> None:
    left = _write_assembly(
        tmp_path,
        assembly_id="monoploid/Left",
        annotation_lines=_basic_annotation("L"),
    )
    right = _write_assembly(
        tmp_path,
        assembly_id="monoploid/Right",
        annotation_lines=_basic_annotation("R"),
    )
    _write_manifest(tmp_path, [left, right])
    output = tmp_path / "feature_index.sqlite"
    build_full_index(db_root=tmp_path, output=output)

    annotation = tmp_path / str(left["annotation"])
    with gzip.open(annotation, "wt", encoding="utf-8") as handle:
        handle.write("##gff-version 3\n")
        handle.write("chr1\tsrc\tgene\t1\t50\t.\t+\t.\tID=newGene\n")
        handle.write("chr1\tsrc\tmRNA\t1\t50\t.\t+\t.\tID=newTx;Parent=newGene\n")
        handle.write("chr1\tsrc\texon\t1\t50\t.\t+\t.\tParent=newTx\n")

    sync_assemblies(
        db_root=tmp_path,
        index_path=output,
        assembly_ids={"monoploid/Left"},
    )

    assert resolve_feature(output, assembly_id="monoploid/Left", query_id="newGene")[
        "gene"
    ]["id"] == "newGene"
    assert resolve_feature(output, assembly_id="monoploid/Right", query_id="Rgene1")[
        "gene"
    ]["id"] == "Rgene1"
    assert index_summary(output)["assemblies"] == 2


def test_sync_requires_exactly_one_assembly(tmp_path: Path) -> None:
    assembly = _write_assembly(
        tmp_path,
        assembly_id="monoploid/Test",
        annotation_lines=_basic_annotation(),
    )
    _write_manifest(tmp_path, [assembly])
    output = tmp_path / "feature_index.sqlite"
    build_full_index(db_root=tmp_path, output=output)

    with pytest.raises(FeatureIndexError, match="sync requires exactly one assembly"):
        sync_assemblies(db_root=tmp_path, index_path=output)


def test_sync_rolls_back_when_final_validation_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assembly = _write_assembly(
        tmp_path,
        assembly_id="monoploid/Test",
        annotation_lines=_basic_annotation(),
    )
    _write_manifest(tmp_path, [assembly])
    output = tmp_path / "feature_index.sqlite"
    build_full_index(db_root=tmp_path, output=output)

    annotation = tmp_path / str(assembly["annotation"])
    with gzip.open(annotation, "wt", encoding="utf-8") as handle:
        handle.write("##gff-version 3\n")
        handle.write("chr1\tsrc\tgene\t1\t50\t.\t+\t.\tID=newGene\n")
        handle.write("chr1\tsrc\tmRNA\t1\t50\t.\t+\t.\tID=newTx;Parent=newGene\n")
        handle.write("chr1\tsrc\texon\t1\t50\t.\t+\t.\tParent=newTx\n")

    def fail_validation(*_args: object, **_kwargs: object) -> dict[str, int]:
        raise FeatureIndexError("forced final validation failure")

    monkeypatch.setattr(feature_index_module, "validate_database", fail_validation)
    with pytest.raises(FeatureIndexError, match="forced final validation failure"):
        sync_assemblies(
            db_root=tmp_path,
            index_path=output,
            assembly_ids={"monoploid/Test"},
        )

    assert resolve_feature(output, assembly_id="monoploid/Test", query_id="gene1")[
        "gene"
    ]["id"] == "gene1"


def test_schema_validation_rejects_behavior_version_mismatch(tmp_path: Path) -> None:
    assembly = _write_assembly(
        tmp_path,
        assembly_id="monoploid/Test",
        annotation_lines=_basic_annotation(),
    )
    _write_manifest(tmp_path, [assembly])
    output = tmp_path / "feature_index.sqlite"
    build_full_index(db_root=tmp_path, output=output)
    with sqlite3.connect(output) as conn:
        conn.execute(
            "update metadata set value='old-rule' where key='representative_rule_version'"
        )

    with pytest.raises(
        FeatureIndexError, match="representative_rule_version mismatch"
    ):
        resolve_feature(output, assembly_id="monoploid/Test", query_id="gene1")


def test_compact_schema_does_not_create_one_row_per_segment(tmp_path: Path) -> None:
    assembly = _write_assembly(
        tmp_path,
        assembly_id="monoploid/Test",
        annotation_lines=_basic_annotation(),
    )
    _write_manifest(tmp_path, [assembly])
    output = tmp_path / "feature_index.sqlite"
    build_full_index(db_root=tmp_path, output=output)

    with sqlite3.connect(output) as conn:
        tables = {
            row[0]
            for row in conn.execute("select name from sqlite_master where type='table'")
        }
    assert "exons" not in tables
    assert "cds_segments" not in tables


def test_child_foreign_keys_have_supporting_indexes(tmp_path: Path) -> None:
    assembly = _write_assembly(
        tmp_path,
        assembly_id="monoploid/Test",
        annotation_lines=_basic_annotation(),
    )
    _write_manifest(tmp_path, [assembly])
    output = tmp_path / "feature_index.sqlite"
    build_full_index(db_root=tmp_path, output=output)

    with sqlite3.connect(output) as conn:
        conn.execute("pragma foreign_keys=on")
        representative_columns = [
            row[2]
            for row in conn.execute(
                "pragma index_info(ix_genes_representative_transcript_pk)"
            )
        ]
        gene_alias_columns = [
            row[2]
            for row in conn.execute("pragma index_info(ix_gene_alias_gene_pk)")
        ]
        transcript_alias_columns = [
            row[2]
            for row in conn.execute(
                "pragma index_info(ix_transcript_alias_transcript_pk)"
            )
        ]
        delete_plans = [
            row[3]
            for statement in (
                "explain query plan delete from genes where gene_pk=-1",
                "explain query plan delete from transcripts where transcript_pk=-1",
            )
            for row in conn.execute(statement)
        ]

    assert representative_columns == ["representative_transcript_pk"]
    assert gene_alias_columns == ["gene_pk"]
    assert transcript_alias_columns == ["transcript_pk"]
    assert not [detail for detail in delete_plans if detail.startswith("SCAN ")]


def test_named_representative_map_header_is_skipped(tmp_path: Path) -> None:
    mapping = tmp_path / "representatives.tsv"
    mapping.write_text(
        "GeneID\tRepre TransID\tAlt TransID\n"
        "gene1\ttx2\ttx1\n",
        encoding="utf-8",
    )

    assert load_representative_map(mapping) == {"gene1": "tx2"}


def test_ncrna_gene_and_transcript_are_indexed(tmp_path: Path) -> None:
    assembly = _write_assembly(
        tmp_path,
        assembly_id="phased_tetraploid/Noncoding",
        annotation_lines=[
            "chr1\tsrc\tncRNA_gene\t100\t400\t.\t-\t.\tID=ncGene",
            "chr1\tsrc\tncRNA\t100\t400\t.\t-\t.\tID=ncTx;Parent=ncGene",
            "chr1\tsrc\texon\t100\t200\t.\t-\t.\tParent=ncTx",
            "chr1\tsrc\texon\t300\t400\t.\t-\t.\tParent=ncTx",
        ],
    )
    assembly["geneCount"] = 1
    assembly["transcriptCount"] = 1
    _write_manifest(tmp_path, [assembly])
    output = tmp_path / "feature_index.sqlite"

    build_full_index(db_root=tmp_path, output=output)
    resolved = resolve_feature(
        output, assembly_id="phased_tetraploid/Noncoding", query_id="ncGene"
    )

    assert resolved["gene"]["featureType"] == "ncRNA_gene"
    assert resolved["gene"]["representativeTranscriptId"] == "ncTx"
    assert resolved["transcripts"][0]["representativeSource"] == "longest_exon"
    assert [(item["start"], item["end"]) for item in resolved["transcripts"][0]["exons"]] == [
        (300, 400),
        (100, 200),
    ]


def test_parentless_transcripts_use_controlled_synthetic_genes(tmp_path: Path) -> None:
    assembly = _write_assembly(
        tmp_path,
        assembly_id="phased_diploid/DH_Test",
        annotation_lines=[
            "chr1\tsrc\tmRNA\t100\t300\t.\t+\t.\tID=DH.gene.1",
            "chr1\tsrc\tCDS\t100\t180\t.\t+\t0\tParent=DH.gene.1",
            "chr1\tsrc\tmRNA\t100\t500\t.\t+\t.\tID=DH.gene.2",
            "chr1\tsrc\tCDS\t100\t450\t.\t+\t0\tParent=DH.gene.2",
            "chr1\tsrc\tmRNA\t700\t800\t.\t+\t.\tID=standalone",
            "chr1\tsrc\texon\t700\t800\t.\t+\t.\tParent=standalone",
        ],
    )
    assembly["geneCount"] = 2
    assembly["transcriptCount"] = 3
    _write_manifest(tmp_path, [assembly])
    output = tmp_path / "feature_index.sqlite"

    build_full_index(db_root=tmp_path, output=output)

    resolved = resolve_feature(
        output, assembly_id="phased_diploid/DH_Test", query_id="DH.gene"
    )
    assert resolved["gene"]["featureType"] == "synthetic_gene"
    assert resolved["gene"]["representativeTranscriptId"] == "DH.gene.2"
    assert resolve_feature(
        output, assembly_id="phased_diploid/DH_Test", query_id="standalone"
    )["gene"]["id"] == "standalone"
    with sqlite3.connect(output) as conn:
        assert conn.execute(
            "select synthetic_gene_count from assemblies"
        ).fetchone()[0] == 2


@pytest.mark.parametrize("fai_line", ["\t100\t0\t80\t81\n", "chr1\t0\t0\t80\t81\n", "chr1\t-1\t0\t80\t81\n"])
def test_load_fai_rejects_empty_names_and_nonpositive_lengths(
    tmp_path: Path, fai_line: str
) -> None:
    path = tmp_path / "bad.fai"
    path.write_text(fai_line, encoding="utf-8")

    with pytest.raises(FeatureIndexError):
        load_fai(path)


def test_check_reports_manifest_missing_orphan_and_stale(tmp_path: Path) -> None:
    left = _write_assembly(
        tmp_path,
        assembly_id="monoploid/Left",
        annotation_lines=_basic_annotation("L"),
    )
    _write_manifest(tmp_path, [left])
    output = tmp_path / "feature_index.sqlite"
    build_full_index(db_root=tmp_path, output=output)

    annotation = tmp_path / str(left["annotation"])
    with gzip.open(annotation, "at", encoding="utf-8") as handle:
        handle.write("#changed\n")
    stale = check_index(db_root=tmp_path, index_path=output)
    assert stale["ok"] is False
    assert stale["staleAssemblies"] == ["monoploid/Left"]

    right = dict(left)
    right["id"] = "monoploid/Right"
    _write_manifest(tmp_path, [right])
    mismatch = check_index(db_root=tmp_path, index_path=output)
    assert mismatch["missingAssemblies"] == ["monoploid/Right"]
    assert mismatch["orphanAssemblies"] == ["monoploid/Left"]
