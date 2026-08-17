#!/usr/bin/env python3
"""Run a supported multiple-sequence aligner and validate its FASTA output."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence


SCRIPT_VERSION = "1.0.0"
ALIGNMENT_NAME = "alignment.fasta"
LOG_NAME = "aligner.log"
METADATA_NAME = "run.json"

EXIT_INPUT = 2
EXIT_ALIGNER = 3
EXIT_OUTPUT = 4
EXIT_INTERRUPTED = 130

VERSION_ARGUMENTS = {
    "mafft": ["--version"],
    "muscle": ["-version"],
    "clustalo": ["--version"],
    "famsa": ["-help"],
}


class PreflightError(Exception):
    """An input, dependency, or output-directory problem."""


@dataclass(frozen=True)
class FastaSummary:
    ids: tuple[str, ...]
    lengths: tuple[int, ...]

    @property
    def count(self) -> int:
        return len(self.ids)


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run MAFFT, MUSCLE 5, Clustal Omega, or FAMSA and validate the "
            "resulting aligned FASTA. Input interpretation remains the caller's "
            "responsibility."
        )
    )
    parser.add_argument("--input", required=True, type=Path, help="Input FASTA")
    parser.add_argument(
        "--aligner",
        choices=sorted(VERSION_ARGUMENTS),
        default="mafft",
        help="Alignment software; defaults to mafft",
    )
    parser.add_argument(
        "--threads",
        type=positive_int,
        default=1,
        help="Threads passed to the aligner; defaults to 1",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="Directory for alignment.fasta, aligner.log, and run.json",
    )
    return parser


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def read_fasta_summary(path: Path) -> FastaSummary:
    """Read only FASTA identifiers and sequence lengths, not sequence biology."""
    ids: list[str] = []
    lengths: list[int] = []
    current_id: str | None = None
    current_length = 0

    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith(">"):
                if current_id is not None:
                    if current_length == 0:
                        raise ValueError(f"FASTA record {current_id!r} is empty")
                    lengths.append(current_length)
                header = line[1:].strip()
                if not header:
                    raise ValueError(f"FASTA header is empty at line {line_number}")
                current_id = header.split(None, 1)[0]
                ids.append(current_id)
                current_length = 0
            elif current_id is None:
                raise ValueError("FASTA has sequence data before its first header")
            else:
                current_length += len("".join(line.split()))

    if current_id is None:
        raise ValueError("FASTA contains no records")
    if current_length == 0:
        raise ValueError(f"FASTA record {current_id!r} is empty")
    lengths.append(current_length)

    duplicates = sorted(
        record_id for record_id, count in Counter(ids).items() if count > 1
    )
    if duplicates:
        preview = ", ".join(duplicates[:5])
        suffix = "..." if len(duplicates) > 5 else ""
        raise ValueError(f"FASTA contains duplicate IDs: {preview}{suffix}")

    return FastaSummary(ids=tuple(ids), lengths=tuple(lengths))


def build_command(
    aligner: str,
    executable: str,
    input_path: Path,
    output_path: Path,
    threads: int,
) -> list[str]:
    if aligner == "mafft":
        return [executable, "--auto", "--thread", str(threads), str(input_path)]
    if aligner == "muscle":
        return [
            executable,
            "-align",
            str(input_path),
            "-output",
            str(output_path),
            "-threads",
            str(threads),
        ]
    if aligner == "clustalo":
        return [
            executable,
            "-i",
            str(input_path),
            "-o",
            str(output_path),
            f"--threads={threads}",
            "--outfmt=fasta",
        ]
    if aligner == "famsa":
        return [executable, "-t", str(threads), str(input_path), str(output_path)]
    raise ValueError(f"Unsupported aligner: {aligner}")


def logical_command(aligner: str, threads: int) -> list[str]:
    return build_command(
        aligner,
        aligner,
        Path("INPUT_FASTA"),
        Path("OUTPUT_FASTA"),
        threads,
    )


def probe_version(executable: str, aligner: str) -> tuple[str, int | None]:
    try:
        completed = subprocess.run(
            [executable, *VERSION_ARGUMENTS[aligner]],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown", None

    combined = "\n".join(
        part.strip() for part in (completed.stdout, completed.stderr) if part.strip()
    )
    first_line = next((line.strip() for line in combined.splitlines() if line.strip()), "")
    return first_line[:1000] or "unknown", completed.returncode


def write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def prepare_paths(output_dir: Path) -> tuple[Path, Path, Path]:
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PreflightError(f"Cannot create output directory {output_dir}: {exc}") from exc
    if not output_dir.is_dir():
        raise PreflightError(f"Output path is not a directory: {output_dir}")

    paths = (
        output_dir / ALIGNMENT_NAME,
        output_dir / LOG_NAME,
        output_dir / METADATA_NAME,
    )
    existing = [path.name for path in paths if path.exists()]
    if existing:
        raise PreflightError(
            "Refusing to overwrite managed output files: " + ", ".join(existing)
        )
    return paths


def execute_aligner(
    aligner: str,
    command: Sequence[str],
    logical_argv: Sequence[str],
    temporary_alignment: Path,
    log_path: Path,
    output_dir: Path,
) -> int:
    with log_path.open("wb") as log_handle:
        log_handle.write(
            (
                f"# run_msa.py {SCRIPT_VERSION}\n"
                f"# command: {shlex.join(logical_argv)}\n"
            ).encode("utf-8")
        )
        log_handle.flush()
        if aligner == "mafft":
            with temporary_alignment.open("wb") as output_handle:
                completed = subprocess.run(
                    command,
                    cwd=output_dir,
                    stdout=output_handle,
                    stderr=log_handle,
                    check=False,
                )
        else:
            completed = subprocess.run(
                command,
                cwd=output_dir,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                check=False,
            )
    return completed.returncode


def record_failure(
    metadata: dict[str, object],
    metadata_path: Path,
    started_monotonic: float,
    *,
    status: str,
    message: str,
    exit_code: int,
) -> int:
    metadata.update(
        {
            "status": status,
            "error": message,
            "finished_at": utc_now(),
            "duration_seconds": round(time.monotonic() - started_monotonic, 6),
        }
    )
    try:
        write_json_atomic(metadata_path, metadata)
    except OSError as exc:
        print(f"ERROR: could not write {metadata_path}: {exc}", file=sys.stderr)
    print(f"ERROR: {message}", file=sys.stderr)
    return exit_code


def run(args: argparse.Namespace) -> int:
    input_path = args.input.expanduser().resolve()
    if not input_path.is_file():
        raise PreflightError(f"Input FASTA is not a file: {input_path}")
    try:
        input_summary = read_fasta_summary(input_path)
        input_sha256 = sha256_file(input_path)
    except (OSError, UnicodeError, ValueError) as exc:
        raise PreflightError(f"Cannot use input FASTA {input_path}: {exc}") from exc
    if input_summary.count < 2:
        raise PreflightError("Multiple sequence alignment requires at least two records")

    executable = shutil.which(args.aligner)
    if executable is None:
        raise PreflightError(f"Required aligner is not on PATH: {args.aligner}")

    output_dir = args.output_dir.expanduser().resolve()
    alignment_path, log_path, metadata_path = prepare_paths(output_dir)
    version, version_probe_exit_code = probe_version(executable, args.aligner)
    started_monotonic = time.monotonic()
    logical_argv = logical_command(args.aligner, args.threads)
    metadata: dict[str, object] = {
        "schema_version": 1,
        "script_version": SCRIPT_VERSION,
        "status": "running",
        "aligner": args.aligner,
        "aligner_version": version,
        "version_probe_exit_code": version_probe_exit_code,
        "command": logical_argv,
        "threads": args.threads,
        "input_file": input_path.name,
        "input_sha256": input_sha256,
        "input_sequence_count": input_summary.count,
        "started_at": utc_now(),
    }

    try:
        with tempfile.TemporaryDirectory(prefix=".msa-", dir=output_dir) as temp_dir:
            temporary_alignment = Path(temp_dir) / ALIGNMENT_NAME
            command = build_command(
                args.aligner,
                executable,
                input_path,
                temporary_alignment,
                args.threads,
            )
            try:
                return_code = execute_aligner(
                    args.aligner,
                    command,
                    logical_argv,
                    temporary_alignment,
                    log_path,
                    output_dir,
                )
            except OSError as exc:
                return record_failure(
                    metadata,
                    metadata_path,
                    started_monotonic,
                    status="aligner_failed",
                    message=f"Could not run {args.aligner}: {exc}",
                    exit_code=EXIT_ALIGNER,
                )

            metadata["aligner_exit_code"] = return_code
            if return_code != 0:
                return record_failure(
                    metadata,
                    metadata_path,
                    started_monotonic,
                    status="aligner_failed",
                    message=f"{args.aligner} exited with status {return_code}",
                    exit_code=EXIT_ALIGNER,
                )
            if not temporary_alignment.is_file() or temporary_alignment.stat().st_size == 0:
                return record_failure(
                    metadata,
                    metadata_path,
                    started_monotonic,
                    status="invalid_output",
                    message=f"{args.aligner} produced no aligned FASTA",
                    exit_code=EXIT_OUTPUT,
                )

            try:
                output_summary = read_fasta_summary(temporary_alignment)
            except (OSError, UnicodeError, ValueError) as exc:
                return record_failure(
                    metadata,
                    metadata_path,
                    started_monotonic,
                    status="invalid_output",
                    message=f"Cannot use aligned FASTA: {exc}",
                    exit_code=EXIT_OUTPUT,
                )

            validation_error: str | None = None
            if output_summary.count != input_summary.count:
                validation_error = (
                    "Input/output sequence counts differ: "
                    f"{input_summary.count} != {output_summary.count}"
                )
            elif set(output_summary.ids) != set(input_summary.ids):
                validation_error = "Input/output FASTA ID sets differ"
            elif len(set(output_summary.lengths)) != 1:
                validation_error = (
                    "Output FASTA records do not have one common alignment length"
                )
            if validation_error:
                return record_failure(
                    metadata,
                    metadata_path,
                    started_monotonic,
                    status="invalid_output",
                    message=validation_error,
                    exit_code=EXIT_OUTPUT,
                )

            alignment_length = output_summary.lengths[0]
            os.replace(temporary_alignment, alignment_path)
    except KeyboardInterrupt:
        return record_failure(
            metadata,
            metadata_path,
            started_monotonic,
            status="interrupted",
            message="Alignment was interrupted",
            exit_code=EXIT_INTERRUPTED,
        )

    metadata.update(
        {
            "status": "success",
            "output_file": alignment_path.name,
            "output_sha256": sha256_file(alignment_path),
            "output_sequence_count": output_summary.count,
            "alignment_length": alignment_length,
            "finished_at": utc_now(),
            "duration_seconds": round(time.monotonic() - started_monotonic, 6),
        }
    )
    try:
        write_json_atomic(metadata_path, metadata)
    except OSError as exc:
        print(f"ERROR: could not write {metadata_path}: {exc}", file=sys.stderr)
        return EXIT_OUTPUT

    print(
        json.dumps(
            {
                "status": "success",
                "alignment": str(alignment_path),
                "log": str(log_path),
                "metadata": str(metadata_path),
                "aligner": args.aligner,
                "aligner_version": version,
                "sequence_count": output_summary.count,
                "alignment_length": alignment_length,
            },
            ensure_ascii=True,
            sort_keys=True,
        )
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except PreflightError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_INPUT


if __name__ == "__main__":
    raise SystemExit(main())
