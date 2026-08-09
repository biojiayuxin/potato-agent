#!/usr/bin/env python3
"""Deterministic sequence extraction from indexed genomes and GFF3/GTF.

The AI selects explicit command-line parameters; this script performs resource
resolution, annotation parsing, coordinate calculations, indexed extraction,
strand handling, splicing, translation, and auditable reporting.

Coordinates accepted from users are 1-based closed intervals.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import re
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, unquote
from urllib.request import Request, urlopen

VERSION = "4.2.0"
DEFAULT_POTATO_ROOT = Path("/mnt/data/public_data/Genome_browser_DB")
DEFAULT_OTHER_ROOT = Path("/mnt/data/public_data/Other_species_genomes")
DEFAULT_API_BASE_URL = "https://potato-agent.ynnu.edu.cn"
DEFAULT_FEATURE_INDEX_NAME = "feature_index.sqlite"
FEATURE_INDEX_METADATA = {
    "schema_version": "1",
    "representative_rule_version": "explicit-map-or-annotation-then-longest-cds-v1",
    "feature_id_rule_version": "raw-or-raw-at-seqid-for-cross-seqid-duplicates-v1",
    "storage_layout": "compact-transcript-segments-v1",
}
FEATURE_INDEX_REQUIRED_COLUMNS = {
    "assemblies": {
        "assembly_pk", "assembly_id", "reference_path", "reference_bytes",
        "reference_mtime_ns", "fai_path", "fai_sha256", "annotation_path",
        "annotation_bytes", "annotation_mtime_ns", "annotation_sha256",
        "representative_map_sha256",
    },
    "seqids": {"assembly_pk", "seqid", "length"},
    "genes": {
        "gene_pk", "assembly_pk", "gene_id", "feature_type", "seqid", "start",
        "end", "strand", "representative_transcript_pk",
    },
    "transcripts": {
        "transcript_pk", "assembly_pk", "gene_pk", "transcript_id",
        "feature_type", "seqid", "start", "end", "strand", "exon_segments",
        "cds_segments", "exon_length", "cds_length", "cds_5p",
        "first_cds_phase", "is_representative", "representative_source",
    },
    "gene_aliases": {"assembly_pk", "alias", "gene_pk"},
    "transcript_aliases": {"assembly_pk", "alias", "transcript_pk"},
}
MAX_API_RESPONSE_BYTES = 2_500_000
MAX_API_SEQUENCE_BP = 1_000_000
MAX_API_SEGMENTS = 256
RC_TABLE = str.maketrans(
    "ACGTRYKMSWBDHVNacgtrykmswbdhvn",
    "TGCAYRMKSWVHDBNtgcayrmkswvhdbn",
)
VALID_NT = set("ACGTRYKMSWBDHVNacgtrykmswbdhvn")
TRANSCRIPT_TYPES = {
    "mrna", "transcript", "ncrna", "lncrna", "trna", "rrna", "snrna",
    "snorna", "mirna", "pre_mirna", "primary_transcript", "pseudogenic_transcript",
}
ALIAS_KEYS = {
    "ID", "Name", "gene", "gene_id", "geneID", "geneId", "locus_tag",
    "transcript", "transcript_id", "transcriptID", "transcriptId", "protein_id",
}

CODON_TABLE = {
    "TTT":"F", "TTC":"F", "TTA":"L", "TTG":"L", "TCT":"S", "TCC":"S", "TCA":"S", "TCG":"S",
    "TAT":"Y", "TAC":"Y", "TAA":"*", "TAG":"*", "TGT":"C", "TGC":"C", "TGA":"*", "TGG":"W",
    "CTT":"L", "CTC":"L", "CTA":"L", "CTG":"L", "CCT":"P", "CCC":"P", "CCA":"P", "CCG":"P",
    "CAT":"H", "CAC":"H", "CAA":"Q", "CAG":"Q", "CGT":"R", "CGC":"R", "CGA":"R", "CGG":"R",
    "ATT":"I", "ATC":"I", "ATA":"I", "ATG":"M", "ACT":"T", "ACC":"T", "ACA":"T", "ACG":"T",
    "AAT":"N", "AAC":"N", "AAA":"K", "AAG":"K", "AGT":"S", "AGC":"S", "AGA":"R", "AGG":"R",
    "GTT":"V", "GTC":"V", "GTA":"V", "GTG":"V", "GCT":"A", "GCC":"A", "GCA":"A", "GCG":"A",
    "GAT":"D", "GAC":"D", "GAA":"E", "GAG":"E", "GGT":"G", "GGC":"G", "GGA":"G", "GGG":"G",
}


class ExtractionError(RuntimeError):
    pass


@dataclass
class Feature:
    feature_id: str
    seqid: str
    start: int
    end: int
    strand: str
    feature_type: str
    parents: Tuple[str, ...] = ()
    attrs: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Segment:
    seqid: str
    start: int
    end: int
    strand: str
    phase: Optional[int] = None


@dataclass
class RecordPlan:
    query_id: str
    resolved_id: str = ""
    resolved_type: str = ""
    transcript_id: str = ""
    seqid: str = ""
    strand: str = "+"
    segments: List[Segment] = field(default_factory=list)
    status: str = "PLANNED"
    message: str = ""
    sequence: str = ""
    requested_length: int = 0
    actual_length: int = 0
    clipped: bool = False
    phase_trim: int = 0
    trailing_trim: int = 0
    internal_stops: int = 0
    invalid_chars: int = 0
    representative_source: str = ""


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Deterministic sequence extraction using Genome_browser_DB, Other_species_genomes, or explicit files."
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    p.add_argument(
        "--mode", required=True,
        choices=["list-resources", "region", "gene", "promoter", "gene-window", "cds", "transcript", "protein"],
    )

    resource = p.add_argument_group("resource selection")
    resource.add_argument("--assembly", help="Potato assembly id/sample (e.g. DMv8.2 or phased_diploid/01-58), or exact other-species directory name")
    resource.add_argument("--resource-dir", help="Explicit non-potato resource directory")
    resource.add_argument("--genome", help="Explicit genome FASTA; overrides resolved genome")
    resource.add_argument("--annotation", help="Explicit GFF3/GTF; overrides resolved annotation")
    resource.add_argument("--potato-root", default=str(DEFAULT_POTATO_ROOT))
    resource.add_argument("--other-root", default=str(DEFAULT_OTHER_ROOT))
    resource.add_argument(
        "--feature-index",
        help="Local centralized feature index; defaults to POTATO_ROOT/feature_index.sqlite",
    )
    resource.add_argument(
        "--source", choices=["auto", "local", "api"], default="auto",
        help="Prefer a complete local database, fall back to the Interface API, or require one backend [default: auto]",
    )
    resource.add_argument(
        "--api-base-url", default=DEFAULT_API_BASE_URL,
        help=f"Interface base URL [default: {DEFAULT_API_BASE_URL}]",
    )
    resource.add_argument("--api-timeout", type=float, default=30.0)

    query = p.add_argument_group("feature queries")
    query.add_argument("--id", action="append", dest="inline_ids", help="Gene/transcript ID; repeat as needed")
    query.add_argument("--ids", action="append", help="ID file; first whitespace-delimited column is used")
    query.add_argument(
        "--isoform", choices=["error", "representative", "longest", "all"], default=None,
        help="Policy when a gene has multiple transcripts; promoter defaults to representative, other modes to error",
    )

    region = p.add_argument_group("region mode")
    region.add_argument("--regions", help="TSV columns: name,seqid,start,end; optional strand,note")
    region.add_argument("--seqid")
    region.add_argument("--start", type=int)
    region.add_argument("--end", type=int)
    region.add_argument("--strand", choices=["+", "-"], default="+")
    region.add_argument("--name")

    relative = p.add_argument_group("gene-relative modes")
    relative.add_argument("--length", type=int, help="Promoter length in bp")
    relative.add_argument("--upstream", type=int, default=0, help="Gene-window upstream bp [default: 0]")
    relative.add_argument("--downstream", type=int, default=0, help="Gene-window downstream bp [default: 0]")

    output = p.add_argument_group("output and validation")
    output.add_argument("--output", help="Output FASTA (required except list-resources)")
    output.add_argument("--report", help="Output audit TSV (required except list-resources)")
    output.add_argument("--missing", help="Failed query IDs; defaults to OUTPUT.missing.txt")
    output.add_argument("--metadata", help="Run metadata JSON; defaults to OUTPUT.meta.json")
    output.add_argument("--clip", action="store_true", help="Clip out-of-bound genomic windows; never silently clip by default")
    output.add_argument("--wrap", type=int, default=60)
    output.add_argument("--samtools", default="samtools")
    args = p.parse_args()

    if args.wrap < 0:
        p.error("--wrap must be >= 0")
    if args.api_timeout <= 0:
        p.error("--api-timeout must be > 0")
    if args.source == "api" and (
        args.genome or args.annotation or args.resource_dir or args.feature_index
    ):
        p.error(
            "--source api cannot be combined with --genome, --annotation, "
            "--resource-dir, or --feature-index"
        )
    if args.feature_index and (args.genome or args.annotation or args.resource_dir):
        p.error(
            "--feature-index cannot be combined with --genome, --annotation, "
            "or --resource-dir"
        )
    if args.mode == "list-resources":
        return args
    if not args.output or not args.report:
        p.error("--output and --report are required for extraction modes")
    if args.mode == "region":
        single = [args.seqid is not None, args.start is not None, args.end is not None]
        if args.regions and any(single):
            p.error("Use --regions or single-region arguments, not both")
        if not args.regions and not all(single):
            p.error("Region mode requires --regions or --seqid --start --end")
    else:
        if not args.inline_ids and not args.ids:
            p.error(f"{args.mode} mode requires at least one --id or --ids")
        if args.mode == "promoter" and (args.length is None or args.length < 1):
            p.error("Promoter mode requires --length >= 1")
        if args.mode == "gene-window" and (args.upstream < 0 or args.downstream < 0):
            p.error("--upstream and --downstream must be >= 0")
    if args.isoform is None:
        args.isoform = "representative" if args.mode == "promoter" else "error"
    return args


def eprint(message: str) -> None:
    sys.stderr.write(message.rstrip() + "\n")


def local_feature_index_path(args: argparse.Namespace, potato_root: Path) -> Path:
    configured = getattr(args, "feature_index", None)
    return Path(configured).resolve() if configured else potato_root / DEFAULT_FEATURE_INDEX_NAME


def connect_local_feature_index(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise ExtractionError(f"Local feature index not found: {path}")
    conn: Optional[sqlite3.Connection] = None
    try:
        conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("pragma query_only=on")
        conn.execute("pragma trusted_schema=off")
        rows = dict(
            conn.execute(
                "select key,value from metadata where key in "
                "('schema_version','representative_rule_version',"
                "'feature_id_rule_version','storage_layout')"
            )
        )
        for table, required_columns in FEATURE_INDEX_REQUIRED_COLUMNS.items():
            actual_columns = {
                str(row[1]) for row in conn.execute(f"pragma table_info({table})")
            }
            missing_columns = required_columns - actual_columns
            if missing_columns:
                raise ExtractionError(
                    f"Local feature index table {table} lacks columns: "
                    f"{sorted(missing_columns)}"
                )
    except (ExtractionError, OSError, sqlite3.Error) as exc:
        if conn is not None:
            conn.close()
        if isinstance(exc, ExtractionError):
            raise
        raise ExtractionError(f"Local feature index is unavailable: {exc}") from exc
    for key, expected in FEATURE_INDEX_METADATA.items():
        if rows.get(key) != expected:
            conn.close()
            raise ExtractionError(
                f"Local feature index {key} mismatch: {rows.get(key)!r} != {expected!r}"
            )
    return conn


def feature_index_has_assembly(path: Path, assembly_id: str) -> bool:
    try:
        conn = connect_local_feature_index(path)
    except (ExtractionError, OSError):
        return False
    try:
        return conn.execute(
            "select 1 from assemblies where assembly_id=?",
            (assembly_id,),
        ).fetchone() is not None
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _manifest_file_path(root: Path, value: str) -> Optional[Path]:
    relative = Path(value)
    if not value or relative.is_absolute() or ".." in relative.parts:
        return None
    path = (root / relative).resolve()
    if root != path and root not in path.parents:
        return None
    try:
        if path.is_file() and path.stat().st_size > 0 and os.access(path, os.R_OK):
            return path
    except OSError:
        pass
    return None


def _samtools_available(value: str) -> bool:
    executable = shutil.which(value) if os.path.sep not in value else value
    return bool(executable and Path(executable).is_file() and os.access(executable, os.X_OK))


def _valid_fai(path: Path) -> bool:
    names: Set[str] = set()
    try:
        if not path.is_file() or not os.access(path, os.R_OK):
            return False
        with path.open("r", encoding="utf-8") as handle:
            for raw in handle:
                fields = raw.rstrip("\n").split("\t")
                if len(fields) < 5 or not fields[0] or fields[0] in names:
                    return False
                length, offset, line_bases, line_width = map(int, fields[1:5])
                if (
                    length <= 0
                    or offset < 0
                    or line_bases <= 0
                    or line_width < line_bases
                ):
                    return False
                names.add(fields[0])
        return bool(names)
    except (OSError, UnicodeDecodeError, ValueError):
        return False


def _valid_gzi(path: Path) -> bool:
    try:
        if not path.is_file() or not os.access(path, os.R_OK):
            return False
        size = path.stat().st_size
        if size < 8:
            return False
        with path.open("rb") as handle:
            count_raw = handle.read(8)
            if len(count_raw) != 8:
                return False
            count = struct.unpack("<Q", count_raw)[0]
            if size != 8 + count * 16:
                return False
            previous_compressed = -1
            previous_uncompressed = -1
            for _index in range(count):
                pair = handle.read(16)
                if len(pair) != 16:
                    return False
                compressed, uncompressed = struct.unpack("<QQ", pair)
                if (
                    compressed <= previous_compressed
                    or uncompressed <= previous_uncompressed
                ):
                    return False
                previous_compressed = compressed
                previous_uncompressed = uncompressed
            return handle.read(1) == b""
    except (OSError, struct.error):
        return False


def _reference_indexes_available(reference: Path) -> bool:
    if not _valid_fai(Path(str(reference) + ".fai")):
        return False
    if reference.name.lower().endswith((".gz", ".bgz")):
        return _valid_gzi(Path(str(reference) + ".gzi"))
    return True


def _representative_map_sha256(root: Path, assembly_id: str) -> Optional[str]:
    manifest_path = root / "assemblies.json"
    if not manifest_path.is_file():
        return None
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExtractionError(f"Local assemblies.json is invalid: {exc}") from exc
    assemblies = payload.get("assemblies") if isinstance(payload, dict) else None
    if not isinstance(assemblies, list):
        raise ExtractionError("Local assemblies.json lacks assemblies")
    matches = [
        item for item in assemblies
        if isinstance(item, dict) and str(item.get("id") or "") == assembly_id
    ]
    if len(matches) != 1:
        raise ExtractionError(
            f"Local assemblies.json does not uniquely identify {assembly_id}"
        )
    relative = str(matches[0].get("representativeMap") or "").strip()
    if not relative:
        return ""
    path = _manifest_file_path(root, relative)
    if path is None:
        raise ExtractionError(
            f"Local representative map is unavailable for {assembly_id}: {relative}"
        )
    return file_sha256(path)


def feature_index_assembly_is_current(
    path: Path,
    *,
    root: Path,
    entry: Dict[str, str],
) -> bool:
    assembly_id = entry.get("id", "")
    reference_relative = entry.get("reference", "")
    annotation_relative = entry.get("annotation", "")
    reference = _manifest_file_path(root, reference_relative)
    annotation = _manifest_file_path(root, annotation_relative)
    if not assembly_id or reference is None or annotation is None:
        return False
    try:
        if not reference.is_file() or not os.access(reference, os.R_OK):
            return False
        fai = Path(str(reference) + ".fai")
        if not _reference_indexes_available(reference):
            return False
        reference_stat = reference.stat()
        annotation_stat = annotation.stat()
        expected_representative_hash = _representative_map_sha256(root, assembly_id)
        conn = connect_local_feature_index(path)
        try:
            row = conn.execute(
                """
                select reference_path,reference_bytes,reference_mtime_ns,
                       fai_path,fai_sha256,annotation_path,annotation_bytes,
                       annotation_mtime_ns,representative_map_sha256
                from assemblies where assembly_id=?
                """,
                (assembly_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return False
        if row["reference_path"] != reference_relative:
            return False
        if row["fai_path"] != reference_relative + ".fai":
            return False
        if row["annotation_path"] != annotation_relative:
            return False
        if int(row["reference_bytes"]) != reference_stat.st_size:
            return False
        if int(row["reference_mtime_ns"]) != reference_stat.st_mtime_ns:
            return False
        if int(row["annotation_bytes"]) != annotation_stat.st_size:
            return False
        if int(row["annotation_mtime_ns"]) != annotation_stat.st_mtime_ns:
            return False
        if str(row["fai_sha256"]) != file_sha256(fai):
            return False
        if (
            expected_representative_hash is not None
            and str(row["representative_map_sha256"]) != expected_representative_hash
        ):
            return False
        return True
    except (ExtractionError, OSError, sqlite3.Error, TypeError, ValueError):
        return False


def local_potato_assembly_available(
    args: argparse.Namespace,
    root: Path,
    entry: Dict[str, str],
) -> bool:
    reference = _manifest_file_path(root, entry.get("reference", ""))
    if reference is None:
        return False
    if args.mode == "list-resources":
        return True
    if not _samtools_available(getattr(args, "samtools", "samtools")):
        return False
    if not _reference_indexes_available(reference):
        return False
    if args.mode == "region":
        return True
    assembly_id = entry.get("id", "")
    return bool(assembly_id) and feature_index_assembly_is_current(
        local_feature_index_path(args, root),
        root=root,
        entry=entry,
    )


def _has_local_other_resources(root: Path) -> bool:
    try:
        return root.is_dir() and os.access(root, os.R_OK | os.X_OK) and any(
            item.is_dir() for item in root.iterdir()
        )
    except OSError:
        return False


def effective_source(args: argparse.Namespace) -> str:
    if args.source in {"local", "api"}:
        return args.source
    if args.genome or args.annotation or args.resource_dir:
        return "local"
    potato_root = Path(args.potato_root).resolve()
    potato_manifest = potato_root / "assemblies.tsv"
    other_root = Path(args.other_root).resolve()
    try:
        potato_manifest_available = (
            potato_manifest.is_file()
            and potato_manifest.stat().st_size > 0
            and os.access(potato_manifest, os.R_OK)
        )
    except OSError:
        potato_manifest_available = False
    if args.assembly:
        entry = (
            resolve_manifest_entry(potato_root, args.assembly)
            if potato_manifest_available
            else None
        )
        if entry is not None:
            return (
                "local"
                if local_potato_assembly_available(args, potato_root, entry)
                else "api"
            )
        if resolve_other_dir(other_root, args.assembly) is not None:
            return "local"
        return "api"
    if potato_manifest_available or _has_local_other_resources(other_root):
        return "local"
    return "api"


def _parse_index_segments(
    value: str,
    *,
    cds: bool,
    strand: str,
    seqid: str,
) -> List[Dict[str, object]]:
    try:
        raw = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ExtractionError("Local feature index contains invalid segment JSON") from exc
    if not isinstance(raw, list):
        raise ExtractionError("Local feature index contains invalid segments")
    result: List[Dict[str, object]] = []
    for order, item in enumerate(raw, start=1):
        if not isinstance(item, list) or len(item) < 2:
            raise ExtractionError("Local feature index contains an invalid segment")
        segment: Dict[str, object] = {
            "order": order,
            "refName": seqid,
            "start": int(item[0]),
            "end": int(item[1]),
            "strand": strand,
        }
        if cds:
            segment["phase"] = item[2] if len(item) >= 3 else None
        result.append(segment)
    return result


def _local_transcript_payload(row: sqlite3.Row) -> Dict[str, object]:
    return {
        "id": row["transcript_id"],
        "featureType": row["feature_type"],
        "refName": row["seqid"],
        "refLength": row["ref_length"],
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
        "exons": _parse_index_segments(
            row["exon_segments"],
            cds=False,
            strand=row["strand"],
            seqid=row["seqid"],
        ),
        "cds": _parse_index_segments(
            row["cds_segments"],
            cds=True,
            strand=row["strand"],
            seqid=row["seqid"],
        ),
    }


def resolve_local_feature(
    conn: sqlite3.Connection,
    *,
    assembly_id: str,
    query_id: str,
) -> Dict[str, object]:
    query_id = query_id.strip()
    if not query_id:
        raise ExtractionError("Feature ID is empty")
    assembly = conn.execute(
        "select assembly_pk from assemblies where assembly_id=?",
        (assembly_id,),
    ).fetchone()
    if assembly is None:
        raise ExtractionError(f"Assembly is not in the local feature index: {assembly_id}")
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
            "select transcript_pk,gene_pk from transcripts "
            "where assembly_pk=? and transcript_id=?",
            (assembly_pk, query_id),
        ).fetchone()
        if transcript is not None:
            gene = {"gene_pk": transcript["gene_pk"]}
            resolved_type = "transcript"
    if gene is None:
        candidates: Set[Tuple[str, int]] = set()
        candidates.update(
            ("gene", int(row[0]))
            for row in conn.execute(
                "select gene_pk from gene_aliases "
                "where assembly_pk=? and alias=? limit 11",
                (assembly_pk, query_id),
            )
        )
        candidates.update(
            ("transcript", int(row[0]))
            for row in conn.execute(
                "select transcript_pk from transcript_aliases "
                "where assembly_pk=? and alias=? limit 11",
                (assembly_pk, query_id),
            )
        )
        if not candidates:
            raise ExtractionError(f"Feature ID not found: {query_id}")
        if len(candidates) > 1:
            labels: List[str] = []
            for kind, primary_key in sorted(candidates)[:10]:
                table = "genes" if kind == "gene" else "transcripts"
                pk = "gene_pk" if kind == "gene" else "transcript_pk"
                identifier = "gene_id" if kind == "gene" else "transcript_id"
                row = conn.execute(
                    f"select {identifier} from {table} where {pk}=?",
                    (primary_key,),
                ).fetchone()
                labels.append(f"{kind}:{row[0]}")
            raise ExtractionError(
                "feature alias is ambiguous; use one exact candidate: "
                + ", ".join(labels)
            )
        kind, primary_key = next(iter(candidates))
        matched_by = "alias"
        resolved_type = kind
        if kind == "gene":
            gene = {"gene_pk": primary_key}
        else:
            transcript = conn.execute(
                "select transcript_pk,gene_pk from transcripts where transcript_pk=?",
                (primary_key,),
            ).fetchone()
            if transcript is None:
                raise ExtractionError("Local feature index transcript alias is corrupt")
            gene = {"gene_pk": transcript["gene_pk"]}

    gene_row = conn.execute(
        """
        select g.*,rt.transcript_id as representative_transcript_id,
               s.length as ref_length
        from genes g
        join seqids s on s.assembly_pk=g.assembly_pk and s.seqid=g.seqid
        left join transcripts rt on rt.transcript_pk=g.representative_transcript_pk
        where g.gene_pk=?
        """,
        (gene["gene_pk"],),
    ).fetchone()
    if gene_row is None:
        raise ExtractionError("Local feature index gene relationship is corrupt")
    if resolved_type == "transcript":
        transcript_filter = "and t.transcript_pk=?"
        transcript_params = (gene_row["gene_pk"], transcript["transcript_pk"])
    else:
        transcript_filter = ""
        transcript_params = (gene_row["gene_pk"],)
    transcript_rows = conn.execute(
        f"""
        select t.*,s.length as ref_length
        from transcripts t
        join seqids s on s.assembly_pk=t.assembly_pk and s.seqid=t.seqid
        where t.gene_pk=? {transcript_filter}
        order by t.is_representative desc,t.transcript_id
        """,
        transcript_params,
    ).fetchall()
    if resolved_type == "transcript" and not transcript_rows:
        raise ExtractionError("Local feature index transcript relationship is corrupt")
    resolved_id = (
        gene_row["gene_id"]
        if resolved_type == "gene"
        else transcript_rows[0]["transcript_id"]
    )
    return {
        "assembly": assembly_id,
        "indexVersion": int(FEATURE_INDEX_METADATA["schema_version"]),
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
        "transcripts": [_local_transcript_payload(row) for row in transcript_rows],
    }


def api_url(args: argparse.Namespace, path: str, query: Optional[Dict[str, str]] = None) -> str:
    base = args.api_base_url.strip().rstrip("/")
    if not base.startswith(("https://", "http://")):
        raise ExtractionError("--api-base-url must start with https:// or http://")
    url = base + path
    if query:
        url += "?" + urlencode(query)
    return url


def format_api_error_detail(status: int, payload: object) -> str:
    fallback = f"HTTP {status}"
    if not isinstance(payload, dict):
        return fallback
    detail = payload.get("detail")
    if isinstance(detail, str):
        return detail or fallback
    if not isinstance(detail, dict):
        return fallback
    message = str(detail.get("message") or fallback)
    raw_candidates = detail.get("candidates")
    if not isinstance(raw_candidates, list):
        return message
    candidates: List[str] = []
    for item in raw_candidates:
        if not isinstance(item, dict):
            continue
        feature_id = str(item.get("id") or "").strip()
        feature_type = str(item.get("type") or "feature").strip()
        if feature_id:
            candidates.append(f"{feature_type}:{feature_id}")
    if not candidates:
        return message
    return f"{message}; use one exact candidate: {', '.join(candidates)}"


def api_request_json(
    args: argparse.Namespace,
    path: str,
    *,
    query: Optional[Dict[str, str]] = None,
    payload: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    data = None
    headers = {"Accept": "application/json", "User-Agent": f"potato-genome-sequence-extraction/{VERSION}"}
    method = "GET"
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
        method = "POST"
    request = Request(api_url(args, path, query), data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=args.api_timeout) as response:
            body = response.read(MAX_API_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        detail = f"HTTP {exc.code}"
        try:
            error_body = exc.read(64_000)
            parsed = json.loads(error_body.decode("utf-8"))
            detail = format_api_error_detail(exc.code, parsed)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            pass
        raise ExtractionError(f"Genome Browser API request failed: {detail}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ExtractionError(f"Genome Browser API is unavailable: {exc}") from exc
    if len(body) > MAX_API_RESPONSE_BYTES:
        raise ExtractionError("Genome Browser API response exceeds the safety limit")
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExtractionError("Genome Browser API returned invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise ExtractionError("Genome Browser API returned a non-object response")
    return parsed


def api_assemblies(args: argparse.Namespace) -> List[Dict[str, object]]:
    payload = api_request_json(args, "/api/genome-browser/assemblies")
    assemblies = payload.get("assemblies")
    if not isinstance(assemblies, list):
        raise ExtractionError("Genome Browser API response lacks assemblies")
    return [item for item in assemblies if isinstance(item, dict)]


def resolve_api_assembly(args: argparse.Namespace) -> Dict[str, object]:
    if not args.assembly:
        raise ExtractionError("API extraction requires --assembly")
    assemblies = api_assemblies(args)
    keys = ("id", "sample", "displayName")
    exact = [item for item in assemblies if any(str(item.get(key) or "") == args.assembly for key in keys)]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise ExtractionError(f"Ambiguous API assembly {args.assembly!r}: {[item.get('id') for item in exact]}")
    folded = args.assembly.casefold()
    insensitive = [
        item
        for item in assemblies
        if any(str(item.get(key) or "").casefold() == folded for key in keys)
    ]
    if len(insensitive) == 1:
        return insensitive[0]
    if len(insensitive) > 1:
        raise ExtractionError(
            f"Ambiguous case-insensitive API assembly {args.assembly!r}: {[item.get('id') for item in insensitive]}"
        )
    raise ExtractionError(f"Assembly not found in Genome Browser API: {args.assembly}")


def list_api_resources(args: argparse.Namespace) -> int:
    writer = csv.writer(sys.stdout, delimiter="\t", lineterminator="\n")
    writer.writerow(["source", "resource_id", "display_name", "species", "genome", "annotation"])
    for item in api_assemblies(args):
        writer.writerow(
            [
                "Genome_browser_API",
                item.get("id", ""),
                item.get("displayName", ""),
                item.get("species", ""),
                item.get("reference", ""),
                item.get("annotation", ""),
            ]
        )
    return 0


def open_text(path: Path):
    with path.open("rb") as fh:
        magic = fh.read(2)
    if magic == b"\x1f\x8b":
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open("r", encoding="utf-8", errors="replace")


def parse_attributes(text: str) -> Dict[str, str]:
    attrs: Dict[str, str] = {}
    for raw in text.strip().strip(";").split(";"):
        item = raw.strip()
        if not item:
            continue
        if "=" in item:
            key, value = item.split("=", 1)
        else:
            match = re.match(r"([^\s]+)\s+[\"']?(.*?)[\"']?$", item)
            if not match:
                continue
            key, value = match.group(1), match.group(2)
        attrs[key.strip()] = unquote(value.strip().strip("\"'"))
    return attrs


def iter_gff(path: Path) -> Iterator[Tuple[str, str, int, int, str, Optional[int], Dict[str, str]]]:
    with open_text(path) as fh:
        for line_no, line in enumerate(fh, 1):
            if not line.strip() or line.startswith("#"):
                continue
            cols = line.rstrip("\n").split("\t")
            if len(cols) != 9:
                continue
            try:
                start, end = int(cols[3]), int(cols[4])
            except ValueError:
                continue
            strand = cols[6] if cols[6] in {"+", "-"} else "+"
            phase = int(cols[7]) if cols[7] in {"0", "1", "2"} else None
            yield cols[0], cols[2], start, end, strand, phase, parse_attributes(cols[8])


def is_transcript_type(feature_type: str) -> bool:
    low = feature_type.lower()
    return low in TRANSCRIPT_TYPES or low.endswith("rna") or low.endswith("transcript")


def split_parents(attrs: Dict[str, str], prefer_transcript: bool = False) -> Tuple[str, ...]:
    text = attrs.get("Parent") or attrs.get("parent")
    if not text and prefer_transcript:
        text = attrs.get("transcript_id") or attrs.get("transcript")
    if not text:
        text = attrs.get("gene_id") or ""
    return tuple(x.strip() for x in text.split(",") if x.strip())


def feature_aliases(feature: Feature) -> Set[str]:
    aliases = {feature.feature_id}
    for key in ALIAS_KEYS:
        value = feature.attrs.get(key)
        if value:
            aliases.update(x.strip() for x in value.split(",") if x.strip())
    return aliases


def read_query_ids(id_files: Optional[Sequence[str]], inline_ids: Optional[Sequence[str]]) -> List[str]:
    values: List[str] = []
    if inline_ids:
        values.extend(x.strip() for x in inline_ids if x.strip())
    if id_files:
        for file_name in id_files:
            with open(file_name, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        values.append(line.split()[0])
    seen: Set[str] = set()
    result: List[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def read_potato_manifest(root: Path) -> List[Dict[str, str]]:
    manifest = root / "assemblies.tsv"
    if not manifest.is_file():
        return []
    with manifest.open("r", encoding="utf-8") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def resolve_manifest_entry(root: Path, name: str) -> Optional[Dict[str, str]]:
    rows = read_potato_manifest(root)
    keys = ("id", "sample", "display_name")
    exact = [row for row in rows if any(row.get(key) == name for key in keys)]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise ExtractionError(f"Ambiguous potato assembly {name!r}: {[row.get('id') for row in exact]}")
    folded = name.casefold()
    insensitive = [row for row in rows if any((row.get(key) or "").casefold() == folded for key in keys)]
    if len(insensitive) == 1:
        return insensitive[0]
    if len(insensitive) > 1:
        raise ExtractionError(f"Ambiguous case-insensitive potato assembly {name!r}: {[row.get('id') for row in insensitive]}")
    return None


def strip_compression_suffix(name: str) -> str:
    low = name.lower()
    for suffix in (".bgz", ".gz"):
        if low.endswith(suffix):
            return low[:-len(suffix)]
    return low


def is_fasta_name(path: Path) -> bool:
    low = strip_compression_suffix(path.name)
    return any(low.endswith(ext) for ext in (".fa", ".fasta", ".fna", ".fas"))


def is_annotation_name(path: Path) -> bool:
    low = strip_compression_suffix(path.name)
    return any(low.endswith(ext) for ext in (".gff3", ".gff", ".gtf"))


def genome_candidate_score(path: Path) -> int:
    low = path.name.lower()
    score = 0
    if Path(str(path) + ".fai").is_file():
        score += 20
    if "brief_id" in low or "brief-id" in low:
        score += 6
    if ".chrs." in low or "chromosome" in low:
        score += 4
    if any(token in low for token in ("cds", "cdna", "pep", "protein", "transcript", "repre", "gene.fa")):
        score -= 100
    return score


def annotation_candidate_score(path: Path) -> int:
    low = path.name.lower()
    score = 0
    if "brief_id" in low or "brief-id" in low:
        score += 6
    if "repre" in low or "representative" in low:
        score -= 10
    if "gene" in low:
        score += 1
    return score


def choose_unique_best(paths: Sequence[Path], score_fn, kind: str, directory: Path) -> Optional[Path]:
    if not paths:
        return None
    ranked = sorted(((score_fn(path), str(path), path) for path in paths), reverse=True)
    best_score = ranked[0][0]
    best = [item[2] for item in ranked if item[0] == best_score]
    if len(best) != 1:
        raise ExtractionError(
            f"Ambiguous {kind} files in {directory}: {[str(x) for x in best]}; set --{kind} explicitly"
        )
    return best[0]


def resolve_other_dir(other_root: Path, name: str) -> Optional[Path]:
    direct = other_root / name
    if direct.is_dir():
        return direct.resolve()
    if not other_root.is_dir():
        return None
    matches = [p for p in other_root.iterdir() if p.is_dir() and p.name.casefold() == name.casefold()]
    if len(matches) == 1:
        return matches[0].resolve()
    if len(matches) > 1:
        raise ExtractionError(f"Ambiguous other-species resource directory: {name}")
    return None


def discover_resource_dir(directory: Path) -> Tuple[Optional[Path], Optional[Path]]:
    files = [p for p in directory.rglob("*") if p.is_file()]
    genomes = [p for p in files if is_fasta_name(p) and genome_candidate_score(p) > -50]
    annotations = [p for p in files if is_annotation_name(p)]
    genome = choose_unique_best(genomes, genome_candidate_score, "genome", directory)
    annotation = choose_unique_best(annotations, annotation_candidate_score, "annotation", directory)
    return genome, annotation


def resolve_resources(args: argparse.Namespace) -> Dict[str, str]:
    potato_root = Path(args.potato_root).resolve()
    other_root = Path(args.other_root).resolve()
    canonical_entry: Optional[Dict[str, str]] = None
    resolved: Dict[str, str] = {
        "source": "explicit",
        "resource_id": "explicit",
        "genome": "",
        "annotation": "",
        "feature_index": "",
    }

    if args.assembly:
        entry = resolve_manifest_entry(potato_root, args.assembly)
        if entry:
            canonical_entry = entry
            resolved.update({
                "source": "Genome_browser_DB",
                "resource_id": entry.get("id", args.assembly),
                "sample": entry.get("sample", ""),
                "species": entry.get("species", ""),
                "doi": entry.get("doi", ""),
                "genome": str((potato_root / entry["reference"]).resolve()),
                "annotation": str((potato_root / entry["annotation"]).resolve()),
                "feature_index": str(local_feature_index_path(args, potato_root)),
            })
        else:
            directory = resolve_other_dir(other_root, args.assembly)
            if directory is None:
                raise ExtractionError(
                    f"Assembly/resource {args.assembly!r} not found in {potato_root / 'assemblies.tsv'} or {other_root}"
                )
            genome, annotation = discover_resource_dir(directory)
            resolved.update({
                "source": "Other_species_genomes",
                "resource_id": directory.name,
                "genome": str(genome or ""),
                "annotation": str(annotation or ""),
            })

    if args.resource_dir:
        directory = Path(args.resource_dir)
        if not directory.is_absolute():
            directory = other_root / directory
        directory = directory.resolve()
        if not directory.is_dir():
            raise ExtractionError(f"Resource directory not found: {directory}")
        genome, annotation = discover_resource_dir(directory)
        resolved.update({
            "source": "resource-dir",
            "resource_id": directory.name,
            "genome": str(genome or ""),
            "annotation": str(annotation or ""),
            "feature_index": "",
        })

    if args.genome:
        resolved["genome"] = str(Path(args.genome).resolve())
        if not args.feature_index:
            resolved["feature_index"] = ""
    if args.annotation:
        resolved["annotation"] = str(Path(args.annotation).resolve())
        if not args.feature_index:
            resolved["feature_index"] = ""
    if args.feature_index:
        resolved["feature_index"] = str(Path(args.feature_index).resolve())

    if not resolved["genome"]:
        raise ExtractionError("No genome resolved; set --assembly, --resource-dir, or --genome")
    genome_path = Path(resolved["genome"])
    if not genome_path.is_file() or genome_path.stat().st_size == 0:
        raise ExtractionError(f"Genome FASTA missing or empty: {genome_path}")
    if args.mode != "region":
        indexed = False
        if resolved["feature_index"] and resolved["resource_id"]:
            if resolved["source"] == "Genome_browser_DB" and canonical_entry:
                indexed = feature_index_assembly_is_current(
                    Path(resolved["feature_index"]),
                    root=potato_root,
                    entry=canonical_entry,
                )
            else:
                indexed = feature_index_has_assembly(
                    Path(resolved["feature_index"]), resolved["resource_id"]
                )
        if not indexed:
            if resolved["source"] == "Genome_browser_DB" and not args.annotation:
                raise ExtractionError(
                    "Canonical Potato feature extraction requires a readable local "
                    "feature index; use --source api for network extraction or provide "
                    "an explicit --annotation for generic local parsing"
                )
            if not resolved["annotation"]:
                raise ExtractionError(
                    "No local feature index or annotation resolved; set --feature-index or --annotation"
                )
            annotation_path = Path(resolved["annotation"])
            if not annotation_path.is_file() or annotation_path.stat().st_size == 0:
                raise ExtractionError(f"Annotation missing or empty: {annotation_path}")
    return resolved


def list_resources(args: argparse.Namespace) -> int:
    root = Path(args.potato_root).resolve()
    writer = csv.writer(sys.stdout, delimiter="\t", lineterminator="\n")
    writer.writerow(["source", "resource_id", "display_name", "species", "genome", "annotation"])
    for row in read_potato_manifest(root):
        writer.writerow([
            "Genome_browser_DB", row.get("id", ""), row.get("display_name", ""), row.get("species", ""),
            str(root / row.get("reference", "")), str(root / row.get("annotation", "")),
        ])
    other_root = Path(args.other_root).resolve()
    if other_root.is_dir():
        for directory in sorted((p for p in other_root.iterdir() if p.is_dir()), key=lambda x: x.name.casefold()):
            try:
                genome, annotation = discover_resource_dir(directory)
                writer.writerow(["Other_species_genomes", directory.name, directory.name, "", genome or "", annotation or ""])
            except ExtractionError as exc:
                writer.writerow(["Other_species_genomes", directory.name, directory.name, "", "AMBIGUOUS", str(exc)])
    return 0


class GenomeAccessor:
    def __init__(self, genome: Path, samtools: str, temp_root: Path):
        executable = shutil.which(samtools) if os.path.sep not in samtools else samtools
        if not executable or not Path(executable).exists():
            raise ExtractionError(f"samtools not found: {samtools}")
        self.samtools = str(executable)
        self.original = genome.resolve()
        self.path = self.original
        self.index_mode = "existing"
        self.fai = Path(str(self.path) + ".fai")
        if not self.fai.is_file():
            self.index_mode = "temporary"
            suffix = "".join(self.original.suffixes[-2:]) if self.original.suffix in {".gz", ".bgz"} else self.original.suffix
            linked = temp_root / ("genome" + (suffix or ".fa"))
            linked.symlink_to(self.original)
            self.path = linked
            cmd = [self.samtools, "faidx", str(self.path)]
            result = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if result.returncode != 0:
                raise ExtractionError(f"Failed to create temporary FASTA index: {result.stderr.strip()}")
            self.fai = Path(str(self.path) + ".fai")
        self.lengths = self._read_fai()

    def _read_fai(self) -> Dict[str, int]:
        lengths: Dict[str, int] = {}
        with self.fai.open("r", encoding="utf-8") as fh:
            for line in fh:
                cols = line.rstrip("\n").split("\t")
                if len(cols) >= 2:
                    lengths[cols[0]] = int(cols[1])
        if not lengths:
            raise ExtractionError(f"Empty FASTA index: {self.fai}")
        return lengths

    def fetch_many(self, intervals: Sequence[Tuple[str, int, int]], temp_root: Path) -> Dict[Tuple[str, int, int], str]:
        unique: List[Tuple[str, int, int]] = []
        seen: Set[Tuple[str, int, int]] = set()
        for interval in intervals:
            if interval not in seen:
                seen.add(interval)
                unique.append(interval)
        if not unique:
            return {}
        regions_file = temp_root / "regions.txt"
        with regions_file.open("w", encoding="utf-8") as out:
            for seqid, start, end in unique:
                out.write(f"{seqid}:{start}-{end}\n")
        result = subprocess.run(
            [self.samtools, "faidx", str(self.path), "-r", str(regions_file)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if result.returncode != 0:
            raise ExtractionError(f"samtools faidx extraction failed: {result.stderr.strip()}")
        sequences = [seq for _header, seq in iter_fasta_text(result.stdout)]
        if len(sequences) != len(unique):
            raise ExtractionError(f"samtools returned {len(sequences)} records for {len(unique)} intervals")
        fetched = dict(zip(unique, sequences))
        for interval, seq in fetched.items():
            expected = interval[2] - interval[1] + 1
            if len(seq) != expected:
                raise ExtractionError(f"Length mismatch for {interval}: expected {expected}, got {len(seq)}")
        return fetched


def iter_fasta_text(text: str) -> Iterator[Tuple[str, str]]:
    header: Optional[str] = None
    parts: List[str] = []
    for line in text.splitlines():
        if line.startswith(">"):
            if header is not None:
                yield header, "".join(parts)
            header = line[1:].strip()
            parts = []
        elif line.strip():
            parts.append(line.strip())
    if header is not None:
        yield header, "".join(parts)


def load_annotation_index(path: Path):
    genes: Dict[str, Feature] = {}
    transcripts: Dict[str, Feature] = {}
    gene_aliases: Dict[str, Set[str]] = defaultdict(set)
    transcript_aliases: Dict[str, Set[str]] = defaultdict(set)
    children: Dict[str, Set[str]] = defaultdict(set)

    for seqid, feature_type, start, end, strand, _phase, attrs in iter_gff(path):
        low = feature_type.lower()
        if low != "gene" and not is_transcript_type(feature_type):
            continue
        feature_id = attrs.get("ID") or attrs.get("transcript_id") or attrs.get("gene_id")
        if not feature_id:
            continue
        feature = Feature(feature_id, seqid, start, end, strand, feature_type, split_parents(attrs), attrs)
        if low == "gene":
            if feature_id in genes:
                raise ExtractionError(f"Duplicate gene ID in annotation: {feature_id}")
            genes[feature_id] = feature
            for alias in feature_aliases(feature):
                gene_aliases[alias].add(feature_id)
        else:
            if feature_id in transcripts:
                raise ExtractionError(f"Duplicate transcript ID in annotation: {feature_id}")
            transcripts[feature_id] = feature
            for alias in feature_aliases(feature):
                transcript_aliases[alias].add(feature_id)
            for parent in feature.parents:
                children[parent].add(feature_id)
    return genes, transcripts, gene_aliases, transcript_aliases, children


def resolve_feature_id(query: str, genes, transcripts, gene_aliases, transcript_aliases) -> Tuple[str, str]:
    if query in genes:
        return "gene", query
    if query in transcripts:
        return "transcript", query
    candidates: Set[Tuple[str, str]] = set()
    candidates.update(("gene", x) for x in gene_aliases.get(query, set()))
    candidates.update(("transcript", x) for x in transcript_aliases.get(query, set()))
    if not candidates:
        raise ExtractionError("ID not found in gene/transcript features")
    if len(candidates) > 1:
        raise ExtractionError(f"Ambiguous annotation alias: {sorted(candidates)}")
    return next(iter(candidates))


def child_transcripts(gene_id: str, transcripts: Dict[str, Feature], children: Dict[str, Set[str]]) -> List[str]:
    direct = set(children.get(gene_id, set()))
    if not direct:
        for transcript_id, transcript in transcripts.items():
            if gene_id in transcript.parents or transcript.attrs.get("gene_id") == gene_id:
                direct.add(transcript_id)
    return sorted(direct)


def load_parts(path: Path, transcript_ids: Set[str]) -> Tuple[Dict[str, List[Segment]], Dict[str, List[Segment]]]:
    exons: Dict[str, List[Segment]] = defaultdict(list)
    cdss: Dict[str, List[Segment]] = defaultdict(list)
    for seqid, feature_type, start, end, strand, phase, attrs in iter_gff(path):
        low = feature_type.lower()
        if low not in {"exon", "cds"}:
            continue
        parents = split_parents(attrs, prefer_transcript=True)
        for parent in parents:
            if parent not in transcript_ids:
                continue
            segment = Segment(seqid, start, end, strand, phase if low == "cds" else None)
            (cdss if low == "cds" else exons)[parent].append(segment)
    return exons, cdss


def ordered_segments(segments: Sequence[Segment], strand: str) -> List[Segment]:
    return sorted(segments, key=lambda x: (x.start, x.end), reverse=(strand == "-"))


def parts_length(parts: Sequence[Segment]) -> int:
    return sum(part.end - part.start + 1 for part in parts)


def choose_transcripts(
    transcript_ids: Sequence[str], policy: str, mode: str, transcripts: Dict[str, Feature],
    exons: Dict[str, List[Segment]], cdss: Dict[str, List[Segment]],
) -> List[str]:
    ids = sorted(set(transcript_ids))
    if not ids:
        raise ExtractionError("Gene has no child transcript")
    if len(ids) == 1:
        return ids
    if policy == "error":
        raise ExtractionError(
            f"Gene has {len(ids)} transcripts; set --isoform representative, longest, or all"
        )
    if policy == "all":
        return ids

    def score(transcript_id: str):
        transcript = transcripts[transcript_id]
        if mode in {"cds", "protein", "promoter"}:
            primary = parts_length(cdss.get(transcript_id, []))
        elif mode == "transcript":
            primary = parts_length(exons.get(transcript_id, []))
        else:
            primary = transcript.end - transcript.start + 1
        return (-primary, transcript_id)

    if policy in {"longest", "representative"}:
        return [sorted(ids, key=score)[0]]
    raise ExtractionError(f"Unsupported isoform policy: {policy}")


def parent_gene_for_transcript(transcript: Feature, genes: Dict[str, Feature]) -> Optional[str]:
    for parent in transcript.parents:
        if parent in genes:
            return parent
    gene_id = transcript.attrs.get("gene_id")
    return gene_id if gene_id in genes else None


def read_region_plans(args: argparse.Namespace) -> List[RecordPlan]:
    plans: List[RecordPlan] = []
    if args.regions:
        with open(args.regions, "r", encoding="utf-8") as fh:
            reader = csv.DictReader(fh, delimiter="\t")
            required = {"name", "seqid", "start", "end"}
            if reader.fieldnames is None or not required.issubset(reader.fieldnames):
                raise ExtractionError(f"Regions TSV requires columns: {sorted(required)}")
            for line_no, row in enumerate(reader, 2):
                try:
                    start, end = int(row["start"]), int(row["end"])
                except (TypeError, ValueError):
                    raise ExtractionError(f"Invalid start/end in regions TSV line {line_no}")
                strand = (row.get("strand") or "+").strip()
                if strand not in {"+", "-"}:
                    raise ExtractionError(f"Invalid strand in regions TSV line {line_no}: {strand}")
                name = (row.get("name") or "").strip()
                seqid = (row.get("seqid") or "").strip()
                plans.append(RecordPlan(name, name, "region", "", seqid, strand, [Segment(seqid, start, end, strand)]))
    else:
        name = args.name or f"{args.seqid}:{args.start}-{args.end}:{args.strand}"
        plans.append(RecordPlan(name, name, "region", "", args.seqid, args.strand,
                                [Segment(args.seqid, args.start, args.end, args.strand)]))
    return plans


def build_feature_plans(args: argparse.Namespace, annotation: Path, queries: Sequence[str]) -> List[RecordPlan]:
    genes, transcripts, gene_aliases, transcript_aliases, children = load_annotation_index(annotation)
    resolutions: Dict[str, Tuple[str, str]] = {}
    errors: Dict[str, str] = {}
    candidate_transcripts: Set[str] = set()

    for query in queries:
        try:
            kind, feature_id = resolve_feature_id(query, genes, transcripts, gene_aliases, transcript_aliases)
            resolutions[query] = (kind, feature_id)
            if kind == "transcript":
                candidate_transcripts.add(feature_id)
            elif args.mode in {"promoter", "cds", "transcript", "protein"}:
                candidate_transcripts.update(child_transcripts(feature_id, transcripts, children))
        except ExtractionError as exc:
            errors[query] = str(exc)

    exons: Dict[str, List[Segment]] = defaultdict(list)
    cdss: Dict[str, List[Segment]] = defaultdict(list)
    if candidate_transcripts:
        exons, cdss = load_parts(annotation, candidate_transcripts)

    plans: List[RecordPlan] = []
    for query in queries:
        if query in errors:
            plans.append(RecordPlan(query_id=query, status="FAILED", message=errors[query]))
            continue
        kind, feature_id = resolutions[query]
        feature = genes[feature_id] if kind == "gene" else transcripts[feature_id]

        if args.mode in {"gene", "gene-window"}:
            if kind == "transcript":
                parent = parent_gene_for_transcript(feature, genes)
                if parent is None:
                    plans.append(RecordPlan(query_id=query, resolved_id=feature_id, resolved_type=kind,
                                            status="FAILED", message="Transcript has no resolvable parent gene"))
                    continue
                feature = genes[parent]
                kind, feature_id = "gene", parent
            start, end = feature.start, feature.end
            if args.mode == "gene-window":
                if feature.strand == "+":
                    start -= args.upstream
                    end += args.downstream
                else:
                    start -= args.downstream
                    end += args.upstream
            plans.append(RecordPlan(query, feature_id, kind, "", feature.seqid, feature.strand,
                                    [Segment(feature.seqid, start, end, feature.strand)]))
            continue

        transcript_ids = [feature_id] if kind == "transcript" else child_transcripts(feature_id, transcripts, children)
        try:
            selected = choose_transcripts(transcript_ids, args.isoform, args.mode, transcripts, exons, cdss)
        except ExtractionError as exc:
            plans.append(RecordPlan(query, feature_id, kind, status="FAILED", message=str(exc)))
            continue

        representative_source = ""
        if kind == "gene" and args.isoform == "representative":
            if len(set(transcript_ids)) == 1:
                representative_source = "sole_transcript"
            elif args.mode == "transcript":
                representative_source = "longest_exon_fallback"
            else:
                representative_source = "longest_cds_fallback"

        for transcript_id in selected:
            transcript = transcripts[transcript_id]
            parts: List[Segment]
            if args.mode == "transcript":
                parts = ordered_segments(exons.get(transcript_id, []), transcript.strand)
                if not parts:
                    plans.append(RecordPlan(query, feature_id, kind, transcript_id, transcript.seqid, transcript.strand,
                                            status="FAILED", message="Transcript has no exon features"))
                    continue
            elif args.mode in {"cds", "protein"}:
                parts = ordered_segments(cdss.get(transcript_id, []), transcript.strand)
                if not parts:
                    plans.append(RecordPlan(query, feature_id, kind, transcript_id, transcript.seqid, transcript.strand,
                                            status="FAILED", message="Transcript has no CDS features"))
                    continue
            else:  # promoter
                cds_parts = ordered_segments(cdss.get(transcript_id, []), transcript.strand)
                if not cds_parts:
                    plans.append(RecordPlan(query, feature_id, kind, transcript_id, transcript.seqid, transcript.strand,
                                            status="FAILED", message="Promoter extraction requires CDS features"))
                    continue
                anchor = cds_parts[0].start if transcript.strand == "+" else cds_parts[0].end
                if transcript.strand == "+":
                    start, end = anchor - args.length, anchor - 1
                else:
                    start, end = anchor + 1, anchor + args.length
                parts = [Segment(transcript.seqid, start, end, transcript.strand)]
            plans.append(
                RecordPlan(
                    query,
                    feature_id,
                    kind,
                    transcript_id,
                    transcript.seqid,
                    transcript.strand,
                    parts,
                    representative_source=representative_source,
                )
            )
    return plans


def _api_segment(item: object, transcript_strand: str) -> Segment:
    if not isinstance(item, dict):
        raise ExtractionError("Feature API returned an invalid segment")
    try:
        seqid = str(item["refName"])
        start = int(item["start"])
        end = int(item["end"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ExtractionError("Feature API segment lacks valid coordinates") from exc
    phase_value = item.get("phase")
    phase = None if phase_value is None else int(phase_value)
    item_strand = item.get("strand")
    if item_strand is not None and item_strand != transcript_strand:
        raise ExtractionError("Feature API segment strand is inconsistent with its transcript")
    if not seqid or start < 1 or end < start or phase not in {None, 0, 1, 2}:
        raise ExtractionError("Feature API returned an invalid segment interval or phase")
    return Segment(seqid, start, end, transcript_strand, phase)


def _select_api_transcripts(
    args: argparse.Namespace,
    payload: Dict[str, object],
) -> List[Dict[str, object]]:
    raw = payload.get("transcripts")
    if not isinstance(raw, list):
        raise ExtractionError("Feature API response lacks transcripts")
    transcripts = [item for item in raw if isinstance(item, dict)]
    query = payload.get("query")
    resolved_type = str(query.get("resolvedType") or "") if isinstance(query, dict) else ""
    if resolved_type == "transcript":
        if len(transcripts) != 1:
            raise ExtractionError("Feature API transcript resolution is inconsistent")
        return transcripts
    if not transcripts:
        raise ExtractionError("Gene has no child transcript")
    if len(transcripts) == 1:
        return transcripts
    if args.isoform == "error":
        raise ExtractionError(
            f"Gene has {len(transcripts)} transcripts; set --isoform representative, longest, or all"
        )
    if args.isoform == "all":
        return sorted(transcripts, key=lambda item: str(item.get("id") or ""))
    if args.isoform == "representative":
        representatives = [item for item in transcripts if item.get("isRepresentative") is True]
        if len(representatives) != 1:
            raise ExtractionError("Gene does not have exactly one indexed representative transcript")
        return representatives
    length_key = "exonLength" if args.mode == "transcript" else "cdsLength"
    return [
        min(
            transcripts,
            key=lambda item: (-int(item.get(length_key) or 0), str(item.get("id") or "")),
        )
    ]


def build_api_plans(
    args: argparse.Namespace,
    *,
    assembly_id: str,
    queries: Sequence[str],
    resolve_payload: Optional[Callable[[str], Dict[str, object]]] = None,
) -> List[RecordPlan]:
    plans: List[RecordPlan] = []
    for query_id in queries:
        try:
            payload = (
                resolve_payload(query_id)
                if resolve_payload is not None
                else api_request_json(
                    args,
                    "/api/genome-browser/features/resolve",
                    query={"assembly": assembly_id, "id": query_id},
                )
            )
            query_payload = payload.get("query")
            gene = payload.get("gene")
            if not isinstance(query_payload, dict) or not isinstance(gene, dict):
                raise ExtractionError("Feature API response is incomplete")
            if str(payload.get("assembly") or assembly_id) != assembly_id:
                raise ExtractionError("Feature API returned a different assembly")
            if str(query_payload.get("id") or "") != query_id:
                raise ExtractionError("Feature API returned a different query ID")
            resolved_type = str(query_payload.get("resolvedType") or "")
            resolved_id = str(query_payload.get("resolvedId") or "")
            gene_id = str(gene.get("id") or "")
            gene_seqid = str(gene.get("refName") or "")
            gene_strand = str(gene.get("strand") or "")
            if resolved_type not in {"gene", "transcript"} or not resolved_id:
                raise ExtractionError("Feature API returned an invalid resolution")
            if not gene_id or not gene_seqid or gene_strand not in {"+", "-"}:
                raise ExtractionError("Resolved gene does not have a usable strand")

            if args.mode in {"gene", "gene-window"}:
                start, end = int(gene["start"]), int(gene["end"])
                if start < 1 or end < start:
                    raise ExtractionError("Resolved gene has invalid coordinates")
                if args.mode == "gene-window":
                    if gene_strand == "+":
                        start -= args.upstream
                        end += args.downstream
                    else:
                        start -= args.downstream
                        end += args.upstream
                plans.append(
                    RecordPlan(
                        query_id=query_id,
                        resolved_id=gene_id,
                        resolved_type="gene",
                        seqid=gene_seqid,
                        strand=gene_strand,
                        segments=[Segment(gene_seqid, start, end, gene_strand)],
                    )
                )
                continue

            selected = _select_api_transcripts(args, payload)
            for transcript in selected:
                transcript_id = str(transcript.get("id") or "")
                transcript_seqid = str(transcript.get("refName") or "")
                transcript_strand = str(transcript.get("strand") or "")
                if not transcript_id or not transcript_seqid or transcript_strand not in {"+", "-"}:
                    raise ExtractionError("Feature API returned an invalid transcript")
                representative_source = str(transcript.get("representativeSource") or "")
                if args.mode == "transcript":
                    raw_segments = transcript.get("exons")
                    if not isinstance(raw_segments, list) or not raw_segments:
                        raise ExtractionError(f"Transcript has no exon features: {transcript_id}")
                    segments = ordered_segments(
                        [_api_segment(item, transcript_strand) for item in raw_segments],
                        transcript_strand,
                    )
                elif args.mode in {"cds", "protein"}:
                    raw_segments = transcript.get("cds")
                    if not isinstance(raw_segments, list) or not raw_segments:
                        raise ExtractionError(f"Transcript has no CDS features: {transcript_id}")
                    segments = ordered_segments(
                        [_api_segment(item, transcript_strand) for item in raw_segments],
                        transcript_strand,
                    )
                else:
                    raw_segments = transcript.get("cds")
                    if not isinstance(raw_segments, list) or not raw_segments:
                        raise ExtractionError(f"Promoter extraction requires CDS features: {transcript_id}")
                    anchor_value = transcript.get("cdsFivePrimePosition")
                    if anchor_value is None:
                        anchor_value = transcript.get("codingStart")
                    if anchor_value is None:
                        raise ExtractionError(f"Transcript lacks a CDS 5-prime position: {transcript_id}")
                    anchor = int(anchor_value)
                    if anchor < 1:
                        raise ExtractionError(f"Transcript has an invalid CDS 5-prime position: {transcript_id}")
                    if transcript_strand == "+":
                        start, end = anchor - args.length, anchor - 1
                    else:
                        start, end = anchor + 1, anchor + args.length
                    segments = [Segment(transcript_seqid, start, end, transcript_strand)]
                plans.append(
                    RecordPlan(
                        query_id=query_id,
                        resolved_id=resolved_id,
                        resolved_type=resolved_type,
                        transcript_id=transcript_id,
                        seqid=transcript_seqid,
                        strand=transcript_strand,
                        segments=segments,
                        representative_source=representative_source,
                    )
                )
        except (ExtractionError, KeyError, TypeError, ValueError) as exc:
            plans.append(RecordPlan(query_id=query_id, status="FAILED", message=str(exc)))
    return plans


def extract_api_sequences(
    args: argparse.Namespace,
    *,
    assembly_id: str,
    plans: Sequence[RecordPlan],
) -> None:
    for plan_index, plan in enumerate(plans):
        if plan.status == "FAILED":
            continue
        if not plan.segments or any(segment.end < segment.start for segment in plan.segments):
            plan.status = "FAILED"
            plan.message = "sequence plan contains no segments or invalid coordinates"
            continue
        plan.requested_length = parts_length(plan.segments)
        if plan.requested_length > MAX_API_SEQUENCE_BP:
            plan.status = "FAILED"
            plan.message = f"requested sequence exceeds API limit of {MAX_API_SEQUENCE_BP} bp"
            continue
        request_segments = [
            {
                "name": f"segment-{plan_index}-{segment_index}",
                "refName": segment.seqid,
                "start": segment.start,
                "end": segment.end,
                "strand": "+",
            }
            for segment_index, segment in enumerate(plan.segments)
        ]
        try:
            fetched: Dict[Tuple[str, int, int], str] = {}
            adjusted: List[Segment] = []
            for chunk_start in range(0, len(request_segments), MAX_API_SEGMENTS):
                request_chunk = request_segments[
                    chunk_start : chunk_start + MAX_API_SEGMENTS
                ]
                original_chunk = plan.segments[
                    chunk_start : chunk_start + MAX_API_SEGMENTS
                ]
                payload = api_request_json(
                    args,
                    "/api/genome-browser/sequences",
                    payload={
                        "assembly": assembly_id,
                        "clip": bool(
                            args.clip
                            and args.mode
                            in {"region", "promoter", "gene-window"}
                        ),
                        "segments": request_chunk,
                    },
                )
                if str(payload.get("assembly") or assembly_id) != assembly_id:
                    raise ExtractionError("Sequence API returned a different assembly")
                coordinate_system = payload.get("coordinateSystem")
                if coordinate_system is not None and coordinate_system != "1-based-inclusive":
                    raise ExtractionError("Sequence API returned an unsupported coordinate system")
                records = payload.get("records")
                if not isinstance(records, list) or len(records) != len(
                    request_chunk
                ):
                    raise ExtractionError("Sequence API returned an unexpected record count")
                for original, requested, record in zip(
                    original_chunk, request_chunk, records
                ):
                    if not isinstance(record, dict):
                        raise ExtractionError("Sequence API returned an invalid record")
                    if record.get("name") != requested["name"]:
                        raise ExtractionError("Sequence API returned records out of order")
                    if record.get("refName") != original.seqid:
                        raise ExtractionError("Sequence API returned a different reference sequence")
                    if record.get("requestedStart") != original.start or record.get("requestedEnd") != original.end:
                        raise ExtractionError("Sequence API returned different requested coordinates")
                    if record.get("strand") != "+":
                        raise ExtractionError("Sequence API did not return the requested forward strand")
                    start, end = int(record["start"]), int(record["end"])
                    if start < 1 or start < original.start or end > original.end or start > end:
                        raise ExtractionError("Sequence API returned invalid actual coordinates")
                    clipped = record.get("clipped")
                    if not isinstance(clipped, bool):
                        raise ExtractionError("Sequence API returned an invalid clipping flag")
                    if not clipped and (start != original.start or end != original.end):
                        raise ExtractionError("Sequence API changed coordinates without reporting clipping")
                    sequence = record.get("sequence")
                    if not isinstance(sequence, str):
                        raise ExtractionError("Sequence API returned an invalid sequence")
                    if record.get("length") != len(sequence) or len(sequence) != end - start + 1:
                        raise ExtractionError("Sequence API returned an invalid sequence length")
                    adjusted_segment = Segment(
                        original.seqid,
                        start,
                        end,
                        original.strand,
                        original.phase,
                    )
                    adjusted.append(adjusted_segment)
                    fetched[(original.seqid, start, end)] = sequence
                    plan.clipped = plan.clipped or clipped
            plan.segments = adjusted
            plan.status = "CLIPPED" if plan.clipped else "OK"
            assemble_sequences([plan], fetched, args.mode)
        except (ExtractionError, KeyError, TypeError, ValueError) as exc:
            plan.status = "FAILED"
            plan.message = str(exc)


def validate_plans(plans: Sequence[RecordPlan], genome: GenomeAccessor, clip: bool) -> None:
    for plan in plans:
        if plan.status == "FAILED":
            continue
        adjusted: List[Segment] = []
        plan.requested_length = parts_length(plan.segments)
        for segment in plan.segments:
            chrom_len = genome.lengths.get(segment.seqid)
            if chrom_len is None:
                plan.status = "FAILED"
                plan.message = f"seqid not found in genome index: {segment.seqid}"
                break
            if segment.start > segment.end:
                plan.status = "FAILED"
                plan.message = f"invalid coordinates: {segment.seqid}:{segment.start}-{segment.end}"
                break
            start, end = segment.start, segment.end
            if start < 1 or end > chrom_len:
                if not clip:
                    plan.status = "FAILED"
                    plan.message = f"out of bounds: {segment.seqid}:{start}-{end}, seqid length={chrom_len}; use --clip"
                    break
                start, end = max(1, start), min(chrom_len, end)
                if start > end:
                    plan.status = "FAILED"
                    plan.message = f"empty after clipping: {segment.seqid}:{segment.start}-{segment.end}"
                    break
                plan.clipped = True
            adjusted.append(Segment(segment.seqid, start, end, segment.strand, segment.phase))
        if plan.status != "FAILED":
            plan.segments = adjusted
            plan.status = "CLIPPED" if plan.clipped else "OK"


def revcomp(sequence: str) -> str:
    return sequence.translate(RC_TABLE)[::-1]


def translate(sequence: str) -> str:
    upper = sequence.upper().replace("U", "T")
    return "".join(CODON_TABLE.get(upper[i:i+3], "X") for i in range(0, len(upper) - 2, 3))


def assemble_sequences(plans: Sequence[RecordPlan], fetched: Dict[Tuple[str, int, int], str], mode: str) -> None:
    for plan in plans:
        if plan.status not in {"OK", "CLIPPED"}:
            continue
        pieces: List[str] = []
        for segment in plan.segments:
            sequence = fetched[(segment.seqid, segment.start, segment.end)]
            if plan.strand == "-":
                sequence = revcomp(sequence)
            pieces.append(sequence)
        nucleotide = "".join(pieces)
        plan.invalid_chars = sum(1 for char in nucleotide if char not in VALID_NT)
        if mode == "protein":
            first_phase = plan.segments[0].phase or 0
            plan.phase_trim = first_phase
            coding = nucleotide[first_phase:]
            plan.trailing_trim = len(coding) % 3
            if plan.trailing_trim:
                coding = coding[:-plan.trailing_trim]
            plan.sequence = translate(coding)
            plan.internal_stops = plan.sequence[:-1].count("*") if plan.sequence else 0
        else:
            plan.sequence = nucleotide
        plan.actual_length = len(plan.sequence)
        if not plan.sequence:
            plan.status = "FAILED"
            plan.message = "extraction produced an empty sequence"


def wrap_sequence(sequence: str, width: int) -> str:
    if width <= 0:
        return sequence
    return "\n".join(sequence[i:i+width] for i in range(0, len(sequence), width))


def segment_text(segments: Sequence[Segment]) -> str:
    return ",".join(f"{x.seqid}:{x.start}-{x.end}" for x in segments)


def safe_header_value(value: object) -> str:
    return re.sub(r"\s+", "_", str(value))


def write_outputs(args: argparse.Namespace, plans: Sequence[RecordPlan], resources: Dict[str, str], genome: object) -> Tuple[int, int]:
    output = Path(args.output)
    report = Path(args.report)
    missing = Path(args.missing) if args.missing else Path(str(output) + ".missing.txt")
    metadata = Path(args.metadata) if args.metadata else Path(str(output) + ".meta.json")
    for path in (output, report, missing, metadata):
        path.parent.mkdir(parents=True, exist_ok=True)

    successes = [p for p in plans if p.status in {"OK", "CLIPPED"} and p.sequence]
    failures = [p for p in plans if p.status == "FAILED" or not p.sequence]

    with output.open("w", encoding="utf-8") as out:
        for plan in successes:
            name = plan.query_id
            if plan.transcript_id:
                name += f"|{plan.transcript_id}"
            header = (
                f"{safe_header_value(name)} mode={args.mode} resource={safe_header_value(resources.get('resource_id', ''))} "
                f"resolved={safe_header_value(plan.resolved_id)} type={plan.resolved_type} "
                f"seqid={safe_header_value(plan.seqid)} strand={plan.strand} length={plan.actual_length} status={plan.status}"
            )
            out.write(f">{header}\n{wrap_sequence(plan.sequence, args.wrap)}\n")

    fields = [
        "query_id", "mode", "resource_id", "resource_source", "extraction_backend", "genome", "annotation",
        "resolved_id", "resolved_type", "transcript_id", "seqid", "strand", "segments",
        "requested_length", "actual_length", "status", "message", "isoform_policy", "representative_source",
        "clipped", "phase_trim", "trailing_trim", "internal_stops", "invalid_chars",
    ]
    with report.open("w", encoding="utf-8", newline="") as rep:
        writer = csv.DictWriter(rep, delimiter="\t", fieldnames=fields)
        writer.writeheader()
        for plan in plans:
            writer.writerow({
                "query_id": plan.query_id, "mode": args.mode,
                "resource_id": resources.get("resource_id", ""), "resource_source": resources.get("source", ""),
                "extraction_backend": resources.get("extraction_backend", "local"),
                "genome": resources.get("genome", ""), "annotation": resources.get("annotation", ""),
                "resolved_id": plan.resolved_id, "resolved_type": plan.resolved_type,
                "transcript_id": plan.transcript_id, "seqid": plan.seqid, "strand": plan.strand,
                "segments": segment_text(plan.segments), "requested_length": plan.requested_length,
                "actual_length": plan.actual_length, "status": plan.status, "message": plan.message,
                "isoform_policy": args.isoform, "representative_source": plan.representative_source,
                "clipped": str(plan.clipped).lower(), "phase_trim": plan.phase_trim,
                "trailing_trim": plan.trailing_trim, "internal_stops": plan.internal_stops,
                "invalid_chars": plan.invalid_chars,
            })

    failed_queries: List[str] = []
    seen_failed: Set[str] = set()
    for plan in failures:
        if plan.query_id not in seen_failed:
            failed_queries.append(plan.query_id)
            seen_failed.add(plan.query_id)
    with missing.open("w", encoding="utf-8") as out:
        for query in failed_queries:
            out.write(query + "\n")

    metadata_payload = {
        "script": Path(__file__).name,
        "version": VERSION,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "argv": sys.argv,
        "mode": args.mode,
        "resources": resources,
        "extraction_backend": resources.get("extraction_backend", "local"),
        "genome_index_mode": getattr(genome, "index_mode", "unknown"),
        "outputs": {"fasta": str(output.resolve()), "report": str(report.resolve()),
                    "missing": str(missing.resolve()), "metadata": str(metadata.resolve())},
        "counts": {"plans": len(plans), "sequences": len(successes), "failed_records": len(failures),
                   "failed_queries": len(failed_queries)},
    }
    with metadata.open("w", encoding="utf-8") as out:
        json.dump(metadata_payload, out, ensure_ascii=False, indent=2)
        out.write("\n")
    return len(successes), len(failed_queries)


def main() -> int:
    args = parse_args()
    try:
        source = effective_source(args)
        if args.mode == "list-resources":
            return list_api_resources(args) if source == "api" else list_resources(args)
        queries = [] if args.mode == "region" else read_query_ids(args.ids, args.inline_ids)
        if source == "api":
            assembly = resolve_api_assembly(args)
            assembly_id = str(assembly.get("id") or "")
            if not assembly_id:
                raise ExtractionError("Genome Browser API assembly does not have an id")
            resources = {
                "source": "Genome_browser_API",
                "extraction_backend": "api",
                "resource_id": assembly_id,
                "sample": str(assembly.get("sample") or ""),
                "species": str(assembly.get("species") or ""),
                "genome": str(assembly.get("reference") or ""),
                "annotation": str(assembly.get("annotation") or ""),
                "api_base_url": args.api_base_url.rstrip("/"),
            }
            if args.mode == "region":
                plans = read_region_plans(args)
            else:
                plans = build_api_plans(args, assembly_id=assembly_id, queries=queries)
            extract_api_sequences(args, assembly_id=assembly_id, plans=plans)
            genome = SimpleNamespace(index_mode="remote_api")
            successes, failed_queries = write_outputs(args, plans, resources, genome)
        else:
            resources = resolve_resources(args)
            resources["extraction_backend"] = "local"
            with tempfile.TemporaryDirectory(prefix="sequence_extract_") as tmp:
                temp_root = Path(tmp)
                genome = GenomeAccessor(Path(resources["genome"]), args.samtools, temp_root)
                if args.mode == "region":
                    plans = read_region_plans(args)
                    resources["feature_resolution_backend"] = "coordinates"
                elif resources.get("feature_index") and feature_index_has_assembly(
                    Path(resources["feature_index"]), resources["resource_id"]
                ):
                    index_conn = connect_local_feature_index(
                        Path(resources["feature_index"])
                    )
                    try:
                        plans = build_api_plans(
                            args,
                            assembly_id=resources["resource_id"],
                            queries=queries,
                            resolve_payload=lambda query_id: resolve_local_feature(
                                index_conn,
                                assembly_id=resources["resource_id"],
                                query_id=query_id,
                            ),
                        )
                    finally:
                        index_conn.close()
                    resources["feature_resolution_backend"] = "local_feature_index"
                else:
                    plans = build_feature_plans(args, Path(resources["annotation"]), queries)
                    resources["feature_resolution_backend"] = "local_annotation"
                allow_clip = args.clip and args.mode in {"region", "promoter", "gene-window"}
                validate_plans(plans, genome, allow_clip)
                intervals = [
                    (segment.seqid, segment.start, segment.end)
                    for plan in plans if plan.status in {"OK", "CLIPPED"}
                    for segment in plan.segments
                ]
                fetched = genome.fetch_many(intervals, temp_root)
                assemble_sequences(plans, fetched, args.mode)
                successes, failed_queries = write_outputs(args, plans, resources, genome)
        eprint(f"mode={args.mode} queries={len(queries) if queries else len(plans)} sequences={successes} failed_queries={failed_queries} output={args.output}")
        return 0 if failed_queries == 0 else 2
    except (
        ExtractionError,
        OSError,
        ValueError,
        sqlite3.Error,
        subprocess.SubprocessError,
    ) as exc:
        eprint(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
