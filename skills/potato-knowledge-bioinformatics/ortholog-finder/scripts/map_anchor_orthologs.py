#!/usr/bin/env python3
"""Create an anchor-first ortholog table with optional LAST fallback."""

from __future__ import annotations

import argparse
from pathlib import Path


def load_anchors(path: Path) -> list[tuple[str, str, float]]:
    anchors: list[tuple[str, str, float]] = []
    with path.open() as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            query = parts[0].strip()
            subject = parts[1].strip()
            try:
                score = float(parts[2].strip()) if len(parts) > 2 else 0.0
            except ValueError:
                score = 0.0
            anchors.append((query, subject, score))
    return anchors


def build_synteny_map(
    anchor_data: list[tuple[str, str, float]], label: str
) -> tuple[dict[str, tuple[str, str]], int, int]:
    round1: dict[str, tuple[str, float]] = {}
    for query, subject, score in anchor_data:
        if query not in round1 or score > round1[query][1]:
            round1[query] = (subject, score)

    round2: dict[str, tuple[str, float]] = {}
    for query, (subject, score) in round1.items():
        if subject not in round2 or score > round2[subject][1]:
            round2[subject] = (query, score)

    synteny_map: dict[str, tuple[str, str]] = {}
    for subject, (query, _score) in round2.items():
        synteny_map[query] = (subject, label)
    return synteny_map, len(round1), len(round2)


def build_fallback_map(
    path: Path, blocked_queries: set[str], min_identity: float
) -> tuple[dict[str, tuple[str, float]], int, int]:
    best: dict[str, tuple[float, str, float]] = {}
    total_rows = 0
    low_identity_rows = 0
    with path.open() as handle:
        for line in handle:
            if not line.strip():
                continue
            fields = line.rstrip("\n").split("\t")
            if len(fields) < 12:
                continue
            total_rows += 1
            query = fields[0]
            if query in blocked_queries:
                continue
            subject = fields[1]
            identity = float(fields[2])
            if identity < min_identity:
                low_identity_rows += 1
                continue
            bitscore = float(fields[11])
            if query not in best:
                best[query] = (bitscore, subject, identity)
                continue
            previous_bitscore, _previous_subject, _previous_identity = best[query]
            if bitscore > previous_bitscore:
                best[query] = (bitscore, subject, identity)

    fallback_map = {
        query: (subject, identity)
        for query, (_bitscore, subject, identity) in best.items()
    }
    return fallback_map, total_rows, low_identity_rows


def load_gene_to_transcripts(path: Path) -> dict[str, list[str]]:
    gene_to_transcripts: dict[str, list[str]] = {}
    with path.open() as handle:
        for line_number, line in enumerate(handle):
            if line_number == 0:
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 2:
                continue
            gene_id, transcript_id = parts[0].strip(), parts[1].strip()
            if not gene_id or not transcript_id:
                continue
            gene_to_transcripts.setdefault(gene_id, [])
            if transcript_id not in gene_to_transcripts[gene_id]:
                gene_to_transcripts[gene_id].append(transcript_id)
    return gene_to_transcripts


def lookup_gene(
    mapping: dict[str, tuple],
    gene_id: str,
    gene_to_transcripts: dict[str, list[str]],
    allow_containment_match: bool,
) -> tuple | None:
    candidates = [gene_id]
    for transcript_id in gene_to_transcripts.get(gene_id, []):
        if transcript_id not in candidates:
            candidates.append(transcript_id)

    for candidate in candidates:
        if candidate in mapping:
            return mapping[candidate]

    if not allow_containment_match:
        return None

    for candidate in candidates:
        for raw_query, value in mapping.items():
            if candidate and candidate in raw_query:
                return value
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gene-ids", required=True)
    parser.add_argument("--gene-transcripts", required=True)
    parser.add_argument("--anchors", required=True)
    parser.add_argument("--filtered", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--synteny-label", default="synteny")
    parser.add_argument("--fallback-label", default="")
    parser.add_argument(
        "--fallback-min-identity",
        type=float,
        default=90.0,
        help="Minimum percent identity required for .last.filtered fallback hits.",
    )
    fallback = parser.add_mutually_exclusive_group()
    fallback.add_argument("--fallback-enabled", action="store_true", dest="fallback_enabled")
    fallback.add_argument("--no-fallback", action="store_false", dest="fallback_enabled")
    parser.set_defaults(fallback_enabled=True)
    containment = parser.add_mutually_exclusive_group()
    containment.add_argument(
        "--allow-containment-match",
        action="store_true",
        dest="allow_containment_match",
    )
    containment.add_argument(
        "--no-containment-match",
        action="store_false",
        dest="allow_containment_match",
    )
    parser.set_defaults(allow_containment_match=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    anchor_data = load_anchors(Path(args.anchors))
    synteny_map, anchors_after_query, anchors_after_subject = build_synteny_map(
        anchor_data, args.synteny_label
    )

    if args.fallback_enabled:
        fallback_map, fallback_total_rows, fallback_low_identity_rows = build_fallback_map(
            Path(args.filtered),
            set(synteny_map),
            args.fallback_min_identity,
        )
    else:
        fallback_map = {}
        fallback_total_rows = 0
        fallback_low_identity_rows = 0

    gene_to_transcripts = load_gene_to_transcripts(Path(args.gene_transcripts))
    gene_ids = [
        line.strip()
        for line in Path(args.gene_ids).read_text().splitlines()
        if line.strip()
    ]

    output_lines = ["QueryGene\tOrthologGene\tSource"]
    synteny_count = 0
    fallback_count = 0
    unmapped_count = 0

    for gene_id in gene_ids:
        hit = lookup_gene(
            synteny_map,
            gene_id,
            gene_to_transcripts,
            args.allow_containment_match,
        )
        if hit is not None:
            ortholog_id, label = hit
            output_lines.append(f"{gene_id}\t{ortholog_id}\t{label}")
            synteny_count += 1
            continue

        hit = lookup_gene(
            fallback_map,
            gene_id,
            gene_to_transcripts,
            args.allow_containment_match,
        )
        if hit is not None:
            ortholog_id, identity = hit
            if args.fallback_label:
                label = args.fallback_label.format(identity=identity)
            else:
                label = f"(identity: {identity:.2f}%)"
            output_lines.append(f"{gene_id}\t{ortholog_id}\t{label}")
            fallback_count += 1
            continue

        output_lines.append(f"{gene_id}\t-\t-")
        unmapped_count += 1

    Path(args.output).write_text("\n".join(output_lines) + "\n")
    Path(args.log).write_text(
        "\n".join(
            [
                f"anchors_file={args.anchors}",
                f"filtered_file={args.filtered}",
                f"gene_ids_file={args.gene_ids}",
                f"gene_to_transcript_file={args.gene_transcripts}",
                f"output={args.output}",
                f"total_genes={len(gene_ids)}",
                f"anchors_raw_pairs={len(anchor_data)}",
                f"anchors_after_query_dedup={anchors_after_query}",
                f"anchors_after_subject_dedup={anchors_after_subject}",
                f"synteny_from_anchors={synteny_count}",
                f"fallback_from_filtered={fallback_count}",
                f"unmapped={unmapped_count}",
                f"fallback_queries_available={len(fallback_map)}",
                f"fallback_enabled={args.fallback_enabled}",
                f"fallback_min_identity={args.fallback_min_identity}",
                f"fallback_filtered_rows_seen={fallback_total_rows}",
                f"fallback_rows_below_min_identity={fallback_low_identity_rows}",
                "fallback_priority=bitscore_desc,line_order_asc_after_identity_filter",
                f"allow_containment_match={args.allow_containment_match}",
                "",
            ]
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
