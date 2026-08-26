from __future__ import annotations

import json
import sqlite3
import zlib
from dataclasses import replace
from pathlib import Path

import pytest

from interface.build_gene_catalog_db import BuildConfig, build_database, load_genes_json


def _write_text(path: Path, value: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    return path


def _prediction(transcript_id: str, grade: str) -> dict[str, object]:
    statistic = (
        "Technical runs are averaged within each sample_name/tissue; tissue mean "
        "and sample SD are calculated across those source means."
    )
    return {
        "status": "ok",
        "potato_gene_id": transcript_id,
        "evidence": {
            "task": "Predict one potato gene function from summarized evidence",
            "potato_gene_id": transcript_id,
            "potato_gene_names": [],
            "potato_function_evidence": {
                "source_species": "Solanum tuberosum",
                "query_gene": transcript_id,
                "resolved_gene_name": transcript_id,
                "function_summary": "No direct evidence.",
                "citations": [],
            },
            "homolog_function_evidence": {},
            "expression_by_tissue": [
                {
                    "tissue": "leaf",
                    "mean_tpm": 2.5,
                    "sd_tpm": 0.5,
                    "n_sources": 2,
                    "n_runs": 4,
                }
            ],
            "expression_statistic": statistic,
        },
        "prediction": {
            "potato_gene_id": transcript_id,
            "predicted_function": f"Predicted function for {transcript_id}.",
            "reliability_grade": grade,
            "grade_reason": "Fixture evidence.",
        },
    }


def _run_report(predicted_genes: list[str]) -> dict[str, object]:
    return {
        "genes": predicted_genes,
        "unique_homolog_targets": {},
        "status": "ok",
        "predicted_gene_count": len(predicted_genes),
        "predicted_genes": predicted_genes,
        "blocked_gene_count": 0,
        "blocked_genes": [],
        "source_and_llm_errors": [],
        "error_count": 0,
        "predictions_jsonl": "predictions.jsonl",
        "predictions_tsv": "predictions.tsv",
    }


def _build_fixture(tmp_path: Path) -> BuildConfig:
    source = tmp_path / "sources"
    output = tmp_path / "output" / "gene_catalog.sqlite"
    gene_a = "DM8.2_chr01G00010"
    gene_b = "DM8.2_chr01G00020"
    trans_a1 = f"{gene_a}.1"
    trans_a2 = f"{gene_a}.2"
    trans_b1 = f"{gene_b}.1"

    genes_json = _write_text(
        source / "genes.json",
        json.dumps(
            [
                {
                    "gene_id": gene_a,
                    "gene_symbols": ["PAL", "PAL", "SP6A(Incorrect)"],
                    "reported_ids": [
                        "OLD1(blast identity:100%)",
                        "OLD1(blast identity:100.00%)",
                    ],
                    "paperIDs": ["P1", "P2"],
                },
                {
                    "gene_id": gene_b,
                    "gene_symbols": ["PAL"],
                    "reported_ids": ["OLD2"],
                    "paperIDs": [],
                },
            ],
            indent=2,
        ),
    )
    paper_metadata = _write_text(
        source / "papers.txt",
        "# fixture\n"
        "10.1000/ABC\tP1.pdf\tFixture paper\n"
        "10.1000/abc\tP2.pdf\tFixture paper.\n",
    )
    transcript_map = _write_text(
        source / "transcripts.txt",
        "#GeneID\tRepresentative_transID\tAlternative_transID\n"
        f"{gene_a}\t{trans_a1}\t{trans_a2}\n"
        f"{gene_b}\t{trans_b1}\t-\n",
    )
    gff = _write_text(
        source / "DMv82.gff3",
        "##gff-version 3\n"
        f"chr01\tDM8.2\tgene\t10\t30\t.\t+\t.\tID={gene_a}\n"
        f"chr01\tDM8.2\tmRNA\t10\t30\t.\t+\t.\tID={trans_a1};Parent={gene_a}\n"
        f"chr01\tDM8.2\tCDS\t10\t15\t.\t+\t0\tParent={trans_a1}\n"
        f"chr01\tDM8.2\tmRNA\t10\t30\t.\t+\t.\tID={trans_a2};Parent={gene_a}\n"
        f"chr01\tDM8.2\tCDS\t10\t15\t.\t+\t0\tParent={trans_a2}\n"
        f"chr01\tDM8.2\tgene\t50\t70\t.\t-\t.\tID={gene_b}\n"
        f"chr01\tDM8.2\tmRNA\t50\t70\t.\t-\t.\tID={trans_b1};Parent={gene_b}\n"
        f"chr01\tDM8.2\tCDS\t65\t70\t.\t-\t0\tParent={trans_b1}\n",
    )

    bases = list("A" * 100)
    bases[9:15] = list("ATGAAA")
    bases[64:70] = list("GGGCAT")
    chromosome = "".join(bases)
    genome = _write_text(
        source / "DMv82.fa",
        f">chr01\n{chromosome[:50]}\n{chromosome[50:]}\n",
    )
    genome_fai = _write_text(source / "DMv82.fa.fai", "chr01\t100\t7\t50\t51\n")
    cds = _write_text(
        source / "cds.fa",
        ">unrelated\nATG\n"
        f">{trans_a1}\nATGAAA\n"
        f">{trans_a2}\nATGAAA\n"
        f">{trans_b1}\nATGCCC\n",
    )
    protein = _write_text(
        source / "pep.fa",
        ">unrelated\nM\n"
        f">{trans_a1}\nMK\n"
        f">{trans_a2}\nMK\n"
        f">{trans_b1}\nMP\n",
    )
    annotations = _write_text(
        source / "annotations.txt",
        "GeneID\tGO\tKEGG\tAnnotation\n"
        f"{gene_a}\tGO:0000001; GO:0000002\tK00001\tIPR000001 Fixture domain\n",
    )
    similarity = _write_text(
        source / "hits.txt",
        "QueryID\tSubjectID\tIdentity%\tE-value\tDescription\n"
        f"{gene_a}\tUniRef100_FIXTURE\t99.0\t1e-10\tFixture protein\n"
        f"{gene_a}\tUniRef100_FIXTURE\t99.0\t1e-10\tFixture protein\n",
    )
    predictions = _write_text(
        source / "predictions.jsonl",
        "\n".join(
            json.dumps(item, ensure_ascii=False)
            for item in (_prediction(trans_a1, "F3"), _prediction(trans_b1, "U"))
        )
        + "\n",
    )
    prediction_readme = _write_text(source / "README.md", "# Fixture release\n")
    primary_report = _write_text(
        source / "primary.json",
        json.dumps(_run_report([trans_a1, trans_b1])),
    )
    retry_185_report = _write_text(
        source / "retry_185.json", json.dumps(_run_report([]))
    )
    retry_1_report = _write_text(
        source / "retry_1.json", json.dumps(_run_report([]))
    )
    return BuildConfig(
        genes_json=genes_json,
        paper_metadata=paper_metadata,
        transcript_map=transcript_map,
        gff=gff,
        genome_fasta=genome,
        genome_fai=genome_fai,
        cds_fasta=cds,
        protein_fasta=protein,
        annotations=annotations,
        similarity_hits=similarity,
        predictions=predictions,
        prediction_readme=prediction_readme,
        primary_run_report=primary_report,
        retry_185_run_report=retry_185_report,
        retry_1_run_report=retry_1_report,
        output_db=output,
        catalog_version="fixture-v1",
        expected_gene_count=2,
    )


def test_build_database_imports_normalized_catalog_and_atg_promoters(tmp_path) -> None:
    config = _build_fixture(tmp_path)
    result = build_database(config)

    assert result["counts"]["genes"] == 2
    assert result["counts"]["transcripts"] == 3
    assert result["counts"]["transcript_sequences"] == 10
    assert result["identifier_stats"]["duplicate_symbols_removed"] == 1
    assert result["identifier_stats"]["duplicate_reported_ids_removed"] == 1
    assert result["paper_stats"]["papers"] == 1
    assert result["paper_stats"]["local_paper_ids"] == 2
    assert result["paper_stats"]["duplicate_gene_doi_refs"] == 1
    assert result["similarity_stats"]["duplicate_rows_removed"] == 1
    assert result["sequence_stats"]["promoter_truncated"] == 2
    assert "genes_json" in result["sources"]
    assert "legacy_genes_db" not in result["sources"]

    with sqlite3.connect(config.output_db) as conn:
        conn.row_factory = sqlite3.Row
        assert conn.execute("pragma integrity_check").fetchone()[0] == "ok"
        assert conn.execute("pragma foreign_key_check").fetchall() == []
        reported = conn.execute(
            """
            select identifier,qualifier_type,qualifier_value
            from gene_identifiers
            where identifier_type='reported_id' and identifier='OLD1'
            """
        ).fetchall()
        assert len(reported) == 1
        assert reported[0]["qualifier_type"] == "blast_identity_pct"
        assert reported[0]["qualifier_value"] == 100.0
        symbols = conn.execute(
            """
            select gi.identifier
            from gene_identifiers gi
            join genes g on g.gene_pk=gi.gene_pk
            where g.gene_id=? and gi.identifier_type='gene_symbol'
            order by gi.display_order
            """,
            ("DM8.2_chr01G00010",),
        ).fetchall()
        assert [row["identifier"] for row in symbols] == ["PAL", "SP6A(Incorrect)"]

        promoter_rows = conn.execute(
            """
            select t.transcript_id,s.sequence_zlib,s.sequence_length,s.strand,
                   s.anchor_type,s.anchor_pos,s.region_start,s.region_end,
                   s.requested_length,s.was_truncated
            from transcript_sequences s
            join transcripts t on t.transcript_pk=s.transcript_pk
            where s.sequence_type='promoter_atg_upstream_2000'
            order by t.transcript_id
            """
        ).fetchall()
        assert len(promoter_rows) == 2
        plus, minus = promoter_rows
        assert (plus["strand"], plus["anchor_pos"], plus["region_start"], plus["region_end"]) == (
            "+",
            10,
            1,
            9,
        )
        assert zlib.decompress(plus["sequence_zlib"]).decode("ascii") == "A" * 9
        assert (minus["strand"], minus["anchor_pos"], minus["region_start"], minus["region_end"]) == (
            "-",
            70,
            71,
            100,
        )
        assert plus["anchor_type"] == minus["anchor_type"] == "ATG"
        assert plus["requested_length"] == minus["requested_length"] == 2000
        assert plus["was_truncated"] == minus["was_truncated"] == 1

        source_run = conn.execute(
            "select distinct source_run from gene_descriptions"
        ).fetchall()
        assert [row[0] for row in source_run] == ["primary"]


def test_failed_build_preserves_existing_database(tmp_path) -> None:
    config = _build_fixture(tmp_path)
    config.output_db.parent.mkdir(parents=True, exist_ok=True)
    original = b"existing catalog placeholder"
    config.output_db.write_bytes(original)

    with pytest.raises(ValueError, match="expected 3 genes"):
        build_database(replace(config, expected_gene_count=3))

    assert config.output_db.read_bytes() == original
    assert not list(config.output_db.parent.glob(f".{config.output_db.name}.*.tmp"))


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"gene_id": "G1"}, "must contain a list"),
        (
            [
                {
                    "gene_id": "G1",
                    "gene_symbols": "PAL",
                    "reported_ids": [],
                    "paperIDs": [],
                }
            ],
            "field 'gene_symbols' must be a list",
        ),
        (
            [
                {
                    "gene_id": "G1",
                    "gene_symbols": [],
                    "reported_ids": [],
                    "paperIDs": [],
                },
                {
                    "gene_id": "G1",
                    "gene_symbols": [],
                    "reported_ids": [],
                    "paperIDs": [],
                },
            ],
            "duplicate gene ID",
        ),
    ],
)
def test_load_genes_json_rejects_invalid_sources(
    tmp_path: Path, payload: object, message: str
) -> None:
    path = _write_text(tmp_path / "genes.json", json.dumps(payload))

    with pytest.raises(ValueError, match=message):
        load_genes_json(path)
