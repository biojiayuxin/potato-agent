#!/usr/bin/env python3
"""Extract query gene IDs and gene-to-transcript mappings from GFF3 files."""

from __future__ import annotations

import argparse
from pathlib import Path


def parse_attrs(attr_text: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for item in attr_text.rstrip(";").split(";"):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        attrs[key.strip()] = value.strip()
    return attrs


def split_ids(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def gff_has_gene_rows(path: Path, gene_type: str) -> bool:
    with path.open() as handle:
        for line in handle:
            if line.startswith("#") or not line.strip():
                continue
            fields = line.rstrip("\n").split("\t")
            if len(fields) >= 3 and fields[2].strip() == gene_type:
                return True
    return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gff", nargs="+", help="Input GFF3 file(s) for query genome.")
    parser.add_argument("--gene-ids-output", required=True)
    parser.add_argument("--gene-transcript-output", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--gene-type", default="gene")
    parser.add_argument("--transcript-types", default="mRNA,transcript")
    parser.add_argument("--gene-id-key", default="ID")
    parser.add_argument("--transcript-id-key", default="ID")
    parser.add_argument("--parent-key", default="Parent")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    transcript_types = {
        item.strip() for item in args.transcript_types.split(",") if item.strip()
    }

    gene_ids: set[str] = set()
    gene_to_transcripts: dict[str, list[str]] = {}
    has_gene_files = 0
    transcript_records = 0

    for raw_gff in args.gff:
        gff = Path(raw_gff)
        has_gene = gff_has_gene_rows(gff, args.gene_type)
        if has_gene:
            has_gene_files += 1

        with gff.open() as handle:
            for line in handle:
                if line.startswith("#") or not line.strip():
                    continue
                fields = line.rstrip("\n").split("\t")
                if len(fields) < 9:
                    continue
                feature_type = fields[2].strip()
                attrs = parse_attrs(fields[8])

                if has_gene and feature_type == args.gene_type:
                    gene_id = attrs.get(args.gene_id_key, "").strip()
                    if gene_id:
                        gene_ids.add(gene_id)
                    continue

                if feature_type not in transcript_types:
                    continue

                transcript_records += 1
                transcript_id = attrs.get(args.transcript_id_key, "").strip()
                parent_value = attrs.get(args.parent_key, "").strip()
                parent_ids = split_ids(parent_value) if parent_value else []
                if not parent_ids and transcript_id:
                    parent_ids = [transcript_id]

                for parent_gene in parent_ids:
                    if not parent_gene:
                        continue
                    if not has_gene:
                        gene_ids.add(parent_gene)
                    gene_to_transcripts.setdefault(parent_gene, [])
                    mapped_transcript = transcript_id or parent_gene
                    if mapped_transcript not in gene_to_transcripts[parent_gene]:
                        gene_to_transcripts[parent_gene].append(mapped_transcript)

    sorted_gene_ids = sorted(gene_ids)
    Path(args.gene_ids_output).write_text("\n".join(sorted_gene_ids) + "\n")

    gene_tx_lines = ["Gene\tTranscript"]
    for gene_id in sorted_gene_ids:
        transcripts = gene_to_transcripts.get(gene_id, [])
        if transcripts:
            for transcript in transcripts:
                gene_tx_lines.append(f"{gene_id}\t{transcript}")
        else:
            gene_tx_lines.append(f"{gene_id}\t{gene_id}")
    Path(args.gene_transcript_output).write_text("\n".join(gene_tx_lines) + "\n")

    Path(args.log).write_text(
        "\n".join(
            [
                f"gff_files={','.join(args.gff)}",
                f"gene_type={args.gene_type}",
                f"transcript_types={','.join(sorted(transcript_types))}",
                f"has_gene_files={has_gene_files}",
                f"gene_ids_extracted={len(sorted_gene_ids)}",
                f"transcript_records_seen={transcript_records}",
                f"gene_to_transcript_rows={len(gene_tx_lines) - 1}",
                f"gene_ids_output={args.gene_ids_output}",
                f"gene_to_transcript_output={args.gene_transcript_output}",
                "",
            ]
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
