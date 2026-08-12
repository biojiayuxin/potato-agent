from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import urllib.parse
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = (
    REPO_ROOT / "skills/potato-knowledge-bioinformatics/potato-pan-genome-query"
)
SCRIPT_PATH = SKILL_ROOT / "scripts/query_pan_genome.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("query_pan_genome", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def test_pan_genome_skill_uses_hermes_frontmatter_and_skill_dir() -> None:
    content = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    assert content.startswith("---\n")
    assert "name: potato-pan-genome-query" in content
    assert "metadata:\n  hermes:" in content
    assert "${HERMES_SKILL_DIR}" in content
    assert "https://potato-agent.ynnu.edu.cn" in content
    assert "Codex" not in content


def test_pan_genome_skill_defaults_to_public_api(monkeypatch) -> None:
    script = _load_script()
    monkeypatch.delenv("POTATO_PAN_GENOME_BASE_URL", raising=False)

    args = script.build_parser().parse_args(["metadata"])

    assert script.DEFAULT_BASE_URL == "https://potato-agent.ynnu.edu.cn"
    assert args.base_url == script.DEFAULT_BASE_URL
    assert (
        script.api_root(args.base_url)
        == "https://potato-agent.ynnu.edu.cn/api/pan-genome"
    )


def test_pan_genome_skill_base_url_override_precedence(monkeypatch) -> None:
    script = _load_script()
    monkeypatch.setenv(
        "POTATO_PAN_GENOME_BASE_URL", "https://pan-genome-mirror.example"
    )

    from_environment = script.build_parser().parse_args(["metadata"])
    from_command_line = script.build_parser().parse_args(
        ["metadata", "--base-url", "https://explicit.example"]
    )

    assert from_environment.base_url == "https://pan-genome-mirror.example"
    assert from_command_line.base_url == "https://explicit.example"


def test_pan_genome_skill_api_root_accepts_site_and_api_urls() -> None:
    script = _load_script()
    assert (
        script.api_root("http://127.0.0.1:3000")
        == "http://127.0.0.1:3000/api/pan-genome"
    )
    assert (
        script.api_root("https://potato-agent.ynnu.edu.cn/api/pan-genome/")
        == "https://potato-agent.ynnu.edu.cn/api/pan-genome"
    )


def test_pan_genome_skill_gene_command_encodes_exact_query(monkeypatch) -> None:
    script = _load_script()
    response = FakeResponse(
        {"matches": [{"genome": "DMv8_2", "geneId": "Gene A", "orthogroup": "OG1"}]}
    )
    calls: list[tuple[object, int]] = []

    def fake_urlopen(request, timeout):
        calls.append((request, timeout))
        return response

    monkeypatch.setattr(script.urllib.request, "urlopen", fake_urlopen)
    args = script.build_parser().parse_args(
        [
            "gene",
            "Gene A",
            "--genome",
            "DMv8_2",
            "--base-url",
            "https://example.test",
            "--timeout",
            "12",
        ]
    )
    url, payload = script.run_gene(args)

    assert payload["matches"][0]["orthogroup"] == "OG1"
    assert len(calls) == 1
    assert calls[0][1] == 12
    assert calls[0][0].full_url == url
    parsed = urllib.parse.urlsplit(url)
    assert parsed.path == "/api/pan-genome/genes/lookup"
    assert urllib.parse.parse_qs(parsed.query) == {
        "gene_id": ["Gene A"],
        "genome": ["DMv8_2"],
    }


def test_pan_genome_skill_members_maps_pagination(monkeypatch) -> None:
    script = _load_script()
    args = script.build_parser().parse_args(
        [
            "members",
            "OG0001",
            "--genome",
            "GenomeA",
            "--limit",
            "250",
            "--offset",
            "500",
        ]
    )
    captured: dict[str, object] = {}

    def fake_request(base_url, endpoint, *, params, timeout):
        captured.update(
            {
                "base_url": base_url,
                "endpoint": endpoint,
                "params": params,
                "timeout": timeout,
            }
        )
        return "http://example.test", {"members": []}

    monkeypatch.setattr(script, "request_json", fake_request)
    script.run_members(args)

    assert captured == {
        "base_url": script.DEFAULT_BASE_URL,
        "endpoint": "orthogroups/OG0001/members",
        "params": {
            "limit": 250,
            "offset": 500,
            "genome": "GenomeA",
        },
        "timeout": script.DEFAULT_TIMEOUT,
    }


def test_pan_genome_skill_orthogroups_maps_category_and_pagination(
    monkeypatch,
) -> None:
    script = _load_script()
    args = script.build_parser().parse_args(
        [
            "orthogroups",
            "--category",
            "soft-core",
            "--limit",
            "250",
            "--offset",
            "500",
        ]
    )
    captured: dict[str, object] = {}

    def fake_request(base_url, endpoint, *, params, timeout):
        captured.update(
            {
                "base_url": base_url,
                "endpoint": endpoint,
                "params": params,
                "timeout": timeout,
            }
        )
        return "https://example.test", {"orthogroups": []}

    monkeypatch.setattr(script, "request_json", fake_request)
    script.run_orthogroups(args)

    assert captured == {
        "base_url": "https://potato-agent.ynnu.edu.cn",
        "endpoint": "orthogroups",
        "params": {
            "limit": 250,
            "offset": 500,
            "category": "soft-core",
        },
        "timeout": script.DEFAULT_TIMEOUT,
    }


def test_pan_genome_skill_orthogroups_help_explains_page_limit() -> None:
    script = _load_script()
    output = io.StringIO()
    with contextlib.redirect_stdout(output), pytest.raises(SystemExit) as exc_info:
        script.build_parser().parse_args(["orthogroups", "--help"])

    assert exc_info.value.code == 0
    help_text = " ".join(output.getvalue().split())
    assert "limit agent context size" in help_text
    assert "not the category's total count" in help_text
    assert "pagination.total" in help_text


def test_pan_genome_skill_main_emits_api_url_and_data(monkeypatch, capsys) -> None:
    script = _load_script()
    monkeypatch.setattr(
        script,
        "request_json",
        lambda *args, **kwargs: (
            "https://potato-agent.ynnu.edu.cn/api/pan-genome/metadata",
            {"datasetVersion": "fixture-v1"},
        ),
    )

    assert script.main(["metadata", "--indent", "0"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "api_url": "https://potato-agent.ynnu.edu.cn/api/pan-genome/metadata",
        "data": {"datasetVersion": "fixture-v1"},
    }
