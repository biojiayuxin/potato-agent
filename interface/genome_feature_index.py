from __future__ import annotations

import csv
import gzip
import hashlib
import json
import os
import re
import sqlite3
import stat
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence
from urllib.parse import unquote


SCHEMA_VERSION = 1
DEFAULT_INDEX_NAME = "feature_index.sqlite"
REPRESENTATIVE_RULE_VERSION = "explicit-map-or-annotation-then-longest-cds-v1"
FEATURE_ID_RULE_VERSION = "raw-or-raw-at-seqid-for-cross-seqid-duplicates-v1"
STORAGE_LAYOUT = "compact-transcript-segments-v1"
INSERT_BATCH_SIZE = 5_000
PARENTLESS_TRANSCRIPT_GENE_RE = re.compile(r"^(.+)\.\d+$")
REQUIRED_REPRESENTATIVE_MAP_ASSEMBLIES = frozenset(
    {
        "monoploid/DMv6.1",
        "monoploid/DMv8.1",
        "monoploid/DMv8.2",
        "monoploid/E4-63",
    }
)
TRANSCRIPT_TYPES = {
    "mrna",
    "transcript",
    "ncrna",
    "lncrna",
    "lnc_rna",
    "trna",
    "rrna",
    "snrna",
    "snorna",
    "mirna",
    "pre_mirna",
    "primary_transcript",
    "pseudogenic_transcript",
}
GENE_ALIAS_KEYS = {"Name", "gene", "gene_id", "geneID", "geneId", "locus_tag"}
TRANSCRIPT_ALIAS_KEYS = {
    "Name",
    "transcript",
    "transcript_id",
    "transcriptID",
    "transcriptId",
    "protein_id",
}
TRUE_VALUES = {"1", "true", "yes", "y"}
REPRESENTATIVE_TAGS = {"canonical", "primary", "representative"}
INDEX_ATTRIBUTE_KEYS = (
    {"ID", "Parent", "parent", "gene_id", "transcript_id"}
    | GENE_ALIAS_KEYS
    | TRANSCRIPT_ALIAS_KEYS
    | {
        "representative",
        "is_representative",
        "canonical",
        "is_canonical",
        "tag",
        "tags",
    }
)


class FeatureIndexError(RuntimeError):
    pass


class FeatureNotFoundError(FeatureIndexError):
    pass


class AmbiguousFeatureError(FeatureIndexError):
    def __init__(self, candidates: Sequence[tuple[str, str]]) -> None:
        self.candidates = tuple(candidates)
        super().__init__(f"ambiguous feature alias: {self.candidates[:10]}")


@dataclass(frozen=True)
class GffFeature:
    seqid: str
    feature_type: str
    start: int
    end: int
    strand: str
    phase: int | None
    attrs: dict[str, str]


@dataclass(frozen=True)
class AssemblyInput:
    assembly_id: str
    reference: str
    annotation: str
    fai: str
    expected_gene_count: int | None = None
    expected_transcript_count: int | None = None
    representative_map: str = ""


@dataclass(frozen=True)
class StagedAssembly:
    assembly: AssemblyInput
    path: Path
    seqids: dict[str, int]
    counts: dict[str, int]
    annotation_bytes: int
    annotation_mtime_ns: int
    annotation_sha256: str
    reference_bytes: int
    reference_mtime_ns: int
    fai_sha256: str
    representative_map_sha256: str


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def default_index_path(db_root: Path) -> Path:
    configured = os.getenv("GENOME_BROWSER_FEATURE_INDEX_PATH", "").strip()
    return Path(configured).resolve() if configured else (db_root / DEFAULT_INDEX_NAME).resolve()


def parse_gff_attributes(
    value: str, *, selected_keys: set[str] | None = None
) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in value.strip().strip(";").split(";"):
        item = item.strip()
        if not item:
            continue
        if "=" in item:
            key, raw = item.split("=", 1)
        elif " " in item:
            key, raw = item.split(None, 1)
            raw = raw.strip().strip('"')
        else:
            continue
        key = key.strip()
        if selected_keys is None or key in selected_keys:
            result[key] = unquote(raw.strip())
    return result


def split_attribute_values(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _open_annotation(path: Path):
    if path.name.lower().endswith((".gz", ".bgz")):
        return gzip.open(path, "rt", encoding="utf-8")
    return path.open("r", encoding="utf-8")


def iter_gff(
    path: Path, *, selected_attribute_keys: set[str] | None = None
) -> Iterator[GffFeature]:
    with _open_annotation(path) as handle:
        for line_number, raw in enumerate(handle, start=1):
            if not raw.strip() or raw.startswith("#"):
                continue
            fields = raw.rstrip("\r\n").split("\t")
            if len(fields) != 9:
                raise FeatureIndexError(f"GFF line {line_number} does not have 9 columns: {path.name}")
            try:
                start = int(fields[3])
                end = int(fields[4])
            except ValueError as exc:
                raise FeatureIndexError(f"GFF line {line_number} has invalid coordinates: {path.name}") from exc
            if start < 1 or end < start:
                raise FeatureIndexError(f"GFF line {line_number} has invalid interval: {path.name}")
            phase: int | None = None
            if fields[7] not in {"", "."}:
                try:
                    phase = int(fields[7])
                except ValueError as exc:
                    raise FeatureIndexError(f"GFF line {line_number} has invalid phase: {path.name}") from exc
                if phase not in {0, 1, 2}:
                    raise FeatureIndexError(f"GFF line {line_number} has phase outside 0-2: {path.name}")
            yield GffFeature(
                seqid=fields[0],
                feature_type=fields[2],
                start=start,
                end=end,
                strand=fields[6],
                phase=phase,
                attrs=parse_gff_attributes(
                    fields[8], selected_keys=selected_attribute_keys
                ),
            )


def is_transcript_type(feature_type: str) -> bool:
    normalized = feature_type.casefold()
    return (
        normalized in TRANSCRIPT_TYPES
        or normalized.endswith("transcript")
        or normalized.endswith("rna")
    )


def is_gene_type(feature_type: str) -> bool:
    normalized = feature_type.casefold()
    return normalized == "gene" or normalized == "pseudogene" or normalized.endswith("_gene")


def feature_id(feature: GffFeature) -> str:
    return (
        feature.attrs.get("ID")
        or feature.attrs.get("transcript_id")
        or feature.attrs.get("gene_id")
        or ""
    ).strip()


def feature_aliases(canonical_id: str, attrs: dict[str, str], keys: set[str]) -> set[str]:
    result: set[str] = set()
    for key in keys:
        result.update(split_attribute_values(attrs.get(key)))
    result.discard(canonical_id)
    result.discard("")
    return result


def has_representative_hint(attrs: dict[str, str]) -> bool:
    folded = {key.casefold(): value for key, value in attrs.items()}
    for key in ("representative", "is_representative", "canonical", "is_canonical"):
        if folded.get(key, "").strip().casefold() in TRUE_VALUES:
            return True
    tags: set[str] = set()
    for key in ("tag", "tags"):
        raw = folded.get(key, "")
        tags.update(item.strip().casefold() for item in raw.replace("|", ",").split(",") if item.strip())
    return bool(tags & REPRESENTATIVE_TAGS)


def load_fai(path: Path) -> dict[str, int]:
    result: dict[str, int] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            fields = raw.rstrip("\n").split("\t")
            if len(fields) < 2:
                raise FeatureIndexError(f"invalid FAI line {line_number}: {path}")
            name = fields[0].strip()
            if not name:
                raise FeatureIndexError(f"empty FAI sequence name at line {line_number}: {path}")
            if name in result:
                raise FeatureIndexError(f"duplicate FAI sequence: {name}")
            try:
                length = int(fields[1])
            except ValueError as exc:
                raise FeatureIndexError(
                    f"invalid FAI sequence length at line {line_number}: {path}"
                ) from exc
            if length <= 0:
                raise FeatureIndexError(
                    f"non-positive FAI sequence length at line {line_number}: {path}"
                )
            result[name] = length
    if not result:
        raise FeatureIndexError(f"empty FAI: {path}")
    return result


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def load_representative_map(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    result: dict[str, str] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle, delimiter="\t")
        for line_number, row in enumerate(reader, start=1):
            if not row or not row[0].strip() or row[0].lstrip().startswith("#"):
                continue
            if len(row) < 2:
                raise FeatureIndexError(f"representative map line {line_number} needs gene and transcript columns")
            gene_id, transcript_id = row[0].strip(), row[1].strip()
            normalized_gene_header = re.sub(r"[^a-z]", "", gene_id.casefold())
            normalized_transcript_header = re.sub(r"[^a-z]", "", transcript_id.casefold())
            if (
                not result
                and normalized_gene_header in {"gene", "geneid"}
                and (
                    "repre" in normalized_transcript_header
                    or "representative" in normalized_transcript_header
                )
            ):
                continue
            if not gene_id or not transcript_id or gene_id in result:
                raise FeatureIndexError(f"invalid or duplicate representative map gene at line {line_number}: {gene_id!r}")
            result[gene_id] = transcript_id
    return result


def _validate_feature_location(
    feature: GffFeature,
    *,
    seqids: dict[str, int],
    label: str,
) -> None:
    length = seqids.get(feature.seqid)
    if length is None:
        raise FeatureIndexError(f"{label} uses seqid absent from FAI: {feature.seqid}")
    if feature.end > length:
        raise FeatureIndexError(
            f"{label} is outside {feature.seqid}: {feature.start}-{feature.end}, length={length}"
        )
    if feature.strand not in {"+", "-", ".", "?"}:
        raise FeatureIndexError(f"{label} has invalid strand: {feature.strand}")


STAGING_SCHEMA_SQL = """
create table genes(
  gene_id text primary key,
  feature_type text not null,
  seqid text not null,
  start integer not null,
  end integer not null,
  strand text not null,
  is_synthetic integer not null default 0
) without rowid;

create table transcripts(
  transcript_id text primary key,
  gene_id text not null,
  feature_type text not null,
  seqid text not null,
  start integer not null,
  end integer not null,
  strand text not null,
  representative_hint integer not null,
  allow_synthetic_gene integer not null,
  exon_segments text not null default '[]',
  cds_segments text not null default '[]',
  exon_length integer not null default 0,
  cds_length integer not null default 0,
  cds_5p integer,
  first_cds_phase integer,
  is_representative integer not null default 0,
  representative_source text not null default 'not_selected'
) without rowid;

create table segments(
  segment_pk integer primary key,
  transcript_id text not null,
  segment_type integer not null,
  seqid text not null,
  start integer not null,
  end integer not null,
  strand text not null,
  phase integer
);

create table gene_aliases(
  gene_id text not null,
  alias text not null,
  primary key(gene_id, alias)
) without rowid;

create table transcript_aliases(
  transcript_id text not null,
  alias text not null,
  primary key(transcript_id, alias)
) without rowid;

create table representative_map(
  gene_id text primary key,
  transcript_id text not null
) without rowid;

create index ix_stage_transcripts_gene on transcripts(gene_id, transcript_id);
create index ix_stage_segments_transcript
  on segments(transcript_id, segment_type, start, end, segment_pk);
"""


def _resolve_db_resource(db_root: Path, relative_path: str, label: str) -> Path:
    raw = Path(relative_path)
    if raw.is_absolute():
        raise FeatureIndexError(f"{label} path must be relative to database root: {relative_path}")
    root = db_root.resolve()
    path = (root / raw).resolve()
    if path == root or root not in path.parents:
        raise FeatureIndexError(f"{label} path escapes database root: {relative_path}")
    if not path.is_file():
        raise FeatureIndexError(f"{label} file is missing: {path}")
    return path


def representative_map_path_for(
    *,
    db_root: Path,
    assembly: AssemblyInput,
    explicit_path: Path | None,
) -> Path | None:
    if explicit_path is not None:
        path = explicit_path.resolve()
    elif assembly.representative_map:
        path = _resolve_db_resource(
            db_root,
            assembly.representative_map,
            f"representative map for {assembly.assembly_id}",
        )
    elif assembly.assembly_id in REQUIRED_REPRESENTATIVE_MAP_ASSEMBLIES:
        raise FeatureIndexError(
            f"manifest assembly must declare representativeMap: {assembly.assembly_id}"
        )
    else:
        path = None
    if path is not None and not path.is_file():
        raise FeatureIndexError(
            f"representative map is missing for {assembly.assembly_id}: {path}"
        )
    return path


def _flush_stage_rows(
    conn: sqlite3.Connection,
    buffers: dict[str, list[tuple[Any, ...]]],
) -> None:
    statements = {
        "genes": (
            "insert into genes(gene_id,feature_type,seqid,start,end,strand) "
            "values(?,?,?,?,?,?)"
        ),
        "transcripts": (
            "insert into transcripts(transcript_id,gene_id,feature_type,seqid,start,end,"
            "strand,representative_hint,allow_synthetic_gene) values(?,?,?,?,?,?,?,?,?)"
        ),
        "segments": (
            "insert into segments(transcript_id,segment_type,seqid,start,end,strand,phase) "
            "values(?,?,?,?,?,?,?)"
        ),
        "gene_aliases": "insert or ignore into gene_aliases(gene_id,alias) values(?,?)",
        "transcript_aliases": (
            "insert or ignore into transcript_aliases(transcript_id,alias) values(?,?)"
        ),
    }
    try:
        for name, rows in buffers.items():
            if rows:
                conn.executemany(statements[name], rows)
                rows.clear()
    except sqlite3.IntegrityError as exc:
        raise FeatureIndexError(f"duplicate feature ID in annotation: {exc}") from exc


def _derived_parent_gene_id(transcript_id: str) -> str:
    match = PARENTLESS_TRANSCRIPT_GENE_RE.fullmatch(transcript_id)
    return match.group(1) if match else transcript_id


def _qualified_feature_id(raw_id: str, seqid: str) -> str:
    return f"{raw_id}@{seqid}"


def _cross_seqid_duplicate_feature_ids(
    annotation_path: Path,
) -> tuple[set[str], set[str]]:
    gene_seqids: dict[str, str] = {}
    transcript_seqids: dict[str, str] = {}
    duplicate_gene_ids: set[str] = set()
    duplicate_transcript_ids: set[str] = set()

    def record(
        seen: dict[str, str], duplicates: set[str], raw_id: str, seqid: str
    ) -> None:
        previous = seen.setdefault(raw_id, seqid)
        if previous != seqid:
            duplicates.add(raw_id)

    for feature in iter_gff(
        annotation_path, selected_attribute_keys=INDEX_ATTRIBUTE_KEYS
    ):
        gene_feature = is_gene_type(feature.feature_type)
        transcript_feature = (
            is_transcript_type(feature.feature_type) and not gene_feature
        )
        if not gene_feature and not transcript_feature:
            continue
        raw_id = feature_id(feature)
        if not raw_id:
            continue
        if gene_feature:
            record(gene_seqids, duplicate_gene_ids, raw_id, feature.seqid)
            continue
        record(
            transcript_seqids,
            duplicate_transcript_ids,
            raw_id,
            feature.seqid,
        )
        parents = split_attribute_values(
            feature.attrs.get("Parent") or feature.attrs.get("parent")
        )
        gene_id = (
            parents[0]
            if len(parents) == 1
            else (feature.attrs.get("gene_id") or "").strip()
        )
        if not gene_id:
            record(
                gene_seqids,
                duplicate_gene_ids,
                _derived_parent_gene_id(raw_id),
                feature.seqid,
            )
    return duplicate_gene_ids, duplicate_transcript_ids


def _stage_annotation(
    conn: sqlite3.Connection,
    *,
    annotation_path: Path,
    seqids: dict[str, int],
    duplicate_gene_ids: set[str] | None = None,
    duplicate_transcript_ids: set[str] | None = None,
) -> None:
    duplicate_gene_ids = duplicate_gene_ids or set()
    duplicate_transcript_ids = duplicate_transcript_ids or set()
    buffers: dict[str, list[tuple[Any, ...]]] = {
        "genes": [],
        "transcripts": [],
        "segments": [],
        "gene_aliases": [],
        "transcript_aliases": [],
    }
    buffered_rows = 0
    for feature in iter_gff(
        annotation_path, selected_attribute_keys=INDEX_ATTRIBUTE_KEYS
    ):
        low = feature.feature_type.casefold()
        gene_feature = is_gene_type(feature.feature_type)
        transcript_feature = is_transcript_type(feature.feature_type) and not gene_feature
        segment_type = 0 if low == "exon" else 1 if low == "cds" else None
        if not gene_feature and not transcript_feature and segment_type is None:
            continue
        raw_id = feature_id(feature)
        if (gene_feature or transcript_feature) and not raw_id:
            continue
        canonical_id = raw_id
        if gene_feature and raw_id in duplicate_gene_ids:
            canonical_id = _qualified_feature_id(raw_id, feature.seqid)
        elif transcript_feature and raw_id in duplicate_transcript_ids:
            canonical_id = _qualified_feature_id(raw_id, feature.seqid)
        _validate_feature_location(
            feature,
            seqids=seqids,
            label=canonical_id or f"{low} feature",
        )
        if gene_feature:
            buffers["genes"].append(
                (
                    canonical_id,
                    feature.feature_type,
                    feature.seqid,
                    feature.start,
                    feature.end,
                    feature.strand,
                )
            )
            aliases = feature_aliases(
                canonical_id, feature.attrs, GENE_ALIAS_KEYS
            )
            if canonical_id != raw_id:
                aliases.add(raw_id)
            buffers["gene_aliases"].extend(
                (canonical_id, alias) for alias in sorted(aliases)
            )
        elif transcript_feature:
            parents = split_attribute_values(
                feature.attrs.get("Parent") or feature.attrs.get("parent")
            )
            if len(parents) > 1:
                raise FeatureIndexError(
                    f"transcript has multiple parent genes: {canonical_id}"
                )
            gene_id = (
                parents[0]
                if parents
                else (feature.attrs.get("gene_id") or "").strip()
            )
            allow_synthetic_gene = not gene_id
            if not gene_id:
                gene_id = _derived_parent_gene_id(raw_id)
            if gene_id in duplicate_gene_ids:
                gene_id = _qualified_feature_id(gene_id, feature.seqid)
            buffers["transcripts"].append(
                (
                    canonical_id,
                    gene_id,
                    feature.feature_type,
                    feature.seqid,
                    feature.start,
                    feature.end,
                    feature.strand,
                    int(has_representative_hint(feature.attrs)),
                    int(allow_synthetic_gene),
                )
            )
            aliases = feature_aliases(
                canonical_id, feature.attrs, TRANSCRIPT_ALIAS_KEYS
            )
            if canonical_id != raw_id:
                aliases.add(raw_id)
            buffers["transcript_aliases"].extend(
                (canonical_id, alias) for alias in sorted(aliases)
            )
        else:
            parents = split_attribute_values(
                feature.attrs.get("Parent") or feature.attrs.get("parent")
            )
            if not parents:
                parent = (feature.attrs.get("transcript_id") or "").strip()
                parents = (parent,) if parent else ()
            if not parents:
                raise FeatureIndexError(
                    f"{low} feature has no transcript parent: "
                    f"{feature.seqid}:{feature.start}-{feature.end}"
                )
            buffers["segments"].extend(
                (
                    _qualified_feature_id(parent, feature.seqid)
                    if parent in duplicate_transcript_ids
                    else parent,
                    segment_type,
                    feature.seqid,
                    feature.start,
                    feature.end,
                    feature.strand,
                    feature.phase if segment_type == 1 else None,
                )
                for parent in parents
            )
        buffered_rows += 1
        if buffered_rows >= INSERT_BATCH_SIZE:
            _flush_stage_rows(conn, buffers)
            buffered_rows = 0
    _flush_stage_rows(conn, buffers)


def _synthesize_missing_genes(conn: sqlite3.Connection) -> int:
    invalid_explicit_parent = conn.execute(
        """
        select t.transcript_id, t.gene_id
        from transcripts t
        left join genes g on g.gene_id=t.gene_id
        where g.gene_id is null and t.allow_synthetic_gene=0
        limit 1
        """
    ).fetchone()
    if invalid_explicit_parent is not None:
        raise FeatureIndexError(
            "transcript parent gene is missing: "
            f"{invalid_explicit_parent[0]} -> {invalid_explicit_parent[1]}"
        )
    incompatible = conn.execute(
        """
        select t.gene_id
        from transcripts t
        left join genes g on g.gene_id=t.gene_id
        where g.gene_id is null and t.allow_synthetic_gene=1
        group by t.gene_id
        having min(t.seqid) <> max(t.seqid)
           or count(distinct case when t.strand in ('+','-') then t.strand end) > 1
        limit 1
        """
    ).fetchone()
    if incompatible is not None:
        raise FeatureIndexError(
            f"cannot synthesize gene spanning seqids or strands: {incompatible[0]}"
        )
    before = conn.total_changes
    conn.execute(
        """
        insert into genes(gene_id,feature_type,seqid,start,end,strand,is_synthetic)
        select t.gene_id, 'synthetic_gene', min(t.seqid), min(t.start), max(t.end),
               case
                 when min(case when t.strand in ('+','-') then t.strand end) is null
                   then '.'
                 else min(case when t.strand in ('+','-') then t.strand end)
               end,
               1
        from transcripts t
        left join genes g on g.gene_id=t.gene_id
        where g.gene_id is null and t.allow_synthetic_gene=1
        group by t.gene_id
        """
    )
    return conn.total_changes - before


def _validate_staged_relationships(conn: sqlite3.Connection) -> None:
    bad_transcript = conn.execute(
        """
        select t.transcript_id
        from transcripts t
        join genes g on g.gene_id=t.gene_id
        where t.seqid <> g.seqid
        limit 1
        """
    ).fetchone()
    if bad_transcript is not None:
        raise FeatureIndexError(
            f"transcript location disagrees with gene: {bad_transcript[0]}"
        )
    orphan = conn.execute(
        """
        select s.transcript_id
        from segments s
        left join transcripts t on t.transcript_id=s.transcript_id
        where t.transcript_id is null
        limit 1
        """
    ).fetchone()
    if orphan is not None:
        raise FeatureIndexError(
            f"exon/CDS parent transcript is missing: {orphan[0]}"
        )
    bad_segment = conn.execute(
        """
        select s.transcript_id
        from segments s
        join transcripts t on t.transcript_id=s.transcript_id
        where s.seqid <> t.seqid
           or (s.strand not in ('.','?') and t.strand not in ('.','?')
               and s.strand <> t.strand)
        limit 1
        """
    ).fetchone()
    if bad_segment is not None:
        raise FeatureIndexError(
            f"exon/CDS location disagrees with transcript: {bad_segment[0]}"
        )


def _write_staged_segment_json(conn: sqlite3.Connection) -> None:
    cursor = conn.execute(
        """
        select s.transcript_id, s.segment_type, s.start, s.end, s.phase, t.strand
        from segments s
        join transcripts t on t.transcript_id=s.transcript_id
        order by s.transcript_id, s.segment_type, s.start, s.end, s.segment_pk
        """
    )
    current_id: str | None = None
    current_strand = "+"
    exons: list[tuple[int, int]] = []
    cds: list[tuple[int, int, int | None]] = []
    updates: list[tuple[Any, ...]] = []

    def finish() -> None:
        if current_id is None:
            return
        ordered_exons = list(reversed(exons)) if current_strand == "-" else exons
        ordered_cds = list(reversed(cds)) if current_strand == "-" else cds
        first = ordered_cds[0] if ordered_cds else None
        updates.append(
            (
                _compact_json(ordered_exons),
                _compact_json(ordered_cds),
                sum(end - start + 1 for start, end in ordered_exons),
                sum(end - start + 1 for start, end, _phase in ordered_cds),
                None
                if first is None
                else first[0]
                if current_strand == "+"
                else first[1],
                None if first is None else first[2],
                current_id,
            )
        )

    for transcript_id, segment_type, start, end, phase, strand in cursor:
        if current_id is not None and transcript_id != current_id:
            finish()
            exons = []
            cds = []
        current_id = str(transcript_id)
        current_strand = str(strand)
        if int(segment_type) == 0:
            exons.append((int(start), int(end)))
        else:
            cds.append((int(start), int(end), phase))
        if len(updates) >= INSERT_BATCH_SIZE:
            conn.executemany(
                """
                update transcripts
                set exon_segments=?, cds_segments=?, exon_length=?, cds_length=?,
                    cds_5p=?, first_cds_phase=?
                where transcript_id=?
                """,
                updates,
            )
            updates.clear()
    finish()
    if updates:
        conn.executemany(
            """
            update transcripts
            set exon_segments=?, cds_segments=?, exon_length=?, cds_length=?,
                cds_5p=?, first_cds_phase=?
            where transcript_id=?
            """,
            updates,
        )


def _select_representative_transcripts(
    conn: sqlite3.Connection, representative_map: dict[str, str]
) -> None:
    conn.executemany(
        "insert into representative_map(gene_id,transcript_id) values(?,?)",
        sorted(representative_map.items()),
    )
    unknown = conn.execute(
        """
        select m.gene_id
        from representative_map m
        left join genes g on g.gene_id=m.gene_id
        where g.gene_id is null
        limit 1
        """
    ).fetchone()
    if unknown is not None:
        raise FeatureIndexError(
            f"representative map contains unknown gene: {unknown[0]}"
        )
    invalid = conn.execute(
        """
        select m.gene_id, m.transcript_id
        from representative_map m
        left join transcripts t
          on t.transcript_id=m.transcript_id and t.gene_id=m.gene_id
        where t.transcript_id is null
        limit 1
        """
    ).fetchone()
    if invalid is not None:
        raise FeatureIndexError(
            f"invalid representative mapping: {invalid[0]} -> {invalid[1]}"
        )
    conn.execute(
        """
        update transcripts
        set is_representative=1, representative_source='mapping'
        where exists(
          select 1 from representative_map m
          where m.gene_id=transcripts.gene_id
            and m.transcript_id=transcripts.transcript_id
        )
        """
    )
    duplicate_hint = conn.execute(
        """
        select t.gene_id
        from transcripts t
        left join representative_map m on m.gene_id=t.gene_id
        where m.gene_id is null and t.representative_hint=1
        group by t.gene_id
        having count(*) > 1
        limit 1
        """
    ).fetchone()
    if duplicate_hint is not None:
        raise FeatureIndexError(
            f"multiple representative transcript hints for gene: {duplicate_hint[0]}"
        )
    conn.execute(
        """
        update transcripts
        set is_representative=1, representative_source='gff_attribute'
        where representative_hint=1
          and gene_id not in (select gene_id from representative_map)
        """
    )
    fallback_rows = conn.execute(
        """
        select transcript_id,
               case when cds_length > 0 then 'longest_cds' else 'longest_exon' end
        from (
          select t.*,
                 row_number() over(
                   partition by gene_id
                   order by
                     case when cds_length > 0 then 0 else 1 end,
                     case when cds_length > 0 then cds_length else exon_length end desc,
                     transcript_id collate binary
                 ) as choice_order
          from transcripts t
          where not exists(
            select 1 from transcripts selected
            where selected.gene_id=t.gene_id and selected.is_representative=1
          )
        )
        where choice_order=1
        """
    )
    batch: list[tuple[str, str]] = []
    for transcript_id, source in fallback_rows:
        batch.append((str(source), str(transcript_id)))
        if len(batch) >= INSERT_BATCH_SIZE:
            conn.executemany(
                """
                update transcripts
                set is_representative=1, representative_source=?
                where transcript_id=?
                """,
                batch,
            )
            batch.clear()
    if batch:
        conn.executemany(
            """
            update transcripts
            set is_representative=1, representative_source=?
            where transcript_id=?
            """,
            batch,
        )


def build_assembly_stage(
    *,
    db_root: Path,
    assembly: AssemblyInput,
    representative_map_path: Path | None = None,
    staging_dir: Path | None = None,
) -> StagedAssembly:
    db_root = db_root.resolve()
    reference_path = _resolve_db_resource(db_root, assembly.reference, "reference")
    annotation_path = _resolve_db_resource(db_root, assembly.annotation, "annotation")
    fai_path = _resolve_db_resource(db_root, assembly.fai, "FAI")
    seqids = load_fai(fai_path)
    duplicate_gene_ids: set[str] = set()
    duplicate_transcript_ids: set[str] = set()
    if assembly.assembly_id.startswith("phased_tetraploid/"):
        duplicate_gene_ids, duplicate_transcript_ids = (
            _cross_seqid_duplicate_feature_ids(annotation_path)
        )
    representative_map_path = representative_map_path_for(
        db_root=db_root,
        assembly=assembly,
        explicit_path=representative_map_path,
    )
    representative_map = load_representative_map(representative_map_path)
    stage_parent = (staging_dir or Path(tempfile.gettempdir())).resolve()
    stage_parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{assembly.assembly_id.replace('/', '_')}.",
        suffix=".feature-stage.sqlite",
        dir=stage_parent,
    )
    os.close(descriptor)
    stage_path = Path(temporary_name)
    stage_path.unlink()
    try:
        conn = sqlite3.connect(stage_path)
        try:
            conn.execute("pragma journal_mode=off")
            conn.execute("pragma synchronous=off")
            conn.execute("pragma temp_store=file")
            conn.execute("pragma cache_size=-131072")
            conn.executescript(STAGING_SCHEMA_SQL)
            conn.execute("begin")
            _stage_annotation(
                conn,
                annotation_path=annotation_path,
                seqids=seqids,
                duplicate_gene_ids=duplicate_gene_ids,
                duplicate_transcript_ids=duplicate_transcript_ids,
            )
            synthetic_gene_count = _synthesize_missing_genes(conn)
            _validate_staged_relationships(conn)
            _write_staged_segment_json(conn)
            _select_representative_transcripts(conn, representative_map)
            counts = {
                "genes": int(conn.execute("select count(*) from genes").fetchone()[0]),
                "transcripts": int(
                    conn.execute("select count(*) from transcripts").fetchone()[0]
                ),
                "exons": int(
                    conn.execute(
                        "select count(*) from segments where segment_type=0"
                    ).fetchone()[0]
                ),
                "cds": int(
                    conn.execute(
                        "select count(*) from segments where segment_type=1"
                    ).fetchone()[0]
                ),
                "synthetic_genes": synthetic_gene_count,
            }
            if (
                assembly.expected_gene_count is not None
                and counts["genes"] != assembly.expected_gene_count
            ):
                raise FeatureIndexError(
                    f"manifest gene count mismatch for {assembly.assembly_id}: "
                    f"{counts['genes']} != {assembly.expected_gene_count}"
                )
            if (
                assembly.expected_transcript_count is not None
                and counts["transcripts"] != assembly.expected_transcript_count
            ):
                raise FeatureIndexError(
                    f"manifest transcript count mismatch for {assembly.assembly_id}: "
                    f"{counts['transcripts']} != {assembly.expected_transcript_count}"
                )
            conn.execute("drop table segments")
            conn.commit()
            if conn.execute("pragma quick_check").fetchone()[0] != "ok":
                raise FeatureIndexError(
                    f"staging database quick_check failed: {assembly.assembly_id}"
                )
        finally:
            conn.close()
        annotation_stat = annotation_path.stat()
        reference_stat = reference_path.stat()
        return StagedAssembly(
            assembly=assembly,
            path=stage_path,
            seqids=seqids,
            counts=counts,
            annotation_bytes=annotation_stat.st_size,
            annotation_mtime_ns=annotation_stat.st_mtime_ns,
            annotation_sha256=file_sha256(annotation_path),
            reference_bytes=reference_stat.st_size,
            reference_mtime_ns=reference_stat.st_mtime_ns,
            fai_sha256=file_sha256(fai_path),
            representative_map_sha256=(
                file_sha256(representative_map_path) if representative_map_path else ""
            ),
        )
    except BaseException:
        stage_path.unlink(missing_ok=True)
        raise


SCHEMA_SQL = """
create table metadata(
  key text primary key,
  value text not null
) without rowid;

create table assemblies(
  assembly_pk integer primary key,
  assembly_id text not null,
  reference_path text not null,
  reference_bytes integer not null,
  reference_mtime_ns integer not null,
  fai_path text not null,
  fai_sha256 text not null,
  annotation_path text not null,
  annotation_bytes integer not null,
  annotation_mtime_ns integer not null,
  annotation_sha256 text not null,
  representative_map_sha256 text not null,
  gene_count integer not null,
  transcript_count integer not null,
  exon_count integer not null,
  cds_count integer not null,
  synthetic_gene_count integer not null,
  indexed_at text not null
) strict;

create table seqids(
  assembly_pk integer not null references assemblies(assembly_pk) on delete cascade,
  seqid text not null,
  length integer not null check(length > 0),
  primary key(assembly_pk, seqid)
) without rowid;

create table genes(
  gene_pk integer primary key,
  assembly_pk integer not null references assemblies(assembly_pk) on delete cascade,
  gene_id text not null,
  feature_type text not null,
  seqid text not null,
  start integer not null check(start >= 1),
  end integer not null check(end >= start),
  strand text not null,
  representative_transcript_pk integer references transcripts(transcript_pk)
) strict;

create table transcripts(
  transcript_pk integer primary key,
  assembly_pk integer not null references assemblies(assembly_pk) on delete cascade,
  gene_pk integer not null references genes(gene_pk) on delete cascade,
  transcript_id text not null,
  feature_type text not null,
  seqid text not null,
  start integer not null check(start >= 1),
  end integer not null check(end >= start),
  strand text not null,
  exon_segments text not null check(json_valid(exon_segments)),
  cds_segments text not null check(json_valid(cds_segments)),
  exon_length integer not null check(exon_length >= 0),
  cds_length integer not null check(cds_length >= 0),
  cds_5p integer,
  first_cds_phase integer check(first_cds_phase is null or first_cds_phase between 0 and 2),
  is_representative integer not null check(is_representative in (0, 1)),
  representative_source text not null
) strict;

create table gene_aliases(
  assembly_pk integer not null references assemblies(assembly_pk) on delete cascade,
  alias text not null,
  gene_pk integer not null references genes(gene_pk) on delete cascade,
  primary key(assembly_pk, alias, gene_pk)
) without rowid;

create table transcript_aliases(
  assembly_pk integer not null references assemblies(assembly_pk) on delete cascade,
  alias text not null,
  transcript_pk integer not null references transcripts(transcript_pk) on delete cascade,
  primary key(assembly_pk, alias, transcript_pk)
) without rowid;
"""


INDEX_SQL = """
create unique index uq_assemblies_id on assemblies(assembly_id);
create unique index uq_genes_assembly_id on genes(assembly_pk, gene_id);
create unique index uq_transcripts_assembly_id on transcripts(assembly_pk, transcript_id);
create index ix_transcripts_gene on transcripts(gene_pk, transcript_id);
create unique index uq_gene_representative
  on transcripts(gene_pk) where is_representative = 1;
create index ix_gene_alias_lookup on gene_aliases(assembly_pk, alias);
create index ix_transcript_alias_lookup on transcript_aliases(assembly_pk, alias);
"""


def create_schema(conn: sqlite3.Connection, *, create_indexes: bool = True) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.executemany(
        "insert into metadata(key, value) values(?, ?)",
        (
            ("schema_version", str(SCHEMA_VERSION)),
            ("representative_rule_version", REPRESENTATIVE_RULE_VERSION),
            ("feature_id_rule_version", FEATURE_ID_RULE_VERSION),
            ("storage_layout", STORAGE_LAYOUT),
            ("built_at", utc_now()),
        ),
    )
    if create_indexes:
        conn.executescript(INDEX_SQL)


def validate_schema(conn: sqlite3.Connection) -> None:
    try:
        rows = dict(
            conn.execute(
                "select key,value from metadata where key in "
                "('schema_version','representative_rule_version',"
                "'feature_id_rule_version','storage_layout')"
            )
        )
    except sqlite3.Error as exc:
        raise FeatureIndexError(f"feature index schema is unavailable: {exc}") from exc
    expected = {
        "schema_version": str(SCHEMA_VERSION),
        "representative_rule_version": REPRESENTATIVE_RULE_VERSION,
        "feature_id_rule_version": FEATURE_ID_RULE_VERSION,
        "storage_layout": STORAGE_LAYOUT,
    }
    for key, expected_value in expected.items():
        actual = rows.get(key)
        if actual != expected_value:
            raise FeatureIndexError(
                f"feature index {key} mismatch: {actual!r} != {expected_value!r}"
            )


def _compact_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def import_staged_assembly(
    conn: sqlite3.Connection,
    *,
    staged: StagedAssembly,
    replace: bool,
    validate_before_commit: bool = False,
) -> dict[str, Any]:
    assembly = staged.assembly
    conn.execute("attach database ? as incoming", (str(staged.path),))
    try:
        conn.execute("begin immediate")
        existing = conn.execute(
            "select assembly_pk from assemblies where assembly_id=?",
            (assembly.assembly_id,),
        ).fetchone()
        if existing is not None and not replace:
            raise FeatureIndexError(f"assembly is already indexed: {assembly.assembly_id}")
        if existing is not None:
            assembly_pk = int(existing[0])
            conn.execute(
                "update genes set representative_transcript_pk=null where assembly_pk=?",
                (assembly_pk,),
            )
            conn.execute("delete from assemblies where assembly_pk=?", (assembly_pk,))
        conn.execute(
            """
            insert into assemblies(
              assembly_id, reference_path, reference_bytes, reference_mtime_ns,
              fai_path, fai_sha256, annotation_path, annotation_bytes,
              annotation_mtime_ns, annotation_sha256, representative_map_sha256,
              gene_count, transcript_count, exon_count, cds_count,
              synthetic_gene_count, indexed_at
            ) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                assembly.assembly_id,
                assembly.reference,
                staged.reference_bytes,
                staged.reference_mtime_ns,
                assembly.fai,
                staged.fai_sha256,
                assembly.annotation,
                staged.annotation_bytes,
                staged.annotation_mtime_ns,
                staged.annotation_sha256,
                staged.representative_map_sha256,
                staged.counts["genes"],
                staged.counts["transcripts"],
                staged.counts["exons"],
                staged.counts["cds"],
                staged.counts["synthetic_genes"],
                utc_now(),
            ),
        )
        assembly_pk = int(conn.execute("select last_insert_rowid()").fetchone()[0])
        conn.executemany(
            "insert into seqids(assembly_pk,seqid,length) values(?,?,?)",
            (
                (assembly_pk, seqid, length)
                for seqid, length in staged.seqids.items()
            ),
        )
        conn.execute(
            """
            insert into genes(
              assembly_pk,gene_id,feature_type,seqid,start,end,strand,
              representative_transcript_pk
            )
            select ?,gene_id,feature_type,seqid,start,end,strand,null
            from incoming.genes
            order by gene_id
            """,
            (assembly_pk,),
        )
        conn.execute(
            """
            insert into transcripts(
              assembly_pk,gene_pk,transcript_id,feature_type,seqid,start,end,strand,
              exon_segments,cds_segments,exon_length,cds_length,cds_5p,
              first_cds_phase,is_representative,representative_source
            )
            select ?,g.gene_pk,t.transcript_id,t.feature_type,t.seqid,t.start,t.end,
                   t.strand,t.exon_segments,t.cds_segments,t.exon_length,t.cds_length,
                   t.cds_5p,t.first_cds_phase,t.is_representative,
                   t.representative_source
            from incoming.transcripts t
            join genes g on g.assembly_pk=? and g.gene_id=t.gene_id
            order by t.transcript_id
            """,
            (assembly_pk, assembly_pk),
        )
        conn.execute(
            """
            update genes
            set representative_transcript_pk=(
              select t.transcript_pk
              from transcripts t
              where t.gene_pk=genes.gene_pk and t.is_representative=1
            )
            where assembly_pk=?
            """,
            (assembly_pk,),
        )
        conn.execute(
            """
            insert into gene_aliases(assembly_pk,alias,gene_pk)
            select ?,a.alias,g.gene_pk
            from incoming.gene_aliases a
            join genes g on g.assembly_pk=? and g.gene_id=a.gene_id
            """,
            (assembly_pk, assembly_pk),
        )
        conn.execute(
            """
            insert into transcript_aliases(assembly_pk,alias,transcript_pk)
            select ?,a.alias,t.transcript_pk
            from incoming.transcript_aliases a
            join transcripts t
              on t.assembly_pk=? and t.transcript_id=a.transcript_id
            """,
            (assembly_pk, assembly_pk),
        )
        conn.execute(
            "update metadata set value=? where key='built_at'",
            (utc_now(),),
        )
        if validate_before_commit:
            validate_database(conn)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.execute("detach database incoming")
    return {"assembly": assembly.assembly_id, **staged.counts}


def insert_assembly(
    conn: sqlite3.Connection,
    *,
    db_root: Path,
    assembly: AssemblyInput,
    representative_map_path: Path | None = None,
) -> dict[str, Any]:
    main_path = Path(conn.execute("pragma database_list").fetchone()[2])
    staged = build_assembly_stage(
        db_root=db_root,
        assembly=assembly,
        representative_map_path=representative_map_path,
        staging_dir=main_path.parent,
    )
    try:
        return import_staged_assembly(conn, staged=staged, replace=False)
    finally:
        staged.path.unlink(missing_ok=True)


def assembly_inputs_from_manifest(db_root: Path) -> list[AssemblyInput]:
    manifest_path = db_root / "assemblies.json"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FeatureIndexError(f"failed to load genome browser manifest: {exc}") from exc
    result: list[AssemblyInput] = []
    for raw in payload.get("assemblies", []):
        if not isinstance(raw, dict):
            continue
        assembly_id = str(raw.get("id") or "").strip()
        reference = str(raw.get("reference") or "").strip()
        annotation = str(raw.get("annotation") or "").strip()
        fai = str(raw.get("fai") or "").strip()
        if not all((assembly_id, reference, annotation, fai)):
            raise FeatureIndexError(f"manifest assembly is incomplete: {assembly_id!r}")
        expected_gene_count = raw.get("geneCount")
        expected_transcript_count = raw.get("transcriptCount")
        try:
            parsed_gene_count = (
                None
                if expected_gene_count is None or expected_gene_count == ""
                else int(expected_gene_count)
            )
            parsed_transcript_count = (
                None
                if expected_transcript_count is None or expected_transcript_count == ""
                else int(expected_transcript_count)
            )
        except (TypeError, ValueError) as exc:
            raise FeatureIndexError(
                f"manifest assembly has invalid feature counts: {assembly_id}"
            ) from exc
        result.append(
            AssemblyInput(
                assembly_id=assembly_id,
                reference=reference,
                annotation=annotation,
                fai=fai,
                expected_gene_count=parsed_gene_count,
                expected_transcript_count=parsed_transcript_count,
                representative_map=str(raw.get("representativeMap") or "").strip(),
            )
        )
    if not result:
        raise FeatureIndexError("manifest contains no assemblies")
    identifiers = [item.assembly_id for item in result]
    if len(identifiers) != len(set(identifiers)):
        raise FeatureIndexError("manifest contains duplicate assembly IDs")
    return result


def validate_database(conn: sqlite3.Connection, expected_assemblies: int | None = None) -> dict[str, int]:
    validate_schema(conn)
    foreign_key_errors = conn.execute("pragma foreign_key_check").fetchmany(1)
    if foreign_key_errors:
        raise FeatureIndexError(f"feature index foreign-key check failed: {foreign_key_errors[0]}")
    quick_check = conn.execute("pragma quick_check").fetchone()
    if quick_check is None or quick_check[0] != "ok":
        raise FeatureIndexError(f"feature index quick_check failed: {quick_check}")
    counts = {
        "assemblies": int(conn.execute("select count(*) from assemblies").fetchone()[0]),
        "genes": int(conn.execute("select count(*) from genes").fetchone()[0]),
        "transcripts": int(conn.execute("select count(*) from transcripts").fetchone()[0]),
    }
    if expected_assemblies is not None and counts["assemblies"] != expected_assemblies:
        raise FeatureIndexError(
            f"feature index assembly count mismatch: {counts['assemblies']} != {expected_assemblies}"
        )
    missing_representatives = conn.execute(
        """
        select count(*)
        from genes g
        where exists(select 1 from transcripts t where t.gene_pk=g.gene_pk)
          and g.representative_transcript_pk is null
        """
    ).fetchone()[0]
    if missing_representatives:
        raise FeatureIndexError(f"genes with transcripts lack representative: {missing_representatives}")
    duplicate_or_invalid_representatives = conn.execute(
        """
        select count(*)
        from genes g
        join transcripts t on t.transcript_pk=g.representative_transcript_pk
        where t.gene_pk<>g.gene_pk or t.is_representative<>1
        """
    ).fetchone()[0]
    if duplicate_or_invalid_representatives:
        raise FeatureIndexError(
            "gene representative pointers do not match representative transcripts"
        )
    count_mismatch = conn.execute(
        """
        select a.assembly_id
        from assemblies a
        where a.gene_count<>(select count(*) from genes g where g.assembly_pk=a.assembly_pk)
           or a.transcript_count<>(
                select count(*) from transcripts t where t.assembly_pk=a.assembly_pk
              )
        limit 1
        """
    ).fetchone()
    if count_mismatch is not None:
        raise FeatureIndexError(
            f"stored assembly feature counts are inconsistent: {count_mismatch[0]}"
        )
    return counts


def build_full_index(
    *,
    db_root: Path,
    output: Path,
    representative_maps: dict[str, Path] | None = None,
    assembly_ids: set[str] | None = None,
) -> dict[str, int]:
    db_root = db_root.resolve()
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    selected = [
        assembly
        for assembly in assembly_inputs_from_manifest(db_root)
        if assembly_ids is None or assembly.assembly_id in assembly_ids
    ]
    if assembly_ids is not None:
        missing = assembly_ids - {item.assembly_id for item in selected}
        if missing:
            raise FeatureIndexError(f"unknown requested assembly: {min(missing)}")
    existing_stat = output.stat() if output.exists() else None
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    temporary.unlink()
    try:
        conn = sqlite3.connect(temporary)
        try:
            conn.execute("pragma foreign_keys=on")
            conn.execute("pragma journal_mode=off")
            conn.execute("pragma synchronous=off")
            conn.execute("pragma temp_store=file")
            conn.execute("pragma cache_size=-262144")
            create_schema(conn, create_indexes=True)
            conn.commit()
            for position, assembly in enumerate(selected, start=1):
                staged = build_assembly_stage(
                    db_root=db_root,
                    assembly=assembly,
                    representative_map_path=(representative_maps or {}).get(assembly.assembly_id),
                    staging_dir=output.parent,
                )
                try:
                    result = import_staged_assembly(conn, staged=staged, replace=False)
                    print(
                        json.dumps(
                            {
                                "event": "indexed",
                                "position": position,
                                "total": len(selected),
                                **result,
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                finally:
                    staged.path.unlink(missing_ok=True)
            conn.execute("analyze")
            conn.execute(
                "update metadata set value=? where key='built_at'",
                (utc_now(),),
            )
            conn.commit()
            counts = validate_database(conn, expected_assemblies=len(selected))
            conn.execute("pragma journal_mode=delete")
            conn.commit()
        finally:
            conn.close()
        mode = stat.S_IMODE(existing_stat.st_mode) if existing_stat is not None else 0o644
        os.chmod(temporary, mode)
        if existing_stat is not None:
            os.chown(temporary, existing_stat.st_uid, existing_stat.st_gid)
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, output)
        directory_fd = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return counts
    finally:
        temporary.unlink(missing_ok=True)


def _assembly_is_current(
    conn: sqlite3.Connection,
    *,
    db_root: Path,
    assembly: AssemblyInput,
    representative_map_path: Path | None,
    verify_annotation_hash: bool = True,
) -> bool:
    row = conn.execute(
        """
        select reference_path,reference_bytes,reference_mtime_ns,fai_path,fai_sha256,
               annotation_path,annotation_bytes,annotation_mtime_ns,annotation_sha256,
               representative_map_sha256,gene_count,transcript_count
        from assemblies where assembly_id=?
        """,
        (assembly.assembly_id,),
    ).fetchone()
    if row is None:
        return False
    reference_path = _resolve_db_resource(db_root, assembly.reference, "reference")
    annotation_path = _resolve_db_resource(db_root, assembly.annotation, "annotation")
    fai_path = _resolve_db_resource(db_root, assembly.fai, "FAI")
    reference_stat = reference_path.stat()
    annotation_stat = annotation_path.stat()
    expected = (
        assembly.reference,
        reference_stat.st_size,
        reference_stat.st_mtime_ns,
        assembly.fai,
        file_sha256(fai_path),
        assembly.annotation,
        annotation_stat.st_size,
        annotation_stat.st_mtime_ns,
        file_sha256(annotation_path) if verify_annotation_hash else str(row[8]),
        file_sha256(representative_map_path) if representative_map_path else "",
    )
    if tuple(row[:10]) != expected:
        return False
    if (
        assembly.expected_gene_count is not None
        and int(row[10]) != assembly.expected_gene_count
    ):
        return False
    if (
        assembly.expected_transcript_count is not None
        and int(row[11]) != assembly.expected_transcript_count
    ):
        return False
    return True


def sync_assemblies(
    *,
    db_root: Path,
    index_path: Path,
    assembly_ids: set[str] | None = None,
    representative_maps: dict[str, Path] | None = None,
) -> list[dict[str, Any]]:
    if not index_path.is_file():
        raise FeatureIndexError(f"feature index does not exist: {index_path}")
    if assembly_ids is None or len(assembly_ids) != 1:
        raise FeatureIndexError("sync requires exactly one assembly")
    by_id = {item.assembly_id: item for item in assembly_inputs_from_manifest(db_root)}
    selected_ids = assembly_ids
    missing = selected_ids - set(by_id)
    if missing:
        raise FeatureIndexError(f"unknown requested assembly: {min(missing)}")
    results: list[dict[str, Any]] = []
    conn = sqlite3.connect(index_path)
    try:
        conn.execute("pragma foreign_keys=on")
        conn.execute("pragma journal_mode=delete")
        conn.execute("pragma synchronous=full")
        conn.execute("pragma busy_timeout=30000")
        validate_schema(conn)
        for assembly_id in sorted(selected_ids):
            assembly = by_id[assembly_id]
            representative_map_path = representative_map_path_for(
                db_root=db_root,
                assembly=assembly,
                explicit_path=(representative_maps or {}).get(assembly_id),
            )
            if _assembly_is_current(
                conn,
                db_root=db_root,
                assembly=assembly,
                representative_map_path=representative_map_path,
            ):
                results.append({"assembly": assembly_id, "status": "unchanged"})
                continue
            staged = build_assembly_stage(
                db_root=db_root,
                assembly=assembly,
                representative_map_path=representative_map_path,
                staging_dir=index_path.parent,
            )
            try:
                result = import_staged_assembly(
                    conn,
                    staged=staged,
                    replace=True,
                    validate_before_commit=True,
                )
                results.append({**result, "status": "updated"})
            finally:
                staged.path.unlink(missing_ok=True)
        validate_database(conn)
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
    return results


def connect_readonly(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FeatureIndexError(f"feature index not found: {path}")
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma query_only=on")
    conn.execute("pragma trusted_schema=off")
    validate_schema(conn)
    return conn


def check_index(
    *,
    db_root: Path,
    index_path: Path,
    representative_maps: dict[str, Path] | None = None,
    deep: bool = False,
) -> dict[str, Any]:
    manifest = assembly_inputs_from_manifest(db_root)
    manifest_by_id = {item.assembly_id: item for item in manifest}
    with connect_readonly(index_path) as conn:
        counts = validate_database(conn)
        indexed_ids = {
            str(row[0]) for row in conn.execute("select assembly_id from assemblies")
        }
        manifest_ids = set(manifest_by_id)
        missing = sorted(manifest_ids - indexed_ids)
        orphan = sorted(indexed_ids - manifest_ids)
        stale: list[str] = []
        stale_errors: dict[str, str] = {}
        for assembly_id in sorted(manifest_ids & indexed_ids):
            assembly = manifest_by_id[assembly_id]
            try:
                representative_map_path = representative_map_path_for(
                    db_root=db_root,
                    assembly=assembly,
                    explicit_path=(representative_maps or {}).get(assembly_id),
                )
                current = _assembly_is_current(
                    conn,
                    db_root=db_root,
                    assembly=assembly,
                    representative_map_path=representative_map_path,
                    verify_annotation_hash=deep,
                )
            except (FeatureIndexError, OSError) as exc:
                current = False
                stale_errors[assembly_id] = str(exc)
            if not current:
                stale.append(assembly_id)
        built_at = conn.execute(
            "select value from metadata where key='built_at'"
        ).fetchone()[0]
    return {
        "ok": not missing and not orphan and not stale,
        "schemaVersion": SCHEMA_VERSION,
        "builtAt": built_at,
        **counts,
        "manifestAssemblies": len(manifest),
        "missingAssemblies": missing,
        "orphanAssemblies": orphan,
        "staleAssemblies": stale,
        "staleErrors": stale_errors,
        "deepHashVerification": deep,
    }


def _parse_segments(value: str, *, cds: bool, strand: str, seqid: str) -> list[dict[str, Any]]:
    raw = json.loads(value)
    result: list[dict[str, Any]] = []
    for order, item in enumerate(raw, start=1):
        segment: dict[str, Any] = {
            "order": order,
            "refName": seqid,
            "start": int(item[0]),
            "end": int(item[1]),
            "strand": strand,
        }
        if cds:
            segment["phase"] = item[2]
        result.append(segment)
    return result


def _transcript_payload(row: sqlite3.Row, ref_length: int) -> dict[str, Any]:
    return {
        "id": row["transcript_id"],
        "featureType": row["feature_type"],
        "refName": row["seqid"],
        "refLength": ref_length,
        "start": row["start"],
        "end": row["end"],
        "strand": row["strand"],
        "isRepresentative": bool(row["is_representative"]),
        "representativeSource": row["representative_source"],
        "exonLength": row["exon_length"],
        "cdsLength": row["cds_length"],
        "codingStart": row["cds_5p"],
        "cdsFivePrimePosition": row["cds_5p"],
        "firstCdsPhase": row["first_cds_phase"],
        "exons": _parse_segments(row["exon_segments"], cds=False, strand=row["strand"], seqid=row["seqid"]),
        "cds": _parse_segments(row["cds_segments"], cds=True, strand=row["strand"], seqid=row["seqid"]),
    }


def resolve_feature(path: Path, *, assembly_id: str, query_id: str) -> dict[str, Any]:
    query_id = query_id.strip()
    if not query_id:
        raise FeatureNotFoundError("feature ID is empty")
    with connect_readonly(path) as conn:
        assembly = conn.execute(
            "select assembly_pk from assemblies where assembly_id=?",
            (assembly_id,),
        ).fetchone()
        if assembly is None:
            raise FeatureNotFoundError(f"assembly not indexed: {assembly_id}")
        assembly_pk = int(assembly["assembly_pk"])
        gene = conn.execute(
            "select gene_pk from genes where assembly_pk=? and gene_id=?",
            (assembly_pk, query_id),
        ).fetchone()
        transcript = None
        matched_by = "id"
        resolved_type = "gene"
        if gene is None:
            transcript = conn.execute(
                "select transcript_pk, gene_pk from transcripts where assembly_pk=? and transcript_id=?",
                (assembly_pk, query_id),
            ).fetchone()
            if transcript is not None:
                gene = {"gene_pk": transcript["gene_pk"]}
                resolved_type = "transcript"
        if gene is None:
            candidates: set[tuple[str, int]] = set()
            candidates.update(
                ("gene", int(row[0]))
                for row in conn.execute(
                    "select gene_pk from gene_aliases where assembly_pk=? and alias=? limit 11",
                    (assembly_pk, query_id),
                )
            )
            candidates.update(
                ("transcript", int(row[0]))
                for row in conn.execute(
                    "select transcript_pk from transcript_aliases where assembly_pk=? and alias=? limit 11",
                    (assembly_pk, query_id),
                )
            )
            if not candidates:
                raise FeatureNotFoundError(f"feature ID not found: {query_id}")
            if len(candidates) > 1:
                labels: list[tuple[str, str]] = []
                for kind, primary_key in sorted(candidates)[:10]:
                    table = "genes" if kind == "gene" else "transcripts"
                    pk = "gene_pk" if kind == "gene" else "transcript_pk"
                    identifier = "gene_id" if kind == "gene" else "transcript_id"
                    row = conn.execute(
                        f"select {identifier} from {table} where {pk}=?",
                        (primary_key,),
                    ).fetchone()
                    labels.append((kind, str(row[0])))
                raise AmbiguousFeatureError(labels)
            kind, primary_key = next(iter(candidates))
            matched_by = "alias"
            resolved_type = kind
            if kind == "gene":
                gene = {"gene_pk": primary_key}
            else:
                transcript = conn.execute(
                    "select transcript_pk, gene_pk from transcripts where transcript_pk=?",
                    (primary_key,),
                ).fetchone()
                gene = {"gene_pk": transcript["gene_pk"]}

        gene_row = conn.execute(
            """
            select g.*, rt.transcript_id as representative_transcript_id,
                   s.length as ref_length
            from genes g
            join seqids s on s.assembly_pk=g.assembly_pk and s.seqid=g.seqid
            left join transcripts rt on rt.transcript_pk=g.representative_transcript_pk
            where g.gene_pk=?
            """,
            (gene["gene_pk"],),
        ).fetchone()
        if gene_row is None:
            raise FeatureIndexError("feature index gene relationship is corrupt")
        if resolved_type == "transcript":
            transcript_filter = "and t.transcript_pk=?"
            transcript_params: tuple[Any, ...] = (gene_row["gene_pk"], transcript["transcript_pk"])
        else:
            transcript_filter = ""
            transcript_params = (gene_row["gene_pk"],)
        transcript_rows = conn.execute(
            f"""
            select t.*, s.length as ref_length
            from transcripts t
            join seqids s on s.assembly_pk=t.assembly_pk and s.seqid=t.seqid
            where t.gene_pk=? {transcript_filter}
            order by t.is_representative desc, t.transcript_id
            """,
            transcript_params,
        ).fetchall()
        resolved_id = gene_row["gene_id"] if resolved_type == "gene" else transcript_rows[0]["transcript_id"]
        return {
            "assembly": assembly_id,
            "indexVersion": SCHEMA_VERSION,
            "coordinateSystem": "1-based-inclusive",
            "query": {
                "id": query_id,
                "matchedBy": matched_by,
                "resolvedType": resolved_type,
                "resolvedId": resolved_id,
            },
            "gene": {
                "id": gene_row["gene_id"],
                "featureType": gene_row["feature_type"],
                "refName": gene_row["seqid"],
                "refLength": gene_row["ref_length"],
                "start": gene_row["start"],
                "end": gene_row["end"],
                "strand": gene_row["strand"],
                "representativeTranscriptId": gene_row["representative_transcript_id"],
            },
            "transcripts": [
                _transcript_payload(row, int(row["ref_length"]))
                for row in transcript_rows
            ],
        }


def index_summary(path: Path) -> dict[str, Any]:
    with connect_readonly(path) as conn:
        counts = validate_database(conn)
        return {
            "schemaVersion": SCHEMA_VERSION,
            "builtAt": conn.execute("select value from metadata where key='built_at'").fetchone()[0],
            **counts,
        }
