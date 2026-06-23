#!/usr/bin/env python3
"""Query public PlantScience.ai Knowledge Graph endpoints.

This script intentionally uses only the Python standard library so Hermes agents
can run it directly from the skill directory.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

DEFAULT_API_BASE = "https://plantscience.ai/api"
DEFAULT_TIMEOUT = 30
DEFAULT_RETRIES = 2
DEFAULT_EDGE_LIMIT = 50
DEFAULT_MAX_ITEMS = 50
TRANSIENT_STATUS = {502, 503, 504}
BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class KGError(RuntimeError):
    """Raised when the PlantScience.ai KG API cannot return usable JSON."""

    def __init__(self, message: str, *, status: Optional[int] = None, url: Optional[str] = None, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.url = url
        self.body = body

    def as_dict(self) -> Dict[str, Any]:
        return {"error": str(self), "status": self.status, "url": self.url, "body": self.body}


def normalize_api_base(value: str) -> str:
    base = (value or DEFAULT_API_BASE).rstrip("/")
    if not base.endswith("/api"):
        base += "/api"
    return base


def quote_title(value: str) -> str:
    return urllib.parse.quote(value.strip(), safe="")


def build_url(api_base: str, path: str, params: Optional[Dict[str, Any]] = None) -> str:
    url = api_base.rstrip("/") + path
    if params:
        clean = {k: v for k, v in params.items() if v is not None}
        if clean:
            url += "?" + urllib.parse.urlencode(clean)
    return url


def get_json(api_base: str, path: str, *, params: Optional[Dict[str, Any]] = None,
             timeout: int = DEFAULT_TIMEOUT, retries: int = DEFAULT_RETRIES) -> Dict[str, Any]:
    url = build_url(api_base, path, params)
    last_error: Optional[KGError] = None
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
            last_error = KGError(
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
            last_error = KGError(f"Failed to connect to PlantScience.ai KG API: {exc}", url=url)
            if attempt < retries:
                time.sleep(0.6 * (attempt + 1))
                continue
            raise last_error

        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise KGError("PlantScience.ai KG API returned non-JSON response", url=url, body=body[:1000]) from exc
        if not isinstance(data, dict):
            raise KGError("PlantScience.ai KG API returned JSON that is not an object", url=url, body=body[:1000])
        return data

    # Defensive fallback; loop either returns or raises.
    if last_error:
        raise last_error
    raise KGError("Unknown PlantScience.ai KG API failure", url=url)


def query_node(title: str, args: argparse.Namespace) -> Dict[str, Any]:
    return get_json(args.api_base, f"/kg/node/{quote_title(title)}", timeout=args.timeout, retries=args.retries)


def query_neighbor(title: str, args: argparse.Namespace) -> Dict[str, Any]:
    return get_json(args.api_base, f"/kg/node_neighbor/{quote_title(title)}", timeout=args.timeout, retries=args.retries)


def query_edge(source: str, target: str, args: argparse.Namespace) -> Dict[str, Any]:
    return get_json(args.api_base, "/kg/edge", params={"source": source, "target": target}, timeout=args.timeout, retries=args.retries)


def query_entity(entity_id: str, entity_type: Optional[str], args: argparse.Namespace) -> Dict[str, Any]:
    return get_json(args.api_base, f"/kg/entity/{quote_title(entity_id)}", params={"type": entity_type}, timeout=args.timeout, retries=args.retries)


def unique_preserve(values: Iterable[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for value in values:
        v = " ".join(str(value).strip().split())
        if not v or v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


def candidate_titles(title: str, aliases: Sequence[str], try_variants: bool = True) -> List[str]:
    base = unique_preserve([title, *aliases])
    if not try_variants:
        return base
    variants: List[str] = list(base)
    for item in list(base):
        upper = item.upper()
        if upper != item:
            variants.append(upper)
        # Common potato gene shorthand fallback: StSP6A -> SP6A, StSWEET11 -> SWEET11.
        compact = upper.replace("-", "").replace("_", "")
        if compact.startswith("ST") and len(compact) > 3 and any(ch.isdigit() for ch in compact[2:]):
            variants.append(compact[2:])
        if "_" in item:
            variants.append(item.replace("_", " "))
    return unique_preserve(variants)


def links_from_neighbor(neighbor: Dict[str, Any]) -> List[Dict[str, Any]]:
    sub_graph = neighbor.get("sub_graph") or {}
    links = sub_graph.get("links") or []
    return [x for x in links if isinstance(x, dict)]


def dedupe_links(links: Sequence[Dict[str, Any]]) -> List[Tuple[str, str]]:
    seen = set()
    pairs: List[Tuple[str, str]] = []
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


def enrich_edges(links: Sequence[Dict[str, Any]], args: argparse.Namespace, *, limit: int, try_reverse: bool = True) -> List[Dict[str, Any]]:
    details: List[Dict[str, Any]] = []
    for source, target in dedupe_links(links)[:limit]:
        try:
            edge = query_edge(source, target, args)
            details.append({"source": source, "target": target, "ok": True, "queried_reverse": False, "edge": edge})
            continue
        except KGError as direct_error:
            if try_reverse and source != target:
                try:
                    edge = query_edge(target, source, args)
                    details.append({"source": source, "target": target, "ok": True, "queried_reverse": True, "edge": edge})
                    continue
                except KGError as reverse_error:
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


def full_lookup(title: str, args: argparse.Namespace) -> Dict[str, Any]:
    candidates = candidate_titles(title, args.alias or [], try_variants=args.try_variants)
    errors: List[Dict[str, Any]] = []
    first_node: Optional[Dict[str, Any]] = None
    first_node_title: Optional[str] = None
    selected_neighbor: Optional[Dict[str, Any]] = None
    selected_neighbor_title: Optional[str] = None

    for candidate in candidates:
        node: Optional[Dict[str, Any]] = None
        try:
            node = query_node(candidate, args)
            if first_node is None:
                first_node = node
                first_node_title = candidate
        except KGError as exc:
            errors.append({"candidate": candidate, "endpoint": "node", **exc.as_dict()})

        try:
            neighbor = query_neighbor(candidate, args)
            selected_neighbor = neighbor
            selected_neighbor_title = candidate
            if first_node is None and node is not None:
                first_node = node
                first_node_title = candidate
            break
        except KGError as exc:
            errors.append({"candidate": candidate, "endpoint": "neighbor", **exc.as_dict()})

    result: Dict[str, Any] = {
        "mode": "full",
        "query": title,
        "candidates_tried": candidates,
        "selected_node_title": first_node_title,
        "selected_neighbor_title": selected_neighbor_title,
        "node": first_node,
        "neighbor": selected_neighbor,
        "errors": errors,
    }
    if selected_neighbor and not args.no_edge_details:
        result["edge_details"] = enrich_edges(links_from_neighbor(selected_neighbor), args, limit=args.edge_limit, try_reverse=True)
    return result


def truncate(value: Any, max_chars: int = 260) -> str:
    text = "" if value is None else " ".join(str(value).split())
    if max_chars > 0 and len(text) > max_chars:
        return text[: max_chars - 1].rstrip() + "…"
    return text


def doi_sample(dois: Any, limit: int = 5) -> str:
    if not isinstance(dois, list) or not dois:
        return ""
    return ", ".join(str(x) for x in dois[:limit])


def describe_node(node: Dict[str, Any]) -> List[str]:
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


def summarize_edge(edge: Dict[str, Any]) -> str:
    src = edge.get("source", "")
    tgt = edge.get("target", "")
    typ = edge.get("type", "")
    eid = edge.get("id", "")
    dois = doi_sample(edge.get("dois"), 4)
    desc = truncate(edge.get("description"), 360)
    suffix = f" | DOI: {dois}" if dois else ""
    return f"- {src} -> {tgt} [{typ}, id={eid}]: {desc}{suffix}"


def summarize_neighbor(neighbor: Dict[str, Any], *, max_items: int = 10) -> List[str]:
    lines: List[str] = []
    lines.extend(describe_node(neighbor))
    sub_graph = neighbor.get("sub_graph") or {}
    nodes = sub_graph.get("nodes") or []
    links = sub_graph.get("links") or []
    categories = sub_graph.get("categories") or []
    lines.append(f"Neighbors reported: {neighbor.get('all_neighbors_count', '')}")
    lines.append(f"Subgraph: nodes={len(nodes)} links={len(links)} categories={len(categories)}")
    if categories:
        category_names = [str(x.get("name", "")) for x in categories if isinstance(x, dict)]
        lines.append("Categories: " + ", ".join(category_names[:max_items]))
    if nodes:
        lines.append("Nodes:")
        for node in nodes[:max_items]:
            if isinstance(node, dict):
                lines.append(f"- {node.get('id') or node.get('name')} | category={node.get('category')} | symbolSize={node.get('symbolSize')}")
    if links:
        lines.append("Links:")
        for source, target in dedupe_links(links)[:max_items]:
            lines.append(f"- {source} -> {target}")
    return lines


def format_summary(result: Dict[str, Any], *, max_items: int = 10) -> str:
    mode = result.get("mode")
    lines: List[str] = [f"Mode: {mode}"]

    if mode == "node":
        lines.extend(describe_node(result["node"]))
    elif mode == "neighbor":
        lines.extend(summarize_neighbor(result["neighbor"], max_items=max_items))
        if result.get("edge_details"):
            lines.append("\nEdge details:")
            for item in result["edge_details"][:max_items]:
                if item.get("ok"):
                    note = " (reverse query)" if item.get("queried_reverse") else ""
                    lines.append(summarize_edge(item["edge"]) + note)
                else:
                    lines.append(f"- {item.get('source')} -> {item.get('target')}: ERROR {item.get('error', {}).get('status')} {item.get('error', {}).get('body', '')[:120]}")
    elif mode == "edge":
        if result.get("queried_reverse"):
            lines.append("Note: direct query failed; reverse direction returned this relationship.")
        lines.append(summarize_edge(result["edge"]))
    elif mode == "entity":
        entity = result["entity"]
        lines.append(f"Entity type: {entity.get('entity_type', '')}")
        if entity.get("entity_type") == "node" or "title" in entity:
            lines.extend(describe_node(entity))
        else:
            lines.append(json.dumps(entity, ensure_ascii=False, indent=2)[:2000])
    elif mode == "full":
        lines.append(f"Query: {result.get('query')}")
        lines.append("Candidates tried: " + ", ".join(result.get("candidates_tried") or []))
        if result.get("node"):
            lines.append("\nNode result:")
            lines.extend(describe_node(result["node"]))
        else:
            lines.append("\nNode result: none")
        if result.get("neighbor"):
            lines.append(f"\nNeighbor result selected by title: {result.get('selected_neighbor_title')}")
            lines.extend(summarize_neighbor(result["neighbor"], max_items=max_items))
        else:
            lines.append("\nNeighbor result: none")
        if result.get("edge_details"):
            lines.append("\nEdge details:")
            for item in result["edge_details"][:max_items]:
                if item.get("ok"):
                    note = " (reverse query)" if item.get("queried_reverse") else ""
                    lines.append(summarize_edge(item["edge"]) + note)
                else:
                    lines.append(f"- {item.get('source')} -> {item.get('target')}: ERROR {item.get('error', {}).get('status')} {item.get('error', {}).get('body', '')[:120]}")
        if result.get("errors"):
            lines.append(f"\nNon-fatal lookup errors: {len(result['errors'])}")
            for err in result["errors"][:max_items]:
                lines.append(f"- {err.get('endpoint')} {err.get('candidate')}: status={err.get('status')} {truncate(err.get('body'), 120)}")
    else:
        lines.append(json.dumps(result, ensure_ascii=False, indent=2))
    return "\n".join(lines)


def edge_rows_from_result(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if result.get("mode") == "edge" and result.get("edge"):
        edge = result["edge"]
        rows.append(edge_to_row(edge, ok=True, queried_reverse=result.get("queried_reverse", False)))
    elif result.get("edge_details"):
        for item in result["edge_details"]:
            if item.get("ok"):
                rows.append(edge_to_row(item["edge"], ok=True, queried_reverse=item.get("queried_reverse", False)))
            else:
                rows.append({
                    "source": item.get("source", ""),
                    "target": item.get("target", ""),
                    "ok": "false",
                    "queried_reverse": "",
                    "type": "",
                    "id": "",
                    "dois": "",
                    "description": "",
                    "error": item.get("error", {}).get("body") or item.get("error", {}).get("error", ""),
                })
    elif result.get("mode") == "neighbor" and result.get("neighbor"):
        for source, target in dedupe_links(links_from_neighbor(result["neighbor"])):
            rows.append({"source": source, "target": target, "ok": "", "queried_reverse": "", "type": "", "id": "", "dois": "", "description": "", "error": ""})
    return rows


def edge_to_row(edge: Dict[str, Any], *, ok: bool, queried_reverse: bool) -> Dict[str, Any]:
    dois = edge.get("dois") or []
    return {
        "source": edge.get("source", ""),
        "target": edge.get("target", ""),
        "ok": "true" if ok else "false",
        "queried_reverse": "true" if queried_reverse else "false",
        "type": edge.get("type", ""),
        "id": edge.get("id", ""),
        "dois": ";".join(str(x) for x in dois) if isinstance(dois, list) else "",
        "description": truncate(edge.get("description"), 0),
        "error": "",
    }


def format_tsv(result: Dict[str, Any]) -> str:
    output = io.StringIO()
    rows = edge_rows_from_result(result)
    if not rows and result.get("mode") == "node" and result.get("node"):
        node = result["node"]
        rows = [{
            "title": node.get("title", ""),
            "id": node.get("id", ""),
            "type": node.get("type", ""),
            "doi_count": len(node.get("dois") or []),
            "description": truncate(node.get("description"), 0),
        }]
        fields = ["title", "id", "type", "doi_count", "description"]
    else:
        fields = ["source", "target", "ok", "queried_reverse", "type", "id", "dois", "description", "error"]
    writer = csv.DictWriter(output, fieldnames=fields, delimiter="\t", extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return output.getvalue().rstrip("\n")


def add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--base-url", default=os.environ.get("PLANT_SCIENCE_KG_BASE_URL", DEFAULT_API_BASE), help=f"API base URL. Default: {DEFAULT_API_BASE}.")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help=f"HTTP timeout seconds. Default: {DEFAULT_TIMEOUT}.")
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES, help=f"Retries for transient errors. Default: {DEFAULT_RETRIES}.")
    parser.add_argument("--format", choices=("json", "summary", "tsv"), default="json", help="Output format. Default: json.")
    parser.add_argument("--max-items", type=int, default=DEFAULT_MAX_ITEMS, help=f"Maximum items shown in summary. Default: {DEFAULT_MAX_ITEMS}.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Query public PlantScience.ai Knowledge Graph endpoints.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p = subparsers.add_parser("node", help="Query /kg/node/{node_title}.")
    p.add_argument("title", help="KG node title, e.g. SP6A or STSWEET11.")
    add_common_options(p)

    p = subparsers.add_parser("neighbor", help="Query /kg/node_neighbor/{node_title}.")
    p.add_argument("title", help="KG node title.")
    p.add_argument("--with-edges", action="store_true", help="Fetch /kg/edge details for returned links.")
    p.add_argument("--edge-limit", type=int, default=DEFAULT_EDGE_LIMIT, help=f"Maximum unique links to enrich. Default: {DEFAULT_EDGE_LIMIT}.")
    add_common_options(p)

    p = subparsers.add_parser("edge", help="Query /kg/edge?source=...&target=....")
    p.add_argument("source", help="Source node title.")
    p.add_argument("target", help="Target node title.")
    p.add_argument("--no-try-reverse", dest="try_reverse", action="store_false", help="Do not try target->source if source->target fails.")
    p.set_defaults(try_reverse=True)
    add_common_options(p)

    p = subparsers.add_parser("entity", help="Query /kg/entity/{entity_id}; entity_id must be numeric.")
    p.add_argument("entity_id", help="Numeric node or edge ID.")
    p.add_argument("--type", choices=("node", "edge"), default=None, help="Optional entity type hint.")
    add_common_options(p)

    p = subparsers.add_parser("full", help="Try node + neighbor + edge details with aliases and simple variants.")
    p.add_argument("title", help="Primary node title or entity name.")
    p.add_argument("--alias", action="append", default=[], help="Alias/fallback node title. Can be repeated.")
    p.add_argument("--no-try-variants", dest="try_variants", action="store_false", help="Disable automatic uppercase/St-prefix variants.")
    p.add_argument("--edge-limit", type=int, default=DEFAULT_EDGE_LIMIT, help=f"Maximum unique links to enrich. Default: {DEFAULT_EDGE_LIMIT}.")
    p.add_argument("--no-edge-details", action="store_true", help="Do not fetch edge relationship details.")
    p.set_defaults(try_variants=True)
    add_common_options(p)

    return parser


def validate_common(args: argparse.Namespace) -> None:
    if args.timeout <= 0:
        raise ValueError("--timeout must be positive")
    if args.retries < 0:
        raise ValueError("--retries must be non-negative")
    if args.max_items <= 0:
        raise ValueError("--max-items must be positive")
    if hasattr(args, "edge_limit") and args.edge_limit <= 0:
        raise ValueError("--edge-limit must be positive")
    args.api_base = normalize_api_base(args.base_url)


def run(args: argparse.Namespace) -> Dict[str, Any]:
    if args.command == "node":
        return {"mode": "node", "query": args.title, "node": query_node(args.title, args)}
    if args.command == "neighbor":
        neighbor = query_neighbor(args.title, args)
        result: Dict[str, Any] = {"mode": "neighbor", "query": args.title, "neighbor": neighbor}
        if args.with_edges:
            result["edge_details"] = enrich_edges(links_from_neighbor(neighbor), args, limit=args.edge_limit, try_reverse=True)
        return result
    if args.command == "edge":
        try:
            edge = query_edge(args.source, args.target, args)
            return {"mode": "edge", "query": {"source": args.source, "target": args.target}, "queried_reverse": False, "edge": edge}
        except KGError:
            if not args.try_reverse or args.source == args.target:
                raise
            edge = query_edge(args.target, args.source, args)
            return {"mode": "edge", "query": {"source": args.source, "target": args.target}, "queried_reverse": True, "edge": edge}
    if args.command == "entity":
        return {"mode": "entity", "query": args.entity_id, "entity": query_entity(args.entity_id, args.type, args)}
    if args.command == "full":
        return full_lookup(args.title, args)
    raise ValueError(f"Unsupported command: {args.command}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        validate_common(args)
        result = run(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KGError as exc:
        if getattr(args, "format", "json") == "json":
            print(json.dumps({"success": False, **exc.as_dict()}, ensure_ascii=False, indent=2), file=sys.stderr)
        else:
            print(f"error: {exc}; status={exc.status}; url={exc.url}; body={truncate(exc.body, 400)}", file=sys.stderr)
        return 1

    result["success"] = True
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.format == "summary":
        print(format_summary(result, max_items=args.max_items))
    elif args.format == "tsv":
        print(format_tsv(result))
    else:
        print(f"error: unsupported format {args.format}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
