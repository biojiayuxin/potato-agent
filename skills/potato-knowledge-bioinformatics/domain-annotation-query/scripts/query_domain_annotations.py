#!/usr/bin/env python3
"""Query and export protein domain annotations from the current Potato deployment."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib
from pathlib import Path
from typing import Any, Sequence


API_PATH = "/api/genome-annotations"
INSTALL_BASE_URL_FILE = Path(__file__).resolve().parents[1] / "api-base-url.txt"
MAX_IDS = 5000


def deployment_base_url(override: str | None) -> str:
    if override is not None:
        return override
    for name in ("POTATO_DOMAIN_ANNOTATIONS_BASE_URL", "POTATO_GENOME_ANNOTATIONS_BASE_URL",
                 "INTERFACE_PUBLIC_BASE_URL"):
        value = os.getenv(name, "").strip()
        if value:
            return value
    if INSTALL_BASE_URL_FILE.is_file():
        value = INSTALL_BASE_URL_FILE.read_text(encoding="utf-8").strip()
        if value:
            return value
    raise ValueError(
        "Deployment URL is not configured. Pass the current site's URL with --base-url, "
        "set POTATO_DOMAIN_ANNOTATIONS_BASE_URL or INTERFACE_PUBLIC_BASE_URL, "
        "or configure api-base-url.txt in the skill directory."
    )


def bounded_int(minimum: int, maximum: int):
    def parse(value: str) -> int:
        try:
            number = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError("expected an integer") from exc
        if not minimum <= number <= maximum:
            raise argparse.ArgumentTypeError(f"expected {minimum} through {maximum}")
        return number
    return parse


def api_root(value: str) -> str:
    parsed = urllib.parse.urlsplit(value.strip().rstrip("/"))
    if (parsed.scheme not in {"http", "https"} or not parsed.netloc
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("base URL must be an HTTP(S) URL without credentials, query or fragment")
    path = parsed.path.rstrip("/")
    if path not in {"", "/functional-annotation", API_PATH}:
        raise ValueError("base URL must be the site root, Domain annotation page or API root")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, API_PATH, "", ""))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--base-url", help="Current deployment URL; otherwise use deployment environment or api-base-url.txt.")
    common.add_argument("--timeout", type=bounded_int(1, 3600), default=120)
    common.add_argument("--indent", type=bounded_int(0, 8), default=2)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("metadata", parents=[common], help="Show the active release, assemblies and methods.")
    commands.add_parser("downloads", parents=[common], help="List the public release downloads and checksums.")
    for command in ("gene", "transcript"):
        detail = commands.add_parser(command, parents=[common], help=f"Show one exact {command} ID.")
        detail.add_argument("id")
        detail.add_argument("--assembly", required=True, help="Exact assembly name from metadata.")
    commands.add_parser("families", parents=[common], help="Show TF family coverage and gene/transcript counts.")
    for command in ("query", "export"):
        sub = commands.add_parser(command, parents=[common], help=(
            "Query one page of matching records." if command == "query" else "Export all matches to a local ZIP file."
        ))
        sub.add_argument("ids", nargs="*", help="Exact gene, transcript or global protein IDs; never strip isoform suffixes.")
        sub.add_argument("--ids-file", type=Path, help="Local UTF-8 text file of exact IDs, separated by whitespace or commas.")
        sub.add_argument("--query-json", type=Path, help="Read a complete API query object from a local JSON file.")
        sub.add_argument("--assembly", action="append", help="Exact assembly ID; repeat for multiple. Default monoploid/DMv8.2.")
        sub.add_argument("--all-assemblies", action="store_true")
        sub.add_argument("--view", choices=("genes", "transcripts"))
        sub.add_argument("--text", help="Annotation description keywords.")
        sub.add_argument("--database", action="append", choices=("CDD", "PANTHER", "Pfam", "SMART"))
        sub.add_argument("--signature", action="append")
        sub.add_argument("--interpro", action="append")
        sub.add_argument("--go", action="append")
        sub.add_argument("--domain-match", choices=("all", "any"))
        sub.add_argument("--tf-family", action="append")
        sub.add_argument("--tf-grade", action="append", choices=("A", "B", "C", "U"))
        sub.add_argument("--tf-status", choices=("all", "selected", "not_selected", "ambiguous", "unassessable"))
        sub.add_argument("--annotation-status", choices=("all", "hit", "no_match", "no_cds"))
        sub.add_argument("--conflict", choices=("all", "presence", "family", "any"))
        if command == "query":
            sub.add_argument("--limit", type=bounded_int(1, 500))
            sub.add_argument("--offset", type=bounded_int(0, 10_000_000))
        else:
            sub.add_argument("--output", type=Path, required=True, help="Local .zip path; existing files are never overwritten.")
            sub.add_argument("--version", help="Pin the queried dataset version; otherwise read current metadata first.")
            sub.add_argument("--table", action="append", choices=("genes", "transcripts", "domains", "tf_decisions", "tf_evidence"),
                             help="Export table; repeat for multiple. Default genes.")
            sub.add_argument("--format", choices=("tsv", "csv"), default="tsv")
            sub.add_argument("--selected-json", type=Path, help="Local JSON list of assembly-qualified selected records.")
    return parser


def read_json(path: Path, expected_type: type) -> Any:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, expected_type):
        raise ValueError(f"{path.name} must contain a JSON {expected_type.__name__}")
    return value


def parse_ids(values: Sequence[str]) -> list[str]:
    ids = list(dict.fromkeys(token for value in values for token in re.split(r"[\s,;]+", value.strip()) if token))
    if len(ids) > MAX_IDS:
        raise ValueError(f"at most {MAX_IDS} exact IDs are allowed")
    return ids


def query_payload(args: argparse.Namespace) -> dict[str, Any]:
    payload = read_json(args.query_json, dict) if args.query_json else {}
    if args.assembly and args.all_assemblies:
        raise ValueError("choose --assembly or --all-assemblies")
    if args.all_assemblies:
        payload["assemblyIds"] = []
    elif args.assembly:
        payload["assemblyIds"] = args.assembly
    elif "assemblyIds" not in payload:
        payload["assemblyIds"] = ["monoploid/DMv8.2"]
    raw_ids = list(args.ids)
    if args.ids_file:
        raw_ids.append(args.ids_file.read_text(encoding="utf-8-sig"))
    if raw_ids:
        payload["ids"] = parse_ids(raw_ids)
    for field, key in (
        ("view", "view"), ("text", "q"), ("database", "analyses"),
        ("signature", "signatures"), ("interpro", "interproIds"), ("go", "goIds"),
        ("domain_match", "domainMode"), ("tf_family", "tfFamilies"),
        ("tf_grade", "grades"), ("tf_status", "tfStatus"),
        ("annotation_status", "annotationStatus"), ("conflict", "conflict"),
        ("limit", "limit"), ("offset", "offset"),
    ):
        value = getattr(args, field, None)
        if value is not None:
            payload[key] = value
    return payload


def make_request(args: argparse.Namespace, endpoint: str, *, payload: dict[str, Any] | None = None,
                 params: dict[str, Any] | None = None, accept: str = "application/json") -> urllib.request.Request:
    url = api_root(deployment_base_url(args.base_url)) + "/" + endpoint
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {"Accept": accept, "User-Agent": "potato-domain-annotation-query/1.0"}
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return urllib.request.Request(url, data=body, headers=headers, method="POST" if body is not None else "GET")


def request_json(request: urllib.request.Request, timeout: int) -> dict[str, Any]:
    with urllib.request.urlopen(request, timeout=timeout) as response:
        try:
            data = json.load(response)
        except (ValueError, UnicodeError) as exc:
            raise RuntimeError("annotation API returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise RuntimeError("annotation API returned JSON that is not an object")
    return {"api_url": request.full_url, "data": data}


def download_export(request: urllib.request.Request, args: argparse.Namespace) -> dict[str, Any]:
    output = args.output.expanduser().absolute()
    if output.suffix.lower() != ".zip":
        raise ValueError("export output must have a .zip extension")
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"output already exists: {output}")
    temporary: Path | None = None
    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type not in {"application/zip", "application/x-zip-compressed"}:
                raise RuntimeError("annotation API did not return a ZIP export")
            output.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            size = 0
            with tempfile.NamedTemporaryFile(prefix=".annotation-", suffix=".zip", dir=output.parent, delete=False) as stream:
                temporary = Path(stream.name)
                while chunk := response.read(1024 * 1024):
                    stream.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
            length = response.headers.get("Content-Length")
            if length is not None and size != int(length):
                raise RuntimeError("annotation API returned an incomplete ZIP export")
        try:
            with zipfile.ZipFile(temporary) as archive:
                metadata = json.loads(archive.read("metadata.json"))
                if not isinstance(metadata, dict):
                    raise ValueError("metadata must be an object")
        except (zipfile.BadZipFile, KeyError, ValueError, UnicodeError, zlib.error) as exc:
            raise RuntimeError("annotation API returned an invalid or incomplete ZIP export") from exc
        # A hard link publishes the complete download atomically and fails if a file appeared meanwhile.
        os.link(temporary, output)
        return {"api_url": request.full_url, "output": str(output), "bytes": size,
                "sha256": digest.hexdigest(), "metadata": metadata}
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.command in {"metadata", "downloads"}:
        request = make_request(args, args.command)
    elif args.command in {"gene", "transcript"}:
        endpoint = "genes" if args.command == "gene" else "transcripts"
        request = make_request(args, endpoint + "/" + urllib.parse.quote(args.id, safe=""), params={"assembly": args.assembly})
    elif args.command == "families":
        request = make_request(args, "tf-families")
    elif args.command == "query":
        request = make_request(args, "query", payload=query_payload(args))
    else:
        query = query_payload(args)
        version = args.version or query.get("datasetVersion")
        if not version:
            source = request_json(make_request(args, "metadata"), args.timeout)
            version = source["data"].get("datasetVersion")
            if not isinstance(version, str) or not version:
                raise RuntimeError("annotation metadata did not provide a datasetVersion")
        query["datasetVersion"] = version
        payload = {"query": query, "datasetVersion": version, "format": args.format,
                   "tables": args.table or ["genes"]}
        if args.selected_json:
            payload["selection"] = read_json(args.selected_json, list)
        request = make_request(args, "export", payload=payload, accept="application/zip")
        return download_export(request, args)
    return request_json(request, args.timeout)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run(args)
    except urllib.error.HTTPError as exc:
        body = exc.read(4096).decode("utf-8", errors="replace")
        try:
            detail = json.loads(body)
            detail = detail.get("detail", detail) if isinstance(detail, dict) else detail
        except ValueError:
            detail = body[:500]
        print(f"annotation API returned HTTP {exc.code}: {detail}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"annotation API connection failed: {exc.reason}", file=sys.stderr)
        return 1
    except (OSError, ValueError, RuntimeError, http.client.HTTPException) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=args.indent or None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
