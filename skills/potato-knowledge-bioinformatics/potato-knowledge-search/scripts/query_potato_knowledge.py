#!/usr/bin/env python3
"""Query potato literature RAG evidence and PlantScience.ai KG evidence.

The script depends only on the Python standard library so it can be used by
different agent runtimes without an installation step.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterable, Optional, Sequence


DEFAULT_RAG_BASE_URL = "https://www.potato-ai.top"
DEFAULT_RAG_TOP_K_RETRIEVE = 200
DEFAULT_RAG_TOP_K_RERANK = 20
DEFAULT_RAG_TIMEOUT = 120

DEFAULT_KG_BASE_URL = "https://plantscience.ai/api"
DEFAULT_KG_TIMEOUT = 30
DEFAULT_KG_RETRIES = 2
DEFAULT_KG_EDGE_LIMIT = 50
DEFAULT_MAX_KG_ENTITIES = 5

DEFAULT_SUMMARY_KG_LIMIT = 5

TRANSIENT_STATUS = {502, 503, 504}
BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

COMMON_KG_PHRASES = [
    "late blight",
    "early blight",
    "bacterial wilt",
    "tuber dormancy",
    "starch biosynthesis",
    "drought stress",
    "salt stress",
    "salinity stress",
    "heat stress",
    "cold stress",
    "nitrogen use efficiency",
    "photoperiod",
    "tuberization",
    "sprouting",
    "phytophthora infestans",
]


class APIError(RuntimeError):
    """Raised when an upstream evidence service cannot return usable data."""

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

    def as_dict(self) -> dict[str, Any]:
        return {
            "error": str(self),
            "status": self.status,
            "url": self.url,
            "body": self.body,
        }


def normalize_rag_base_url(value: str) -> str:
    return (value or DEFAULT_RAG_BASE_URL).rstrip("/")


def normalize_kg_base_url(value: str) -> str:
    base = (value or DEFAULT_KG_BASE_URL).rstrip("/")
    if not base.endswith("/api"):
        base += "/api"
    return base


def truncate(value: Any, max_chars: int = 260) -> str:
    text = "" if value is None else " ".join(str(value).split())
    if max_chars > 0 and len(text) > max_chars:
        return text[: max_chars - 3].rstrip() + "..."
    return text


def unique_preserve(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        item = " ".join(str(value).strip().split())
        if not item:
            continue
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def post_json(url: str, payload: dict[str, object], timeout: int) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise APIError(
            f"HTTP {exc.code} from Potato Knowledge Hub RAG API",
            status=exc.code,
            url=url,
            body=error_body[:1000],
        ) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise APIError(f"Failed to connect to Potato Knowledge Hub RAG API: {exc}", url=url) from exc

    try:
        data = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise APIError("Potato Knowledge Hub RAG API returned non-JSON response", url=url, body=response_body[:1000]) from exc
    if not isinstance(data, dict):
        raise APIError("Potato Knowledge Hub RAG API returned JSON that is not an object", url=url, body=response_body[:1000])
    return data


def normalize_rag_results(data: dict[str, Any]) -> list[dict[str, Any]]:
    results = data.get("results", [])
    if results is None:
        return []
    if not isinstance(results, list):
        raise APIError("Potato Knowledge Hub RAG API returned 'results' that is not a list")
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(results, start=1):
        if not isinstance(item, dict):
            continue
        row = dict(item)
        row.setdefault("rank", index)
        normalized.append(row)
    return normalized


def run_rag(args: argparse.Namespace) -> dict[str, Any]:
    endpoint = normalize_rag_base_url(args.rag_base_url) + "/api/rag/search"
    payload = {
        "query": args.query,
        "top_k_retrieve": args.rag_top_k_retrieve,
        "top_k_rerank": args.rag_top_k_rerank,
    }
    try:
        data = post_json(endpoint, payload, args.rag_timeout)
        results = normalize_rag_results(data)
    except APIError as exc:
        return {"success": False, **exc.as_dict(), "results": []}

    if data.get("success") is False:
        return {
            "success": False,
            "error": "Potato Knowledge Hub RAG API reported failure",
            "status": None,
            "url": endpoint,
            "body": truncate(data.get("error", data), 1000),
            "results": [],
        }

    return {
        "success": True,
        "query": data.get("query", args.query),
        "top_k_retrieve": args.rag_top_k_retrieve,
        "top_k_rerank": args.rag_top_k_rerank,
        "results": results,
        "raw": data,
    }


def quote_title(value: str) -> str:
    return urllib.parse.quote(value.strip(), safe="")


def build_url(api_base: str, path: str, params: Optional[dict[str, Any]] = None) -> str:
    url = api_base.rstrip("/") + path
    if params:
        clean = {key: value for key, value in params.items() if value is not None}
        if clean:
            url += "?" + urllib.parse.urlencode(clean)
    return url


def get_json(
    api_base: str,
    path: str,
    *,
    params: Optional[dict[str, Any]] = None,
    timeout: int = DEFAULT_KG_TIMEOUT,
    retries: int = DEFAULT_KG_RETRIES,
) -> dict[str, Any]:
    url = build_url(api_base, path, params)
    last_error: Optional[APIError] = None
    for attempt in range(retries + 1):
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": BROWSER_UA,
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://plantscience.ai/",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            last_error = APIError(
                f"HTTP {exc.code} from PlantScience.ai KG API",
                status=exc.code,
                url=url,
                body=body[:1000],
            )
            if exc.code in TRANSIENT_STATUS and attempt < retries:
                time.sleep(0.6 * (attempt + 1))
                continue
            raise last_error
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = APIError(f"Failed to connect to PlantScience.ai KG API: {exc}", url=url)
            if attempt < retries:
                time.sleep(0.6 * (attempt + 1))
                continue
            raise last_error

        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise APIError("PlantScience.ai KG API returned non-JSON response", url=url, body=body[:1000]) from exc
        if not isinstance(data, dict):
            raise APIError("PlantScience.ai KG API returned JSON that is not an object", url=url, body=body[:1000])
        return data

    if last_error:
        raise last_error
    raise APIError("Unknown PlantScience.ai KG API failure", url=url)


def query_kg_node(title: str, args: argparse.Namespace) -> dict[str, Any]:
    return get_json(args.kg_base_url, f"/kg/node/{quote_title(title)}", timeout=args.kg_timeout, retries=args.kg_retries)


def query_kg_neighbor(title: str, args: argparse.Namespace) -> dict[str, Any]:
    return get_json(args.kg_base_url, f"/kg/node_neighbor/{quote_title(title)}", timeout=args.kg_timeout, retries=args.kg_retries)


def query_kg_edge(source: str, target: str, args: argparse.Namespace) -> dict[str, Any]:
    return get_json(
        args.kg_base_url,
        "/kg/edge",
        params={"source": source, "target": target},
        timeout=args.kg_timeout,
        retries=args.kg_retries,
    )


def candidate_titles(title: str, aliases: Sequence[str], try_variants: bool = True) -> list[str]:
    base = unique_preserve([title, *aliases])
    if not try_variants:
        return base
    variants: list[str] = list(base)
    for item in list(base):
        upper = item.upper()
        if upper != item:
            variants.append(upper)
        compact = upper.replace("-", "").replace("_", "")
        if compact.startswith("ST") and len(compact) > 3 and any(ch.isdigit() for ch in compact[2:]):
            variants.append(compact[2:])
        if "_" in item:
            variants.append(item.replace("_", " "))
    return unique_preserve(variants)


def links_from_neighbor(neighbor: dict[str, Any]) -> list[dict[str, Any]]:
    sub_graph = neighbor.get("sub_graph") or {}
    links = sub_graph.get("links") or []
    return [item for item in links if isinstance(item, dict)]


def dedupe_links(links: Sequence[dict[str, Any]]) -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    pairs: list[tuple[str, str]] = []
    for link in links:
        source = str(link.get("source") or "").strip()
        target = str(link.get("target") or "").strip()
        if not source or not target:
            continue
        key = (source, target)
        if key in seen:
            continue
        seen.add(key)
        pairs.append(key)
    return pairs


def enrich_edges(links: Sequence[dict[str, Any]], args: argparse.Namespace, *, limit: int) -> list[dict[str, Any]]:
    details: list[dict[str, Any]] = []
    for source, target in dedupe_links(links)[:limit]:
        try:
            edge = query_kg_edge(source, target, args)
            details.append({"source": source, "target": target, "ok": True, "queried_reverse": False, "edge": edge})
            continue
        except APIError as direct_error:
            if source != target:
                try:
                    edge = query_kg_edge(target, source, args)
                    details.append({"source": source, "target": target, "ok": True, "queried_reverse": True, "edge": edge})
                    continue
                except APIError as reverse_error:
                    details.append({
                        "source": source,
                        "target": target,
                        "ok": False,
                        "error": direct_error.as_dict(),
                        "reverse_error": reverse_error.as_dict(),
                    })
            else:
                details.append({"source": source, "target": target, "ok": False, "error": direct_error.as_dict()})
    return details


def full_kg_lookup(title: str, aliases: Sequence[str], args: argparse.Namespace) -> dict[str, Any]:
    candidates = candidate_titles(title, aliases, try_variants=not args.no_kg_variants)
    errors: list[dict[str, Any]] = []
    first_node: Optional[dict[str, Any]] = None
    first_node_title: Optional[str] = None
    selected_neighbor: Optional[dict[str, Any]] = None
    selected_neighbor_title: Optional[str] = None

    for candidate in candidates:
        node: Optional[dict[str, Any]] = None
        try:
            node = query_kg_node(candidate, args)
            if first_node is None:
                first_node = node
                first_node_title = candidate
        except APIError as exc:
            errors.append({"candidate": candidate, "endpoint": "node", **exc.as_dict()})

        try:
            neighbor = query_kg_neighbor(candidate, args)
            selected_neighbor = neighbor
            selected_neighbor_title = candidate
            if first_node is None and node is not None:
                first_node = node
                first_node_title = candidate
            break
        except APIError as exc:
            errors.append({"candidate": candidate, "endpoint": "neighbor", **exc.as_dict()})

    result: dict[str, Any] = {
        "mode": "full",
        "query": title,
        "aliases": list(aliases),
        "candidates_tried": candidates,
        "selected_node_title": first_node_title,
        "selected_neighbor_title": selected_neighbor_title,
        "node": first_node,
        "neighbor": selected_neighbor,
        "errors": errors,
    }
    if selected_neighbor and not args.no_kg_edge_details:
        result["edge_details"] = enrich_edges(links_from_neighbor(selected_neighbor), args, limit=args.kg_edge_limit)
    return result


def parse_entity_spec(value: str) -> tuple[str, list[str]]:
    parts = [part.strip() for part in value.split("|") if part.strip()]
    if not parts:
        raise ValueError("empty KG entity specification")
    return parts[0], parts[1:]


def auto_extract_kg_entities(query: str) -> list[str]:
    candidates: list[str] = []
    for match in re.findall(r'"([^"]+)"|\'([^\']+)\'', query):
        candidates.extend(part for part in match if part)

    candidates.extend(re.findall(r"\b[A-Z][a-z]+ [a-z][a-z.-]+\b", query))
    candidates.extend(re.findall(r"\bSt[A-Za-z0-9_.-]{2,}\b", query))
    candidates.extend(re.findall(r"\b[A-Z]{2,}[A-Z0-9_.-]*\d[A-Z0-9_.-]*\b", query))

    query_lower = query.lower()
    for phrase in COMMON_KG_PHRASES:
        if phrase in query_lower:
            candidates.append(phrase)

    filtered: list[str] = []
    stop = {"RAG", "DOI", "DNA", "RNA", "QTL", "GWAS", "KG"}
    for candidate in unique_preserve(candidates):
        if candidate.upper() in stop:
            continue
        if candidate.upper().startswith("DM8C"):
            continue
        filtered.append(candidate)
    return filtered


def collect_kg_entities(args: argparse.Namespace) -> list[tuple[str, list[str], str]]:
    entities: list[tuple[str, list[str], str]] = []
    for value in args.kg_entity or []:
        title, aliases = parse_entity_spec(value)
        entities.append((title, aliases, "user"))

    if not args.no_auto_kg_entities:
        for title in auto_extract_kg_entities(args.query):
            entities.append((title, [], "auto"))

    seen: set[str] = set()
    deduped: list[tuple[str, list[str], str]] = []
    for title, aliases, source in entities:
        key = title.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append((title, aliases, source))
    return deduped[: args.max_kg_entities]


def run_kg(args: argparse.Namespace) -> dict[str, Any]:
    entities = collect_kg_entities(args)
    if not entities:
        return {
            "success": True,
            "skipped": True,
            "entities": [],
            "warning": "No KG entity was provided or detected. Pass --kg-entity for PlantScience.ai KG lookup.",
        }

    results: list[dict[str, Any]] = []
    any_hit = False
    for title, aliases, entity_source in entities:
        result = full_kg_lookup(title, aliases, args)
        hit = bool(result.get("node") or result.get("neighbor"))
        any_hit = any_hit or hit
        results.append({
            "entity": title,
            "aliases": aliases,
            "entity_source": entity_source,
            "success": hit,
            "result": result,
        })

    return {
        "success": any_hit,
        "skipped": False,
        "entities": results,
    }


def doi_sample(dois: Any, limit: int = 5) -> str:
    if not isinstance(dois, list) or not dois:
        return ""
    return ", ".join(str(item) for item in dois[:limit])


def summarize_node(node: dict[str, Any]) -> list[str]:
    dois = node.get("dois") or []
    lines = [
        f"Title: {node.get('title', '')}",
        f"ID: {node.get('id', '')} | Type: {node.get('type', '')}",
        f"DOI count: {len(dois) if isinstance(dois, list) else 0}",
    ]
    if dois:
        lines.append(f"DOI sample: {doi_sample(dois)}")
    if node.get("description"):
        lines.append(f"Description: {truncate(node.get('description'), 600)}")
    return lines


def summarize_edge(edge: dict[str, Any]) -> str:
    src = edge.get("source", "")
    tgt = edge.get("target", "")
    typ = edge.get("type", "")
    edge_id = edge.get("id", "")
    dois = doi_sample(edge.get("dois"), 4)
    desc = truncate(edge.get("description"), 360)
    suffix = f" | DOI: {dois}" if dois else ""
    return f"- {src} -> {tgt} [{typ}, id={edge_id}]: {desc}{suffix}"


def summarize_neighbor(neighbor: dict[str, Any], *, max_items: int) -> list[str]:
    lines: list[str] = []
    lines.extend(summarize_node(neighbor))
    sub_graph = neighbor.get("sub_graph") or {}
    nodes = sub_graph.get("nodes") or []
    links = sub_graph.get("links") or []
    categories = sub_graph.get("categories") or []
    lines.append(f"Neighbors reported: {neighbor.get('all_neighbors_count', '')}")
    lines.append(f"Subgraph: nodes={len(nodes)} links={len(links)} categories={len(categories)}")
    if categories:
        category_names = [str(item.get("name", "")) for item in categories if isinstance(item, dict)]
        lines.append("Categories: " + ", ".join(category_names[:max_items]))
    if links:
        lines.append("Links:")
        for source, target in dedupe_links(links)[:max_items]:
            lines.append(f"- {source} -> {target}")
    return lines


def format_summary(data: dict[str, Any], args: argparse.Namespace) -> str:
    lines: list[str] = [f"Query: {data.get('query', '')}", ""]

    rag = data.get("rag") or {}
    lines.append("Potato Knowledge Hub RAG evidence:")
    if rag.get("success"):
        results = normalize_rag_results(rag)
        lines.append(f"Results: {len(results)}")
        for row in results[: args.rag_top_k_rerank]:
            title = row.get("title") or "No title returned"
            doi = row.get("doi") or "No DOI returned"
            snippet = truncate(row.get("text"), args.max_text_chars)
            lines.extend(
                [
                    "",
                    f"[{row.get('rank', '')}] {title}",
                    f"DOI: {doi}",
                    f"Score: {row.get('score', '')}",
                    "Text:",
                    textwrap.fill(snippet, width=100, replace_whitespace=False),
                ]
            )
    else:
        lines.append(f"Unavailable: {rag.get('error', 'RAG lookup was not run')}")

    kg = data.get("kg") or {}
    lines.extend(["", "PlantScience.ai KG evidence:"])
    if kg.get("skipped"):
        lines.append(kg.get("warning", "KG lookup skipped."))
    elif kg.get("entities"):
        for item in kg.get("entities", [])[: args.summary_kg_limit]:
            result = item.get("result") or {}
            lines.extend(["", f"Entity: {item.get('entity')} ({item.get('entity_source')})"])
            lines.append("Candidates tried: " + ", ".join(result.get("candidates_tried") or []))
            if result.get("node"):
                lines.append("Node:")
                lines.extend(summarize_node(result["node"]))
            else:
                lines.append("Node: none")
            if result.get("neighbor"):
                lines.append(f"Neighbor selected by title: {result.get('selected_neighbor_title')}")
                lines.extend(summarize_neighbor(result["neighbor"], max_items=args.summary_kg_limit))
            else:
                lines.append("Neighbor: none")
            if result.get("edge_details"):
                lines.append("Edge details:")
                for edge_item in result["edge_details"][: args.summary_kg_limit]:
                    if edge_item.get("ok"):
                        note = " (reverse query)" if edge_item.get("queried_reverse") else ""
                        lines.append(summarize_edge(edge_item["edge"]) + note)
                    else:
                        error = edge_item.get("error", {})
                        lines.append(f"- {edge_item.get('source')} -> {edge_item.get('target')}: ERROR {error.get('status')} {truncate(error.get('body'), 120)}")
            if result.get("errors"):
                lines.append(f"Non-fatal KG lookup errors: {len(result['errors'])}")
    else:
        lines.append("No KG results returned.")

    if data.get("warnings"):
        lines.extend(["", "Warnings:"])
        for warning in data["warnings"]:
            lines.append(f"- {warning}")

    lines.extend([
        "",
        "Interpretation note: RAG snippets are potato literature retrieval evidence. PlantScience.ai KG entries are automatically extracted graph evidence and should be labeled separately in downstream answers.",
    ])
    return "\n".join(lines)


def iter_tsv_rows(data: dict[str, Any]) -> Iterable[dict[str, str]]:
    rag = data.get("rag") or {}
    if rag.get("success"):
        for row in normalize_rag_results(rag):
            yield {
                "source": "rag",
                "entity": "",
                "rank": str(row.get("rank", "")),
                "score": str(row.get("score", "")),
                "title": str(row.get("title", "")),
                "doi": str(row.get("doi", "")),
                "relation": "",
                "text": truncate(row.get("text"), 0),
            }

    kg = data.get("kg") or {}
    for item in kg.get("entities") or []:
        entity = str(item.get("entity", ""))
        result = item.get("result") or {}
        node = result.get("node")
        if isinstance(node, dict):
            yield {
                "source": "kg_node",
                "entity": entity,
                "rank": "",
                "score": "",
                "title": str(node.get("title", "")),
                "doi": doi_sample(node.get("dois"), 50),
                "relation": "",
                "text": truncate(node.get("description"), 0),
            }
        for edge_item in result.get("edge_details") or []:
            if not edge_item.get("ok"):
                continue
            edge = edge_item.get("edge") or {}
            relation = f"{edge.get('source', '')} -> {edge.get('target', '')} [{edge.get('type', '')}]"
            yield {
                "source": "kg_edge",
                "entity": entity,
                "rank": "",
                "score": "",
                "title": "",
                "doi": doi_sample(edge.get("dois"), 50),
                "relation": relation,
                "text": truncate(edge.get("description"), 0),
            }


def format_tsv(data: dict[str, Any]) -> str:
    output = io.StringIO()
    fields = ["source", "entity", "rank", "score", "title", "doi", "relation", "text"]
    writer = csv.DictWriter(output, fieldnames=fields, delimiter="\t", extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in iter_tsv_rows(data):
        writer.writerow(row)
    return output.getvalue().rstrip("\n")


def positive_int(name: str, value: int) -> None:
    if value <= 0:
        raise ValueError(f"{name} must be positive")


def non_negative_int(name: str, value: int) -> None:
    if value < 0:
        raise ValueError(f"{name} must be non-negative")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Query Potato Knowledge Hub RAG evidence and PlantScience.ai KG "
            "entity/relationship evidence for a potato knowledge question."
        )
    )
    parser.add_argument("query", help="Question or retrieval text.")
    parser.add_argument("--format", choices=("json", "summary", "tsv"), default="json", help="Output format. Default: json.")

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--rag-only", action="store_true", help="Only query Potato Knowledge Hub RAG.")
    mode.add_argument("--kg-only", action="store_true", help="Only query PlantScience.ai KG.")

    parser.add_argument("--rag-base-url", default=os.environ.get("POTATO_RAG_BASE_URL", DEFAULT_RAG_BASE_URL), help=f"Potato RAG service base URL. Default: {DEFAULT_RAG_BASE_URL}.")
    parser.add_argument("--rag-top-k-retrieve", type=int, default=DEFAULT_RAG_TOP_K_RETRIEVE, help=f"RAG vector candidates. Default: {DEFAULT_RAG_TOP_K_RETRIEVE}.")
    parser.add_argument("--rag-top-k-rerank", type=int, default=DEFAULT_RAG_TOP_K_RERANK, help=f"RAG reranked results and summary items. Default: {DEFAULT_RAG_TOP_K_RERANK}.")
    parser.add_argument("--rag-timeout", type=int, default=DEFAULT_RAG_TIMEOUT, help=f"RAG HTTP timeout seconds. Default: {DEFAULT_RAG_TIMEOUT}.")

    parser.add_argument(
        "--kg-entity",
        action="append",
        default=[],
        help=(
            "PlantScience.ai KG entity to query. Repeat as needed. "
            "Use 'primary|alias1|alias2' to provide aliases."
        ),
    )
    parser.add_argument("--kg-base-url", default=os.environ.get("PLANT_SCIENCE_KG_BASE_URL", DEFAULT_KG_BASE_URL), help=f"PlantScience.ai KG API base URL. Default: {DEFAULT_KG_BASE_URL}.")
    parser.add_argument("--kg-timeout", type=int, default=DEFAULT_KG_TIMEOUT, help=f"KG HTTP timeout seconds. Default: {DEFAULT_KG_TIMEOUT}.")
    parser.add_argument("--kg-retries", type=int, default=DEFAULT_KG_RETRIES, help=f"KG retries for transient errors. Default: {DEFAULT_KG_RETRIES}.")
    parser.add_argument("--kg-edge-limit", type=int, default=DEFAULT_KG_EDGE_LIMIT, help=f"Maximum unique KG links to enrich per entity. Default: {DEFAULT_KG_EDGE_LIMIT}.")
    parser.add_argument("--max-kg-entities", type=int, default=DEFAULT_MAX_KG_ENTITIES, help=f"Maximum KG entities to query. Default: {DEFAULT_MAX_KG_ENTITIES}.")
    parser.add_argument("--no-auto-kg-entities", action="store_true", help="Disable lightweight automatic entity extraction from the query.")
    parser.add_argument("--no-kg-variants", action="store_true", help="Disable automatic uppercase/St-prefix KG title variants.")
    parser.add_argument("--no-kg-edge-details", action="store_true", help="Skip /kg/edge enrichment for returned neighbor links.")

    parser.add_argument("--summary-kg-limit", type=int, default=DEFAULT_SUMMARY_KG_LIMIT, help=f"KG items shown in summary. Default: {DEFAULT_SUMMARY_KG_LIMIT}.")
    parser.add_argument("--max-text-chars", type=int, default=700, help="Maximum snippet characters per RAG result in summary output. Default: 700.")
    return parser


def validate_args(args: argparse.Namespace) -> None:
    args.query = args.query.strip()
    if not args.query:
        raise ValueError("query must not be empty")
    positive_int("--rag-top-k-retrieve", args.rag_top_k_retrieve)
    positive_int("--rag-top-k-rerank", args.rag_top_k_rerank)
    positive_int("--rag-timeout", args.rag_timeout)
    positive_int("--kg-timeout", args.kg_timeout)
    non_negative_int("--kg-retries", args.kg_retries)
    positive_int("--kg-edge-limit", args.kg_edge_limit)
    positive_int("--max-kg-entities", args.max_kg_entities)
    positive_int("--summary-kg-limit", args.summary_kg_limit)
    non_negative_int("--max-text-chars", args.max_text_chars)
    args.kg_base_url = normalize_kg_base_url(args.kg_base_url)


def run(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    warnings: list[str] = []
    data: dict[str, Any] = {
        "success": False,
        "query": args.query,
        "rag": {"success": None, "skipped": True, "results": []},
        "kg": {"success": None, "skipped": True, "entities": []},
        "warnings": warnings,
    }

    if not args.kg_only:
        data["rag"] = run_rag(args)
        if not data["rag"].get("success"):
            warnings.append(f"RAG lookup failed: {data['rag'].get('error')}")

    if not args.rag_only:
        data["kg"] = run_kg(args)
        if data["kg"].get("skipped"):
            warnings.append(data["kg"].get("warning", "KG lookup skipped."))
        elif not data["kg"].get("success"):
            warnings.append("KG lookup ran but returned no usable node or neighbor results.")

    rag_ok = bool(data.get("rag", {}).get("success"))
    kg_ok = bool(data.get("kg", {}).get("success"))
    kg_skipped = bool(data.get("kg", {}).get("skipped"))
    data["success"] = rag_ok or kg_ok or (args.rag_only and rag_ok) or (kg_skipped and rag_ok)
    return (0 if data["success"] else 1), data


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        validate_args(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    exit_code, data = run(args)
    if args.format == "json":
        print(json.dumps(data, ensure_ascii=False, indent=2))
    elif args.format == "summary":
        print(format_summary(data, args))
    elif args.format == "tsv":
        print(format_tsv(data))
    else:
        print(f"error: unsupported format: {args.format}", file=sys.stderr)
        return 2
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
