from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_msa.py"


def load_script():
    spec = importlib.util.spec_from_file_location("multiple_sequence_alignment_run", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def script():
    return load_script()


def write_input(path: Path) -> None:
    path.write_text(">seq_a description\nACGT\n>seq_b\nACGA\n", encoding="utf-8")


def install_fake_mafft(directory: Path) -> Path:
    executable = directory / "mafft"
    executable.write_text(
        """#!/usr/bin/env python3
import os
import sys
from pathlib import Path

if "--version" in sys.argv:
    print("MAFFT v7.fake", file=sys.stderr)
    raise SystemExit(0)

mode = os.environ.get("FAKE_MAFFT_MODE", "success")
if mode == "fail":
    print("simulated failure", file=sys.stderr)
    raise SystemExit(7)
if mode == "unequal":
    print(">seq_a\\nACGT-\\n>seq_b\\nACGA")
    raise SystemExit(0)
if mode == "wrong_id":
    print(">seq_a\\nACGT\\n>unexpected\\nACGA")
    raise SystemExit(0)

print(Path(sys.argv[-1]).read_text(encoding="utf-8"), end="")
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


@pytest.mark.parametrize(
    ("aligner", "expected"),
    [
        ("mafft", ["tool", "--auto", "--thread", "4", "input.fa"]),
        (
            "muscle",
            [
                "tool",
                "-align",
                "input.fa",
                "-output",
                "output.fa",
                "-threads",
                "4",
            ],
        ),
        (
            "clustalo",
            [
                "tool",
                "-i",
                "input.fa",
                "-o",
                "output.fa",
                "--threads=4",
                "--outfmt=fasta",
            ],
        ),
        ("famsa", ["tool", "-t", "4", "input.fa", "output.fa"]),
    ],
)
def test_build_command(script, aligner: str, expected: list[str]) -> None:
    assert script.build_command(
        aligner,
        "tool",
        Path("input.fa"),
        Path("output.fa"),
        4,
    ) == expected


def test_successful_mafft_run_records_reproducibility_metadata(
    script, tmp_path: Path, monkeypatch
) -> None:
    install_fake_mafft(tmp_path)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}")
    input_path = tmp_path / "input.fasta"
    output_dir = tmp_path / "result"
    write_input(input_path)

    exit_code = script.main(
        [
            "--input",
            str(input_path),
            "--aligner",
            "mafft",
            "--threads",
            "4",
            "--output-dir",
            str(output_dir),
        ]
    )

    assert exit_code == 0
    assert (output_dir / "alignment.fasta").read_text(encoding="utf-8") == (
        input_path.read_text(encoding="utf-8")
    )
    metadata = json.loads((output_dir / "run.json").read_text(encoding="utf-8"))
    assert metadata["status"] == "success"
    assert metadata["aligner"] == "mafft"
    assert metadata["aligner_version"] == "MAFFT v7.fake"
    assert metadata["command"] == [
        "mafft",
        "--auto",
        "--thread",
        "4",
        "INPUT_FASTA",
    ]
    assert metadata["input_sequence_count"] == 2
    assert metadata["output_sequence_count"] == 2
    assert metadata["alignment_length"] == 4
    assert len(metadata["input_sha256"]) == 64
    assert len(metadata["output_sha256"]) == 64


def test_aligner_failure_has_metadata_but_no_formal_alignment(
    script, tmp_path: Path, monkeypatch
) -> None:
    install_fake_mafft(tmp_path)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("FAKE_MAFFT_MODE", "fail")
    input_path = tmp_path / "input.fasta"
    output_dir = tmp_path / "result"
    write_input(input_path)

    exit_code = script.main(
        ["--input", str(input_path), "--output-dir", str(output_dir)]
    )

    assert exit_code == script.EXIT_ALIGNER
    assert not (output_dir / "alignment.fasta").exists()
    metadata = json.loads((output_dir / "run.json").read_text(encoding="utf-8"))
    assert metadata["status"] == "aligner_failed"
    assert metadata["aligner_exit_code"] == 7
    assert "simulated failure" in (output_dir / "aligner.log").read_text(
        encoding="utf-8"
    )


def test_unequal_output_lengths_fail_validation(
    script, tmp_path: Path, monkeypatch
) -> None:
    install_fake_mafft(tmp_path)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("FAKE_MAFFT_MODE", "unequal")
    input_path = tmp_path / "input.fasta"
    output_dir = tmp_path / "result"
    write_input(input_path)

    exit_code = script.main(
        ["--input", str(input_path), "--output-dir", str(output_dir)]
    )

    assert exit_code == script.EXIT_OUTPUT
    assert not (output_dir / "alignment.fasta").exists()
    metadata = json.loads((output_dir / "run.json").read_text(encoding="utf-8"))
    assert metadata["status"] == "invalid_output"
    assert "common alignment length" in metadata["error"]


def test_changed_output_ids_fail_validation(
    script, tmp_path: Path, monkeypatch
) -> None:
    install_fake_mafft(tmp_path)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("FAKE_MAFFT_MODE", "wrong_id")
    input_path = tmp_path / "input.fasta"
    output_dir = tmp_path / "result"
    write_input(input_path)

    exit_code = script.main(
        ["--input", str(input_path), "--output-dir", str(output_dir)]
    )

    assert exit_code == script.EXIT_OUTPUT
    assert not (output_dir / "alignment.fasta").exists()
    metadata = json.loads((output_dir / "run.json").read_text(encoding="utf-8"))
    assert metadata["status"] == "invalid_output"
    assert metadata["error"] == "Input/output FASTA ID sets differ"


def test_duplicate_input_ids_are_rejected_before_execution(
    script, tmp_path: Path, monkeypatch
) -> None:
    install_fake_mafft(tmp_path)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}")
    input_path = tmp_path / "input.fasta"
    input_path.write_text(">same one\nAAAA\n>same two\nAAAA\n", encoding="utf-8")

    exit_code = script.main(
        ["--input", str(input_path), "--output-dir", str(tmp_path / "result")]
    )

    assert exit_code == script.EXIT_INPUT
    assert not (tmp_path / "result").exists()


def test_managed_outputs_are_never_overwritten(
    script, tmp_path: Path, monkeypatch
) -> None:
    install_fake_mafft(tmp_path)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}")
    input_path = tmp_path / "input.fasta"
    output_dir = tmp_path / "result"
    output_dir.mkdir()
    existing = output_dir / "alignment.fasta"
    existing.write_text("do not replace\n", encoding="utf-8")
    write_input(input_path)

    exit_code = script.main(
        ["--input", str(input_path), "--output-dir", str(output_dir)]
    )

    assert exit_code == script.EXIT_INPUT
    assert existing.read_text(encoding="utf-8") == "do not replace\n"
