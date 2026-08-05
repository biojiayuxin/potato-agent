from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


SAMPLE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
CATEGORY = "monoploid"
TSV_COLUMNS = (
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


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(mode)
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def load_fasta_index(fai_path: Path) -> dict[str, int]:
    sequences: dict[str, int] = {}
    with fai_path.open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            fields = raw.rstrip("\n").split("\t")
            if len(fields) < 2:
                raise ValueError(f"invalid FASTA index line {line_number}: {raw.rstrip()}")
            name = fields[0]
            if name in sequences:
                raise ValueError(f"duplicate FASTA sequence name: {name}")
            sequences[name] = int(fields[1])
    if not sequences:
        raise ValueError("FASTA contains no sequences")
    return sequences


def _attribute_values(attributes: str, attribute_name: str) -> set[str]:
    for item in attributes.split(";"):
        key, separator, value = item.partition("=")
        if separator and key == attribute_name:
            return {part for part in value.split(",") if part}
    return set()


def validate_and_sort_gff3(
    source: Path,
    sorted_path: Path,
    sequence_lengths: dict[str, int],
    sort_temp_dir: Path,
) -> dict[str, int]:
    unsorted_path = sorted_path.with_suffix(".unsorted")
    sorted_data_path = sorted_path.with_suffix(".sorted-data")
    headers: list[str] = []
    saw_feature = False
    feature_count = 0
    gene_count = 0
    transcript_count = 0
    declared_ids: set[str] = set()
    parent_children: dict[str, dict[str, Any]] = {}

    try:
        with source.open("r", encoding="utf-8") as source_handle, unsorted_path.open(
            "w", encoding="utf-8", newline=""
        ) as data_handle:
            for line_number, raw in enumerate(source_handle, start=1):
                line = raw.rstrip("\r\n")
                if not line:
                    continue
                if line == "##FASTA":
                    raise ValueError("embedded FASTA is not supported")
                if line.startswith("#"):
                    if not saw_feature:
                        headers.append(line)
                    continue

                saw_feature = True
                fields = line.split("\t")
                if len(fields) != 9:
                    raise ValueError(f"GFF3 line {line_number} does not have 9 columns")
                ref_name = fields[0]
                if ref_name not in sequence_lengths:
                    raise ValueError(
                        f"GFF3 line {line_number} uses sequence absent from FASTA: {ref_name}"
                    )
                try:
                    start = int(fields[3])
                    end = int(fields[4])
                except ValueError as exc:
                    raise ValueError(f"GFF3 line {line_number} has invalid coordinates") from exc
                if start < 1 or end < start or end > sequence_lengths[ref_name]:
                    raise ValueError(
                        f"GFF3 line {line_number} is outside {ref_name} (length "
                        f"{sequence_lengths[ref_name]}): {start}-{end}"
                    )

                feature_type = fields[2].lower()
                feature_count += 1
                declared_ids.update(_attribute_values(fields[8], "ID"))
                for parent_id in _attribute_values(fields[8], "Parent"):
                    child = parent_children.setdefault(
                        parent_id,
                        {
                            "types": set(),
                            "refNames": set(),
                            "sources": set(),
                            "strands": set(),
                            "start": start,
                            "end": end,
                        },
                    )
                    child["types"].add(feature_type)
                    child["refNames"].add(ref_name)
                    child["sources"].add(fields[1])
                    child["strands"].add(fields[6])
                    child["start"] = min(child["start"], start)
                    child["end"] = max(child["end"], end)
                if feature_type == "gene":
                    gene_count += 1
                elif feature_type in {"mrna", "transcript"}:
                    transcript_count += 1
                data_handle.write(line)
                data_handle.write("\n")

        if not saw_feature:
            raise ValueError("GFF3 contains no features")
        missing_parent_ids = set(parent_children) - declared_ids
        with unsorted_path.open("a", encoding="utf-8", newline="") as data_handle:
            for parent_id in sorted(missing_parent_ids):
                child = parent_children[parent_id]
                if not child["types"] <= {"mrna", "transcript"}:
                    child_types = ", ".join(sorted(child["types"]))
                    raise ValueError(
                        f"cannot synthesize missing parent {parent_id}; child types: {child_types}"
                    )
                if len(child["refNames"]) != 1:
                    raise ValueError(f"missing gene parent {parent_id} spans multiple sequences")
                informative_strands = child["strands"] - {"."}
                if len(informative_strands) > 1:
                    raise ValueError(f"missing gene parent {parent_id} spans multiple strands")
                ref_name = next(iter(child["refNames"]))
                source_name = (
                    next(iter(child["sources"]))
                    if len(child["sources"]) == 1
                    else "Potato_Agent"
                )
                strand = next(iter(informative_strands), ".")
                data_handle.write(
                    "\t".join(
                        (
                            ref_name,
                            source_name,
                            "gene",
                            str(child["start"]),
                            str(child["end"]),
                            ".",
                            strand,
                            ".",
                            f"ID={parent_id};Name={parent_id}",
                        )
                    )
                    + "\n"
                )
                gene_count += 1
                feature_count += 1
        env = dict(os.environ, LC_ALL="C")
        subprocess.run(
            [
                "sort",
                "-t",
                "\t",
                "-k1,1",
                "-k4,4n",
                "-k5,5n",
                "--temporary-directory",
                str(sort_temp_dir),
                "--output",
                str(sorted_data_path),
                str(unsorted_path),
            ],
            check=True,
            env=env,
        )

        if not any(header.startswith("##gff-version") for header in headers):
            headers.insert(0, "##gff-version 3")
        with sorted_path.open("w", encoding="utf-8", newline="") as output_handle:
            output_handle.write("\n".join(headers) + "\n")
            with sorted_data_path.open("r", encoding="utf-8") as sorted_data_handle:
                shutil.copyfileobj(sorted_data_handle, output_handle)
    finally:
        unsorted_path.unlink(missing_ok=True)
        sorted_data_path.unlink(missing_ok=True)

    return {
        "featureCount": feature_count,
        "geneCount": gene_count,
        "transcriptCount": transcript_count,
    }


def build_assembly(
    *,
    sample: str,
    display_name: str,
    fasta: Path,
    gff3: Path,
    species: str,
    doi: str,
    sequence_lengths: dict[str, int],
    annotation_counts: dict[str, int],
) -> dict[str, Any]:
    directory = f"{CATEGORY}/{sample}"
    assembly: dict[str, Any] = {
        "id": directory,
        "sample": sample,
        "displayName": display_name,
        "category": CATEGORY,
        "ploidy": CATEGORY,
        "directory": directory,
        "referenceMode": "bgzip_fasta",
        "reference": f"{directory}/reference/{sample}.fa.bgz",
        "fai": f"{directory}/reference/{sample}.fa.bgz.fai",
        "gzi": f"{directory}/reference/{sample}.fa.bgz.gzi",
        "chromSizes": f"{directory}/reference/{sample}.chrom.sizes",
        "annotation": f"{directory}/annotation/{sample}.gff3.bgz",
        "annotationIndex": f"{directory}/annotation/{sample}.gff3.bgz.tbi",
        "sourceReferences": [str(fasta.absolute())],
        "sourceAnnotations": [str(gff3.absolute())],
        "species": species,
        "refNameCount": len(sequence_lengths),
        "totalBp": sum(sequence_lengths.values()),
        "geneCount": annotation_counts["geneCount"],
        "transcriptCount": annotation_counts["transcriptCount"],
    }
    if doi:
        assembly["doi"] = doi
    return assembly


def update_manifest(manifest: dict[str, Any], assembly: dict[str, Any]) -> dict[str, Any]:
    assemblies = manifest.get("assemblies")
    if not isinstance(assemblies, list):
        raise ValueError("manifest is missing an assemblies list")
    assembly_id = assembly["id"]
    if any(item.get("id") == assembly_id for item in assemblies if isinstance(item, dict)):
        raise ValueError(f"assembly already exists: {assembly_id}")

    assemblies.append(assembly)
    category_order = {"monoploid": 0, "phased_diploid": 1, "phased_tetraploid": 2}
    assemblies.sort(
        key=lambda item: (
            category_order.get(str(item.get("category")), 99),
            str(item.get("sample", "")).casefold(),
        )
    )
    manifest["updatedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )
    manifest["counts"] = {
        "assemblies": len(assemblies),
        "monoploidAssemblies": sum(item.get("category") == "monoploid" for item in assemblies),
        "phasedDiploidAssemblies": sum(
            item.get("category") == "phased_diploid" for item in assemblies
        ),
        "phasedTetraploidAssemblies": sum(
            item.get("category") == "phased_tetraploid" for item in assemblies
        ),
        "sourceFastaFiles": sum(len(item.get("sourceReferences", [])) for item in assemblies),
    }
    return manifest


def update_assemblies_tsv(content: str, assembly: dict[str, Any]) -> str:
    from io import StringIO

    reader = csv.DictReader(StringIO(content), delimiter="\t")
    if tuple(reader.fieldnames or ()) != TSV_COLUMNS:
        raise ValueError("assemblies.tsv has an unexpected header")
    rows = list(reader)
    if any(row["id"] == assembly["id"] for row in rows):
        raise ValueError(f"assembly already exists in assemblies.tsv: {assembly['id']}")
    rows.append(
        {
            "id": assembly.get("id", ""),
            "category": assembly.get("category", ""),
            "ploidy": assembly.get("ploidy", ""),
            "sample": assembly.get("sample", ""),
            "display_name": assembly.get("displayName", ""),
            "haplotypes": "monoploid",
            "total_bp": assembly.get("totalBp", ""),
            "ref_name_count": assembly.get("refNameCount", ""),
            "gene_count": assembly.get("geneCount", ""),
            "transcript_count": assembly.get("transcriptCount", ""),
            "reference": assembly.get("reference", ""),
            "annotation": assembly.get("annotation", ""),
            "species": assembly.get("species", ""),
            "doi": assembly.get("doi", ""),
        }
    )
    category_order = {"monoploid": 0, "phased_diploid": 1, "phased_tetraploid": 2}
    rows.sort(
        key=lambda row: (
            category_order.get(row["category"], 99),
            row["sample"].casefold(),
        )
    )
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=TSV_COLUMNS, delimiter="\t", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def render_readme(counts: dict[str, int]) -> str:
    return f"""# Genome_browser_DB

Optimized genome browser database for Potato Agent and the web interface.

- User-facing assemblies: {counts['assemblies']}
- Monoploid assemblies: {counts['monoploidAssemblies']}
- Phased diploid assemblies: {counts['phasedDiploidAssemblies']}
- Phased tetraploid assemblies: {counts['phasedTetraploidAssemblies']}
- Source FASTA files merged/indexed: {counts['sourceFastaFiles']}

Reference files are bgzip-compressed FASTA with `.fai` and `.gzi` indexes.
Annotation files are sorted bgzip-compressed GFF3 with tabix `.tbi` indexes.
Phased diploid and tetraploid samples are exposed as one sample-level assembly by merging haplotype source files when needed.
"""


def normalize_tree_permissions(root: Path) -> None:
    root.chmod(0o755)
    for path in root.rglob("*"):
        path.chmod(0o755 if path.is_dir() else 0o644)


def build_annotation_artifacts(
    *,
    source: Path,
    annotation_dir: Path,
    sample: str,
    sequence_lengths: dict[str, int],
    bgzip: str,
    tabix: str,
    threads: int,
    sort_temp_dir: Path,
) -> dict[str, int]:
    sorted_gff3 = annotation_dir / f"{sample}.sorted.gff3"
    annotation_counts = validate_and_sort_gff3(
        source, sorted_gff3, sequence_lengths, sort_temp_dir
    )
    compressed_gff3 = annotation_dir / f"{sample}.gff3.bgz"
    with compressed_gff3.open("wb") as output:
        subprocess.run(
            [bgzip, "-@", str(threads), "-c", str(sorted_gff3)],
            check=True,
            stdout=output,
        )
    sorted_gff3.unlink()
    subprocess.run([tabix, "-f", "-p", "gff", str(compressed_gff3)], check=True)
    return annotation_counts


def import_assembly(args: argparse.Namespace) -> dict[str, Any]:
    db_root = args.db_root.resolve()
    fasta = args.fasta.absolute()
    gff3 = args.gff3.absolute()
    manifest_path = db_root / "assemblies.json"
    tsv_path = db_root / "assemblies.tsv"
    readme_path = db_root / "README.md"
    target = db_root / CATEGORY / args.sample

    if not SAMPLE_RE.fullmatch(args.sample):
        raise ValueError("sample may contain only letters, digits, dots, underscores, and hyphens")
    for source in (fasta, gff3):
        if not source.is_file():
            raise FileNotFoundError(source)
    if target.exists():
        raise FileExistsError(target)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    original_tsv = tsv_path.read_text(encoding="utf-8")
    original_readme = readme_path.read_text(encoding="utf-8")
    if any(item.get("id") == f"{CATEGORY}/{args.sample}" for item in manifest["assemblies"]):
        raise ValueError(f"assembly already exists: {CATEGORY}/{args.sample}")

    bgzip = shutil.which(args.bgzip)
    tabix = shutil.which(args.tabix)
    samtools = shutil.which(args.samtools)
    for name, executable in (("bgzip", bgzip), ("tabix", tabix), ("samtools", samtools)):
        if executable is None:
            raise FileNotFoundError(f"{name} executable not found")

    category_root = db_root / CATEGORY
    category_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{args.sample}.import-", dir=category_root))
    staging.chmod(0o755)
    reference_dir = staging / "reference"
    annotation_dir = staging / "annotation"
    reference_dir.mkdir()
    annotation_dir.mkdir()
    published = False

    try:
        compressed_fasta = reference_dir / f"{args.sample}.fa.bgz"
        with compressed_fasta.open("wb") as output:
            subprocess.run(
                [bgzip, "-@", str(args.threads), "-c", str(fasta)],
                check=True,
                stdout=output,
            )
        subprocess.run([samtools, "faidx", str(compressed_fasta)], check=True)
        sequence_lengths = load_fasta_index(compressed_fasta.with_suffix(".bgz.fai"))
        gzi_path = compressed_fasta.with_suffix(".bgz.gzi")
        if not gzi_path.is_file():
            raise RuntimeError(f"samtools did not create {gzi_path.name}")
        chrom_sizes = reference_dir / f"{args.sample}.chrom.sizes"
        chrom_sizes.write_text(
            "".join(f"{name}\t{length}\n" for name, length in sequence_lengths.items()),
            encoding="utf-8",
        )

        annotation_counts = build_annotation_artifacts(
            source=gff3,
            annotation_dir=annotation_dir,
            sample=args.sample,
            sequence_lengths=sequence_lengths,
            bgzip=bgzip,
            tabix=tabix,
            threads=args.threads,
            sort_temp_dir=staging,
        )

        assembly = build_assembly(
            sample=args.sample,
            display_name=args.display_name or args.sample,
            fasta=fasta,
            gff3=gff3,
            species=args.species,
            doi=args.doi,
            sequence_lengths=sequence_lengths,
            annotation_counts=annotation_counts,
        )
        sample_payload = dict(assembly)
        sample_payload["haplotypes"] = ["monoploid"]
        (staging / "sample.json").write_text(
            json.dumps(sample_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        normalize_tree_permissions(staging)

        updated_manifest = update_manifest(manifest, assembly)
        updated_tsv = update_assemblies_tsv(original_tsv, assembly)
        staging.rename(target)
        published = True
        try:
            atomic_write_text(tsv_path, updated_tsv)
            atomic_write_text(readme_path, render_readme(updated_manifest["counts"]))
            atomic_write_text(
                manifest_path,
                json.dumps(updated_manifest, indent=2, ensure_ascii=False) + "\n",
            )
        except BaseException:
            atomic_write_text(tsv_path, original_tsv)
            atomic_write_text(readme_path, original_readme)
            shutil.rmtree(target)
            published = False
            raise
        return assembly
    finally:
        if not published:
            shutil.rmtree(staging, ignore_errors=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import one monoploid FASTA/GFF3 assembly into Genome_browser_DB"
    )
    parser.add_argument("--db-root", type=Path, required=True)
    parser.add_argument("--sample", required=True)
    parser.add_argument("--display-name", default="")
    parser.add_argument("--fasta", type=Path, required=True)
    parser.add_argument("--gff3", type=Path, required=True)
    parser.add_argument("--species", default="Solanum tuberosum")
    parser.add_argument("--doi", default="")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--bgzip", default="bgzip")
    parser.add_argument("--tabix", default="tabix")
    parser.add_argument("--samtools", default="samtools")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.threads < 1:
        raise ValueError("threads must be at least 1")
    assembly = import_assembly(args)
    print(json.dumps(assembly, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
