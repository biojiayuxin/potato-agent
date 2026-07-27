from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import unicodedata
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Iterator
from urllib.parse import unquote


DEFAULT_KNOWLEDGE_HUB_ROOT = Path("/home/jiayuxin/tmp/potato-knowledge-hub")
DEFAULT_DATA_ROOT = DEFAULT_KNOWLEDGE_HUB_ROOT / "data"
DEFAULT_GENES_DB = (
    DEFAULT_KNOWLEDGE_HUB_ROOT / "scripts" / "search_genes" / "genes.db"
)
DEFAULT_GENES_JSON = DEFAULT_DATA_ROOT / "genes_db" / "genes_db.260531.json"
DEFAULT_PAPER_METADATA = (
    DEFAULT_DATA_ROOT
    / "paperID_title_doi"
    / "paperID_title_doi.merge.260531_updates.embedded.txt"
)
DEFAULT_TRANSCRIPT_MAP = DEFAULT_DATA_ROOT / "GeneID_to_TransID.260605.txt"
DEFAULT_GFF = DEFAULT_DATA_ROOT / "DMv82.gff3"
DEFAULT_GENOME_FASTA = DEFAULT_DATA_ROOT / "DMv82.fa"
DEFAULT_GENOME_FAI = DEFAULT_DATA_ROOT / "DMv82.fa.fai"
DEFAULT_CDS_FASTA = DEFAULT_DATA_ROOT / "cds.fa"
DEFAULT_PROTEIN_FASTA = DEFAULT_DATA_ROOT / "pep.fa"
DEFAULT_ANNOTATIONS = DEFAULT_DATA_ROOT / "DMv82_annotations.geneID.txt"
DEFAULT_SIMILARITY_HITS = DEFAULT_DATA_ROOT / "uniprot_blastp.txt"
DEFAULT_PREDICTION_ROOT = Path("/home/jiayuxin/gene_function_prediction")
DEFAULT_PREDICTIONS = DEFAULT_PREDICTION_ROOT / "results_all" / "predictions.jsonl"
DEFAULT_PREDICTION_README = DEFAULT_PREDICTION_ROOT / "results_all" / "README.md"
DEFAULT_PRIMARY_RUN_REPORT = DEFAULT_PREDICTION_ROOT / "output" / "run_report.json"
DEFAULT_RETRY_185_RUN_REPORT = (
    DEFAULT_PREDICTION_ROOT / "output_retry_185" / "run_report.json"
)
DEFAULT_RETRY_1_RUN_REPORT = (
    DEFAULT_PREDICTION_ROOT / "output_retry_1" / "run_report.json"
)
DEFAULT_OUTPUT_DB = (
    Path.home() / "tmp" / "potato-gene-catalog" / "gene_catalog.sqlite"
)

SCHEMA_VERSION = 1
DATASET_ID = "DMv8.2"
ASSEMBLY = "DMv8.2"
EXPECTED_GENE_COUNT = 37_658
PROMOTER_LENGTH = 2_000
PREDICTION_RELEASE = "results_all-2026-07-25"

BLAST_IDENTITY_RE = re.compile(
    r"^(?P<identifier>.*?)\s*\(blast identity:\s*(?P<identity>[0-9.]+)%\)\s*$",
    re.IGNORECASE,
)
DOI_PREFIX_RE = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.I)

DNA_COMPLEMENT = str.maketrans(
    "ACGTRYKMSWBDHVNacgtrykmswbdhvn",
    "TGCAYRMKSWVHDBNtgcayrmkswvhdbn",
)
CODON_TABLE = {
    "TTT": "F", "TTC": "F", "TTA": "L", "TTG": "L",
    "TCT": "S", "TCC": "S", "TCA": "S", "TCG": "S",
    "TAT": "Y", "TAC": "Y", "TAA": "*", "TAG": "*",
    "TGT": "C", "TGC": "C", "TGA": "*", "TGG": "W",
    "CTT": "L", "CTC": "L", "CTA": "L", "CTG": "L",
    "CCT": "P", "CCC": "P", "CCA": "P", "CCG": "P",
    "CAT": "H", "CAC": "H", "CAA": "Q", "CAG": "Q",
    "CGT": "R", "CGC": "R", "CGA": "R", "CGG": "R",
    "ATT": "I", "ATC": "I", "ATA": "I", "ATG": "M",
    "ACT": "T", "ACC": "T", "ACA": "T", "ACG": "T",
    "AAT": "N", "AAC": "N", "AAA": "K", "AAG": "K",
    "AGT": "S", "AGC": "S", "AGA": "R", "AGG": "R",
    "GTT": "V", "GTC": "V", "GTA": "V", "GTG": "V",
    "GCT": "A", "GCC": "A", "GCA": "A", "GCG": "A",
    "GAT": "D", "GAC": "D", "GAA": "E", "GAG": "E",
    "GGT": "G", "GGC": "G", "GGA": "G", "GGG": "G",
}


@dataclass(frozen=True)
class BuildConfig:
    genes_db: Path = DEFAULT_GENES_DB
    genes_json: Path = DEFAULT_GENES_JSON
    paper_metadata: Path = DEFAULT_PAPER_METADATA
    transcript_map: Path = DEFAULT_TRANSCRIPT_MAP
    gff: Path = DEFAULT_GFF
    genome_fasta: Path = DEFAULT_GENOME_FASTA
    genome_fai: Path = DEFAULT_GENOME_FAI
    cds_fasta: Path = DEFAULT_CDS_FASTA
    protein_fasta: Path = DEFAULT_PROTEIN_FASTA
    annotations: Path = DEFAULT_ANNOTATIONS
    similarity_hits: Path = DEFAULT_SIMILARITY_HITS
    predictions: Path = DEFAULT_PREDICTIONS
    prediction_readme: Path = DEFAULT_PREDICTION_README
    primary_run_report: Path = DEFAULT_PRIMARY_RUN_REPORT
    retry_185_run_report: Path = DEFAULT_RETRY_185_RUN_REPORT
    retry_1_run_report: Path = DEFAULT_RETRY_1_RUN_REPORT
    output_db: Path = DEFAULT_OUTPUT_DB
    catalog_version: str = "DMv8.2-20260726"
    prediction_release: str = PREDICTION_RELEASE
    expected_gene_count: int | None = EXPECTED_GENE_COUNT


@dataclass(frozen=True)
class LegacyGene:
    gene_id: str
    symbols: tuple[str, ...]
    reported_ids: tuple[str, ...]
    paper_ids: tuple[str, ...]


@dataclass(frozen=True)
class TranscriptDefinition:
    gene_id: str
    transcript_id: str
    is_representative: bool
    display_order: int


@dataclass(frozen=True)
class Feature:
    seqid: str
    start: int
    end: int
    strand: str


@dataclass(frozen=True)
class CdsSegment:
    seqid: str
    start: int
    end: int
    strand: str
    phase: str


@dataclass(frozen=True)
class FaiEntry:
    length: int
    offset: int
    line_bases: int
    line_bytes: int


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def normalize_identifier(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    return " ".join(normalized.split())


def normalize_doi(value: str) -> str:
    return DOI_PREFIX_RE.sub("", normalize_identifier(value)).rstrip(".")


def _split_legacy_list(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(item.strip() for item in str(value).split(", ") if item.strip())


def _deduplicate(values: Iterable[str], *, key=normalize_identifier) -> tuple[list[str], int]:
    result: list[str] = []
    seen: set[Any] = set()
    duplicate_count = 0
    for value in values:
        normalized = key(value)
        if normalized in seen:
            duplicate_count += 1
            continue
        seen.add(normalized)
        result.append(value)
    return result, duplicate_count


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _count_lines(path: Path, *, encoding: str = "utf-8") -> int:
    with path.open("r", encoding=encoding) as handle:
        return sum(1 for _ in handle)


def _count_fasta_records(path: Path) -> int:
    with path.open("r", encoding="ascii") as handle:
        return sum(1 for line in handle if line.startswith(">"))


def _require_file(path: Path, label: str) -> Path:
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(f"{label} not found: {path}")
    return path


def _source_paths(config: BuildConfig) -> dict[str, Path]:
    return {
        "legacy_genes_db": _require_file(config.genes_db, "legacy genes database"),
        "legacy_genes_json": _require_file(config.genes_json, "legacy genes JSON"),
        "paper_metadata": _require_file(config.paper_metadata, "paper metadata"),
        "transcript_map": _require_file(config.transcript_map, "transcript mapping"),
        "dmv82_gff": _require_file(config.gff, "DMv8.2 GFF"),
        "dmv82_genome": _require_file(config.genome_fasta, "DMv8.2 genome FASTA"),
        "dmv82_genome_fai": _require_file(config.genome_fai, "DMv8.2 genome FASTA index"),
        "dmv82_cds": _require_file(config.cds_fasta, "CDS FASTA"),
        "dmv82_protein": _require_file(config.protein_fasta, "protein FASTA"),
        "dmv82_annotations": _require_file(config.annotations, "DMv8.2 annotations"),
        "uniref100_hits": _require_file(config.similarity_hits, "UniRef100 hits"),
        "function_predictions": _require_file(config.predictions, "function predictions"),
        "prediction_release_readme": _require_file(
            config.prediction_readme, "prediction release README"
        ),
        "prediction_run_primary": _require_file(
            config.primary_run_report, "primary prediction run report"
        ),
        "prediction_run_retry_185": _require_file(
            config.retry_185_run_report, "185-gene retry run report"
        ),
        "prediction_run_retry_1": _require_file(
            config.retry_1_run_report, "one-gene retry run report"
        ),
    }


def load_legacy_genes(path: Path) -> list[LegacyGene]:
    conn = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        table = conn.execute(
            "select sql from sqlite_master where type='table' and name='new_genes'"
        ).fetchone()
        if table is None:
            raise ValueError("legacy genes database does not contain new_genes")
        rows = conn.execute(
            """
            select gene_id, gene_symbol, ID_reported, refs, descriptions
            from new_genes
            order by gene_id
            """
        ).fetchall()
    finally:
        conn.close()

    genes: list[LegacyGene] = []
    seen: set[str] = set()
    for row in rows:
        gene_id = str(row["gene_id"] or "").strip()
        if not gene_id or gene_id in seen:
            raise ValueError(f"invalid or duplicate legacy gene ID: {gene_id!r}")
        if row["descriptions"] not in (None, ""):
            raise ValueError(
                f"legacy descriptions must remain empty; found data for {gene_id}"
            )
        seen.add(gene_id)
        genes.append(
            LegacyGene(
                gene_id=gene_id,
                symbols=_split_legacy_list(row["gene_symbol"]),
                reported_ids=_split_legacy_list(row["ID_reported"]),
                paper_ids=_split_legacy_list(row["refs"]),
            )
        )
    return genes


def validate_legacy_json(path: Path, genes: list[LegacyGene]) -> None:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, list):
        raise ValueError("legacy genes JSON must contain a list")
    expected = {
        gene.gene_id: {
            "gene_symbols": list(gene.symbols),
            "reported_ids": list(gene.reported_ids),
            "paperIDs": list(gene.paper_ids),
        }
        for gene in genes
    }
    if len(payload) != len(expected):
        raise ValueError(
            f"legacy genes JSON count differs from genes.db: {len(payload)} != {len(expected)}"
        )
    seen: set[str] = set()
    for index, row in enumerate(payload, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"legacy genes JSON item {index} is not an object")
        gene_id = str(row.get("gene_id") or "")
        if gene_id in seen or gene_id not in expected:
            raise ValueError(f"unexpected or duplicate gene in legacy JSON: {gene_id!r}")
        seen.add(gene_id)
        actual = {
            "gene_symbols": row.get("gene_symbols") or [],
            "reported_ids": row.get("reported_ids") or [],
            "paperIDs": row.get("paperIDs") or [],
        }
        if actual != expected[gene_id]:
            raise ValueError(f"genes.db and legacy JSON differ for {gene_id}")


def load_transcript_definitions(
    path: Path, gene_ids: set[str]
) -> tuple[list[TranscriptDefinition], dict[str, str]]:
    definitions: list[TranscriptDefinition] = []
    representative_by_gene: dict[str, str] = {}
    seen_transcripts: set[str] = set()
    seen_genes: set[str] = set()
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter="\t")
        for line_number, row in enumerate(reader, start=1):
            if not row or row[0].startswith("#"):
                continue
            if len(row) != 3:
                raise ValueError(f"invalid transcript mapping row {line_number}")
            gene_id, representative, alternatives = (item.strip() for item in row)
            if gene_id not in gene_ids or gene_id in seen_genes:
                raise ValueError(
                    f"unexpected or duplicate gene in transcript mapping: {gene_id!r}"
                )
            transcript_ids = [representative]
            if alternatives and alternatives != "-":
                transcript_ids.extend(
                    item.strip() for item in alternatives.split(",") if item.strip()
                )
            if not representative or len(set(transcript_ids)) != len(transcript_ids):
                raise ValueError(f"invalid transcript list for {gene_id}")
            for display_order, transcript_id in enumerate(transcript_ids):
                if transcript_id in seen_transcripts:
                    raise ValueError(f"duplicate transcript ID: {transcript_id}")
                seen_transcripts.add(transcript_id)
                definitions.append(
                    TranscriptDefinition(
                        gene_id=gene_id,
                        transcript_id=transcript_id,
                        is_representative=display_order == 0,
                        display_order=display_order,
                    )
                )
            representative_by_gene[gene_id] = representative
            seen_genes.add(gene_id)
    missing = gene_ids - seen_genes
    if missing:
        raise ValueError(
            f"transcript mapping is missing {len(missing)} catalog genes; example: {min(missing)}"
        )
    return definitions, representative_by_gene


def _parse_gff_attributes(value: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in value.split(";"):
        if not item or "=" not in item:
            continue
        key, raw_value = item.split("=", 1)
        result[key] = unquote(raw_value)
    return result


def load_gff_features(
    path: Path,
    gene_ids: set[str],
    transcript_parent: dict[str, str],
) -> tuple[
    dict[str, Feature],
    dict[str, Feature],
    dict[str, list[CdsSegment]],
    dict[str, int],
]:
    gene_features: dict[str, Feature] = {}
    transcript_features: dict[str, Feature] = {}
    cds_segments: dict[str, list[CdsSegment]] = {}
    counts = {"records": 0, "genes": 0, "mrna": 0, "cds": 0, "extra_genes": 0}
    all_gff_genes: set[str] = set()
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip() or line.startswith("#"):
                continue
            fields = line.rstrip("\n").split("\t")
            if len(fields) != 9:
                raise ValueError(f"invalid GFF row {line_number}")
            counts["records"] += 1
            seqid, _source, feature_type, start_raw, end_raw, _score, strand, phase, raw_attrs = fields
            if strand not in {"+", "-", "."}:
                raise ValueError(f"invalid GFF strand at row {line_number}: {strand}")
            start = int(start_raw)
            end = int(end_raw)
            if start < 1 or end < start:
                raise ValueError(f"invalid GFF coordinates at row {line_number}")
            attrs = _parse_gff_attributes(raw_attrs)
            if feature_type == "gene":
                gene_id = attrs.get("ID", "")
                all_gff_genes.add(gene_id)
                if gene_id in gene_ids:
                    if gene_id in gene_features or strand not in {"+", "-"}:
                        raise ValueError(f"invalid duplicate gene feature for {gene_id}")
                    gene_features[gene_id] = Feature(seqid, start, end, strand)
                continue
            if feature_type == "mRNA":
                transcript_id = attrs.get("ID", "")
                if transcript_id not in transcript_parent:
                    continue
                parent = attrs.get("Parent", "")
                if parent != transcript_parent[transcript_id]:
                    raise ValueError(
                        f"GFF parent mismatch for {transcript_id}: {parent!r}"
                    )
                if transcript_id in transcript_features or strand not in {"+", "-"}:
                    raise ValueError(f"invalid duplicate transcript feature: {transcript_id}")
                transcript_features[transcript_id] = Feature(seqid, start, end, strand)
                continue
            if feature_type != "CDS":
                continue
            parents = [item for item in attrs.get("Parent", "").split(",") if item]
            for transcript_id in parents:
                if transcript_id not in transcript_parent:
                    continue
                if strand not in {"+", "-"}:
                    raise ValueError(f"invalid CDS strand for {transcript_id}")
                cds_segments.setdefault(transcript_id, []).append(
                    CdsSegment(seqid, start, end, strand, phase)
                )

    missing_genes = gene_ids - set(gene_features)
    missing_transcripts = set(transcript_parent) - set(transcript_features)
    missing_cds = set(transcript_parent) - set(cds_segments)
    if missing_genes:
        raise ValueError(
            f"GFF is missing {len(missing_genes)} catalog genes; example: {min(missing_genes)}"
        )
    if missing_transcripts:
        raise ValueError(
            f"GFF is missing {len(missing_transcripts)} transcripts; example: {min(missing_transcripts)}"
        )
    if missing_cds:
        raise ValueError(
            f"GFF is missing CDS for {len(missing_cds)} transcripts; example: {min(missing_cds)}"
        )
    for transcript_id, feature in transcript_features.items():
        gene_feature = gene_features[transcript_parent[transcript_id]]
        segments = cds_segments[transcript_id]
        if feature.seqid != gene_feature.seqid or feature.strand != gene_feature.strand:
            raise ValueError(f"gene/transcript location mismatch for {transcript_id}")
        if feature.start < gene_feature.start or feature.end > gene_feature.end:
            raise ValueError(f"transcript outside gene bounds for {transcript_id}")
        for segment in segments:
            if segment.seqid != feature.seqid or segment.strand != feature.strand:
                raise ValueError(f"CDS location mismatch for {transcript_id}")
            if segment.start < feature.start or segment.end > feature.end:
                raise ValueError(f"CDS outside transcript bounds for {transcript_id}")
    counts.update(
        {
            "genes": len(gene_features),
            "mrna": len(transcript_features),
            "cds": sum(len(items) for items in cds_segments.values()),
            "extra_genes": len(all_gff_genes - gene_ids),
        }
    )
    return gene_features, transcript_features, cds_segments, counts


class FastaIndexReader:
    def __init__(self, fasta_path: Path, fai_path: Path):
        self.fasta_path = fasta_path
        self.entries: dict[str, FaiEntry] = {}
        with fai_path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                fields = line.rstrip("\n").split("\t")
                if len(fields) < 5:
                    raise ValueError(f"invalid FASTA index row {line_number}")
                name = fields[0]
                if name in self.entries:
                    raise ValueError(f"duplicate FASTA index sequence: {name}")
                self.entries[name] = FaiEntry(*(int(value) for value in fields[1:5]))
        self._handle = fasta_path.open("rb")

    def close(self) -> None:
        self._handle.close()

    def __enter__(self) -> "FastaIndexReader":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def fetch(self, seqid: str, start: int, end: int) -> str:
        entry = self.entries.get(seqid)
        if entry is None:
            raise ValueError(f"sequence {seqid!r} is absent from FASTA index")
        if start < 1 or end < start or end > entry.length:
            raise ValueError(
                f"invalid FASTA interval {seqid}:{start}-{end} (length {entry.length})"
            )
        position = start - 1
        remaining = end - start + 1
        chunks: list[bytes] = []
        while remaining:
            line_index, within_line = divmod(position, entry.line_bases)
            take = min(remaining, entry.line_bases - within_line)
            byte_offset = entry.offset + line_index * entry.line_bytes + within_line
            self._handle.seek(byte_offset)
            chunk = self._handle.read(take)
            if len(chunk) != take:
                raise ValueError(f"short FASTA read for {seqid}:{start}-{end}")
            chunks.append(chunk)
            position += take
            remaining -= take
        return b"".join(chunks).decode("ascii")


def iter_fasta(path: Path) -> Iterator[tuple[str, str]]:
    identifier: str | None = None
    chunks: list[str] = []
    with path.open("r", encoding="ascii") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            if line.startswith(">"):
                if identifier is not None:
                    yield identifier, "".join(chunks)
                identifier = line[1:].split(None, 1)[0]
                if not identifier:
                    raise ValueError(f"empty FASTA identifier at row {line_number}")
                chunks = []
            elif identifier is None:
                raise ValueError(f"FASTA sequence before header at row {line_number}")
            else:
                chunks.append(line)
    if identifier is not None:
        yield identifier, "".join(chunks)


def load_selected_fasta(
    path: Path, expected_ids: set[str], *, alphabet: str
) -> tuple[dict[str, str], int]:
    selected: dict[str, str] = {}
    total_records = 0
    if alphabet == "dna":
        allowed = set("ACGTRYKMSWBDHVNacgtrykmswbdhvn")
    else:
        allowed = set("ABCDEFGHIKLMNPQRSTVWXYZ*abcdefghiklmnpqrstvwxyz")
    for identifier, sequence in iter_fasta(path):
        total_records += 1
        if identifier not in expected_ids:
            continue
        if identifier in selected:
            raise ValueError(f"duplicate selected FASTA record: {identifier}")
        if not sequence or not set(sequence) <= allowed:
            raise ValueError(f"invalid {alphabet} sequence for {identifier}")
        selected[identifier] = sequence.upper()
    missing = expected_ids - set(selected)
    if missing:
        raise ValueError(
            f"{path.name} is missing {len(missing)} DMv8.2 transcripts; example: {min(missing)}"
        )
    return selected, total_records


def reverse_complement(sequence: str) -> str:
    return sequence.translate(DNA_COMPLEMENT)[::-1]


def translate_cds(sequence: str) -> str:
    sequence = sequence.upper()
    if len(sequence) % 3:
        raise ValueError(f"CDS length {len(sequence)} is not divisible by three")
    return "".join(
        CODON_TABLE.get(sequence[index : index + 3], "X")
        for index in range(0, len(sequence), 3)
    )


def extract_spliced_cds(
    reader: FastaIndexReader, segments: list[CdsSegment]
) -> str:
    strand = segments[0].strand
    ordered = sorted(segments, key=lambda item: item.start, reverse=strand == "-")
    chunks: list[str] = []
    for segment in ordered:
        sequence = reader.fetch(segment.seqid, segment.start, segment.end)
        chunks.append(reverse_complement(sequence) if strand == "-" else sequence)
    return "".join(chunks).upper()


def _sequence_values(
    transcript_pk: int,
    sequence_type: str,
    sequence: str,
    *,
    seqid: str | None = None,
    region_start: int | None = None,
    region_end: int | None = None,
    strand: str | None = None,
    anchor_type: str | None = None,
    anchor_pos: int | None = None,
    requested_length: int | None = None,
    was_truncated: bool = False,
) -> tuple[Any, ...]:
    encoded = sequence.encode("ascii")
    return (
        transcript_pk,
        sequence_type,
        sqlite3.Binary(zlib.compress(encoded, level=6)),
        len(sequence),
        hashlib.sha256(encoded).hexdigest(),
        seqid,
        region_start,
        region_end,
        strand,
        anchor_type,
        anchor_pos,
        requested_length,
        int(was_truncated),
    )


def open_output_database(output_db: Path) -> tuple[sqlite3.Connection, Path]:
    output_db = output_db.resolve()
    output_db.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{output_db.name}.", suffix=".tmp", dir=output_db.parent
    )
    os.close(fd)
    temp_path = Path(temp_name)
    conn = sqlite3.connect(temp_path)
    conn.execute("pragma foreign_keys=on")
    return conn, temp_path


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        pragma foreign_keys=on;
        pragma journal_mode=off;
        pragma synchronous=off;
        pragma temp_store=memory;

        create table catalog_metadata(
          singleton integer primary key check(singleton=1),
          schema_version integer not null,
          catalog_version text not null unique,
          dataset_id text not null,
          assembly text not null,
          built_at text not null,
          counts_json text not null check(json_valid(counts_json)),
          prediction_release text not null,
          expression_statistic text not null
        ) strict;

        create table catalog_sources(
          source_key text primary key,
          file_name text not null,
          sha256 text not null check(length(sha256)=64),
          byte_count integer not null check(byte_count>=0),
          record_count integer not null check(record_count>=0)
        ) strict;

        create table genes(
          gene_pk integer primary key,
          gene_id text not null unique,
          gene_id_norm text not null unique,
          seqid text not null,
          start integer not null check(start>=1),
          end integer not null check(end>=start),
          strand text not null check(strand in ('+','-'))
        ) strict;

        create table gene_identifiers(
          identifier_pk integer primary key,
          gene_pk integer not null references genes(gene_pk) on delete cascade,
          identifier_type text not null
            check(identifier_type in ('gene_id','gene_symbol','reported_id')),
          identifier text not null,
          identifier_norm text not null,
          qualifier_type text
            check(qualifier_type is null or qualifier_type='blast_identity_pct'),
          qualifier_value real
            check(qualifier_value is null or qualifier_value between 0 and 100),
          display_order integer not null check(display_order>=0),
          unique(gene_pk,identifier_type,display_order)
        ) strict;

        create table transcripts(
          transcript_pk integer primary key,
          gene_pk integer not null references genes(gene_pk) on delete cascade,
          transcript_id text not null unique,
          transcript_id_norm text not null unique,
          is_representative integer not null check(is_representative in (0,1)),
          display_order integer not null check(display_order>=0),
          seqid text not null,
          start integer not null check(start>=1),
          end integer not null check(end>=start),
          strand text not null check(strand in ('+','-')),
          unique(gene_pk,display_order)
        ) strict;

        create unique index uq_transcript_representative
          on transcripts(gene_pk) where is_representative=1;

        create table gene_descriptions(
          gene_pk integer primary key references genes(gene_pk) on delete cascade,
          transcript_pk integer not null unique references transcripts(transcript_pk),
          predicted_function text not null,
          reliability_grade text not null
            check(reliability_grade in ('F1','F2','F3','F4','U')),
          grade_reason text not null,
          evidence_json text not null check(json_valid(evidence_json)),
          source_run text not null
            check(source_run in ('primary','retry_185','retry_1')),
          source_key text not null references catalog_sources(source_key)
        ) strict;

        create table papers(
          paper_pk integer primary key,
          doi text not null,
          doi_norm text not null unique,
          title text not null
        ) strict;

        create table paper_local_ids(
          local_paper_id text primary key,
          paper_pk integer not null references papers(paper_pk) on delete cascade,
          source_title text not null
        ) strict;

        create table gene_paper_refs(
          gene_pk integer not null references genes(gene_pk) on delete cascade,
          local_paper_id text not null references paper_local_ids(local_paper_id),
          display_order integer not null check(display_order>=0),
          primary key(gene_pk,local_paper_id),
          unique(gene_pk,display_order)
        ) strict, without rowid;

        create table gene_annotations(
          gene_pk integer primary key references genes(gene_pk) on delete cascade,
          go_terms_json text check(go_terms_json is null or json_valid(go_terms_json)),
          kegg_terms_json text check(kegg_terms_json is null or json_valid(kegg_terms_json)),
          interpro_json text check(interpro_json is null or json_valid(interpro_json))
        ) strict;

        create table protein_similarity_hits(
          hit_pk integer primary key,
          gene_pk integer not null references genes(gene_pk) on delete cascade,
          database_name text not null default 'UniRef100',
          subject_id text not null,
          identity_pct real not null check(identity_pct between 0 and 100),
          e_value real not null check(e_value>=0),
          description text not null,
          display_order integer not null check(display_order>=0),
          unique(gene_pk,display_order)
        ) strict;

        create table transcript_sequences(
          transcript_pk integer not null references transcripts(transcript_pk)
            on delete cascade,
          sequence_type text not null
            check(sequence_type in
              ('cds','protein','genomic','promoter_atg_upstream_2000')),
          sequence_zlib blob not null,
          sequence_length integer not null check(sequence_length>0),
          sha256 text not null check(length(sha256)=64),
          seqid text,
          region_start integer,
          region_end integer,
          strand text check(strand is null or strand in ('+','-')),
          anchor_type text check(anchor_type is null or anchor_type='ATG'),
          anchor_pos integer,
          requested_length integer,
          was_truncated integer not null default 0 check(was_truncated in (0,1)),
          primary key(transcript_pk,sequence_type),
          check(
            sequence_type not in ('genomic','promoter_atg_upstream_2000') or
            (seqid is not null and region_start>=1 and region_end>=region_start and
             strand is not null and sequence_length=region_end-region_start+1)
          ),
          check(
            sequence_type<>'promoter_atg_upstream_2000' or
            (anchor_type='ATG' and anchor_pos>=1 and requested_length=2000 and
             ((was_truncated=0 and sequence_length=2000) or
              (was_truncated=1 and sequence_length<2000)))
          )
        ) strict, without rowid;
        """
    )


def register_sources(
    conn: sqlite3.Connection, source_paths: dict[str, Path]
) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for source_key, path in source_paths.items():
        record = {
            "source_key": source_key,
            "file_name": path.name,
            "sha256": _sha256_file(path),
            "byte_count": path.stat().st_size,
            "record_count": 0,
        }
        records[source_key] = record
        conn.execute(
            """
            insert into catalog_sources(
              source_key,file_name,sha256,byte_count,record_count
            ) values (?,?,?,?,?)
            """,
            (
                record["source_key"],
                record["file_name"],
                record["sha256"],
                record["byte_count"],
                record["record_count"],
            ),
        )
    return records


def update_source_count(
    conn: sqlite3.Connection,
    source_records: dict[str, dict[str, Any]],
    source_key: str,
    count: int,
) -> None:
    source_records[source_key]["record_count"] = count
    conn.execute(
        "update catalog_sources set record_count=? where source_key=?",
        (count, source_key),
    )


def insert_genes_and_identifiers(
    conn: sqlite3.Connection,
    genes: list[LegacyGene],
    features: dict[str, Feature],
) -> tuple[dict[str, int], dict[str, int]]:
    gene_pk_by_id = {gene.gene_id: index for index, gene in enumerate(genes, start=1)}
    conn.executemany(
        """
        insert into genes(gene_pk,gene_id,gene_id_norm,seqid,start,end,strand)
        values (?,?,?,?,?,?,?)
        """,
        [
            (
                gene_pk_by_id[gene.gene_id],
                gene.gene_id,
                normalize_identifier(gene.gene_id),
                features[gene.gene_id].seqid,
                features[gene.gene_id].start,
                features[gene.gene_id].end,
                features[gene.gene_id].strand,
            )
            for gene in genes
        ],
    )

    identifier_rows: list[tuple[Any, ...]] = []
    stats = {
        "gene_id": 0,
        "gene_symbol": 0,
        "reported_id": 0,
        "duplicate_symbols_removed": 0,
        "duplicate_reported_ids_removed": 0,
        "qualified_reported_ids": 0,
    }
    identifier_pk = 1
    for gene in genes:
        gene_pk = gene_pk_by_id[gene.gene_id]
        identifier_rows.append(
            (
                identifier_pk,
                gene_pk,
                "gene_id",
                gene.gene_id,
                normalize_identifier(gene.gene_id),
                None,
                None,
                0,
            )
        )
        identifier_pk += 1
        stats["gene_id"] += 1

        symbols, duplicates = _deduplicate(gene.symbols)
        stats["duplicate_symbols_removed"] += duplicates
        for display_order, symbol in enumerate(symbols):
            identifier_rows.append(
                (
                    identifier_pk,
                    gene_pk,
                    "gene_symbol",
                    symbol,
                    normalize_identifier(symbol),
                    None,
                    None,
                    display_order,
                )
            )
            identifier_pk += 1
            stats["gene_symbol"] += 1

        reported_rows: list[tuple[str, str | None, float | None]] = []
        for reported_id in gene.reported_ids:
            match = BLAST_IDENTITY_RE.fullmatch(reported_id)
            if match:
                identifier = match.group("identifier").strip()
                qualifier_type = "blast_identity_pct"
                qualifier_value = float(match.group("identity"))
                stats["qualified_reported_ids"] += 1
            else:
                identifier = reported_id
                qualifier_type = None
                qualifier_value = None
            reported_rows.append((identifier, qualifier_type, qualifier_value))
        deduplicated: list[tuple[str, str | None, float | None]] = []
        seen_reported: set[str] = set()
        for row in reported_rows:
            key = normalize_identifier(row[0])
            if key in seen_reported:
                stats["duplicate_reported_ids_removed"] += 1
                continue
            seen_reported.add(key)
            deduplicated.append(row)
        for display_order, (identifier, qualifier_type, qualifier_value) in enumerate(
            deduplicated
        ):
            identifier_rows.append(
                (
                    identifier_pk,
                    gene_pk,
                    "reported_id",
                    identifier,
                    normalize_identifier(identifier),
                    qualifier_type,
                    qualifier_value,
                    display_order,
                )
            )
            identifier_pk += 1
            stats["reported_id"] += 1

    conn.executemany(
        """
        insert into gene_identifiers(
          identifier_pk,gene_pk,identifier_type,identifier,identifier_norm,
          qualifier_type,qualifier_value,display_order
        ) values (?,?,?,?,?,?,?,?)
        """,
        identifier_rows,
    )
    return gene_pk_by_id, stats


def insert_transcripts(
    conn: sqlite3.Connection,
    definitions: list[TranscriptDefinition],
    gene_pk_by_id: dict[str, int],
    features: dict[str, Feature],
) -> dict[str, int]:
    transcript_pk_by_id = {
        definition.transcript_id: index
        for index, definition in enumerate(definitions, start=1)
    }
    conn.executemany(
        """
        insert into transcripts(
          transcript_pk,gene_pk,transcript_id,transcript_id_norm,is_representative,
          display_order,seqid,start,end,strand
        ) values (?,?,?,?,?,?,?,?,?,?)
        """,
        [
            (
                transcript_pk_by_id[item.transcript_id],
                gene_pk_by_id[item.gene_id],
                item.transcript_id,
                normalize_identifier(item.transcript_id),
                int(item.is_representative),
                item.display_order,
                features[item.transcript_id].seqid,
                features[item.transcript_id].start,
                features[item.transcript_id].end,
                features[item.transcript_id].strand,
            )
            for item in definitions
        ],
    )
    return transcript_pk_by_id


def import_papers(
    conn: sqlite3.Connection,
    path: Path,
    genes: list[LegacyGene],
    gene_pk_by_id: dict[str, int],
) -> dict[str, int]:
    paper_by_doi: dict[str, tuple[int, str, str]] = {}
    local_ids: dict[str, tuple[int, str]] = {}
    data_rows = 0
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip() or line.startswith("#"):
                continue
            fields = line.rstrip("\n").split("\t")
            if len(fields) != 3:
                raise ValueError(f"invalid paper metadata row {line_number}")
            doi, local_file, title = (item.strip() for item in fields)
            local_paper_id = re.sub(r"\.pdf$", "", local_file, flags=re.I)
            doi_norm = normalize_doi(doi)
            if not doi_norm or not local_paper_id or not title:
                raise ValueError(f"incomplete paper metadata at row {line_number}")
            if local_paper_id in local_ids:
                raise ValueError(f"duplicate local paper ID: {local_paper_id}")
            if doi_norm not in paper_by_doi:
                paper_by_doi[doi_norm] = (len(paper_by_doi) + 1, doi, title)
            paper_pk = paper_by_doi[doi_norm][0]
            local_ids[local_paper_id] = (paper_pk, title)
            data_rows += 1
    conn.executemany(
        "insert into papers(paper_pk,doi,doi_norm,title) values (?,?,?,?)",
        [
            (paper_pk, doi, doi_norm, title)
            for doi_norm, (paper_pk, doi, title) in paper_by_doi.items()
        ],
    )
    conn.executemany(
        "insert into paper_local_ids(local_paper_id,paper_pk,source_title) values (?,?,?)",
        [
            (local_id, paper_pk, source_title)
            for local_id, (paper_pk, source_title) in local_ids.items()
        ],
    )

    gene_ref_rows: list[tuple[int, str, int]] = []
    referenced_local_ids: set[str] = set()
    duplicate_gene_doi_refs = 0
    for gene in genes:
        seen_local: set[str] = set()
        seen_papers: set[int] = set()
        for display_order, local_id in enumerate(gene.paper_ids):
            if local_id in seen_local:
                raise ValueError(f"duplicate paper ref {local_id} for {gene.gene_id}")
            if local_id not in local_ids:
                raise ValueError(
                    f"paper ref {local_id} for {gene.gene_id} has no metadata mapping"
                )
            paper_pk = local_ids[local_id][0]
            if paper_pk in seen_papers:
                duplicate_gene_doi_refs += 1
            seen_papers.add(paper_pk)
            seen_local.add(local_id)
            referenced_local_ids.add(local_id)
            gene_ref_rows.append(
                (gene_pk_by_id[gene.gene_id], local_id, display_order)
            )
    conn.executemany(
        "insert into gene_paper_refs(gene_pk,local_paper_id,display_order) values (?,?,?)",
        gene_ref_rows,
    )
    return {
        "metadata_rows": data_rows,
        "papers": len(paper_by_doi),
        "local_paper_ids": len(local_ids),
        "duplicate_doi_groups": data_rows - len(paper_by_doi),
        "gene_paper_refs": len(gene_ref_rows),
        "referenced_local_paper_ids": len(referenced_local_ids),
        "duplicate_gene_doi_refs": duplicate_gene_doi_refs,
    }


def _split_annotation_terms(value: str) -> list[str]:
    if not value or value == "-":
        return []
    values, _duplicates = _deduplicate(
        (item.strip() for item in value.split(";") if item.strip())
    )
    return values


def import_annotations(
    conn: sqlite3.Connection, path: Path, gene_pk_by_id: dict[str, int]
) -> dict[str, int]:
    rows: list[tuple[Any, ...]] = []
    seen: set[str] = set()
    coverage = {"rows": 0, "go": 0, "kegg": 0, "interpro": 0}
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        required = {"GeneID", "GO", "KEGG", "Annotation"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(f"annotation file must contain {sorted(required)}")
        for line_number, row in enumerate(reader, start=2):
            gene_id = str(row.get("GeneID") or "").strip()
            if gene_id not in gene_pk_by_id or gene_id in seen:
                raise ValueError(
                    f"unexpected or duplicate annotation gene at row {line_number}: {gene_id}"
                )
            seen.add(gene_id)
            go_terms = _split_annotation_terms(str(row.get("GO") or ""))
            kegg_terms = _split_annotation_terms(str(row.get("KEGG") or ""))
            interpro_items: list[dict[str, str]] = []
            for item in _split_annotation_terms(str(row.get("Annotation") or "")):
                accession, _, description = item.partition(" ")
                interpro_items.append(
                    {"id": accession, "description": description.strip()}
                )
            coverage["rows"] += 1
            coverage["go"] += int(bool(go_terms))
            coverage["kegg"] += int(bool(kegg_terms))
            coverage["interpro"] += int(bool(interpro_items))
            rows.append(
                (
                    gene_pk_by_id[gene_id],
                    _compact_json(go_terms) if go_terms else None,
                    _compact_json(kegg_terms) if kegg_terms else None,
                    _compact_json(interpro_items) if interpro_items else None,
                )
            )
    conn.executemany(
        """
        insert into gene_annotations(
          gene_pk,go_terms_json,kegg_terms_json,interpro_json
        ) values (?,?,?,?)
        """,
        rows,
    )
    coverage["missing"] = len(gene_pk_by_id) - len(seen)
    return coverage


def import_similarity_hits(
    conn: sqlite3.Connection, path: Path, gene_pk_by_id: dict[str, int]
) -> dict[str, int]:
    rows: list[tuple[Any, ...]] = []
    seen_rows: set[tuple[str, str, str, str, str]] = set()
    order_by_gene: dict[str, int] = {}
    genes_with_hits: set[str] = set()
    duplicate_rows = 0
    source_rows = 0
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        required = ("QueryID", "SubjectID", "Identity%", "E-value", "Description")
        if not set(required).issubset(reader.fieldnames or []):
            raise ValueError(f"similarity file must contain {sorted(required)}")
        for line_number, row in enumerate(reader, start=2):
            source_rows += 1
            values = tuple(str(row.get(key) or "").strip() for key in required)
            keyed = dict(zip(required, values))
            gene_id = keyed["QueryID"]
            if gene_id not in gene_pk_by_id:
                raise ValueError(f"unexpected similarity query at row {line_number}: {gene_id}")
            dedupe_key = (
                gene_id,
                keyed["SubjectID"],
                keyed["Identity%"],
                keyed["E-value"],
                keyed["Description"],
            )
            if dedupe_key in seen_rows:
                duplicate_rows += 1
                continue
            seen_rows.add(dedupe_key)
            try:
                identity = float(keyed["Identity%"])
                e_value = float(keyed["E-value"])
            except ValueError as exc:
                raise ValueError(f"invalid similarity score at row {line_number}") from exc
            display_order = order_by_gene.get(gene_id, 0)
            order_by_gene[gene_id] = display_order + 1
            genes_with_hits.add(gene_id)
            rows.append(
                (
                    gene_pk_by_id[gene_id],
                    "UniRef100",
                    keyed["SubjectID"],
                    identity,
                    e_value,
                    keyed["Description"],
                    display_order,
                )
            )
    conn.executemany(
        """
        insert into protein_similarity_hits(
          gene_pk,database_name,subject_id,identity_pct,e_value,description,display_order
        ) values (?,?,?,?,?,?,?)
        """,
        rows,
    )
    return {
        "source_rows": source_rows,
        "imported_hits": len(rows),
        "duplicate_rows_removed": duplicate_rows,
        "genes_with_hits": len(genes_with_hits),
        "missing_genes": len(gene_pk_by_id) - len(genes_with_hits),
    }


def import_sequences(
    conn: sqlite3.Connection,
    *,
    cds_path: Path,
    protein_path: Path,
    genome_path: Path,
    fai_path: Path,
    transcript_pk_by_id: dict[str, int],
    transcript_features: dict[str, Feature],
    cds_segments: dict[str, list[CdsSegment]],
    representative_by_gene: dict[str, str],
) -> dict[str, int]:
    transcript_ids = set(transcript_pk_by_id)
    cds_by_transcript, cds_source_records = load_selected_fasta(
        cds_path, transcript_ids, alphabet="dna"
    )
    proteins_by_transcript, protein_source_records = load_selected_fasta(
        protein_path, transcript_ids, alphabet="protein"
    )
    sequence_rows: list[tuple[Any, ...]] = []
    promoter_truncated = 0
    with FastaIndexReader(genome_path, fai_path) as genome:
        for transcript_id in sorted(transcript_ids):
            expected_cds = extract_spliced_cds(genome, cds_segments[transcript_id])
            source_cds = cds_by_transcript[transcript_id]
            if source_cds != expected_cds:
                raise ValueError(
                    f"CDS FASTA differs from GFF/genome extraction for {transcript_id}"
                )
            expected_protein = translate_cds(source_cds)
            source_protein = proteins_by_transcript[transcript_id]
            if source_protein != expected_protein:
                raise ValueError(
                    f"protein FASTA differs from CDS translation for {transcript_id}"
                )
            transcript_pk = transcript_pk_by_id[transcript_id]
            sequence_rows.append(_sequence_values(transcript_pk, "cds", source_cds))
            sequence_rows.append(
                _sequence_values(transcript_pk, "protein", source_protein)
            )
            if len(sequence_rows) >= 1_000:
                conn.executemany(
                    """
                    insert into transcript_sequences(
                      transcript_pk,sequence_type,sequence_zlib,sequence_length,sha256,
                      seqid,region_start,region_end,strand,anchor_type,anchor_pos,
                      requested_length,was_truncated
                    ) values (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    sequence_rows,
                )
                sequence_rows.clear()
        if sequence_rows:
            conn.executemany(
                """
                insert into transcript_sequences(
                  transcript_pk,sequence_type,sequence_zlib,sequence_length,sha256,
                  seqid,region_start,region_end,strand,anchor_type,anchor_pos,
                  requested_length,was_truncated
                ) values (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                sequence_rows,
            )
            sequence_rows.clear()

        for gene_id, transcript_id in sorted(representative_by_gene.items()):
            feature = transcript_features[transcript_id]
            transcript_pk = transcript_pk_by_id[transcript_id]
            genomic = genome.fetch(feature.seqid, feature.start, feature.end)
            if feature.strand == "-":
                genomic = reverse_complement(genomic)
            sequence_rows.append(
                _sequence_values(
                    transcript_pk,
                    "genomic",
                    genomic,
                    seqid=feature.seqid,
                    region_start=feature.start,
                    region_end=feature.end,
                    strand=feature.strand,
                )
            )

            segments = cds_segments[transcript_id]
            if feature.strand == "+":
                anchor_pos = min(segment.start for segment in segments)
                promoter_start = max(1, anchor_pos - PROMOTER_LENGTH)
                promoter_end = anchor_pos - 1
            else:
                anchor_pos = max(segment.end for segment in segments)
                contig_length = genome.entries[feature.seqid].length
                promoter_start = anchor_pos + 1
                promoter_end = min(contig_length, anchor_pos + PROMOTER_LENGTH)
            if promoter_end < promoter_start:
                raise ValueError(f"empty ATG-upstream promoter for {transcript_id}")
            promoter = genome.fetch(feature.seqid, promoter_start, promoter_end)
            if feature.strand == "-":
                promoter = reverse_complement(promoter)
            was_truncated = len(promoter) != PROMOTER_LENGTH
            promoter_truncated += int(was_truncated)
            if cds_by_transcript[transcript_id][:3] != "ATG":
                raise ValueError(f"representative transcript does not start with ATG: {transcript_id}")
            sequence_rows.append(
                _sequence_values(
                    transcript_pk,
                    "promoter_atg_upstream_2000",
                    promoter,
                    seqid=feature.seqid,
                    region_start=promoter_start,
                    region_end=promoter_end,
                    strand=feature.strand,
                    anchor_type="ATG",
                    anchor_pos=anchor_pos,
                    requested_length=PROMOTER_LENGTH,
                    was_truncated=was_truncated,
                )
            )
            if len(sequence_rows) >= 500:
                conn.executemany(
                    """
                    insert into transcript_sequences(
                      transcript_pk,sequence_type,sequence_zlib,sequence_length,sha256,
                      seqid,region_start,region_end,strand,anchor_type,anchor_pos,
                      requested_length,was_truncated
                    ) values (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    sequence_rows,
                )
                sequence_rows.clear()
        if sequence_rows:
            conn.executemany(
                """
                insert into transcript_sequences(
                  transcript_pk,sequence_type,sequence_zlib,sequence_length,sha256,
                  seqid,region_start,region_end,strand,anchor_type,anchor_pos,
                  requested_length,was_truncated
                ) values (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                sequence_rows,
            )

    return {
        "cds_source_records": cds_source_records,
        "protein_source_records": protein_source_records,
        "cds": len(transcript_ids),
        "protein": len(transcript_ids),
        "genomic": len(representative_by_gene),
        "promoter_atg_upstream_2000": len(representative_by_gene),
        "promoter_truncated": promoter_truncated,
    }


def _load_predicted_gene_sets(
    primary_path: Path, retry_185_path: Path, retry_1_path: Path
) -> tuple[dict[str, set[str]], dict[str, int]]:
    paths = {
        "primary": primary_path,
        "retry_185": retry_185_path,
        "retry_1": retry_1_path,
    }
    sets: dict[str, set[str]] = {}
    counts: dict[str, int] = {}
    for source_run, path in paths.items():
        with path.open("r", encoding="utf-8") as handle:
            report = json.load(handle)
        predicted = report.get("predicted_genes")
        if not isinstance(predicted, list) or not all(
            isinstance(item, str) for item in predicted
        ):
            raise ValueError(f"invalid predicted_genes in {path}")
        if int(report.get("predicted_gene_count", -1)) != len(predicted):
            raise ValueError(f"prediction count mismatch in {path}")
        if len(set(predicted)) != len(predicted):
            raise ValueError(f"duplicate predicted gene IDs in {path}")
        sets[source_run] = set(predicted)
        counts[source_run] = len(predicted)
    overlaps = (
        (sets["primary"] & sets["retry_185"])
        | (sets["primary"] & sets["retry_1"])
        | (sets["retry_185"] & sets["retry_1"])
    )
    if overlaps:
        raise ValueError(
            f"prediction run reports overlap; example: {min(overlaps)}"
        )
    return sets, counts


def import_predictions(
    conn: sqlite3.Connection,
    path: Path,
    *,
    gene_pk_by_id: dict[str, int],
    transcript_pk_by_id: dict[str, int],
    representative_by_gene: dict[str, str],
    predicted_sets: dict[str, set[str]],
) -> dict[str, Any]:
    gene_by_representative = {
        transcript_id: gene_id
        for gene_id, transcript_id in representative_by_gene.items()
    }
    seen_genes: set[str] = set()
    source_run_counts = {"primary": 0, "retry_185": 0, "retry_1": 0}
    expression_statistic: str | None = None
    expression_evidence_rows = 0
    description_rows: list[tuple[Any, ...]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid prediction JSON at row {line_number}") from exc
            if record.get("status") != "ok":
                raise ValueError(f"non-ok prediction at row {line_number}")
            transcript_id = str(record.get("potato_gene_id") or "")
            gene_id = gene_by_representative.get(transcript_id)
            if gene_id is None or gene_id in seen_genes:
                raise ValueError(
                    f"unexpected or duplicate prediction transcript: {transcript_id}"
                )
            evidence = record.get("evidence")
            prediction = record.get("prediction")
            if not isinstance(evidence, dict) or not isinstance(prediction, dict):
                raise ValueError(f"incomplete prediction at row {line_number}")
            if evidence.get("potato_gene_id") != transcript_id:
                raise ValueError(f"prediction evidence ID mismatch for {transcript_id}")
            if prediction.get("potato_gene_id") != transcript_id:
                raise ValueError(f"prediction result ID mismatch for {transcript_id}")
            predicted_function = str(prediction.get("predicted_function") or "").strip()
            reliability_grade = str(prediction.get("reliability_grade") or "").strip()
            grade_reason = str(prediction.get("grade_reason") or "").strip()
            if not predicted_function or not grade_reason:
                raise ValueError(f"empty prediction description for {transcript_id}")

            if transcript_id in predicted_sets["retry_1"]:
                source_run = "retry_1"
            elif transcript_id in predicted_sets["retry_185"]:
                source_run = "retry_185"
            elif transcript_id in predicted_sets["primary"]:
                source_run = "primary"
            else:
                raise ValueError(f"prediction has no source run: {transcript_id}")
            source_run_counts[source_run] += 1

            statistic = str(evidence.get("expression_statistic") or "").strip()
            if not statistic:
                raise ValueError(f"missing expression statistic for {transcript_id}")
            if expression_statistic is None:
                expression_statistic = statistic
            elif expression_statistic != statistic:
                raise ValueError("prediction records use inconsistent expression statistics")

            gene_pk = gene_pk_by_id[gene_id]
            description_rows.append(
                (
                    gene_pk,
                    transcript_pk_by_id[transcript_id],
                    predicted_function,
                    reliability_grade,
                    grade_reason,
                    _compact_json(evidence),
                    source_run,
                    "function_predictions",
                )
            )
            tissues = evidence.get("expression_by_tissue")
            if not isinstance(tissues, list) or not tissues:
                raise ValueError(f"missing Expression Atlas data for {transcript_id}")
            seen_tissues: set[str] = set()
            for tissue_row in tissues:
                if not isinstance(tissue_row, dict):
                    raise ValueError(f"invalid tissue evidence for {transcript_id}")
                tissue = str(tissue_row.get("tissue") or "").strip()
                if not tissue or tissue in seen_tissues:
                    raise ValueError(f"duplicate/empty tissue for {transcript_id}: {tissue}")
                seen_tissues.add(tissue)
                mean_tpm = float(tissue_row["mean_tpm"])
                sd_tpm = float(tissue_row["sd_tpm"])
                n_sources = int(tissue_row["n_sources"])
                n_runs = int(tissue_row["n_runs"])
                if mean_tpm < 0 or sd_tpm < 0 or n_sources < 1 or n_runs < 1:
                    raise ValueError(f"invalid tissue statistics for {transcript_id}")
                expression_evidence_rows += 1
            seen_genes.add(gene_id)
            if len(description_rows) >= 500:
                conn.executemany(
                    """
                    insert into gene_descriptions(
                      gene_pk,transcript_pk,predicted_function,reliability_grade,
                      grade_reason,evidence_json,source_run,source_key
                    ) values (?,?,?,?,?,?,?,?)
                    """,
                    description_rows,
                )
                description_rows.clear()
    if description_rows:
        conn.executemany(
            """
            insert into gene_descriptions(
              gene_pk,transcript_pk,predicted_function,reliability_grade,
              grade_reason,evidence_json,source_run,source_key
            ) values (?,?,?,?,?,?,?,?)
            """,
            description_rows,
        )
    missing = set(gene_pk_by_id) - seen_genes
    if missing:
        raise ValueError(
            f"predictions are missing {len(missing)} genes; example: {min(missing)}"
        )
    return {
        "predictions": len(seen_genes),
        "expression_evidence_rows": expression_evidence_rows,
        "expression_statistic": expression_statistic or "",
        "source_run_counts": source_run_counts,
    }


def finalize_database(
    conn: sqlite3.Connection,
    *,
    config: BuildConfig,
    expression_statistic: str,
) -> dict[str, int]:
    conn.executescript(
        """
        create index idx_gene_identifier_lookup
          on gene_identifiers(identifier_norm,identifier_type,gene_pk);
        create index idx_gene_identifier_gene
          on gene_identifiers(gene_pk,identifier_type,display_order);
        create index idx_transcript_gene
          on transcripts(gene_pk,is_representative desc,display_order);
        create index idx_paper_local_paper on paper_local_ids(paper_pk);
        create index idx_gene_paper_local on gene_paper_refs(local_paper_id,gene_pk);
        create index idx_similarity_gene
          on protein_similarity_hits(gene_pk,display_order);
        analyze;
        """
    )
    table_names = (
        "genes",
        "gene_identifiers",
        "transcripts",
        "gene_descriptions",
        "papers",
        "paper_local_ids",
        "gene_paper_refs",
        "gene_annotations",
        "protein_similarity_hits",
        "transcript_sequences",
    )
    counts = {
        table: int(conn.execute(f"select count(*) from {table}").fetchone()[0])
        for table in table_names
    }
    built_at = datetime.now(UTC).replace(microsecond=0).isoformat()
    conn.execute(
        """
        insert into catalog_metadata(
          singleton,schema_version,catalog_version,dataset_id,assembly,built_at,
          counts_json,prediction_release,expression_statistic
        ) values (1,?,?,?,?,?,?,?,?)
        """,
        (
            SCHEMA_VERSION,
            config.catalog_version,
            DATASET_ID,
            ASSEMBLY,
            built_at,
            _compact_json(counts),
            config.prediction_release,
            expression_statistic,
        ),
    )
    conn.commit()

    foreign_key_errors = conn.execute("pragma foreign_key_check").fetchall()
    if foreign_key_errors:
        raise ValueError(f"foreign key check failed: {foreign_key_errors[:3]}")
    integrity = conn.execute("pragma integrity_check").fetchone()[0]
    if integrity != "ok":
        raise ValueError(f"SQLite integrity check failed: {integrity}")
    quick = conn.execute("pragma quick_check").fetchone()[0]
    if quick != "ok":
        raise ValueError(f"SQLite quick check failed: {quick}")
    conn.execute("vacuum")
    final_quick = conn.execute("pragma quick_check").fetchone()[0]
    if final_quick != "ok":
        raise ValueError(f"SQLite quick check after vacuum failed: {final_quick}")
    return counts


def build_database(config: BuildConfig) -> dict[str, Any]:
    source_paths = _source_paths(config)
    output_db = config.output_db.resolve()
    conn, temp_path = open_output_database(output_db)
    try:
        create_schema(conn)
        source_records = register_sources(conn, source_paths)

        genes = load_legacy_genes(source_paths["legacy_genes_db"])
        if config.expected_gene_count is not None and len(genes) != config.expected_gene_count:
            raise ValueError(
                f"expected {config.expected_gene_count} genes, found {len(genes)}"
            )
        validate_legacy_json(source_paths["legacy_genes_json"], genes)
        update_source_count(conn, source_records, "legacy_genes_db", len(genes))
        update_source_count(conn, source_records, "legacy_genes_json", len(genes))
        gene_ids = {gene.gene_id for gene in genes}

        definitions, representative_by_gene = load_transcript_definitions(
            source_paths["transcript_map"], gene_ids
        )
        transcript_parent = {
            item.transcript_id: item.gene_id for item in definitions
        }
        update_source_count(
            conn, source_records, "transcript_map", len(representative_by_gene)
        )
        gene_features, transcript_features, cds_segments, gff_counts = load_gff_features(
            source_paths["dmv82_gff"], gene_ids, transcript_parent
        )
        update_source_count(
            conn, source_records, "dmv82_gff", gff_counts["records"]
        )
        update_source_count(
            conn,
            source_records,
            "dmv82_genome_fai",
            _count_lines(source_paths["dmv82_genome_fai"]),
        )
        update_source_count(
            conn,
            source_records,
            "dmv82_genome",
            _count_fasta_records(source_paths["dmv82_genome"]),
        )

        gene_pk_by_id, identifier_stats = insert_genes_and_identifiers(
            conn, genes, gene_features
        )
        transcript_pk_by_id = insert_transcripts(
            conn, definitions, gene_pk_by_id, transcript_features
        )

        paper_stats = import_papers(
            conn, source_paths["paper_metadata"], genes, gene_pk_by_id
        )
        update_source_count(
            conn, source_records, "paper_metadata", paper_stats["metadata_rows"]
        )
        annotation_stats = import_annotations(
            conn, source_paths["dmv82_annotations"], gene_pk_by_id
        )
        update_source_count(
            conn, source_records, "dmv82_annotations", annotation_stats["rows"]
        )
        similarity_stats = import_similarity_hits(
            conn, source_paths["uniref100_hits"], gene_pk_by_id
        )
        update_source_count(
            conn, source_records, "uniref100_hits", similarity_stats["source_rows"]
        )

        sequence_stats = import_sequences(
            conn,
            cds_path=source_paths["dmv82_cds"],
            protein_path=source_paths["dmv82_protein"],
            genome_path=source_paths["dmv82_genome"],
            fai_path=source_paths["dmv82_genome_fai"],
            transcript_pk_by_id=transcript_pk_by_id,
            transcript_features=transcript_features,
            cds_segments=cds_segments,
            representative_by_gene=representative_by_gene,
        )
        update_source_count(
            conn, source_records, "dmv82_cds", sequence_stats["cds_source_records"]
        )
        update_source_count(
            conn,
            source_records,
            "dmv82_protein",
            sequence_stats["protein_source_records"],
        )

        predicted_sets, run_report_counts = _load_predicted_gene_sets(
            source_paths["prediction_run_primary"],
            source_paths["prediction_run_retry_185"],
            source_paths["prediction_run_retry_1"],
        )
        expected_prediction_ids = set(representative_by_gene.values())
        reported_prediction_ids = set().union(*predicted_sets.values())
        if reported_prediction_ids != expected_prediction_ids:
            missing = expected_prediction_ids - reported_prediction_ids
            extra = reported_prediction_ids - expected_prediction_ids
            raise ValueError(
                "prediction run reports do not exactly cover representative transcripts "
                f"(missing={len(missing)}, extra={len(extra)})"
            )
        update_source_count(
            conn,
            source_records,
            "prediction_run_primary",
            run_report_counts["primary"],
        )
        update_source_count(
            conn,
            source_records,
            "prediction_run_retry_185",
            run_report_counts["retry_185"],
        )
        update_source_count(
            conn,
            source_records,
            "prediction_run_retry_1",
            run_report_counts["retry_1"],
        )
        prediction_stats = import_predictions(
            conn,
            source_paths["function_predictions"],
            gene_pk_by_id=gene_pk_by_id,
            transcript_pk_by_id=transcript_pk_by_id,
            representative_by_gene=representative_by_gene,
            predicted_sets=predicted_sets,
        )
        if prediction_stats["source_run_counts"] != run_report_counts:
            raise ValueError(
                "final prediction source-run counts do not match run reports"
            )
        update_source_count(
            conn,
            source_records,
            "function_predictions",
            prediction_stats["predictions"],
        )
        update_source_count(
            conn,
            source_records,
            "prediction_release_readme",
            _count_lines(source_paths["prediction_release_readme"]),
        )

        counts = finalize_database(
            conn,
            config=config,
            expression_statistic=prediction_stats["expression_statistic"],
        )
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
        "catalog_version": config.catalog_version,
        "prediction_release": config.prediction_release,
        "dataset_id": DATASET_ID,
        "assembly": ASSEMBLY,
        "counts": counts,
        "identifier_stats": identifier_stats,
        "paper_stats": paper_stats,
        "annotation_stats": annotation_stats,
        "similarity_stats": similarity_stats,
        "sequence_stats": sequence_stats,
        "prediction_stats": prediction_stats,
        "gff_stats": gff_counts,
        "sources": {
            key: {
                "file_name": value["file_name"],
                "sha256": value["sha256"],
                "byte_count": value["byte_count"],
                "record_count": value["record_count"],
            }
            for key, value in source_records.items()
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the versioned Potato Gene Catalog SQLite database."
    )
    parser.add_argument("--genes-db", type=Path, default=DEFAULT_GENES_DB)
    parser.add_argument("--genes-json", type=Path, default=DEFAULT_GENES_JSON)
    parser.add_argument("--paper-metadata", type=Path, default=DEFAULT_PAPER_METADATA)
    parser.add_argument("--transcript-map", type=Path, default=DEFAULT_TRANSCRIPT_MAP)
    parser.add_argument("--gff", type=Path, default=DEFAULT_GFF)
    parser.add_argument("--genome-fasta", type=Path, default=DEFAULT_GENOME_FASTA)
    parser.add_argument("--genome-fai", type=Path, default=DEFAULT_GENOME_FAI)
    parser.add_argument("--cds-fasta", type=Path, default=DEFAULT_CDS_FASTA)
    parser.add_argument("--protein-fasta", type=Path, default=DEFAULT_PROTEIN_FASTA)
    parser.add_argument("--annotations", type=Path, default=DEFAULT_ANNOTATIONS)
    parser.add_argument("--similarity-hits", type=Path, default=DEFAULT_SIMILARITY_HITS)
    parser.add_argument("--predictions", type=Path, default=DEFAULT_PREDICTIONS)
    parser.add_argument(
        "--prediction-readme", type=Path, default=DEFAULT_PREDICTION_README
    )
    parser.add_argument(
        "--primary-run-report", type=Path, default=DEFAULT_PRIMARY_RUN_REPORT
    )
    parser.add_argument(
        "--retry-185-run-report", type=Path, default=DEFAULT_RETRY_185_RUN_REPORT
    )
    parser.add_argument(
        "--retry-1-run-report", type=Path, default=DEFAULT_RETRY_1_RUN_REPORT
    )
    parser.add_argument("--output-db", type=Path, default=DEFAULT_OUTPUT_DB)
    parser.add_argument("--catalog-version", default="DMv8.2-20260726")
    parser.add_argument("--prediction-release", default=PREDICTION_RELEASE)
    parser.add_argument(
        "--expected-gene-count", type=int, default=EXPECTED_GENE_COUNT
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = BuildConfig(
        genes_db=args.genes_db,
        genes_json=args.genes_json,
        paper_metadata=args.paper_metadata,
        transcript_map=args.transcript_map,
        gff=args.gff,
        genome_fasta=args.genome_fasta,
        genome_fai=args.genome_fai,
        cds_fasta=args.cds_fasta,
        protein_fasta=args.protein_fasta,
        annotations=args.annotations,
        similarity_hits=args.similarity_hits,
        predictions=args.predictions,
        prediction_readme=args.prediction_readme,
        primary_run_report=args.primary_run_report,
        retry_185_run_report=args.retry_185_run_report,
        retry_1_run_report=args.retry_1_run_report,
        output_db=args.output_db,
        catalog_version=args.catalog_version,
        prediction_release=args.prediction_release,
        expected_gene_count=args.expected_gene_count,
    )
    result = build_database(config)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
