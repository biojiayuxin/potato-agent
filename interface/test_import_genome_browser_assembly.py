from __future__ import annotations

import csv
import fcntl
import gzip
import json
import shutil
import subprocess
from io import StringIO
from pathlib import Path

import pytest

from interface.import_genome_browser_assembly import (
    TSV_COLUMNS,
    build_assembly,
    build_parser,
    import_assembly,
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


def test_gzip_gene_normalization_preserves_ids_children_and_unknown_phase(tmp_path: Path) -> None:
    source = tmp_path / "annotation.gff.gz"
    original = (
        "chr1_1\ts\tgene\t20\t30\t.\t+\t.\tID=g1\n"
        "chr1_1\ts\tmRNA\t10\t40\t.\t+\t.\tID=t1;Parent=g1\n"
        "chr1_1\ts\tCDS\t10\t40\t.\t+\t.\tID=c1;Parent=t1\n"
        "chr1_2\ts\tgene\t50\t60\t.\t-\t.\tID=g1\n"
        "chr1_2\ts\tmRNA\t45\t65\t.\t-\t.\tID=t1;Parent=g1\n"
    )
    with gzip.open(source, "wt") as handle:
        handle.write(original)
    report = tmp_path / "changes.tsv"
    output = tmp_path / "sorted.gff3"
    counts = validate_and_sort_gff3(
        source, output, {"chr1_1": 100, "chr1_2": 100}, tmp_path,
        gene_bounds_report=report,
    )
    assert counts == {
        "featureCount": 5, "geneCount": 2, "transcriptCount": 2, "normalizedGeneCount": 2,
    }
    records = [line.split("\t") for line in output.read_text().splitlines() if not line.startswith("#")]
    assert [(x[0], x[3], x[4]) for x in records if x[2] == "gene"] == [
        ("chr1_1", "10", "40"), ("chr1_2", "45", "65"),
    ]
    assert next(x for x in records if x[2] == "CDS")[7] == "."
    assert {"\t".join(x) for x in records if x[2] != "gene"} == {
        x for x in original.splitlines() if x.split("\t")[2] != "gene"
    }
    rows = list(csv.DictReader(StringIO(report.read_text()), delimiter="\t"))
    assert [row["old_start"] for row in rows] == ["20", "50"]
    with gzip.open(source, "rt") as handle:
        assert handle.read() == original

    validate_and_sort_gff3(source, output, {"chr1_1": 100, "chr1_2": 100}, tmp_path)
    assert "gene\t20\t30" in output.read_text()


def test_gene_normalization_rejects_strand_conflict(tmp_path: Path) -> None:
    source = tmp_path / "source.gff3"
    source.write_text(
        "chr1\ts\tgene\t20\t30\t.\t+\t.\tID=g\n"
        "chr1\ts\tmRNA\t10\t40\t.\t-\t.\tID=t;Parent=g\n"
    )
    with pytest.raises(ValueError, match="disagree on strand"):
        validate_and_sort_gff3(
            source, tmp_path / "sorted.gff3", {"chr1": 100}, tmp_path,
            gene_bounds_report=tmp_path / "changes.tsv",
        )


@pytest.fixture
def assembly_import_args(tmp_path: Path):
    tools = {}
    for name in ("bgzip", "tabix", "samtools"):
        executable = shutil.which(name)
        if executable is None:
            pytest.skip(f"{name} required for assembly import integration test")
        tools[name] = executable
    db = tmp_path / "db"
    db.mkdir()
    (db / "assemblies.json").write_text(json.dumps({"assemblies": [], "counts": {}}))
    (db / "assemblies.tsv").write_text("\t".join(TSV_COLUMNS) + "\n")
    (db / "README.md").write_text("Original database\n")
    fasta, gff = tmp_path / "sample.fa.gz", tmp_path / "sample.gff.gz"
    with gzip.open(fasta, "wt") as handle:
        for hap in range(1, 5):
            handle.write(f">chr01_{hap}\n" + "ACGT" * 25 + "\n")
    with gzip.open(gff, "wt") as handle:
        for hap in range(1, 5):
            handle.write(
                f"chr01_{hap}\ts\tgene\t10\t20\t.\t+\t.\tID=hap{hap}_g1\n"
                f"chr01_{hap}\ts\tmRNA\t5\t30\t.\t+\t.\tID=hap{hap}_g1.t1;Parent=hap{hap}_g1\n"
                f"chr01_{hap}\ts\tCDS\t5\t30\t.\t+\t.\tParent=hap{hap}_g1.t1\n"
            )
    return build_parser().parse_args([
        "--db-root", str(db), "--sample", "Test4", "--category", "phased_tetraploid",
        "--fasta", str(fasta), "--gff3", str(gff), "--normalize-gene-bounds", "--threads", "1",
        *[arg for name, path in tools.items() for arg in (f"--{name}", path)],
    ])


def test_import_compressed_tetraploid_with_queryable_indexes(assembly_import_args) -> None:
    args = assembly_import_args
    assembly = import_assembly(args)
    assert assembly["id"] == "phased_tetraploid/Test4"
    assert assembly["haplotypes"] == ["H1", "H2", "H3", "H4"]
    assert (assembly["totalBp"], assembly["geneCount"], assembly["transcriptCount"]) == (400, 4, 4)
    manifest = json.loads((args.db_root / "assemblies.json").read_text())
    assert manifest["counts"]["phasedTetraploidAssemblies"] == 1
    assert manifest["counts"]["sourceFastaFiles"] == 1
    with (args.db_root / "assemblies.tsv").open() as handle:
        row = next(csv.DictReader(handle, delimiter="\t"))
    assert row["haplotypes"] == "H1,H2,H3,H4"
    for key in ("reference", "fai", "gzi", "chromSizes", "annotation", "annotationIndex"):
        assert (args.db_root / assembly[key]).is_file()
    result = subprocess.check_output([
        args.samtools, "faidx", str(args.db_root / assembly["reference"]), "chr01_4:5-8",
    ], text=True)
    assert result == ">chr01_4:5-8\nACGT\n"
    annotation = subprocess.check_output([
        args.tabix, str(args.db_root / assembly["annotation"]), "chr01_4:5-30",
    ], text=True)
    assert "gene\t5\t30" in annotation
    assert "CDS\t5\t30\t.\t+\t." in annotation
    sample = json.loads((args.db_root / assembly["directory"] / "sample.json").read_text())
    assert sample["annotationProcessing"]["normalizedGeneCount"] == 4
    with pytest.raises(FileExistsError):
        import_assembly(args)


def test_import_rolls_back_files_if_manifest_publication_fails(assembly_import_args, monkeypatch) -> None:
    from interface import import_genome_browser_assembly as module

    args = assembly_import_args
    original = {name: (args.db_root / name).read_bytes() for name in ("assemblies.json", "assemblies.tsv", "README.md")}
    atomic_write = module.atomic_write_text

    def fail_manifest(path, content):
        if path.name == "assemblies.json":
            raise OSError("simulated publication failure")
        atomic_write(path, content)

    monkeypatch.setattr(module, "atomic_write_text", fail_manifest)
    with pytest.raises(OSError, match="simulated publication failure"):
        import_assembly(args)
    assert {name: (args.db_root / name).read_bytes() for name in original} == original
    assert list((args.db_root / "phased_tetraploid").iterdir()) == []


def test_import_lock_rejects_concurrent_writer(assembly_import_args) -> None:
    args = assembly_import_args
    with (args.db_root / ".assembly-import.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            import_assembly(args)


def test_corrupt_gzip_does_not_publish_assembly(assembly_import_args) -> None:
    args = assembly_import_args
    args.fasta.write_bytes(args.fasta.read_bytes()[:-8])
    with pytest.raises(EOFError):
        import_assembly(args)
    assert json.loads((args.db_root / "assemblies.json").read_text())["assemblies"] == []
    assert list((args.db_root / "phased_tetraploid").iterdir()) == []


def test_default_monoploid_import_keeps_original_gene_bounds(assembly_import_args) -> None:
    args = assembly_import_args
    for name in ("fasta", "gff3"):
        source = getattr(args, name)
        plain = source.with_suffix("")
        with gzip.open(source, "rb") as handle:
            plain.write_bytes(handle.read())
        setattr(args, name, plain)
    defaults = build_parser().parse_args([
        "--db-root", str(args.db_root), "--sample", "Default",
        "--fasta", str(args.fasta), "--gff3", str(args.gff3),
        "--bgzip", args.bgzip, "--tabix", args.tabix, "--samtools", args.samtools,
    ])
    assembly = import_assembly(defaults)
    assert assembly["id"] == "monoploid/Default"
    assert assembly["haplotypes"] == ["monoploid"]
    with gzip.open(args.db_root / assembly["annotation"], "rt") as handle:
        assert "gene\t10\t20" in handle.read()
    assert not (args.db_root / assembly["directory"] / "annotation/gene_bounds_changes.tsv").exists()
