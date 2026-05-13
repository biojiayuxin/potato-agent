#!/usr/bin/env python3
"""Query Arabidopsis genes via TAIR + PlantConnectome.

Workflow:
1. Use TAIR public search API to resolve gene symbol/alias/AGI ID.
2. If multiple exact candidates exist, return ambiguous status for user confirmation.
3. Use confirmed TAIR AGI ID to query PlantConnectome KG relationships and PMIDs.

Only Python standard library is required.
"""
from __future__ import annotations

import argparse
import ast
import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from typing import Any, Dict, List, Optional, Sequence, Tuple

TAIR_API_BASE = "https://www.arabidopsis.org/api"
PLANTCONNECTOME_BASE = "https://plant.connectome.tools"
BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def http_json(url: str, *, method: str = "GET", payload: Optional[dict] = None,
              timeout: int = 60, referer: str = "https://www.arabidopsis.org/") -> Any:
    data = None
    headers = {
        "User-Agent": BROWSER_UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": referer,
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json;charset=UTF-8"
        parsed = urllib.parse.urlsplit(url)
        headers["Origin"] = f"{parsed.scheme}://{parsed.netloc}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def http_text(url: str, *, method: str = "GET", payload: Optional[dict] = None,
              timeout: int = 60, referer: Optional[str] = None) -> Tuple[str, str]:
    data = None
    headers = {
        "User-Agent": BROWSER_UA,
        "Accept": "text/html,application/json,text/plain,*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if referer:
        headers["Referer"] = referer
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json;charset=UTF-8"
        parsed = urllib.parse.urlsplit(url)
        headers["Origin"] = f"{parsed.scheme}://{parsed.netloc}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace"), resp.geturl()


def normalize_query(q: str) -> str:
    return q.strip().upper()


def is_agi_id(q: str) -> bool:
    return bool(re.fullmatch(r"AT[1-5CM]G\d{5}(?:\.\d+)?", q.strip().upper()))


def tair_search(query: str, *, timeout: int = 60) -> Dict[str, Any]:
    data = http_json(
        f"{TAIR_API_BASE}/search/gene",
        method="POST",
        payload={"searchText": query.strip()},
        timeout=timeout,
        referer="https://www.arabidopsis.org/search/genes",
    )
    return {"query": query, "total": data.get("total"), "docs": data.get("docs") or [], "raw": data}


def summarize_tair_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    desc = doc.get("description") or []
    return {
        "id": doc.get("id"),
        "gene_id": (doc.get("gene_name") or [None])[0],
        "gene_model_ids": doc.get("gene_model_ids") or [],
        "other_names": doc.get("other_names") or [],
        "description": desc[0] if desc else "",
        "keywords": doc.get("keywords") or [],
        "keyword_types": doc.get("keyword_types") or [],
        "phenotypes": doc.get("phenotypes") or [],
        "gene_model_type": doc.get("gene_model_type") or [],
        "locus_tairObjectId": doc.get("locus_tairObjectId"),
        "gene_tairObjectId": doc.get("gene_tairObjectId"),
        "has_publications": doc.get("has_publications"),
        "is_obselete": doc.get("is_obselete"),
    }


def exact_matches(docs: Sequence[Dict[str, Any]], query: str) -> List[Dict[str, Any]]:
    q = normalize_query(query)
    out: List[Dict[str, Any]] = []
    for d in docs:
        gene_id = ((d.get("gene_name") or [""])[0] or "").upper()
        gene_models = [str(x).upper() for x in d.get("gene_model_ids") or []]
        aliases = [str(x).upper() for x in d.get("other_names") or []]
        if q == gene_id or q in gene_models or q in aliases:
            out.append(d)
    return out


def choose_tair_candidate(query: str, docs: Sequence[Dict[str, Any]], *,
                          forced_gene_id: Optional[str] = None,
                          timeout: int = 60) -> Dict[str, Any]:
    if forced_gene_id:
        fg = forced_gene_id.upper()
        search_sets = [docs]
        if not docs:
            search_sets.append(tair_search(forced_gene_id, timeout=timeout).get("docs", []))
        else:
            try:
                search_sets.append(tair_search(forced_gene_id, timeout=timeout).get("docs", []))
            except Exception:
                pass
        for group in search_sets:
            for d in group:
                gene_id = ((d.get("gene_name") or [""])[0] or "").upper()
                gene_models = [str(x).upper() for x in d.get("gene_model_ids") or []]
                if fg == gene_id or fg in gene_models:
                    return {"status": "ok", "selected": d, "reason": "forced_gene_id matched TAIR"}
        return {"status": "error", "message": f"forced_gene_id {forced_gene_id} not found in TAIR results"}

    if not docs:
        return {"status": "not_found", "message": "TAIR returned no gene candidates"}

    q = normalize_query(query)
    if is_agi_id(q):
        q_gene = q.split(".")[0]
        for d in docs:
            gene_id = ((d.get("gene_name") or [""])[0] or "").upper()
            gene_models = [str(x).upper() for x in d.get("gene_model_ids") or []]
            if q_gene == gene_id or q in gene_models:
                return {"status": "ok", "selected": d, "reason": "AGI ID uniquely identifies the candidate"}

    exact = exact_matches(docs, query)
    seen = set()
    uniq_exact = []
    for d in exact:
        gid = (d.get("gene_name") or [""])[0]
        if gid not in seen:
            seen.add(gid)
            uniq_exact.append(d)
    if len(uniq_exact) == 1:
        return {"status": "ok", "selected": uniq_exact[0], "reason": "single exact TAIR alias/gene match"}
    if len(uniq_exact) > 1:
        return {"status": "ambiguous", "candidates": uniq_exact, "reason": "multiple exact TAIR alias/gene matches"}
    if len(docs) == 1:
        return {"status": "ok", "selected": docs[0], "reason": "single TAIR search result"}
    return {"status": "ambiguous", "candidates": list(docs[:10]), "reason": "multiple TAIR candidates and no unique exact match"}


def parse_preview(html_text: str) -> Tuple[Optional[str], List[List[Any]]]:
    uid_match = re.search(r'const\s+unique_id\s*=\s*"([^"]+)"', html_text)
    uid = uid_match.group(1) if uid_match else None
    m = re.search(
        r"allRowsData\s*=\s*cached\s*\?\s*cached\.preview_results\s*:\s*(.*?);\s*\n\s*/\*\s*build entityNodeMap",
        html_text,
        re.S,
    )
    rows: List[List[Any]] = []
    if m:
        try:
            rows = json.loads(m.group(1).strip())
        except Exception:
            rows = []
    return uid, rows


def parse_kg_edges(html_text: str) -> List[Dict[str, Any]]:
    m = re.search(r'const\s+g\s*=\s*"(.*?)";\s*\n', html_text, re.S)
    if not m:
        return []
    decoded = html.unescape(m.group(1))
    try:
        val = ast.literal_eval(decoded)
        if isinstance(val, list):
            return [x for x in val if isinstance(x, dict)]
    except Exception:
        pass
    try:
        val = json.loads(decoded)
        if isinstance(val, list):
            return [x for x in val if isinstance(x, dict)]
    except Exception:
        return []
    return []


def plant_preview(gene_id: str, *, timeout: int = 60) -> Dict[str, Any]:
    encoded = urllib.parse.quote(gene_id, safe="")
    url = f"{PLANTCONNECTOME_BASE}/normal/{encoded}"
    text, final_url = http_text(url, timeout=timeout, referer=f"{PLANTCONNECTOME_BASE}/")
    uid, rows = parse_preview(text)
    return {"gene_id": gene_id, "url": final_url, "unique_id": uid, "rows": rows}


def entity_result_url(search_type: str, entity: str, entity_type: str, uid: Optional[str]) -> str:
    seg = "non_alpha" if search_type == "non-alphanumeric" else search_type
    url = (
        f"{PLANTCONNECTOME_BASE}/{seg}/"
        f"{urllib.parse.quote(entity, safe='')}/results/"
        f"{urllib.parse.quote(entity_type, safe='')}"
    )
    if uid:
        url += "?uid=" + urllib.parse.quote(uid)
    return url


def plant_snippet(p_source: str, *, timeout: int = 60) -> Dict[str, Any]:
    return http_json(
        f"{PLANTCONNECTOME_BASE}/process-text-withoutapi",
        method="POST",
        payload={"p_source": p_source},
        timeout=timeout,
        referer=f"{PLANTCONNECTOME_BASE}/",
    )


def plant_details(gene_id: str, *, max_entities: int = 3, max_edges: int = 200,
                  snippets: int = 0, timeout: int = 60) -> Dict[str, Any]:
    prev = plant_preview(gene_id, timeout=timeout)
    uid = prev.get("unique_id")
    rows = prev.get("rows") or []
    entities = []
    seen_p_sources: List[str] = []
    for row in rows[:max_entities]:
        if not isinstance(row, list) or len(row) < 2:
            continue
        entity = str(row[0])
        entity_type = str(row[1])
        url = entity_result_url("normal", entity, entity_type, uid)
        try:
            detail_html, final_url = http_text(url, timeout=timeout, referer=prev.get("url") or None)
            edges = parse_kg_edges(detail_html)
        except Exception as exc:
            entities.append({"preview_row": row, "entity": entity, "entity_type": entity_type, "url": url, "error": str(exc), "edges": []})
            continue
        kept_edges = edges[:max_edges]
        for e in kept_edges:
            ps = e.get("p_source")
            if ps and ps not in seen_p_sources:
                seen_p_sources.append(ps)
        entities.append({
            "preview_row": row,
            "entity": entity,
            "entity_type": entity_type,
            "url": final_url,
            "edge_count_total": len(edges),
            "edges": kept_edges,
            "pmids": sorted({str(e.get("publication")) for e in edges if e.get("publication")}),
            "relation_counts": Counter(str(e.get("inter_type") or e.get("edge_disamb") or "") for e in edges).most_common(20),
        })
    snippet_map = {}
    if snippets > 0:
        for ps in seen_p_sources[:snippets]:
            try:
                snippet_map[ps] = plant_snippet(ps, timeout=timeout)
            except Exception as exc:
                snippet_map[ps] = {"error": str(exc)}
    return {
        "gene_id": gene_id,
        "preview": {"url": prev.get("url"), "unique_id": uid, "row_count": len(rows), "rows": rows[:max(20, max_entities)]},
        "entities": entities,
        "snippets": snippet_map,
    }


def compact_edge(edge: Dict[str, Any]) -> Dict[str, Any]:
    keep = ["id", "idtype", "target", "targettype", "inter_type", "edge_disamb", "publication", "p_source", "species", "basis", "source_extracted_definition", "target_extracted_definition"]
    return {k: edge.get(k) for k in keep if edge.get(k) not in (None, "")}


def preferred_alias_queries(selected: Dict[str, Any], *, max_alias_queries: int = 2) -> List[str]:
    """Pick TAIR-confirmed long aliases/full names that are safer than short symbols.

    Heuristics:
    - prefer multi-word descriptive aliases over short symbols;
    - prefer aliases whose word tokens occur in TAIR's description;
    - penalize allele-like or family-like names ending in digits;
    - keep final identity anchored to the selected TAIR gene_id.
    """
    aliases = selected.get("other_names") or []
    gene_id = selected.get("gene_id") or ""
    description = " ".join(selected.get("description") or []) if isinstance(selected.get("description"), list) else str(selected.get("description") or "")
    desc_words = set(re.findall(r"[A-Za-z]{4,}", description.upper()))
    bad = {gene_id.upper(), *(str(x).upper() for x in selected.get("gene_model_ids") or [])}
    ranked = []
    for alias in aliases:
        a = str(alias).strip()
        au = a.upper()
        if not a or au in bad:
            continue
        tokens = re.findall(r"[A-Za-z]{3,}", au)
        score = 0
        # Prefer long/full names and multi-word descriptive names.
        if " " in a:
            score += 5
        if len(a) >= 10:
            score += 3
        # Prefer aliases semantically reflected in the TAIR description.
        score += 3 * sum(1 for tok in tokens if tok in desc_words)
        # Avoid very short symbols and digit-suffixed family/allele-like aliases unless needed.
        if au.isupper() and len(a) <= 5:
            score -= 5
        if re.search(r"\b\d+$", au):
            score -= 2
        ranked.append((score, len(a), a))
    ranked.sort(reverse=True)
    return [a for _, __, a in ranked[:max_alias_queries]]


def build_result(query: str, *, mode: str, forced_gene_id: Optional[str],
                 max_candidates: int, max_entities: int, max_edges: int,
                 snippets: int, timeout: int, include_aliases: bool = False,
                 max_alias_queries: int = 2) -> Dict[str, Any]:
    if mode == "plant":
        return {"mode": mode, "plantconnectome": plant_details(query, max_entities=max_entities, max_edges=max_edges, snippets=snippets, timeout=timeout)}

    tair_res = tair_search(query, timeout=timeout)
    docs = tair_res.get("docs", [])
    choice = choose_tair_candidate(query, docs, forced_gene_id=forced_gene_id, timeout=timeout)
    out: Dict[str, Any] = {
        "mode": mode,
        "query": query,
        "tair": {
            "total": tair_res.get("total"),
            "candidate_count": len(docs),
            "candidates": [summarize_tair_doc(d) for d in docs[:max_candidates]],
            "exact_candidates": [summarize_tair_doc(d) for d in exact_matches(docs, query)[:max_candidates]],
            "choice": {k: v for k, v in choice.items() if k not in {"selected", "candidates"}},
        },
    }
    if choice.get("status") == "ambiguous":
        out["status"] = "ambiguous"
        out["message"] = "TAIR returned multiple plausible geneID candidates. Ask the user to confirm one geneID before querying PlantConnectome."
        out["tair"]["ambiguous_candidates"] = [summarize_tair_doc(d) for d in choice.get("candidates", [])[:max_candidates]]
        return out
    if choice.get("status") != "ok":
        out["status"] = choice.get("status", "error")
        out["message"] = choice.get("message", "TAIR candidate selection failed")
        return out

    selected = summarize_tair_doc(choice["selected"])
    out["status"] = "ok"
    out["tair"]["selected"] = selected
    gene_id = selected.get("gene_id")
    if mode == "full" and gene_id:
        out["plantconnectome"] = plant_details(gene_id, max_entities=max_entities, max_edges=max_edges, snippets=snippets, timeout=timeout)
        if include_aliases:
            alias_queries = preferred_alias_queries(selected, max_alias_queries=max_alias_queries)
            out["plantconnectome_alias_queries"] = []
            for alias in alias_queries:
                try:
                    out["plantconnectome_alias_queries"].append({
                        "query": alias,
                        "result": plant_details(alias, max_entities=max_entities, max_edges=max_edges, snippets=snippets, timeout=timeout),
                    })
                except Exception as exc:
                    out["plantconnectome_alias_queries"].append({"query": alias, "error": str(exc)})
    return out


def to_jsonable(obj: Any) -> Any:
    if isinstance(obj, Counter):
        return obj.most_common()
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, tuple):
        return [to_jsonable(v) for v in obj]
    return obj


def plant_summary_lines(pc: Dict[str, Any]) -> List[str]:
    lines: List[str] = []
    lines.append(f"PlantConnectome 查询词：{pc.get('gene_id')}")
    prev = pc.get("preview", {})
    lines.append(f"预览实体数：{prev.get('row_count')} | URL: {prev.get('url')}")
    rows = prev.get("rows") or []
    if rows:
        lines.append("预览前几项：")
        for row in rows[:5]:
            lines.append(f"- {row}")
    all_pmids = set()
    relation_counter = Counter()
    target_counter = Counter()
    edge_examples: List[Dict[str, Any]] = []
    for ent in pc.get("entities") or []:
        for pmid in ent.get("pmids") or []:
            all_pmids.add(pmid)
        for rel, n in ent.get("relation_counts") or []:
            if rel:
                relation_counter[rel] += n
        for e in ent.get("edges") or []:
            if e.get("target"):
                target_counter[str(e.get("target"))] += 1
            if len(edge_examples) < 8:
                edge_examples.append(compact_edge(e))
    lines.append(f"解析实体数：{len(pc.get('entities') or [])} | PMID 数：{len(all_pmids)}")
    if all_pmids:
        lines.append("PMID 示例：" + ", ".join(sorted(all_pmids)[:20]))
    if relation_counter:
        lines.append("高频关系：" + "; ".join(f"{k}({v})" for k, v in relation_counter.most_common(10)))
    if target_counter:
        lines.append("高频 target：" + "; ".join(f"{k}({v})" for k, v in target_counter.most_common(10)))
    if edge_examples:
        lines.append("关系示例：")
        for e in edge_examples:
            pmid = e.get("publication") or ""
            src = e.get("id") or ""
            rel = e.get("inter_type") or e.get("edge_disamb") or ""
            tgt = e.get("target") or ""
            basis = e.get("basis") or ""
            lines.append(f"- {src} --[{rel}]--> {tgt} PMID:{pmid} basis:{basis}")
    if pc.get("snippets"):
        lines.append("文献片段：")
        for ps, sn in pc["snippets"].items():
            text = sn.get("text_input") if isinstance(sn, dict) else None
            if text:
                lines.append(f"- {ps}: {text[:350].replace(chr(10), ' ')}")
            else:
                lines.append(f"- {ps}: {sn}")
    return lines


def summary_lines(result: Dict[str, Any]) -> List[str]:
    status = result.get("status")
    mode = result.get("mode")
    if mode == "plant":
        return plant_summary_lines(result.get("plantconnectome", {}))
    lines: List[str] = []
    lines.append(f"查询：{result.get('query')}")
    tair = result.get("tair", {})
    lines.append(f"TAIR 返回候选：{tair.get('candidate_count')} / total={tair.get('total')}")
    if status == "ambiguous":
        lines.append("状态：ambiguous，需要用户确认 geneID 后再查 PlantConnectome。")
        for i, c in enumerate(tair.get("ambiguous_candidates") or [], 1):
            desc = (c.get("description") or "")[:180]
            aliases = ", ".join((c.get("other_names") or [])[:8])
            lines.append(f"{i}. {c.get('gene_id')} | models={','.join(c.get('gene_model_ids') or [])} | aliases={aliases}")
            if desc:
                lines.append(f"   描述：{desc}")
        return lines
    if status != "ok":
        lines.append(f"状态：{status}; {result.get('message','')}")
        return lines
    sel = tair.get("selected") or {}
    lines.append(f"TAIR 确认 geneID：{sel.get('gene_id')}")
    if sel.get("gene_model_ids"):
        lines.append(f"Gene model：{', '.join(sel.get('gene_model_ids'))}")
    if sel.get("other_names"):
        lines.append(f"别名：{', '.join(sel.get('other_names')[:12])}")
    if sel.get("description"):
        lines.append(f"TAIR 描述：{sel.get('description')}")
    if mode == "full" and result.get("plantconnectome"):
        lines.append("")
        lines.extend(plant_summary_lines(result["plantconnectome"]))
        alias_results = result.get("plantconnectome_alias_queries") or []
        if alias_results:
            lines.append("")
            lines.append("TAIR 确认别名辅助查询（仍以 geneID 为准）：")
            for item in alias_results:
                lines.append(f"\n## Alias query: {item.get('query')}")
                if item.get("error"):
                    lines.append(f"错误：{item.get('error')}")
                else:
                    lines.extend(plant_summary_lines(item.get("result", {})))
    return lines


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["tair", "plant", "full"], help="tair only, PlantConnectome only, or full TAIR->PlantConnectome workflow")
    parser.add_argument("query", help="gene symbol/alias/AGI ID; in plant mode this should be confirmed AGI ID")
    parser.add_argument("--gene-id", help="confirmed AGI ID to use when query is ambiguous")
    parser.add_argument("--format", choices=["json", "summary"], default="json")
    parser.add_argument("--max-candidates", type=int, default=10)
    parser.add_argument("--max-entities", type=int, default=3)
    parser.add_argument("--max-edges", type=int, default=50)
    parser.add_argument("--snippets", type=int, default=0, help="fetch snippets for first N p_source values")
    parser.add_argument("--include-aliases", action="store_true", help="in full mode, also query TAIR-confirmed long aliases/full names in PlantConnectome")
    parser.add_argument("--max-alias-queries", type=int, default=2, help="maximum TAIR-confirmed alias/full-name PlantConnectome queries")
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args(argv)
    try:
        result = build_result(args.query, mode=args.mode, forced_gene_id=args.gene_id,
                              max_candidates=args.max_candidates, max_entities=args.max_entities,
                              max_edges=args.max_edges, snippets=args.snippets, timeout=args.timeout,
                              include_aliases=args.include_aliases,
                              max_alias_queries=args.max_alias_queries)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:2000]
        result = {"status": "http_error", "code": exc.code, "reason": exc.reason, "body": body}
    except Exception as exc:
        result = {"status": "error", "error_type": type(exc).__name__, "message": str(exc)}
    result = to_jsonable(result)
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("\n".join(summary_lines(result)))
    return 0 if result.get("status") not in {"error", "http_error"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
