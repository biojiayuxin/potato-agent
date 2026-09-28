from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import sqlite3
from pathlib import Path

import pytest

from interface.build_genome_annotations_db import TF_ROOT, build_database


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".gz":
        path.write_bytes(gzip.compress(text.encode(), mtime=0))
    else:
        path.write_text(text)


def _tsv(path: Path, rows: list[dict]) -> None:
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]), delimiter="\t", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    _write(path, stream.getvalue())


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def freeze_manifests(root: Path) -> None:
    for directory, excluded in ((root, TF_ROOT), (root / TF_ROOT, None)):
        entries = []
        for path in sorted(directory.rglob("*")):
            relative = path.relative_to(directory)
            if not path.is_file() or path.name == "SHA256SUMS":
                continue
            if excluded and relative.is_relative_to(excluded):
                continue
            entries.append(f"{_digest(path)}  {relative.as_posix()}\n")
        _write(directory / "release/SHA256SUMS", "".join(entries))


def make_source(root: Path) -> Path:
    """Real source column shapes, including conflicting isoforms and no-CDS genes."""
    assembly = "monoploid/DMv8.2"
    _tsv(root / "metadata/selected_assemblies.tsv", [{"assembly_id": assembly}])
    metadata = root / "parent/metadata/transcripts/monoploid/DMv8.2.transcripts.tsv.gz"
    _tsv(metadata, [
        {"assembly_id": assembly, "transcript_id": "gene.1.1", "gene_id": "gene.1", "protein_length": 100, "status": "valid", "reason": ""},
        {"assembly_id": assembly, "transcript_id": "gene.1.2", "gene_id": "gene.1", "protein_length": 80, "status": "valid", "reason": ""},
        {"assembly_id": assembly, "transcript_id": "lnc-mRNA-1", "gene_id": "lnc", "protein_length": 0, "status": "no_cds", "reason": "No CDS feature references this transcript"},
    ])
    _write(root / "metadata/source_inputs.lock.json", json.dumps({"selected_assemblies": [{
        "assembly_id": assembly, "transcript_metadata": {"path": str(metadata), "sha256": _digest(metadata)},
    }]}))
    _tsv(root / "qc/final_counts_by_assembly.tsv", [{"assembly_id": assembly, "total_transcripts": 3,
        "valid_transcripts": 2, "hit_transcripts": 1, "no_hit_transcripts": 1,
        "extraction_exceptions": 1, "closure_status": "PASS"}])
    _write(root / "qc/G08_final_validation.json", json.dumps({"status": "PASS",
        "checks": {"transcript_partition": "PASS"}, "counts": {"total_transcripts": 3, "global_hit_proteins": 1}}))
    tf = root / TF_ROOT
    _tsv(tf / "qc/assembly_summary.tsv", [{"assembly_id": assembly, "total_genes": 2}])
    _write(tf / "qc/run_summary.json", json.dumps({"status": "PASS", "closure_checks": {"mapping_complete": True},
        "counts": {"genes_total": 2, "transcripts_total": 3, "transcripts_valid": 2,
                   "transcripts_exceptions": 1, "unique_proteins_total": 2, "genes_selected_tf": 1,
                   "unique_proteins_selected_tf": 1, "transcripts_selected_tf": 1,
                   "relevant_pfam_evidence_rows": 1},
        "software": {"interproscan": "5.74-105.0"}, "method_label": "local public rules",
        "ruleset_version": "test-v1", "limitations": ["Not experimental evidence"]}))
    rule = {"family": "WRKY", "confidence_grade": "A", "coverage_status": "public_pfam_rule_complete"}
    _tsv(tf / "rules/planttfdb_public_rules_v5.tsv", [rule, dict(rule, family="WOX", confidence_grade="U", coverage_status="requires_unpublished_selfbuilt_hmm")])
    _tsv(tf / "rules/supplemental_candidate_policy.tsv", [dict(rule, family="Homeobox TF candidate", confidence_grade="C", coverage_status="supplemental_candidate")])
    _tsv(tf / "qc/family_counts.tsv", [{"scope": scope, "family": "WRKY", "selected_genes": 1,
        "selected_transcripts": 1, "selected_unique_proteins": 1} for scope in ("ALL", assembly)])
    base_gene = {"assembly_id": assembly, "gene_id": "gene.1", "gene_decision": "selected_tf",
        "isoform_presence_conflict": "true", "family_conflict": "false", "total_transcript_count": 2,
        "valid_transcript_count": 2, "exception_transcript_count": 0, "distinct_protein_count": 2,
        "has_selected_tf_isoform": "true"}
    _tsv(tf / "results/gene_decisions.tsv.gz", [base_gene, dict(base_gene, gene_id="lnc",
        gene_decision="extraction_exception_only", isoform_presence_conflict="false", total_transcript_count=1,
        valid_transcript_count=0, exception_transcript_count=1, distinct_protein_count=0,
        has_selected_tf_isoform="false")])
    base_protein = {"global_unique_protein_id": "UPglobal1", "protein_length": 100, "has_any_interpro_hit": "true",
        "decision_status": "selected_tf", "is_tf_inclusive_result": "true", "tf_families": "WRKY",
        "confidence_grades": "A", "sequence_md5": "md5", "sequence_sha256": "sha256"}
    _tsv(tf / "results/protein_decisions.tsv.gz", [base_protein, dict(base_protein,
        global_unique_protein_id="UPglobal2", protein_length=80, has_any_interpro_hit="false",
        decision_status="not_selected", is_tf_inclusive_result="false", tf_families="-", confidence_grades="-")])
    transcripts = [{"assembly_id": assembly, "transcript_id": tid, "gene_id": "gene.1", "global_unique_protein_id": protein,
                    "sequence_md5": "md5", "sequence_sha256": "sha256"}
                   for tid, protein in (("gene.1.1", "UPglobal1"), ("gene.1.2", "UPglobal2"))]
    _tsv(tf / "results/transcript_decisions.tsv.gz", transcripts)
    _tsv(root / "metadata/transcript_to_selected_unique.tsv.gz", [dict(row,
        subset_unique_protein_id="UPglobal2" if index == 0 else "UPglobal1", protein_length=100 if index == 0 else 80)
        for index, row in enumerate(transcripts)])
    hit = "UPglobal1\tmd5\t100\tPfam\tPF03106\tWRKY DNA binding domain\t10\t40\t1E-20\tT\t<RUN_DATE>\tIPR003657\tWRKY transcription factor\tGO:0003700(Pfam)|GO:0006355\t-\n"
    _write(root / "results/unique/interproscan_global_unique.tsv.gz", hit + hit.replace("\t10\t40\t", "\t25\t55\t"))
    _tsv(root / "results/unique/no_match_global_unique.tsv.gz", [{"global_unique_protein_id": "UPglobal2", "protein_length": 80}])
    _tsv(tf / "results/tf_evidence.tsv.gz", [{"global_unique_protein_id": "UPglobal1", "signature_accession": "PF03106", "start": 10, "end": 40}])
    _tsv(root / f"results/by_assembly/{assembly}.interproscan.tsv.gz", [
        {"assembly_id": assembly, "transcript_id": "gene.1.1", "signature_accession": "PF03106",
         "go_terms": "GO:0003700(Pfam)", "pathways": "Reactome: R-TEST", "score": "1E-20"}])
    for suffix in ("no_match.tsv.gz", "extraction_exceptions.tsv.gz"):
        _write(root / f"results/by_assembly/{assembly}.{suffix}", "safe public table\n")
    for suffix in ("tf_genes.tsv.gz", "tf_transcripts.tsv.gz"):
        _write(tf / f"results/by_assembly/{assembly}.{suffix}", "safe public table\n")
    freeze_manifests(root)
    return root


def test_build_preserves_gene_links_global_proteins_and_overlapping_hits(tmp_path):
    source = make_source(tmp_path / "source")
    output = tmp_path / "release/annotations.sqlite"
    result = build_database(source, output, "test-v1", downloads_dir=output.parent / "downloads")
    assert result["counts"]["transcripts_total"] == 3
    with sqlite3.connect(output) as conn:
        assert conn.execute("SELECT gene_id,protein_id,status FROM transcripts WHERE transcript_id='gene.1.1'").fetchone() == ("gene.1", "UPglobal1", "valid")
        assert conn.execute("SELECT gene_id,protein_id,status FROM transcripts WHERE transcript_id='lnc-mRNA-1'").fetchone() == ("lnc", None, "no_cds")
        assert conn.execute("SELECT start,end FROM matches ORDER BY hit_id").fetchall() == [(10, 40), (25, 55)]
        assert conn.execute("SELECT term FROM protein_terms WHERE kind='go' ORDER BY term").fetchall() == [("GO:0003700",), ("GO:0006355",)]
        assert conn.execute("SELECT COUNT(*) FROM matches_fts WHERE matches_fts MATCH 'WRKY'").fetchone()[0] == 2
        assert conn.execute("SELECT coverage_status FROM tf_families WHERE family='WOX'").fetchone()[0] == "requires_unpublished_selfbuilt_hmm"
        public = conn.execute("SELECT metadata_json FROM metadata").fetchone()[0]
        assert str(tmp_path) not in public
        downloads = conn.execute("SELECT relative_path,sha256 FROM downloads").fetchall()
        assert len(downloads) == 7
        for relative, digest in downloads:
            assert _digest(output.parent / relative) == digest
        assert not any("lock" in name for name, _ in downloads)


def test_annotation_download_omits_pathways_and_keeps_other_columns(tmp_path):
    source = make_source(tmp_path / "source")
    output = tmp_path / "release/annotations.sqlite"
    build_database(source, output, "test-v3", downloads_dir=output.parent / "downloads")
    with sqlite3.connect(output) as conn:
        filename, relative = conn.execute("SELECT filename,relative_path FROM downloads WHERE category='annotations'").fetchone()
    assert filename.endswith(".domains.tsv.gz")
    with gzip.open(output.parent / relative, "rt") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        assert reader.fieldnames == ["assembly_id", "transcript_id", "signature_accession", "go_terms", "score"]
        assert list(reader) == [{"assembly_id": "monoploid/DMv8.2", "transcript_id": "gene.1.1",
                                 "signature_accession": "PF03106", "go_terms": "GO:0003700(Pfam)", "score": "1E-20"}]


def test_checksum_failure_preserves_existing_release(tmp_path):
    source = make_source(tmp_path / "source")
    output = tmp_path / "annotations.sqlite"
    output.write_bytes(b"previous release")
    with (source / "metadata/selected_assemblies.tsv").open("a") as handle:
        handle.write("changed\n")
    with pytest.raises(ValueError, match="Checksum mismatch"):
        build_database(source, output, "v2")
    assert output.read_bytes() == b"previous release"
    assert not list(tmp_path.glob(".annotations.sqlite-*.tmp"))


def test_pathways_omitted_without_collapsing_repeated_hits(tmp_path):
    source = make_source(tmp_path / "source")
    hits = source / "results/unique/interproscan_global_unique.tsv.gz"
    with gzip.open(hits, "rt") as handle:
        hit = next(csv.reader(handle, delimiter="\t"))
    long_pathways = 'Reactome: R-TEST|MetaCyc: "通路,β"; ' * 2000
    # Ignoring pathway values must preserve every domain hit, including duplicates.
    pathways = [long_pathways, "", "-", long_pathways, "B|A", "A|B", " A|B ", "", "-"]
    stream = io.StringIO()
    writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
    writer.writerows([*hit[:-1], value] for value in pathways)
    _write(hits, stream.getvalue())
    freeze_manifests(source)
    output = tmp_path / "annotations.sqlite"
    result = build_database(source, output, "v1")
    assert result["schema_version"] == 3
    assert result["counts"]["annotation_hits"] == len(pathways)
    with sqlite3.connect(output) as conn:
        assert not conn.execute("SELECT name FROM sqlite_master WHERE name LIKE '%pathway%'").fetchall()
        assert all("pathway" not in row[1] for row in conn.execute("PRAGMA table_info(matches)"))
        rows = conn.execute("SELECT * FROM matches ORDER BY hit_id").fetchall()
        assert len(rows) == len(pathways)
        assert all(row[1:] == rows[0][1:] for row in rows)
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
        assert conn.execute("SELECT schema_version FROM metadata").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM matches_fts WHERE matches_fts MATCH 'WRKY'").fetchone()[0] == len(pathways)


def test_per_gene_closure_detects_missing_no_cds_record(tmp_path):
    source = make_source(tmp_path / "source")
    metadata = source / "parent/metadata/transcripts/monoploid/DMv8.2.transcripts.tsv.gz"
    with gzip.open(metadata, "rt") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    rows[-1]["gene_id"] = "gene.1"
    _tsv(metadata, rows)
    lock_path = source / "metadata/source_inputs.lock.json"
    lock = json.loads(lock_path.read_text())
    lock["selected_assemblies"][0]["transcript_metadata"]["sha256"] = _digest(metadata)
    lock_path.write_text(json.dumps(lock))
    freeze_manifests(source)
    with pytest.raises(ValueError, match="Per-gene transcript closure"):
        build_database(source, tmp_path / "annotations.sqlite", "v1")


def test_locked_metadata_can_be_remapped_without_rewriting_lock(tmp_path):
    source = make_source(tmp_path / "source")
    moved = tmp_path / "parent_copy"
    (source / "parent").rename(moved)
    result = build_database(source, tmp_path / "annotations.sqlite", "v1", source_project_root=moved)
    assert result["counts"]["transcripts_exceptions"] == 1


def test_downloads_cannot_escape_release_directory(tmp_path):
    with pytest.raises(ValueError, match="beneath"):
        build_database(tmp_path / "source", tmp_path / "release/annotations.sqlite", "v1",
                       downloads_dir=tmp_path / "external")


def test_rejects_valid_checksum_but_inconsistent_sequence_identity(tmp_path):
    source = make_source(tmp_path / "source")
    hits = source / "results/unique/interproscan_global_unique.tsv.gz"
    with gzip.open(hits, "rt") as handle:
        content = handle.read()
    _write(hits, content.replace("\tmd5\t", "\twrong-md5\t"))
    freeze_manifests(source)
    with pytest.raises(ValueError, match="sequence MD5"):
        build_database(source, tmp_path / "annotations.sqlite", "v1")


@pytest.mark.parametrize("decision", ["false", "ambiguous", "unresolved"])
def test_inclusive_tf_decision_preserves_nonselected_states_and_families(tmp_path, decision):
    source = make_source(tmp_path / "source")
    proteins = source / TF_ROOT / "results/protein_decisions.tsv.gz"
    with gzip.open(proteins, "rt") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    rows[1]["is_tf_inclusive_result"] = decision
    rows[1]["decision_status"] = "not_selected" if decision == "false" else decision
    rows[1]["tf_families"] = "WRKY;Homeobox TF candidate" if decision != "false" else "-"
    _tsv(proteins, rows)
    freeze_manifests(source)
    output = tmp_path / "annotations.sqlite"
    result = build_database(source, output, "v1")
    # Only the true decision contributes to selected statistics, even when other
    # proteins retain ambiguous families for evidence-based querying.
    assert result["counts"]["unique_proteins_selected_tf"] == 1
    with sqlite3.connect(output) as conn:
        selected, raw = conn.execute("SELECT is_tf,data_json FROM proteins WHERE protein_id='UPglobal2'").fetchone()
        assert selected == 0
        assert json.loads(raw)["is_tf_inclusive_result"] == decision
        assert conn.execute("SELECT COUNT(*) FROM protein_families WHERE protein_id='UPglobal2'").fetchone()[0] == (0 if decision == "false" else 2)
