#!/usr/bin/env python3
"""Query the Potato Agent quick gene-function prediction endpoint."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import textwrap
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional, Sequence


DEFAULT_BASE_URL = "https://potato-agent.ynnu.edu.cn"
DEFAULT_TIMEOUT = 60
DEFAULT_MAX_TEXT_CHARS = 700
DEFAULT_EVIDENCE_LIMIT = 5

DMV82_GENE_ID_RE = re.compile(r"DM8\.2_chr[0-9A-Za-z]+G[0-9]+")
BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class PredictionAPIError(RuntimeError):
    """Raised when the prediction service cannot return usable data."""

    def __init__(
        self,
        message: str,
        *,
        status: Optional[int] = None,
        url: Optional[str] = None,
        body: str = "",
    ) -> None:
        super().__init__(message)
        self.status = status
        self.url = url
        self.body = body

    def as_dict(self, gene_id: str) -> dict[str, Any]:
        return {
            "success": False,
            "geneId": gene_id,
            "error": str(self),
            "status": self.status,
            "url": self.url,
            "body": self.body,
        }


def normalize_gene_id(value: str) -> str:
    gene_id = value.strip()
    if not DMV82_GENE_ID_RE.fullmatch(gene_id):
        raise ValueError(
            "--predict_summary must be one DMv8.2 gene ID, such as "
            "DM8.2_chr01G26640"
        )
    return gene_id


def build_endpoint(base_url: str, gene_id: str) -> str:
    encoded_gene_id = urllib.parse.quote(gene_id, safe="")
    return (
        base_url.rstrip("/")
        + f"/api/v1/genes/{encoded_gene_id}/description/evidence"
    )


def validate_response(data: Any, *, url: str, body: str) -> dict[str, Any]:
    required_strings = (
        "geneId",
        "transcriptId",
        "predictedFunction",
        "reliabilityGrade",
        "gradeReason",
    )
    if not isinstance(data, dict):
        raise PredictionAPIError(
            "Gene function prediction API returned JSON that is not an object",
            url=url,
            body=body[:1000],
        )
    if any(not isinstance(data.get(field), str) for field in required_strings):
        raise PredictionAPIError(
            "Gene function prediction API response is missing required string fields",
            url=url,
            body=body[:1000],
        )
    if not isinstance(data.get("evidence"), dict):
        raise PredictionAPIError(
            "Gene function prediction API response field 'evidence' is not an object",
            url=url,
            body=body[:1000],
        )
    return data


def query_prediction(
    gene_id: str,
    *,
    base_url: str,
    timeout: int,
) -> dict[str, Any]:
    endpoint = build_endpoint(base_url, gene_id)
    request = urllib.request.Request(
        endpoint,
        headers={"User-Agent": BROWSER_UA, "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise PredictionAPIError(
            f"HTTP {exc.code} from gene function prediction API",
            status=exc.code,
            url=endpoint,
            body=error_body[:1000],
        ) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise PredictionAPIError(
            f"Failed to connect to gene function prediction API: {exc}",
            url=endpoint,
        ) from exc

    try:
        data = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise PredictionAPIError(
            "Gene function prediction API returned non-JSON response",
            url=endpoint,
            body=response_body[:1000],
        ) from exc
    return validate_response(data, url=endpoint, body=response_body)


def compact_text(value: Any, max_chars: int) -> str:
    text = "" if value is None else " ".join(str(value).split())
    if max_chars > 0 and len(text) > max_chars:
        return text[: max_chars - 3].rstrip() + "..."
    return text


def format_summary(
    data: dict[str, Any],
    max_text_chars: int,
    evidence_limit: int,
) -> str:
    evidence = data.get("evidence") or {}
    potato_evidence = evidence.get("potato_function_evidence") or {}
    homolog_evidence = evidence.get("homolog_function_evidence") or {}
    expression_by_tissue = evidence.get("expression_by_tissue") or []
    lines = [
        f"Gene: {data.get('geneId', '')}",
        f"Transcript: {data.get('transcriptId', '')}",
        f"Reliability: {data.get('reliabilityGrade', '')}",
        f"Grade reason: {compact_text(data.get('gradeReason'), max_text_chars)}",
        "Predicted function:",
        textwrap.fill(
            compact_text(data.get("predictedFunction"), max_text_chars),
            width=100,
            replace_whitespace=False,
        ),
    ]
    if isinstance(potato_evidence, dict) and potato_evidence:
        function_summary = compact_text(
            potato_evidence.get("function_summary"), max_text_chars
        )
        if function_summary:
            lines.append(f"Direct potato evidence: {function_summary}")
        citations = potato_evidence.get("citations") or []
        if isinstance(citations, list) and citations:
            lines.append("Direct citations: " + ", ".join(map(str, citations)))
    if isinstance(homolog_evidence, dict) and homolog_evidence:
        lines.append("Homolog evidence species: " + ", ".join(homolog_evidence))
    if isinstance(expression_by_tissue, list) and expression_by_tissue:
        expression_rows = []
        for item in expression_by_tissue[:evidence_limit]:
            if isinstance(item, dict):
                expression_rows.append(
                    f"{item.get('tissue', '')}={item.get('mean_tpm', '')} mean TPM"
                )
        if expression_rows:
            lines.append("Top expression entries: " + "; ".join(expression_rows))
    lines.extend(
        [
            "",
            "Interpretation note: this is a quick function prediction, not an "
            "experimentally verified conclusion. The prediction database may lag "
            "behind current annotations and literature.",
        ]
    )
    return "\n".join(lines)


def positive_int(name: str, value: int) -> None:
    if value <= 0:
        raise ValueError(f"{name} must be positive")


def non_negative_int(name: str, value: int) -> None:
    if value < 0:
        raise ValueError(f"{name} must be non-negative")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Quickly query the predicted function of one potato gene."
    )
    parser.add_argument(
        "--predict_summary",
        required=True,
        metavar="GENE_ID",
        help="One concrete DMv8.2 gene ID to query.",
    )
    parser.add_argument(
        "--format",
        choices=("json", "summary"),
        default="json",
        help="Output format. Default: json.",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("POTATO_GENE_FUNCTION_BASE_URL", DEFAULT_BASE_URL),
        help=f"Prediction service base URL. Default: {DEFAULT_BASE_URL}.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"HTTP timeout seconds. Default: {DEFAULT_TIMEOUT}.",
    )
    parser.add_argument(
        "--max-text-chars",
        type=int,
        default=DEFAULT_MAX_TEXT_CHARS,
        help=(
            "Maximum characters per long field in summary output; 0 disables "
            f"truncation. Default: {DEFAULT_MAX_TEXT_CHARS}."
        ),
    )
    parser.add_argument(
        "--evidence-limit",
        type=int,
        default=DEFAULT_EVIDENCE_LIMIT,
        help=(
            "Maximum expression evidence entries in summary output. Default: "
            f"{DEFAULT_EVIDENCE_LIMIT}."
        ),
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        gene_id = normalize_gene_id(args.predict_summary)
        positive_int("--timeout", args.timeout)
        non_negative_int("--max-text-chars", args.max_text_chars)
        positive_int("--evidence-limit", args.evidence_limit)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        prediction = query_prediction(
            gene_id,
            base_url=args.base_url,
            timeout=args.timeout,
        )
    except PredictionAPIError as exc:
        print(json.dumps(exc.as_dict(gene_id), ensure_ascii=False, indent=2))
        return 1

    output = {**prediction, "success": True}
    if args.format == "json":
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(
            format_summary(
                output,
                args.max_text_chars,
                args.evidence_limit,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
