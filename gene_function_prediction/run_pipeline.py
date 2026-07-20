#!/usr/bin/env python3
"""Script-controlled DMv8.2 gene-function evidence and prediction pipeline."""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import re
import statistics
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Any, Callable

if __package__:
    from .llm_client import LLMError, ResponsesClient, ResponsesConfig
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from llm_client import LLMError, ResponsesClient, ResponsesConfig


HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
SKILLS_ROOT = REPO_ROOT / "skills" / "potato-knowledge-bioinformatics"
PROMPT_DIR = HERE / "prompts"

DEFAULT_HOMOLOG_DIR = Path("/mnt/data/ref_homlogs")
DEFAULT_EXPRESSION_MATRIX = Path(
    "/mnt/data/public_data/Expression_atlas/DMv8.2/transcript_tpm_matrix_merged.tsv"
)
DEFAULT_EXPRESSION_METADATA = Path(
    "/mnt/data/public_data/Expression_atlas/DMv8.2/sample_tissue_list.tsv"
)
DEFAULT_MAIZE_DATA = Path(
    "/mnt/data/public_data/Genomes/Other_species/Maize/Zm00001eb.1.fulldata.txt"
)
RICE_COLUMNS = [
    "GeneID",
    "基因名称或注释",
    "基因符号",
    "RAP_Locus",
    "MSU_Locus或其它",
    "NCBI_Locus",
    "cDNAs",
    "RefSeq_Locus",
    "Uniprots",
]

GRADES = {"F1", "F2", "F3", "F4", "U"}

EVIDENCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "source_species": {"type": "string"},
        "query_gene": {"type": "string"},
        "resolved_gene_name": {"type": "string"},
        "function_summary": {"type": "string"},
        "citations": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "source_species",
        "query_gene",
        "resolved_gene_name",
        "function_summary",
        "citations",
    ],
}

ARABIDOPSIS_GENE_NAMES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "gene_names": {
            "type": "array",
            "items": {"type": "string"},
            "uniqueItems": True,
        }
    },
    "required": ["gene_names"],
}

PREDICTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "potato_gene_id": {"type": "string"},
        "predicted_function": {"type": "string"},
        "reliability_grade": {"type": "string", "enum": sorted(GRADES)},
        "grade_reason": {"type": "string"},
    },
    "required": [
        "potato_gene_id",
        "predicted_function",
        "reliability_grade",
        "grade_reason",
    ],
}


class SourceError(RuntimeError):
    """Raised when a required external command or HTTP query fails."""


@dataclass(frozen=True)
class Homolog:
    target_gene_id: str
    level: str


def read_gene_list(path: Path) -> list[str]:
    genes: list[str] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            value = line.strip().split("\t", 1)[0]
            if not value or value.startswith("#") or value.lower() in {"gene", "gene_id", "potato_gene_id"}:
                continue
            if value not in seen:
                seen.add(value)
                genes.append(value)
    if not genes:
        raise ValueError(f"No gene IDs found in {path}")
    return genes


def extract_gene_names(value: object) -> list[str]:
    text = clean_text(value)
    if not text:
        return []
    names: list[str] = []
    seen: set[str] = set()
    for token in re.split(r"[,;|]", text):
        for component in token.split("/"):
            name = component.strip(" \"'")
            if not name:
                continue
            name = re.sub(r"-(?:a|b|c|d)$", "", name, flags=re.IGNORECASE).strip()
            key = unicodedata.normalize("NFKC", name).casefold()
            if name and key not in seen:
                seen.add(key)
                names.append(name)
    return names


def clean_text(value: object, max_chars: int = 0) -> str:
    if value is None:
        return ""
    text = html.unescape(str(value)).replace("\xa0", " ")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"买抗体|买突变体", "", text)
    lines = []
    for line in text.replace("\r", "\n").split("\n"):
        compact = re.sub(r"\s+", " ", line).strip()
        if not compact or re.search(
            r"^(copyright|acknowledg|author contributions?|funding|data availability|conflicts? of interest)\b",
            compact,
            flags=re.IGNORECASE,
        ):
            continue
        lines.append(compact)
    result = " ".join(lines).strip()
    if result.lower() in {"nan", "none", "-"}:
        return ""
    if max_chars and len(result) > max_chars:
        return result[: max_chars - 3].rstrip() + "..."
    return result


def sha256_json(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp.{os.getpid()}.{uuid.uuid4().hex}")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def safe_key(value: str) -> str:
    prefix = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")[:80] or "item"
    return f"{prefix}.{hashlib.sha256(value.encode('utf-8')).hexdigest()[:12]}"


def load_homologs(path: Path, target_column: str) -> dict[str, Homolog]:
    with path.open(encoding="utf-8") as handle:
        rows = csv.DictReader((line for line in handle if not line.startswith("#")), delimiter="\t")
        required = {"Potato_gene", target_column, "Evidence_level"}
        if not required.issubset(rows.fieldnames or []):
            raise ValueError(f"Unexpected homolog columns in {path}: {rows.fieldnames}")
        result: dict[str, Homolog] = {}
        for row in rows:
            gene = clean_text(row["Potato_gene"])
            homolog = Homolog(clean_text(row[target_column]), clean_text(row["Evidence_level"]).upper())
            if not gene or not homolog.target_gene_id or homolog.level not in {"L1", "L2", "L3"}:
                raise ValueError(f"Invalid homolog row for {gene} in {path}")
            if gene in result:
                raise ValueError(f"Duplicate potato gene in {path}: {gene}")
            result[gene] = homolog
    return result


def load_maize_index(path: Path) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            fields = line.rstrip("\n").split("\t")
            if len(fields) != 14:
                raise ValueError(f"Expected 14 maize fields at {path}:{line_number}")
            result[fields[1]] = {
                "gene_id": fields[1],
                "gene_name": clean_text(fields[10]),
                "long_name": clean_text(fields[11]),
                "description": clean_text(fields[12], 1200),
                "go_terms": clean_text(fields[13], 1800),
                "source_file": str(path.resolve()),
                "source_line": str(line_number),
            }
    return result


def run_json_command(
    command: list[str],
    timeout: int | float,
    attempts: int = 2,
    accepted_returncodes: set[int] | None = None,
) -> dict[str, Any]:
    accepted_returncodes = accepted_returncodes or {0}
    error = "command did not run"
    for attempt in range(attempts):
        try:
            process = subprocess.run(
                command,
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
            try:
                data = json.loads(process.stdout)
            except json.JSONDecodeError:
                data = None
            if process.returncode in accepted_returncodes and isinstance(data, dict):
                return data
            if isinstance(data, dict) and data.get("error"):
                error = clean_text(data.get("error"), 1000)
            else:
                error = clean_text(process.stderr or process.stdout, 1000) or f"exit {process.returncode}"
        except subprocess.TimeoutExpired:
            error = f"timeout after {timeout}s"
        if attempt + 1 < attempts:
            time.sleep(1.0 + attempt)
    raise SourceError(f"Command failed ({' '.join(command[:3])}): {error}")


def fetch_ricedata(target: str, timeout: int, retries: int = 3) -> dict[str, Any]:
    if retries < 0:
        raise ValueError("RiceData retries must be zero or greater")
    params = {
        "para": re.sub(r"\.\d+$", "", target),
        "genenm": "",
        "cloned": "false",
        "located": "false",
        "chro": "",
    }
    url = "https://www.ricedata.cn/gene/accessions_switch.aspx?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8", "replace")
            break
        except Exception as exc:
            if attempt >= retries:
                raise SourceError(
                    f"RiceData request failed after {retries + 1} attempts: {exc}"
                ) from exc
            time.sleep(1.0 + attempt)
    try:
        import pandas as pd
    except ImportError as exc:
        raise SourceError("pandas and lxml are required to parse RiceData") from exc
    try:
        tables = pd.read_html(StringIO(body))
    except Exception as exc:
        raise SourceError(f"RiceData HTML parsing failed: {exc}") from exc

    candidates: list[dict[str, str]] = []
    for table in tables:
        columns = [_flatten_column(column) for column in table.columns]
        if len(columns) == len(RICE_COLUMNS) and all(column.isdigit() for column in columns):
            columns = RICE_COLUMNS
        for values in table.itertuples(index=False, name=None):
            row = {columns[index]: clean_text(value, 1200) for index, value in enumerate(values)}
            if any(row.values()):
                candidates.append(row)
    normalized_target = re.sub(r"\.\d+$", "", target).casefold()
    exact = [
        row
        for row in candidates
        if any(normalized_target == token.casefold() for value in row.values() for token in _id_tokens(value))
    ]
    status = "matched" if exact else "not_found"
    return {
        "query_gene": target,
        "url": url,
        "status": status,
        "exact_matches": exact,
        "candidates": candidates[:5],
    }


def _flatten_column(value: object) -> str:
    parts = value if isinstance(value, tuple) else (value,)
    cleaned = [clean_text(part) for part in parts if "unnamed" not in str(part).lower()]
    return " / ".join(part for part in cleaned if part) or "column"


def _id_tokens(value: str) -> list[str]:
    return [token for token in re.split(r"[\s,;|/]+", value) if token]


def rice_gene_symbols(record: dict[str, Any]) -> list[str]:
    matches = record.get("exact_matches")
    if not isinstance(matches, list):
        return []
    symbols: list[str] = []
    seen: set[str] = set()
    for match in matches:
        if not isinstance(match, dict):
            continue
        for key, value in match.items():
            key_lower = key.casefold()
            if (
                "基因符号" not in key
                and "gene symbol" not in key_lower
                and not key_lower.endswith("symbol")
            ):
                continue
            symbol = clean_text(value)
            normalized = unicodedata.normalize("NFKC", symbol).casefold()
            if symbol and normalized not in seen:
                seen.add(normalized)
                symbols.append(symbol)
    return symbols


def validate_rice_source(record: dict[str, Any]) -> None:
    status = clean_text(record.get("status"))
    if status not in {"matched", "not_found"}:
        raise SourceError(f"RiceData returned status={status or 'missing'}")
    matches = record.get("exact_matches")
    if not isinstance(matches, list) or not all(isinstance(match, dict) for match in matches):
        raise SourceError("RiceData returned malformed exact matches")


def validate_potato_gene_source(record: dict[str, Any]) -> None:
    if not isinstance(record.get("results"), list):
        raise SourceError("potato gene search returned malformed results")


def validate_potato_rag_source(record: dict[str, Any]) -> None:
    rag = record.get("rag") if isinstance(record.get("rag"), dict) else {}
    if not record.get("success") or not rag.get("success"):
        raise SourceError(clean_text(rag.get("error") or record.get("warnings"), 800))
    if not isinstance(rag.get("results"), list):
        raise SourceError("potato RAG returned malformed results")


def summarize_expression(
    matrix_path: Path,
    metadata_path: Path,
    genes: list[str],
) -> dict[str, list[dict[str, Any]]]:
    with metadata_path.open(encoding="utf-8") as handle:
        metadata = list(csv.DictReader(handle, delimiter="\t"))
    required = {"sample_column", "sample_name", "tissue"}
    if not metadata or not required.issubset(metadata[0]):
        raise ValueError(f"Expression metadata must contain {sorted(required)}")

    with matrix_path.open(encoding="utf-8") as handle:
        header = handle.readline().rstrip("\n").split("\t")
        if header[:3] != ["transcript_id", "gene_id", "gene_name"]:
            raise ValueError("Unexpected expression matrix columns")
        sample_columns = header[3:]
        if sample_columns != [row["sample_column"] for row in metadata]:
            raise ValueError("Expression matrix and metadata sample columns differ")
        wanted = set(genes)
        values_by_gene: dict[str, list[float]] = {}
        for line in handle:
            fields = line.rstrip("\n").split("\t")
            matched_genes = wanted.intersection(fields[:2])
            if not matched_genes:
                continue
            values = [float(value or 0.0) for value in fields[3:]]
            for gene in matched_genes:
                if gene in values_by_gene:
                    values_by_gene[gene] = [left + right for left, right in zip(values_by_gene[gene], values)]
                else:
                    values_by_gene[gene] = values.copy()

    groups: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for index, row in enumerate(metadata):
        groups[clean_text(row["tissue"])][clean_text(row["sample_name"])].append(index)

    output: dict[str, list[dict[str, Any]]] = {}
    for gene in genes:
        values = values_by_gene.get(gene)
        if values is None:
            output[gene] = []
            continue
        tissue_rows: list[dict[str, Any]] = []
        for tissue, sample_groups in groups.items():
            group_means = [statistics.fmean(values[index] for index in indices) for indices in sample_groups.values()]
            mean = statistics.fmean(group_means)
            sd = statistics.stdev(group_means) if len(group_means) > 1 else 0.0
            tissue_rows.append(
                {
                    "tissue": tissue,
                    "mean_tpm": round(mean, 6),
                    "sd_tpm": round(sd, 6),
                    "n_sources": len(group_means),
                    "n_runs": sum(len(indices) for indices in sample_groups.values()),
                }
            )
        output[gene] = sorted(tissue_rows, key=lambda row: (-row["mean_tpm"], row["tissue"]))
    return output


def compact_pubmed(data: dict[str, Any], max_abstract_chars: int = 1800) -> list[dict[str, Any]]:
    papers = data.get("data")
    if not isinstance(papers, list):
        return []
    output = []
    for paper in papers:
        if not isinstance(paper, dict):
            continue
        output.append(
            {
                "pmid": clean_text(paper.get("pmid") or paper.get("id")),
                "doi": clean_text(paper.get("doi")),
                "title": clean_text(paper.get("title"), 500),
                "year": paper.get("year"),
                "abstract": clean_text(paper.get("abstract"), max_abstract_chars),
            }
        )
    return output


def validate_arabidopsis_tair_source(data: dict[str, Any], target: str) -> None:
    source_status = clean_text(data.get("status")).casefold()
    if source_status != "ok":
        raise SourceError(
            f"TAIR returned status={source_status or 'missing'}"
        )
    tair = data.get("tair")
    selected = tair.get("selected") if isinstance(tair, dict) else None
    selected_gene = clean_text(selected.get("gene_id")) if isinstance(selected, dict) else ""
    if selected_gene.casefold() != re.sub(r"\.\d+$", "", target).casefold():
        raise SourceError(
            f"TAIR did not resolve {target} to the expected AGI ID (got {selected_gene or 'none'})"
        )


def validate_plantconnectome_source(data: dict[str, Any]) -> None:
    source_status = clean_text(data.get("status")).casefold()
    if source_status not in {"ok", "not_found"}:
        raise SourceError(
            f"PlantConnectome source returned status={source_status or 'missing'}"
        )
    plantconnectome = data.get("plantconnectome")
    if not isinstance(plantconnectome, dict):
        raise SourceError("PlantConnectome result is missing")
    plant_status = clean_text(plantconnectome.get("status")).casefold()
    if plant_status not in {"ok", "not_found"}:
        raise SourceError(
            f"PlantConnectome returned status={plant_status or 'missing'}"
        )
    preview = plantconnectome.get("preview")
    entities = plantconnectome.get("entities")
    if not isinstance(preview, dict) or not isinstance(entities, list):
        raise SourceError("PlantConnectome preview or entity results are malformed")
    row_count = preview.get("row_count")
    if isinstance(row_count, int) and row_count > 0 and not entities:
        raise SourceError("PlantConnectome returned preview rows but no entity detail")
    failed_entities = [
        entity
        for entity in entities
        if isinstance(entity, dict) and clean_text(entity.get("error"))
    ]
    if failed_entities:
        raise SourceError(
            "PlantConnectome entity detail failed: "
            + clean_text(failed_entities[0].get("error"), 800)
        )


def arabidopsis_gene_name_candidates(selected: object) -> list[str]:
    if not isinstance(selected, dict):
        return []
    values = selected.get("other_names")
    if not isinstance(values, list):
        return []
    names: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        name = clean_text(value)
        key = unicodedata.normalize("NFKC", name).casefold()
        if name and key not in seen:
            seen.add(key)
            names.append(name)
    return names


def validate_arabidopsis_gene_names(
    data: dict[str, Any], candidates: list[str]
) -> dict[str, Any]:
    if not isinstance(data, dict) or set(data) != {"gene_names"}:
        raise ValueError("Arabidopsis gene-name response must contain only gene_names")
    values = data.get("gene_names")
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise ValueError("Arabidopsis gene_names must be an array of strings")

    candidate_by_key = {
        unicodedata.normalize("NFKC", candidate).casefold(): candidate
        for candidate in candidates
    }
    names: list[str] = []
    seen: set[str] = set()
    for value in values:
        name = clean_text(value)
        key = unicodedata.normalize("NFKC", name).casefold()
        if not name or key not in candidate_by_key:
            raise ValueError(f"Arabidopsis gene-name response selected an unknown name: {value!r}")
        if key not in seen:
            seen.add(key)
            names.append(candidate_by_key[key])
    return {"gene_names": names}


def validate_arabidopsis_source(data: dict[str, Any], target: str) -> None:
    validate_arabidopsis_tair_source({"status": "ok", "tair": data.get("tair")}, target)
    searches = data.get("plantconnectome_searches")
    if not isinstance(searches, list):
        raise SourceError("Arabidopsis PlantConnectome searches are malformed")
    for search in searches:
        if not isinstance(search, dict) or not clean_text(search.get("gene_name")):
            raise SourceError("Arabidopsis PlantConnectome search is malformed")
        validate_plantconnectome_source(
            {
                "status": search.get("status"),
                "plantconnectome": search.get("plantconnectome"),
            }
        )


def compact_arabidopsis(data: dict[str, Any]) -> dict[str, Any]:
    tair = data.get("tair") if isinstance(data.get("tair"), dict) else {}
    selected = tair.get("selected") if isinstance(tair.get("selected"), dict) else None
    compact_searches: list[dict[str, Any]] = []
    for search in data.get("plantconnectome_searches") or []:
        if not isinstance(search, dict):
            continue
        pc = search.get("plantconnectome")
        if not isinstance(pc, dict):
            pc = {}
        relationships: list[dict[str, str]] = []
        for entity in pc.get("entities") or []:
            if not isinstance(entity, dict):
                continue
            for edge in entity.get("edges") or []:
                if not isinstance(edge, dict):
                    continue
                relationships.append(
                    {
                        "entity_1": clean_text(
                            edge.get("entity1") or edge.get("id")
                        ),
                        "relationship": clean_text(
                            edge.get("inter_type") or edge.get("edge_disamb")
                        ),
                        "entity_2": clean_text(
                            edge.get("entity2") or edge.get("target")
                        ),
                        "citation": clean_text(edge.get("publication")),
                    }
                )
        compact_searches.append(
            {
                "gene_name": clean_text(search.get("gene_name")),
                "relationships": relationships,
            }
        )
    return {
        "tair_selected": selected,
        "plantconnectome_searches": compact_searches,
    }


def validate_evidence_summary(
    data: dict[str, Any], gene: str, allowed_citations: set[str]
) -> dict[str, Any]:
    required = set(EVIDENCE_SCHEMA["required"])
    if not required.issubset(data):
        raise ValueError(f"Evidence summary missing fields: {sorted(required - set(data))}")
    for field in required - {"citations"}:
        if not isinstance(data[field], str):
            raise ValueError(f"Evidence summary {field} must be a string")
    if data["query_gene"].casefold() != gene.casefold():
        raise ValueError("Evidence summary changed query_gene")
    citations = data["citations"]
    if not isinstance(citations, list) or not all(isinstance(value, str) for value in citations):
        raise ValueError("Evidence summary citations must be an array of strings")
    unknown_citations = [value for value in citations if _citation_key(value) not in allowed_citations]
    if unknown_citations:
        raise ValueError(f"Evidence summary invented citations: {unknown_citations}")
    return {
        "source_species": data["source_species"],
        "query_gene": data["query_gene"],
        "resolved_gene_name": data["resolved_gene_name"],
        "function_summary": data["function_summary"],
        "citations": data["citations"],
    }


def validate_prediction(data: dict[str, Any], gene: str) -> dict[str, Any]:
    required = set(PREDICTION_SCHEMA["required"])
    if not required.issubset(data):
        raise ValueError(f"Prediction missing fields: {sorted(required - set(data))}")
    for field in required:
        if not isinstance(data[field], str):
            raise ValueError(f"Prediction {field} must be a string")
    if data["potato_gene_id"] != gene:
        raise ValueError("Prediction changed potato_gene_id")
    if data["reliability_grade"] not in GRADES:
        raise ValueError(f"Invalid reliability grade: {data['reliability_grade']}")
    return {
        "potato_gene_id": data["potato_gene_id"],
        "predicted_function": data["predicted_function"],
        "reliability_grade": data["reliability_grade"],
        "grade_reason": data["grade_reason"],
    }


def evidence_citations(value: object) -> set[str]:
    citations: set[str] = set()

    def visit(item: object, parent_key: str = "") -> None:
        if isinstance(item, dict):
            for key, nested in item.items():
                visit(nested, key.casefold())
        elif isinstance(item, list):
            for nested in item:
                visit(nested, parent_key)
        elif parent_key in {"pmid", "publication", "citation", "doi", "citations"}:
            text = clean_text(item)
            if not text:
                return
            citations.add(_citation_key(text))
            if parent_key in {"pmid", "publication", "citation"}:
                citations.add(_citation_key(f"PMID:{text}"))
            elif parent_key == "doi":
                citations.add(_citation_key(f"DOI:{text}"))

    visit(value)
    return citations


def _citation_key(value: str) -> str:
    text = clean_text(value).casefold()
    text = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", text)
    text = re.sub(r"^(pmid|doi)\s*:\s*", r"\1:", text)
    return text


class Pipeline:
    def __init__(
        self,
        args: argparse.Namespace,
        genes: list[str],
        homologs: dict[str, dict[str, Homolog]],
        maize_index: dict[str, dict[str, str]],
    ) -> None:
        self.args = args
        self.genes = genes
        self.homologs = homologs
        self.maize_index = maize_index
        self.output_dir = args.output_dir.resolve()
        self.cache_dir = self.output_dir / "cache"
        self.evidence_dir = self.output_dir / "evidence"
        self.result_dir = self.output_dir / "results"
        self.prompts = {
            name: (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")
            for name in (
                "potato_rag_summary",
                "arabidopsis_gene_names",
                "arabidopsis_summary",
                "rice_summary",
                "maize_summary",
                "final_prediction",
            )
        }
        self.llm_config = ResponsesConfig.from_env()
        self.llm = ResponsesClient(self.llm_config)
        self._pubmed_semaphore = threading.BoundedSemaphore(2)
        self._cache_lock_guard = threading.Lock()
        self._cache_locks: dict[Path, threading.Lock] = {}
        self._error_lock = threading.Lock()
        self.errors: list[dict[str, str]] = []
        self._script_hashes = {
            str(path): sha256_file(path)
            for path in (
                args.potato_gene_script,
                args.potato_rag_script,
                args.arabidopsis_script,
                args.literature_script,
            )
        }

    def record_error(self, stage: str, key: str, error: object) -> None:
        record = {"stage": stage, "key": key, "error": clean_text(error, 1500)}
        with self._error_lock:
            self.errors.append(record)

    def source_call(
        self,
        stage: str,
        key: str,
        specification: dict[str, Any],
        producer: Callable[[], dict[str, Any]],
        validator: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        path = self.cache_dir / "sources" / stage / f"{safe_key(key)}.json"
        digest = sha256_json(specification)
        with self._cache_lock(path):
            if path.exists() and not self.args.force_sources:
                try:
                    cached = json.loads(path.read_text(encoding="utf-8"))
                    if cached.get("request_hash") == digest and cached.get("status") == "ok":
                        data = cached["data"]
                        if not isinstance(data, dict):
                            raise ValueError("cached source data is not a JSON object")
                        if validator is not None:
                            validator(data)
                        return data
                except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError, SourceError):
                    pass
            try:
                data = producer()
                if not isinstance(data, dict):
                    raise SourceError("source did not return a JSON object")
                if validator is not None:
                    validator(data)
                atomic_json(path, {"request_hash": digest, "status": "ok", "data": data})
                return data
            except Exception as exc:
                atomic_json(path, {"request_hash": digest, "status": "error", "error": str(exc)})
                raise

    def llm_call(
        self,
        stage: str,
        key: str,
        payload: dict[str, Any],
        prompt_name: str,
        schema_name: str,
        schema: dict[str, Any],
        validator: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        prompt = self.prompts[prompt_name]
        specification = {
            "llm": self.llm_config.cache_identity(),
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "payload": payload,
            "schema": schema,
        }
        path = self.cache_dir / "llm" / stage / f"{safe_key(key)}.json"
        digest = sha256_json(specification)
        with self._cache_lock(path):
            if path.exists() and not self.args.force_llm:
                try:
                    cached = json.loads(path.read_text(encoding="utf-8"))
                    if cached.get("request_hash") == digest and cached.get("status") == "ok":
                        data = cached["data"]
                        if not isinstance(data, dict):
                            raise ValueError("Cached LLM response is not a JSON object")
                        return validator(data) if validator is not None else data
                except (OSError, json.JSONDecodeError, KeyError, ValueError):
                    pass
            try:
                response = self.llm.complete_json(
                    instructions=prompt,
                    input_payload=payload,
                    schema_name=schema_name,
                    schema=schema,
                    validator=validator,
                )
                atomic_json(
                    path,
                    {
                        "request_hash": digest,
                        "status": "ok",
                        "data": response.data,
                        "response": {
                            "id": response.response_id,
                            "model": response.model,
                            "usage": response.usage,
                        },
                    },
                )
                return response.data
            except Exception as exc:
                atomic_json(path, {"request_hash": digest, "status": "error", "error": str(exc)})
                raise

    def _cache_lock(self, path: Path) -> threading.Lock:
        with self._cache_lock_guard:
            return self._cache_locks.setdefault(path, threading.Lock())

    def pubmed(self, query: str) -> dict[str, Any]:
        specification = {
            "query": query,
            "limit": self.args.pubmed_limit,
            "source": "pm",
            "script": str(self.args.literature_script.resolve()),
            "script_sha256": self._script_hashes[str(self.args.literature_script)],
        }

        def produce() -> dict[str, Any]:
            with self._pubmed_semaphore:
                data = run_json_command(
                    [
                        sys.executable,
                        str(self.args.literature_script),
                        "search",
                        query,
                        "--limit",
                        str(self.args.pubmed_limit),
                        "--source",
                        "pm",
                    ],
                    timeout=self.args.source_timeout * 2 + 20,
                    attempts=self.args.source_retries + 1,
                )
            if data.get("error"):
                raise SourceError(f"PubMed search failed: {data['error']}")
            if not isinstance(data.get("data"), list):
                raise SourceError("PubMed search returned malformed results")
            return {"query": query, "papers": compact_pubmed(data)}

        return self.source_call("pubmed", query, specification, produce)

    def process_potato(self, gene: str) -> dict[str, Any]:
        names: list[str] = []
        blocking_errors: list[dict[str, str]] = []
        specification = {
            "query": gene,
            "max_results": 5,
            "script": str(self.args.potato_gene_script.resolve()),
            "script_sha256": self._script_hashes[str(self.args.potato_gene_script)],
            "timeout": self.args.source_timeout,
        }
        try:
            search = self.source_call(
                "potato_gene_search",
                gene,
                specification,
                lambda: run_json_command(
                    [
                        sys.executable,
                        str(self.args.potato_gene_script),
                        "search",
                        gene,
                        "--max-results",
                        "5",
                        "--timeout",
                        str(self.args.source_timeout),
                    ],
                    timeout=self.args.source_timeout + 20,
                    attempts=self.args.source_retries + 1,
                ),
                validator=validate_potato_gene_source,
            )
            results = search.get("results") if isinstance(search.get("results"), list) else []
            names = extract_gene_names(
                ";".join(
                    clean_text(item.get("symbol"))
                    for item in results
                    if isinstance(item, dict)
                )
            )
        except Exception as exc:
            self.record_error("source:potato_gene_search", gene, exc)
            blocking_errors.append(
                {
                    "stage": "source:potato_gene_search",
                    "key": gene,
                    "error": clean_text(exc, 1500),
                }
            )

        results: list[dict[str, Any]] = []
        queries: list[str] = []
        for name in names:
            query = f"{name} potato gene function"
            queries.append(query)
            specification = {
                "query": query,
                "top_k_retrieve": self.args.rag_top_k_retrieve,
                "top_k_rerank": self.args.rag_top_k_rerank,
                "script": str(self.args.potato_rag_script.resolve()),
                "script_sha256": self._script_hashes[str(self.args.potato_rag_script)],
            }
            try:
                raw = self.source_call(
                    "potato_rag",
                    query,
                    specification,
                    lambda q=query: run_json_command(
                        [
                            sys.executable,
                            str(self.args.potato_rag_script),
                            q,
                            "--rag-only",
                            "--format",
                            "json",
                            "--rag-top-k-retrieve",
                            str(self.args.rag_top_k_retrieve),
                            "--rag-top-k-rerank",
                            str(self.args.rag_top_k_rerank),
                            "--rag-timeout",
                            str(self.args.source_timeout),
                        ],
                        timeout=self.args.source_timeout + 20,
                        attempts=self.args.source_retries + 1,
                    ),
                    validator=validate_potato_rag_source,
                )
                rag = raw.get("rag") if isinstance(raw.get("rag"), dict) else {}
                for item in rag.get("results") or []:
                    if not isinstance(item, dict):
                        continue
                    text = clean_text(item.get("text"), self.args.rag_text_chars)
                    if not text:
                        continue
                    results.append(
                        {
                            "query_name": name,
                            "rank": item.get("rank"),
                            "score": item.get("score"),
                            "title": clean_text(item.get("title"), 500),
                            "doi": clean_text(item.get("doi"), 200),
                            "text": text,
                        }
                    )
            except Exception as exc:
                self.record_error("source:potato_rag", query, exc)
                blocking_errors.append(
                    {
                        "stage": "source:potato_rag",
                        "key": query,
                        "error": clean_text(exc, 1500),
                    }
                )

        compact_results = _dedupe_rag_results(results, self.args.rag_max_evidence)
        payload = {
            "task": "Summarize potato gene-name RAG evidence",
            "potato_gene_id": gene,
            "query_gene": gene,
            "query_names": names,
            "rag_queries": queries,
            "rag_results": compact_results,
        }
        if blocking_errors:
            record = {
                "status": "failed",
                "potato_gene_id": gene,
                "gene_names": names,
                "input": payload,
                "blocking_errors": blocking_errors,
            }
            atomic_json(self.evidence_dir / "potato" / f"{gene}.json", record)
            return record

        try:
            summary = self.llm_call(
                "potato",
                gene,
                payload,
                "potato_rag_summary",
                "potato_evidence_summary",
                EVIDENCE_SCHEMA,
                lambda value: validate_evidence_summary(
                    value, gene, evidence_citations(payload)
                ),
            )
        except Exception as exc:
            self.record_error("llm:potato", gene, exc)
            record = {
                "status": "failed",
                "potato_gene_id": gene,
                "gene_names": names,
                "input": payload,
                "blocking_errors": [
                    {"stage": "llm:potato", "key": gene, "error": clean_text(exc, 1500)}
                ],
            }
            atomic_json(self.evidence_dir / "potato" / f"{gene}.json", record)
            return record

        record = {
            "status": "ok",
            "potato_gene_id": gene,
            "gene_names": names,
            "input": payload,
            "summary": summary,
        }
        atomic_json(self.evidence_dir / "potato" / f"{gene}.json", record)
        return record

    def _failed_species_record(
        self,
        species: str,
        target: str,
        stage: str,
        error: object,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.record_error(stage, target, error)
        record = {
            "status": "failed",
            "target_gene_id": target,
            "source_species": species,
            "input": payload or {},
            "blocking_errors": [
                {"stage": stage, "key": target, "error": clean_text(error, 1500)}
            ],
        }
        atomic_json(self.evidence_dir / species / f"{target}.json", record)
        return record

    def process_arabidopsis(self, target: str) -> dict[str, Any]:
        query_deadline = (
            self.args.source_timeout * (self.args.source_retries + 1)
            + (2 ** self.args.source_retries - 1)
        )
        common_specification = {
            "target": target,
            "timeout": self.args.source_timeout,
            "retries": self.args.source_retries,
            "deadline": query_deadline,
            "script": str(self.args.arabidopsis_script.resolve()),
            "script_sha256": self._script_hashes[str(self.args.arabidopsis_script)],
        }
        try:
            tair_raw = self.source_call(
                "arabidopsis_tair",
                target,
                {**common_specification, "mode": "tair"},
                lambda: run_json_command(
                    [
                        sys.executable,
                        str(self.args.arabidopsis_script),
                        "tair",
                        target,
                        "--timeout",
                        str(self.args.source_timeout),
                        "--retries",
                        str(self.args.source_retries),
                        "--deadline",
                        str(query_deadline),
                        "--format",
                        "json",
                    ],
                    timeout=query_deadline + 20,
                    attempts=1,
                ),
                validator=lambda value: validate_arabidopsis_tair_source(value, target),
            )
        except Exception as exc:
            return self._failed_species_record(
                "arabidopsis", target, "source:arabidopsis_tair", exc
            )
        confirmed_target = clean_text(tair_raw["tair"]["selected"]["gene_id"])
        selected = tair_raw["tair"]["selected"]
        candidate_names = arabidopsis_gene_name_candidates(selected)
        retrieval_names = candidate_names
        if len(candidate_names) >= 2:
            name_payload = {"gene_names": candidate_names}
            try:
                filtered = self.llm_call(
                    "arabidopsis_gene_names",
                    target,
                    name_payload,
                    "arabidopsis_gene_names",
                    "arabidopsis_gene_name_filter",
                    ARABIDOPSIS_GENE_NAMES_SCHEMA,
                    lambda value: validate_arabidopsis_gene_names(value, candidate_names),
                )
                retrieval_names = validate_arabidopsis_gene_names(
                    filtered, candidate_names
                )["gene_names"]
            except Exception as exc:
                return self._failed_species_record(
                    "arabidopsis",
                    target,
                    "llm:arabidopsis_gene_names",
                    exc,
                    {
                        "tair": tair_raw,
                        "candidate_gene_names": candidate_names,
                    },
                )

        plantconnectome_searches: list[dict[str, Any]] = []
        literature: list[dict[str, Any]] = []
        max_entities = getattr(self.args, "plantconnectome_max_entities", 10)
        for gene_name in retrieval_names:
            try:
                plant_raw = self.source_call(
                    "arabidopsis_plantconnectome",
                    f"{target}|{gene_name}",
                    {
                        **common_specification,
                        "mode": "plant",
                        "confirmed_target": confirmed_target,
                        "query_gene_name": gene_name,
                        "max_entities": max_entities,
                        "max_edges": self.args.plantconnectome_max_edges,
                        "snippets": 0,
                    },
                    lambda name=gene_name: run_json_command(
                        [
                            sys.executable,
                            str(self.args.arabidopsis_script),
                            "plant",
                            name,
                            "--max-entities",
                            str(max_entities),
                            "--max-edges",
                            str(self.args.plantconnectome_max_edges),
                            "--snippets",
                            "0",
                            "--timeout",
                            str(self.args.source_timeout),
                            "--retries",
                            str(self.args.source_retries),
                            "--deadline",
                            str(query_deadline),
                            "--format",
                            "json",
                        ],
                        timeout=query_deadline + 20,
                        attempts=1,
                        accepted_returncodes={0, 3},
                    ),
                    validator=validate_plantconnectome_source,
                )
            except Exception as exc:
                return self._failed_species_record(
                    "arabidopsis",
                    target,
                    "source:arabidopsis_plantconnectome",
                    f"PlantConnectome query failed for {gene_name}: {exc}",
                    {
                        "tair": tair_raw,
                        "candidate_gene_names": candidate_names,
                        "retrieval_gene_names": retrieval_names,
                        "failed_query_gene_name": gene_name,
                        "plantconnectome_searches": plantconnectome_searches,
                        "pubmed": literature,
                    },
                )
            plantconnectome_searches.append(
                {
                    "gene_name": gene_name,
                    "status": plant_raw.get("status"),
                    "plantconnectome": plant_raw.get("plantconnectome"),
                }
            )

            query = _literature_query(
                gene_name, confirmed_target, "Arabidopsis thaliana"
            )
            try:
                result = self.pubmed(query)
            except Exception as exc:
                return self._failed_species_record(
                    "arabidopsis",
                    target,
                    "source:pubmed_arabidopsis",
                    f"PubMed query failed for {gene_name}: {exc}",
                    {
                        "tair": tair_raw,
                        "candidate_gene_names": candidate_names,
                        "retrieval_gene_names": retrieval_names,
                        "failed_query_gene_name": gene_name,
                        "plantconnectome_searches": plantconnectome_searches,
                        "pubmed": literature,
                        "pubmed_query": query,
                    },
                )
            literature.append({"gene_name": gene_name, **result})

        raw = {
            "status": "ok",
            "query": target,
            "tair": tair_raw.get("tair"),
            "plantconnectome_searches": plantconnectome_searches,
        }
        try:
            validate_arabidopsis_source(raw, target)
            compact = compact_arabidopsis(raw)
        except Exception as exc:
            return self._failed_species_record(
                "arabidopsis",
                target,
                "source:arabidopsis_compaction",
                exc,
                {**raw, "pubmed": literature},
            )
        payload = {
            "task": "Summarize Arabidopsis homolog evidence",
            "source_species": "arabidopsis",
            "query_gene": target,
            "candidate_gene_names": candidate_names,
            "retrieval_gene_names": retrieval_names,
            "database_evidence": compact,
            "pubmed": literature,
        }
        return self._species_summary("arabidopsis", target, payload)

    def process_rice(self, target: str) -> dict[str, Any]:
        specification = {
            "target": target,
            "timeout": self.args.source_timeout,
            "retries": self.args.source_retries,
            "method": "RiceData HTML exact matches v2",
        }
        try:
            database = self.source_call(
                "rice",
                target,
                specification,
                lambda: fetch_ricedata(
                    target,
                    self.args.source_timeout,
                    self.args.source_retries,
                ),
                validator=validate_rice_source,
            )
        except Exception as exc:
            return self._failed_species_record("rice", target, "source:rice", exc)
        official_names = rice_gene_symbols(database)
        literature: list[dict[str, Any]] = []
        for official_name in official_names:
            query = _literature_query(official_name, target, "rice OR Oryza sativa")
            try:
                result = self.pubmed(query)
            except Exception as exc:
                return self._failed_species_record(
                    "rice",
                    target,
                    "source:pubmed_rice",
                    exc,
                    {
                        "database_evidence": database,
                        "official_gene_names": official_names,
                        "pubmed": literature,
                        "pubmed_query": query,
                    },
                )
            literature.append({"gene_symbol": official_name, **result})
        payload = {
            "task": "Summarize rice homolog evidence",
            "source_species": "rice",
            "query_gene": target,
            "official_gene_names": official_names,
            "database_evidence": database,
            "pubmed": literature,
        }
        return self._species_summary("rice", target, payload)

    def process_maize(self, target: str) -> dict[str, Any]:
        database = self.maize_index.get(target)
        if database is None:
            return self._failed_species_record(
                "maize",
                target,
                "source:maize",
                "target missing from local maize data",
            )
        official = clean_text(database.get("gene_name"))
        query = _literature_query(official, target, "maize OR Zea mays")
        try:
            literature = self.pubmed(query)
        except Exception as exc:
            return self._failed_species_record(
                "maize",
                target,
                "source:pubmed_maize",
                exc,
                {"database_evidence": database, "pubmed_query": query},
            )
        payload = {
            "task": "Summarize maize homolog evidence",
            "source_species": "maize",
            "query_gene": target,
            "official_gene_name": official,
            "database_evidence": database,
            "pubmed": literature,
        }
        return self._species_summary("maize", target, payload)

    def _species_summary(self, species: str, target: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            summary = self.llm_call(
                species,
                target,
                payload,
                f"{species}_summary",
                f"{species}_evidence_summary",
                EVIDENCE_SCHEMA,
                lambda value: validate_evidence_summary(value, target, evidence_citations(payload)),
            )
        except Exception as exc:
            return self._failed_species_record(
                species, target, f"llm:{species}", exc, payload
            )
        record = {
            "status": "ok",
            "target_gene_id": target,
            "source_species": species,
            "input": payload,
            "summary": summary,
        }
        atomic_json(self.evidence_dir / species / f"{target}.json", record)
        return record

    def process_final(
        self,
        gene: str,
        potato: dict[str, Any],
        species_records: dict[tuple[str, str], dict[str, Any]],
        expression: list[dict[str, Any]],
    ) -> dict[str, Any]:
        homolog_evidence: dict[str, Any] = {}
        blocking_errors: list[dict[str, str]] = []
        if potato.get("status") != "ok":
            potato_errors = potato.get("blocking_errors")
            if isinstance(potato_errors, list):
                blocking_errors.extend(error for error in potato_errors if isinstance(error, dict))
            else:
                blocking_errors.append(
                    {
                        "stage": "evidence:potato",
                        "key": gene,
                        "error": "potato evidence did not complete",
                    }
                )
        for species in ("arabidopsis", "rice", "maize"):
            homolog = self.homologs[species].get(gene)
            if homolog is None:
                homolog_evidence[species] = None
                continue
            source = species_records.get((species, homolog.target_gene_id))
            if not source or source.get("status") != "ok":
                source_errors = source.get("blocking_errors") if source else None
                if isinstance(source_errors, list):
                    blocking_errors.extend(
                        error for error in source_errors if isinstance(error, dict)
                    )
                else:
                    blocking_errors.append(
                        {
                            "stage": f"evidence:{species}",
                            "key": homolog.target_gene_id,
                            "error": f"{species} evidence did not complete",
                        }
                    )
                homolog_evidence[species] = {
                    "target_gene_id": homolog.target_gene_id,
                    "homology_level": homolog.level,
                    "status": "failed",
                }
                continue
            homolog_evidence[species] = {
                "target_gene_id": homolog.target_gene_id,
                "homology_level": homolog.level,
                "function_summary": source["summary"],
            }
        payload = {
            "task": "Predict one potato gene function from summarized evidence",
            "potato_gene_id": gene,
            "potato_gene_names": potato.get("gene_names", []),
            "potato_function_evidence": potato.get("summary"),
            "homolog_function_evidence": homolog_evidence,
            "expression_by_tissue": expression,
            "expression_statistic": (
                "Technical runs are averaged within each sample_name/tissue; tissue mean and sample SD "
                "are calculated across those source means."
            ),
        }
        if blocking_errors:
            record = {
                "status": "blocked",
                "potato_gene_id": gene,
                "evidence": payload,
                "blocking_errors": blocking_errors,
            }
            atomic_json(self.result_dir / "genes" / f"{gene}.json", record)
            return record

        try:
            prediction = self.llm_call(
                "final",
                gene,
                payload,
                "final_prediction",
                "potato_function_prediction",
                PREDICTION_SCHEMA,
                lambda value: validate_prediction(value, gene),
            )
        except Exception as exc:
            self.record_error("llm:final", gene, exc)
            record = {
                "status": "failed",
                "potato_gene_id": gene,
                "evidence": payload,
                "blocking_errors": [
                    {"stage": "llm:final", "key": gene, "error": clean_text(exc, 1500)}
                ],
            }
            atomic_json(self.result_dir / "genes" / f"{gene}.json", record)
            return record
        record = {
            "status": "ok",
            "potato_gene_id": gene,
            "evidence": payload,
            "prediction": prediction,
        }
        atomic_json(self.result_dir / "genes" / f"{gene}.json", record)
        return record


def _dedupe_rag_results(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    by_digest: dict[str, dict[str, Any]] = {}
    for row in rows:
        normalized = unicodedata.normalize("NFKC", clean_text(row.get("text"))).casefold()
        if not normalized:
            continue
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        if digest in by_digest:
            names = by_digest[digest]["query_names"]
            if row["query_name"] not in names:
                names.append(row["query_name"])
            continue
        item = dict(row)
        item["query_names"] = [item.pop("query_name")]
        by_digest[digest] = item
        output.append(item)
    output.sort(key=lambda row: _score(row.get("score")), reverse=True)
    return output[:limit]


def _score(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("-inf")


def _literature_query(name: str, target: str, species: str) -> str:
    if name and name.casefold() != target.casefold():
        return f"({name} OR {target}) AND ({species})"
    return f"{target} AND ({species})"


def run_parallel(
    stage: str,
    items: list[Any],
    worker: Callable[[Any], Any],
    workers: int,
) -> dict[Any, Any]:
    if not items:
        return {}
    output: dict[Any, Any] = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(worker, item): item for item in items}
        for completed, future in enumerate(as_completed(futures), 1):
            item = futures[future]
            output[item] = future.result()
            print(f"[{stage}] {completed}/{len(items)}", flush=True)
    return output


def write_outputs(output_dir: Path, genes: list[str], records: dict[str, dict[str, Any]]) -> tuple[Path, Path]:
    result_dir = output_dir / "results"
    result_dir.mkdir(parents=True, exist_ok=True)
    completed_genes = [
        gene
        for gene in genes
        if records.get(gene, {}).get("status") == "ok"
        and isinstance(records.get(gene, {}).get("prediction"), dict)
    ]
    jsonl_path = result_dir / "predictions.jsonl"
    jsonl_tmp = jsonl_path.with_name(jsonl_path.name + f".tmp.{os.getpid()}")
    with jsonl_tmp.open("w", encoding="utf-8") as handle:
        for gene in completed_genes:
            handle.write(json.dumps(records[gene], ensure_ascii=False, separators=(",", ":")) + "\n")
    os.replace(jsonl_tmp, jsonl_path)

    fields = [
        "potato_gene_id",
        "gene_names",
        "predicted_function",
        "reliability_grade",
        "grade_reason",
    ]
    tsv_path = result_dir / "predictions.tsv"
    tsv_tmp = tsv_path.with_name(tsv_path.name + f".tmp.{os.getpid()}")
    with tsv_tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for gene in completed_genes:
            record = records[gene]
            evidence = record["evidence"]
            prediction = record["prediction"]
            writer.writerow(
                {
                    "potato_gene_id": gene,
                    "gene_names": ";".join(evidence["potato_gene_names"]),
                    "predicted_function": prediction["predicted_function"],
                    "reliability_grade": prediction["reliability_grade"],
                    "grade_reason": prediction["grade_reason"],
                }
            )
    os.replace(tsv_tmp, tsv_path)
    return jsonl_path, tsv_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--genes", type=Path, required=True, help="One gene ID per line; IDs are used unchanged")
    parser.add_argument("--output-dir", type=Path, default=HERE / "output")
    parser.add_argument("--homolog-dir", type=Path, default=DEFAULT_HOMOLOG_DIR)
    parser.add_argument("--expression-matrix", type=Path, default=DEFAULT_EXPRESSION_MATRIX)
    parser.add_argument("--expression-metadata", type=Path, default=DEFAULT_EXPRESSION_METADATA)
    parser.add_argument("--maize-data", type=Path, default=DEFAULT_MAIZE_DATA)
    parser.add_argument(
        "--potato-gene-script",
        type=Path,
        default=SKILLS_ROOT / "potato-gene-search" / "scripts" / "query_potato_gene.py",
    )
    parser.add_argument(
        "--potato-rag-script",
        type=Path,
        default=SKILLS_ROOT / "potato-knowledge-search" / "scripts" / "query_potato_knowledge.py",
    )
    parser.add_argument(
        "--arabidopsis-script",
        type=Path,
        default=SKILLS_ROOT / "arabidopsis-gene-search" / "scripts" / "query_arabidopsis_gene_search.py",
    )
    parser.add_argument(
        "--literature-script",
        type=Path,
        default=SKILLS_ROOT / "literature-review" / "scripts" / "lit_search.py",
    )
    parser.add_argument("--fetch-workers", type=int, default=8)
    parser.add_argument("--source-timeout", type=int, default=60)
    parser.add_argument(
        "--source-retries",
        type=int,
        default=3,
        help="Additional retries after the first transient source request failure",
    )
    parser.add_argument(
        "--pubmed-limit",
        type=int,
        default=20,
        help="Maximum PubMed records per homolog query; must be at least 20",
    )
    parser.add_argument("--rag-top-k-retrieve", type=int, default=20)
    parser.add_argument("--rag-top-k-rerank", type=int, default=5)
    parser.add_argument("--rag-max-evidence", type=int, default=10)
    parser.add_argument("--rag-text-chars", type=int, default=1600)
    parser.add_argument(
        "--plantconnectome-max-edges",
        type=int,
        default=5,
        help="Maximum edges retrieved per PlantConnectome entity",
    )
    parser.add_argument(
        "--plantconnectome-max-entities",
        type=int,
        default=10,
        help="Maximum PlantConnectome entities retrieved per gene-name query",
    )
    parser.add_argument("--force-sources", action="store_true")
    parser.add_argument("--force-llm", action="store_true")
    parser.add_argument("--preflight", action="store_true", help="Validate inputs and report target counts only")
    return parser


def validate_paths(args: argparse.Namespace) -> None:
    paths = [
        args.genes,
        args.expression_matrix,
        args.expression_metadata,
        args.maize_data,
        args.potato_gene_script,
        args.potato_rag_script,
        args.arabidopsis_script,
        args.literature_script,
        *(PROMPT_DIR / f"{name}.md" for name in (
            "potato_rag_summary",
            "arabidopsis_gene_names",
            "arabidopsis_summary",
            "rice_summary",
            "maize_summary",
            "final_prediction",
        )),
    ]
    for path in paths:
        if not path.is_file():
            raise ValueError(f"Required file is missing: {path}")
    if args.fetch_workers < 1 or args.source_timeout < 1:
        raise ValueError("workers and source timeout must be positive")
    if not 0 <= args.source_retries <= 10:
        raise ValueError("source retries must be between 0 and 10")
    if args.pubmed_limit < 20:
        raise ValueError("PubMed limit must be at least 20")
    if args.plantconnectome_max_edges < 1:
        raise ValueError("PlantConnectome max edges must be positive")
    if args.plantconnectome_max_entities < 1:
        raise ValueError("PlantConnectome max entities must be positive")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        validate_paths(args)
        genes = read_gene_list(args.genes)
        homolog_files = {
            "arabidopsis": (args.homolog_dir / "DMv82_Arabidopsis_homologs_L1_L2_L3.tsv", "Arabidopsis_gene"),
            "rice": (args.homolog_dir / "DMv82_OsMSU7_homologs_L1_L2_L3.tsv", "Target_gene"),
            "maize": (args.homolog_dir / "DMv82_ZmNAM5_homologs_L1_L2_L3.tsv", "Target_gene"),
        }
        homologs = {species: load_homologs(path, column) for species, (path, column) in homolog_files.items()}
        counts = {
            species: len({mapping[gene].target_gene_id for gene in genes if gene in mapping})
            for species, mapping in homologs.items()
        }
        report = {"genes": len(genes), "unique_homolog_targets": counts}
        if args.preflight:
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0

        maize_index = load_maize_index(args.maize_data)
        pipeline = Pipeline(args, genes, homologs, maize_index)

        potato = run_parallel("potato", genes, pipeline.process_potato, args.fetch_workers)
        target_jobs = [
            (species, target)
            for species, mapping in homologs.items()
            for target in sorted({mapping[gene].target_gene_id for gene in genes if gene in mapping})
        ]

        def process_target(job: tuple[str, str]) -> dict[str, Any]:
            species, target = job
            return {
                "arabidopsis": pipeline.process_arabidopsis,
                "rice": pipeline.process_rice,
                "maize": pipeline.process_maize,
            }[species](target)

        species_records = run_parallel("homolog", target_jobs, process_target, args.fetch_workers)
        expression = summarize_expression(args.expression_matrix, args.expression_metadata, genes)
        atomic_json(pipeline.evidence_dir / "expression.json", expression)

        final_records = run_parallel(
            "final",
            genes,
            lambda gene: pipeline.process_final(gene, potato[gene], species_records, expression[gene]),
            args.fetch_workers,
        )
        jsonl_path, tsv_path = write_outputs(args.output_dir.resolve(), genes, final_records)
        predicted_genes = [
            gene for gene in genes if final_records.get(gene, {}).get("status") == "ok"
        ]
        blocked_genes = [
            {
                "potato_gene_id": gene,
                "status": final_records.get(gene, {}).get("status", "failed"),
                "blocking_errors": final_records.get(gene, {}).get("blocking_errors", []),
            }
            for gene in genes
            if gene not in predicted_genes
        ]
        if len(predicted_genes) == len(genes):
            run_status = "complete"
        elif predicted_genes:
            run_status = "incomplete"
        else:
            run_status = "failed"
        sorted_errors = sorted(
            pipeline.errors,
            key=lambda item: (item.get("stage", ""), item.get("key", ""), item.get("error", "")),
        )
        run_report = {
            **report,
            "status": run_status,
            "predicted_gene_count": len(predicted_genes),
            "predicted_genes": predicted_genes,
            "blocked_gene_count": len(blocked_genes),
            "blocked_genes": blocked_genes,
            "source_and_llm_errors": sorted_errors,
            "error_count": len(sorted_errors),
            "predictions_jsonl": str(jsonl_path.resolve()),
            "predictions_tsv": str(tsv_path.resolve()),
        }
        atomic_json(args.output_dir.resolve() / "run_report.json", run_report)
        print(json.dumps(run_report, ensure_ascii=False, indent=2))
        return 0 if run_status == "complete" else 2
    except (ValueError, OSError, LLMError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
