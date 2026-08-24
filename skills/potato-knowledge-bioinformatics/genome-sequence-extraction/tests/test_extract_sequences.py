from __future__ import annotations

import csv
import importlib.util
import io
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "extract_sequences.py"
SPEC = importlib.util.spec_from_file_location("genome_sequence_extract_sequences", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
EXTRACT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = EXTRACT
SPEC.loader.exec_module(EXTRACT)

BACKEND_INDEX_PATH = Path(__file__).resolve().parents[4] / "interface" / "genome_feature_index.py"
BACKEND_SPEC = importlib.util.spec_from_file_location(
    "genome_sequence_extract_backend_index",
    BACKEND_INDEX_PATH,
)
assert BACKEND_SPEC is not None and BACKEND_SPEC.loader is not None
BACKEND_INDEX = importlib.util.module_from_spec(BACKEND_SPEC)
sys.modules[BACKEND_SPEC.name] = BACKEND_INDEX
BACKEND_SPEC.loader.exec_module(BACKEND_INDEX)


ASSEMBLY = {
    "id": "monoploid/DMv8.2",
    "sample": "DMv8.2",
    "displayName": "DMv8.2",
    "species": "Solanum tuberosum",
    "reference": "monoploid/DMv8.2/reference/genome.fa.bgz",
    "annotation": "monoploid/DMv8.2/annotation/genes.gff3.bgz",
}


class BinaryResponse:
    def __init__(self, body: bytes, *, content_length: int | None = None) -> None:
        self.body = body
        self.offset = 0
        self.headers = {
            "Content-Length": str(len(body) if content_length is None else content_length)
        }

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self.body) - self.offset
        block = self.body[self.offset : self.offset + size]
        self.offset += len(block)
        return block


def write_test_fai(genome: Path, *, length: int = 100) -> None:
    Path(str(genome) + ".fai").write_text(
        f"chr1\t{length}\t6\t{length}\t{length + 1}\n",
        encoding="utf-8",
    )


def write_local_feature_index(
    path: Path,
    *,
    ambiguous_alias: bool = False,
    reference_path: str = "genome.fa",
    annotation_path: str = "genes.gff3",
) -> None:
    reference = path.parent / reference_path
    annotation = path.parent / annotation_path
    fai = Path(str(reference) + ".fai")
    reference_stat = reference.stat() if reference.is_file() else None
    annotation_stat = annotation.stat() if annotation.is_file() else None
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            create table metadata(key text primary key, value text not null);
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
              representative_map_sha256 text not null
            );
            create table seqids(
              assembly_pk integer not null,
              seqid text not null,
              length integer not null
            );
            create table genes(
              gene_pk integer primary key,
              assembly_pk integer not null,
              gene_id text not null,
              feature_type text not null,
              seqid text not null,
              start integer not null,
              end integer not null,
              strand text not null,
              representative_transcript_pk integer
            );
            create table transcripts(
              transcript_pk integer primary key,
              assembly_pk integer not null,
              gene_pk integer not null,
              transcript_id text not null,
              feature_type text not null,
              seqid text not null,
              start integer not null,
              end integer not null,
              strand text not null,
              exon_segments text not null,
              cds_segments text not null,
              exon_length integer not null,
              cds_length integer not null,
              cds_5p integer,
              first_cds_phase integer,
              is_representative integer not null,
              representative_source text not null
            );
            create table gene_aliases(assembly_pk integer, alias text, gene_pk integer);
            create table transcript_aliases(
              assembly_pk integer,
              alias text,
              transcript_pk integer
            );
            """
        )
        conn.executemany(
            "insert into metadata(key,value) values(?,?)",
            EXTRACT.FEATURE_INDEX_METADATA.items(),
        )
        conn.execute(
            "insert into assemblies values(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                1,
                ASSEMBLY["id"],
                reference_path,
                reference_stat.st_size if reference_stat else 0,
                reference_stat.st_mtime_ns if reference_stat else 0,
                reference_path + ".fai",
                EXTRACT.file_sha256(fai) if fai.is_file() else "",
                annotation_path,
                annotation_stat.st_size if annotation_stat else 0,
                annotation_stat.st_mtime_ns if annotation_stat else 0,
                EXTRACT.file_sha256(annotation) if annotation.is_file() else "",
                "",
            ),
        )
        conn.executemany(
            "insert into seqids values(1,?,?)",
            (("chr1", 100), ("chr2", 100)),
        )
        conn.execute(
            "insert into genes values(1,1,'gene1','gene','chr1',10,90,'+',11)"
        )
        conn.executemany(
            "insert into transcripts values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                (
                    11, 1, 1, "tx1", "mRNA", "chr1", 10, 90, "+",
                    "[[10,15],[80,85]]", "[[20,22,0],[48,50,0]]",
                    12, 6, 20, 0, 1, "mapping",
                ),
                (
                    12, 1, 1, "tx-longer", "mRNA", "chr1", 10, 90, "+",
                    "[[10,40],[60,90]]", "[[20,39,0]]",
                    62, 20, 20, 0, 0, "not_selected",
                ),
            ),
        )
        if ambiguous_alias:
            conn.executemany(
                "insert into genes values(?,?,?,?,?,?,?,?,null)",
                (
                    (2, 1, "geneA@chr1", "gene", "chr1", 1, 9, "+"),
                    (3, 1, "geneA@chr2", "gene", "chr2", 1, 9, "+"),
                ),
            )
            conn.executemany(
                "insert into gene_aliases values(1,'geneA',?)",
                ((2,), (3,)),
            )
        conn.commit()
    finally:
        conn.close()


def parse_args(*argv: str):
    with mock.patch.object(sys, "argv", [str(SCRIPT_PATH), *argv]):
        return EXTRACT.parse_args()


def feature_payload(
    *,
    query_id: str = "gene1",
    strand: str = "+",
    representative_source: str = "mapping",
    coding_start_key: str = "cdsFivePrimePosition",
) -> dict[str, object]:
    transcript = {
        "id": "tx1",
        "refName": "chr1",
        "start": 10,
        "end": 90,
        "strand": strand,
        "isRepresentative": True,
        "representativeSource": representative_source,
        "exonLength": 12,
        "cdsLength": 6,
        "exons": [
            {"refName": "chr1", "start": 10, "end": 15, "strand": strand},
            {"refName": "chr1", "start": 80, "end": 85, "strand": strand},
        ],
        "cds": [
            {"refName": "chr1", "start": 20, "end": 22, "strand": strand, "phase": 0},
            {"refName": "chr1", "start": 48, "end": 50, "strand": strand, "phase": 0},
        ],
        coding_start_key: 20 if strand == "+" else 50,
    }
    return {
        "assembly": ASSEMBLY["id"],
        "coordinateSystem": "1-based-inclusive",
        "query": {
            "id": query_id,
            "matchedBy": "id",
            "resolvedType": "gene",
            "resolvedId": query_id,
        },
        "gene": {
            "id": query_id,
            "refName": "chr1",
            "start": 10,
            "end": 90,
            "strand": strand,
        },
        "transcripts": [transcript],
    }


def sequence_response(payload: dict[str, object], sequence_by_interval=None) -> dict[str, object]:
    sequence_by_interval = sequence_by_interval or {}
    records = []
    for segment in payload["segments"]:
        key = (segment["refName"], segment["start"], segment["end"])
        sequence = sequence_by_interval.get(key, "A" * (segment["end"] - segment["start"] + 1))
        records.append(
            {
                "name": segment["name"],
                "refName": segment["refName"],
                "requestedStart": segment["start"],
                "requestedEnd": segment["end"],
                "start": segment["start"],
                "end": segment["end"],
                "strand": segment["strand"],
                "clipped": False,
                "length": len(sequence),
                "sequence": sequence,
            }
        )
    return {
        "assembly": payload["assembly"],
        "coordinateSystem": "1-based-inclusive",
        "records": records,
    }


def run_main(argv: list[str]) -> int:
    with mock.patch.object(sys, "argv", [str(SCRIPT_PATH), *argv]):
        return EXTRACT.main()


def read_report(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def test_promoter_defaults_to_representative_and_other_modes_default_to_error(tmp_path) -> None:
    common = ["--source", "api", "--assembly", "DMv8.2", "--id", "gene1"]
    promoter = parse_args(
        "--mode", "promoter", *common, "--length", "2000",
        "--output", str(tmp_path / "promoter.fa"), "--report", str(tmp_path / "promoter.tsv"),
    )
    cds = parse_args(
        "--mode", "cds", *common,
        "--output", str(tmp_path / "cds.fa"), "--report", str(tmp_path / "cds.tsv"),
    )

    assert promoter.isoform == "representative"
    assert cds.isoform == "error"


def test_download_mode_requires_its_dedicated_output_arguments(tmp_path) -> None:
    args = parse_args(
        "--mode",
        "download",
        "--source",
        "api",
        "--assembly",
        "DMv8.2",
        "--output-dir",
        str(tmp_path / "downloads"),
    )

    assert args.output_dir == str(tmp_path / "downloads")
    assert args.output is None
    assert args.report is None

    with pytest.raises(SystemExit):
        parse_args(
            "--mode",
            "download",
            "--source",
            "api",
            "--assembly",
            "DMv8.2",
        )
    with pytest.raises(SystemExit):
        parse_args(
            "--mode",
            "list-resources",
            "--overwrite",
        )


def test_mocked_api_main_extracts_cds_and_audits_backend(tmp_path) -> None:
    output = tmp_path / "gene1.cds.fa"
    report = tmp_path / "gene1.cds.tsv"
    calls: list[tuple[str, object, object]] = []

    def fake_request(_args, path, *, query=None, payload=None):
        calls.append((path, query, payload))
        if path.endswith("/assemblies"):
            return {"assemblies": [ASSEMBLY]}
        if path.endswith("/features/resolve"):
            return feature_payload()
        assert payload is not None
        return sequence_response(
            payload,
            {("chr1", 20, 22): "ATG", ("chr1", 48, 50): "TAA"},
        )

    with mock.patch.object(EXTRACT, "api_request_json", side_effect=fake_request):
        code = run_main(
            [
                "--mode", "cds", "--source", "api", "--api-base-url", "https://example.test",
                "--assembly", "DMv8.2", "--id", "gene1", "--output", str(output),
                "--report", str(report),
            ]
        )

    assert code == 0
    assert output.read_text(encoding="utf-8").splitlines()[-1] == "ATGTAA"
    row = read_report(report)[0]
    assert row["extraction_backend"] == "api"
    assert row["representative_source"] == "mapping"
    assert [call[0] for call in calls] == [
        "/api/genome-browser/assemblies",
        "/api/genome-browser/features/resolve",
        "/api/genome-browser/sequences",
    ]
    metadata = json.loads(Path(str(output) + ".meta.json").read_text(encoding="utf-8"))
    assert metadata["genome_index_mode"] == "remote_api"


def test_negative_strand_promoter_uses_forward_api_sequence_then_reverse_complements(tmp_path) -> None:
    output = tmp_path / "promoter.fa"
    report = tmp_path / "promoter.tsv"
    sequence_payloads: list[dict[str, object]] = []

    def fake_request(_args, path, *, query=None, payload=None):
        if path.endswith("/assemblies"):
            return {"assemblies": [ASSEMBLY]}
        if path.endswith("/features/resolve"):
            return feature_payload(strand="-", coding_start_key="codingStart")
        assert payload is not None
        sequence_payloads.append(payload)
        return sequence_response(payload, {("chr1", 51, 54): "AAGC"})

    with mock.patch.object(EXTRACT, "api_request_json", side_effect=fake_request):
        code = run_main(
            [
                "--mode", "promoter", "--source", "api", "--api-base-url", "https://example.test",
                "--assembly", "DMv8.2", "--id", "gene1", "--length", "4",
                "--output", str(output), "--report", str(report),
            ]
        )

    assert code == 0
    assert output.read_text(encoding="utf-8").splitlines()[-1] == "GCTT"
    assert sequence_payloads[0]["segments"] == [
        {"name": "segment-0-0", "refName": "chr1", "start": 51, "end": 54, "strand": "+"}
    ]
    row = read_report(report)[0]
    assert row["strand"] == "-"
    assert row["representative_source"] == "mapping"


def test_local_representative_policy_uses_deterministic_longest_cds_fallback(tmp_path) -> None:
    annotation = tmp_path / "genes.gff3"
    annotation.write_text(
        "##gff-version 3\n"
        "chr1\ttest\tgene\t10\t100\t.\t+\t.\tID=gene1\n"
        "chr1\ttest\tmRNA\t10\t100\t.\t+\t.\tID=tx-short;Parent=gene1\n"
        "chr1\ttest\tmRNA\t10\t100\t.\t+\t.\tID=tx-long;Parent=gene1\n"
        "chr1\ttest\tCDS\t20\t25\t.\t+\t0\tParent=tx-short\n"
        "chr1\ttest\tCDS\t30\t41\t.\t+\t0\tParent=tx-long\n",
        encoding="utf-8",
    )
    args = SimpleNamespace(mode="promoter", isoform="representative", length=3)

    plans = EXTRACT.build_feature_plans(args, annotation, ["gene1"])

    assert len(plans) == 1
    assert plans[0].transcript_id == "tx-long"
    assert plans[0].segments == [EXTRACT.Segment("chr1", 27, 29, "+")]
    assert plans[0].representative_source == "longest_cds_fallback"


def test_explicit_api_transcript_bypasses_gene_isoform_policy() -> None:
    payload = feature_payload(query_id="tx1")
    payload["query"]["resolvedType"] = "transcript"
    payload["query"]["resolvedId"] = "tx1"
    args = SimpleNamespace(isoform="error", mode="cds")

    selected = EXTRACT._select_api_transcripts(args, payload)

    assert [item["id"] for item in selected] == ["tx1"]


def test_api_sequence_plans_are_chunked_at_256_segments() -> None:
    segments = [EXTRACT.Segment("chr1", index, index, "+") for index in range(1, 258)]
    plan = EXTRACT.RecordPlan(query_id="tx1", strand="+", segments=segments)
    args = SimpleNamespace(mode="transcript", clip=False)
    calls: list[dict[str, object]] = []

    def fake_request(_args, _path, *, query=None, payload=None):
        assert payload is not None
        calls.append(payload)
        return sequence_response(payload)

    with mock.patch.object(EXTRACT, "api_request_json", side_effect=fake_request):
        EXTRACT.extract_api_sequences(args, assembly_id=ASSEMBLY["id"], plans=[plan])

    assert [len(call["segments"]) for call in calls] == [256, 1]
    assert plan.status == "OK"
    assert plan.sequence == "A" * 257


def test_api_plan_over_one_megabase_is_rejected_without_sequence_request() -> None:
    plan = EXTRACT.RecordPlan(
        query_id="large",
        strand="+",
        segments=[EXTRACT.Segment("chr1", 1, EXTRACT.MAX_API_SEQUENCE_BP + 1, "+")],
    )
    args = SimpleNamespace(mode="region", clip=False)

    with mock.patch.object(EXTRACT, "api_request_json") as request:
        EXTRACT.extract_api_sequences(args, assembly_id=ASSEMBLY["id"], plans=[plan])

    request.assert_not_called()
    assert plan.status == "FAILED"
    assert "1000000" in plan.message


def test_list_resources_can_use_api() -> None:
    stdout = io.StringIO()
    with (
        mock.patch.object(EXTRACT, "api_request_json", return_value={"assemblies": [ASSEMBLY]}),
        mock.patch.object(sys, "stdout", stdout),
    ):
        code = run_main(
            ["--mode", "list-resources", "--source", "api", "--api-base-url", "https://example.test"]
        )

    assert code == 0
    lines = stdout.getvalue().splitlines()
    assert lines[0].startswith("source\tresource_id")
    assert lines[1].startswith("Genome_browser_API\tmonoploid/DMv8.2")


def test_api_download_streams_complete_configured_genome_and_annotation(
    tmp_path, capsys
) -> None:
    output_dir = tmp_path / "downloads"
    genome_bytes = b">chr1\nACGTACGT\n"
    annotation_bytes = b"##gff-version 3\nchr1\ttest\tgene\t1\t8\t.\t+\t.\tID=gene1\n"
    calls: list[tuple[str, float]] = []

    def fake_urlopen(request, timeout):
        calls.append((request.full_url, timeout))
        if request.full_url.endswith("/genome.fa.bgz"):
            return BinaryResponse(genome_bytes)
        if request.full_url.endswith("/genes.gff3.bgz"):
            return BinaryResponse(annotation_bytes)
        raise AssertionError(request.full_url)

    with (
        mock.patch.object(
            EXTRACT,
            "api_request_json",
            return_value={"assemblies": [ASSEMBLY]},
        ),
        mock.patch.object(EXTRACT, "urlopen", side_effect=fake_urlopen),
    ):
        code = run_main(
            [
                "--mode",
                "download",
                "--source",
                "api",
                "--api-base-url",
                "https://example.test",
                "--assembly",
                "DMv8.2",
                "--output-dir",
                str(output_dir),
            ]
        )

    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["assembly"] == ASSEMBLY["id"]
    assert summary["source"] == "Genome_browser_API"
    assert [item["kind"] for item in summary["files"]] == ["genome", "annotation"]
    assert [item["bytes"] for item in summary["files"]] == [
        len(genome_bytes),
        len(annotation_bytes),
    ]
    assert (output_dir / "genome.fa.bgz").read_bytes() == genome_bytes
    assert (output_dir / "genes.gff3.bgz").read_bytes() == annotation_bytes
    assert [url for url, _timeout in calls] == [
        "https://example.test/api/genome-browser/data/monoploid/DMv8.2/reference/genome.fa.bgz",
        "https://example.test/api/genome-browser/data/monoploid/DMv8.2/annotation/genes.gff3.bgz",
    ]


def test_download_auto_uses_complete_local_configured_files_without_indexes(
    tmp_path, capsys
) -> None:
    potato_root = tmp_path / "potato"
    reference = potato_root / "configured" / "reference" / "genome.fa.bgz"
    annotation = potato_root / "configured" / "annotation" / "genes.gff3.bgz"
    reference.parent.mkdir(parents=True)
    annotation.parent.mkdir(parents=True)
    reference.write_bytes(b"configured-genome")
    annotation.write_bytes(b"configured-annotation")
    (potato_root / "assemblies.tsv").write_text(
        "id\tsample\tdisplay_name\treference\tannotation\n"
        "monoploid/DMv8.2\tDMv8.2\tDMv8.2\t"
        "configured/reference/genome.fa.bgz\t"
        "configured/annotation/genes.gff3.bgz\n",
        encoding="utf-8",
    )
    output_dir = tmp_path / "downloads"

    code = run_main(
        [
            "--mode",
            "download",
            "--source",
            "auto",
            "--assembly",
            "DMv8.2",
            "--potato-root",
            str(potato_root),
            "--other-root",
            str(tmp_path / "other"),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["source"] == "Genome_browser_DB"
    assert (output_dir / reference.name).read_bytes() == b"configured-genome"
    assert (output_dir / annotation.name).read_bytes() == b"configured-annotation"


def test_download_refuses_existing_targets_before_transfer(tmp_path, capsys) -> None:
    output_dir = tmp_path / "downloads"
    output_dir.mkdir()
    existing = output_dir / "genome.fa.bgz"
    existing.write_bytes(b"keep-existing")

    with (
        mock.patch.object(
            EXTRACT,
            "api_request_json",
            return_value={"assemblies": [ASSEMBLY]},
        ),
        mock.patch.object(EXTRACT, "urlopen") as request,
    ):
        code = run_main(
            [
                "--mode",
                "download",
                "--source",
                "api",
                "--assembly",
                "DMv8.2",
                "--output-dir",
                str(output_dir),
            ]
        )

    assert code == 1
    request.assert_not_called()
    assert existing.read_bytes() == b"keep-existing"
    assert not (output_dir / "genes.gff3.bgz").exists()
    assert "--overwrite" in capsys.readouterr().err


def test_download_refuses_symbolic_link_target_even_with_overwrite(tmp_path) -> None:
    output_dir = tmp_path / "downloads"
    output_dir.mkdir()
    outside = tmp_path / "outside.bgz"
    outside.write_bytes(b"outside-must-remain")
    (output_dir / "genome.fa.bgz").symlink_to(outside)

    with (
        mock.patch.object(
            EXTRACT,
            "api_request_json",
            return_value={"assemblies": [ASSEMBLY]},
        ),
        mock.patch.object(EXTRACT, "urlopen") as request,
    ):
        code = run_main(
            [
                "--mode",
                "download",
                "--source",
                "api",
                "--assembly",
                "DMv8.2",
                "--output-dir",
                str(output_dir),
                "--overwrite",
            ]
        )

    assert code == 1
    request.assert_not_called()
    assert outside.read_bytes() == b"outside-must-remain"


def test_download_failure_does_not_publish_partial_files(tmp_path) -> None:
    output_dir = tmp_path / "downloads"

    def fake_urlopen(request, timeout):
        if request.full_url.endswith("/genome.fa.bgz"):
            return BinaryResponse(b"complete-genome")
        return BinaryResponse(b"short", content_length=99)

    with (
        mock.patch.object(
            EXTRACT,
            "api_request_json",
            return_value={"assemblies": [ASSEMBLY]},
        ),
        mock.patch.object(EXTRACT, "urlopen", side_effect=fake_urlopen),
    ):
        code = run_main(
            [
                "--mode",
                "download",
                "--source",
                "api",
                "--assembly",
                "DMv8.2",
                "--output-dir",
                str(output_dir),
            ]
        )

    assert code == 1
    assert not (output_dir / "genome.fa.bgz").exists()
    assert not (output_dir / "genes.gff3.bgz").exists()
    assert list(output_dir.iterdir()) == []


@pytest.mark.parametrize(
    "configured_path",
    [
        "../genome.fa.bgz",
        "/absolute/genome.fa.bgz",
        "nested\\genome.fa.bgz",
        "nested//genome.fa.bgz",
        "./genome.fa.bgz",
    ],
)
def test_api_download_rejects_unsafe_configured_paths(configured_path) -> None:
    with pytest.raises(EXTRACT.ExtractionError, match="invalid configured file path"):
        EXTRACT.api_data_path(configured_path)


def test_api_download_percent_encodes_each_configured_path_segment() -> None:
    assert EXTRACT.api_data_path("monoploid/A B/reference/A+B.fa.bgz") == (
        "/api/genome-browser/data/monoploid/A%20B/reference/A%2BB.fa.bgz"
    )


def test_skill_uses_hermes_metadata_and_runtime_paths() -> None:
    skill_root = SCRIPT_PATH.parents[1]
    content = (skill_root / "SKILL.md").read_text(encoding="utf-8")

    assert content.startswith("---\n")
    assert "metadata:\n  hermes:" in content
    assert "${HERMES_SKILL_DIR}" in content
    assert "Codex" not in content
    assert "python3 scripts/" not in content
    assert not (skill_root / "agents" / "openai.yaml").exists()


def test_default_api_origin_is_fixed_to_public_https() -> None:
    with mock.patch.dict(
        "os.environ",
        {"POTATO_GENOME_BROWSER_API": "http://10.186.0.25:3000"},
    ):
        args = parse_args("--mode", "list-resources", "--source", "api")

    assert EXTRACT.DEFAULT_API_BASE_URL == "https://potato-agent.ynnu.edu.cn"
    assert args.api_base_url == "https://potato-agent.ynnu.edu.cn"


def test_api_origin_can_only_be_overridden_explicitly_for_testing() -> None:
    args = parse_args(
        "--mode", "list-resources",
        "--source", "api",
        "--api-base-url", "http://10.186.0.25:3000",
    )

    assert args.api_base_url == "http://10.186.0.25:3000"


def test_ambiguous_feature_api_error_lists_qualified_candidates() -> None:
    detail = EXTRACT.format_api_error_detail(
        409,
        {
            "detail": {
                "message": "feature alias is ambiguous",
                "candidates": [
                    {"type": "gene", "id": "gene1@chr1_hap1"},
                    {"type": "gene", "id": "gene1@chr1_hap2"},
                ],
            }
        },
    )

    assert detail == (
        "feature alias is ambiguous; use one exact candidate: "
        "gene:gene1@chr1_hap1, gene:gene1@chr1_hap2"
    )


def test_auto_prefers_complete_local_indexed_potato_assembly(tmp_path) -> None:
    potato_root = tmp_path / "potato"
    potato_root.mkdir()
    genome = potato_root / "genome.fa"
    genome.write_text(">chr1\n" + "A" * 100 + "\n", encoding="utf-8")
    write_test_fai(genome)
    (potato_root / "genes.gff3").write_text("##gff-version 3\n", encoding="utf-8")
    (potato_root / "assemblies.tsv").write_text(
        "id\tsample\tdisplay_name\treference\tannotation\n"
        "monoploid/DMv8.2\tDMv8.2\tDMv8.2\tgenome.fa\tgenes.gff3\n",
        encoding="utf-8",
    )
    write_local_feature_index(potato_root / EXTRACT.DEFAULT_FEATURE_INDEX_NAME)
    args = SimpleNamespace(
        source="auto",
        genome=None,
        annotation=None,
        resource_dir=None,
        feature_index=None,
        assembly="DMv8.2",
        mode="cds",
        potato_root=str(potato_root),
        other_root=str(tmp_path / "other"),
    )

    assert EXTRACT.effective_source(args) == "local"


def test_auto_falls_back_to_api_when_local_index_is_unavailable(tmp_path) -> None:
    potato_root = tmp_path / "potato"
    potato_root.mkdir()
    (potato_root / "genome.fa").write_text(">chr1\nAAAA\n", encoding="utf-8")
    (potato_root / "assemblies.tsv").write_text(
        "id\tsample\tdisplay_name\treference\tannotation\n"
        "monoploid/DMv8.2\tDMv8.2\tDMv8.2\tgenome.fa\tmissing.gff3\n",
        encoding="utf-8",
    )
    args = SimpleNamespace(
        source="auto",
        genome=None,
        annotation=None,
        resource_dir=None,
        feature_index=None,
        assembly="DMv8.2",
        mode="promoter",
        potato_root=str(potato_root),
        other_root=str(tmp_path / "other"),
    )

    assert EXTRACT.effective_source(args) == "api"


def test_auto_falls_back_to_api_when_local_index_provenance_is_stale(tmp_path) -> None:
    potato_root = tmp_path / "potato"
    potato_root.mkdir()
    genome = potato_root / "genome.fa"
    genome.write_text(">chr1\n" + "A" * 100 + "\n", encoding="utf-8")
    write_test_fai(genome)
    (potato_root / "genes.gff3").write_text("##gff-version 3\n", encoding="utf-8")
    (potato_root / "assemblies.tsv").write_text(
        "id\tsample\tdisplay_name\treference\tannotation\n"
        "monoploid/DMv8.2\tDMv8.2\tDMv8.2\tgenome.fa\tgenes.gff3\n",
        encoding="utf-8",
    )
    write_local_feature_index(potato_root / EXTRACT.DEFAULT_FEATURE_INDEX_NAME)
    genome.write_text(">chr1\n" + "C" * 101 + "\n", encoding="utf-8")
    args = SimpleNamespace(
        source="auto",
        genome=None,
        annotation=None,
        resource_dir=None,
        feature_index=None,
        assembly="DMv8.2",
        mode="cds",
        potato_root=str(potato_root),
        other_root=str(tmp_path / "other"),
    )

    assert EXTRACT.effective_source(args) == "api"


def test_incomplete_local_feature_index_schema_is_unavailable(tmp_path) -> None:
    index = tmp_path / "feature_index.sqlite"
    conn = sqlite3.connect(index)
    try:
        conn.execute("create table metadata(key text primary key,value text not null)")
        conn.executemany(
            "insert into metadata values(?,?)",
            EXTRACT.FEATURE_INDEX_METADATA.items(),
        )
        conn.execute(
            "create table assemblies(assembly_pk integer primary key,assembly_id text)"
        )
        conn.execute("insert into assemblies values(1,?)", (ASSEMBLY["id"],))
        conn.commit()
    finally:
        conn.close()

    assert not EXTRACT.feature_index_has_assembly(index, ASSEMBLY["id"])


def test_auto_uses_local_reference_for_coordinate_regions_without_feature_index(
    tmp_path,
) -> None:
    potato_root = tmp_path / "potato"
    potato_root.mkdir()
    genome = potato_root / "genome.fa"
    genome.write_text(">chr1\nAAAA\n", encoding="utf-8")
    write_test_fai(genome, length=4)
    (potato_root / "assemblies.tsv").write_text(
        "id\tsample\tdisplay_name\treference\tannotation\n"
        "monoploid/DMv8.2\tDMv8.2\tDMv8.2\tgenome.fa\tmissing.gff3\n",
        encoding="utf-8",
    )
    args = SimpleNamespace(
        source="auto",
        genome=None,
        annotation=None,
        resource_dir=None,
        feature_index=None,
        assembly="DMv8.2",
        mode="region",
        potato_root=str(potato_root),
        other_root=str(tmp_path / "other"),
    )

    assert EXTRACT.effective_source(args) == "local"


@pytest.mark.parametrize("fai_content", ["", "chr1\tnot-a-length\t6\t4\t5\n"])
def test_auto_region_falls_back_to_api_for_invalid_fai(
    tmp_path,
    fai_content,
) -> None:
    potato_root = tmp_path / "potato"
    potato_root.mkdir()
    genome = potato_root / "genome.fa"
    genome.write_text(">chr1\nAAAA\n", encoding="utf-8")
    Path(str(genome) + ".fai").write_text(fai_content, encoding="utf-8")
    (potato_root / "assemblies.tsv").write_text(
        "id\tsample\tdisplay_name\treference\tannotation\n"
        "monoploid/DMv8.2\tDMv8.2\tDMv8.2\tgenome.fa\tmissing.gff3\n",
        encoding="utf-8",
    )
    args = SimpleNamespace(
        source="auto",
        genome=None,
        annotation=None,
        resource_dir=None,
        feature_index=None,
        assembly="DMv8.2",
        mode="region",
        potato_root=str(potato_root),
        other_root=str(tmp_path / "other"),
    )

    assert EXTRACT.effective_source(args) == "api"


def test_auto_region_falls_back_to_api_for_corrupt_gzi(tmp_path) -> None:
    potato_root = tmp_path / "potato"
    potato_root.mkdir()
    genome = potato_root / "genome.fa.bgz"
    genome.write_text(">chr1\nAAAA\n", encoding="utf-8")
    write_test_fai(genome, length=4)
    Path(str(genome) + ".gzi").write_bytes(b"corrupt")
    (potato_root / "assemblies.tsv").write_text(
        "id\tsample\tdisplay_name\treference\tannotation\n"
        "monoploid/DMv8.2\tDMv8.2\tDMv8.2\tgenome.fa.bgz\tmissing.gff3\n",
        encoding="utf-8",
    )
    args = SimpleNamespace(
        source="auto",
        genome=None,
        annotation=None,
        resource_dir=None,
        feature_index=None,
        assembly="DMv8.2",
        mode="region",
        potato_root=str(potato_root),
        other_root=str(tmp_path / "other"),
    )

    assert EXTRACT.effective_source(args) == "api"


@pytest.mark.parametrize("resource_flag", ["--genome", "--annotation", "--resource-dir"])
def test_feature_index_cannot_be_combined_with_explicit_resources(
    tmp_path,
    resource_flag,
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        parse_args(
            "--mode", "region",
            "--source", "local",
            resource_flag, str(tmp_path / "resource"),
            "--feature-index", str(tmp_path / "feature_index.sqlite"),
            "--seqid", "chr1",
            "--start", "1",
            "--end", "4",
            "--output", str(tmp_path / "region.fa"),
            "--report", str(tmp_path / "region.tsv"),
        )

    assert exc_info.value.code == 2


def test_explicit_annotation_disables_the_implicit_canonical_index(tmp_path) -> None:
    potato_root = tmp_path / "potato"
    potato_root.mkdir()
    genome = potato_root / "genome.fa"
    genome.write_text(">chr1\nAAAA\n", encoding="utf-8")
    manifest_annotation = potato_root / "manifest.gff3"
    manifest_annotation.write_text("##gff-version 3\n", encoding="utf-8")
    custom_annotation = tmp_path / "custom.gff3"
    custom_annotation.write_text("##gff-version 3\n", encoding="utf-8")
    (potato_root / "assemblies.tsv").write_text(
        "id\tsample\tdisplay_name\treference\tannotation\n"
        "monoploid/DMv8.2\tDMv8.2\tDMv8.2\tgenome.fa\tmanifest.gff3\n",
        encoding="utf-8",
    )
    write_local_feature_index(potato_root / EXTRACT.DEFAULT_FEATURE_INDEX_NAME)
    args = SimpleNamespace(
        assembly="DMv8.2",
        resource_dir=None,
        genome=None,
        annotation=str(custom_annotation),
        feature_index=None,
        potato_root=str(potato_root),
        other_root=str(tmp_path / "other"),
        mode="cds",
    )

    resources = EXTRACT.resolve_resources(args)

    assert resources["annotation"] == str(custom_annotation)
    assert resources["feature_index"] == ""


def test_forced_local_canonical_feature_mode_requires_index_or_explicit_annotation(
    tmp_path,
) -> None:
    potato_root = tmp_path / "potato"
    potato_root.mkdir()
    (potato_root / "genome.fa").write_text(">chr1\nAAAA\n", encoding="utf-8")
    (potato_root / "genes.gff3").write_text("##gff-version 3\n", encoding="utf-8")
    (potato_root / "assemblies.tsv").write_text(
        "id\tsample\tdisplay_name\treference\tannotation\n"
        "monoploid/DMv8.2\tDMv8.2\tDMv8.2\tgenome.fa\tgenes.gff3\n",
        encoding="utf-8",
    )
    args = SimpleNamespace(
        assembly="DMv8.2",
        resource_dir=None,
        genome=None,
        annotation=None,
        feature_index=None,
        potato_root=str(potato_root),
        other_root=str(tmp_path / "other"),
        mode="cds",
    )

    try:
        EXTRACT.resolve_resources(args)
    except EXTRACT.ExtractionError as exc:
        assert "requires a readable local feature index" in str(exc)
    else:
        raise AssertionError("missing local canonical feature index was accepted")


def test_local_index_preserves_representative_selection_and_extracts_local_fasta(
    tmp_path,
) -> None:
    potato_root = tmp_path / "potato"
    potato_root.mkdir()
    bases = ["A"] * 100
    bases[19:22] = list("ATG")
    bases[47:50] = list("TAA")
    (potato_root / "genome.fa").write_text(
        ">chr1\n" + "".join(bases) + "\n",
        encoding="utf-8",
    )
    write_test_fai(potato_root / "genome.fa")
    (potato_root / "genes.gff3").write_text("##gff-version 3\n", encoding="utf-8")
    (potato_root / "assemblies.tsv").write_text(
        "id\tsample\tdisplay_name\treference\tannotation\n"
        "monoploid/DMv8.2\tDMv8.2\tDMv8.2\tgenome.fa\tgenes.gff3\n",
        encoding="utf-8",
    )
    write_local_feature_index(potato_root / EXTRACT.DEFAULT_FEATURE_INDEX_NAME)
    output = tmp_path / "gene1.cds.fa"
    report = tmp_path / "gene1.cds.tsv"

    code = run_main(
        [
            "--mode", "cds", "--source", "auto",
            "--potato-root", str(potato_root),
            "--other-root", str(tmp_path / "other"),
            "--assembly", "DMv8.2", "--id", "gene1",
            "--isoform", "representative",
            "--output", str(output), "--report", str(report),
        ]
    )

    assert code == 0
    assert output.read_text(encoding="utf-8").splitlines()[-1] == "ATGTAA"
    row = read_report(report)[0]
    assert row["extraction_backend"] == "local"
    assert row["transcript_id"] == "tx1"
    assert row["representative_source"] == "mapping"
    metadata = json.loads(Path(str(output) + ".meta.json").read_text(encoding="utf-8"))
    assert metadata["resources"]["feature_resolution_backend"] == "local_feature_index"


def test_local_index_reports_ambiguous_raw_id_and_accepts_qualified_id(tmp_path) -> None:
    index = tmp_path / "feature_index.sqlite"
    write_local_feature_index(index, ambiguous_alias=True)
    conn = EXTRACT.connect_local_feature_index(index)
    args = SimpleNamespace(mode="gene", isoform="error", upstream=0, downstream=0)
    try:
        ambiguous = EXTRACT.build_api_plans(
            args,
            assembly_id=ASSEMBLY["id"],
            queries=["geneA"],
            resolve_payload=lambda query_id: EXTRACT.resolve_local_feature(
                conn,
                assembly_id=ASSEMBLY["id"],
                query_id=query_id,
            ),
        )
        qualified = EXTRACT.build_api_plans(
            args,
            assembly_id=ASSEMBLY["id"],
            queries=["geneA@chr2"],
            resolve_payload=lambda query_id: EXTRACT.resolve_local_feature(
                conn,
                assembly_id=ASSEMBLY["id"],
                query_id=query_id,
            ),
        )
    finally:
        conn.close()

    assert ambiguous[0].status == "FAILED"
    assert "gene:geneA@chr1" in ambiguous[0].message
    assert "gene:geneA@chr2" in ambiguous[0].message
    assert qualified[0].status == "PLANNED"
    assert qualified[0].resolved_id == "geneA@chr2"


def test_local_resolver_matches_backend_resolver_payload(tmp_path) -> None:
    index = tmp_path / "feature_index.sqlite"
    write_local_feature_index(index)
    conn = EXTRACT.connect_local_feature_index(index)
    try:
        local_payload = EXTRACT.resolve_local_feature(
            conn,
            assembly_id=ASSEMBLY["id"],
            query_id="gene1",
        )
    finally:
        conn.close()

    backend_payload = BACKEND_INDEX.resolve_feature(
        index,
        assembly_id=ASSEMBLY["id"],
        query_id="gene1",
    )

    assert local_payload == backend_payload


def test_local_region_main_remains_operational(tmp_path) -> None:
    genome = tmp_path / "genome.fa"
    genome.write_text(">chr1\nACGTACGTACGT\n", encoding="utf-8")
    output = tmp_path / "region.fa"
    report = tmp_path / "region.tsv"

    code = run_main(
        [
            "--mode", "region", "--source", "local", "--genome", str(genome),
            "--seqid", "chr1", "--start", "3", "--end", "8", "--strand", "-",
            "--output", str(output), "--report", str(report),
        ]
    )

    assert code == 0
    assert output.read_text(encoding="utf-8").splitlines()[-1] == "ACGTAC"
    row = read_report(report)[0]
    assert row["extraction_backend"] == "local"
    assert row["status"] == "OK"


def test_api_errors_and_response_identity_mismatches_fail_the_plan() -> None:
    args = SimpleNamespace(mode="region", clip=False)

    error_plan = EXTRACT.RecordPlan(
        query_id="error", strand="+", segments=[EXTRACT.Segment("chr1", 1, 3, "+")]
    )
    with mock.patch.object(
        EXTRACT, "api_request_json", side_effect=EXTRACT.ExtractionError("service busy")
    ):
        EXTRACT.extract_api_sequences(args, assembly_id=ASSEMBLY["id"], plans=[error_plan])
    assert error_plan.status == "FAILED"
    assert error_plan.message == "service busy"

    mutations = (
        ("name", lambda record: record.update(name="wrong")),
        ("refName", lambda record: record.update(refName="chr2")),
        ("requested coordinates", lambda record: record.update(requestedStart=2)),
        ("actual coordinates", lambda record: record.update(start=2)),
    )
    for label, mutate in mutations:
        plan = EXTRACT.RecordPlan(
            query_id=label, strand="+", segments=[EXTRACT.Segment("chr1", 1, 3, "+")]
        )

        def invalid_response(_args, _path, *, query=None, payload=None):
            response = sequence_response(payload)
            mutate(response["records"][0])
            return response

        with mock.patch.object(EXTRACT, "api_request_json", side_effect=invalid_response):
            EXTRACT.extract_api_sequences(args, assembly_id=ASSEMBLY["id"], plans=[plan])
        assert plan.status == "FAILED", label
